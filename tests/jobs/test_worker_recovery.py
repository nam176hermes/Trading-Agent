from __future__ import annotations

import psycopg
import pytest

from packages.job_contracts import EnqueueJobRequest, JobState
from services.job_store import JobRepository, JobStoreSettings
from services.job_store.worker_repository import ProcessIdentity, WorkerRepository
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


class Inspector:
    def __init__(self, observed): self.observed = observed
    def inspect(self, pid: int): return self.observed


def _settings(database) -> JobStoreSettings:
    return JobStoreSettings(host=database.host, port=database.port, database=database.database, user=database.user, password=database.password, pool_max=3)


@pytest.fixture
def recovery_store():
    with disposable_database(operation_id="jobs-worker-recovery-v1") as database:
        upgrade_to_head(database)
        api = _settings(disposable_role_settings(database, "trading_job_api"))
        worker = _settings(disposable_role_settings(database, "trading_job_worker"))
        with JobRepository(api) as jobs, WorkerRepository(worker) as workers:
            yield jobs, workers, database


def _running(jobs, workers, key: str):
    request = EnqueueJobRequest.model_validate({"job_type": "SNAPSHOT", "payload": {"scope": "default", "requested_as_of": None}, "idempotency_key": key, "actor": {"actor_type": "OPERATOR", "actor_id": "recovery-test"}})
    job = jobs.enqueue(request, trace_id=f"enqueue-{key}").job
    claim = workers.claim_next("worker-recovery", 30, f"claim-{key}")
    identity = ProcessIdentity(5001, 5001, 777, "d" * 64)
    assert workers.start_attempt(job.job_id, claim.attempt_id, "worker-recovery", claim.lease_token, identity, f"start-{key}")
    return job, claim, identity


def _expire(database, job_id):
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE job_id = %s", (job_id,))


def test_matching_live_child_blocks_without_retry(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, _, identity = _running(jobs, workers, "live")
    _expire(database, job.job_id)

    outcomes = workers.recover_expired_leases(Inspector(identity), trace_id="recover-live")

    assert outcomes == ((job.job_id, "LEASE_EXPIRED_CHILD_STILL_RUNNING"),)
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.BLOCKED
    assert detail.job.attempt_count == 1
    assert detail.events[-1].reason_code == "LEASE_EXPIRED_CHILD_STILL_RUNNING"
    assert detail.events[-1].trace_id == "recover-live"


def test_absent_child_interrupts_then_requeues_when_eligible(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, claim, _ = _running(jobs, workers, "retry-absent")
    _expire(database, job.job_id)

    workers.recover_expired_leases(Inspector(None), trace_id="recover-retry")

    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.QUEUED
    assert detail.job.attempt_count == 1
    assert detail.attempts[0].outcome == "INTERRUPTED"
    assert [(e.from_state, e.to_state, e.reason_code) for e in detail.events[-2:]] == [
        (JobState.RUNNING, JobState.FAILED, "LEASE_EXPIRED_CHILD_ABSENT"),
        (JobState.FAILED, JobState.QUEUED, "LEASE_EXPIRED_RETRY_SCHEDULED"),
    ]


@pytest.mark.parametrize(
    "observed",
    [
        ProcessIdentity(5001, 5001, 778, "d" * 64),
        ProcessIdentity(5001, 5001, 777, "e" * 64),
    ],
)
def test_nonmatching_live_identity_blocks_without_retry(recovery_store, observed) -> None:
    jobs, workers, database = recovery_store
    job, _, _ = _running(jobs, workers, f"mismatch-{observed.start_ticks}")
    _expire(database, job.job_id)

    outcomes = workers.recover_expired_leases(
        Inspector(observed), trace_id="recover-mismatch"
    )

    assert outcomes == ((job.job_id, "LEASE_EXPIRED_CHILD_IDENTITY_MISMATCH"),)
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.BLOCKED
    assert detail.attempts[0].outcome == "BLOCKED"


def test_incomplete_identity_blocks_as_unverifiable(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, claim, _ = _running(jobs, workers, "incomplete")
    _expire(database, job.job_id)
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "UPDATE job_attempts SET command_fingerprint = NULL WHERE attempt_id = %s",
            (claim.attempt_id,),
        )

    outcomes = workers.recover_expired_leases(
        Inspector(None), trace_id="recover-unverifiable"
    )

    assert outcomes == ((job.job_id, "LEASE_EXPIRED_CHILD_IDENTITY_UNVERIFIABLE"),)
    assert jobs.get_job(job.job_id).job.state is JobState.BLOCKED


def test_attempts_exhausted_stays_failed(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, _, _ = _running(jobs, workers, "exhausted")
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "UPDATE jobs SET max_attempts = attempt_count WHERE job_id = %s",
            (job.job_id,),
        )
    _expire(database, job.job_id)
    workers.recover_expired_leases(Inspector(None), trace_id="recover-exhausted")
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.FAILED
    assert detail.job.reason_code == "LEASE_EXPIRED_ATTEMPTS_EXHAUSTED"
    assert detail.attempts[0].outcome == "INTERRUPTED"


def test_possible_result_requires_reconciliation_not_retry(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, claim, _ = _running(jobs, workers, "result")
    _expire(database, job.job_id)
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute("UPDATE jobs SET result_hash = %s, result_metadata = '{\"possible\":true}'::jsonb WHERE job_id = %s", ("f" * 64, job.job_id))
    workers.recover_expired_leases(Inspector(None), trace_id="recover-result")
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.BLOCKED
    assert detail.job.reason_code == "RESULT_RECONCILIATION_REQUIRED"
    assert detail.attempts[0].outcome == "BLOCKED"


def test_result_appearing_during_process_observation_blocks_stale_requeue(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, _, _ = _running(jobs, workers, "result-race")
    _expire(database, job.job_id)

    class ResultPublishingInspector:
        def inspect(self, pid: int):
            with psycopg.connect(database.conninfo()) as connection:
                connection.execute(
                    "UPDATE jobs SET result_hash = %s, "
                    "result_metadata = '{\"published_during_recovery\":true}'::jsonb "
                    "WHERE job_id = %s",
                    ("a" * 64, job.job_id),
                )
            return None

    outcomes = workers.recover_expired_leases(
        ResultPublishingInspector(), trace_id="recover-result-race"
    )

    assert outcomes == ((job.job_id, "RESULT_RECONCILIATION_REQUIRED"),)
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.BLOCKED
    assert detail.attempts[0].outcome == "BLOCKED"
    assert all(event.to_state is not JobState.QUEUED for event in detail.events[-1:])


def test_terminal_attempt_winning_during_observation_rejects_recovery_mutation(
    recovery_store,
) -> None:
    jobs, workers, database = recovery_store
    job, claim, _ = _running(jobs, workers, "attempt-race")
    _expire(database, job.job_id)
    before = len(jobs.get_job(job.job_id).events)

    class AttemptFinishingInspector:
        def inspect(self, pid: int):
            with psycopg.connect(database.conninfo()) as connection:
                connection.execute(
                    "UPDATE job_attempts SET outcome = 'INTERRUPTED', "
                    "finished_at = now() WHERE attempt_id = %s",
                    (claim.attempt_id,),
                )
            return None

    outcomes = workers.recover_expired_leases(
        AttemptFinishingInspector(), trace_id="recover-attempt-race"
    )

    assert outcomes == ((job.job_id, "LEASE_RECOVERY_STALE"),)
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.RUNNING
    assert detail.attempts[0].outcome == "INTERRUPTED"
    assert len(detail.events) == before


def test_terminal_attempt_before_candidate_scan_is_not_recovered(recovery_store) -> None:
    jobs, workers, database = recovery_store
    job, claim, _ = _running(jobs, workers, "terminal-before-scan")
    _expire(database, job.job_id)
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "UPDATE job_attempts SET outcome = 'INTERRUPTED', finished_at = now() "
            "WHERE attempt_id = %s",
            (claim.attempt_id,),
        )
    before = len(jobs.get_job(job.job_id).events)

    class InspectorMustNotRun:
        def inspect(self, pid: int):
            raise AssertionError("terminal attempts must not become recovery candidates")

    outcomes = workers.recover_expired_leases(
        InspectorMustNotRun(), trace_id="recover-terminal-before-scan"
    )

    assert outcomes == ()
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.RUNNING
    assert detail.attempts[0].outcome == "INTERRUPTED"
    assert len(detail.events) == before
