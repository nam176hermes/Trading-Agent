from __future__ import annotations

import asyncio
import json
import logging

import pytest

from fastapi.testclient import TestClient

from apps.job_api import config as job_api_config
from apps.job_api.app import create_app as build_job_api
from apps.job_api.config import JobApiSettings
from packages.job_contracts import ActorIdentity
from packages.runtime_release import (
    ProtectedAuthorityError,
    RuntimeAuthority,
    validate_job_plane_authority,
)


PRINCIPAL = ActorIdentity(actor_type="OPERATOR", actor_id="dashboard-service")


def settings(token: str = "safe-token", **overrides) -> JobApiSettings:
    if "authority_factory" not in overrides:
        protected = _runtime_authority((97, 101), "2" * 64)
        capability = validate_job_plane_authority(
            authority_loader=lambda: protected,
            application_attestor=lambda _authority: True,
        )
        overrides["authority_factory"] = lambda: capability
    return JobApiSettings(bearer_token=token, principal=PRINCIPAL, **overrides)


class Repository:
    def list_jobs(self, filters):
        return ()


def _runtime_authority(
    identity: tuple[int, int], document_sha256: str
) -> RuntimeAuthority:
    authority = object.__new__(RuntimeAuthority)
    object.__setattr__(authority, "_identity", identity)
    object.__setattr__(authority, "_document_sha256", document_sha256)
    return authority


def create_app(configured, repository, authority=None):
    pinned = configured.load_authority() if authority is None else authority
    return build_job_api(configured, repository, pinned)


@pytest.mark.parametrize(
    "failure",
    [
        "absent",
        "raises",
        "rotated",
        "release_false",
        "release_raises",
        "release_rotated",
    ],
)
def test_entrypoint_rejects_invalid_authority_before_repository_construction(
    failure, monkeypatch
) -> None:
    from apps.job_api import main

    current = {"value": _runtime_authority((31, 41), "a" * 64)}
    release_valid = {"value": True}
    pinned = validate_job_plane_authority(
        authority_loader=lambda: current["value"],
        application_attestor=lambda _authority: release_valid["value"],
    )
    if failure == "absent":
        factory = lambda: None
    elif failure == "raises":
        def factory():
            raise RuntimeError("private authority path and digest")
    elif failure == "rotated":
        current["value"] = _runtime_authority((31, 43), "b" * 64)
        factory = lambda: pinned
    elif failure == "release_false":
        factory = lambda: validate_job_plane_authority(
            authority_loader=lambda: current["value"],
            application_attestor=lambda _authority: False,
        )
    elif failure == "release_raises":
        def raise_release_error(_authority):
            raise RuntimeError("private release path and digest")

        factory = lambda: validate_job_plane_authority(
            authority_loader=lambda: current["value"],
            application_attestor=raise_release_error,
        )
    else:
        release_valid["value"] = False
        factory = lambda: pinned

    configured = JobApiSettings(
        bearer_token="safe-token",
        principal=PRINCIPAL,
        authority_factory=factory,
    )
    repository_calls = []
    settings_calls = []
    store_calls = []
    app_calls = []
    uvicorn_calls = []
    monkeypatch.setattr(
        main,
        "validate_job_plane_authority",
        factory,
        raising=False,
    )
    monkeypatch.setattr(
        main.JobApiSettings,
        "from_env",
        lambda env=None: settings_calls.append(env) or configured,
    )
    monkeypatch.setattr(
        main.JobStoreSettings,
        "from_env",
        lambda env=None, *, expected_user: store_calls.append(
            (env, expected_user)
        ) or object(),
    )
    monkeypatch.setattr(
        main, "JobRepository", lambda store: repository_calls.append(store) or Repository()
    )
    monkeypatch.setattr(
        main,
        "create_app",
        lambda configured, repository, authority: app_calls.append(
            (configured, repository, authority)
        ) or "job-app",
    )
    monkeypatch.setattr(
        main.uvicorn, "run", lambda app, **kwargs: uvicorn_calls.append((app, kwargs))
    )

    with pytest.raises(ProtectedAuthorityError) as raised:
        main.run(env={})

    assert str(raised.value) == "protected runtime authority is unavailable"
    assert "private" not in repr(raised.value)
    assert settings_calls == []
    assert store_calls == []
    assert repository_calls == []
    assert app_calls == []
    assert uvicorn_calls == []


def test_request_body_larger_than_16_kib_is_rejected_before_json_parsing() -> None:
    client = TestClient(
        create_app(settings(), Repository())
    )

    response = client.post(
        "/v1/jobs",
        content=b"{" + b"x" * (16 * 1024) + b"}",
        headers={
            "Authorization": "Bearer safe-token",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_TOO_LARGE"


def test_tokens_and_headers_do_not_appear_in_response_or_request_log(
    caplog, monkeypatch
) -> None:
    sensitive_value = "unique-secret-must-never-leak"
    client = TestClient(create_app(settings(sensitive_value), Repository()))
    monkeypatch.setattr(logging.getLogger("job_api.request"), "disabled", False)

    with caplog.at_level(logging.INFO, logger="job_api.request"):
        response = client.get(
            "/v1/jobs",
            headers={"Authorization": f"Bearer {sensitive_value}", "X-Private": "header-secret"},
        )

    rendered = response.text + "\n" + caplog.text
    assert response.status_code == 200
    assert sensitive_value not in rendered
    assert "header-secret" not in rendered
    assert "authorization" not in rendered.lower()


def test_settings_representation_never_contains_token() -> None:
    sensitive_value = "representation-secret"
    configured = settings(sensitive_value)

    assert sensitive_value not in repr(configured)
    assert sensitive_value not in str(configured)


def test_job_api_has_no_cors_headers() -> None:
    client = TestClient(
        create_app(settings(), Repository())
    )

    response = client.options(
        "/v1/jobs",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_settings_reject_every_non_explicit_loopback_bind() -> None:
    for host in ("0.0.0.0", "::", "::1", "localhost", "192.168.1.20"):
        with pytest.raises(ValueError, match="127.0.0.1"):
            settings(host=host)


def test_entrypoint_defaults_to_fixed_loopback_address(monkeypatch) -> None:
    from apps.job_api import main

    configured = settings()
    captured = {}
    store_calls = []
    monkeypatch.setattr(
        main, "validate_job_plane_authority", configured.authority_factory
    )
    monkeypatch.setattr(main.JobApiSettings, "from_env", lambda env=None: configured)
    monkeypatch.setattr(
        main.JobStoreSettings,
        "from_env",
        lambda env=None, *, expected_user: store_calls.append(
            (env, expected_user)
        ) or object(),
    )
    monkeypatch.setattr(main, "JobRepository", lambda settings: Repository())
    monkeypatch.setattr(
        main, "create_app", lambda settings, repository, authority: "job-app"
    )
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, **kwargs))

    main.run(env={})

    assert captured == {
        "app": "job-app",
        "host": "127.0.0.1",
        "port": 8401,
        "access_log": False,
    }
    assert store_calls == [({}, "trading_job_api")]


@pytest.mark.parametrize(
    "configured_user",
    ("trading_jobs", "trading_job_worker", "trading_job_scheduler"),
)
def test_entrypoint_rejects_shared_or_cross_role_before_repository_construction(
    configured_user, monkeypatch
) -> None:
    from apps.job_api import main

    configured = settings()
    repository_calls = []
    uvicorn_calls = []
    monkeypatch.setattr(
        main, "validate_job_plane_authority", configured.authority_factory
    )
    monkeypatch.setattr(main.JobApiSettings, "from_env", lambda env=None: configured)
    monkeypatch.setattr(
        main,
        "JobRepository",
        lambda store: repository_calls.append(store) or Repository(),
    )
    monkeypatch.setattr(
        main.uvicorn, "run", lambda app, **kwargs: uvicorn_calls.append((app, kwargs))
    )

    with pytest.raises(ValueError, match="job database user does not match"):
        main.run(
            env={
                "TRADING_DATABASE_HOST": "127.0.0.1",
                "TRADING_DATABASE_PORT": "5432",
                "TRADING_DATABASE_NAME": "test_only",
                "TRADING_DATABASE_USER": configured_user,
                "TRADING_DATABASE_PASSWORD": "fixed-test-only-password",
            }
        )

    assert repository_calls == []
    assert uvicorn_calls == []


def test_settings_reject_mismatched_fixed_port_and_revision() -> None:
    assert getattr(job_api_config, "JOB_API_PORT", None) == 8401
    assert (
        getattr(job_api_config, "EXPECTED_REVISION", None)
        == "0006_job_transition_database_authority"
    )
    with pytest.raises(ValueError, match="8401"):
        settings(port=8402)
    with pytest.raises(ValueError, match="0006_job_transition_database_authority"):
        settings(expected_revision="0006_wrong")
    with pytest.raises(ValueError, match="8401"):
        JobApiSettings.from_env({"TRADING_JOB_API_PORT": "8402"})
    with pytest.raises(ValueError, match="0006_job_transition_database_authority"):
        JobApiSettings.from_env({"TRADING_JOB_API_EXPECTED_REVISION": "0006_wrong"})


def _run_asgi(app, scope, messages):
    sent = []
    consumed = 0

    async def receive():
        nonlocal consumed
        consumed += 1
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent, consumed


def _scope(path="/v1/jobs", headers=(), method="POST"):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8401),
    }


def test_unauthenticated_v1_request_is_rejected_without_consuming_streamed_body() -> None:
    app = create_app(settings(), Repository())
    sent, consumed = _run_asgi(
        app,
        _scope(),
        [{"type": "http.request", "body": b"{", "more_body": True}],
    )

    assert consumed == 0
    assert sent[0]["status"] == 401
    body = json.loads(sent[1]["body"])
    assert body["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_disconnect_aborts_request_without_replaying_partial_body_or_responding() -> None:
    class RecordingRepository(Repository):
        called = False

        def enqueue(self, request, *, trace_id):
            self.called = True

    repository = RecordingRepository()
    app = create_app(settings(), repository)
    sent, consumed = _run_asgi(
        app,
        _scope(headers=((b"authorization", b"Bearer safe-token"),)),
        [
            {"type": "http.request", "body": b'{"job_type":', "more_body": True},
            {"type": "http.disconnect"},
        ],
    )

    assert consumed == 2
    assert sent == []
    assert repository.called is False


def test_logs_use_allowlisted_endpoint_and_server_generated_trace(
    caplog, monkeypatch
) -> None:
    inbound = "trace_attacker_authoritative"
    client = TestClient(
        create_app(settings(), Repository())
    )
    monkeypatch.setattr(logging.getLogger("job_api.request"), "disabled", False)

    with caplog.at_level(logging.INFO, logger="job_api.request"):
        response = client.get(
            "/v1/jobs/private-job-id",
            headers={"Authorization": "Bearer safe-token", "X-Trace-Id": inbound},
        )

    assert response.headers["x-trace-id"].startswith("trace_")
    assert response.headers["x-trace-id"] != inbound
    assert response.json()["trace_id"] == response.headers["x-trace-id"]
    assert "private-job-id" not in caplog.text
    event = json.loads(caplog.records[-1].message)
    assert event["endpoint"] == "jobs.detail"
    assert "route" not in event
