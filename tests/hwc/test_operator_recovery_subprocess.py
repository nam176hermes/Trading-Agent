from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests.hwc.fixtures.operator_state"
FAILPOINTS = (
    "AFTER_INTENT_FSYNC",
    "BEFORE_STATE_APPLY",
    "AFTER_STATE_APPLY",
    "AFTER_APPLIED_FSYNC",
    "BEFORE_RECEIPT_FSYNC",
    "AFTER_RECEIPT_FSYNC",
)


def _run(root: Path, scenario: str, *action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            FIXTURE,
            "--root",
            str(root),
            "--scenario",
            scenario,
            *action,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _popen(root: Path, scenario: str, *action: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            FIXTURE,
            "--root",
            str(root),
            "--scenario",
            scenario,
            *action,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _private_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)


def test_qualifier_starts_all_concurrent_workers_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import qualify_hwc_headless as qualifier

    started: list[object] = []

    class Process:
        returncode = 0

        def __init__(self, *_args, **_kwargs) -> None:
            started.append(self)

        def communicate(self, timeout: int) -> tuple[bytes, bytes]:
            assert timeout == 10
            assert len(started) == 2
            return b"{}", b""

    monkeypatch.setattr(qualifier.subprocess, "Popen", Process)
    assert qualifier._run_concurrently([["first"], ["second"]], {}) == [
        (0, {}),
        (0, {}),
    ]


def _campaign(root: Path, scenario: str, failpoint: str) -> dict[str, object]:
    crashed = _run(root, scenario, "--crash-at", failpoint)
    assert crashed.returncode == 77, crashed.stderr
    recovered = _run(root, scenario, "--retry")
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["deduplicated"] is True
    assert result["receipt"]["outcome"] == (
        "RECOVERED_APPLIED" if failpoint == "AFTER_STATE_APPLY" else "APPLIED"
    )
    return result


@pytest.mark.parametrize("scenario", ("paper", "activate", "clear"))
@pytest.mark.parametrize("failpoint", FAILPOINTS)
def test_every_command_recovers_deterministically_at_every_failpoint(
    tmp_path: Path, scenario: str, failpoint: str
) -> None:
    _campaign(tmp_path, scenario, failpoint)


@pytest.mark.parametrize("scenario", ("paper", "activate", "clear"))
def test_completed_recovery_replays_the_exact_receipt(
    tmp_path: Path, scenario: str
) -> None:
    result = _campaign(tmp_path, scenario, "AFTER_STATE_APPLY")
    replayed = _run(tmp_path, scenario, "--retry")
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["receipt"] == result["receipt"]


def test_recovery_campaign_digest_is_stable(tmp_path: Path) -> None:
    from scripts.qualify_hwc_headless import _recovery_campaign

    campaign = _recovery_campaign(tmp_path, dict(os.environ))
    assert campaign["case_count"] == 124
    assert campaign["repetitions"] == 3
    digest = campaign["campaign_sha256"]
    assert len(digest) == 64 and not set(digest) - set("0123456789abcdef")
    assert {record["case"] for record in campaign["records"]} == {
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
    full_matrix = {
        (scenario, failpoint)
        for scenario in ("paper", "activate", "clear")
        for failpoint in FAILPOINTS
    }
    for case in (
        "crash_retry",
        "completed_replay",
        "concurrent_conflict",
        "concurrent_same",
        "idempotency_conflict",
        "unsafe_journal",
    ):
        assert {
            (record["scenario"], record["failpoint"])
            for record in campaign["records"]
            if record["case"] == case
        } == full_matrix
    assert {
        (record["scenario"], record["failpoint"])
        for record in campaign["records"]
        if record["case"] == "external_state_change"
    } == {
        (scenario, failpoint)
        for scenario in ("paper", "activate", "clear")
        for failpoint in FAILPOINTS[:3]
    }
    assert {
        record["failpoint"]
        for record in campaign["records"]
        if record["case"] == "stale_expected_state"
    } == set(FAILPOINTS)
    conflict = next(
        record
        for record in campaign["records"]
        if record["case"] == "concurrent_conflict"
    )
    assert len(conflict["winner_request_sha256"]) == 64
    assert len(conflict["receipt_sha256"]) == 64


def test_concurrent_same_and_conflicting_requests_are_serialized(
    tmp_path: Path,
) -> None:
    same_root = tmp_path / "same"
    assert _run(same_root, "paper", "--prepare").returncode == 0
    same = [_popen(same_root, "paper", "--retry") for _ in range(2)]
    same_results = []
    for process in same:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        same_results.append(json.loads(stdout))
    assert sorted(result["deduplicated"] for result in same_results) == [False, True]
    assert same_results[0]["receipt"] == same_results[1]["receipt"]

    conflict_root = tmp_path / "conflict"
    assert _run(conflict_root, "paper", "--prepare").returncode == 0
    alternate = json.loads((conflict_root / "request.json").read_bytes())
    alternate["command"] = {
        "command_type": "SET_KILL_SWITCH",
        "desired_state": "ACTIVE",
        "reason": "conflicting request",
    }
    alternate_path = conflict_root / "alternate.json"
    _private_json(alternate_path, alternate)
    conflicting = [
        _popen(conflict_root, "paper", "--retry"),
        _popen(
            conflict_root, "paper", "--retry", "--request-file", str(alternate_path)
        ),
    ]
    outcomes = []
    for process in conflicting:
        stdout, _ = process.communicate(timeout=10)
        outcomes.append((process.returncode, json.loads(stdout)))
    assert sorted(code for code, _ in outcomes) == [0, 3]
    assert [body for code, body in outcomes if code == 3] == [
        {"error": "IDEMPOTENCY_CONFLICT"}
    ]


def test_stale_expected_state_and_ambiguous_external_changes_never_succeed(
    tmp_path: Path,
) -> None:
    stale_root = tmp_path / "stale"
    assert _run(stale_root, "clear", "--prepare").returncode == 0
    stale = json.loads((stale_root / "request.json").read_bytes())
    stale["expected_state_sha256"] = "f" * 64
    stale_path = stale_root / "stale.json"
    _private_json(stale_path, stale)
    rejected = _run(stale_root, "clear", "--retry", "--request-file", str(stale_path))
    assert (rejected.returncode, json.loads(rejected.stdout)) == (
        3,
        {"error": "EXPECTED_STATE_CONFLICT"},
    )

    changed_root = tmp_path / "changed"
    assert (
        _run(changed_root, "paper", "--crash-at", "AFTER_INTENT_FSYNC").returncode == 77
    )
    mode = changed_root / "operator-data/.mode"
    mode.write_bytes(b"live\n")
    mode.chmod(0o600)
    unknown = _run(changed_root, "paper", "--retry")
    assert (unknown.returncode, json.loads(unknown.stdout)) == (
        3,
        {"error": "COMMAND_OUTCOME_UNKNOWN"},
    )


def test_clear_tombstone_reactivation_and_unsafe_journal_are_explicit(
    tmp_path: Path,
) -> None:
    clear_root = tmp_path / "reactivated"
    assert _run(clear_root, "clear", "--crash-at", "AFTER_STATE_APPLY").returncode == 77
    kill_switch = clear_root / "operator-data/.kill_switch"
    kill_switch.write_bytes(b"2026-09-02T12:00:00Z: incident\n")
    kill_switch.chmod(0o600)
    unknown = _run(clear_root, "clear", "--retry")
    assert (unknown.returncode, json.loads(unknown.stdout)) == (
        3,
        {"error": "COMMAND_OUTCOME_UNKNOWN"},
    )

    unsafe_root = tmp_path / "unsafe"
    result = _campaign(unsafe_root, "paper", "AFTER_RECEIPT_FSYNC")
    key = hashlib.sha256(b"idem.1").hexdigest()
    receipt = unsafe_root / f"operator-data/.operator-commands/receipts/{key}.json"
    receipt.write_bytes(b"{}\n")
    receipt.chmod(0o600)
    unsafe = _run(unsafe_root, "paper", "--retry")
    assert result["receipt"]["outcome"] == "APPLIED"
    assert (unsafe.returncode, json.loads(unsafe.stdout)) == (
        3,
        {"error": "COMMAND_JOURNAL_UNSAFE"},
    )
