from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest
from psycopg import Error as PostgresError

from packages.engine_contracts import EventFamily, canonical_json_bytes
from packages.engine_event_ledger import (
    EngineEventConflictError,
    EngineEventSequenceBlockedError,
)
from services.job_store.engine_event_repository import (
    InMemoryEngineEventLedger,
    PostgresEngineEventLedger,
    PostgresEngineEventLedgerSql,
)
from tests.jobs.test_engine_event_ledger import RUN_ID, _batch, _event


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

    def __enter__(self):
        assert not self._connection.transaction_active
        self._connection.transaction_active = True
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

    def transaction(self):
        self.transaction_count += 1
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
    def __init__(self, sqlstate: str, detail: str | None = None) -> None:
        self.sqlstate = sqlstate
        self._test_diag = SimpleNamespace(message_detail=detail)
        super().__init__("database authority rejected request")

    @property
    def diag(self):
        return self._test_diag


def _receipt_row(receipt) -> dict[str, object]:
    return receipt.model_dump(mode="python")


def test_postgres_ingest_uses_one_transaction_and_one_function_statement() -> None:
    batch = _batch(_event(2, "BacktestStarted"), _event(3, "BacktestCompleted"))
    expected = InMemoryEngineEventLedger().ingest(batch)
    connection = Connection([[_receipt_row(expected)]])

    receipt = PostgresEngineEventLedger(Pool(connection)).ingest(batch)

    assert receipt == expected
    assert connection.transaction_count == 1
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert len(connection.executions) == 1
    statement, params = connection.executions[0]
    assert statement == PostgresEngineEventLedgerSql.INGEST_BATCH
    assert "%(batch_document)s::jsonb" not in statement
    document = json.loads(params["batch_document"])
    assert document["batch_sha256"] == batch.sha256
    assert document["ingestion_digest"] == expected.ingestion_digest
    assert document["events"][0]["canonical_json"].encode() == canonical_json_bytes(
        batch.events[0]
    )
    assert tuple(event["stream_sequence"] for event in document["events"]) == (2, 3)


def test_postgres_ingest_rejects_receipt_that_differs_from_validated_batch() -> None:
    batch = _batch(_event(2, "BacktestStarted"))
    expected = InMemoryEngineEventLedger().ingest(batch)
    row = _receipt_row(expected)
    row["last_digest"] = "f" * 64
    connection = Connection([[row]])
    repository = PostgresEngineEventLedger(Pool(connection))

    with pytest.raises(EngineEventConflictError, match="differs"):
        repository.ingest(batch)

    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_postgres_ingest_rolls_back_when_write_authority_returns_no_receipt() -> None:
    batch = _batch(_event(2, "BacktestStarted"))
    connection = Connection([[]])

    with pytest.raises(EngineEventConflictError, match="no receipt"):
        PostgresEngineEventLedger(Pool(connection)).ingest(batch)

    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_postgres_ingest_maps_database_identity_conflict_without_leaking_details() -> None:
    batch = _batch(_event(2, "BacktestStarted"))
    repository = PostgresEngineEventLedger(
        Pool(Connection(error=DatabaseError("P2D01")))
    )

    with pytest.raises(EngineEventConflictError, match="identity conflict"):
        repository.ingest(batch)


@pytest.mark.parametrize(
    ("sqlstate", "actual", "reason"),
    (("P2D02", 4, "SEQUENCE_GAP"), ("P2D03", 1, "SEQUENCE_REGRESSION")),
)
def test_postgres_ingest_maps_sequence_block_with_typed_authority(
    sqlstate: str,
    actual: int,
    reason: str,
) -> None:
    batch = _batch(_event(2, "BacktestStarted"))
    detail = f"engine_run_id={RUN_ID};expected=3;actual={actual}"
    repository = PostgresEngineEventLedger(
        Pool(Connection(error=DatabaseError(sqlstate, detail)))
    )

    with pytest.raises(EngineEventSequenceBlockedError) as raised:
        repository.ingest(batch)

    assert raised.value.engine_run_id == RUN_ID
    assert raised.value.expected_sequence == 3
    assert raised.value.actual_sequence == actual
    assert raised.value.reason.value == reason


def test_postgres_ingest_rejects_inconsistent_sequence_error_authority() -> None:
    batch = _batch(_event(2, "BacktestStarted"))
    detail = f"engine_run_id={RUN_ID};expected=3;actual=1"
    repository = PostgresEngineEventLedger(
        Pool(Connection(error=DatabaseError("P2D02", detail)))
    )

    with pytest.raises(EngineEventConflictError, match="inconsistent"):
        repository.ingest(batch)


def test_postgres_reads_strict_receipt_events_and_projection_rows() -> None:
    batch = _batch(_event(2, "BacktestStarted"), _event(3, "OrderAccepted"))
    memory = InMemoryEngineEventLedger()
    receipt = memory.ingest(batch)
    events = memory.load_events(RUN_ID)
    projection = memory.load_projection(RUN_ID)
    assert projection is not None
    event_rows = [
        {
            "message_id": event.message_id,
            "engine_run_id": event.engine_run_id,
            "stream_sequence": event.stream_sequence,
            "event_type": event.event_type,
            "event_family": event.event_family.value,
            "canonical_json_text": event.canonical_json,
            "digest": event.digest,
            "batch_sha256": event.batch_sha256,
        }
        for event in events
    ]
    projection_row = projection.model_dump(mode="python")
    projection_row["event_type_counts"] = [
        count.model_dump(mode="python") for count in projection.event_type_counts
    ]
    connection = Connection(
        [[_receipt_row(receipt)], event_rows, [projection_row], [projection_row]]
    )
    repository = PostgresEngineEventLedger(Pool(connection))

    assert repository.load_receipt(batch.sha256) == receipt
    assert repository.load_events(RUN_ID) == events
    assert repository.load_projection(RUN_ID) == projection
    assert repository.recover_projections() == (projection,)
    assert connection.transaction_count == 1
    assert connection.commit_count == 1
    assert connection.rollback_count == 0


def test_projection_recovery_rolls_back_if_rows_are_not_strict_and_unique() -> None:
    projection_row = {
        "engine_run_id": RUN_ID,
        "event_count": 1,
        "event_type_counts": [
            {"event_type": "BacktestStarted", "count": 1}
        ],
        "last_sequence": 2,
        "last_digest": "a" * 64,
    }
    connection = Connection([[projection_row, projection_row]])

    with pytest.raises(EngineEventConflictError, match="duplicate engine runs"):
        PostgresEngineEventLedger(Pool(connection)).recover_projections()

    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_postgres_replay_is_derived_from_immutable_event_rows() -> None:
    batch = _batch(
        _event(2, "BacktestStarted", family=EventFamily.ENGINE_LIFECYCLE),
        _event(3, "OrderAccepted", family=EventFamily.ORDER_LIFECYCLE),
    )
    memory = InMemoryEngineEventLedger()
    memory.ingest(batch)
    event_rows = [
        {
            "message_id": event.message_id,
            "engine_run_id": event.engine_run_id,
            "stream_sequence": event.stream_sequence,
            "event_type": event.event_type,
            "event_family": event.event_family.value,
            "canonical_json_text": event.canonical_json,
            "digest": event.digest,
            "batch_sha256": event.batch_sha256,
        }
        for event in memory.load_events(RUN_ID)
    ]

    replayed = PostgresEngineEventLedger(
        Pool(Connection([event_rows]))
    ).replay_projection(RUN_ID)

    assert replayed == memory.load_projection(RUN_ID)
