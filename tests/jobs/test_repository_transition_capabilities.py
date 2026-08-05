from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from inspect import getsource
from types import SimpleNamespace

import pytest

from packages.job_contracts import (
    ActorType,
    EnqueueJobRequest,
    JobState,
    JobType,
    canonical_payload_json,
    payload_fingerprint,
)
from services.job_store.errors import InvalidJobFilters, InvalidTraceId
from services.job_store.records import EnqueueOutcome, JobFilters
from services.job_store.repository import JobRepository
from services.job_store.worker_repository import ProcessIdentity, WorkerRepository


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.transaction_events.append(("enter", None))
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.connection.transaction_events.append(("exit", exc_type))
        return False


class _Connection:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.calls: list[tuple[str, object]] = []
        self.commits = 0
        self.transaction_events: list[tuple[str, object]] = []

    def transaction(self):
        return _Transaction(self)

    def execute(self, statement, parameters=None):
        self.calls.append((" ".join(str(statement).split()), parameters))
        return _Result(self.rows.pop(0) if self.rows else None)

    def commit(self):
        self.commits += 1


class _Pool:
    def __init__(self, connection: _Connection):
        self.value = connection
        self.opens = 0

    @contextmanager
    def connection(self):
        self.opens += 1
        yield self.value


def _worker(connection: _Connection) -> WorkerRepository:
    repository = object.__new__(WorkerRepository)
    repository._pool = _Pool(connection)
    return repository


def _engine_payload_wire() -> dict[str, object]:
    def artifact(value: str, media_type: str) -> dict[str, str]:
        return {
            "artifact_id": value,
            "sha256": value.replace("-", "")[:1] * 64,
            "media_type": media_type,
        }

    return {
        "engine_backtest": {
            "engine_configuration": artifact(
                "11111111-1111-4111-8111-111111111111", "application/json"
            ),
            "instrument_catalog": artifact(
                "22222222-2222-4222-8222-222222222222", "application/json"
            ),
            "strategy_configuration": artifact(
                "33333333-3333-4333-8333-333333333333", "application/json"
            ),
            "market_data": artifact(
                "44444444-4444-4444-8444-444444444444",
                "application/jsonl",
            ),
            "start_time": "2026-07-01T00:00:00Z",
            "end_time": "2026-08-01T00:00:00Z",
        }
    }


def _repository(connection: _Connection) -> JobRepository:
    repository = object.__new__(JobRepository)
    repository._pool = _Pool(connection)
    return repository


def _request(
    *,
    actor_type: str = "SCHEDULER",
    job_type: str = "SNAPSHOT",
    key: str = "schedule:snapshot:2026-07-16T12:00Z",
) -> EnqueueJobRequest:
    payloads = {
        "SNAPSHOT": {"scope": "default", "requested_as_of": None},
        "DEBATE": {"asset": "BTC", "horizon": "1d"},
    }
    return EnqueueJobRequest.model_validate(
        {
            "job_type": job_type,
            "payload": payloads[job_type],
            "idempotency_key": key,
            "actor": {"actor_type": actor_type, "actor_id": "scheduler-1"},
            "priority": 0 if actor_type == "SCHEDULER" else 5,
        }
    )


def _job_row(request: EnqueueJobRequest, *, job_id: str = "job_fixed") -> dict[str, object]:
    requested_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    return {
        "job_id": job_id,
        "job_type": request.job_type.value,
        "state": "QUEUED",
        "payload": request.payload.model_dump(mode="json"),
        "payload_fingerprint": payload_fingerprint(request.payload),
        "idempotency_key": request.idempotency_key,
        "actor_type": request.actor.actor_type.value,
        "actor_id": request.actor.actor_id,
        "priority": request.priority,
        "requested_at": requested_at,
        "updated_at": requested_at,
        "attempt_count": 0,
        "max_attempts": 2,
        "reason_code": "ENQUEUED",
        "result_hash": None,
        "cancel_requested_at": None,
        "cancel_actor_type": None,
        "cancel_actor_id": None,
    }


def test_runtime_repositories_use_only_fixed_transition_capabilities() -> None:
    api_source = getsource(JobRepository)
    worker_source = getsource(WorkerRepository)

    assert "job_plane.api_enqueue_snapshot" in api_source
    assert "job_plane.api_cancel_snapshot" in api_source
    assert "job_plane.scheduler_enqueue_snapshot" in api_source
    assert '"worker_claim_snapshot"' in worker_source
    assert '"worker_claim_paper"' in worker_source
    assert "job_plane.worker_start_paper" in worker_source
    assert "job_plane.worker_control_paper_lease" in worker_source
    assert "job_plane.worker_finalize_paper" in worker_source
    assert "job_plane.worker_recover_expired_paper" in worker_source

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


def test_schedule_snapshot_binds_authority_and_heartbeat_in_one_transaction() -> None:
    request = _request()
    tick_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    slot_at = datetime(2026, 7, 16, 11, 55, tzinfo=UTC)
    connection = _Connection(
        {"job_id": "job_fixed", "outcome": "ENQUEUED"},
        _job_row(request),
        None,
    )
    repository = _repository(connection)
    generated = iter(("job_fixed", "event_enqueue", "heartbeat_fixed"))
    repository._new_id = lambda prefix: next(generated)

    result = repository.schedule_snapshot(
        request=request,
        scheduler_id="snapshot-scheduler",
        code_commit="365b5d8",
        trace_id="trace-schedule",
        tick_at=tick_at,
        slot_at=slot_at,
    )

    assert result.outcome is EnqueueOutcome.ENQUEUED
    assert result.job.job_id == "job_fixed"
    assert connection.transaction_events == [("enter", None), ("exit", None)]
    authority_sql, authority_parameters = connection.calls[0]
    assert "job_plane.scheduler_enqueue_snapshot" in authority_sql
    assert authority_parameters == (
        "job_fixed",
        canonical_payload_json(request.payload),
        payload_fingerprint(request.payload),
        request.idempotency_key,
        "scheduler-1",
        "trace-schedule",
        "event_enqueue",
    )
    assert connection.calls[1][1] == ("job_fixed",)
    heartbeat_sql, heartbeat_parameters = connection.calls[2]
    assert "INSERT INTO scheduler_heartbeats" in heartbeat_sql
    assert heartbeat_parameters == (
        "heartbeat_fixed",
        "snapshot-scheduler",
        "365b5d8",
        "scheduler-1",
        "trace-schedule",
        tick_at,
        slot_at,
        "ENQUEUED",
        "job_fixed",
        "ENQUEUED",
    )


@pytest.mark.parametrize(
    ("rows", "expected_exception", "message", "expected_calls"),
    (
        ((None,), RuntimeError, "authority returned no result", 1),
        (
            ({"job_id": "job_missing", "outcome": "ENQUEUED"}, None),
            RuntimeError,
            "unknown job",
            2,
        ),
        (
            (
                {"job_id": "job_fixed", "outcome": "NOT_AUTHORIZED"},
                "valid-job-row",
            ),
            ValueError,
            "NOT_AUTHORIZED",
            2,
        ),
    ),
)
def test_schedule_snapshot_rolls_back_missing_or_invalid_authority_results(
    rows, expected_exception, message: str, expected_calls: int
) -> None:
    request = _request()
    resolved_rows = tuple(
        _job_row(request) if row == "valid-job-row" else row for row in rows
    )
    connection = _Connection(*resolved_rows)
    repository = _repository(connection)
    repository._new_id = lambda prefix: f"{prefix}_fixed"

    with pytest.raises(expected_exception, match=message):
        repository.schedule_snapshot(
            request=request,
            scheduler_id="snapshot-scheduler",
            code_commit="365b5d8",
            trace_id="trace-schedule-failure",
            tick_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
            slot_at=datetime(2026, 7, 16, 11, 55, tzinfo=UTC),
        )

    assert len(connection.calls) == expected_calls
    assert connection.transaction_events == [
        ("enter", None),
        ("exit", expected_exception),
    ]
    assert "INSERT INTO scheduler_heartbeats" not in " ".join(
        statement for statement, _ in connection.calls
    )


def test_schedule_snapshot_rejects_trace_type_and_actor_before_database_access() -> None:
    connection = _Connection()
    repository = _repository(connection)
    schedule_arguments = {
        "scheduler_id": "snapshot-scheduler",
        "code_commit": "365b5d8",
        "tick_at": datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        "slot_at": datetime(2026, 7, 16, 11, 55, tzinfo=UTC),
    }

    with pytest.raises(InvalidTraceId):
        repository.schedule_snapshot(
            request=_request(), trace_id="invalid trace", **schedule_arguments
        )
    with pytest.raises(ValueError, match="only for SNAPSHOT"):
        repository.schedule_snapshot(
            request=_request(
                actor_type="OPERATOR", job_type="DEBATE", key="manual:debate"
            ),
            trace_id="trace-wrong-type",
            **schedule_arguments,
        )
    with pytest.raises(ValueError, match="actor authority"):
        repository.schedule_snapshot(
            request=_request(actor_type="OPERATOR", key="manual:snapshot"),
            trace_id="trace-wrong-actor",
            **schedule_arguments,
        )

    assert repository._pool.opens == 0
    assert connection.calls == []


def test_list_jobs_builds_exact_bounded_filter_query_without_mutation() -> None:
    requested_from = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    requested_to = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    connection = _Connection([])
    repository = _repository(connection)

    listed = repository.list_jobs(
        JobFilters(
            job_type=JobType.SNAPSHOT,
            state=JobState.QUEUED,
            actor_type=ActorType.SCHEDULER.value,
            actor_id="scheduler-1",
            requested_from=requested_from,
            requested_to=requested_to,
            limit=25,
            offset=5,
        )
    )

    assert listed == ()
    assert repository._pool.opens == 1
    sql, parameters = connection.calls[0]
    assert all(
        predicate in sql
        for predicate in (
            "job_type = %s",
            "state = %s",
            "actor_type = %s",
            "actor_id = %s",
            "requested_at >= %s",
            "requested_at <= %s",
            "ORDER BY requested_at DESC, job_id DESC",
            "LIMIT %s OFFSET %s",
        )
    )
    assert parameters == [
        "SNAPSHOT",
        "QUEUED",
        "SCHEDULER",
        "scheduler-1",
        requested_from,
        requested_to,
        25,
        5,
    ]


@pytest.mark.parametrize(
    "filters",
    (
        object(),
        JobFilters(limit=True),
        JobFilters(offset=False),
        JobFilters(actor_id="scheduler-1"),
        JobFilters(requested_from=datetime(2026, 7, 16, 12, 0)),
        JobFilters(actor_type="UNKNOWN"),
    ),
)
def test_list_jobs_rejects_malformed_boundaries_before_database_access(filters) -> None:
    connection = _Connection()
    repository = _repository(connection)

    with pytest.raises(InvalidJobFilters):
        repository.list_jobs(filters)

    assert repository._pool.opens == 0
    assert connection.calls == []


def test_get_job_unknown_id_uses_one_read_only_transaction() -> None:
    connection = _Connection(None, None)
    repository = _repository(connection)

    assert repository.get_job("job_unknown") is None

    assert connection.transaction_events == [("enter", None), ("exit", None)]
    assert connection.calls[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        None,
    )
    assert "FROM jobs WHERE job_id = %s" in connection.calls[1][0]
    assert connection.calls[1][1] == ("job_unknown",)


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
    assert "job_plane.worker_start_paper" in start_sql
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


def test_worker_claim_empty_result_preserves_exact_fence_arguments(
    monkeypatch,
) -> None:
    connection = _Connection(None)
    repository = _worker(connection)
    generated = iter(("attempt_empty", "event_empty"))
    repository._new_id = lambda prefix: next(generated)
    monkeypatch.setattr(
        "services.job_store.worker_repository.secrets.token_urlsafe",
        lambda size: "lease-token-fixed",
    )

    assert repository.claim_next("worker-empty", 45, "trace-claim-empty") is None

    assert connection.transaction_events == [("enter", None), ("exit", None)]
    sql, parameters = connection.calls[0]
    assert "job_plane.worker_claim_snapshot" in sql
    assert parameters == (
        "attempt_empty",
        "worker-empty",
        "lease-token-fixed",
        45,
        "trace-claim-empty",
        "event_empty",
    )


def test_worker_repository_accepts_only_the_opt_in_engine_claim_set() -> None:
    expires = datetime(2026, 8, 5, 13, 0, tzinfo=UTC)
    connection = _Connection(
        {
            "job_id": "job_0123456789abcdef0123456789abcdef",
            "job_type": "BACKTEST",
            "payload": _engine_payload_wire(),
            "attempt_number": 1,
            "max_attempts": 2,
            "lease_expires_at": expires,
        }
    )
    repository = _worker(connection)
    generated = iter(("attempt_engine", "event_engine"))
    repository._new_id = lambda prefix: next(generated)

    claimed = repository.claim_next(
        "worker-engine",
        30,
        "trace-engine",
        allowed_job_types=(JobType.SNAPSHOT, JobType.BACKTEST),
    )

    assert len(connection.calls) == 1
    assert "job_plane.worker_claim_paper" in connection.calls[0][0]
    assert claimed is not None
    assert claimed.job_type is JobType.BACKTEST
    assert claimed.payload.__class__.__name__ == "EngineBacktestPayload"


def test_worker_claim_rejects_invalid_authority_inputs_before_database_access() -> None:
    connection = _Connection()
    repository = _worker(connection)

    with pytest.raises(ValueError, match="worker identity"):
        repository.claim_next("invalid worker", 30, "trace-worker")
    with pytest.raises(ValueError, match="lease duration"):
        repository.claim_next("worker-1", True, "trace-worker")
    with pytest.raises(InvalidTraceId):
        repository.claim_next("worker-1", 30, "invalid trace")
    with pytest.raises(ValueError, match="job-type authority"):
        repository.claim_next(
            "worker-1",
            30,
            "trace-worker",
            allowed_job_types=(JobType.DEBATE,),
        )

    assert repository._pool.opens == 0
    assert connection.calls == []


@pytest.mark.parametrize("authority", (None, {"control": "INVALID"}))
def test_worker_lease_control_fails_closed_on_invalid_authority_result(authority) -> None:
    connection = _Connection(authority)
    repository = _worker(connection)

    with pytest.raises(RuntimeError, match="invalid result"):
        repository.heartbeat_control("job", "attempt", "worker", "token", 30)

    assert connection.transaction_events == [("enter", None), ("exit", None)]
    assert "job_plane.worker_control_paper_lease" in connection.calls[0][0]
    assert connection.calls[0][1] == (
        "job",
        "attempt",
        "worker",
        "token",
        30,
        "RUNNING",
    )


def test_worker_heartbeat_validates_identity_and_upserts_exact_observation() -> None:
    invalid_connection = _Connection()
    invalid_repository = _worker(invalid_connection)
    with pytest.raises(ValueError, match="code commit"):
        invalid_repository.worker_heartbeat(
            "worker-1",
            "",
            "IDLE",
            current_job_id=None,
            current_attempt_id=None,
        )
    with pytest.raises(ValueError, match="status"):
        invalid_repository.worker_heartbeat(
            "worker-1",
            "365b5d8",
            "UNKNOWN",
            current_job_id=None,
            current_attempt_id=None,
        )
    with pytest.raises(ValueError, match="incomplete"):
        invalid_repository.worker_heartbeat(
            "worker-1",
            "365b5d8",
            "BUSY",
            current_job_id="job",
            current_attempt_id=None,
        )
    assert invalid_repository._pool.opens == 0

    connection = _Connection(None)
    repository = _worker(connection)
    repository.worker_heartbeat(
        "worker-1",
        "365b5d8",
        "BUSY",
        current_job_id="job",
        current_attempt_id="attempt",
        metadata={"z": 2, "a": 1},
    )

    sql, parameters = connection.calls[0]
    assert "INSERT INTO worker_heartbeats" in sql
    assert "ON CONFLICT (worker_id) DO UPDATE" in sql
    assert parameters == (
        "worker-1",
        "365b5d8",
        "BUSY",
        "job",
        "attempt",
        '{"a":1,"z":2}',
    )
    assert connection.commits == 1


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
        "job_plane.worker_control_paper_lease" in sql
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


def test_worker_lifecycle_calls_fail_closed_when_the_lease_fence_is_stale() -> None:
    connection = _Connection(None, {"control": "STALE"}, None)
    repository = _worker(connection)
    generated = iter(("event_start", "event_finalize"))
    repository._new_id = lambda prefix: next(generated)
    identity = ProcessIdentity(123, 123, 456, "a" * 64)

    assert not repository.start_attempt(
        "job",
        "attempt",
        "worker",
        "stale-lease-token",
        identity,
        "trace-start-stale",
    )
    assert not repository.heartbeat(
        "job",
        "attempt",
        "worker",
        "stale-lease-token",
        30,
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
    )
    assert not repository.finalize(
        "job",
        "attempt",
        "worker",
        "stale-lease-token",
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
        final_state=JobState.FAILED,
        reason_code="LEASE_FENCE_LOST",
        trace_id="trace-finalize-stale",
        exit_code=1,
    )

    statements = [statement for statement, _ in connection.calls]
    assert len(statements) == 3
    assert "job_plane.worker_start_paper" in statements[0]
    assert "job_plane.worker_control_paper_lease" in statements[1]
    assert "job_plane.worker_finalize_paper" in statements[2]
    assert all(
        parameters is not None
        and parameters[:4] == ("job", "attempt", "worker", "stale-lease-token")
        for _, parameters in connection.calls
    )


def test_worker_finalize_retry_binds_retry_fence_and_sanitized_metadata() -> None:
    connection = _Connection({"finalized": True}, None)
    repository = _worker(connection)
    generated = iter(("event_terminal", "event_retry", "artifact_retry"))
    repository._new_id = lambda prefix: next(generated)
    artifact = SimpleNamespace(
        artifact_type="stderr",
        relative_ref="streams/stderr.txt",
        sha256="e" * 64,
        size_bytes=9,
        media_type="text/plain",
        truncated=True,
        validator_id="stream-v1",
    )
    result_metadata = {
        "lineage": {
            "command": {
                "authority_document_sha256": "authority",
                "ignored": "not-copied",
            },
            "safety": {
                "initial": {
                    "requested_mode": "paper",
                    "live_execution_enabled": False,
                    "ignored": 7,
                },
                "final": {
                    "effective_mode": "paper",
                    "kill_switch_state": "inactive",
                },
            },
        }
    }

    assert repository.finalize(
        "job",
        "attempt",
        "worker",
        "lease",
        expected_state=JobState.RUNNING,
        expected_attempt_outcome="RUNNING",
        final_state=JobState.FAILED,
        reason_code="PROCESS_EXIT_NONZERO",
        trace_id="trace-finalize-retry",
        exit_code=7,
        termination_reason="exit",
        result_metadata=result_metadata,
        error_code="PROCESS_EXIT_NONZERO",
        artifacts=(artifact,),
        retry=True,
    )

    finalize_sql, parameters = connection.calls[0]
    assert "job_plane.worker_finalize_paper" in finalize_sql
    assert parameters[:13] == (
        "job",
        "attempt",
        "worker",
        "lease",
        "RUNNING",
        "RUNNING",
        "FAILED",
        "PROCESS_EXIT_NONZERO",
        "trace-finalize-retry",
        "event_terminal",
        7,
        "exit",
        None,
    )
    assert json.loads(parameters[13]) == result_metadata
    assert parameters[14:18] == (
        "PROCESS_EXIT_NONZERO",
        None,
        True,
        "event_retry",
    )
    assert json.loads(parameters[18])["lineage"] == {
        "command": {"authority_document_sha256": "authority"},
        "safety": {
            "initial": {
                "requested_mode": "paper",
                "live_execution_enabled": False,
            },
            "final": {
                "effective_mode": "paper",
                "kill_switch_state": "inactive",
            },
        },
    }
    artifact_sql, artifact_parameters = connection.calls[1]
    assert "INSERT INTO job_artifacts" in artifact_sql
    assert artifact_parameters == (
        "artifact_retry",
        "job",
        "attempt",
        "stderr",
        "streams/stderr.txt",
        "e" * 64,
        9,
        "text/plain",
        True,
        "stream-v1",
        "{}",
    )
    assert connection.transaction_events == [("enter", None), ("exit", None)]


def test_worker_finalize_rejects_invalid_hash_and_attempt_fence_before_database() -> None:
    connection = _Connection()
    repository = _worker(connection)
    arguments = {
        "job_id": "job",
        "attempt_id": "attempt",
        "worker_id": "worker",
        "lease_token": "lease",
        "expected_state": JobState.RUNNING,
        "final_state": JobState.FAILED,
        "reason_code": "PROCESS_EXIT_NONZERO",
        "trace_id": "trace-finalize-validation",
    }

    with pytest.raises(ValueError, match="does not match job state"):
        repository.finalize(expected_attempt_outcome="CLAIMED", **arguments)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        repository.finalize(
            expected_attempt_outcome="RUNNING",
            result_hash="A" * 64,
            **arguments,
        )

    assert repository._pool.opens == 0
    assert connection.calls == []


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
    assert "job_plane.worker_finalize_paper" in finalize_sql
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
    assert "job_plane.worker_recover_expired_paper" in sql
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


def test_worker_recovery_classifies_process_observations_and_inspector_failures() -> None:
    def candidate(
        suffix: str,
        *,
        identity: tuple[object, object, object, object],
    ) -> dict[str, object]:
        child_pid, process_group_id, process_start_ticks, command_fingerprint = identity
        return {
            "job_id": f"job_{suffix}",
            "attempt_id": f"attempt_{suffix}",
            "state": "RUNNING",
            "attempt_outcome": "RUNNING",
            "lease_owner": "worker-original",
            "lease_token": f"lease-{suffix}",
            "child_pid": child_pid,
            "process_group_id": process_group_id,
            "process_start_ticks": process_start_ticks,
            "command_fingerprint": command_fingerprint,
        }

    identities = {
        "none": (None, None, None, None),
        "absent": (2, 2, 20, "a" * 64),
        "running": (3, 3, 30, "b" * 64),
        "mismatch": (4, 4, 40, "c" * 64),
        "incomplete": (5, None, 50, "d" * 64),
        "exception": (6, 6, 60, "e" * 64),
    }
    candidates = [
        candidate(name, identity=identity) for name, identity in identities.items()
    ]
    connection = _Connection(
        candidates,
        *({"outcome": f"RECOVERED_{index}"} for index in range(len(candidates))),
    )
    repository = _worker(connection)
    generated = iter(f"event_{index}" for index in range(12))
    repository._new_id = lambda prefix: next(generated)

    class Inspector:
        def inspect(self, pid: int):
            if pid == 2:
                return None
            if pid == 3:
                return ProcessIdentity(3, 3, 30, "b" * 64)
            if pid == 4:
                return ProcessIdentity(4, 44, 40, "c" * 64)
            if pid == 6:
                raise PermissionError("procfs denied")
            raise AssertionError(f"unexpected inspection for pid {pid}")

    outcomes = repository.recover_expired_leases(
        Inspector(), trace_id="trace-recovery-matrix", recovery_id="recovery-1"
    )

    assert outcomes == tuple(
        (candidate_row["job_id"], f"RECOVERED_{index}")
        for index, candidate_row in enumerate(candidates)
    )
    assert "FROM jobs j JOIN job_attempts a" in connection.calls[0][0]
    observations = [parameters[10] for _, parameters in connection.calls[1:]]
    assert observations == [
        "UNVERIFIABLE",
        "ABSENT",
        "STILL_RUNNING",
        "IDENTITY_MISMATCH",
        "UNVERIFIABLE",
        "UNVERIFIABLE",
    ]
    for index, (_, parameters) in enumerate(connection.calls[1:]):
        assert parameters[:6] == (
            candidates[index]["job_id"],
            candidates[index]["attempt_id"],
            "RUNNING",
            "RUNNING",
            "worker-original",
            candidates[index]["lease_token"],
        )
        assert parameters[11:13] == (
            "trace-recovery-matrix",
            "recovery-1",
        )


@pytest.mark.parametrize("authority", (None, {"outcome": 7}))
def test_worker_recovery_fails_closed_on_invalid_authority_result(authority) -> None:
    candidate = {
        "job_id": "job",
        "attempt_id": "attempt",
        "state": "RUNNING",
        "attempt_outcome": "RUNNING",
        "lease_owner": "worker",
        "lease_token": "lease",
        "child_pid": None,
        "process_group_id": None,
        "process_start_ticks": None,
        "command_fingerprint": None,
    }
    connection = _Connection(authority)
    repository = _worker(connection)
    repository._new_id = lambda prefix: f"{prefix}_fixed"

    with pytest.raises(RuntimeError, match="invalid result"):
        repository._recover_observed_candidate(
            candidate,
            "UNVERIFIABLE",
            "trace-recovery-invalid",
            "recovery-1",
        )

    assert "job_plane.worker_recover_expired_paper" in connection.calls[0][0]
    assert connection.transaction_events == [("enter", None), ("exit", None)]
