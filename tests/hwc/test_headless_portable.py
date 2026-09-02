from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from copy import deepcopy

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests/hwc/fixtures/headless_runtime_runner.py"


def _recovery_report() -> dict[str, object]:
    from scripts.qualify_hwc_headless import (
        RECOVERY_CASE_COUNT,
        RECOVERY_FAILPOINTS,
        RECOVERY_SCENARIOS,
        RECOVERY_UNRESOLVED_FAILPOINTS,
        _sha,
    )

    records = []
    for scenario in RECOVERY_SCENARIOS:
        for failpoint in RECOVERY_FAILPOINTS:
            common = {"failpoint": failpoint, "scenario": scenario}
            records.extend(
                [
                    {
                        "case": "crash_retry",
                        "deduplicated": True,
                        "outcome": "RECOVERED_APPLIED"
                        if failpoint == "AFTER_STATE_APPLY"
                        else "APPLIED",
                        "receipt_sha256": "a" * 64,
                        **common,
                    },
                    {
                        "case": "idempotency_conflict",
                        "error": "IDEMPOTENCY_CONFLICT",
                        "receipt_sha256": "b" * 64,
                        "winner_request_sha256": "c" * 64,
                        **common,
                    },
                    {
                        "case": "completed_replay",
                        "receipt_sha256": "d" * 64,
                        **common,
                    },
                    {
                        "case": "unsafe_journal",
                        "error": "COMMAND_JOURNAL_UNSAFE",
                        "receipt_sha256": "e" * 64,
                        **common,
                    },
                    {
                        "case": "concurrent_same",
                        "deduplicated": [True, True],
                        "receipt_sha256": "f" * 64,
                        **common,
                    },
                    {
                        "case": "concurrent_conflict",
                        "error": "IDEMPOTENCY_CONFLICT",
                        "receipt_sha256": "1" * 64,
                        "winner_request_sha256": "2" * 64,
                        **common,
                    },
                ]
            )
            if failpoint in RECOVERY_UNRESOLVED_FAILPOINTS:
                records.append(
                    {
                        "case": "external_state_change",
                        "error": "COMMAND_OUTCOME_UNKNOWN",
                        **common,
                    }
                )
            if scenario == "clear":
                records.append(
                    {
                        "case": "stale_expected_state",
                        "error": "EXPECTED_STATE_CONFLICT",
                        **common,
                    }
                )
    records.append(
        {
            "case": "clear_reactivated",
            "error": "COMMAND_OUTCOME_UNKNOWN",
            "failpoint": "AFTER_STATE_APPLY",
            "scenario": "clear",
        }
    )
    assert len(records) == RECOVERY_CASE_COUNT
    return {
        "schema_version": "hwc-command-recovery-campaign-v1",
        "case_count": RECOVERY_CASE_COUNT,
        "campaign_sha256": _sha(records),
        "repetitions": 3,
        "records": records,
    }


def _batch(name: str) -> dict[str, str]:
    return {"schema_version": "hwc-paper-batch-v1", "batch": name}


def _line(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    raw = process.stdout.readline()
    assert raw, process.stderr.read() if process.stderr is not None else ""
    return json.loads(raw)


def _send(process: subprocess.Popen[str], payload: dict[str, str]) -> dict[str, object]:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()
    return _line(process)


def test_runtime_runner_advances_deterministically_without_dashboard(
    tmp_path: Path,
) -> None:
    process = subprocess.Popen(
        [sys.executable, str(RUNNER), "--root", str(tmp_path / "runtime")],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = _line(process)
        first = _send(process, _batch("A"))
        second = _send(process, _batch("B"))
        assert process.poll() is None
        assert ready["schema_version"] == "hwc-headless-runtime-ready-v1"
        assert first["sequence"] == 1
        assert second["sequence"] == 2
        assert first["state"] == "RUNNING"
        assert second["state"] == "STOPPED"
        assert second["event_batch_sha256"] is not None
        assert second["parity_receipt_sha256"] is not None
        assert (
            first["input_sha256"]
            == hashlib.sha256(canonical_json_bytes(_batch("A"))).hexdigest()
        )
        assert (tmp_path / "runtime/checkpoint.json").is_file()
        assert (tmp_path / "runtime/results/batch-a.json").is_file()
        assert (tmp_path / "runtime/results/batch-b.json").is_file()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


def test_cleanup_stops_children_after_the_process_group_leader_exits() -> None:
    from scripts import qualify_hwc_headless as qualifier

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess; subprocess.Popen(['sleep', '30']);",
        ],
        start_new_session=True,
    )
    process.wait(timeout=5)
    assert qualifier._process_group_alive(process.pid)

    qualifier._stop(process)
    for _ in range(50):
        if not qualifier._process_group_alive(process.pid):
            break
        time.sleep(0.02)
    assert not qualifier._process_group_alive(process.pid)


def test_actual_dashboard_restart_preserves_headless_runtime_and_operator_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import qualify_hwc_headless as qualifier

    monkeypatch.setattr(qualifier, "_recovery_campaign", lambda *_: _recovery_report())
    receipt = qualifier.qualify(require_clean=False, build_dashboard=False)

    assert qualifier.validate_receipt(receipt) == receipt
    assert (
        receipt["processes"]["dashboard_initial_pid"]
        != receipt["processes"]["dashboard_restart_pid"]
    )
    assert (
        receipt["evidence"]["batch_a"]["event_sha256"]
        != receipt["evidence"]["batch_b"]["event_sha256"]
    )


def test_headless_receipt_validation_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import qualify_hwc_headless as qualifier

    monkeypatch.setattr(qualifier, "_recovery_campaign", lambda *_: _recovery_report())
    with pytest.raises(qualifier.HeadlessQualificationError):
        qualifier.validate_receipt({"verdict": "PASS"})
    valid = qualifier.qualify(require_clean=False, build_dashboard=False)
    for mutation in (
        lambda value: value["authority"].update({"live": True}),
        lambda value: value["observations"].update({"cleanup_complete": False}),
        lambda value: value["cleanup"].update({"complete": False}),
        lambda value: value["evidence"].pop("batch_b"),
        lambda value: value["source"].update({"commit_sha": "0" * 40}),
        lambda value: value["processes"].update(
            {"runtime_session_id": "not-a-session"}
        ),
        lambda value: value["evidence"]["batch_b"]["output"].update(
            {"state": "RUNNING"}
        ),
        lambda value: value["evidence"].update({"dashboard_after": {"ok": False}}),
    ):
        candidate = deepcopy(valid)
        mutation(candidate)
        candidate["receipt_sha256"] = qualifier._sha(
            {key: value for key, value in candidate.items() if key != "receipt_sha256"}
        )
        with pytest.raises(qualifier.HeadlessQualificationError):
            qualifier.validate_receipt(candidate)


@pytest.mark.parametrize(
    "fault",
    [
        "dashboard_startup",
        "port_collision",
        "runtime_exit",
        "partial_evidence",
        "cleanup_failure",
    ],
)
def test_headless_failure_injections_never_pass(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import qualify_hwc_headless as qualifier

    monkeypatch.setattr(qualifier, "_recovery_campaign", lambda *_: _recovery_report())
    with pytest.raises(qualifier.HeadlessQualificationError):
        qualifier.qualify(require_clean=False, build_dashboard=False, _fault=fault)
