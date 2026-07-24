#!/usr/bin/env python3
"""Run one deterministic exchange-free SNAPSHOT fixture from a staged backend release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


_FIXTURE = r'''
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import main
from job_attribution import ResearchInvocation

output = Path(sys.argv[1]) / "report_fixture.json"

async def fixture_pipeline(symbols, *, allow_execution, semantic_inputs):
    assert symbols == ["BTC"]
    assert allow_execution is False
    assert semantic_inputs.source_fingerprint == "b" * 64
    return {
        "schema_version": "phase4b-fixture/v1",
        "timestamp": "2026-07-12T18:30:00Z",
        "assets": [{"symbol": "BTC", "suggestion": "WATCH", "confidence": 0.0}],
    }

def fixture_save(report, **kwargs):
    assert kwargs["allow_notifications"] is False
    output.write_text(json.dumps(report, separators=(",", ":")) + "\n", encoding="utf-8")
    output.chmod(0o600)

main.load_snapshot_semantic_inputs = lambda _root: SimpleNamespace(source_fingerprint="b" * 64)
main.run_pipeline = fixture_pipeline
main.save_report = fixture_save
invocation = ResearchInvocation(
    job_id="job_0123456789abcdef0123456789abcdef",
    attempt_id="attempt_fedcba9876543210fedcba9876543210",
    research_only=True,
    backend_commit="41f055b48033714c660f44cc20498b7545366e75",
    reports_dir=None,
    signal_output_dir=None,
    replay_scratchpad_root=None,
)
asyncio.run(main.mode_snapshot(["BTC"], allow_execution=False, invocation=invocation))
assert not any(
    name == "ccxt" or name.startswith("ccxt.") or name.startswith("exchange.")
    for name in sys.modules
)
print("fixture snapshot ok")
'''


def run_fixture_snapshot(release_root: Path, output_root: Path) -> Path:
    release_root = Path(release_root)
    output_root = Path(output_root)
    interpreter = release_root / ".venv/bin/python3.11"
    try:
        info = interpreter.lstat()
        output_info = output_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise ValueError
        if stat.S_ISLNK(output_info.st_mode) or not stat.S_ISDIR(output_info.st_mode):
            raise ValueError
        if stat.S_IMODE(output_info.st_mode) != 0o700 or output_info.st_uid != os.geteuid():
            raise ValueError
        result_path = output_root / "report_fixture.json"
        if result_path.exists() or result_path.is_symlink():
            raise ValueError
        completed = subprocess.run(
            [str(interpreter), "-B", "-c", _FIXTURE, str(output_root)],
            cwd=release_root,
            env={
                "HOME": str(output_root), "PATH": "/usr/bin:/bin", "TZ": "UTC",
                "TRADING_MODE": "paper", "LIVE_EXECUTION_ENABLED": "false",
                "LIVE_TRADING_APPROVED": "false",
            },
            shell=False, check=True, capture_output=True, text=True, timeout=120,
        )
        if completed.stdout.splitlines()[-1:] != ["fixture snapshot ok"]:
            raise ValueError
        result_info = result_path.lstat()
        if (
            not stat.S_ISREG(result_info.st_mode) or stat.S_ISLNK(result_info.st_mode)
            or stat.S_IMODE(result_info.st_mode) != 0o600
        ):
            raise ValueError
        document = json.loads(result_path.read_bytes())
        if (
            document.get("schema_version") != "phase4b-fixture/v1"
            or document.get("research_only") is not True
            or document.get("backend_commit") != "41f055b48033714c660f44cc20498b7545366e75"
        ):
            raise ValueError
        return result_path
    except Exception:
        raise RuntimeError("backend release fixture smoke rejected") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_root", type=Path)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    try:
        result = run_fixture_snapshot(args.release_root, args.output_root)
    except RuntimeError:
        return 2
    print(json.dumps({"result": result.name}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
