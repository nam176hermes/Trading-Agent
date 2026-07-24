from __future__ import annotations

import psycopg
import pytest

from packages.job_contracts import ActorIdentity, EnqueueJobRequest, JobState
from services.job_store import JobRepository, JobStoreSettings
from services.job_store.worker_repository import ProcessIdentity, WorkerRepository
from services.job_worker.artifacts import ArtifactMetadata
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


def _settings(database) -> JobStoreSettings:
    return JobStoreSettings(host=database.host, port=database.port, database=database.database, user=database.user, password=database.password, pool_max=3)


@pytest.fixture
def claimed():
    with disposable_database(operation_id="jobs-worker-leases-v1") as database:
        upgrade_to_head(database)
        api = _settings(disposable_role_settings(database, "trading_job_api"))
        worker = _settings(disposable_role_settings(database, "trading_job_worker"))
        request = EnqueueJobRequest.model_validate({"job_type": "SNAPSHOT", "payload": {"scope": "default", "requested_as_of": None}, "idempotency_key": "lease:test", "actor": {"actor_type": "OPERATOR", "actor_id": "lease-test"}})
        with JobRepository(api) as jobs, WorkerRepository(worker) as workers:
            job = jobs.enqueue(request, trace_id="lease-enqueue").job
            claim = workers.claim_next("worker-lease", 30, "lease-claim")
            yield jobs, workers, database, job, claim


def test_start_heartbeat_and_finalize_require_full_fence(claimed) -> None:
    jobs, workers, _, job, claim = claimed
    identity = ProcessIdentity(4321, 4321, 987654, "a" * 64)
    wrong_lease_token = "x" * 64

    assert not workers.start_attempt(job.job_id, claim.attempt_id, "worker-lease", wrong_lease_token, identity, "start-wrong")
    assert workers.start_attempt(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, identity, "start-ok")
    assert not workers.heartbeat(job.job_id, claim.attempt_id, "other-worker", claim.lease_token, 30, expected_attempt_outcome="RUNNING")
    assert workers.heartbeat(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30, expected_attempt_outcome="RUNNING")
    assert not workers.finalize(job.job_id, claim.attempt_id, "worker-lease", wrong_lease_token, expected_state=JobState.RUNNING, expected_attempt_outcome="RUNNING", final_state=JobState.SUCCEEDED, reason_code="SUCCEEDED", trace_id="finish-stale", exit_code=0, result_hash="b" * 64, result_metadata={"validator": "test"})
    assert workers.finalize(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, expected_state=JobState.RUNNING, expected_attempt_outcome="RUNNING", final_state=JobState.SUCCEEDED, reason_code="SUCCEEDED", trace_id="finish-ok", exit_code=0, result_hash="b" * 64, result_metadata={"validator": "test"})
    assert not workers.heartbeat(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30, expected_attempt_outcome="RUNNING")

    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.SUCCEEDED
    assert [(event.reason_code, event.trace_id) for event in detail.events] == [
        ("ENQUEUED", "lease-enqueue"), ("CLAIMED", "lease-claim"),
        ("STARTED", "start-ok"), ("SUCCEEDED", "finish-ok"),
    ]


def test_start_persists_complete_process_identity_and_wrong_token_writes_no_event(claimed) -> None:
    jobs, workers, database, job, claim = claimed
    identity = ProcessIdentity(111, 222, 333, "c" * 64)
    before = len(jobs.get_job(job.job_id).events)
    assert not workers.start_attempt(job.job_id, claim.attempt_id, "wrong-worker", claim.lease_token, identity, "wrong-worker")
    assert len(jobs.get_job(job.job_id).events) == before
    assert workers.start_attempt(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, identity, "start-identity")
    with psycopg.connect(database.conninfo()) as connection:
        row = connection.execute("SELECT child_pid, process_group_id, process_start_ticks, command_fingerprint, outcome FROM job_attempts WHERE attempt_id = %s", (claim.attempt_id,)).fetchone()
    assert row == (111, 222, 333, "c" * 64, "RUNNING")


def test_expired_token_cannot_renew_or_finalize_and_writes_no_false_event(claimed) -> None:
    jobs, workers, database, job, claim = claimed
    identity = ProcessIdentity(444, 444, 555, "d" * 64)
    assert workers.start_attempt(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, identity, "start-expiry")
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE job_id = %s",
            (job.job_id,),
        )
    event_count = len(jobs.get_job(job.job_id).events)

    assert not workers.heartbeat(job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30, expected_attempt_outcome="RUNNING")
    assert not workers.finalize(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        expected_state=JobState.RUNNING, expected_attempt_outcome="RUNNING", final_state=JobState.FAILED,
        reason_code="EXECUTION_FAILED", trace_id="finish-expired", exit_code=1,
    )
    assert len(jobs.get_job(job.job_id).events) == event_count


def test_late_finalize_fence_loss_rolls_back_inserted_artifacts(claimed) -> None:
    jobs, workers, database, job, claim = claimed
    assert workers.start_attempt(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        ProcessIdentity(445, 445, 556, "d" * 64), "start-late-fence",
    )
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "CREATE FUNCTION raise_finalize_transition_fault() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'synthetic finalize transition failure'; END $$"
        )
        connection.execute(
            "CREATE TRIGGER raise_finalize_transition_fault "
            "BEFORE UPDATE ON jobs FOR EACH ROW "
            "WHEN (OLD.state = 'RUNNING' AND NEW.state = 'FAILED') "
            "EXECUTE FUNCTION raise_finalize_transition_fault()"
        )

    artifact = ArtifactMetadata(
        "stdout", "job-1/attempt-1/stdout.log", "a" * 64, 3,
        "application/octet-stream", False,
    )
    event_count = len(jobs.get_job(job.job_id).events)
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="synthetic finalize transition failure",
    ):
        workers.finalize(
            job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
            expected_state=JobState.RUNNING, expected_attempt_outcome="RUNNING",
            final_state=JobState.FAILED, reason_code="EXECUTION_FAILED",
            trace_id="late-fence", exit_code=1, artifacts=(artifact,),
        )
    with psycopg.connect(database.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM job_artifacts WHERE attempt_id = %s",
            (claim.attempt_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM jobs WHERE job_id = %s", (job.job_id,)
        ).fetchone()[0] == "RUNNING"
        assert connection.execute(
            "SELECT outcome FROM job_attempts WHERE attempt_id = %s",
            (claim.attempt_id,),
        ).fetchone()[0] == "RUNNING"
        assert connection.execute(
            "SELECT count(*) FROM job_events WHERE job_id = %s", (job.job_id,)
        ).fetchone()[0] == event_count


@pytest.mark.parametrize("started,expected_outcome", [(False, "CLAIMED"), (True, "RUNNING")])
def test_cancel_requested_heartbeat_and_finalize_preserve_attempt_outcome(
    claimed, started: bool, expected_outcome: str
) -> None:
    jobs, workers, database, job, claim = claimed
    if started:
        assert workers.start_attempt(
            job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
            ProcessIdentity(777, 777, 888, "e" * 64), "start-cancel",
        )
    jobs.request_cancel(
        job.job_id,
        ActorIdentity(actor_type="OPERATOR", actor_id="cancel-test"),
        "request-cancel",
    )

    assert workers.heartbeat(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome=expected_outcome,
    )
    assert workers.finalize(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome=expected_outcome,
        final_state=JobState.CANCELLED,
        reason_code="CANCELLED",
        trace_id="cancel-complete",
        termination_reason="CANCELLED",
    )

    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.CANCELLED
    assert detail.attempts[0].outcome == "CANCELLED"
    with psycopg.connect(database.conninfo()) as connection:
        assert connection.execute(
            "SELECT count(*) FROM job_attempts WHERE outcome = 'CANCEL_REQUESTED'"
        ).fetchone()[0] == 0


def test_cancel_requested_before_start_is_fenced_and_finalized_without_identity(claimed) -> None:
    jobs, workers, database, job, claim = claimed
    jobs.request_cancel(
        job.job_id,
        ActorIdentity(actor_type="OPERATOR", actor_id="cancel-before-start"),
        "request-cancel-before-start",
    )

    assert workers.pre_spawn_control(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30,
    ) == "CANCEL"
    assert workers.finalize(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome="CLAIMED", final_state=JobState.CANCELLED,
        reason_code="CANCELLED", trace_id="cancel-before-start-complete",
        termination_reason="CANCELLED",
    )
    detail = jobs.get_job(job.job_id)
    assert detail.job.state is JobState.CANCELLED
    assert detail.attempts[0].outcome == "CANCELLED"
    with psycopg.connect(database.conninfo()) as connection:
        assert connection.execute(
            "SELECT child_pid, process_group_id FROM job_attempts WHERE attempt_id = %s",
            (claim.attempt_id,),
        ).fetchone() == (None, None)


def test_terminal_attempt_cannot_be_heartbeated_or_finalized(claimed) -> None:
    jobs, workers, database, job, claim = claimed
    assert workers.start_attempt(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        ProcessIdentity(901, 901, 902, "f" * 64), "start-terminal-race",
    )
    jobs.request_cancel(
        job.job_id,
        ActorIdentity(actor_type="OPERATOR", actor_id="cancel-test"),
        "request-terminal-race",
    )
    with psycopg.connect(database.conninfo()) as connection:
        connection.execute(
            "UPDATE job_attempts SET outcome = 'INTERRUPTED', finished_at = now() "
            "WHERE attempt_id = %s",
            (claim.attempt_id,),
        )
    before = len(jobs.get_job(job.job_id).events)

    assert not workers.heartbeat(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome="RUNNING",
    )
    assert not workers.finalize(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome="RUNNING",
        final_state=JobState.CANCELLED,
        reason_code="CANCELLED",
        trace_id="terminal-race-finalize",
    )
    assert len(jobs.get_job(job.job_id).events) == before


def test_running_job_rejects_claimed_attempt_outcome_for_lifecycle_updates(
    claimed,
) -> None:
    jobs, workers, _, job, claim = claimed
    assert workers.start_attempt(
        job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
        ProcessIdentity(911, 911, 912, "a" * 64), "start-illegal-mapping",
    )
    before = len(jobs.get_job(job.job_id).events)

    with pytest.raises(ValueError, match="does not match job state RUNNING"):
        workers.heartbeat(
            job.job_id, claim.attempt_id, "worker-lease", claim.lease_token, 30,
            expected_state=JobState.RUNNING,
            expected_attempt_outcome="CLAIMED",
        )
    with pytest.raises(ValueError, match="does not match job state RUNNING"):
        workers.finalize(
            job.job_id, claim.attempt_id, "worker-lease", claim.lease_token,
            expected_state=JobState.RUNNING,
            expected_attempt_outcome="CLAIMED",
            final_state=JobState.FAILED,
            reason_code="EXECUTION_FAILED",
            trace_id="illegal-mapping-finalize",
        )
    assert len(jobs.get_job(job.job_id).events) == before
