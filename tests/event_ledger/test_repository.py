from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.event_ledger import EventConflictError, InMemoryEventLedger, OutboxIntent, PostgresLedgerSql, ReducerPolicy, ReplayError, ReplayStatus, SequenceError, snapshot_from_result
from packages.event_ledger import replay
from packages.event_ledger.models import EventTypeCount
from packages.event_ledger.replay import canonical_state_json, event_digest

from tests.event_ledger.test_reducer import envelope, fill, signal
from tests.runtime_risk.test_approval import runtime_risk_event


class HostileUUIDSubclass(UUID):
    def __eq__(self, other: object) -> bool:
        raise AssertionError("UUID subclass equality must not run")

    def __hash__(self) -> int:
        raise AssertionError("UUID subclass hashing must not run")


class UUIDDuck:
    int = 1
    bytes = b"\x00" * 16

    def __eq__(self, other: object) -> bool:
        raise AssertionError("UUID duck equality must not run")

    def __hash__(self) -> int:
        raise AssertionError("UUID duck hashing must not run")


def test_append_and_outbox_are_atomic_and_identical_retry_is_idempotent() -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1)
    intent = OutboxIntent(event_id=event.event_id, topic="domain.signal")
    assert repository.append(event, intent).inserted is True
    assert repository.append(event, intent).inserted is False
    assert repository.load_events() == (event,)
    assert repository.load_outbox() == (intent,)
    with pytest.raises(EventConflictError):
        repository.append(event.model_copy(update={"source": "conflict"}), intent)
    assert repository.load_outbox() == (intent,)


def test_repository_preserves_runtime_risk_decision_concrete_payload() -> None:
    repository = InMemoryEventLedger()
    event = runtime_risk_event()
    outbox = OutboxIntent(
        event_id=event.event_id,
        topic="runtime-risk.decisions",
        payload_json='{"decision_id":"00000000-0000-0000-0000-00000000005a"}',
    )

    repository.append(event, outbox)

    assert repository.load_events() == (event,)
    assert type(repository.load_events()[0].payload) is type(event.payload)
    assert repository.load_outbox() == (outbox,)


def test_repository_preserves_global_halt_transition_concrete_payload() -> None:
    from tests.runtime_risk.test_global_halt import transition_event, transition_payload

    repository = InMemoryEventLedger()
    event = transition_event(transition_payload(), event_id=UUID(int=901), sequence=1)
    outbox = OutboxIntent(event_id=event.event_id, topic="global-halt.audit")

    repository.append(event, outbox)

    loaded = repository.load_events()[0]
    assert loaded == event
    assert type(loaded.payload) is type(event.payload)


def test_repository_preserves_submit_permit_prepared_concrete_payload() -> None:
    from tests.runtime_risk.test_global_halt import initialize, prepared_event

    repository = InMemoryEventLedger()
    state = initialize(repository)
    event = prepared_event(state)
    outbox = OutboxIntent(event_id=event.event_id, topic="submit-permit.audit")

    repository.append(event, outbox)

    loaded = repository.load_events()[-1]
    assert loaded == event
    assert type(loaded.payload) is type(event.payload)


def test_exact_retry_survives_publication_and_pending_outbox_retention() -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1)
    intent = OutboxIntent(
        event_id=event.event_id,
        topic="domain.signal",
        payload_json='{"attempt":1}',
    )

    assert repository.append(event, intent).inserted is True
    assert repository.acknowledge_outbox(event.event_id) is True
    assert repository.load_outbox() == ()
    assert repository.acknowledge_outbox(event.event_id) is False

    assert repository.append(event, intent).inserted is False
    assert repository.load_outbox() == ()
    changed = intent.model_copy(update={"payload_json": '{"attempt":2}'})
    with pytest.raises(EventConflictError, match="conflicting content"):
        repository.append(event, changed)
    with pytest.raises(EventConflictError, match="conflicting content"):
        repository.append(event.model_copy(update={"source": "changed"}), intent)
    assert repository.load_outbox() == ()


def test_publication_acknowledgement_requires_pending_work() -> None:
    repository = InMemoryEventLedger()
    unknown = envelope(signal(), event_number=999).event_id

    with pytest.raises(EventConflictError, match="pending outbox"):
        repository.acknowledge_outbox(unknown)


def test_repository_enforces_sequence_order_claims_inbox_once_and_loads_deterministically() -> None:
    repository = InMemoryEventLedger()
    first = envelope(signal(), event_number=1, stream_number=20, sequence=1)
    second = envelope(fill(), event_number=2, stream_number=10, sequence=1)
    repository.append(first, OutboxIntent(event_id=first.event_id, topic="a"))
    repository.append(second, OutboxIntent(event_id=second.event_id, topic="b"))
    assert repository.load_events() == (second, first)
    assert repository.claim_inbox("consumer", first.event_id) is True
    assert repository.claim_inbox("consumer", first.event_id) is False
    gap = envelope(fill(), event_number=3, stream_number=20, sequence=3)
    with pytest.raises(SequenceError):
        repository.append(gap, OutboxIntent(event_id=gap.event_id, topic="gap"))


def test_inbox_claim_never_reopens_after_publication_retention() -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1)
    repository.append(event, OutboxIntent(event_id=event.event_id, topic="topic"))

    assert repository.claim_inbox("consumer", event.event_id) is True
    assert repository.acknowledge_outbox(event.event_id) is True
    assert repository.claim_inbox("consumer", event.event_id) is False
    assert not hasattr(repository, "release_inbox")


def test_ingest_boundary_rejects_delivery_order_that_replay_canonicalizes() -> None:
    repository = InMemoryEventLedger()
    first = envelope(signal(), event_number=1, sequence=1)
    second = envelope(fill(), event_number=2, sequence=2)

    assert replay((second, first)) == replay((first, second))
    with pytest.raises(SequenceError, match="expected sequence 1, got 2"):
        repository.append(second, OutboxIntent(event_id=second.event_id, topic="second"))

    repository.append(first, OutboxIntent(event_id=first.event_id, topic="first"))
    repository.append(second, OutboxIntent(event_id=second.event_id, topic="second"))
    assert repository.load_events() == (first, second)


def test_load_stream_events_returns_empty_tuple_for_unknown_stream() -> None:
    repository = InMemoryEventLedger()

    assert repository.load_stream_events(UUID(int=999)) == ()


def test_load_stream_events_returns_one_exact_concrete_event() -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1, stream_number=20, sequence=1)
    repository.append(event, OutboxIntent(event_id=event.event_id, topic="signal"))

    loaded = repository.load_stream_events(event.stream_id)

    assert loaded == (event,)
    assert type(loaded[0].payload) is type(event.payload)


def test_load_stream_events_returns_only_requested_stream_in_canonical_order() -> None:
    repository = InMemoryEventLedger()
    requested_first = envelope(
        signal(), event_number=3, stream_number=20, sequence=1
    )
    foreign = envelope(fill(), event_number=1, stream_number=10, sequence=1)
    requested_second = envelope(
        fill(), event_number=2, stream_number=20, sequence=2
    )
    for event in (requested_first, foreign, requested_second):
        repository.append(
            event,
            OutboxIntent(event_id=event.event_id, topic="domain.event"),
        )

    assert repository.load_stream_events(requested_first.stream_id) == (
        requested_first,
        requested_second,
    )
    assert repository.load_stream_events(foreign.stream_id) == (foreign,)


@pytest.mark.parametrize(
    "stream_id",
    (
        "00000000-0000-0000-0000-000000000014",
        HostileUUIDSubclass(int=20),
        20,
        UUIDDuck(),
    ),
)
def test_load_stream_events_rejects_non_exact_uuid_before_lookup(
    stream_id: object,
) -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1, stream_number=20, sequence=1)
    repository.append(event, OutboxIntent(event_id=event.event_id, topic="signal"))

    with pytest.raises(ReplayError, match="stream_id must be an exact UUID"):
        repository.load_stream_events(stream_id)  # type: ignore[arg-type]


def test_inbox_rejects_unknown_event_identity_like_database_foreign_key() -> None:
    repository = InMemoryEventLedger()

    with pytest.raises(EventConflictError):
        repository.claim_inbox("consumer", envelope(signal(), event_number=999).event_id)


def test_snapshot_save_load_verifies_hash_is_idempotent_and_never_overwrites() -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1)
    snapshot = snapshot_from_result(replay((event,)))
    repository.save_snapshot(snapshot)
    assert repository.load_snapshot(snapshot.state_hash) == snapshot
    repository.save_snapshot(snapshot)
    assert repository.load_snapshot(snapshot.state_hash) == snapshot

    conflicting_event = event.model_copy(update={"event_id": envelope(signal(), event_number=2).event_id, "source": "conflict"})
    conflicting = snapshot_from_result(replay((conflicting_event,)))
    repository.save_snapshot(conflicting)
    assert repository.load_snapshot(conflicting.state_hash) == conflicting
    with pytest.raises(EventConflictError):
        repository.save_snapshot(snapshot.model_copy(update={"state_hash": "0" * 64}))


def test_global_snapshots_are_content_addressed_and_preserve_history() -> None:
    repository = InMemoryEventLedger()
    first = envelope(signal(), event_number=1, sequence=1)
    second = envelope(fill(), event_number=2, sequence=2)
    first_snapshot = snapshot_from_result(replay((first,)))
    later_snapshot = snapshot_from_result(replay((first, second)))

    repository.save_snapshot(first_snapshot)
    repository.save_snapshot(later_snapshot)

    assert repository.load_snapshot(first_snapshot.state_hash) == first_snapshot
    assert repository.load_snapshot(later_snapshot.state_hash) == later_snapshot
    assert repository.load_snapshot("f" * 64) is None
    repository.save_snapshot(later_snapshot)


def test_snapshot_repository_normalizes_forged_model_construct_instances() -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1)
    snapshot = snapshot_from_result(replay((event,)))
    raw_fields = {
        field_name: getattr(snapshot, field_name)
        for field_name in type(snapshot).model_fields
    }
    forged = type(snapshot).model_construct(**raw_fields)

    repository.save_snapshot(forged)
    loaded = repository.load_snapshot(snapshot.state_hash)

    assert loaded is not None
    assert loaded == snapshot
    assert loaded is not forged
    assert type(loaded.state) is type(snapshot.state)


@pytest.mark.parametrize("consumer", (None, True, 1, object()))
def test_inbox_rejects_non_string_consumers_with_typed_error(consumer: object) -> None:
    repository = InMemoryEventLedger()
    event = envelope(signal(), event_number=1)
    repository.append(event, OutboxIntent(event_id=event.event_id, topic="topic"))

    with pytest.raises(ReplayError):
        repository.claim_inbox(consumer, event.event_id)  # type: ignore[arg-type]


def test_repository_rejects_structurally_forged_snapshot_with_recomputed_hash() -> None:
    repository = InMemoryEventLedger()
    first = envelope(signal(), event_number=1, sequence=1)
    gap = envelope(fill(), event_number=2, sequence=3)
    snapshot = snapshot_from_result(replay((first, gap), policy=ReducerPolicy.DEGRADED))
    issue = snapshot.issues[0]

    invalid_states = (
        snapshot.state.model_copy(update={"event_count": 2}),
        snapshot.state.model_copy(update={"applied_events": (snapshot.state.applied_events[0], snapshot.state.applied_events[0])}),
        snapshot.state.model_copy(update={"type_counts": (EventTypeCount(event_type="SignalProposal", count=2),)}),
    )
    for state in invalid_states:
        canonical_json = canonical_state_json(state, snapshot.status, snapshot.issues)
        forged = snapshot.model_copy(update={"state": state, "canonical_state_json": canonical_json, "state_hash": event_digest(canonical_json)})
        with pytest.raises(EventConflictError):
            repository.save_snapshot(forged)

    wrong_status_json = canonical_state_json(snapshot.state, ReplayStatus.COMPLETE, (issue,))
    wrong_status = snapshot.model_copy(update={"status": ReplayStatus.COMPLETE, "canonical_state_json": wrong_status_json, "state_hash": event_digest(wrong_status_json)})
    with pytest.raises(EventConflictError):
        repository.save_snapshot(wrong_status)


@pytest.mark.parametrize("payload_json", (
    "{", '{"a":1,"a":2}', '{"b":1, "a":2}', '{"b":1,"a":2}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
    '{"value":1.0}', '{"value":1.5}', '{"value":1e+20}',
    r'{"value":"\u0000"}', r'{"value":"\ud800"}', r'{"value":"\udc00"}',
))
def test_outbox_payload_json_must_be_canonical_json(payload_json: str) -> None:
    with pytest.raises(ValidationError):
        OutboxIntent(event_id=envelope(signal(), event_number=1).event_id, topic="topic", payload_json=payload_json)


def test_outbox_accepts_canonical_payload_json() -> None:
    intent = OutboxIntent(event_id=envelope(signal(), event_number=1).event_id, topic="topic", payload_json='{"a":[1,true],"b":2}')
    assert intent.payload_json == '{"a":[1,true],"b":2}'


def test_postgres_sql_boundary_is_one_atomic_function_call_and_loads_canonical_text() -> None:
    statement = PostgresLedgerSql.APPEND_EVENT_AND_OUTBOX.strip()
    assert statement.startswith("SELECT public.append_domain_event(")
    assert statement.count(";") == 1
    assert "INSERT" not in statement
    assert "digest" not in statement
    assert "canonical_event_text" in PostgresLedgerSql.LOAD_EVENTS
    assert "canonical_event" not in PostgresLedgerSql.LOAD_EVENTS.replace("canonical_event_text", "")

    stream_statement = PostgresLedgerSql.LOAD_STREAM_EVENTS
    assert "SELECT canonical_event_text FROM public.domain_events" in stream_statement
    assert "WHERE stream_id = %(stream_id)s" in stream_statement
    assert "ORDER BY sequence, event_id" in stream_statement


def test_postgres_sql_boundary_has_snapshot_and_inbox_parity() -> None:
    save_snapshot = PostgresLedgerSql.SAVE_SNAPSHOT.strip()
    assert save_snapshot.startswith("SELECT public.save_domain_snapshot(")
    assert save_snapshot.count(";") == 1
    assert "INSERT" not in save_snapshot

    claim_inbox = PostgresLedgerSql.CLAIM_INBOX.strip()
    assert claim_inbox.count(";") == 1
    assert "INSERT INTO public.consumer_inbox" in claim_inbox
    assert "ON CONFLICT DO NOTHING" in claim_inbox
    assert "EXISTS" in claim_inbox

    acknowledge = PostgresLedgerSql.ACKNOWLEDGE_OUTBOX.strip()
    assert acknowledge.startswith("SELECT public.acknowledge_domain_publication(")
    assert acknowledge.count(";") == 1
    assert "DELETE" not in acknowledge

    assert "canonical_state_json" in save_snapshot
    assert "stream_id" not in save_snapshot
    assert "last_sequence" not in save_snapshot

    load_snapshot = PostgresLedgerSql.LOAD_SNAPSHOT
    assert "public.aggregate_snapshots" in load_snapshot
    assert "WHERE state_hash = %(state_hash)s" in load_snapshot
    assert "canonical_state_json" in load_snapshot
    assert "replay_schema_version" in load_snapshot
    assert "reducer_version" in load_snapshot
    assert "state_hash" in load_snapshot
