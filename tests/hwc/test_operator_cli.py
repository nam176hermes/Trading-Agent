from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from apps.operator_cli import cli


SHA = "a" * 64


class _ApiHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict[str, str], bytes]] = []
    responses: dict[tuple[str, str], tuple[int, dict[str, object]]] = {}

    def _handle(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append(
            (self.command, self.path, dict(self.headers.items()), body)
        )
        status, payload = type(self).responses.get(
            (self.command, self.path),
            (
                404,
                {"error": {"code": "NOT_FOUND", "message": "missing", "details": {}}},
            ),
        )
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *_args: object) -> None:
        return None


@contextmanager
def _api(
    responses: dict[tuple[str, str], tuple[int, dict[str, object]]],
) -> Iterator[str]:
    handler = type(
        "IsolatedApiHandler", (_ApiHandler,), {"requests": [], "responses": responses}
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
        _ApiHandler.requests = handler.requests


def _success(data: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "trace_id": "trace_test",
        "generated_at": "2026-09-01T00:00:00Z",
        "data": data,
    }


def _state(active: bool) -> dict[str, object]:
    return _success(
        {
            "state": {
                "schema_version": "operator-source-state-v1",
                "requested_mode": "PAPER",
                "kill_switch_state": "ACTIVE" if active else "INACTIVE",
                "kill_switch_activated_at": "2026-09-01T00:00:00Z" if active else None,
                "kill_switch_reason": "test" if active else None,
                "mode_file_sha256": SHA,
                "kill_switch_file_sha256": SHA if active else None,
                "state_sha256": SHA,
            }
        }
    )


def _receipt() -> dict[str, object]:
    return _success(
        {
            "result": {
                "schema_version": "operator-command-execution-result-v1",
                "receipt": {},
                "deduplicated": False,
            }
        }
    )


def _environment(
    monkeypatch: pytest.MonkeyPatch, origin: str, token_file: Path
) -> None:
    job_token_file = token_file.with_name("job-token")
    job_token_file.write_text("job-token".ljust(32, "j"), encoding="ascii")
    job_token_file.chmod(0o600)
    monkeypatch.setenv("TRADING_CONTROL_API_URL", origin)
    monkeypatch.setenv("TRADING_JOB_API_URL", origin)
    monkeypatch.setenv("TRADING_OPERATOR_API_URL", origin)
    monkeypatch.setenv("TRADING_JOB_API_TOKEN_FILE", str(job_token_file))
    monkeypatch.setenv("TRADING_OPERATOR_API_CLI_TOKEN_FILE", str(token_file))


def test_read_commands_work_with_dashboard_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: the independent CLI depends on a dashboard process or package."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("c" * 32, encoding="ascii")
    token_file.chmod(0o600)
    responses = {
        ("GET", "/v1/system/status"): (200, _success({"system": {"status": "READY"}})),
        ("GET", "/v1/capabilities"): (200, _success({"items": [], "total": 0})),
        ("GET", "/v1/jobs?limit=7&offset=2"): (
            200,
            _success({"items": [], "limit": 7, "offset": 2}),
        ),
        ("GET", "/v1/jobs/job_123"): (200, _success({"job": {"job_id": "job_123"}})),
        ("GET", "/v1/state"): (200, _state(False)),
    }
    with _api(responses) as origin:
        _environment(monkeypatch, origin, token_file)
        commands = [
            ["status"],
            ["capabilities"],
            ["jobs", "list", "--limit", "7", "--offset", "2"],
            ["jobs", "show", "job_123"],
            ["kill-switch", "status"],
        ]
        for arguments in commands:
            assert cli.main(arguments) == 0
            assert json.loads(capsys.readouterr().out)["schema_version"] == "1.0.0"

    assert all("apps.dashboard" not in name for name in os.sys.modules)
    authenticated = [
        request
        for request in _ApiHandler.requests
        if request[1].startswith("/v1/jobs") or request[1] == "/v1/state"
    ]
    assert [headers.get("Authorization") for _, _, headers, _ in authenticated] == [
        f"Bearer {'job-token'.ljust(32, 'j')}",
        f"Bearer {'job-token'.ljust(32, 'j')}",
        f"Bearer {'c' * 32}",
    ]


def test_operator_mutations_emit_exact_identity_and_request_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: command identity, correlation, or caller idempotency changes in transit."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("d" * 32, encoding="ascii")
    token_file.chmod(0o600)
    responses = {("POST", "/v1/commands"): (200, _receipt())}
    generated = iter(["1" * 32, "2" * 32, "3" * 32, "4" * 32])
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _size: next(generated))

    with _api(responses) as origin:
        _environment(monkeypatch, origin, token_file)
        assert cli.main(["mode", "paper", "--idempotency-key", "idem.mode"]) == 0
        capsys.readouterr()
        assert (
            cli.main(
                [
                    "kill-switch",
                    "activate",
                    "--reason",
                    "operator drill",
                    "--idempotency-key",
                    "idem.kill",
                ]
            )
            == 0
        )
        capsys.readouterr()

    posts = [request for request in _ApiHandler.requests if request[0] == "POST"]
    assert posts[0][3] == (
        b'{"command":{"command_type":"SET_REQUESTED_MODE","desired_mode":"PAPER"},'
        b'"command_id":"cmd_22222222222222222222222222222222","correlation_id":'
        b'"corr_11111111111111111111111111111111","expected_state_sha256":null,'
        b'"idempotency_key":"idem.mode","schema_version":"submit-operator-command-v1"}'
    )
    assert posts[1][3] == (
        b'{"command":{"command_type":"SET_KILL_SWITCH","desired_state":"ACTIVE","reason":"operator drill"},'
        b'"command_id":"cmd_44444444444444444444444444444444","correlation_id":'
        b'"corr_33333333333333333333333333333333","expected_state_sha256":null,'
        b'"idempotency_key":"idem.kill","schema_version":"submit-operator-command-v1"}'
    )


def test_kill_switch_clear_reads_active_state_and_binds_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: clear can race or apply without the observed state digest."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("e" * 32, encoding="ascii")
    token_file.chmod(0o600)
    responses = {
        ("GET", "/v1/state"): (200, _state(True)),
        ("POST", "/v1/commands"): (200, _receipt()),
    }
    generated = iter(["5" * 32, "6" * 32])
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _size: next(generated))

    with _api(responses) as origin:
        _environment(monkeypatch, origin, token_file)
        assert (
            cli.main(["kill-switch", "clear", "--idempotency-key", "idem.clear"]) == 0
        )
        capsys.readouterr()

    assert [(method, path) for method, path, _, _ in _ApiHandler.requests] == [
        ("GET", "/v1/state"),
        ("POST", "/v1/commands"),
    ]
    payload = json.loads(_ApiHandler.requests[1][3])
    assert payload["expected_state_sha256"] == SHA
    assert payload["command"] == {
        "command_type": "SET_KILL_SWITCH",
        "desired_state": "INACTIVE",
        "reason": None,
    }


def test_kill_switch_clear_refuses_inactive_state_without_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: clear posts a mutation when there is no active kill switch."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("f" * 32, encoding="ascii")
    token_file.chmod(0o600)
    with _api({("GET", "/v1/state"): (200, _state(False))}) as origin:
        _environment(monkeypatch, origin, token_file)
        assert (
            cli.main(["kill-switch", "clear", "--idempotency-key", "idem.clear"]) == 5
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": "KILL_SWITCH_NOT_ACTIVE"}
    assert [method for method, _, _, _ in _ApiHandler.requests] == ["GET"]


def test_job_cancel_posts_only_the_job_api_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: cancel bypasses Job API or invents unsupported command fields."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("g" * 32, encoding="ascii")
    token_file.chmod(0o600)
    responses = {
        ("POST", "/v1/jobs/job_123/cancel"): (200, _success({"job_id": "job_123"}))
    }
    with _api(responses) as origin:
        _environment(monkeypatch, origin, token_file)
        assert cli.main(["jobs", "cancel", "job_123"]) == 0
        capsys.readouterr()
    assert _ApiHandler.requests[0][3] == b"{}"


def test_cli_has_stable_configuration_and_upstream_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: automation cannot distinguish local configuration from API failure."""
    for key in (
        "TRADING_JOB_API_TOKEN_FILE",
        "TRADING_OPERATOR_API_CLI_TOKEN_FILE",
        "TRADING_CONTROL_API_URL",
        "TRADING_JOB_API_URL",
        "TRADING_OPERATOR_API_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["jobs", "list"]) == 2
    assert json.loads(capsys.readouterr().err) == {"code": "CONFIGURATION_ERROR"}

    token_file = tmp_path / "cli-token"
    token_file.write_text("h" * 32, encoding="ascii")
    token_file.chmod(0o600)
    with _api(
        {
            ("GET", "/v1/system/status"): (
                503,
                {
                    "error": {
                        "code": "SOURCE_UNAVAILABLE",
                        "message": "held",
                        "details": {},
                    }
                },
            )
        }
    ) as origin:
        _environment(monkeypatch, origin, token_file)
        assert cli.main(["status"]) == 4
    assert json.loads(capsys.readouterr().err) == {"code": "SOURCE_UNAVAILABLE"}


def test_cli_treats_malformed_job_token_as_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: malformed credential input escapes the stable CLI error boundary."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("j" * 32, encoding="ascii")
    token_file.chmod(0o600)
    with _api({}) as origin:
        _environment(monkeypatch, origin, token_file)
        job_token = tmp_path / "job-token"
        job_token.write_text(" padded-secret".ljust(32, "x"), encoding="ascii")
        job_token.chmod(0o600)
        monkeypatch.setenv("TRADING_JOB_API_TOKEN_FILE", str(job_token))
        assert cli.main(["jobs", "list"]) == 2
    assert json.loads(capsys.readouterr().err) == {"code": "CONFIGURATION_ERROR"}
    assert _ApiHandler.requests == []


def test_jobs_list_help_is_bounded_and_names_numeric_ranges(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Break caught: argparse expands million-value choices into unusable help output."""
    assert cli.main(["jobs", "list", "--help"]) == 0
    output = capsys.readouterr().out
    assert len(output) < 2_000
    assert "1..100" in output
    assert "0..1000000" in output


def test_usage_errors_are_sanitized_json_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["jobs", "list", "--limit", "0"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"code": "USAGE_ERROR"}
    assert captured.err.count("\n") == 1


def test_mutation_transport_failure_is_not_automatically_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: an ambiguous mutation outcome triggers a second POST automatically."""
    token_file = tmp_path / "cli-token"
    token_file.write_text("i" * 32, encoding="ascii")
    token_file.chmod(0o600)
    responses = {
        ("POST", "/v1/commands"): (
            503,
            {
                "error": {
                    "code": "COMMAND_OUTCOME_UNKNOWN",
                    "message": "unknown",
                    "details": {},
                }
            },
        )
    }
    with _api(responses) as origin:
        _environment(monkeypatch, origin, token_file)
        assert cli.main(["mode", "paper", "--idempotency-key", "idem.same"]) == 4
    assert json.loads(capsys.readouterr().err) == {"code": "COMMAND_OUTCOME_UNKNOWN"}
    assert len(_ApiHandler.requests) == 1


@pytest.mark.parametrize(
    ("status", "code", "exit_code"),
    (
        (401, "AUTHENTICATION_REQUIRED", 3),
        (403, "CAPABILITY_FORBIDDEN", 3),
        (409, "IDEMPOTENCY_CONFLICT", 5),
        (503, "OPERATOR_AUTHORITY_UNAVAILABLE", 4),
        (503, "COMMAND_OUTCOME_UNKNOWN", 4),
        (500, "INTERNAL_ERROR", 6),
    ),
)
def test_cli_maps_typed_api_failures_to_the_frozen_exit_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    code: str,
    exit_code: int,
) -> None:
    token_file = tmp_path / "cli-token"
    token_file.write_text("k" * 32, encoding="ascii")
    token_file.chmod(0o600)
    response = {"error": {"code": code, "message": "held", "details": {}}}
    with _api({("GET", "/v1/state"): (status, response)}) as origin:
        _environment(monkeypatch, origin, token_file)
        assert cli.main(["kill-switch", "status"]) == exit_code
    assert json.loads(capsys.readouterr().err) == {"code": code}
