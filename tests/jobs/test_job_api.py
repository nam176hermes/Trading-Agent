from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
from fastapi.testclient import TestClient

from apps.job_api.app import create_app
from apps.job_api.config import JobApiSettings
from packages.job_contracts import ActorIdentity, SnapshotPayload
from packages.job_contracts.transitions import InvalidTransition
from packages.runtime_release import (
    RuntimeAuthority,
    ValidatedJobPlaneAuthority,
    validate_job_plane_authority,
)
from services.job_store import (
    ArtifactRecord,
    AttemptRecord,
    EnqueueOutcome,
    EnqueueResult,
    EventRecord,
    IdempotencyConflict,
    JobDetailRecord,
    JobFilters,
    JobNotFound,
    JobRecord,
)


TEST_AUTH_VALUE = "endpoint-test-token"
TOKEN = TEST_AUTH_VALUE
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PRINCIPAL = ActorIdentity(actor_type="OPERATOR", actor_id="dashboard-service")
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class _ReadyResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _ReadyConnection:
    def execute(self, query):
        if "version_num" in query:
            return _ReadyResult(
                {"version_num": "0011_engine_backtest_worker_authority"}
            )
        return _ReadyResult({"?column?": 1})


class _ReadyPool:
    @contextmanager
    def connection(self):
        yield _ReadyConnection()


def isolated_job_plane_authority() -> ValidatedJobPlaneAuthority:
    authority = object.__new__(RuntimeAuthority)
    object.__setattr__(authority, "_identity", (211, 223))
    object.__setattr__(authority, "_document_sha256", "9" * 64)
    return validate_job_plane_authority(
        authority_loader=lambda: authority,
        application_attestor=lambda candidate: candidate is authority,
    )


def job_record(*, job_id: str = "job_123", state: str = "QUEUED") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        job_type="SNAPSHOT",
        state=state,
        payload=SnapshotPayload(scope="default", requested_as_of=None),
        payload_fingerprint="a" * 64,
        idempotency_key="must-not-be-returned",
        actor=ActorIdentity(actor_type="OPERATOR", actor_id="operator-1"),
        priority=3,
        requested_at=NOW,
        updated_at=NOW,
        attempt_count=0,
        max_attempts=2,
        reason_code="ENQUEUED",
        result_hash=None,
        cancel_requested_at=None,
        cancel_actor=None,
    )


class Repository:
    _pool = _ReadyPool()

    def __init__(self) -> None:
        self.jobs = [job_record()]
        self.enqueue_outcome = EnqueueOutcome.ENQUEUED
        self.last_enqueue = None
        self.last_filters = None
        self.last_cancel = None

    def readiness(self) -> tuple[bool, bool]:
        return True, True

    def enqueue(self, request, *, trace_id: str):
        self.last_enqueue = (request, trace_id)
        return EnqueueResult(self.jobs[0], self.enqueue_outcome)

    def list_jobs(self, filters):
        self.last_filters = filters
        return tuple(self.jobs)

    def get_job(self, job_id: str):
        if job_id == "missing":
            return None
        attempt = AttemptRecord(
            attempt_id="attempt_1",
            job_id=self.jobs[0].job_id,
            attempt_number=1,
            worker_id="worker-1",
            outcome="SUCCEEDED",
            claimed_at=NOW,
            started_at=NOW,
            finished_at=NOW,
            exit_code=0,
            termination_reason=None,
        )
        event = EventRecord(
            event_id="event_1",
            job_id=self.jobs[0].job_id,
            attempt_id="attempt_1",
            sequence=1,
            from_state=None,
            to_state="QUEUED",
            reason_code="ENQUEUED",
            actor=self.jobs[0].actor,
            trace_id="trace_event",
            metadata={"raw_stdout": "must-not-leak"},
            created_at=NOW,
        )
        artifact = ArtifactRecord(
            artifact_id="artifact_1",
            job_id=self.jobs[0].job_id,
            attempt_id="attempt_1",
            artifact_type="RESULT",
            relative_ref="private/path/result.json",
            sha256="b" * 64,
            size_bytes=42,
            media_type="application/json",
            truncated=False,
            validator_id="report-v1",
            validation_metadata={"stderr": "must-not-leak"},
            created_at=NOW,
        )
        return JobDetailRecord(self.jobs[0], (attempt,), (event,), (artifact,))

    def request_cancel(self, job_id: str, actor, trace_id: str):
        self.last_cancel = (job_id, actor, trace_id)
        if job_id == "missing":
            raise JobNotFound("does not exist")
        if job_id == "terminal":
            raise InvalidTransition("cannot cancel terminal job")
        return job_record(job_id=job_id, state="CANCELLED")


def client(repository: Repository | None = None) -> tuple[TestClient, Repository]:
    selected = repository or Repository()
    settings = JobApiSettings(bearer_token=TOKEN, principal=PRINCIPAL)
    return TestClient(
        create_app(settings, selected, isolated_job_plane_authority())
    ), selected


def enqueue_payload(**overrides):
    value = {
        "job_type": "SNAPSHOT",
        "payload": {"scope": "default", "requested_as_of": None},
        "idempotency_key": "manual:snapshot:api",
        "priority": 3,
    }
    value.update(overrides)
    return value


def test_create_returns_canonical_job_and_reports_deduplication() -> None:
    api, repository = client()

    created = api.post(
        "/v1/jobs", json=enqueue_payload(), headers={**AUTH, "X-Trace-Id": "trace_create"}
    )
    repository.enqueue_outcome = EnqueueOutcome.DEDUPLICATED
    duplicate = api.post("/v1/jobs", json=enqueue_payload(), headers=AUTH)

    assert created.status_code == 201
    assert created.json()["data"]["outcome"] == "ENQUEUED"
    assert created.json()["data"]["job"]["job_id"] == "job_123"
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["outcome"] == "DEDUPLICATED"
    assert repository.last_enqueue[0].actor == PRINCIPAL
    assert repository.last_enqueue[1].startswith("trace_")
    assert "idempotency_key" not in created.text


def test_create_maps_idempotency_conflict_to_typed_409() -> None:
    repository = Repository()

    def conflict(request, *, trace_id):
        raise IdempotencyConflict("SNAPSHOT", "manual:snapshot:api")

    repository.enqueue = conflict
    api, _ = client(repository)

    response = api.post("/v1/jobs", json=enqueue_payload(), headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert "manual:snapshot:api" not in response.text


def test_create_rejects_reserved_scheduler_namespace_before_repository() -> None:
    api, repository = client()

    response = api.post(
        "/v1/jobs",
        json=enqueue_payload(
            idempotency_key="schedule:snapshot:2026-07-16T12:00Z"
        ),
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert repository.last_enqueue is None


def test_list_maps_filters_and_returns_stable_page_shape() -> None:
    api, repository = client()

    response = api.get(
        "/v1/jobs?job_type=SNAPSHOT&state=QUEUED&actor_type=OPERATOR&actor_id=operator-1"
        "&requested_from=2026-07-12T00:00:00Z&requested_to=2026-07-13T00:00:00Z"
        "&limit=25&offset=10",
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["data"]["limit"] == 25
    assert response.json()["data"]["offset"] == 10
    assert response.json()["data"]["items"][0]["job_id"] == "job_123"
    assert repository.last_filters == JobFilters(
        job_type="SNAPSHOT",
        state="QUEUED",
        actor_type="OPERATOR",
        actor_id="operator-1",
        requested_from=datetime(2026, 7, 12, tzinfo=UTC),
        requested_to=datetime(2026, 7, 13, tzinfo=UTC),
        limit=25,
        offset=10,
    )


def test_job_filter_domain_construction_failure_is_typed_422(monkeypatch) -> None:
    from apps.job_api import app as job_api_app
    from services.job_store import InvalidJobFilters

    def reject_filters(**values):
        raise InvalidJobFilters("sensitive invalid filter repr")

    monkeypatch.setattr(job_api_app, "JobFilters", reject_filters)
    api, _ = client()

    response = api.get("/v1/jobs", headers=AUTH)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "sensitive" not in response.text


def test_detail_returns_sanitized_attempt_event_and_artifact_metadata() -> None:
    api, _ = client()

    response = api.get("/v1/jobs/job_123", headers=AUTH)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["attempts"][0]["artifact_count"] == 1
    assert data["events"][0]["reason_code"] == "ENQUEUED"
    assert data["artifacts"][0]["sha256"] == "b" * 64
    assert "relative_ref" not in response.text
    assert "raw_stdout" not in response.text
    assert "stderr" not in response.text
    assert client()[0].get("/v1/jobs/missing", headers=AUTH).status_code == 404


def test_cancel_attributes_actor_and_maps_invalid_transition_to_409() -> None:
    api, repository = client()

    cancelled = api.post(
        "/v1/jobs/job_123/cancel",
        json={},
        headers={**AUTH, "X-Trace-Id": "trace_cancel"},
    )
    attributed_trace = repository.last_cancel[2]
    conflict = api.post(
        "/v1/jobs/terminal/cancel",
        json={},
        headers=AUTH,
    )

    assert cancelled.status_code == 200
    assert repository.last_cancel[1] == PRINCIPAL
    assert cancelled.json()["data"]["state"] == "CANCELLED"
    assert attributed_trace.startswith("trace_")
    assert attributed_trace != "trace_cancel"
    assert cancelled.headers["x-trace-id"] == attributed_trace
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "INVALID_JOB_STATE"


def test_strict_payload_rejects_executable_fields_and_malformed_filters() -> None:
    api, repository = client()

    arbitrary_command = api.post(
        "/v1/jobs", json=enqueue_payload(command="rm -rf /"), headers=AUTH
    )
    malformed_filter = api.get("/v1/jobs?limit=0", headers=AUTH)
    forged_actor = api.post(
        "/v1/jobs",
        json=enqueue_payload(
            actor={"actor_type": "SYSTEM", "actor_id": "forged-admin"}
        ),
        headers=AUTH,
    )

    assert arbitrary_command.status_code == 422
    assert arbitrary_command.json()["error"]["code"] == "INVALID_REQUEST"
    assert forged_actor.status_code == 422
    assert repository.last_enqueue is None
    assert malformed_filter.status_code == 422


def test_repository_unavailability_is_503_and_unexpected_errors_are_sanitized() -> None:
    unavailable_repository = Repository()
    unavailable_repository.list_jobs = lambda filters: (_ for _ in ()).throw(
        ConnectionError("postgres credential secret-db-value")
    )
    unavailable, _ = client(unavailable_repository)
    broken_repository = Repository()
    broken_repository.list_jobs = lambda filters: (_ for _ in ()).throw(
        RuntimeError("internal secret traceback value")
    )
    broken, _ = client(broken_repository)

    unavailable_response = unavailable.get("/v1/jobs", headers=AUTH)
    broken_response = broken.get("/v1/jobs", headers=AUTH)

    assert unavailable_response.status_code == 503
    assert unavailable_response.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"
    assert "secret-db-value" not in unavailable_response.text
    assert broken_response.status_code == 500
    assert broken_response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "internal secret" not in broken_response.text


def test_unexpected_projection_error_is_caught_inside_boundary_and_logs_are_sanitized(
    monkeypatch, caplog
) -> None:
    from apps.job_api import app as job_api_app

    def fail_projection(record):
        raise RuntimeError("payload={'secret_input': 'must-not-log'}")

    monkeypatch.setattr(job_api_app, "_job_metadata", fail_projection)
    api = TestClient(
        create_app(
            JobApiSettings(bearer_token=TOKEN, principal=PRINCIPAL),
            Repository(),
            isolated_job_plane_authority(),
        ),
        raise_server_exceptions=False,
    )

    with caplog.at_level(logging.INFO):
        response = api.get("/v1/jobs", headers=AUTH)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "secret_input" not in caplog.text
    assert "must-not-log" not in caplog.text


def test_database_driver_failure_is_typed_repository_unavailability() -> None:
    repository = Repository()
    repository.list_jobs = lambda filters: (_ for _ in ()).throw(
        psycopg.OperationalError("database endpoint details")
    )
    api, _ = client(repository)

    response = api.get("/v1/jobs", headers=AUTH)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"
    assert "endpoint details" not in response.text


def test_openapi_models_exact_success_and_custom_error_envelopes() -> None:
    document = create_app(
        JobApiSettings(bearer_token=TOKEN, principal=PRINCIPAL),
        Repository(),
        isolated_job_plane_authority(),
    ).openapi()
    paths = document["paths"]
    enqueue_schema = document["components"]["schemas"]["EnqueueJobBody"]

    assert all(
        variant["properties"]["idempotency_key"]["not"]
        == {"pattern": "^schedule:"}
        for variant in enqueue_schema["oneOf"]
    )

    assert set(paths["/health/live"]["get"]["responses"]) == {"200", "413", "500"}
    assert set(paths["/health/ready"]["get"]["responses"]) == {
        "200", "413", "500", "503"
    }
    assert set(paths["/v1/jobs"]["post"]["responses"]) == {
        "200", "201", "401", "409", "413", "422", "500", "503"
    }
    assert set(paths["/v1/jobs"]["get"]["responses"]) == {
        "200", "401", "413", "422", "500", "503"
    }
    assert set(paths["/v1/jobs/{job_id}"]["get"]["responses"]) == {
        "200", "401", "404", "413", "422", "500", "503"
    }
    assert set(paths["/v1/jobs/{job_id}/cancel"]["post"]["responses"]) == {
        "200", "401", "404", "409", "413", "422", "500", "503"
    }

    expected_success_models = {
        ("/health/live", "get", "200"): "HealthLiveEnvelope",
        ("/health/ready", "get", "200"): "HealthReadyEnvelope",
        ("/health/ready", "get", "503"): "HealthReadyEnvelope",
        ("/v1/jobs", "post", "201"): "JobEnqueuedEnvelope",
        ("/v1/jobs", "post", "200"): "JobDeduplicatedEnvelope",
        ("/v1/jobs", "get", "200"): "JobListEnvelope",
        ("/v1/jobs/{job_id}", "get", "200"): "JobDetailEnvelope",
        ("/v1/jobs/{job_id}/cancel", "post", "200"): "JobEnvelope",
    }
    for (path, method, status), model in expected_success_models.items():
        response_schema = paths[path][method]["responses"][status]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{model}"}

    error_statuses = {
        (path, method, status)
        for path, methods in paths.items()
        for method, operation in methods.items()
        for status in operation["responses"]
        if status in {"401", "404", "409", "413", "422", "500", "503"}
        and not (path == "/health/ready" and status == "503")
    }
    for path, method, status in error_statuses:
        response_schema = paths[path][method]["responses"][status]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": "#/components/schemas/JobApiErrorEnvelope"}

    serialized = json.dumps(document["components"]["schemas"])
    assert "HTTPValidationError" not in serialized
    assert "relative_ref" not in serialized
    assert "stdout_ref" not in serialized
    assert "stderr_ref" not in serialized
    assert "lease_token" not in serialized
