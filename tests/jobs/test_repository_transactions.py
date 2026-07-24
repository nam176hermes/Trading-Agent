from __future__ import annotations

import psycopg
import pytest

from packages.job_contracts import ActorIdentity, EnqueueJobRequest, JobState
from services.job_store import (
    JobNotFound,
    JobRepository,
    JobStoreSettings,
)
from services.job_store.worker_repository import ClaimedJob, WorkerRepository
from services.job_worker.recovery import ProcessIdentity
from tests.jobs._postgres import (
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


def _settings(database_settings) -> JobStoreSettings:
    return JobStoreSettings(
        host=database_settings.host,
        port=database_settings.port,
        database=database_settings.database,
        user=database_settings.user,
        password=database_settings.password,
        pool_max=3,
    )


def _request(key: str) -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate(
        {
            "job_type": "SNAPSHOT",
            "payload": {"scope": "default", "requested_as_of": None},
            "idempotency_key": key,
            "actor": {"actor_type": "OPERATOR", "actor_id": "operator-tx"},
        }
    )


@pytest.fixture
def repository_database():
    with disposable_database(
        operation_id="jobs-repository-transactions-v1"
    ) as database_settings:
        upgrade_to_head(database_settings)
        api_settings = disposable_role_settings(
            database_settings, "trading_job_api"
        )
        worker_settings = disposable_role_settings(
            database_settings, "trading_job_worker"
        )
        with (
            JobRepository(_settings(api_settings)) as repository,
            WorkerRepository(_settings(worker_settings)) as workers,
        ):
            yield repository, workers, database_settings


def _actor(actor_type: str = "OPERATOR", actor_id: str = "operator-cancel"):
    return ActorIdentity.model_validate(
        {"actor_type": actor_type, "actor_id": actor_id}
    )


def _claim_job(
    workers: WorkerRepository,
    *,
    job_id: str,
    worker_id: str,
    trace_id: str,
) -> ClaimedJob:
    claimed = workers.claim_next(worker_id, 30, trace_id)
    assert claimed is not None and claimed.job_id == job_id
    return claimed


def _start_job(
    workers: WorkerRepository,
    claimed: ClaimedJob,
    *,
    trace_id: str,
) -> None:
    assert workers.start_attempt(
        claimed.job_id,
        claimed.attempt_id,
        claimed.worker_id,
        claimed.lease_token,
        ProcessIdentity(8001, 8001, 17, "e" * 64),
        trace_id,
    )


def test_cancel_queued_transitions_directly_to_cancelled(repository_database) -> None:
    repository, _, _ = repository_database
    job = repository.enqueue(_request("cancel:queued"), trace_id="trace-q-1").job

    cancelled = repository.request_cancel(
        job.job_id, _actor(), "trace-q-cancel"
    )

    assert cancelled.state is JobState.CANCELLED
    assert cancelled.cancel_actor == _actor()
    assert cancelled.cancel_requested_at is not None
    detail = repository.get_job(job.job_id)
    assert [(event.from_state, event.to_state, event.trace_id) for event in detail.events] == [
        (None, JobState.QUEUED, "trace-q-1"),
        (JobState.QUEUED, JobState.CANCELLED, "trace-q-cancel"),
    ]


@pytest.mark.parametrize("active_state", [JobState.CLAIMED, JobState.RUNNING])
def test_cancel_active_job_transitions_to_cancel_requested(
    repository_database, active_state
) -> None:
    repository, workers, _ = repository_database
    job = repository.enqueue(
        _request(f"cancel:{active_state.value.lower()}"), trace_id="trace-active-1"
    ).job
    claimed = _claim_job(
        workers,
        job_id=job.job_id,
        worker_id="worker-one",
        trace_id="trace-active-2",
    )
    if active_state is JobState.RUNNING:
        _start_job(workers, claimed, trace_id="trace-active-3")

    cancelled = repository.request_cancel(
        job.job_id, _actor(), "trace-active-cancel"
    )

    assert cancelled.state is JobState.CANCEL_REQUESTED
    assert repository.get_job(job.job_id).events[-1].actor == _actor()


@pytest.mark.parametrize(
    "terminal_state",
    [
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.BLOCKED,
        JobState.TIMED_OUT,
        JobState.CANCELLED,
    ],
)
def test_cancel_terminal_job_is_a_noop(repository_database, terminal_state) -> None:
    repository, workers, _ = repository_database
    job = repository.enqueue(
        _request(f"cancel:terminal:{terminal_state.value.lower()}"),
        trace_id="trace-terminal-1",
    ).job
    if terminal_state is JobState.CANCELLED:
        repository.request_cancel(
            job.job_id,
            _actor(actor_id="operator-terminal"),
            "trace-terminal-finalize",
        )
    else:
        claimed = _claim_job(
            workers,
            job_id=job.job_id,
            worker_id="worker-terminal",
            trace_id="trace-terminal-claim",
        )
        _start_job(workers, claimed, trace_id="trace-terminal-start")
        reason_code = {
            JobState.SUCCEEDED: "RESULT_VALIDATED",
            JobState.FAILED: "PROCESS_FAILED",
            JobState.BLOCKED: "SAFETY_BLOCKED",
            JobState.TIMED_OUT: "PROCESS_TIMEOUT",
        }[terminal_state]
        assert workers.finalize(
            job.job_id,
            claimed.attempt_id,
            claimed.worker_id,
            claimed.lease_token,
            expected_state=JobState.RUNNING,
            expected_attempt_outcome="RUNNING",
            final_state=terminal_state,
            reason_code=reason_code,
            trace_id="trace-terminal-finalize",
        )
    before = repository.get_job(job.job_id)

    result = repository.request_cancel(
        job.job_id, _actor(), "trace-terminal-cancel"
    )

    assert result.state is terminal_state
    assert repository.get_job(job.job_id).events == before.events


def test_stale_worker_capability_returns_false_and_writes_no_event(
    repository_database,
) -> None:
    repository, workers, _ = repository_database
    job = repository.enqueue(_request("transition:stale"), trace_id="trace-stale-1").job
    claimed = _claim_job(
        workers,
        job_id=job.job_id,
        worker_id="worker-stale",
        trace_id="trace-stale-claim",
    )

    assert workers.start_attempt(
        job.job_id,
        claimed.attempt_id,
        claimed.worker_id,
        "wrong-lease-token-0000000000000000",
        ProcessIdentity(8002, 8002, 18, "f" * 64),
        "trace-stale-2",
    ) is False

    detail = repository.get_job(job.job_id)
    assert detail.job.state is JobState.CLAIMED
    assert detail.attempts[0].outcome == "CLAIMED"
    assert len(detail.events) == 2


def test_worker_transition_rolls_back_when_database_rejects_event_insert(
    repository_database,
) -> None:
    repository, workers, database_settings = repository_database
    job = repository.enqueue(_request("transition:rollback"), trace_id="trace-rb-1").job
    claimed = _claim_job(
        workers,
        job_id=job.job_id,
        worker_id="worker-rollback",
        trace_id="trace-rb-claim",
    )
    with psycopg.connect(database_settings.conninfo()) as owner:
        owner.execute(
            """
            CREATE FUNCTION test_reject_started_event()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.trace_id = 'trace-rb-2' THEN
                RAISE EXCEPTION 'synthetic transition event failure';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
        owner.execute(
            """
            CREATE TRIGGER trg_test_reject_started_event
            BEFORE INSERT ON job_events
            FOR EACH ROW EXECUTE FUNCTION test_reject_started_event()
            """
        )

    with pytest.raises(psycopg.Error, match="synthetic transition event failure"):
        workers.start_attempt(
            job.job_id,
            claimed.attempt_id,
            claimed.worker_id,
            claimed.lease_token,
            ProcessIdentity(8003, 8003, 19, "a" * 64),
            "trace-rb-2",
        )

    detail = repository.get_job(job.job_id)
    assert detail.job.state is JobState.CLAIMED
    assert detail.attempts[0].outcome == "CLAIMED"
    assert [event.trace_id for event in detail.events] == [
        "trace-rb-1",
        "trace-rb-claim",
    ]


@pytest.mark.parametrize("terminal_state", [JobState.FAILED, JobState.TIMED_OUT])
def test_approved_retry_requeues_without_terminal_or_lease_residue(
    repository_database, terminal_state
) -> None:
    repository, workers, database_settings = repository_database
    job = repository.enqueue(
        _request(f"retry:{terminal_state.value.lower()}"), trace_id="trace-retry-1"
    ).job
    claimed = _claim_job(
        workers,
        job_id=job.job_id,
        worker_id="worker-retry",
        trace_id="trace-retry-claim",
    )
    _start_job(workers, claimed, trace_id="trace-retry-start")
    reason_code = {
        JobState.FAILED: "TRANSIENT_FAILURE",
        JobState.TIMED_OUT: "PROCESS_TIMEOUT",
    }[terminal_state]

    requeued = workers.finalize(
        job.job_id,
        claimed.attempt_id,
        claimed.worker_id,
        claimed.lease_token,
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
        final_state=terminal_state,
        reason_code=reason_code,
        trace_id="trace-retry-2",
        result_hash="b" * 64,
        result_metadata={"stale": True},
        error_code="TRANSIENT_FAILURE",
        error_message="sanitized stale error",
        retry=True,
    )

    assert requeued is True
    with psycopg.connect(database_settings.conninfo()) as connection:
        row = connection.execute(
            """
            SELECT finished_at, lease_owner, lease_token, lease_expires_at,
                   result_hash, result_metadata, error_code, error_message,
                   cancel_requested_at, cancel_actor_type, cancel_actor_id
            FROM jobs WHERE job_id = %s
            """,
            (job.job_id,),
        ).fetchone()
    assert row == (None, None, None, None, None, {}, None, None, None, None, None)
    detail = repository.get_job(job.job_id)
    assert detail.events[-1].from_state is terminal_state
    assert detail.events[-1].to_state is JobState.QUEUED
    assert detail.events[-1].reason_code == "PROCESS_RETRY_SCHEDULED"


def test_cancel_unknown_job_raises_typed_error(repository_database) -> None:
    repository, _, _ = repository_database
    with pytest.raises(JobNotFound) as caught:
        repository.request_cancel("job_unknown", _actor(), "trace-unknown")
    assert caught.value.code == "JOB_NOT_FOUND"
