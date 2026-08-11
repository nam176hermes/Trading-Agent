from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from psycopg import Error as PostgresError

from packages.event_ledger import (
    EventConflictError,
    OutboxIntent,
    PostgresEventLedgerRepository,
    PostgresLedgerSql,
    ReplayError,
    SequenceError,
    replay,
    snapshot_from_result,
)
from packages.event_ledger.replay import serialize_event
from tests.event_ledger.test_reducer import envelope, fill, signal


class Cursor:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def fetchone(self) -> object:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[object]:
        return self._rows


class Transaction:
    def __init__(self, connection: "Connection") -> None:
        self._connection = connection

    def __enter__(self) -> "Transaction":
        assert not self._connection.transaction_active
        self._connection.transaction_active = True
        self._connection.transaction_count += 1
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        assert self._connection.transaction_active
        self._connection.transaction_active = False
        if exc_type is None:
            self._connection.commit_count += 1
        else:
            self._connection.rollback_count += 1
        return False


class Connection:
    def __init__(
        self,
        responses: list[list[object]] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.executions: list[tuple[str, dict[str, object]]] = []
        self.transaction_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.transaction_active = False

    def transaction(self) -> Transaction:
        return Transaction(self)

    def execute(self, statement: str, params: dict[str, object]) -> Cursor:
        self.executions.append((statement, params))
        if self.error is not None:
            raise self.error
        return Cursor(self.responses.pop(0) if self.responses else [])


class Pool:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def connection(self):
        return nullcontext(self._connection)


class DatabaseError(PostgresError):
    def __init__(
        self,
        sqlstate: str,
        primary: str = "database rejected request",
    ) -> None:
        self.sqlstate = sqlstate
        self._test_diag = SimpleNamespace(message_primary=primary)
        super().__init__("database authority rejected request")

    @property
    def diag(self):
        return self._test_diag


class UUIDSubclass(UUID):
    pass


class TextSubclass(str):
    pass


class TupleSubclass(tuple):
    def __iter__(self):
        raise AssertionError("hostile row iteration must not run")


class DictSubclass(dict):
    def __getitem__(self, key):
        raise AssertionError("hostile row lookup must not run")


def test_append_uses_one_transaction_and_database_owned_function_result() -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(
        event_id=event.event_id,
        topic="domain.signal",
        payload_json='{"attempt":1}',
    )
    connection = Connection([[(True,)]])

    outcome = PostgresEventLedgerRepository(Pool(connection)).append(event, outbox)

    assert outcome.event_id == event.event_id
    assert outcome.inserted is True
    assert connection.transaction_count == 1
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert connection.executions == [
        (
            PostgresLedgerSql.APPEND_EVENT_AND_OUTBOX,
            {
                "event_id": event.event_id,
                "stream_id": event.stream_id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "canonical_event_text": serialize_event(event),
                "topic": outbox.topic,
                "payload_json": outbox.payload_json,
            },
        )
    ]
    assert "INSERT" not in connection.executions[0][0]


def test_append_preserves_database_exact_retry_outcome_without_local_state() -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="domain.signal")
    connection = Connection([[{"append_domain_event": False}]])
    repository = PostgresEventLedgerRepository(Pool(connection))

    first_process_retry = repository.append(event, outbox)

    assert first_process_retry.event_id == event.event_id
    assert first_process_retry.inserted is False
    assert not hasattr(repository, "_events")
    assert not hasattr(repository, "_append_idempotency")


def test_append_rejects_outbox_identity_before_opening_a_connection() -> None:
    event = envelope(signal(), event_number=1)
    wrong = OutboxIntent(event_id=UUID(int=999), topic="domain.signal")
    connection = Connection()

    with pytest.raises(EventConflictError, match="reference"):
        PostgresEventLedgerRepository(Pool(connection)).append(event, wrong)

    assert connection.executions == []


@pytest.mark.parametrize(
    "forged",
    (
        lambda event, outbox: (
            event.model_copy(update={"restore": True}),
            outbox,
        ),
        lambda event, outbox: (
            event.model_copy(update={"event_id": UUIDSubclass(int=event.event_id.int)}),
            outbox,
        ),
        lambda event, outbox: (
            event,
            outbox.model_copy(update={"restore": True}),
        ),
        lambda event, outbox: (
            event,
            outbox.model_copy(update={"topic": TextSubclass(outbox.topic)}),
        ),
    ),
)
def test_append_rejects_copy_forged_and_subclassed_inputs_before_database_access(
    forged,
) -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="domain.signal")
    supplied_event, supplied_outbox = forged(event, outbox)
    connection = Connection()

    with pytest.raises((EventConflictError, ReplayError)):
        PostgresEventLedgerRepository(Pool(connection)).append(
            supplied_event, supplied_outbox
        )

    assert connection.executions == []


@pytest.mark.parametrize(
    ("sqlstate", "error_type"),
    (("23505", EventConflictError), ("23514", SequenceError)),
)
def test_append_translates_database_identity_and_sequence_authority(
    sqlstate: str,
    error_type: type[Exception],
) -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="domain.signal")
    primary = (
        "expected sequence 2 for stream "
        "00000000-0000-0000-0000-000000000064, got 3"
        if sqlstate == "23514"
        else "conflicting duplicate event"
    )
    connection = Connection(error=DatabaseError(sqlstate, primary))

    with pytest.raises(error_type):
        PostgresEventLedgerRepository(Pool(connection)).append(event, outbox)

    assert connection.transaction_count == 1
    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_append_does_not_mislabel_non_sequence_check_violation_as_sequence() -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="domain.signal")
    connection = Connection(
        error=DatabaseError("23514", "event envelope metadata does not match")
    )

    with pytest.raises(ReplayError) as raised:
        PostgresEventLedgerRepository(Pool(connection)).append(event, outbox)

    assert type(raised.value) is ReplayError
    assert connection.rollback_count == 1


def test_append_rolls_back_for_missing_or_non_boolean_database_outcome() -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="domain.signal")

    for rows in ([], [(1,)]):
        connection = Connection([rows])
        with pytest.raises(ReplayError, match="invalid append outcome") as raised:
            PostgresEventLedgerRepository(Pool(connection)).append(event, outbox)
        assert type(raised.value) is ReplayError
        assert connection.commit_count == 0
        assert connection.rollback_count == 1


def test_load_events_reconstructs_registered_payloads_from_canonical_text() -> None:
    first = envelope(fill(), event_number=2, stream_number=10, sequence=1)
    second = envelope(signal(), event_number=1, stream_number=20, sequence=1)
    connection = Connection(
        [
            [
                {"canonical_event_text": serialize_event(first)},
                (serialize_event(second),),
            ]
        ]
    )

    loaded = PostgresEventLedgerRepository(Pool(connection)).load_events()

    assert loaded == (first, second)
    assert type(loaded[0].payload) is type(first.payload)
    assert type(loaded[1].payload) is type(second.payload)
    assert connection.executions == [(PostgresLedgerSql.LOAD_EVENTS, {})]
    assert connection.transaction_count == 0


def test_load_events_returns_empty_tuple_for_empty_authoritative_history() -> None:
    assert PostgresEventLedgerRepository(Pool(Connection([[]]))).load_events() == ()


def test_load_events_rejects_noncanonical_text_and_never_returns_a_valid_prefix() -> None:
    first = envelope(signal(), event_number=1, sequence=1)
    second = envelope(fill(), event_number=2, sequence=2)
    noncanonical = json.dumps(json.loads(serialize_event(second)), sort_keys=True)
    connection = Connection([[(serialize_event(first),), (noncanonical,)]])

    with pytest.raises(ReplayError, match="not canonical"):
        PostgresEventLedgerRepository(Pool(connection)).load_events()


@pytest.mark.parametrize(
    "row",
    (
        (),
        (1,),
        ("{}", "surplus"),
        {"wrong": "{}"},
        {"canonical_event_text": "{}"},
        (TextSubclass("{}"),),
        TupleSubclass(("{}",)),
        DictSubclass(canonical_event_text="{}"),
    ),
)
def test_load_events_rejects_invalid_row_shapes_and_stored_event_bytes(row: object) -> None:
    connection = Connection([ [row] ])

    with pytest.raises(ReplayError):
        PostgresEventLedgerRepository(Pool(connection)).load_events()


def test_claim_inbox_validates_input_and_uses_existing_claim_contract() -> None:
    event_id = UUID(int=1)
    connection = Connection([[(True,)], [{"claimed": False}]])
    repository = PostgresEventLedgerRepository(Pool(connection))

    assert repository.claim_inbox("consumer", event_id) is True
    assert repository.claim_inbox("consumer", event_id) is False

    assert connection.executions == [
        (PostgresLedgerSql.CLAIM_INBOX, {"consumer": "consumer", "event_id": event_id}),
        (PostgresLedgerSql.CLAIM_INBOX, {"consumer": "consumer", "event_id": event_id}),
    ]
    assert connection.transaction_count == 2
    assert connection.commit_count == 2


@pytest.mark.parametrize("consumer", (None, True, 1, "", "x" * 257))
def test_claim_inbox_rejects_invalid_consumers_without_database_access(
    consumer: object,
) -> None:
    connection = Connection()

    with pytest.raises(ReplayError):
        PostgresEventLedgerRepository(Pool(connection)).claim_inbox(
            consumer, UUID(int=1)  # type: ignore[arg-type]
        )

    assert connection.executions == []


def test_claim_inbox_maps_unknown_event_foreign_key_to_conflict() -> None:
    connection = Connection(error=DatabaseError("23503"))

    with pytest.raises(EventConflictError, match="inbox claim"):
        PostgresEventLedgerRepository(Pool(connection)).claim_inbox(
            "consumer", UUID(int=1)
        )

    assert connection.rollback_count == 1


def test_claim_inbox_rejects_uuid_subclass_and_malformed_database_boolean() -> None:
    rejected_input = Connection()
    repository = PostgresEventLedgerRepository(Pool(rejected_input))
    with pytest.raises(ReplayError, match="exact UUID"):
        repository.claim_inbox("consumer", UUIDSubclass(int=1))
    assert rejected_input.executions == []

    malformed_result = Connection([[(1,)]])
    with pytest.raises(ReplayError, match="invalid inbox claim outcome") as raised:
        PostgresEventLedgerRepository(Pool(malformed_result)).claim_inbox(
            "consumer", UUID(int=1)
        )
    assert type(raised.value) is ReplayError
    assert malformed_result.rollback_count == 1


def test_acknowledge_outbox_uses_database_owned_idempotent_result() -> None:
    event_id = UUID(int=1)
    connection = Connection([[(True,)], [{"acknowledge_domain_publication": False}]])
    repository = PostgresEventLedgerRepository(Pool(connection))

    assert repository.acknowledge_outbox(event_id) is True
    assert repository.acknowledge_outbox(event_id) is False

    assert connection.executions == [
        (PostgresLedgerSql.ACKNOWLEDGE_OUTBOX, {"event_id": event_id}),
        (PostgresLedgerSql.ACKNOWLEDGE_OUTBOX, {"event_id": event_id}),
    ]
    assert connection.transaction_count == 2
    assert connection.commit_count == 2
    assert all("DELETE" not in statement for statement, _ in connection.executions)


def test_acknowledge_outbox_maps_missing_pending_work_to_conflict() -> None:
    connection = Connection(error=DatabaseError("23514"))

    with pytest.raises(EventConflictError, match="outbox acknowledgement"):
        PostgresEventLedgerRepository(Pool(connection)).acknowledge_outbox(UUID(int=1))

    assert connection.rollback_count == 1


def test_acknowledge_outbox_rejects_malformed_database_boolean() -> None:
    connection = Connection([[{"acknowledge_domain_publication": "true"}]])

    with pytest.raises(ReplayError, match="invalid outbox acknowledgement") as raised:
        PostgresEventLedgerRepository(Pool(connection)).acknowledge_outbox(UUID(int=1))

    assert type(raised.value) is ReplayError
    assert connection.rollback_count == 1


def test_save_snapshot_uses_validated_canonical_bytes_and_database_idempotency() -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    )
    connection = Connection([[(True,)], [{"save_domain_snapshot": False}]])
    repository = PostgresEventLedgerRepository(Pool(connection))

    assert repository.save_snapshot(snapshot) is None
    assert repository.save_snapshot(snapshot) is None

    assert connection.executions == [
        (
            PostgresLedgerSql.SAVE_SNAPSHOT,
            {"canonical_state_json": snapshot.canonical_state_json},
        ),
        (
            PostgresLedgerSql.SAVE_SNAPSHOT,
            {"canonical_state_json": snapshot.canonical_state_json},
        ),
    ]
    assert connection.transaction_count == 2
    assert connection.commit_count == 2
    assert all("INSERT" not in statement for statement, _ in connection.executions)


def test_save_snapshot_rejects_forged_content_before_database_access() -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    ).model_copy(update={"state_hash": "0" * 64})
    connection = Connection()

    with pytest.raises(EventConflictError, match="invalid snapshot"):
        PostgresEventLedgerRepository(Pool(connection)).save_snapshot(snapshot)

    assert connection.executions == []


def test_save_snapshot_rejects_copy_forged_root_extra_before_database_access() -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    ).model_copy(update={"restore": True})
    connection = Connection()

    with pytest.raises(EventConflictError, match="invalid snapshot"):
        PostgresEventLedgerRepository(Pool(connection)).save_snapshot(snapshot)

    assert connection.executions == []


def test_save_snapshot_rejects_copy_forged_nested_state_before_database_access() -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    )
    forged = snapshot.model_copy(
        update={"state": snapshot.state.model_copy(update={"restore": True})}
    )
    connection = Connection()

    with pytest.raises(EventConflictError, match="invalid snapshot"):
        PostgresEventLedgerRepository(Pool(connection)).save_snapshot(forged)

    assert connection.executions == []


def test_save_snapshot_rejects_malformed_database_boolean_as_replay_error() -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    )
    connection = Connection([[{"save_domain_snapshot": 1}]])

    with pytest.raises(ReplayError, match="invalid snapshot save outcome") as raised:
        PostgresEventLedgerRepository(Pool(connection)).save_snapshot(snapshot)

    assert type(raised.value) is ReplayError
    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_load_snapshot_reconstructs_and_revalidates_complete_stored_wrapper() -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    )
    row = {
        "canonical_state_json": snapshot.canonical_state_json,
        "replay_schema_version": snapshot.schema_version,
        "reducer_version": snapshot.reducer_version,
        "state_hash": snapshot.state_hash,
    }
    connection = Connection([[row]])

    loaded = PostgresEventLedgerRepository(Pool(connection)).load_snapshot(
        snapshot.state_hash
    )

    assert loaded == snapshot
    assert loaded is not snapshot
    assert connection.executions == [
        (PostgresLedgerSql.LOAD_SNAPSHOT, {"state_hash": snapshot.state_hash})
    ]
    assert connection.transaction_count == 0


def test_load_snapshot_returns_none_only_for_a_missing_row() -> None:
    connection = Connection([[]])

    assert (
        PostgresEventLedgerRepository(Pool(connection)).load_snapshot("f" * 64)
        is None
    )


@pytest.mark.parametrize(
    "row",
    (
        (),
        ("{}",),
        {"canonical_state_json": "{}"},
        TupleSubclass(("{}", "schema", "reducer", "0" * 64)),
        DictSubclass(
            canonical_state_json="{}",
            replay_schema_version="schema",
            reducer_version="reducer",
            state_hash="0" * 64,
        ),
    ),
)
def test_load_snapshot_rejects_malformed_and_hostile_row_shapes(row: object) -> None:
    connection = Connection([[row]])

    with pytest.raises(ReplayError):
        PostgresEventLedgerRepository(Pool(connection)).load_snapshot("0" * 64)


@pytest.mark.parametrize("mutation", ("canonical", "schema", "reducer", "hash"))
def test_load_snapshot_rejects_corrupt_or_mismatched_stored_state(
    mutation: str,
) -> None:
    snapshot = snapshot_from_result(
        replay((envelope(signal(), event_number=1),))
    )
    row: list[object] = [
        snapshot.canonical_state_json,
        snapshot.schema_version,
        snapshot.reducer_version,
        snapshot.state_hash,
    ]
    if mutation == "canonical":
        row[0] = json.dumps(json.loads(snapshot.canonical_state_json), indent=2)
    elif mutation == "schema":
        row[1] = "wrong-replay-schema"
    elif mutation == "reducer":
        row[2] = "wrong-reducer"
    else:
        row[3] = "0" * 64
    connection = Connection([[tuple(row)]])

    with pytest.raises(ReplayError):
        PostgresEventLedgerRepository(Pool(connection)).load_snapshot(
            snapshot.state_hash
        )


@pytest.mark.parametrize("state_hash", (None, True, 1, "A" * 64, "0" * 63))
def test_load_snapshot_rejects_invalid_lookup_hash_without_database_access(
    state_hash: object,
) -> None:
    connection = Connection()

    with pytest.raises(ReplayError):
        PostgresEventLedgerRepository(Pool(connection)).load_snapshot(
            state_hash  # type: ignore[arg-type]
        )

    assert connection.executions == []


def test_unrecognized_database_failure_is_not_laundered() -> None:
    event = envelope(signal(), event_number=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="domain.signal")
    connection = Connection(error=DatabaseError("08006"))

    with pytest.raises(DatabaseError):
        PostgresEventLedgerRepository(Pool(connection)).append(event, outbox)

    assert connection.rollback_count == 1
