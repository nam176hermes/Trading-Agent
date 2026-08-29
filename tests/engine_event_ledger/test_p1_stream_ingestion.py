from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from packages.engine_contracts import EventFamily
from tests.jobs.test_engine_result_validation import _stdout
from tests.jobs.test_engine_event_ledger import _batch as _generic_batch
from tests.jobs.test_engine_event_ledger import _event as _generic_event
from tests.nautilus_runtime_contracts.test_result import (
    _batch,
    _p1_claim,
    _p1_request,
)


def _validated_p1_batch(root: Path):
    from services.job_worker.engine_results import EngineResultValidator

    raw, _events = _batch()
    return EngineResultValidator(
        root,
        p1_product_closure_sha256="a" * 64,
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


def test_postgres_adapter_carries_only_closed_p1_projection_authority(
    tmp_path: Path,
) -> None:
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

    assert PostgresEngineEventLedger(Pool(connection)).ingest(batch) == expected

    statement, params = connection.executions[0]
    assert statement == PostgresEngineEventLedgerSql.INGEST_BATCH
    assert "ingest_engine_event_batch_v2" in statement
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
