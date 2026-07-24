from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from inspect import getsource
from types import SimpleNamespace

from packages.job_contracts import JobState
from services.job_store.repository import JobRepository
from services.job_store.worker_repository import ProcessIdentity, WorkerRepository


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    def transaction(self):
        return _Transaction()

    def execute(self, statement, parameters=None):
        self.calls.append((" ".join(str(statement).split()), parameters))
        return _Result(self.rows.pop(0) if self.rows else None)


class _Pool:
    def __init__(self, connection: _Connection):
        self.value = connection

    @contextmanager
    def connection(self):
        yield self.value


def _worker(connection: _Connection) -> WorkerRepository:
    repository = object.__new__(WorkerRepository)
    repository._pool = _Pool(connection)
    return repository


def test_runtime_repositories_use_only_fixed_transition_capabilities() -> None:
    api_source = getsource(JobRepository)
    worker_source = getsource(WorkerRepository)

    assert "job_plane.api_enqueue_snapshot" in api_source
    assert "job_plane.api_cancel_snapshot" in api_source
    assert "job_plane.scheduler_enqueue_snapshot" in api_source
    assert "job_plane.worker_claim_snapshot" in worker_source
    assert "job_plane.worker_start_snapshot" in worker_source
    assert "job_plane.worker_control_snapshot_lease" in worker_source
    assert "job_plane.worker_finalize_snapshot" in worker_source
    assert "job_plane.worker_recover_expired_snapshot" in worker_source

    forbidden_api_sql = (
        "INSERT INTO jobs",
        "UPDATE jobs",
        "INSERT INTO job_events",
    )
    forbidden_worker_sql = (
        "INSERT INTO job_attempts",
        "UPDATE job_attempts",
        "UPDATE jobs",
        "INSERT INTO job_events",
    )
    assert all(fragment not in api_source for fragment in forbidden_api_sql)
    assert all(fragment not in worker_source for fragment in forbidden_worker_sql)


def test_worker_claim_and_start_bind_exact_fixed_capability_arguments() -> None:
    expires = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    claim_connection = _Connection(
        {
            "job_id": "job_1",
            "job_type": "SNAPSHOT",
            "payload": {"scope": "default", "requested_as_of": None},
            "attempt_number": 1,
            "max_attempts": 2,
            "lease_expires_at": expires,
        }
    )
    repository = _worker(claim_connection)
    generated = iter(("attempt_fixed", "event_claim"))
    repository._new_id = lambda prefix: next(generated)

    claimed = repository.claim_next("worker-1", 30, "trace-claim")

    assert claimed is not None
    assert claimed.attempt_id == "attempt_fixed"
    claim_sql, claim_parameters = claim_connection.calls[0]
    assert "job_plane.worker_claim_snapshot" in claim_sql
    assert claim_parameters[:2] == ("attempt_fixed", "worker-1")
    assert claim_parameters[3:] == (30, "trace-claim", "event_claim")
    assert claim_parameters[2] == claimed.lease_token

    start_connection = _Connection({"started": True})
    repository._pool = _Pool(start_connection)
    repository._new_id = lambda prefix: "event_start"
    identity = ProcessIdentity(123, 123, 456, "a" * 64)

    assert repository.start_attempt(
        claimed.job_id,
        claimed.attempt_id,
        claimed.worker_id,
        claimed.lease_token,
        identity,
        "trace-start",
    )
    start_sql, start_parameters = start_connection.calls[0]
    assert "job_plane.worker_start_snapshot" in start_sql
    assert start_parameters == (
        "job_1",
        "attempt_fixed",
        "worker-1",
        claimed.lease_token,
        123,
        123,
        456,
        "a" * 64,
        "trace-start",
        "event_start",
    )


def test_worker_lease_controls_use_only_fixed_phases() -> None:
    connection = _Connection(
        {"control": "CONTINUE"},
        {"control": "CANCEL"},
        {"control": "STALE"},
    )
    repository = _worker(connection)

    assert repository.pre_spawn_control("job", "attempt", "worker", "token", 30) == "CONTINUE"
    assert repository.heartbeat_control("job", "attempt", "worker", "token", 30) == "CANCEL"
    assert not repository.heartbeat(
        "job",
        "attempt",
        "worker",
        "token",
        30,
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
    )

    phases = [parameters[-1] for _, parameters in connection.calls]
    assert phases == ["PRE_SPAWN", "RUNNING", "RUNNING"]
    assert all(
        "job_plane.worker_control_snapshot_lease" in sql
        for sql, _ in connection.calls
    )


def test_heartbeat_preserves_the_callers_exact_expected_state() -> None:
    connection = _Connection(
        {"control": "CANCEL"},
        {"control": "CANCEL"},
        {"control": "CONTINUE"},
    )
    repository = _worker(connection)

    assert not repository.heartbeat(
        "job",
        "attempt",
        "worker",
        "token",
        30,
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
    )
    assert repository.heartbeat(
        "job",
        "attempt",
        "worker",
        "token",
        30,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome="RUNNING",
    )
    assert not repository.heartbeat(
        "job",
        "attempt",
        "worker",
        "token",
        30,
        expected_state=JobState.CANCEL_REQUESTED,
        expected_attempt_outcome="RUNNING",
    )


def test_worker_finalize_keeps_artifact_and_transition_in_one_transaction() -> None:
    connection = _Connection({"finalized": True}, None)
    repository = _worker(connection)
    generated = iter(("event_terminal", "artifact_1"))
    repository._new_id = lambda prefix: next(generated)
    artifact = SimpleNamespace(
        artifact_type="result",
        relative_ref="results/report.json",
        sha256="b" * 64,
        size_bytes=12,
        media_type="application/json",
        truncated=False,
        validator_id="result-v1",
        validation_metadata={},
    )

    assert repository.finalize(
        "job",
        "attempt",
        "worker",
        "lease",
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
        final_state=JobState.SUCCEEDED,
        reason_code="RESULT_VALIDATED",
        trace_id="trace-finalize",
        exit_code=0,
        result_hash="c" * 64,
        result_metadata={"validator": "result-v1"},
        artifacts=(artifact,),
    )

    finalize_sql, parameters = connection.calls[0]
    assert "job_plane.worker_finalize_snapshot" in finalize_sql
    assert len(parameters) == 19
    assert parameters[:10] == (
        "job",
        "attempt",
        "worker",
        "lease",
        "RUNNING",
        "RUNNING",
        "SUCCEEDED",
        "RESULT_VALIDATED",
        "trace-finalize",
        "event_terminal",
    )
    assert "INSERT INTO job_artifacts" in connection.calls[1][0]


def test_worker_recovery_passes_the_complete_observed_fence() -> None:
    connection = _Connection({"outcome": "LEASE_EXPIRED_RETRY_SCHEDULED"})
    repository = _worker(connection)
    generated = iter(("event_terminal", "event_retry"))
    repository._new_id = lambda prefix: next(generated)
    candidate = {
        "job_id": "job",
        "attempt_id": "attempt",
        "state": "RUNNING",
        "attempt_outcome": "RUNNING",
        "lease_owner": "worker",
        "lease_token": "lease",
        "child_pid": 123,
        "process_group_id": 123,
        "process_start_ticks": 456,
        "command_fingerprint": "d" * 64,
    }

    assert repository._recover_observed_candidate(
        candidate,
        "ABSENT",
        "trace-recovery",
        "lease-recovery",
    ) == "LEASE_EXPIRED_RETRY_SCHEDULED"

    sql, parameters = connection.calls[0]
    assert "job_plane.worker_recover_expired_snapshot" in sql
    assert parameters == (
        "job",
        "attempt",
        "RUNNING",
        "RUNNING",
        "worker",
        "lease",
        123,
        123,
        456,
        "d" * 64,
        "ABSENT",
        "trace-recovery",
        "lease-recovery",
        "event_terminal",
        "event_retry",
    )
