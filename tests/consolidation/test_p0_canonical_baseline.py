from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts/audit_canonical_repo.py"
MANIFEST = ROOT / "ops/consolidation/p0-canonical-baseline.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def test_p0_baseline_manifest_is_approved_and_passes_the_portable_audit() -> None:
    """A missing or altered approved candidate baseline must block P0 validation."""
    assert MANIFEST.is_file(), "manifest missing"
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "base_branch",
        "base_sha",
        "candidate_source_branch",
        "candidate_start_sha",
        "qualified_sha",
        "promotion_mode",
        "paper_only",
        "live_execution_authorized",
    }
    assert document == {
        "schema_version": "p0-canonical-baseline/v1",
        "base_branch": "main",
        "base_sha": "19627785c140c502260f864e462fed9b9925436e",
        "candidate_source_branch": "codex/phase1-terra-autopilot-19627785c140",
        "candidate_start_sha": "417c17452ea31f0ca8c8e9893ac3c03a3a90a7c1",
        "qualified_sha": None,
        "promotion_mode": "fast-forward-only",
        "paper_only": True,
        "live_execution_authorized": False,
    }
    assert all(SHA.fullmatch(document[key]) for key in ("base_sha", "candidate_start_sha"))

    result = subprocess.run(
        [sys.executable, str(AUDIT), "--portable", "--check-p0-baseline"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
