#!/usr/bin/env python3
"""Qualify paper-runtime independence from the disposable dashboard process."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.operator_control.policy import OperatorCommandRejected
from packages.pre_p3_provenance import (
    SOURCE_CLOSURE_POLICY_SHA256,
    SOURCE_CLOSURE_SCHEMA,
    canonical_source_identity,
)
from services.operator_control.state_store import OperatorStatePaths, OperatorStateStore


AUTHORITY = {"broker": False, "live": False, "network": False, "production": False}
RUNNER = ROOT / "tests/hwc/fixtures/headless_runtime_runner.py"
CLOSURE_MATRIX = ROOT / "docs/implementation/hwc/hwc-closure-matrix-v1.json"
DASHBOARD = ROOT / "apps/dashboard"
_HEX = set("0123456789abcdef")


class HeadlessQualificationError(RuntimeError):
    pass


def _sha(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        raw = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start(command: list[str], *, cwd: Path, env: dict[str, str], log: Any) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _stop(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _wait(url: str, process: subprocess.Popen[Any], *, headers: dict[str, str] | None = None) -> None:
    for _ in range(120):
        if process.poll() is not None:
            raise HeadlessQualificationError(f"process exited before readiness: {url}")
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise HeadlessQualificationError(f"process readiness timed out: {url}")


def _json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200 or not response.headers.get_content_type() == "application/json":
                raise HeadlessQualificationError(f"unexpected HTTP response: {url}")
            payload = json.loads(response.read(512 * 1024 + 1))
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
        raise HeadlessQualificationError(f"HTTP evidence unavailable: {url}") from exc
    if not isinstance(payload, dict):
        raise HeadlessQualificationError("HTTP evidence is not an object")
    return payload


def _dashboard_view(port: int) -> dict[str, Any]:
    login = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/auth/session",
        data=canonical_json_bytes({"password": "fixture-reader-password"}),
        headers={
            "content-type": "application/json",
            "cf-connecting-ip": "198.51.100.10",
            "x-trusted-proxy-secret": "fixture-trusted-proxy-secret",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(login, timeout=5) as response:
            if response.status != 200:
                raise HeadlessQualificationError("dashboard login failed")
            cookie = response.headers.get("set-cookie", "").split(";", 1)[0]
        if not cookie.startswith("trading_session="):
            raise HeadlessQualificationError("dashboard session cookie is unavailable")
        view = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/trading/mode", headers={"cookie": cookie}
        )
        with urllib.request.urlopen(view, timeout=5) as response:
            payload = json.loads(response.read(64 * 1024 + 1))
    except (OSError, UnicodeError, ValueError, urllib.error.URLError) as exc:
        raise HeadlessQualificationError("dashboard view is unavailable") from exc
    if payload != {
        "ok": True,
        "requested_mode": "paper",
        "effective_mode": "paper",
        "live_execution_enabled": False,
    }:
        raise HeadlessQualificationError("dashboard did not reconstruct the API view")
    return payload


def _batch(name: str) -> dict[str, str]:
    return {
        "schema_version": "hwc-paper-batch-v1",
        "batch": name,
        "price": "100.00" if name == "A" else "101.25",
        "target_quantity": "1.00000000" if name == "A" else "1.25000000",
    }


def _runtime_exchange(process: subprocess.Popen[bytes], payload: dict[str, str]) -> dict[str, Any]:
    if process.poll() is not None or process.stdin is None or process.stdout is None:
        raise HeadlessQualificationError("paper runtime is unavailable")
    process.stdin.write(canonical_json_bytes(payload) + b"\n")
    process.stdin.flush()
    raw = process.stdout.readline()
    try:
        result = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HeadlessQualificationError("paper runtime result is invalid") from exc
    if not isinstance(result, dict) or result.get("schema_version") != "hwc-paper-result-v1":
        raise HeadlessQualificationError("paper runtime rejected the batch")
    return result


def _runtime_ready(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    if process.stdout is None:
        raise HeadlessQualificationError("paper runtime output is unavailable")
    try:
        payload = json.loads(process.stdout.readline())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HeadlessQualificationError("paper runtime readiness is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "hwc-headless-runtime-ready-v1":
        raise HeadlessQualificationError("paper runtime did not become ready")
    return payload


def _build_dashboard(environment: dict[str, str], log: Any) -> None:
    result = subprocess.run(
        [str(DASHBOARD / "node_modules/.bin/next"), "build", "--webpack"],
        cwd=DASHBOARD,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        raise HeadlessQualificationError("dashboard build failed")


def _dashboard_command(port: int) -> list[str]:
    return [str(DASHBOARD / "node_modules/.bin/next"), "start", "-H", "127.0.0.1", "-p", str(port)]


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["receipt_sha256"] = _sha(payload)
    return validate_receipt(payload)


def validate_receipt(payload: object) -> dict[str, Any]:
    required = {
        "schema_version", "source", "evidence", "processes", "observations",
        "cleanup", "authority", "verdict", "receipt_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise HeadlessQualificationError("headless receipt field set is invalid")
    source = payload.get("source")
    evidence = payload.get("evidence")
    processes = payload.get("processes")
    observations = payload.get("observations")
    cleanup = payload.get("cleanup")
    if (
        payload.get("schema_version") != "hwc-headless-portable-evidence-v1"
        or payload.get("verdict") != "PASS"
        or payload.get("authority") != AUTHORITY
        or not isinstance(source, dict)
        or set(source) != {"commit_sha", "tree_sha", "closure_sha256", "closure_schema_version", "closure_policy_sha256"}
        or source.get("closure_schema_version") != SOURCE_CLOSURE_SCHEMA
        or source.get("closure_policy_sha256") != SOURCE_CLOSURE_POLICY_SHA256
        or not isinstance(evidence, dict)
        or set(evidence) != {"closure_matrix_sha256", "runtime_fixture_sha256", "dashboard_build_sha256", "batch_a", "batch_b", "dashboard_before_sha256", "dashboard_after_sha256", "operator_before_sha256", "operator_after_sha256"}
        or not isinstance(processes, dict)
        or set(processes) != {"runtime_pid", "runtime_session_id", "control_api_pid", "operator_api_pid", "dashboard_initial_pid", "dashboard_restart_pid"}
        or not all(type(processes[key]) is int and processes[key] > 0 for key in processes if key.endswith("_pid"))
        or not isinstance(observations, dict)
        or set(observations) != {"runtime_alive_after_dashboard_kill", "control_api_alive_after_dashboard_kill", "operator_api_alive_after_dashboard_kill", "batch_b_after_dashboard_kill", "operator_state_unchanged", "dashboard_reconstructed"}
        or set(observations.values()) != {True}
        or not isinstance(cleanup, dict)
        or set(cleanup) != {"process_groups_stopped", "complete"}
        or cleanup.get("complete") is not True
        or cleanup.get("process_groups_stopped") != 5
    ):
        raise HeadlessQualificationError("headless receipt evidence is invalid")
    digests: list[object] = [
        source.get("closure_sha256"), source.get("closure_policy_sha256"),
        *[evidence.get(key) for key in ("closure_matrix_sha256", "runtime_fixture_sha256", "dashboard_build_sha256", "dashboard_before_sha256", "dashboard_after_sha256", "operator_before_sha256", "operator_after_sha256")],
    ]
    for batch_name in ("batch_a", "batch_b"):
        batch = evidence.get(batch_name)
        if not isinstance(batch, dict) or set(batch) != {"input_sha256", "output_sha256", "event_sha256", "result_sha256", "checkpoint_sha256"}:
            raise HeadlessQualificationError("headless batch evidence is invalid")
        digests.extend(batch.values())
    if any(not isinstance(value, str) or len(value) != 64 or set(value) - _HEX for value in digests):
        raise HeadlessQualificationError("headless receipt digest is invalid")
    if evidence["operator_before_sha256"] != evidence["operator_after_sha256"] or evidence["dashboard_before_sha256"] != evidence["dashboard_after_sha256"]:
        raise HeadlessQualificationError("headless receipt state changed")
    if payload.get("receipt_sha256") != _sha({key: value for key, value in payload.items() if key != "receipt_sha256"}):
        raise HeadlessQualificationError("headless receipt seal is invalid")
    return payload


def qualify(
    *, require_clean: bool = True, build_dashboard: bool = True, _fault: str | None = None
) -> dict[str, Any]:
    if _fault not in {None, "dashboard_startup", "port_collision", "runtime_exit", "partial_evidence", "cleanup_failure"}:
        raise ValueError("unknown test fault")
    if require_clean and subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, stdout=subprocess.PIPE, check=True
    ).stdout:
        raise HeadlessQualificationError("qualification requires a clean committed source")
    environment = dict(os.environ)
    environment.update({"TMPDIR": "/tmp", "TMP": "/tmp", "TEMP": "/tmp", "NEXT_TELEMETRY_DISABLED": "1"})
    processes: list[subprocess.Popen[Any]] = []
    with tempfile.TemporaryDirectory(prefix="hwc-headless-", dir="/tmp") as temporary, ExitStack() as stack:
        work = Path(temporary)
        log = stack.enter_context((work / "qualification.log").open("wb"))
        data = work / "data"
        data.mkdir(mode=0o700)
        (data / ".operator-commands").mkdir(mode=0o700)
        (data / ".mode").write_bytes(b"paper\n")
        (data / ".mode").chmod(0o600)
        token_root = work / "credentials"
        token_root.mkdir(mode=0o700)
        web_token = "w" * 48
        cli_token = "c" * 48
        for name, token in (("web.token", web_token), ("cli.token", cli_token)):
            (token_root / name).write_text(token + "\n", encoding="ascii")
            (token_root / name).chmod(0o600)
        control_port, operator_port, dashboard_port = _port(), _port(), _port()
        runtime = subprocess.Popen(
            [sys.executable, str(RUNNER), "--root", str(work / "runtime")],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
        processes.append(runtime)
        stack.callback(_stop, runtime)
        ready = _runtime_ready(runtime)
        if _fault == "runtime_exit":
            _stop(runtime)
            _runtime_exchange(runtime, _batch("A"))
        control = _start(
            [sys.executable, str(__file__), "--serve-control", str(data), str(control_port)],
            cwd=ROOT, env=environment, log=log,
        )
        operator = _start(
            [sys.executable, str(__file__), "--serve-operator", str(data), str(operator_port), str(token_root / "web.token"), str(token_root / "cli.token")],
            cwd=ROOT, env=environment, log=log,
        )
        processes.extend((control, operator))
        stack.callback(_stop, control)
        stack.callback(_stop, operator)
        collision: socket.socket | None = None
        if _fault == "port_collision":
            _stop(control)
            collision = socket.socket()
            collision.bind(("127.0.0.1", control_port))
            collision.listen()
            stack.callback(collision.close)
            control = _start(
                [sys.executable, str(__file__), "--serve-control", str(data), str(control_port)],
                cwd=ROOT, env=environment, log=log,
            )
            processes[-2] = control
            stack.callback(_stop, control)
        _wait(f"http://127.0.0.1:{control_port}/health/ready", control)
        _wait(f"http://127.0.0.1:{operator_port}/health/ready", operator)
        if build_dashboard:
            _build_dashboard(environment, log)
        dashboard_environment = {
            **environment,
            "HOME": str(work / "home"),
            "TRADING_DATA_ROOT": str(data),
            "TRADING_DASHBOARD_PASSWORD": "fixture-reader-password",
            "TRADING_DASHBOARD_OPERATOR_PASSWORD": "fixture-operator-password",
            "TRADING_DASHBOARD_ADMIN_PASSWORD": "fixture-admin-password",
            "TRADING_DASHBOARD_SESSION_SECRET": "fixture-session-signing-secret-at-least-32-characters",
            "TRADING_DASHBOARD_TRUSTED_PROXY_SECRET": "fixture-trusted-proxy-secret",
            "TRADING_CONTROL_API_ORIGIN": f"http://127.0.0.1:{control_port}",
            "TRADING_OPERATOR_API_ORIGIN": f"http://127.0.0.1:{operator_port}",
            "OPERATOR_API_WEB_TOKEN_FILE": str(token_root / "web.token"),
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        }
        (work / "home").mkdir(mode=0o700)
        dashboard_command = (
            [sys.executable, "-c", "raise SystemExit(7)"]
            if _fault == "dashboard_startup"
            else _dashboard_command(dashboard_port)
        )
        dashboard = _start(dashboard_command, cwd=DASHBOARD, env=dashboard_environment, log=log)
        processes.append(dashboard)
        stack.callback(_stop, dashboard)
        _wait(f"http://127.0.0.1:{dashboard_port}/api/auth/session", dashboard)
        batch_a_request = _batch("A")
        batch_a = _runtime_exchange(runtime, batch_a_request)
        before_view = _dashboard_view(dashboard_port)
        operator_before = _json(
            f"http://127.0.0.1:{operator_port}/v1/state",
            headers={"authorization": f"Bearer {cli_token}"},
        )["data"]["state"]
        os.killpg(dashboard.pid, signal.SIGKILL)
        dashboard.wait(timeout=5)
        alive = (runtime.poll() is None, control.poll() is None, operator.poll() is None)
        batch_b_request = _batch("B")
        batch_b = _runtime_exchange(runtime, batch_b_request)
        operator_after = _json(
            f"http://127.0.0.1:{operator_port}/v1/state",
            headers={"authorization": f"Bearer {cli_token}"},
        )["data"]["state"]
        dashboard_restart = _start(_dashboard_command(dashboard_port), cwd=DASHBOARD, env=dashboard_environment, log=log)
        processes.append(dashboard_restart)
        stack.callback(_stop, dashboard_restart)
        _wait(f"http://127.0.0.1:{dashboard_port}/api/auth/session", dashboard_restart)
        after_view = _dashboard_view(dashboard_port)
        for process in reversed(processes):
            _stop(process)
        cleanup_complete = all(process.poll() is not None for process in processes)
        fixture_digest = _sha({
            "fixture": _file_sha(ROOT / "tests/fixtures/paper_runtime.py"),
            "runner": _file_sha(RUNNER),
        })
        def batch_evidence(request: dict[str, str], result: dict[str, Any]) -> dict[str, str]:
            return {
                "input_sha256": _sha(request),
                "output_sha256": _sha(result),
                "event_sha256": result["event_sha256"],
                "result_sha256": result["result_sha256"],
                "checkpoint_sha256": result["checkpoint_sha256"],
            }
        receipt = {
            "schema_version": "hwc-headless-portable-evidence-v1",
            "source": canonical_source_identity(ROOT),
            "evidence": {
                "closure_matrix_sha256": _file_sha(CLOSURE_MATRIX),
                "runtime_fixture_sha256": fixture_digest,
                "dashboard_build_sha256": _tree_sha(DASHBOARD / ".next"),
                "batch_a": batch_evidence(batch_a_request, batch_a),
                "batch_b": batch_evidence(batch_b_request, batch_b),
                "dashboard_before_sha256": _sha(before_view),
                "dashboard_after_sha256": _sha(after_view),
                "operator_before_sha256": operator_before["state_sha256"],
                "operator_after_sha256": operator_after["state_sha256"],
            },
            "processes": {
                "runtime_pid": runtime.pid,
                "runtime_session_id": ready["session_id"],
                "control_api_pid": control.pid,
                "operator_api_pid": operator.pid,
                "dashboard_initial_pid": dashboard.pid,
                "dashboard_restart_pid": dashboard_restart.pid,
            },
            "observations": {
                "runtime_alive_after_dashboard_kill": alive[0],
                "control_api_alive_after_dashboard_kill": alive[1],
                "operator_api_alive_after_dashboard_kill": alive[2],
                "batch_b_after_dashboard_kill": batch_b["sequence"] == 2,
                "operator_state_unchanged": operator_before == operator_after,
                "dashboard_reconstructed": before_view == after_view,
            },
            "cleanup": {"process_groups_stopped": len(processes), "complete": cleanup_complete},
            "authority": dict(AUTHORITY),
            "verdict": "PASS",
        }
        if _fault == "partial_evidence":
            receipt["evidence"].pop("batch_b")
        if _fault == "cleanup_failure":
            receipt["cleanup"]["complete"] = False
        return _seal(receipt)


def _serve_control(data_root: Path, port: int) -> None:
    import uvicorn
    sys.path.insert(0, str(ROOT / "apps/control_api"))
    from control_api.app import create_app
    from control_api.config import Settings

    app = create_app(
        Settings(data_root=data_root, allowed_origins=()),
        env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _serve_operator(data_root: Path, port: int, web_token: Path, cli_token: Path) -> None:
    import uvicorn
    from apps.operator_api.app import create_app
    from apps.operator_api.auth import OperatorAuthenticator
    from apps.operator_api.config import OperatorApiSettings

    paths = OperatorStatePaths(
        data_root=data_root,
        command_root=data_root / ".operator-commands",
        mode_path=data_root / ".mode",
        kill_switch_path=data_root / ".kill_switch",
    )
    store = OperatorStateStore(paths)

    class ReadOnlyService:
        @staticmethod
        def read_state(actor: Any) -> Any:
            if actor.interface != "CLI":
                raise OperatorCommandRejected("CAPABILITY_FORBIDDEN", 403)
            return store.read_state()

        @staticmethod
        def execute(actor: Any, request: Any) -> Any:
            raise OperatorCommandRejected("CAPABILITY_FORBIDDEN", 403)

    settings = OperatorApiSettings(web_token, "dashboard.reader", cli_token, "operator.cli")
    uvicorn.run(
        create_app(settings, ReadOnlyService(), OperatorAuthenticator(settings)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve-control", nargs=2, metavar=("DATA_ROOT", "PORT"))
    parser.add_argument("--serve-operator", nargs=4, metavar=("DATA_ROOT", "PORT", "WEB_TOKEN", "CLI_TOKEN"))
    arguments = parser.parse_args()
    if arguments.serve_control:
        _serve_control(Path(arguments.serve_control[0]), int(arguments.serve_control[1]))
        return 0
    if arguments.serve_operator:
        _serve_operator(
            Path(arguments.serve_operator[0]), int(arguments.serve_operator[1]),
            Path(arguments.serve_operator[2]), Path(arguments.serve_operator[3]),
        )
        return 0
    if arguments.output is None:
        parser.error("--output is required")
    try:
        receipt = qualify()
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(canonical_json_bytes(receipt) + b"\n")
    except (HeadlessQualificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"HWC headless qualification: FAIL ({exc})", file=sys.stderr)
        return 1
    print(f"HWC headless qualification: PASS ({arguments.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
