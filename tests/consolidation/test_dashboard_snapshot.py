from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "ops/consolidation/source-authority.json"
MANIFEST = ROOT / "ops/consolidation/dashboard-source-manifest.json"
DESTINATION = ROOT / "apps/dashboard"
VERIFY = ROOT / "scripts/verify_component_snapshot.py"

DASHBOARD_COMMIT = "84627f16e9753b1104d661697720b93897f27d27"
DASHBOARD_TREE = "792f572dea8f819438785e43ee05e07c5b6567bd"
SOURCE_PREFIX = "trading-agent"
DESTINATION_PREFIX = "apps/dashboard"
ENTRY_COUNT = 223

REQUIRED_SOURCE_PATHS = {
    "trading-agent/AGENTS.md",
    "trading-agent/package.json",
    "trading-agent/package-lock.json",
    "trading-agent/src/app/page.tsx",
    "trading-agent/src/lib/trading/access-policy.ts",
    "trading-agent/tests/api-access-boundary.test.mjs",
    "trading-agent/tests/dashboard-security.integration.sh",
    "trading-agent/tsconfig.json",
}
FORBIDDEN_COMPONENTS = {
    ".next",
    "__pycache__",
    "coverage",
    "credentials",
    "node_modules",
    "runtime",
}


def _manifest() -> dict[str, object]:
    assert MANIFEST.is_file(), "approved dashboard manifest is absent"
    assert DESTINATION.is_dir(), "dashboard snapshot destination is absent"
    return json.loads(MANIFEST.read_bytes())


def test_dashboard_manifest_is_fixed_complete_and_source_scoped() -> None:
    document = _manifest()
    entries = document["entries"]

    assert document["component"] == "dashboard"
    assert document["source_commit"] == DASHBOARD_COMMIT
    assert document["source_tree"] == DASHBOARD_TREE
    assert document["source_prefix"] == SOURCE_PREFIX
    assert document["destination_prefix"] == DESTINATION_PREFIX
    assert isinstance(entries, list) and len(entries) == ENTRY_COUNT

    source_paths = {entry["source_path"] for entry in entries}
    assert len(source_paths) == ENTRY_COUNT
    assert REQUIRED_SOURCE_PATHS <= source_paths

    for entry in entries:
        source = entry["source_path"]
        destination = entry["destination_path"]
        source_path = PurePosixPath(source)
        assert source_path.parts[0] == SOURCE_PREFIX
        relative = PurePosixPath(*source_path.parts[1:])
        assert "*" not in source and "?" not in source
        assert "*" not in destination and "?" not in destination
        assert destination == f"{DESTINATION_PREFIX}/{relative.as_posix()}"
        assert entry["mode"] in {"100644", "100755"}

        lowered_parts = {part.lower() for part in relative.parts}
        lowered_name = relative.name.lower()
        assert not lowered_parts & FORBIDDEN_COMPONENTS
        assert not lowered_name.startswith(".env")
        assert not lowered_name.endswith(".log")
        assert "credential" not in lowered_name
        assert "secret" not in lowered_name
        assert relative.parts[:2] != ("data", "runtime")
        assert relative.suffix != ".pyc"


def test_dashboard_snapshot_preserves_introduction_and_tracks_current_regular_files() -> None:
    document = _manifest()
    entries = document["entries"]
    assert isinstance(entries, list)
    expected_introduction = {entry["destination_path"] for entry in entries}
    actual: set[str] = set()

    agents = DESTINATION / "AGENTS.md"
    metadata = agents.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert not stat.S_ISLNK(metadata.st_mode)
    agents_text = agents.read_text(encoding="utf-8")
    assert "<!-- BEGIN:nextjs-agent-rules -->" in agents_text
    assert "node_modules/next/dist/docs/" in agents_text
    assert "<!-- END:nextjs-agent-rules -->" in agents_text

    for directory, names, filenames in os.walk(DESTINATION, followlinks=False):
        names[:] = sorted(name for name in names if name not in {".next", "node_modules", "coverage", "__pycache__"})
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(ROOT)
        for name in names:
            metadata = (directory_path / name).lstat()
            assert stat.S_ISDIR(metadata.st_mode)
            assert not stat.S_ISLNK(metadata.st_mode)
        for name in filenames:
            if name in {"next-env.d.ts", "tsconfig.tsbuildinfo"}:
                continue
            path = directory_path / name
            metadata = path.lstat()
            assert stat.S_ISREG(metadata.st_mode)
            assert not stat.S_ISLNK(metadata.st_mode)
            assert metadata.st_nlink == 1
            actual.add((relative_directory / name).as_posix())

    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", DESTINATION_PREFIX],
        check=True,
        stdout=subprocess.PIPE,
    )
    tracked_paths = {raw.decode("utf-8") for raw in tracked.stdout.split(b"\0") if raw}
    assert expected_introduction <= actual
    assert actual == tracked_paths


def test_dashboard_snapshot_passes_independent_verifier() -> None:
    _manifest()
    introductions = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "log",
            "--diff-filter=A",
            "--format=%H",
            "HEAD",
            "--",
            f"{DESTINATION_PREFIX}/package.json",
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    assert len(introductions) == 1
    introduction = introductions[0]
    parent = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", f"{introduction}^"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    absent_from_parent = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{parent}:{DESTINATION_PREFIX}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert absent_from_parent.returncode != 0
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFY),
            "--authority",
            str(AUTHORITY),
            "--manifest",
            str(MANIFEST),
            "--root",
            str(ROOT),
            "--revision",
            introduction,
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"component=dashboard revision={introduction} result=PASS"
    )
