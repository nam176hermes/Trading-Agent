from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest

from packages.job_contracts import EnqueueJobRequest
from services.job_store import JobRepository, JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


def _settings(database) -> JobStoreSettings:
    return JobStoreSettings(
        host=database.host, port=database.port, database=database.database,
        user=database.user, password=database.password, pool_max=4,
    )


def _request(key: str, priority: int) -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate({
        "job_type": "SNAPSHOT",
        "payload": {"scope": "default", "requested_as_of": None},
        "idempotency_key": key,
        "actor": {"actor_type": "OPERATOR", "actor_id": "claims-test"},
        "priority": priority,
    })


@pytest.fixture
def stores():
    with disposable_database(operation_id="jobs-worker-claims-v1") as database:
        upgrade_to_head(database)
        api = _settings(disposable_role_settings(database, "trading_job_api"))
        worker = _settings(disposable_role_settings(database, "trading_job_worker"))
        with JobRepository(api) as jobs, WorkerRepository(worker) as workers:
            yield jobs, workers, database


def test_claim_orders_by_priority_then_fifo_and_is_atomic(stores) -> None:
    jobs, workers, database = stores
    oldest = jobs.enqueue(_request("claim:oldest", 20), trace_id="enqueue-old").job
    newer = jobs.enqueue(_request("claim:newer", 20), trace_id="enqueue-new").job
    jobs.enqueue(_request("claim:low", 5), trace_id="enqueue-low")
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "UPDATE jobs SET requested_at = %s WHERE job_id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=2), oldest.job_id),
        )
        connection.execute(
            "UPDATE jobs SET requested_at = %s WHERE job_id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), newer.job_id),
        )

    claim = workers.claim_next("worker-a", lease_seconds=30, trace_id="claim-a")

    assert claim is not None
    assert claim.job_id == oldest.job_id
    assert claim.attempt_number == 1
    assert claim.lease_token
    with psycopg.connect(database.conninfo()) as connection:
        job = connection.execute(
            "SELECT state, attempt_count, lease_owner, lease_token FROM jobs WHERE job_id = %s",
            (oldest.job_id,),
        ).fetchone()
        attempt = connection.execute(
            "SELECT job_id, attempt_number, worker_id, outcome, lease_token FROM job_attempts WHERE attempt_id = %s",
            (claim.attempt_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT sequence, from_state, to_state, reason_code, trace_id, attempt_id, metadata FROM job_events WHERE job_id = %s ORDER BY sequence",
            (oldest.job_id,),
        ).fetchall()
    assert job == ("CLAIMED", 1, "worker-a", claim.lease_token)
    assert attempt == (oldest.job_id, 1, "worker-a", "CLAIMED", claim.lease_token)
    assert [(row[0], row[1], row[2], row[3], row[4], row[5]) for row in event] == [
        (1, None, "QUEUED", "ENQUEUED", "enqueue-old", None),
        (2, "QUEUED", "CLAIMED", "CLAIMED", "claim-a", claim.attempt_id),
    ]
    assert event[-1][6]["lease_token_sha256"] != claim.lease_token
    assert len(event[-1][6]["lease_token_sha256"]) == 64


def test_skip_locked_claims_next_job_and_never_double_claims(stores) -> None:
    jobs, workers, database = stores
    first = jobs.enqueue(_request("claim:locked", 50), trace_id="enqueue-locked").job
    second = jobs.enqueue(_request("claim:available", 40), trace_id="enqueue-available").job

    with psycopg.connect(database.conninfo(), autocommit=False) as blocker:
        blocker.execute("SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE", (first.job_id,))
        claimed = workers.claim_next("worker-b", 30, "claim-skip")
        assert claimed is not None and claimed.job_id == second.job_id
        blocker.rollback()

    next_claim = workers.claim_next("worker-c", 30, "claim-next")
    assert next_claim is not None and next_claim.job_id == first.job_id
    assert workers.claim_next("worker-d", 30, "claim-empty") is None


def test_two_simultaneous_claim_transactions_assign_one_job_once(stores) -> None:
    jobs, workers, database = stores
    job = jobs.enqueue(_request("claim:single", 10), trace_id="enqueue-single").job
    barrier = Barrier(2)

    def claim(index: int):
        barrier.wait(timeout=5)
        return workers.claim_next(f"worker-{index}", 30, f"claim-{index}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, (1, 2)))

    assert sum(claim is not None for claim in claims) == 1
    with psycopg.connect(database.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM job_attempts WHERE job_id = %s", (job.job_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT attempt_count FROM jobs WHERE job_id = %s", (job.job_id,)
        ).fetchone()[0] == 1


def test_claim_rolls_back_job_attempt_and_event_together(stores) -> None:
    jobs, workers, database = stores
    job = jobs.enqueue(_request("claim:rollback", 1), trace_id="enqueue-rb").job

    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "CREATE FUNCTION raise_claim_transition_fault() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'synthetic claim transition failure'; END $$"
        )
        connection.execute(
            "CREATE TRIGGER raise_claim_transition_fault "
            "BEFORE UPDATE ON jobs FOR EACH ROW "
            "WHEN (OLD.state = 'QUEUED' AND NEW.state = 'CLAIMED') "
            "EXECUTE FUNCTION raise_claim_transition_fault()"
        )

    with pytest.raises(
        psycopg.errors.RaiseException,
        match="synthetic claim transition failure",
    ):
        workers.claim_next("worker-rb", 30, "claim-rb")

    with psycopg.connect(database.conninfo()) as connection:
        row = connection.execute(
            "SELECT state, attempt_count, lease_owner FROM jobs WHERE job_id = %s", (job.job_id,)
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT count(*) FROM job_attempts WHERE job_id = %s", (job.job_id,)
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT count(*) FROM job_events WHERE job_id = %s", (job.job_id,)
        ).fetchone()[0]
    assert row == ("QUEUED", 0, None)
    assert attempt_count == 0
    assert event_count == 1
