from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/implementation/p0-maintainability-hotspots.json"
SCHEMA_VERSION = "p0-maintainability-hotspots/v1"
EXPECTED_BASELINE_SHA = "e0baa410cdcf0de4344d58ad82fd8a56788f84df"
FROZEN_FOR_GROWTH = "FROZEN_FOR_GROWTH"
MONITOR = "MONITOR"
EXPECTED_HOTSPOTS = [
    {
        "path": "scripts/t_g03_capability_topology.py",
        "status": FROZEN_FOR_GROWTH,
        "baseline_bytes": 362662,
        "max_net_growth_bytes": 0,
        "responsibility_id": "P0_CAPABILITY_TOPOLOGY",
        "baseline_first_party_imports": [
            "scripts",
            "scripts.validate_disposable_postgres_approval",
            "scripts.validate_disposable_postgres_fixture_plan",
            "trading_control.phase3b_sources",
        ],
    },
    {
        "path": "scripts/check_artifact_firewall.py",
        "status": FROZEN_FOR_GROWTH,
        "baseline_bytes": 141810,
        "max_net_growth_bytes": 0,
        "responsibility_id": "P0_ARTIFACT_FIREWALL",
        "baseline_first_party_imports": ["scripts"],
    },
    {
        "path": "scripts/check_p0_ci_closure.py",
        "status": MONITOR,
        "baseline_bytes": 43300,
        "responsibility_id": "P0_CLOSURE_CHECKER",
        "baseline_first_party_imports": ["scripts", "scripts.check_artifact_firewall"],
    },
]

CHECKER = ROOT / "scripts/check_p0_maintainability.py"


def _run_checker(root: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python",
            str(CHECKER),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write_fixture_manifest(
    root: Path,
    *,
    status: str = FROZEN_FOR_GROWTH,
    path: str = "scripts/hotspot.py",
    baseline_bytes: int = 4,
    max_net_growth_bytes: int | None = 0,
    baseline_first_party_imports: list[str] | None = None,
) -> Path:
    hotspot: dict[str, object] = {
        "path": path,
        "status": status,
        "baseline_bytes": baseline_bytes,
        "responsibility_id": "P0_FIXTURE",
        "baseline_first_party_imports": baseline_first_party_imports or [],
    }
    if max_net_growth_bytes is not None:
        hotspot["max_net_growth_bytes"] = max_net_growth_bytes
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "baseline_sha": "HEAD",
                "hotspots": [hotspot],
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.fixture
def checker_repo(tmp_path: Path) -> Path:
    """A real Git repository with a committed four-byte hotspot baseline."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "p0@example.invalid")
    _git(root, "config", "user.name", "P0 Fixture")
    hotspot = root / "scripts/hotspot.py"
    hotspot.parent.mkdir()
    hotspot.write_bytes(b"pass")
    _git(root, "add", "scripts/hotspot.py")
    _git(root, "commit", "-qm", "baseline")
    return root


def test_p0_maintainability_hotspot_inventory_is_a_strict_custody_manifest() -> None:
    """Reject policy changes that make a hotspot untracked, ambiguous, or unsafe."""
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert set(document) == {"schema_version", "baseline_sha", "hotspots"}
    assert document["schema_version"] == SCHEMA_VERSION
    assert isinstance(document["baseline_sha"], str)
    assert document["baseline_sha"] == EXPECTED_BASELINE_SHA
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
            "baseline_first_party_imports",
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

        assert type(hotspot["baseline_bytes"]) is int
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
        assert isinstance(hotspot["baseline_first_party_imports"], list)
        assert hotspot["baseline_first_party_imports"] == sorted(
            hotspot["baseline_first_party_imports"]
        )
        assert all(
            isinstance(import_name, str) and import_name
            for import_name in hotspot["baseline_first_party_imports"]
        )
        if status == FROZEN_FOR_GROWTH:
            assert type(hotspot["max_net_growth_bytes"]) is int
            assert hotspot["max_net_growth_bytes"] >= 0


def test_checker_rejects_missing_manifest(checker_repo: Path) -> None:
    """Removing the reviewed policy must prevent the guard from running."""
    result = _run_checker(checker_repo, checker_repo / "missing.json")
    assert result.returncode != 0
    assert "manifest" in result.stderr.lower()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda document: document.update(unexpected=True), "unknown"),
        (lambda document: document["hotspots"].append(document["hotspots"][0].copy()), "duplicate"),
        (lambda document: document["hotspots"][0].update(path="/tmp/hotspot.py"), "absolute"),
        (lambda document: document["hotspots"][0].update(path="../hotspot.py"), "traversal"),
        (lambda document: document.update(baseline_sha="does-not-exist"), "baseline"),
    ],
)
def test_checker_rejects_invalid_manifest_contract(
    checker_repo: Path, change: object, expected: str
) -> None:
    """Malformed policy data must not weaken hotspot custody or baseline proof."""
    manifest = _write_fixture_manifest(checker_repo)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    change(document)  # type: ignore[operator]
    manifest.write_text(json.dumps(document), encoding="utf-8")
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert expected in result.stderr.lower()


def test_checker_rejects_baseline_object_path_absent(checker_repo: Path) -> None:
    """A current file absent from the pinned Git baseline cannot establish custody."""
    current_only = checker_repo / "scripts/current_only.py"
    current_only.write_bytes(b"pass")
    manifest = _write_fixture_manifest(checker_repo, path="scripts/current_only.py")
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "baseline" in result.stderr.lower()


def test_checker_rejects_import_baseline_not_derived_from_pinned_blob(
    checker_repo: Path,
) -> None:
    """A manifest cannot silently grandfather a dependency absent from its Git blob."""
    manifest = _write_fixture_manifest(
        checker_repo, baseline_first_party_imports=["services.market_data"]
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "baseline first-party imports" in result.stderr.lower()


def test_checker_rejects_symlink_hotspot(checker_repo: Path) -> None:
    """A symlink can switch the checked source after policy review and is forbidden."""
    target = checker_repo / "scripts/target.py"
    target.write_bytes(b"pass")
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.unlink()
    hotspot.symlink_to(target.name)
    result = _run_checker(checker_repo, _write_fixture_manifest(checker_repo))
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_checker_rejects_symlinked_parent_hotspot(checker_repo: Path) -> None:
    """Every logical path component must be a real directory, not an alias."""
    linked_hotspot = checker_repo / "linked/hotspot.py"
    linked_hotspot.parent.mkdir()
    linked_hotspot.write_bytes(b"pass")
    _git(checker_repo, "add", "linked/hotspot.py")
    _git(checker_repo, "commit", "-qm", "linked baseline")
    linked_hotspot.unlink()
    linked_hotspot.parent.rmdir()
    linked_hotspot.parent.symlink_to("scripts", target_is_directory=True)
    manifest = _write_fixture_manifest(checker_repo, path="linked/hotspot.py")
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_checker_rejects_non_regular_hotspot(checker_repo: Path) -> None:
    """Directories do not have a stable source-byte meaning and must be rejected."""
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.unlink()
    hotspot.mkdir()
    result = _run_checker(checker_repo, _write_fixture_manifest(checker_repo))
    assert result.returncode != 0
    assert "regular" in result.stderr.lower()


@pytest.mark.parametrize(
    ("contents", "returncode"),
    [(b"pass", 0), (b"pas", 0), (b"pass!", 1)],
)
def test_checker_enforces_frozen_net_growth(
    checker_repo: Path, contents: bytes, returncode: int
) -> None:
    """A frozen hotspot may shrink but cannot exceed its approved byte ceiling."""
    (checker_repo / "scripts/hotspot.py").write_bytes(contents)
    result = _run_checker(checker_repo, _write_fixture_manifest(checker_repo))
    assert result.returncode == returncode
    assert "current_bytes=" in result.stderr
    assert "baseline_bytes=" in result.stderr
    assert "delta_bytes=" in result.stderr
    assert "status=FROZEN_FOR_GROWTH" in result.stderr


def test_checker_reports_monitor_growth_without_failing(checker_repo: Path) -> None:
    """MONITOR hotspots expose growth for review without imposing a size ceiling."""
    (checker_repo / "scripts/hotspot.py").write_bytes(b"pass\n#grow")
    manifest = _write_fixture_manifest(checker_repo, status=MONITOR, max_net_growth_bytes=None)
    result = _run_checker(checker_repo, manifest)
    assert result.returncode == 0
    assert result.stdout == "P0_MAINTAINABILITY_GUARD_PASS\n"
    assert "current_bytes=10" in result.stderr
    assert "baseline_bytes=4" in result.stderr
    assert "delta_bytes=6" in result.stderr
    assert "status=MONITOR" in result.stderr


@pytest.mark.parametrize("status", [FROZEN_FOR_GROWTH, MONITOR])
def test_checker_rejects_same_size_first_party_import_drift(
    checker_repo: Path, status: str
) -> None:
    """Status only controls size ceilings; new first-party coupling is always rejected."""
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.write_bytes(b"import sys #\n")
    _git(checker_repo, "add", "scripts/hotspot.py")
    _git(checker_repo, "commit", "-qm", "import baseline")
    hotspot.write_bytes(b"import ops #\n")
    manifest = _write_fixture_manifest(
        checker_repo,
        status=status,
        baseline_bytes=13,
        max_net_growth_bytes=0 if status == FROZEN_FOR_GROWTH else None,
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr


@pytest.mark.parametrize(
    "import_name",
    [
        "services.market_data",
        "services.quant_research",
        "services.agent_reasoning",
        "packages.portfolio_strategy",
        "engines.nautilus.runtime",
    ],
)
def test_checker_rejects_new_frozen_runtime_import_without_review(
    checker_repo: Path, import_name: str
) -> None:
    """Frozen topology code cannot acquire runtime-domain dependencies unreviewed."""
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.write_bytes(b"import sys #\n")
    _git(checker_repo, "add", "scripts/hotspot.py")
    _git(checker_repo, "commit", "-qm", "stdlib import baseline")
    hotspot.write_text(f"import {import_name}\n", encoding="utf-8")
    manifest = _write_fixture_manifest(
        checker_repo,
        baseline_bytes=13,
        max_net_growth_bytes=100,
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr
    assert import_name in result.stderr


def test_checker_has_no_automatic_policy_rewrite_mode() -> None:
    """Policy updates remain source-reviewed instead of accepting local drift by CLI."""
    result = subprocess.run(
        ["python", str(CHECKER), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--update" not in result.stdout
    assert "--accept-current" not in result.stdout
