from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.operator_api import main as main_module
from apps.operator_api.app import create_app
from apps.operator_api.config import OperatorApiSettings
from apps.operator_api.errors import OperatorApiError
from packages.operator_control.contracts import (
    CommandExecutionResultV1,
    CommandReceiptV1,
    OperatorActorV1,
    OperatorSourceStateV1,
    SubmitOperatorCommandV1,
)
from packages.operator_control.policy import OperatorCommandRejected
from services.operator_control.protected_fs import ProtectedFilesystemError
from services.operator_control.state_store import RecoveryError


WEB = OperatorActorV1(
    schema_version="operator-actor-v1", principal_id="operator.web", interface="WEB"
)
CLI = OperatorActorV1(
    schema_version="operator-actor-v1", principal_id="operator.cli", interface="CLI"
)
ZERO = "0" * 64


class StubAuthenticator:
    def __init__(self) -> None:
        self.calls = 0

    def authenticate(self, scope: dict[str, object]) -> OperatorActorV1:
        self.calls += 1
        values = [
            value
            for key, value in scope.get("headers", ())  # type: ignore[union-attr]
            if key.lower() == b"authorization"
        ]
        if len(values) != 1 or values[0] not in {b"Bearer web", b"Bearer cli"}:
            raise OperatorApiError(
                401,
                "AUTHENTICATION_REQUIRED",
                "Valid bearer authentication is required.",
            )
        return WEB if values[0] == b"Bearer web" else CLI


class StubService:
    def __init__(self) -> None:
        self.state = OperatorSourceStateV1(
            schema_version="operator-source-state-v1",
            requested_mode="PAPER",
            kill_switch_state="INACTIVE",
            kill_switch_activated_at=None,
            kill_switch_reason=None,
            mode_file_sha256=ZERO,
            kill_switch_file_sha256=None,
            state_sha256=ZERO,
        )
        self.calls: list[tuple[OperatorActorV1, object | None]] = []
        self.failure: Exception | None = None

    def read_state(self, actor: OperatorActorV1) -> OperatorSourceStateV1:
        self.calls.append((actor, None))
        if self.failure is not None:
            raise self.failure
        if actor.interface != "CLI":
            raise OperatorCommandRejected("CAPABILITY_FORBIDDEN", 403)
        return self.state

    def execute(
        self, actor: OperatorActorV1, request: SubmitOperatorCommandV1
    ) -> CommandExecutionResultV1:
        self.calls.append((actor, request))
        if self.failure is not None:
            raise self.failure
        command = request.command
        if actor.interface == "WEB" and (
            command.command_type != "SET_KILL_SWITCH"
            or command.desired_state != "ACTIVE"
        ):
            raise OperatorCommandRejected("CAPABILITY_FORBIDDEN", 403)
        receipt = CommandReceiptV1(
            schema_version="operator-command-receipt-v1",
            command_id=request.command_id,
            idempotency_key_sha256=ZERO,
            correlation_id=request.correlation_id,
            request_sha256=ZERO,
            actor=actor,
            command_type=command.command_type,
            desired_state=(
                "PAPER"
                if command.command_type == "SET_REQUESTED_MODE"
                else f"KILL_SWITCH_{command.desired_state}"
            ),
            prior_state_sha256=ZERO,
            expected_state_sha256=request.expected_state_sha256,
            safety_evidence_sha256=None,
            reason_sha256=None,
            accepted_at="2026-09-02T00:00:00Z",
            applied_at="2026-09-02T00:00:00Z",
            completed_at="2026-09-02T00:00:00Z",
            outcome="APPLIED",
            outcome_code="KILL_SWITCH_ACTIVATED",
            resulting_state_sha256=ZERO,
            intent_sha256=ZERO,
            applied_sha256=ZERO,
            receipt_sha256=ZERO,
        )
        return CommandExecutionResultV1(
            schema_version="operator-command-execution-result-v1",
            receipt=receipt,
            deduplicated=False,
        )


def settings(tmp_path: Path) -> OperatorApiSettings:
    return OperatorApiSettings(
        tmp_path / "web.token",
        "operator.web",
        tmp_path / "cli.token",
        "operator.cli",
    )


@pytest.fixture
def boundary(tmp_path: Path) -> tuple[TestClient, StubService, StubAuthenticator]:
    service = StubService()
    authenticator = StubAuthenticator()
    return (
        TestClient(create_app(settings(tmp_path), service, authenticator)),
        service,
        authenticator,
    )


def command(
    command_type: str = "SET_KILL_SWITCH",
    *,
    desired: str = "ACTIVE",
    reason: str | None = "operator stop",
) -> dict[str, object]:
    payload: dict[str, object]
    if command_type == "SET_REQUESTED_MODE":
        payload = {"command_type": command_type, "desired_mode": desired}
    else:
        payload = {
            "command_type": command_type,
            "desired_state": desired,
            "reason": reason,
        }
    return {
        "schema_version": "submit-operator-command-v1",
        "command_id": "cmd_" + "1" * 32,
        "idempotency_key": "request-1",
        "correlation_id": "correlation-1",
        "expected_state_sha256": None,
        "command": payload,
    }


def assert_boundary_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-trace-id"].startswith("trace_")


def test_app_has_exactly_four_routes_and_no_cors_or_redirects(boundary) -> None:
    client, _, _ = boundary
    routes = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", ())
    }
    assert routes == {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/v1/state"),
        ("POST", "/v1/commands"),
    }
    response = client.get(
        "/v1/state/",
        headers={"authorization": "Bearer cli"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers


def test_health_is_public_and_ready_after_pure_composition(boundary) -> None:
    client, service, authenticator = boundary
    for path, status in (("/health/live", "UP"), ("/health/ready", "READY")):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["data"] == {"status": status}
        assert_boundary_headers(response)
    assert authenticator.calls == 0
    assert service.calls == []


def test_state_is_cli_only_and_identity_headers_cannot_widen_access(boundary) -> None:
    client, service, _ = boundary
    cli = client.get("/v1/state", headers={"authorization": "Bearer cli"})
    assert cli.status_code == 200
    assert cli.json()["data"]["state"]["state_sha256"] == ZERO
    web = client.get(
        "/v1/state",
        headers={"authorization": "Bearer web", "x-operator-interface": "CLI"},
    )
    assert (web.status_code, web.json()["error"]["code"]) == (
        403,
        "CAPABILITY_FORBIDDEN",
    )
    assert service.calls[-1][0] == WEB


@pytest.mark.parametrize(
    ("token", "payload", "status", "code"),
    (
        ("web", command(), 200, None),
        (
            "web",
            command("SET_REQUESTED_MODE", desired="PAPER", reason=None),
            403,
            "CAPABILITY_FORBIDDEN",
        ),
        ("web", command(desired="INACTIVE", reason=None), 403, "CAPABILITY_FORBIDDEN"),
        ("cli", command(), 200, None),
        ("cli", command("SET_REQUESTED_MODE", desired="PAPER", reason=None), 200, None),
        ("cli", command(desired="INACTIVE", reason=None), 200, None),
    ),
)
def test_command_capability_matrix(boundary, token, payload, status, code) -> None:
    client, _, _ = boundary
    response = client.post(
        "/v1/commands", json=payload, headers={"authorization": f"Bearer {token}"}
    )
    assert response.status_code == status
    if code:
        assert response.json()["error"]["code"] == code
    else:
        assert (
            response.json()["data"]["result"]["receipt"]["actor"]["interface"]
            == token.upper()
        )


def test_body_cap_runs_before_json_parser_and_service(boundary) -> None:
    client, service, _ = boundary
    response = client.post(
        "/v1/commands",
        content=b"{" + b"x" * 8192,
        headers={"authorization": "Bearer cli", "content-type": "application/json"},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (
        413,
        "REQUEST_BODY_TOO_LARGE",
    )
    assert service.calls == []


@pytest.mark.parametrize(
    "headers",
    (
        {},
        {"authorization": "Basic nope"},
        [("authorization", "Bearer cli"), ("authorization", "Bearer cli")],
    ),
)
def test_missing_malformed_and_duplicate_authorization_are_401(
    boundary, headers
) -> None:
    client, _, _ = boundary
    response = client.get("/v1/state", headers=headers)
    assert (response.status_code, response.json()["error"]["code"]) == (
        401,
        "AUTHENTICATION_REQUIRED",
    )
    assert_boundary_headers(response)


def test_invalid_json_and_model_are_typed_422(boundary) -> None:
    client, _, _ = boundary
    for content in (b"{", json.dumps({**command(), "actor": {}}).encode()):
        response = client.post(
            "/v1/commands",
            content=content,
            headers={"authorization": "Bearer cli", "content-type": "application/json"},
        )
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "INVALID_REQUEST",
        )


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    (
        (OperatorCommandRejected("PAPER_ONLY_RELEASE", 403), 403, "PAPER_ONLY_RELEASE"),
        (
            OperatorCommandRejected("LIVE_EXECUTION_DISABLED", 403),
            403,
            "LIVE_EXECUTION_DISABLED",
        ),
        (
            OperatorCommandRejected("EXPECTED_STATE_CONFLICT", 409),
            409,
            "EXPECTED_STATE_CONFLICT",
        ),
        (
            OperatorCommandRejected("IDEMPOTENCY_CONFLICT", 409),
            409,
            "IDEMPOTENCY_CONFLICT",
        ),
        (
            OperatorCommandRejected("KILL_SWITCH_CLEAR_UNSAFE", 409),
            409,
            "KILL_SWITCH_CLEAR_UNSAFE",
        ),
        (
            OperatorCommandRejected("SOURCE_STATE_UNKNOWN", 503),
            503,
            "OPERATOR_AUTHORITY_UNAVAILABLE",
        ),
        (
            ProtectedFilesystemError("private path"),
            503,
            "OPERATOR_AUTHORITY_UNAVAILABLE",
        ),
        (RecoveryError("COMMAND_OUTCOME_UNKNOWN"), 503, "COMMAND_OUTCOME_UNKNOWN"),
        (RuntimeError("secret reason"), 500, "INTERNAL_ERROR"),
    ),
)
def test_service_failures_have_exact_sanitized_http_mapping(
    boundary, failure, status, code
) -> None:
    client, service, _ = boundary
    service.failure = failure
    response = client.post(
        "/v1/commands", json=command(), headers={"authorization": "Bearer cli"}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert "secret reason" not in response.text


def test_logs_are_metadata_only_after_authentication(boundary, caplog) -> None:
    client, _, _ = boundary
    caplog.set_level(logging.INFO, logger="operator_api.request")
    token = "cli"
    reason = "private operator reason"
    response = client.post(
        "/v1/commands",
        json=command(reason=reason),
        headers={"authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    record = json.loads(caplog.records[-1].message)
    assert set(record) == {
        "trace_id",
        "method",
        "endpoint",
        "status_code",
        "duration_ms",
        "principal_id",
    }
    assert record["principal_id"] == "operator.cli"
    assert "Bearer cli" not in caplog.text
    assert reason not in caplog.text


def test_create_app_does_not_touch_service_or_authenticator(tmp_path: Path) -> None:
    class Untouchable:
        def __getattribute__(self, name: str):
            raise AssertionError(f"construction touched {name}")

    app = create_app(settings(tmp_path), Untouchable(), Untouchable())
    assert app.title == "Trading Agent Operator API"


def test_runtime_composes_before_fixed_loopback_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = settings(tmp_path)
    authenticator = object()
    service = object()
    app = object()
    observed: list[tuple[str, object]] = []
    monkeypatch.setattr(
        main_module.OperatorApiSettings,
        "from_env",
        lambda environment: observed.append(("settings", environment)) or configured,
    )
    monkeypatch.setattr(
        main_module,
        "OperatorAuthenticator",
        lambda value: observed.append(("authenticator", value)) or authenticator,
    )
    monkeypatch.setattr(
        main_module,
        "build_production_operator_control_service",
        lambda value: observed.append(("service", value)) or service,
    )
    monkeypatch.setattr(
        main_module,
        "create_app",
        lambda *values: observed.append(("app", values)) or app,
    )
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda value, **options: observed.append(("run", (value, options))),
    )

    environment = {"isolated": "value"}
    main_module.run(env=environment)

    assert observed[0] == ("settings", environment)
    assert observed[1] == ("authenticator", configured)
    assert observed[3] == ("app", (configured, service, authenticator))
    assert observed[4] == (
        "run",
        (app, {"host": "127.0.0.1", "port": 8402, "access_log": False}),
    )
