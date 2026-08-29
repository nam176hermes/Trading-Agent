from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from uuid import UUID

import pytest

from packages.engine_contracts import (
    EngineEvent,
    EventAttribute,
    EventFamily,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_runtime_contracts import semantic_digest
from packages.nautilus_runtime_contracts.result import _decode_event
from tests.jobs.test_engine_result_validation import _stdout
from tests.jobs.test_engine_event_ledger import _batch as _generic_batch
from tests.jobs.test_engine_event_ledger import _event as _generic_event
from tests.jobs.test_engine_event_ledger import _reconstructed_state
from tests.nautilus_runtime_contracts.test_result import (
    _batch,
    _p1_claim,
    _p1_request,
)


def _validated_p1_batch(root: Path, *, closure_digest: str = "a" * 64):
    from services.job_worker.engine_results import EngineResultValidator

    raw, envelopes = _batch()
    if closure_digest != "a" * 64:
        typed_events = tuple(_decode_event(envelope) for envelope in envelopes)
        start = typed_events[0].model_copy(
            update={"closure_digest": closure_digest}
        )
        completion = typed_events[-1].model_copy(
            update={"closure_digest": closure_digest, "semantic_digest": "0" * 64}
        )
        pending = (start, *typed_events[1:-1], completion)
        typed_events = pending[:-1] + (
            completion.model_copy(
                update={"semantic_digest": semantic_digest(pending)}
            ),
        )
        rebuilt = []
        for envelope, event in zip(envelopes, typed_events, strict=True):
            document = event.model_dump(mode="json")
            payload = EngineEvent(
                event_type=event.event_type,
                family=envelope.payload.family,
                attributes=tuple(
                    EventAttribute(
                        name=name,
                        value=(
                            canonical_json_bytes(value).decode("utf-8")
                            if type(value) is list
                            else value
                        ),
                    )
                    for name, value in document.items()
                    if name != "event_type" and value is not None
                ),
            )
            rebuilt.append(
                envelope.model_copy(
                    update={
                        "payload": payload,
                        "payload_digest": payload_digest(payload),
                    }
                )
            )
        envelopes = tuple(rebuilt)
        raw = b"".join(
            canonical_json_bytes(envelope) + b"\n" for envelope in envelopes
        )
    return EngineResultValidator(
        root,
        p1_product_closure_sha256=closure_digest,
    ).validate(
        "nautilus-p1-event-stream-v1",
        _p1_claim(),
        request=_p1_request(),
        stdout=_stdout(root, raw),
        exit_code=0,
    )


def test_p1_batch_is_atomic_idempotent_and_recoverable(tmp_path: Path) -> None:
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    repository = InMemoryEngineEventLedger()

    receipt = repository.ingest(batch)
    projection = repository.load_projection(batch.events[0].engine_run_id)

    assert projection is not None
    assert projection.batch_sha256 == batch.sha256
    assert projection.semantic_digest == batch.profile_result.semantic_sha256
    assert projection.request_message_id == UUID(
        batch.validation_metadata["request_message_id"]
    )
    assert repository.ingest(batch) is receipt
    restarted = InMemoryEngineEventLedger(repository.export_state())
    assert restarted.load_receipt(batch.sha256) == receipt
    assert restarted.load_projection(batch.events[0].engine_run_id) == projection
    assert restarted.replay_projection(batch.events[0].engine_run_id) == projection


@pytest.mark.parametrize(
    "mutation",
    ("metadata", "profile", "semantic", "closure"),
)
def test_p1_batch_rejects_any_profile_or_metadata_drift(
    tmp_path: Path, mutation: str
) -> None:
    from packages.engine_event_ledger import InvalidEngineEventBatchError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path / mutation)
    assert batch.profile_result is not None
    if mutation == "metadata":
        changed = replace(
            batch,
            validation_metadata={
                **batch.validation_metadata,
                "target_count": batch.profile_result.target_count + 1,
            },
        )
    elif mutation == "profile":
        changed = replace(batch, profile_result=None)
    elif mutation == "semantic":
        changed_profile = replace(batch.profile_result, semantic_sha256="f" * 64)
        changed = replace(
            batch,
            profile_result=changed_profile,
            validation_metadata={
                **batch.validation_metadata,
                "semantic_digest": "f" * 64,
            },
        )
    else:
        changed_profile = replace(
            batch.profile_result,
            product_closure_sha256="f" * 64,
        )
        changed = replace(
            batch,
            profile_result=changed_profile,
            validation_metadata={
                **batch.validation_metadata,
                "p1_product_closure_sha256": "f" * 64,
            },
        )

    with pytest.raises(InvalidEngineEventBatchError):
        InMemoryEngineEventLedger().ingest(changed)


def test_p1_batch_rejects_resealed_wrong_payload_digest(tmp_path: Path) -> None:
    from hashlib import sha256

    from packages.engine_event_ledger import InvalidEngineEventBatchError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    assert batch.profile_result is not None
    changed_events = (
        batch.events[0].model_copy(update={"payload_digest": "f" * 64}),
        *batch.events[1:],
    )
    raw = b"".join(
        canonical_json_bytes(event) + b"\n" for event in changed_events
    )
    digest = sha256(raw).hexdigest()
    changed = replace(
        batch,
        events=changed_events,
        sha256=digest,
        size_bytes=len(raw),
        relative_ref=(
            f"engine-results/{batch.validation_metadata['job_id']}/"
            f"{batch.validation_metadata['attempt_id']}/{digest}.jsonl"
        ),
        profile_result=replace(batch.profile_result, batch_sha256=digest),
    )

    with pytest.raises(InvalidEngineEventBatchError):
        InMemoryEngineEventLedger().ingest(changed)


def test_p1_result_keeps_shared_message_and_sequence_conflicts(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import (
        EngineEventConflictError,
    )
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    repository = InMemoryEngineEventLedger()
    repository.ingest(batch)
    first = batch.events[0]

    conflicting = _generic_batch(
        _generic_event(
            first.stream_sequence,
            "BacktestChanged",
            family=EventFamily.ENGINE_LIFECYCLE,
            message_id=first.message_id,
            engine_run_id=first.engine_run_id,
        )
    )
    with pytest.raises(EngineEventConflictError):
        repository.ingest(conflicting)

    gapped = _generic_batch(
        _generic_event(
            batch.events[-1].stream_sequence + 2,
            "BacktestContinued",
            engine_run_id=first.engine_run_id,
        )
    )
    with pytest.raises(EngineEventConflictError):
        repository.ingest(gapped)


def test_p1_job_receipt_binding_survives_restart(tmp_path: Path) -> None:
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    repository = InMemoryEngineEventLedger()
    receipt = repository.ingest_for_job(batch, claimed=_p1_claim())

    restarted = InMemoryEngineEventLedger(repository.export_state())
    assert restarted.load_job_receipt(_p1_claim().job_id) == receipt
    assert restarted.ingest_for_job(batch, claimed=_p1_claim()) == receipt


def test_completed_p1_projection_cannot_be_advanced_or_duplicated(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    repository = InMemoryEngineEventLedger()
    repository.ingest(batch)
    after_completion = _generic_batch(
        _generic_event(
            batch.events[-1].stream_sequence + 1,
            "BacktestContinued",
            engine_run_id=batch.events[0].engine_run_id,
        )
    )

    with pytest.raises(EngineEventConflictError):
        repository.ingest(after_completion)

    state = repository.export_state()
    duplicated = state.model_copy(
        update={"projections": (state.projections[0], state.projections[0])}
    )
    with pytest.raises(EngineEventConflictError):
        InMemoryEngineEventLedger(duplicated)


def test_restart_rejects_tampered_p1_receipt_ingestion_digest(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    repository.ingest(_validated_p1_batch(tmp_path))
    state = repository.export_state()
    tampered = state.model_copy(
        update={
            "receipts": (
                state.receipts[0].model_copy(update={"ingestion_digest": "f" * 64}),
            )
        }
    )

    with pytest.raises(EngineEventConflictError):
        InMemoryEngineEventLedger(tampered)


def test_restart_rejects_tampered_p1_stored_event_identity(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    repository.ingest(_validated_p1_batch(tmp_path))
    state = repository.export_state()
    tampered = state.model_copy(
        update={
            "events": (
                state.events[0].model_copy(update={"digest": "f" * 64}),
                *state.events[1:],
            )
        }
    )

    with pytest.raises(EngineEventConflictError):
        InMemoryEngineEventLedger(tampered)


def test_restart_rejects_p1_events_downgraded_to_generic_receipt(
    tmp_path: Path,
) -> None:
    from hashlib import sha256

    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    repository = InMemoryEngineEventLedger()
    repository.ingest_for_job(batch, claimed=_p1_claim())
    state = repository.export_state()
    first = batch.events[0]
    last = batch.events[-1]
    metadata = {
        "attempt_id": state.receipts[0].attempt_id,
        "config_digest": first.config_digest,
        "engine_run_id": str(first.engine_run_id),
        "event_count": len(batch.events),
        "first_sequence": first.stream_sequence,
        "job_id": state.receipts[0].job_id,
        "last_sequence": last.stream_sequence,
        "request_message_id": str(first.causation_id),
        "source_commit": first.source_commit,
        "validator_id": "engine-event-v1",
    }
    identity = canonical_json_bytes(
        {
            "artifact_type": "engine_event_batch",
            "media_type": "application/x-ndjson",
            "relative_ref": batch.relative_ref,
            "sha256": batch.sha256,
            "size_bytes": batch.size_bytes,
            "truncated": False,
            "validation_metadata": metadata,
            "validator_id": "engine-event-v1",
        }
    )
    downgraded = state.model_copy(
        update={
            "projections": (),
            "receipts": (
                state.receipts[0].model_copy(
                    update={"ingestion_digest": sha256(identity).hexdigest()}
                ),
            ),
        }
    )

    with pytest.raises(EngineEventConflictError):
        InMemoryEngineEventLedger(downgraded)


def test_direct_ingest_rejects_p1_events_downgraded_to_generic_validator(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import InvalidEngineEventBatchError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    batch = _validated_p1_batch(tmp_path)
    first = batch.events[0]
    last = batch.events[-1]
    downgraded = replace(
        batch,
        validator_id="engine-event-v1",
        validation_metadata={
            "attempt_id": batch.validation_metadata["attempt_id"],
            "config_digest": first.config_digest,
            "engine_run_id": str(first.engine_run_id),
            "event_count": len(batch.events),
            "first_sequence": first.stream_sequence,
            "job_id": batch.validation_metadata["job_id"],
            "last_sequence": last.stream_sequence,
            "request_message_id": str(first.causation_id),
            "source_commit": first.source_commit,
            "validator_id": "engine-event-v1",
        },
        profile_result=None,
    )

    with pytest.raises(InvalidEngineEventBatchError):
        InMemoryEngineEventLedger().ingest(downgraded)


def test_restart_rejects_append_after_completed_p1_projection(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import EngineEventConflictError
    from services.job_store.engine_event_repository import InMemoryEngineEventLedger

    repository = InMemoryEngineEventLedger()
    batch = _validated_p1_batch(tmp_path)
    repository.ingest(batch)
    state = repository.export_state()
    appended = _reconstructed_state(
        _generic_event(
            batch.events[-1].stream_sequence + 1,
            "BacktestContinued",
            engine_run_id=batch.events[0].engine_run_id,
        )
    )
    tampered = state.model_copy(
        update={
            "events": state.events + appended.events,
            "receipts": state.receipts + appended.receipts,
        }
    )

    with pytest.raises(EngineEventConflictError):
        InMemoryEngineEventLedger(tampered)


def test_postgres_adapter_carries_only_closed_p1_projection_authority(
    tmp_path: Path,
) -> None:
    from packages.engine_event_ledger import InvalidEngineEventBatchError
    from services.job_store.engine_event_repository import (
        InMemoryEngineEventLedger,
        PostgresEngineEventLedger,
        PostgresEngineEventLedgerSql,
    )
    from tests.jobs.test_engine_event_postgres_repository import (
        Connection,
        Pool,
        _receipt_row,
    )

    batch = _validated_p1_batch(tmp_path)
    memory = InMemoryEngineEventLedger()
    expected = memory.ingest(batch)
    projection = memory.load_projection(batch.events[0].engine_run_id)
    assert projection is not None
    connection = Connection([[_receipt_row(expected)]])

    repository = PostgresEngineEventLedger(Pool(connection))
    with pytest.raises(InvalidEngineEventBatchError, match="ingest_for_job"):
        repository.ingest(batch)
    assert connection.executions == []

    assert repository.ingest_for_job(batch, claimed=_p1_claim()) == expected

    statement, params = connection.executions[0]
    assert statement == PostgresEngineEventLedgerSql.INGEST_JOB_RESULT
    assert "ingest_engine_job_result_v2" in statement
    document = json.loads(params["batch_document"])
    assert document["batch_sha256"] == batch.sha256
    assert document["validator_id"] == batch.validator_id
    assert document["validation_metadata"] == batch.validation_metadata
    assert document["validation_metadata"]["semantic_digest"] == (
        projection.semantic_digest
    )


def test_postgres_projection_reads_durable_p1_batch_and_semantic_digest(
    tmp_path: Path,
) -> None:
    from services.job_store.engine_event_repository import (
        InMemoryEngineEventLedger,
        PostgresEngineEventLedger,
    )
    from tests.jobs.test_engine_event_postgres_repository import Connection, Pool

    batch = _validated_p1_batch(tmp_path)
    memory = InMemoryEngineEventLedger()
    memory.ingest(batch)
    projection = memory.load_projection(batch.events[0].engine_run_id)
    assert projection is not None
    row = projection.model_dump(mode="python")
    row["event_type_counts"] = [
        value.model_dump(mode="python") for value in projection.event_type_counts
    ]

    loaded = PostgresEngineEventLedger(Pool(Connection([[row]]))).load_projection(
        projection.engine_run_id
    )

    assert loaded == projection
