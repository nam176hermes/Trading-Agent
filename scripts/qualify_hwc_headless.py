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
from scripts.t_g03_capability_topology import _publish_no_clobber


AUTHORITY = {"broker": False, "live": False, "network": False, "production": False}
RUNNER = ROOT / "tests/hwc/fixtures/headless_runtime_runner.py"
CLOSURE_MATRIX = ROOT / "docs/implementation/hwc/hwc-closure-matrix-v1.json"
DASHBOARD = ROOT / "apps/dashboard"
RECOVERY_FIXTURE = "tests.hwc.fixtures.operator_state"
RECOVERY_FAILPOINTS = (
    "AFTER_INTENT_FSYNC",
    "BEFORE_STATE_APPLY",
    "AFTER_STATE_APPLY",
    "AFTER_APPLIED_FSYNC",
    "BEFORE_RECEIPT_FSYNC",
    "AFTER_RECEIPT_FSYNC",
)
RECOVERY_SCENARIOS = ("paper", "activate", "clear")
RECOVERY_UNRESOLVED_FAILPOINTS = RECOVERY_FAILPOINTS[:3]
RECOVERY_CASE_COUNT = 124
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


def _start(
    command: list[str], *, cwd: Path, env: dict[str, str], log: Any
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _stop(process: subprocess.Popen[Any]) -> None:
    if _process_group_alive(process.pid):
        os.killpg(process.pid, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while _process_group_alive(process.pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _process_group_alive(process.pid):
        os.killpg(process.pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while _process_group_alive(process.pid) and time.monotonic() < deadline:
            time.sleep(0.02)


def _wait(
    url: str, process: subprocess.Popen[Any], *, headers: dict[str, str] | None = None
) -> None:
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
    request = urllib.request.Request(
        url, headers={"accept": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if (
                response.status != 200
                or not response.headers.get_content_type() == "application/json"
            ):
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
    return {"schema_version": "hwc-paper-batch-v1", "batch": name}


def _runtime_exchange(
    process: subprocess.Popen[bytes], payload: dict[str, str]
) -> dict[str, Any]:
    if process.poll() is not None or process.stdin is None or process.stdout is None:
        raise HeadlessQualificationError("paper runtime is unavailable")
    process.stdin.write(canonical_json_bytes(payload) + b"\n")
    process.stdin.flush()
    raw = process.stdout.readline()
    try:
        result = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HeadlessQualificationError("paper runtime result is invalid") from exc
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "hwc-paper-result-v2"
    ):
        raise HeadlessQualificationError("paper runtime rejected the batch")
    return result


def _runtime_ready(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    if process.stdout is None:
        raise HeadlessQualificationError("paper runtime output is unavailable")
    try:
        payload = json.loads(process.stdout.readline())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HeadlessQualificationError("paper runtime readiness is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "hwc-headless-runtime-ready-v1"
    ):
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


def _fixture_command(root: Path, scenario: str, *action: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        RECOVERY_FIXTURE,
        "--root",
        str(root),
        "--scenario",
        scenario,
        *action,
    ]


def _fixture_run(
    root: Path, environment: dict[str, str], scenario: str, *action: str
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        _fixture_command(root, scenario, *action),
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout) if completed.stdout else {}
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HeadlessQualificationError(
            "recovery campaign evidence is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise HeadlessQualificationError("recovery campaign evidence is invalid")
    return completed.returncode, payload


def _private_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    path.chmod(0o600)


def _conflicting_request(root: Path, scenario: str) -> Path:
    request = json.loads((root / "request.json").read_bytes())
    request["command"] = (
        {
            "command_type": "SET_KILL_SWITCH",
            "desired_state": "ACTIVE",
            "reason": "conflicting request",
        }
        if scenario != "activate"
        else {
            "command_type": "SET_KILL_SWITCH",
            "desired_state": "ACTIVE",
            "reason": "different activation",
        }
    )
    path = root / "conflicting-request.json"
    _private_json(path, request)
    return path


def _external_change(root: Path, scenario: str) -> None:
    target = (
        root / "operator-data" / (".mode" if scenario == "paper" else ".kill_switch")
    )
    target.write_bytes(
        b"live\n"
        if scenario == "paper"
        else b"2026-09-02T12:00:00Z: externally changed\n"
    )
    target.chmod(0o600)


def _communicate_json(process: subprocess.Popen[bytes]) -> tuple[int, dict[str, Any]]:
    stdout, _ = process.communicate(timeout=10)
    try:
        payload = json.loads(stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HeadlessQualificationError(
            "recovery campaign evidence is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise HeadlessQualificationError("recovery campaign evidence is invalid")
    return process.returncode, payload


def _run_concurrently(
    commands: list[list[str]], environment: dict[str, str]
) -> list[tuple[int, dict[str, Any]]]:
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for command in commands
    ]
    return [_communicate_json(process) for process in processes]


def _recovery_campaign_once(
    root: Path, environment: dict[str, str]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for scenario in RECOVERY_SCENARIOS:
        for failpoint in RECOVERY_FAILPOINTS:
            case = root / "matrix" / scenario / failpoint.lower()
            crashed, _ = _fixture_run(
                case, environment, scenario, "--crash-at", failpoint
            )
            conflicting_path = _conflicting_request(case, scenario)
            conflict_code, conflict = _fixture_run(
                case,
                environment,
                scenario,
                "--retry",
                "--request-file",
                str(conflicting_path),
            )
            recovered, result = _fixture_run(case, environment, scenario, "--retry")
            receipt = result.get("receipt")
            if not isinstance(receipt, dict):
                raise HeadlessQualificationError(
                    "recovery campaign evidence is invalid"
                )
            expected = (
                "RECOVERED_APPLIED" if failpoint == "AFTER_STATE_APPLY" else "APPLIED"
            )
            if (
                crashed != 77
                or (conflict_code, conflict)
                != (
                    3,
                    {"error": "IDEMPOTENCY_CONFLICT"},
                )
                or recovered != 0
                or result.get("deduplicated") is not True
                or receipt.get("outcome") != expected
            ):
                raise HeadlessQualificationError("recovery campaign failed")
            records.append(
                {
                    "case": "crash_retry",
                    "deduplicated": True,
                    "failpoint": failpoint,
                    "outcome": expected,
                    "receipt_sha256": receipt.get("receipt_sha256"),
                    "scenario": scenario,
                }
            )
            records.append(
                {
                    "case": "idempotency_conflict",
                    "error": conflict["error"],
                    "failpoint": failpoint,
                    "receipt_sha256": receipt["receipt_sha256"],
                    "scenario": scenario,
                    "winner_request_sha256": receipt["request_sha256"],
                }
            )
            replay_code, replay = _fixture_run(case, environment, scenario, "--retry")
            if replay_code or replay.get("receipt") != receipt:
                raise HeadlessQualificationError("recovery replay evidence failed")
            records.append(
                {
                    "case": "completed_replay",
                    "failpoint": failpoint,
                    "receipt_sha256": receipt["receipt_sha256"],
                    "scenario": scenario,
                }
            )
            key = receipt["idempotency_key_sha256"]
            receipt_path = (
                case / f"operator-data/.operator-commands/receipts/{key}.json"
            )
            receipt_path.write_bytes(b"{}\n")
            receipt_path.chmod(0o600)
            unsafe_code, unsafe = _fixture_run(case, environment, scenario, "--retry")
            if (unsafe_code, unsafe) != (
                3,
                {"error": "COMMAND_JOURNAL_UNSAFE"},
            ):
                raise HeadlessQualificationError("unsafe journal evidence failed")
            records.append(
                {
                    "case": "unsafe_journal",
                    "error": unsafe["error"],
                    "failpoint": failpoint,
                    "receipt_sha256": receipt["receipt_sha256"],
                    "scenario": scenario,
                }
            )

            same_root = root / "concurrent-same" / scenario / failpoint.lower()
            if (
                _fixture_run(same_root, environment, scenario, "--crash-at", failpoint)[
                    0
                ]
                != 77
            ):
                raise HeadlessQualificationError("concurrent same preparation failed")
            same_results = _run_concurrently(
                [_fixture_command(same_root, scenario, "--retry") for _ in range(2)],
                environment,
            )
            if (
                [code for code, _ in same_results] != [0, 0]
                or [body.get("deduplicated") for _, body in same_results]
                != [True, True]
                or same_results[0][1].get("receipt")
                != same_results[1][1].get("receipt")
            ):
                raise HeadlessQualificationError(
                    "concurrent same-request evidence failed"
                )
            same_receipt = same_results[0][1]["receipt"]
            records.append(
                {
                    "case": "concurrent_same",
                    "deduplicated": [True, True],
                    "failpoint": failpoint,
                    "receipt_sha256": same_receipt["receipt_sha256"],
                    "scenario": scenario,
                }
            )

            concurrent_root = (
                root / "concurrent-conflict" / scenario / failpoint.lower()
            )
            if (
                _fixture_run(
                    concurrent_root, environment, scenario, "--crash-at", failpoint
                )[0]
                != 77
            ):
                raise HeadlessQualificationError(
                    "concurrent conflict preparation failed"
                )
            concurrent_path = _conflicting_request(concurrent_root, scenario)
            conflicting_results = _run_concurrently(
                [
                    _fixture_command(concurrent_root, scenario, "--retry", *extra)
                    for extra in ((), ("--request-file", str(concurrent_path)))
                ],
                environment,
            )
            if sorted(code for code, _ in conflicting_results) != [0, 3]:
                raise HeadlessQualificationError("concurrent conflict evidence failed")
            success = next(body for code, body in conflicting_results if code == 0)
            failure = next(body for code, body in conflicting_results if code == 3)
            success_receipt = success.get("receipt")
            if failure != {"error": "IDEMPOTENCY_CONFLICT"} or not isinstance(
                success_receipt, dict
            ):
                raise HeadlessQualificationError("concurrent conflict evidence failed")
            records.append(
                {
                    "case": "concurrent_conflict",
                    "error": failure["error"],
                    "failpoint": failpoint,
                    "receipt_sha256": success_receipt["receipt_sha256"],
                    "scenario": scenario,
                    "winner_request_sha256": success_receipt["request_sha256"],
                }
            )

            if failpoint in RECOVERY_UNRESOLVED_FAILPOINTS:
                changed_root = root / "external-change" / scenario / failpoint.lower()
                if (
                    _fixture_run(
                        changed_root, environment, scenario, "--crash-at", failpoint
                    )[0]
                    != 77
                ):
                    raise HeadlessQualificationError(
                        "external state-change preparation failed"
                    )
                _external_change(changed_root, scenario)
                changed_code, changed = _fixture_run(
                    changed_root, environment, scenario, "--retry"
                )
                if (changed_code, changed) != (
                    3,
                    {"error": "COMMAND_OUTCOME_UNKNOWN"},
                ):
                    raise HeadlessQualificationError(
                        "external state-change evidence failed"
                    )
                records.append(
                    {
                        "case": "external_state_change",
                        "error": changed["error"],
                        "failpoint": failpoint,
                        "scenario": scenario,
                    }
                )

            if scenario == "clear":
                stale_root = root / "stale-state" / failpoint.lower()
                if (
                    _fixture_run(
                        stale_root, environment, scenario, "--crash-at", failpoint
                    )[0]
                    != 77
                ):
                    raise HeadlessQualificationError("stale state preparation failed")
                stale = json.loads((stale_root / "request.json").read_bytes())
                stale["command_id"] = (
                    f"cmd_{hashlib.sha256(failpoint.encode()).hexdigest()[:32]}"
                )
                stale["idempotency_key"] = f"stale.clear.{failpoint.lower()}"
                stale["correlation_id"] = f"stale.clear.{failpoint.lower()}"
                stale["expected_state_sha256"] = "f" * 64
                stale_path = stale_root / "stale-request.json"
                _private_json(stale_path, stale)
                stale_code, stale_result = _fixture_run(
                    stale_root,
                    environment,
                    scenario,
                    "--retry",
                    "--request-file",
                    str(stale_path),
                )
                if (stale_code, stale_result) != (
                    3,
                    {"error": "EXPECTED_STATE_CONFLICT"},
                ):
                    raise HeadlessQualificationError("stale state evidence failed")
                records.append(
                    {
                        "case": "stale_expected_state",
                        "error": stale_result["error"],
                        "failpoint": failpoint,
                        "scenario": scenario,
                    }
                )

    reactivated_root = root / "clear-reactivated"
    if (
        _fixture_run(
            reactivated_root,
            environment,
            "clear",
            "--crash-at",
            "AFTER_STATE_APPLY",
        )[0]
        != 77
    ):
        raise HeadlessQualificationError("clear reactivation preparation failed")
    _external_change(reactivated_root, "clear")
    reactivated_code, reactivated = _fixture_run(
        reactivated_root, environment, "clear", "--retry"
    )
    if (reactivated_code, reactivated) != (
        3,
        {"error": "COMMAND_OUTCOME_UNKNOWN"},
    ):
        raise HeadlessQualificationError("clear reactivation evidence failed")
    records.append(
        {
            "case": "clear_reactivated",
            "error": reactivated["error"],
            "failpoint": "AFTER_STATE_APPLY",
            "scenario": "clear",
        }
    )
    if len(records) != RECOVERY_CASE_COUNT:
        raise HeadlessQualificationError("recovery campaign matrix is incomplete")
    return records


def _recovery_campaign(root: Path, environment: dict[str, str]) -> dict[str, Any]:
    campaigns = [
        _recovery_campaign_once(root / str(index), environment) for index in range(3)
    ]
    campaign_digests = [_sha(records) for records in campaigns]
    if len(set(campaign_digests)) != 1:
        raise HeadlessQualificationError("recovery campaign is nondeterministic")
    return {
        "schema_version": "hwc-command-recovery-campaign-v1",
        "case_count": len(campaigns[0]),
        "campaign_sha256": campaign_digests[0],
        "repetitions": len(campaigns),
        "records": campaigns[0],
    }


def _dashboard_command(port: int) -> list[str]:
    return [
        str(DASHBOARD / "node_modules/.bin/next"),
        "start",
        "-H",
        "127.0.0.1",
        "-p",
        str(port),
    ]


def _valid_recovery_records(records: list[object]) -> bool:
    if len(records) != RECOVERY_CASE_COUNT or any(
        not isinstance(record, dict) for record in records
    ):
        return False
    typed_records = [record for record in records if isinstance(record, dict)]
    expected_full = {
        (scenario, failpoint)
        for scenario in RECOVERY_SCENARIOS
        for failpoint in RECOVERY_FAILPOINTS
    }
    expected_unresolved = {
        (scenario, failpoint)
        for scenario in RECOVERY_SCENARIOS
        for failpoint in RECOVERY_UNRESOLVED_FAILPOINTS
    }
    names = {
        "clear_reactivated",
        "completed_replay",
        "concurrent_conflict",
        "concurrent_same",
        "crash_retry",
        "external_state_change",
        "idempotency_conflict",
        "stale_expected_state",
        "unsafe_journal",
    }
    grouped = {
        name: [record for record in typed_records if record.get("case") == name]
        for name in names
    }
    if {str(record.get("case")) for record in typed_records} != names or sum(
        len(group) for group in grouped.values()
    ) != len(records):
        return False

    def tuples(name: str) -> set[tuple[object, object]]:
        return {
            (record.get("scenario"), record.get("failpoint"))
            for record in grouped[name]
        }

    if any(
        len(grouped[name]) != len(expected_full) or tuples(name) != expected_full
        for name in (
            "crash_retry",
            "completed_replay",
            "concurrent_conflict",
            "concurrent_same",
            "idempotency_conflict",
            "unsafe_journal",
        )
    ):
        return False
    if (
        len(grouped["external_state_change"]) != len(expected_unresolved)
        or tuples("external_state_change") != expected_unresolved
        or len(grouped["stale_expected_state"]) != len(RECOVERY_FAILPOINTS)
        or tuples("stale_expected_state")
        != {("clear", failpoint) for failpoint in RECOVERY_FAILPOINTS}
    ):
        return False

    crash_keys = {
        "case",
        "deduplicated",
        "failpoint",
        "outcome",
        "receipt_sha256",
        "scenario",
    }
    if any(
        set(record) != crash_keys
        or record.get("deduplicated") is not True
        or record.get("outcome")
        != (
            "RECOVERED_APPLIED"
            if record.get("failpoint") == "AFTER_STATE_APPLY"
            else "APPLIED"
        )
        or not _is_sha256(record.get("receipt_sha256"))
        for record in grouped["crash_retry"]
    ):
        return False
    if any(
        set(record) != {"case", "failpoint", "receipt_sha256", "scenario"}
        or not _is_sha256(record.get("receipt_sha256"))
        for record in grouped["completed_replay"]
    ):
        return False
    if any(
        set(record)
        != {
            "case",
            "error",
            "failpoint",
            "receipt_sha256",
            "scenario",
            "winner_request_sha256",
        }
        or record.get("error") != "IDEMPOTENCY_CONFLICT"
        or not _is_sha256(record.get("receipt_sha256"))
        or not _is_sha256(record.get("winner_request_sha256"))
        for name in ("idempotency_conflict", "concurrent_conflict")
        for record in grouped[name]
    ):
        return False
    if any(
        set(record)
        != {"case", "deduplicated", "failpoint", "receipt_sha256", "scenario"}
        or record.get("deduplicated") != [True, True]
        or not _is_sha256(record.get("receipt_sha256"))
        for record in grouped["concurrent_same"]
    ):
        return False
    if any(
        set(record) != {"case", "error", "failpoint", "receipt_sha256", "scenario"}
        or record.get("error") != "COMMAND_JOURNAL_UNSAFE"
        or not _is_sha256(record.get("receipt_sha256"))
        for record in grouped["unsafe_journal"]
    ):
        return False
    for name, error in (
        ("external_state_change", "COMMAND_OUTCOME_UNKNOWN"),
        ("stale_expected_state", "EXPECTED_STATE_CONFLICT"),
    ):
        if any(
            set(record) != {"case", "error", "failpoint", "scenario"}
            or record.get("error") != error
            for record in grouped[name]
        ):
            return False
    return grouped["clear_reactivated"] == [
        {
            "case": "clear_reactivated",
            "error": "COMMAND_OUTCOME_UNKNOWN",
            "failpoint": "AFTER_STATE_APPLY",
            "scenario": "clear",
        }
    ]


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - _HEX


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["receipt_sha256"] = _sha(payload)
    return validate_receipt(payload)


def validate_receipt(payload: object, *, root: Path = ROOT) -> dict[str, Any]:
    required = {
        "schema_version",
        "source",
        "evidence",
        "processes",
        "observations",
        "cleanup",
        "authority",
        "verdict",
        "receipt_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise HeadlessQualificationError("headless receipt field set is invalid")
    source = payload.get("source")
    evidence = payload.get("evidence")
    processes = payload.get("processes")
    observations = payload.get("observations")
    cleanup = payload.get("cleanup")
    source_keys = {
        "commit_sha",
        "tree_sha",
        "closure_sha256",
        "closure_schema_version",
        "closure_policy_sha256",
    }
    evidence_keys = {
        "closure_matrix_sha256",
        "runtime_fixture_sha256",
        "dashboard_build_sha256",
        "recovery_campaign",
        "recovery_campaign_sha256",
        "batch_a",
        "batch_b",
        "dashboard_before",
        "dashboard_before_sha256",
        "dashboard_after",
        "dashboard_after_sha256",
        "operator_before",
        "operator_before_sha256",
        "operator_during",
        "operator_during_sha256",
        "operator_after",
        "operator_after_sha256",
    }
    process_keys = {
        "runtime_pid",
        "runtime_session_id",
        "control_api_pid",
        "operator_api_pid",
        "dashboard_initial_pid",
        "dashboard_restart_pid",
    }
    observation_keys = {
        "runtime_alive_after_dashboard_kill",
        "control_api_alive_after_dashboard_kill",
        "operator_api_alive_after_dashboard_kill",
        "batch_b_after_dashboard_kill",
        "operator_state_unchanged",
        "dashboard_reconstructed",
        "dashboard_build_present",
    }
    if (
        payload.get("schema_version") != "hwc-headless-portable-evidence-v1"
        or payload.get("verdict") != "PASS"
        or payload.get("authority") != AUTHORITY
        or not isinstance(source, dict)
        or set(source) != source_keys
        or source.get("closure_schema_version") != SOURCE_CLOSURE_SCHEMA
        or source.get("closure_policy_sha256") != SOURCE_CLOSURE_POLICY_SHA256
        or not isinstance(evidence, dict)
        or set(evidence) != evidence_keys
        or not isinstance(processes, dict)
        or set(processes) != process_keys
        or not isinstance(observations, dict)
        or set(observations) != observation_keys
        or set(observations.values()) != {True}
        or not isinstance(cleanup, dict)
        or set(cleanup) != {"process_groups_stopped", "complete"}
        or cleanup != {"process_groups_stopped": 5, "complete": True}
    ):
        raise HeadlessQualificationError("headless receipt evidence is invalid")
    try:
        actual_source = canonical_source_identity(root)
    except (OSError, ValueError) as exc:
        raise HeadlessQualificationError(
            "headless receipt source is unavailable"
        ) from exc
    if source != actual_source:
        raise HeadlessQualificationError("headless receipt source is stale")

    pids = [value for key, value in processes.items() if key.endswith("_pid")]
    try:
        runtime_session_id = str(
            __import__("uuid").UUID(processes["runtime_session_id"])
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise HeadlessQualificationError(
            "headless runtime identity is invalid"
        ) from exc
    if (
        any(type(value) is not int or value <= 0 for value in pids)
        or len(set(pids)) != len(pids)
        or runtime_session_id != processes["runtime_session_id"]
    ):
        raise HeadlessQualificationError("headless process identity is invalid")

    expected_fixture_sha = _sha(
        {
            "fixture": _file_sha(root / "tests/fixtures/paper_runtime.py"),
            "runner": _file_sha(root / "tests/hwc/fixtures/headless_runtime_runner.py"),
        }
    )
    if (
        evidence.get("closure_matrix_sha256")
        != _file_sha(root / "docs/implementation/hwc/hwc-closure-matrix-v1.json")
        or evidence.get("runtime_fixture_sha256") != expected_fixture_sha
        or evidence.get("dashboard_build_sha256")
        != _tree_sha(root / "apps/dashboard/.next")
        or observations.get("dashboard_build_present")
        != (root / "apps/dashboard/.next/BUILD_ID").is_file()
    ):
        raise HeadlessQualificationError("headless file evidence is stale")

    recovery = evidence.get("recovery_campaign")
    required_cases = {
        "clear_reactivated",
        "completed_replay",
        "concurrent_conflict",
        "concurrent_same",
        "crash_retry",
        "external_state_change",
        "idempotency_conflict",
        "stale_expected_state",
        "unsafe_journal",
    }
    if (
        not isinstance(recovery, dict)
        or set(recovery)
        != {"schema_version", "case_count", "campaign_sha256", "repetitions", "records"}
        or recovery.get("schema_version") != "hwc-command-recovery-campaign-v1"
        or recovery.get("case_count") != RECOVERY_CASE_COUNT
        or recovery.get("repetitions") != 3
        or not isinstance(recovery.get("records"), list)
        or len(recovery["records"]) != recovery["case_count"]
        or not _valid_recovery_records(recovery["records"])
        or {
            record.get("case")
            for record in recovery["records"]
            if isinstance(record, dict)
        }
        != required_cases
        or recovery.get("campaign_sha256") != _sha(recovery["records"])
        or evidence.get("recovery_campaign_sha256") != _sha(recovery)
    ):
        raise HeadlessQualificationError("headless recovery evidence is invalid")

    batches: dict[str, dict[str, Any]] = {}
    for key, name, sequence, state in (
        ("batch_a", "A", 1, "RUNNING"),
        ("batch_b", "B", 2, "STOPPED"),
    ):
        batch = evidence.get(key)
        if not isinstance(batch, dict) or set(batch) != {
            "input",
            "input_sha256",
            "output",
            "output_sha256",
            "event_sha256",
            "result_sha256",
            "checkpoint_sha256",
        }:
            raise HeadlessQualificationError("headless batch evidence is invalid")
        request = batch["input"]
        result = batch["output"]
        if (
            request != _batch(name)
            or not isinstance(result, dict)
            or result.get("schema_version") != "hwc-paper-result-v2"
            or result.get("batch") != name
            or result.get("sequence") != sequence
            or result.get("state") != state
            or result.get("session_id") != runtime_session_id
            or result.get("input_sha256") != _sha(request)
            or result.get("result_sha256")
            != _sha(
                {
                    item: value
                    for item, value in result.items()
                    if item != "result_sha256"
                }
            )
            or batch["input_sha256"] != _sha(request)
            or batch["output_sha256"] != _sha(result)
            or batch["event_sha256"] != result.get("event_sha256")
            or batch["result_sha256"] != result.get("result_sha256")
            or batch["checkpoint_sha256"] != result.get("checkpoint_sha256")
        ):
            raise HeadlessQualificationError("headless batch evidence is invalid")
        batches[key] = result
    if (
        batches["batch_a"].get("event_batch_sha256") is not None
        or batches["batch_a"].get("parity_receipt_sha256") is not None
        or not isinstance(batches["batch_b"].get("event_batch_sha256"), str)
        or not isinstance(batches["batch_b"].get("parity_receipt_sha256"), str)
        or batches["batch_a"]["event_sha256"] == batches["batch_b"]["event_sha256"]
        or batches["batch_a"]["checkpoint_sha256"]
        == batches["batch_b"]["checkpoint_sha256"]
        or any(
            not _is_sha256(result.get(field))
            for result in batches.values()
            for field in (
                "input_sha256",
                "event_sha256",
                "checkpoint_sha256",
                "result_sha256",
            )
        )
        or any(
            not _is_sha256(batches["batch_b"].get(field))
            for field in ("event_batch_sha256", "parity_receipt_sha256")
        )
    ):
        raise HeadlessQualificationError("headless Nautilus result evidence is invalid")

    if (
        evidence.get("dashboard_before") != evidence.get("dashboard_after")
        or evidence.get("dashboard_before_sha256")
        != _sha(evidence.get("dashboard_before"))
        or evidence.get("dashboard_after_sha256")
        != _sha(evidence.get("dashboard_after"))
        or not (
            evidence.get("operator_before")
            == evidence.get("operator_during")
            == evidence.get("operator_after")
        )
        or any(
            evidence.get(f"{name}_sha256") != _sha(evidence.get(name))
            for name in ("operator_before", "operator_during", "operator_after")
        )
    ):
        raise HeadlessQualificationError("headless state evidence changed")
    if (
        not observations.get("batch_b_after_dashboard_kill")
        or batches["batch_b"]["sequence"] != 2
    ):
        raise HeadlessQualificationError("headless continuation evidence is invalid")
    digest_values = [
        source.get("closure_sha256"),
        source.get("closure_policy_sha256"),
        evidence.get("closure_matrix_sha256"),
        evidence.get("runtime_fixture_sha256"),
        evidence.get("dashboard_build_sha256"),
        evidence.get("recovery_campaign_sha256"),
    ]
    if any(
        not isinstance(value, str) or len(value) != 64 or set(value) - _HEX
        for value in digest_values
    ):
        raise HeadlessQualificationError("headless receipt digest is invalid")
    if payload.get("receipt_sha256") != _sha(
        {key: value for key, value in payload.items() if key != "receipt_sha256"}
    ):
        raise HeadlessQualificationError("headless receipt seal is invalid")
    return payload


def qualify(
    *,
    require_clean: bool = True,
    build_dashboard: bool = True,
    _fault: str | None = None,
) -> dict[str, Any]:
    if _fault not in {
        None,
        "dashboard_startup",
        "port_collision",
        "runtime_exit",
        "partial_evidence",
        "cleanup_failure",
    }:
        raise ValueError("unknown test fault")
    if (
        require_clean
        and subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
    ):
        raise HeadlessQualificationError(
            "qualification requires a clean committed source"
        )
    environment = dict(os.environ)
    environment.update(
        {
            "TMPDIR": "/tmp",
            "TMP": "/tmp",
            "TEMP": "/tmp",
            "NEXT_TELEMETRY_DISABLED": "1",
        }
    )
    processes: list[subprocess.Popen[Any]] = []
    with (
        tempfile.TemporaryDirectory(prefix="hwc-headless-", dir="/tmp") as temporary,
        ExitStack() as stack,
    ):
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
            [
                sys.executable,
                str(__file__),
                "--serve-control",
                str(data),
                str(control_port),
            ],
            cwd=ROOT,
            env=environment,
            log=log,
        )
        operator = _start(
            [
                sys.executable,
                str(__file__),
                "--serve-operator",
                str(data),
                str(operator_port),
                str(token_root / "web.token"),
                str(token_root / "cli.token"),
            ],
            cwd=ROOT,
            env=environment,
            log=log,
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
                [
                    sys.executable,
                    str(__file__),
                    "--serve-control",
                    str(data),
                    str(control_port),
                ],
                cwd=ROOT,
                env=environment,
                log=log,
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
            "TRADING_OPERATOR_API_URL": f"http://127.0.0.1:{operator_port}",
            "TRADING_OPERATOR_API_WEB_TOKEN_FILE": str(token_root / "web.token"),
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        }
        (work / "home").mkdir(mode=0o700)
        operator_before = _json(
            f"http://127.0.0.1:{operator_port}/v1/state",
            headers={"authorization": f"Bearer {cli_token}"},
        )["data"]["state"]
        dashboard_command = (
            [sys.executable, "-c", "raise SystemExit(7)"]
            if _fault == "dashboard_startup"
            else _dashboard_command(dashboard_port)
        )
        dashboard = _start(
            dashboard_command, cwd=DASHBOARD, env=dashboard_environment, log=log
        )
        processes.append(dashboard)
        stack.callback(_stop, dashboard)
        _wait(f"http://127.0.0.1:{dashboard_port}/api/auth/session", dashboard)
        batch_a_request = _batch("A")
        batch_a = _runtime_exchange(runtime, batch_a_request)
        before_view = _dashboard_view(dashboard_port)
        operator_during = _json(
            f"http://127.0.0.1:{operator_port}/v1/state",
            headers={"authorization": f"Bearer {cli_token}"},
        )["data"]["state"]
        os.killpg(dashboard.pid, signal.SIGKILL)
        dashboard.wait(timeout=5)
        alive = (
            runtime.poll() is None,
            control.poll() is None,
            operator.poll() is None,
        )
        batch_b_request = _batch("B")
        batch_b = _runtime_exchange(runtime, batch_b_request)
        dashboard_restart = _start(
            _dashboard_command(dashboard_port),
            cwd=DASHBOARD,
            env=dashboard_environment,
            log=log,
        )
        processes.append(dashboard_restart)
        stack.callback(_stop, dashboard_restart)
        _wait(f"http://127.0.0.1:{dashboard_port}/api/auth/session", dashboard_restart)
        after_view = _dashboard_view(dashboard_port)
        operator_after = _json(
            f"http://127.0.0.1:{operator_port}/v1/state",
            headers={"authorization": f"Bearer {cli_token}"},
        )["data"]["state"]
        for process in reversed(processes):
            _stop(process)
        cleanup_complete = all(
            process.poll() is not None and not _process_group_alive(process.pid)
            for process in processes
        )
        fixture_digest = _sha(
            {
                "fixture": _file_sha(ROOT / "tests/fixtures/paper_runtime.py"),
                "runner": _file_sha(RUNNER),
            }
        )
        recovery = _recovery_campaign(work / "recovery", environment)

        def batch_evidence(
            request: dict[str, str], result: dict[str, Any]
        ) -> dict[str, Any]:
            return {
                "input": request,
                "input_sha256": _sha(request),
                "output": result,
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
                "recovery_campaign": recovery,
                "recovery_campaign_sha256": _sha(recovery),
                "batch_a": batch_evidence(batch_a_request, batch_a),
                "batch_b": batch_evidence(batch_b_request, batch_b),
                "dashboard_before": before_view,
                "dashboard_before_sha256": _sha(before_view),
                "dashboard_after": after_view,
                "dashboard_after_sha256": _sha(after_view),
                "operator_before": operator_before,
                "operator_before_sha256": _sha(operator_before),
                "operator_during": operator_during,
                "operator_during_sha256": _sha(operator_during),
                "operator_after": operator_after,
                "operator_after_sha256": _sha(operator_after),
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
                "operator_state_unchanged": operator_before
                == operator_during
                == operator_after,
                "dashboard_reconstructed": before_view == after_view,
                "dashboard_build_present": (DASHBOARD / ".next/BUILD_ID").is_file(),
            },
            "cleanup": {
                "process_groups_stopped": len(processes),
                "complete": cleanup_complete,
            },
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


def _serve_operator(
    data_root: Path, port: int, web_token: Path, cli_token: Path
) -> None:
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

    settings = OperatorApiSettings(
        web_token, "dashboard.reader", cli_token, "operator.cli"
    )
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
    parser.add_argument(
        "--serve-operator",
        nargs=4,
        metavar=("DATA_ROOT", "PORT", "WEB_TOKEN", "CLI_TOKEN"),
    )
    arguments = parser.parse_args()
    if arguments.serve_control:
        _serve_control(
            Path(arguments.serve_control[0]), int(arguments.serve_control[1])
        )
        return 0
    if arguments.serve_operator:
        _serve_operator(
            Path(arguments.serve_operator[0]),
            int(arguments.serve_operator[1]),
            Path(arguments.serve_operator[2]),
            Path(arguments.serve_operator[3]),
        )
        return 0
    if arguments.output is None:
        parser.error("--output is required")
    try:
        receipt = qualify()
        _publish_no_clobber(arguments.output, canonical_json_bytes(receipt) + b"\n")
    except (HeadlessQualificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"HWC headless qualification: FAIL ({exc})", file=sys.stderr)
        return 1
    print(f"HWC headless qualification: PASS ({arguments.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
