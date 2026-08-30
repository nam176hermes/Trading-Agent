from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Barrier
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
import pytest

from packages.engine_contracts import canonical_json_bytes
from packages.engine_event_ledger import EngineEventConflictError
from packages.job_contracts import canonical_payload_json, payload_fingerprint
from services.job_store.engine_event_repository import (
    PostgresEngineEventLedger,
    _validated_records,
)
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from tests.control_api._disposable_runtime import require_disposable_green
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)
from tests.jobs.test_engine_event_ledger import _batch, _event
from tests.engine_event_ledger.test_p1_stream_ingestion import _validated_p1_batch
from tests.nautilus_runtime_contracts.test_result import CODE_COMMIT, _p1_claim


OPERATION_ID = "engine-event-ingestion-concurrency-runtime-green-v1"
P1_PRODUCT_CLOSURE_SHA256 = (
    "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
)
RECEIPT_FIELDS = (
    "batch_sha256",
    "ingestion_digest",
    "job_id",
    "attempt_id",
    "engine_run_id",
    "event_count",
    "first_sequence",
    "last_sequence",
    "last_digest",
)
pytestmark = pytest.mark.runtime_postgres


@pytest.fixture(scope="module")
def engine_event_database():
    require_disposable_green()
    with disposable_database(operation_id=OPERATION_ID, planned=True) as owner:
        upgrade_to_head(owner)
        yield owner


def _semantic_receipt_bytes(receipt: dict[str, object]) -> bytes:
    return canonical_json_bytes(
        {
            field: str(receipt[field])
            if isinstance(receipt[field], UUID)
            else receipt[field]
            for field in RECEIPT_FIELDS
        }
    )


def _seed_running_job(owner, claim, *, idempotency_key: str) -> None:
    owner.execute(
        """
        INSERT INTO public.jobs (
          job_id, job_type, state, payload, payload_fingerprint,
          idempotency_key, actor_type, actor_id, priority,
          attempt_count, max_attempts, lease_owner, lease_token,
          lease_expires_at, reason_code
        ) VALUES (
          %s, 'BACKTEST', 'RUNNING', %s::jsonb, %s,
          %s, 'SYSTEM', %s, 0,
          1, %s, %s, %s, now() + interval '10 minutes', 'STARTED'
        )
        """,
        (
            claim.job_id,
            canonical_payload_json(claim.payload),
            payload_fingerprint(claim.payload),
            idempotency_key,
            idempotency_key,
            claim.max_attempts,
            claim.worker_id,
            claim.lease_token,
        ),
    )
    owner.execute(
        """
        INSERT INTO public.job_attempts (
          attempt_id, job_id, attempt_number, worker_id, outcome,
          lease_token, lease_expires_at, claimed_at, started_at,
          heartbeat_at
        ) VALUES (
          %s, %s, %s, %s, 'RUNNING', %s,
          now() + interval '10 minutes', now(), now(), now()
        )
        """,
        (
            claim.attempt_id,
            claim.job_id,
            claim.attempt_number,
            claim.worker_id,
            claim.lease_token,
        ),
    )


def test_concurrent_identical_engine_batch_is_idempotent_and_serialized(
    engine_event_database,
) -> None:
    batch = _batch(
        _event(2, "BacktestStarted"),
        _event(3, "BacktestCompleted"),
    )
    records, ingestion_digest, _projection_authority = _validated_records(batch)
    batch_document = PostgresEngineEventLedger._batch_document(
        batch,
        records,
        ingestion_digest,
    )
    barrier = Barrier(2)

    def ingest() -> dict[str, object]:
        with psycopg.connect(
            engine_event_database.conninfo(),
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            barrier.wait(timeout=10)
            receipt = connection.execute(
                "SELECT * FROM public.ingest_engine_event_batch(%s)",
                (batch_document,),
            ).fetchone()
        assert receipt is not None
        return receipt

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(ingest) for _ in range(2))
        receipts = tuple(future.result() for future in futures)

    assert _semantic_receipt_bytes(receipts[0]) == _semantic_receipt_bytes(
        receipts[1]
    )
    with psycopg.connect(engine_event_database.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.engine_event_batch_receipts",
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM public.engine_events",
        ).fetchone() == (len(batch.events),)
        assert connection.execute(
            "SELECT count(*) FROM public.engine_run_projections",
        ).fetchone() == (1,)


def test_worker_legacy_job_wrapper_preserves_generic_and_nautilus_v1(
    engine_event_database,
) -> None:
    base_claim = _p1_claim()
    generic_claim = replace(
        base_claim,
        job_id="job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        attempt_id="attempt_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        lease_token="legacy-generic_0123456789abcdefghijklmnopqrstuvwxyz",
    )
    nautilus_claim = replace(
        base_claim,
        job_id="job_cccccccccccccccccccccccccccccccc",
        attempt_id="attempt_dddddddddddddddddddddddddddddddd",
        lease_token="legacy-nautilus_0123456789abcdefghijklmnopqrstuvwxyz",
    )
    generic_batch = _batch(
        _event(
            2,
            "BacktestStarted",
            engine_run_id=UUID("50000000-0000-4000-8000-000000000001"),
        ),
        job_id=generic_claim.job_id,
        attempt_id=generic_claim.attempt_id,
    )
    nautilus_batch = _batch(
        _event(
            2,
            "NautilusBacktestCompleted",
            engine_run_id=UUID("60000000-0000-4000-8000-000000000001"),
        ),
        job_id=nautilus_claim.job_id,
        attempt_id=nautilus_claim.attempt_id,
        validator_id="nautilus-backtest-result-v1",
    )
    with psycopg.connect(engine_event_database.conninfo()) as owner:
        _seed_running_job(owner, generic_claim, idempotency_key="legacy-generic")
        _seed_running_job(owner, nautilus_claim, idempotency_key="legacy-nautilus")

    worker_database = disposable_role_settings(
        engine_event_database, "trading_job_worker"
    )
    worker_settings = JobStoreSettings(
        host=worker_database.host,
        port=worker_database.port,
        database=worker_database.database,
        user=worker_database.user,
        password=worker_database.password,
        pool_max=2,
    )
    with WorkerRepository(worker_settings) as worker_repository:
        repository = worker_repository.engine_event_ingestor()
        for claim, batch in (
            (generic_claim, generic_batch),
            (nautilus_claim, nautilus_batch),
        ):
            worker_repository.worker_heartbeat(
                claim.worker_id,
                CODE_COMMIT,
                "BUSY",
                current_job_id=claim.job_id,
                current_attempt_id=claim.attempt_id,
            )
            receipt = repository.ingest_for_job(batch, claimed=claim)
            assert repository.ingest_for_job(batch, claimed=claim) == receipt

    records, ingestion_digest, _projection = _validated_records(generic_batch)
    legacy_document = PostgresEngineEventLedger._batch_document(
        generic_batch, records, ingestion_digest
    )
    with psycopg.connect(
        worker_database.conninfo(), autocommit=True
    ) as worker_connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker_connection.execute(
                "SELECT * FROM job_plane.ingest_engine_job_result("
                "%s, %s, %s, %s, %s)",
                (
                    generic_claim.job_id,
                    generic_claim.attempt_id,
                    generic_claim.worker_id,
                    generic_claim.lease_token,
                    legacy_document,
                ),
            )


def test_p1_v2_ingestion_is_idempotent_conflict_safe_and_restart_recoverable(
    engine_event_database,
    tmp_path,
) -> None:
    claim = _p1_claim()
    batch = _validated_p1_batch(
        tmp_path,
        closure_digest=P1_PRODUCT_CLOSURE_SHA256,
    )
    assert batch.profile_result is not None
    run_id = batch.events[0].engine_run_id
    with psycopg.connect(engine_event_database.conninfo()) as owner:
        _seed_running_job(owner, claim, idempotency_key="p1-runtime-projection")

    def open_repository(database):
        pool = ConnectionPool(
            conninfo=database.conninfo(),
            min_size=1,
            max_size=2,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        pool.wait()
        return pool, PostgresEngineEventLedger(pool)

    worker_database = disposable_role_settings(
        engine_event_database, "trading_job_worker"
    )
    worker_settings = JobStoreSettings(
        host=worker_database.host,
        port=worker_database.port,
        database=worker_database.database,
        user=worker_database.user,
        password=worker_database.password,
        pool_max=2,
    )
    with WorkerRepository(worker_settings) as worker_repository:
        worker_repository.worker_heartbeat(
            claim.worker_id,
            CODE_COMMIT,
            "IDLE",
            current_job_id=None,
            current_attempt_id=None,
        )
        worker_repository.worker_heartbeat(
            claim.worker_id,
            CODE_COMMIT,
            "BUSY",
            current_job_id=claim.job_id,
            current_attempt_id=claim.attempt_id,
        )
        records, ingestion_digest, _projection = _validated_records(batch)
        p1_document = PostgresEngineEventLedger._batch_document(
            batch, records, ingestion_digest
        )
        stripped = json.loads(p1_document)
        stripped.pop("validation_metadata")
        stripped.pop("validator_id")
        stripped_document = json.dumps(
            stripped, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with psycopg.connect(
            worker_database.conninfo(), autocommit=True
        ) as worker_connection:
            with pytest.raises(psycopg.Error) as rejected:
                worker_connection.execute(
                    "SELECT * FROM job_plane.ingest_legacy_engine_job_result_v2("
                    "%s, %s, %s, %s, %s)",
                    (
                        claim.job_id,
                        claim.attempt_id,
                        claim.worker_id,
                        claim.lease_token,
                        stripped_document,
                    ),
                )
            assert rejected.value.sqlstate == "P2D04"
        repository = worker_repository.engine_event_ingestor()
        receipt = repository.ingest_for_job(batch, claimed=claim)
        assert repository.ingest_for_job(batch, claimed=claim) == receipt

    owner_pool, owner_repository = open_repository(engine_event_database)
    try:
        projection = owner_repository.load_projection(run_id)
        assert projection is not None
        assert projection.batch_sha256 == batch.sha256
        assert projection.semantic_digest == batch.profile_result.semantic_sha256
        assert projection.request_message_id == UUID(
            batch.validation_metadata["request_message_id"]
        )
        after_completion = _batch(
            _event(
                batch.events[-1].stream_sequence + 1,
                "BacktestContinued",
                engine_run_id=run_id,
            )
        )
        with pytest.raises(EngineEventConflictError):
            owner_repository.ingest(after_completion)
    finally:
        owner_pool.close()

    restarted_pool, restarted = open_repository(engine_event_database)
    try:
        assert restarted.load_receipt(batch.sha256) == receipt
        assert restarted.load_projection(run_id) == projection
        assert restarted.replay_projection(run_id) == projection
        assert projection in restarted.recover_projections()
    finally:
        restarted_pool.close()
