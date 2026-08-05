from __future__ import annotations

import hmac
import inspect
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.job_api.app import create_app as build_job_api
from apps.job_api.config import JobApiSettings
from packages.job_contracts import ActorIdentity
import packages.runtime_release as runtime_release


PRINCIPAL = ActorIdentity(actor_type="OPERATOR", actor_id="dashboard-service")


def test_job_plane_authority_capability_is_exported_and_opaque() -> None:
    assert hasattr(runtime_release, "ValidatedJobPlaneAuthority")
    assert hasattr(runtime_release, "validate_job_plane_authority")

    current = _runtime_authority((17, 23), "a" * 64)
    capability = runtime_release.validate_job_plane_authority(
        authority_loader=lambda: current,
        application_attestor=lambda _authority: True,
    )

    assert isinstance(capability, runtime_release.ValidatedJobPlaneAuthority)
    assert repr(capability) == "ValidatedJobPlaneAuthority(validated=True)"
    assert capability.recheck_mutation() is capability
    with pytest.raises(FrozenInstanceError):
        capability._document_sha256 = "b" * 64


@pytest.mark.parametrize("failure", ["false", "raises"])
def test_job_plane_capability_requires_full_release_attestation(failure) -> None:
    protected = _runtime_authority((19, 29), "b" * 64)

    def attest(_authority):
        if failure == "raises":
            raise RuntimeError("private release path and digest")
        return False

    with pytest.raises(runtime_release.ProtectedAuthorityError) as raised:
        runtime_release.validate_job_plane_authority(
            authority_loader=lambda: protected,
            application_attestor=attest,
        )

    assert str(raised.value) == "protected runtime authority is unavailable"
    assert "private" not in repr(raised.value)


def test_job_plane_capability_rechecks_full_release_attestation() -> None:
    protected = _runtime_authority((23, 31), "c" * 64)
    release_valid = {"value": True}
    capability = runtime_release.validate_job_plane_authority(
        authority_loader=lambda: protected,
        application_attestor=lambda _authority: release_valid["value"],
    )
    release_valid["value"] = False

    with pytest.raises(runtime_release.ProtectedAuthorityError):
        capability.recheck_mutation()


def test_full_release_attestor_must_verify_the_exact_loaded_authority() -> None:
    authority_a = _runtime_authority((29, 37), "d" * 64)
    authority_b = _runtime_authority((31, 41), "e" * 64)
    observed = []

    def attest_only_b(authority):
        observed.append(authority)
        return authority is authority_b

    with pytest.raises(runtime_release.ProtectedAuthorityError):
        runtime_release.validate_job_plane_authority(
            authority_loader=lambda: authority_a,
            application_attestor=attest_only_b,
        )

    assert observed == [authority_a]


def test_capability_cannot_be_constructed_or_forged_outside_factory() -> None:
    protected = _runtime_authority((37, 43), "f" * 64)

    with pytest.raises(TypeError):
        runtime_release.ValidatedJobPlaneAuthority(
            _document_identity=protected._identity,
            _document_sha256=protected._document_sha256,
            _authority_loader=lambda: protected,
            _application_attestor=lambda _authority: True,
        )

    forged = object.__new__(runtime_release.ValidatedJobPlaneAuthority)
    object.__setattr__(forged, "_document_identity", protected._identity)
    object.__setattr__(forged, "_document_sha256", protected._document_sha256)
    object.__setattr__(forged, "_authority_loader", lambda: protected)
    object.__setattr__(forged, "_application_attestor", lambda _authority: True)

    with pytest.raises(runtime_release.ProtectedAuthorityError):
        forged.recheck_mutation()


def _runtime_authority(
    identity: tuple[int, int], document_sha256: str
) -> runtime_release.RuntimeAuthority:
    authority = object.__new__(runtime_release.RuntimeAuthority)
    object.__setattr__(authority, "_identity", identity)
    object.__setattr__(authority, "_document_sha256", document_sha256)
    return authority


def create_app(settings, repository, authority=None):
    pinned = settings.load_authority() if authority is None else authority
    return build_job_api(settings, repository, pinned)


def test_settings_accept_only_an_opaque_authority_factory() -> None:
    parameters = inspect.signature(JobApiSettings).parameters

    assert "authority_factory" in parameters
    assert "authority_attestor" not in parameters


@pytest.mark.parametrize(
    ("path", "payload", "repository_method"),
    [
        (
            "/v1/jobs",
            {
                "job_type": "SNAPSHOT",
                "payload": {"scope": "default", "requested_as_of": None},
                "idempotency_key": "manual:snapshot:authority-rotation",
                "priority": 0,
            },
            "enqueue",
        ),
        ("/v1/jobs/job_123/cancel", {}, "request_cancel"),
    ],
)
@pytest.mark.parametrize(
    "rotation", ["document", "release_false", "release_raises"]
)
def test_mutation_rechecks_pinned_authority_before_repository_access(
    path, payload, repository_method, rotation
) -> None:
    current = {"value": _runtime_authority((47, 53), "c" * 64)}
    release_state = {"value": True}

    def attest_release(_authority):
        if isinstance(release_state["value"], Exception):
            raise release_state["value"]
        return release_state["value"]

    capability = runtime_release.validate_job_plane_authority(
        authority_loader=lambda: current["value"],
        application_attestor=attest_release,
    )
    configured = JobApiSettings(
        bearer_token="separate-job-api-secret",
        principal=PRINCIPAL,
        authority_factory=lambda: capability,
    )

    class Repository:
        enqueue_calls = 0
        cancel_calls = 0

        def enqueue(self, request, *, trace_id):
            self.enqueue_calls += 1
            raise AssertionError("enqueue must not run")

        def request_cancel(self, job_id, actor, trace_id):
            self.cancel_calls += 1
            raise AssertionError("cancel must not run")

    repository = Repository()
    client = TestClient(create_app(configured, repository, capability))
    if rotation == "document":
        current["value"] = _runtime_authority((47, 59), "d" * 64)
    elif rotation == "release_false":
        release_state["value"] = False
    else:
        release_state["value"] = RuntimeError("private release verification detail")

    response = client.post(
        path,
        json=payload,
        headers={"Authorization": "Bearer separate-job-api-secret"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "JOB_PLANE_AUTHORITY_UNAVAILABLE",
        "message": "Job-plane mutation authority is unavailable.",
        "details": {},
    }
    assert repository.enqueue_calls == 0
    assert repository.cancel_calls == 0
    assert repository_method not in response.text
    assert "sha256" not in response.text.lower()
    assert "/opt/" not in response.text


class ReadinessRepository:
    def __init__(self, *, database_ready: bool = True, revision_ready: bool = True) -> None:
        self.database_ready = database_ready
        self.revision_ready = revision_ready

    def readiness(self) -> tuple[bool, bool]:
        return self.database_ready, self.revision_ready


def settings(
    token: str | None = "separate-job-api-secret", *, authority_ready=True
) -> JobApiSettings:
    protected = _runtime_authority((61, 67), "e" * 64)
    capability = runtime_release.validate_job_plane_authority(
        authority_loader=lambda: protected,
        application_attestor=lambda _authority: True,
    )

    def factory():
        if isinstance(authority_ready, Exception):
            raise authority_ready
        return capability if authority_ready is True else authority_ready

    return JobApiSettings(
        bearer_token=token,
        principal=PRINCIPAL if token is not None else None,
        authority_factory=factory,
    )


def test_job_routes_reject_missing_and_invalid_bearer_tokens() -> None:
    client = TestClient(create_app(settings(), ReadinessRepository()))

    missing = client.get("/v1/jobs")
    invalid = client.get(
        "/v1/jobs", headers={"Authorization": "Bearer definitely-wrong"}
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_authentication_uses_constant_time_byte_comparison(monkeypatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(hmac, "compare_digest", recording_compare)
    client = TestClient(create_app(settings(), ReadinessRepository()))

    response = client.get(
        "/v1/jobs", headers={"Authorization": "Bearer definitely-wrong"}
    )

    assert response.status_code == 401
    byte_calls = [call for call in calls if all(isinstance(value, bytes) for value in call)]
    assert len(byte_calls) == 1


def test_non_ascii_bearer_bytes_receive_uniform_401() -> None:
    client = TestClient(create_app(settings(), ReadinessRepository()))

    response = client.get("/v1/jobs", headers={"Authorization": b"Bearer caf\xe9"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert (
        response.json()["error"]["message"]
        == "Valid bearer authentication is required."
    )


def test_missing_token_configuration_fails_operations_and_readiness_closed() -> None:
    client = TestClient(create_app(settings(None), ReadinessRepository()))

    operation = client.get(
        "/v1/jobs", headers={"Authorization": "Bearer any-present-token"}
    )
    readiness = client.get("/health/ready")

    assert operation.status_code == 503
    assert operation.json()["error"]["code"] == "AUTHENTICATION_UNAVAILABLE"
    assert readiness.status_code == 503
    assert readiness.json()["data"]["status"] == "NOT_READY"


def test_token_without_server_principal_is_rejected_at_configuration_boundary() -> None:
    with pytest.raises(ValueError, match="token and principal"):
        JobApiSettings(bearer_token="orphan-token")
    with pytest.raises(ValueError, match="token and principal"):
        JobApiSettings.from_env({"TRADING_JOB_API_TOKEN": "orphan-token"})


def test_environment_binds_bearer_credential_to_exact_server_principal() -> None:
    configured = JobApiSettings.from_env(
        {
            "TRADING_JOB_API_TOKEN": "dashboard-token",
            "TRADING_JOB_API_PRINCIPAL_TYPE": "OPERATOR",
            "TRADING_JOB_API_PRINCIPAL_ID": "dashboard-service",
        }
    )

    assert configured.principal == PRINCIPAL
    with pytest.raises(ValueError):
        JobApiSettings.from_env(
            {
                "TRADING_JOB_API_TOKEN": "dashboard-token",
                "TRADING_JOB_API_PRINCIPAL_TYPE": "ADMIN",
                "TRADING_JOB_API_PRINCIPAL_ID": "forged-admin",
            }
        )


def test_systemd_credentials_bind_token_to_exact_server_principal(
    tmp_path: Path,
) -> None:
    credential_root = tmp_path / "credentials"
    credential_root.mkdir(mode=0o700)
    for name, value in {
        "job-api-token": "dashboard-token",
        "job-api-principal-type": "OPERATOR",
        "job-api-principal-id": "dashboard-service",
    }.items():
        path = credential_root / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o400)

    configured = JobApiSettings.from_systemd_credentials(
        {"CREDENTIALS_DIRECTORY": str(credential_root)}
    )

    assert configured.bearer_token == "dashboard-token"
    assert configured.principal == PRINCIPAL


def test_systemd_principal_validation_error_does_not_disclose_credential(
    tmp_path: Path,
) -> None:
    credential_root = tmp_path / "credentials"
    credential_root.mkdir(mode=0o700)
    sensitive_value = "private-invalid-principal-type-must-not-leak"
    for name, value in {
        "job-api-token": "dashboard-token",
        "job-api-principal-type": sensitive_value,
        "job-api-principal-id": "dashboard-service",
    }.items():
        path = credential_root / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o400)

    with pytest.raises(ValueError) as caught:
        JobApiSettings.from_systemd_credentials(
            {"CREDENTIALS_DIRECTORY": str(credential_root)}
        )

    assert sensitive_value not in str(caught.value)
    assert sensitive_value not in repr(caught.value)


def test_liveness_is_independent_of_database_and_readiness_checks_all_dependencies() -> None:
    database_down = TestClient(
        create_app(settings(), ReadinessRepository(database_ready=False))
    )
    revision_down = TestClient(
        create_app(settings(), ReadinessRepository(revision_ready=False))
    )

    assert database_down.get("/health/live").status_code == 200
    assert database_down.get("/health/ready").status_code == 503
    assert revision_down.get("/health/ready").status_code == 503


def test_liveness_remains_200_but_readiness_is_503_after_authority_rotation() -> None:
    current = {"value": _runtime_authority((71, 73), "f" * 64)}
    capability = runtime_release.validate_job_plane_authority(
        authority_loader=lambda: current["value"],
        application_attestor=lambda _authority: True,
    )
    configured = JobApiSettings(
        bearer_token="separate-job-api-secret",
        principal=PRINCIPAL,
        authority_factory=lambda: capability,
    )
    client = TestClient(create_app(configured, ReadinessRepository(), capability))
    current["value"] = _runtime_authority((71, 79), "0" * 64)

    assert client.get("/health/live").status_code == 200
    response = client.get("/health/ready")
    assert response.status_code == 503
    rendered = response.text
    assert "/opt/" not in rendered
    assert "sha256" not in rendered.lower()


def test_readiness_is_200_only_with_exact_valid_authority() -> None:
    class Result:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row
    class Connection:
        def execute(self, query):
            return Result(
                {"version_num": "0011_engine_backtest_worker_authority"}
                if "version_num" in query
                else {"?column?": 1}
            )
    class Pool:
        @contextmanager
        def connection(self): yield Connection()
    class Repository:
        _pool = Pool()

    client = TestClient(create_app(settings(authority_ready=True), Repository()))
    assert client.get("/health/ready").status_code == 200


def test_protected_authority_is_pinned_at_startup_and_rechecked_for_readiness() -> None:
    calls = []
    protected = _runtime_authority((83, 89), "1" * 64)

    def load():
        calls.append("load")
        return protected

    capability = runtime_release.validate_job_plane_authority(
        authority_loader=load, application_attestor=lambda _authority: True
    )
    configured = JobApiSettings(
        bearer_token="separate-job-api-secret",
        principal=PRINCIPAL,
        authority_factory=lambda: capability,
    )
    client = TestClient(create_app(configured, ReadinessRepository()))
    before_readiness = len(calls)

    client.get("/health/ready")

    assert before_readiness >= 2
    assert len(calls) == before_readiness + 2


def test_environment_cannot_supply_independent_digest_or_manifest_authority() -> None:
    for key in (
        "TRADING_APP_MANIFEST_SHA256",
        "TRADING_BACKEND_MANIFEST_SHA256",
        "TRADING_COMMAND_MANIFEST_SHA256",
        "TRADING_SEMANTIC_MANIFEST_SHA256",
    ):
        with pytest.raises(ValueError, match="protected runtime authority"):
            JobApiSettings.from_env({key: "a" * 64})


@pytest.mark.parametrize(
    ("database_revision", "expected_status", "expected_readiness"),
    [
        ("0011_engine_backtest_worker_authority", 200, "READY"),
        ("0006_job_transition_database_authority", 503, "NOT_READY"),
        ("unexpected_revision", 503, "NOT_READY"),
    ],
)
def test_readiness_requires_exact_canonical_database_revision(
    database_revision: str,
    expected_status: int,
    expected_readiness: str,
) -> None:
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, query):
            if "version_num" in query:
                return Result({"version_num": database_revision})
            return Result({"?column?": 1})

    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()

    class RealRepositoryShape:
        _pool = Pool()

    client = TestClient(create_app(settings(), RealRepositoryShape()))

    response = client.get("/health/ready")

    assert response.status_code == expected_status
    assert response.json()["data"]["status"] == expected_readiness


@pytest.mark.parametrize(
    ("path", "payload", "method"),
    [
        (
            "/v1/jobs",
            {
                "job_type": "SNAPSHOT",
                "payload": {"scope": "default", "requested_as_of": None},
                "idempotency_key": "manual:snapshot:db-head-rotation",
                "priority": 0,
            },
            "enqueue",
        ),
        ("/v1/jobs/job_123/cancel", {}, "cancel"),
    ],
)
def test_mutation_rechecks_database_health_and_exact_head_before_repository_write(
    path: str, payload: dict[str, object], method: str,
) -> None:
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    revision = {"value": "0005_job_plane_role_split"}

    class Connection:
        def execute(self, query):
            if "version_num" in query:
                return Result({"version_num": revision["value"]})
            return Result({"?column?": 1})

    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()

    class Repository:
        _pool = Pool()
        enqueue_calls = 0
        cancel_calls = 0

        def enqueue(self, *args, **kwargs):
            self.enqueue_calls += 1
            raise AssertionError("enqueue reached with stale database head")

        def request_cancel(self, *args, **kwargs):
            self.cancel_calls += 1
            raise AssertionError("cancel reached with stale database head")

    repository = Repository()
    client = TestClient(create_app(settings(), repository))

    response = client.post(
        path,
        json=payload,
        headers={"Authorization": "Bearer separate-job-api-secret"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"
    assert repository.enqueue_calls == 0
    assert repository.cancel_calls == 0
    assert method not in response.text


def test_readiness_ignores_repository_claim_and_verifies_actual_revision() -> None:
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, query):
            if "version_num" in query:
                return Result({"version_num": "0003_asset_source_lineage"})
            return Result({"?column?": 1})

    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()

    class MisreportingRepository:
        _pool = Pool()

        def readiness(self):
            return True, True

    client = TestClient(create_app(settings(), MisreportingRepository()))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["data"]["status"] == "NOT_READY"
