from __future__ import annotations

import hashlib
from dataclasses import fields, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.engine_contracts import (
    EngineEvent,
    EngineEventEnvelope,
    EventFamily,
    canonical_json_bytes,
    payload_digest,
)
from services.job_worker.engine_results import ValidatedEngineEventBatch


NOW = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-4000-8000-000000000001")
CORRELATION_ID = UUID("20000000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("30000000-0000-4000-8000-000000000001")
CODE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
CONFIG_DIGEST = "4" * 64
JOB_ID = "job_0123456789abcdef0123456789abcdef"
ATTEMPT_ID = "attempt_fedcba9876543210fedcba9876543210"


def _event(
    sequence: int,
    event_type: str,
    *,
    message_id: UUID | None = None,
    family: EventFamily = EventFamily.ENGINE_LIFECYCLE,
) -> EngineEventEnvelope:
    payload = EngineEvent(event_type=event_type, family=family)
    return EngineEventEnvelope(
        message_id=message_id
        or UUID(f"40000000-0000-4000-8000-{sequence:012d}"),
        correlation_id=CORRELATION_ID,
        causation_id=REQUEST_ID,
        engine_run_id=RUN_ID,
        stream_sequence=sequence,
        event_time=NOW,
        initialization_time=NOW,
        schema_version="1.0.0",
        producer_identity="engine-fixture",
        source_commit=CODE_COMMIT,
        config_digest=CONFIG_DIGEST,
        payload_digest=payload_digest(payload),
        payload=payload,
    )


def _batch(*events: EngineEventEnvelope) -> ValidatedEngineEventBatch:
    raw = b"".join(canonical_json_bytes(event) + b"\n" for event in events)
    digest = hashlib.sha256(raw).hexdigest()
    return ValidatedEngineEventBatch(
        artifact_type="engine_event_batch",
        relative_ref=f"engine-results/{JOB_ID}/{ATTEMPT_ID}/{digest}.jsonl",
        sha256=digest,
        size_bytes=len(raw),
        media_type="application/x-ndjson",
        truncated=False,
        validator_id="engine-event-v1",
        validation_metadata={
            "attempt_id": ATTEMPT_ID,
            "config_digest": CONFIG_DIGEST,
            "engine_run_id": str(RUN_ID),
            "event_count": len(events),
            "first_sequence": events[0].stream_sequence,
            "job_id": JOB_ID,
            "last_sequence": events[-1].stream_sequence,
            "request_message_id": str(REQUEST_ID),
            "source_commit": CODE_COMMIT,
            "validator_id": "engine-event-v1",
        },
        events=tuple(events),
    )


def test_ingest_records_canonical_events_receipt_and_deterministic_projection() -> None:
    from packages.engine_event_ledger import EngineEventTypeCount, EngineRunProjection
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _batch(
        _event(2, "BacktestStarted"),
        _event(3, "OrderAccepted", family=EventFamily.ORDER_LIFECYCLE),
        _event(4, "OrderAccepted", family=EventFamily.ORDER_LIFECYCLE),
    )
    repository = InMemoryEngineEventLedger()

    receipt = repository.ingest(batch)

    assert receipt.batch_sha256 == batch.sha256
    assert receipt.engine_run_id == RUN_ID
    assert receipt.first_sequence == 2
    assert receipt.last_sequence == 4
    assert receipt.event_count == 3
    assert repository.load_receipt(batch.sha256) == receipt
    stored = repository.load_events(RUN_ID)
    assert tuple(event.message_id for event in stored) == tuple(
        event.message_id for event in batch.events
    )
    assert tuple(event.canonical_json.encode() for event in stored) == tuple(
        canonical_json_bytes(event) for event in batch.events
    )
    assert repository.load_projection(RUN_ID) == EngineRunProjection(
        engine_run_id=RUN_ID,
        event_count=3,
        event_type_counts=(
            EngineEventTypeCount(event_type="BacktestStarted", count=1),
            EngineEventTypeCount(event_type="OrderAccepted", count=2),
        ),
        last_sequence=4,
        last_digest=stored[-1].digest,
    )


def test_exact_batch_retry_returns_original_receipt_without_projection_effects() -> None:
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _batch(_event(2, "BacktestStarted"), _event(3, "BacktestCompleted"))
    repository = InMemoryEngineEventLedger()
    original = repository.ingest(batch)
    events_before = repository.load_events(RUN_ID)
    projection_before = repository.load_projection(RUN_ID)

    duplicate = repository.ingest(batch)

    assert duplicate is original
    assert repository.load_events(RUN_ID) == events_before
    assert repository.load_projection(RUN_ID) == projection_before


def test_message_identity_with_changed_canonical_content_conflicts_and_rolls_back() -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    original_event = _event(2, "BacktestStarted")
    original_batch = _batch(original_event)
    repository = InMemoryEngineEventLedger()
    original_receipt = repository.ingest(original_batch)
    conflicting_batch = _batch(
        _event(2, "BacktestChanged", message_id=original_event.message_id)
    )

    with pytest.raises(EngineEventConflictError):
        repository.ingest(conflicting_batch)

    assert repository.load_events(RUN_ID)[0].event_type == "BacktestStarted"
    assert repository.load_receipt(original_batch.sha256) is original_receipt
    assert repository.load_receipt(conflicting_batch.sha256) is None
    assert repository.load_projection(RUN_ID).event_count == 1


@pytest.mark.parametrize(
    ("sequence", "expected_reason"),
    ((4, "SEQUENCE_GAP"), (1, "SEQUENCE_REGRESSION")),
)
def test_noncontiguous_next_batch_is_typed_blocked_without_advancing_projection(
    sequence: int,
    expected_reason: str,
) -> None:
    from packages.engine_event_ledger import EngineEventSequenceBlockedError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    repository.ingest(_batch(_event(2, "BacktestStarted")))
    projection_before = repository.load_projection(RUN_ID)
    blocked_batch = _batch(_event(sequence, "BacktestContinued"))

    with pytest.raises(EngineEventSequenceBlockedError) as raised:
        repository.ingest(blocked_batch)

    assert raised.value.reason.value == expected_reason
    assert raised.value.engine_run_id == RUN_ID
    assert raised.value.expected_sequence == 3
    assert raised.value.actual_sequence == sequence
    assert repository.load_receipt(blocked_batch.sha256) is None
    assert repository.load_projection(RUN_ID) == projection_before
    assert len(repository.load_events(RUN_ID)) == 1


def test_gap_inside_batch_rolls_back_every_event_receipt_and_projection() -> None:
    from packages.engine_event_ledger import EngineEventSequenceBlockedError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    repository.ingest(_batch(_event(2, "BacktestStarted")))
    projection_before = repository.load_projection(RUN_ID)
    blocked_batch = _batch(
        _event(3, "OrderAccepted"),
        _event(5, "OrderFilled"),
    )

    with pytest.raises(EngineEventSequenceBlockedError) as raised:
        repository.ingest(blocked_batch)

    assert raised.value.expected_sequence == 4
    assert raised.value.actual_sequence == 5
    assert tuple(event.stream_sequence for event in repository.load_events(RUN_ID)) == (2,)
    assert repository.load_receipt(blocked_batch.sha256) is None
    assert repository.load_projection(RUN_ID) == projection_before


def test_first_batch_must_begin_at_first_engine_event_sequence() -> None:
    from packages.engine_event_ledger import EngineEventSequenceBlockedError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    blocked_batch = _batch(_event(3, "BacktestStarted"))

    with pytest.raises(EngineEventSequenceBlockedError) as raised:
        repository.ingest(blocked_batch)

    assert raised.value.reason.value == "SEQUENCE_GAP"
    assert raised.value.expected_sequence == 2
    assert raised.value.actual_sequence == 3
    assert repository.load_events(RUN_ID) == ()
    assert repository.load_receipt(blocked_batch.sha256) is None


def test_ingest_accepts_only_an_exact_authentic_validated_batch() -> None:
    from packages.engine_event_ledger import InvalidEngineEventBatchError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _batch(_event(2, "BacktestStarted"))

    class BatchSubclass(ValidatedEngineEventBatch):
        pass

    subclass = BatchSubclass(
        *(getattr(batch, field.name) for field in fields(ValidatedEngineEventBatch))
    )
    forged_digest = replace(batch, sha256="0" * 64)
    repository = InMemoryEngineEventLedger()

    for rejected in (subclass, forged_digest, batch.events, b"raw child output"):
        with pytest.raises(InvalidEngineEventBatchError):
            repository.ingest(rejected)  # type: ignore[arg-type]

    assert repository.load_events(RUN_ID) == ()
    assert repository.load_projection(RUN_ID) is None


def test_same_canonical_batch_with_changed_receipt_authority_is_a_conflict() -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _batch(_event(2, "BacktestStarted"))
    repository = InMemoryEngineEventLedger()
    original = repository.ingest(batch)
    changed_metadata = dict(batch.validation_metadata)
    changed_metadata["job_id"] = "job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    changed = replace(
        batch,
        relative_ref=(
            "engine-results/job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"
            f"{ATTEMPT_ID}/{batch.sha256}.jsonl"
        ),
        validation_metadata=changed_metadata,
    )

    with pytest.raises(EngineEventConflictError):
        repository.ingest(changed)

    assert repository.load_receipt(batch.sha256) is original


def test_restart_recovers_receipts_and_projection_by_replaying_authoritative_events() -> None:
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    first = _batch(_event(2, "BacktestStarted"), _event(3, "OrderAccepted"))
    second = _batch(_event(4, "OrderFilled"), _event(5, "BacktestCompleted"))
    repository = InMemoryEngineEventLedger()
    first_receipt = repository.ingest(first)
    second_receipt = repository.ingest(second)
    projection = repository.load_projection(RUN_ID)

    restarted = InMemoryEngineEventLedger(repository.export_state())

    assert restarted.load_receipt(first.sha256) == first_receipt
    assert restarted.load_receipt(second.sha256) == second_receipt
    assert restarted.load_projection(RUN_ID) == projection
    assert restarted.replay_projection(RUN_ID) == projection
    assert restarted.recover_projections() == (projection,)
    assert restarted.ingest(second) == second_receipt
    assert restarted.load_projection(RUN_ID) == projection


def test_restart_replay_blocks_a_gap_in_authoritative_event_state() -> None:
    from packages.engine_event_ledger import EngineEventSequenceBlockedError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    repository.ingest(
        _batch(
            _event(2, "BacktestStarted"),
            _event(3, "OrderAccepted"),
            _event(4, "OrderFilled"),
        )
    )
    state = repository.export_state()
    gapped = state.model_copy(update={"events": (state.events[0], state.events[2])})

    with pytest.raises(EngineEventSequenceBlockedError) as raised:
        InMemoryEngineEventLedger(gapped)

    assert raised.value.expected_sequence == 3
    assert raised.value.actual_sequence == 4


@pytest.mark.parametrize("missing", ("receipt", "events"))
def test_restart_rejects_nonatomic_receipt_event_state(missing: str) -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    repository.ingest(_batch(_event(2, "BacktestStarted")))
    state = repository.export_state()
    broken = state.model_copy(
        update={"receipts": ()} if missing == "receipt" else {"events": ()}
    )

    with pytest.raises(EngineEventConflictError):
        InMemoryEngineEventLedger(broken)


def test_public_ledger_boundary_excludes_domain_and_provider_types() -> None:
    import packages.engine_event_ledger as ledger_api
    import services.job_store.engine_event_repository as repository_api

    assert repository_api.__all__ == [
        "EngineEventLedgerRepository",
        "InMemoryEngineEventLedger",
    ]
    assert "ValidatedEngineEventBatch" not in repository_api.__all__
    assert {
        "EventEnvelope",
        "StoredEvent",
        "InMemoryEventLedger",
        "NautilusTrader",
    }.isdisjoint(ledger_api.__all__)
    for name in ledger_api.__all__:
        module = getattr(ledger_api, name).__module__
        assert module.startswith("packages.engine_event_ledger")
