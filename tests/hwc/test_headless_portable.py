from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from copy import deepcopy

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests/hwc/fixtures/headless_runtime_runner.py"


def _batch(name: str) -> dict[str, str]:
    return {
        "schema_version": "hwc-paper-batch-v1",
        "batch": name,
        "price": "100.00" if name == "A" else "101.25",
        "target_quantity": "1.00000000" if name == "A" else "1.25000000",
    }


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


def test_runtime_runner_advances_deterministically_without_dashboard(tmp_path: Path) -> None:
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
        assert second["previous_event_sha256"] == first["event_sha256"]
        assert first["input_sha256"] == hashlib.sha256(
            canonical_json_bytes(_batch("A"))
        ).hexdigest()
        assert (tmp_path / "runtime/checkpoint.json").is_file()
        assert (tmp_path / "runtime/results/batch-a.json").is_file()
        assert (tmp_path / "runtime/results/batch-b.json").is_file()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


def test_actual_dashboard_restart_preserves_headless_runtime_and_operator_state() -> None:
    from scripts.qualify_hwc_headless import qualify, validate_receipt

    receipt = qualify(require_clean=False, build_dashboard=False)

    assert validate_receipt(receipt) == receipt
    assert receipt["processes"]["dashboard_initial_pid"] != receipt["processes"]["dashboard_restart_pid"]
    assert receipt["evidence"]["batch_a"]["event_sha256"] != receipt["evidence"]["batch_b"]["event_sha256"]


def test_headless_receipt_validation_is_fail_closed() -> None:
    from scripts.qualify_hwc_headless import HeadlessQualificationError, qualify, validate_receipt

    with pytest.raises(HeadlessQualificationError):
        validate_receipt({"verdict": "PASS"})
    valid = qualify(require_clean=False, build_dashboard=False)
    for mutation in (
        lambda value: value["authority"].update({"live": True}),
        lambda value: value["observations"].update({"cleanup_complete": False}),
        lambda value: value["cleanup"].update({"complete": False}),
        lambda value: value["evidence"].pop("batch_b"),
    ):
        candidate = deepcopy(valid)
        mutation(candidate)
        with pytest.raises(HeadlessQualificationError):
            validate_receipt(candidate)


@pytest.mark.parametrize(
    "fault",
    ["dashboard_startup", "port_collision", "runtime_exit", "partial_evidence", "cleanup_failure"],
)
def test_headless_failure_injections_never_pass(fault: str) -> None:
    from scripts.qualify_hwc_headless import HeadlessQualificationError, qualify

    with pytest.raises(HeadlessQualificationError):
        qualify(require_clean=False, build_dashboard=False, _fault=fault)
