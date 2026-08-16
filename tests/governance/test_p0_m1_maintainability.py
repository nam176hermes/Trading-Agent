from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/implementation/p0-maintainability-hotspots.json"
SCHEMA_VERSION = "p0-maintainability-hotspots/v1"
FROZEN_FOR_GROWTH = "FROZEN_FOR_GROWTH"
MONITOR = "MONITOR"
EXPECTED_HOTSPOTS = [
    {
        "path": "scripts/t_g03_capability_topology.py",
        "status": FROZEN_FOR_GROWTH,
        "baseline_bytes": 362662,
        "max_net_growth_bytes": 0,
        "responsibility_id": "P0_CAPABILITY_TOPOLOGY",
    },
    {
        "path": "scripts/check_artifact_firewall.py",
        "status": FROZEN_FOR_GROWTH,
        "baseline_bytes": 141810,
        "max_net_growth_bytes": 0,
        "responsibility_id": "P0_ARTIFACT_FIREWALL",
    },
    {
        "path": "scripts/check_p0_ci_closure.py",
        "status": MONITOR,
        "baseline_bytes": 43300,
        "responsibility_id": "P0_CLOSURE_CHECKER",
    },
]


def test_p0_maintainability_hotspot_inventory_is_a_strict_custody_manifest() -> None:
    """Reject policy changes that make a hotspot untracked, ambiguous, or unsafe."""
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert set(document) == {"schema_version", "baseline_sha", "hotspots"}
    assert document["schema_version"] == SCHEMA_VERSION
    assert isinstance(document["baseline_sha"], str)
    assert document["baseline_sha"]
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{document['baseline_sha']}^{{commit}}"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", document["baseline_sha"], "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0

    hotspots = document["hotspots"]
    assert isinstance(hotspots, list)
    assert hotspots == EXPECTED_HOTSPOTS
    paths: set[str] = set()
    for hotspot in hotspots:
        assert isinstance(hotspot, dict)
        status = hotspot.get("status")
        expected_keys = {
            "path",
            "status",
            "baseline_bytes",
            "responsibility_id",
        }
        if status == FROZEN_FOR_GROWTH:
            expected_keys.add("max_net_growth_bytes")
        assert set(hotspot) == expected_keys
        assert status in {FROZEN_FOR_GROWTH, MONITOR}

        path = hotspot["path"]
        assert isinstance(path, str)
        assert path not in paths
        paths.add(path)
        candidate = ROOT / path
        assert candidate.resolve().is_relative_to(ROOT.resolve())
        assert candidate.exists()
        assert not candidate.is_symlink()
        assert stat.S_ISREG(candidate.stat().st_mode)

        assert isinstance(hotspot["baseline_bytes"], int)
        assert hotspot["baseline_bytes"] > 0
        baseline_size = subprocess.run(
            ["git", "cat-file", "-s", f"{document['baseline_sha']}:{path}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert baseline_size.returncode == 0
        assert int(baseline_size.stdout) == hotspot["baseline_bytes"]
        assert isinstance(hotspot["responsibility_id"], str)
        assert hotspot["responsibility_id"]
        if status == FROZEN_FOR_GROWTH:
            assert isinstance(hotspot["max_net_growth_bytes"], int)
            assert hotspot["max_net_growth_bytes"] >= 0
