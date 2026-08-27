from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "scripts/check_workspace.py"


def _run_doctor(**environment_updates: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        **environment_updates,
    }
    return subprocess.run(
        [sys.executable, str(DOCTOR), "--root", str(ROOT)],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_doctor_reports_canonical_paper_only_workspace() -> None:
    result = _run_doctor()

    assert result.returncode == 0, result.stdout
    assert "CHECK=PASS name=canonical_root" in result.stdout
    assert "CHECK=PASS name=core_worktree" in result.stdout
    assert "CHECK=PASS name=live_flags" in result.stdout
    assert "CHECK=PASS name=trusted_test_tmp" in result.stdout
    assert "SUMMARY=PASS" in result.stdout


def test_doctor_fails_when_a_live_flag_is_enabled() -> None:
    result = _run_doctor(LIVE_EXECUTION_ENABLED="true")

    assert result.returncode == 2
    assert "CHECK=FAIL name=live_flags" in result.stdout
    assert "SUMMARY=FAIL" in result.stdout
