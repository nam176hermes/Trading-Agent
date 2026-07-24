from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "ops/consolidation/source-authority.json"
MANIFEST = ROOT / "ops/consolidation/backend-source-manifest.json"
DESTINATION = ROOT / "legacy/research-backend"
VERIFY = ROOT / "scripts/verify_component_snapshot.py"

BACKEND_COMMIT = "59578f984b72d5d03583a2c06b15a53a224b31c8"
BACKEND_TREE = "54e688e9f144aecd2ee204ab95953f7c57069d3c"
DESTINATION_PREFIX = "legacy/research-backend"

REQUIRED_SOURCE_PATHS = {
    "CLAUDE.md",
    "constraints-phase1.txt",
    "db/repository.py",
    "exchange/adapter.py",
    "main.py",
    "memory.py",
    "model_config.py",
    "pyproject.toml",
    "scratchpad.py",
    "signal_parser.py",
    "tests/test_phase4_research_only.py",
    "uv.lock",
}
FORBIDDEN_COMPONENTS = {
    ".codegraph",
    ".dexter",
    ".superpowers",
    ".venv",
    "__pycache__",
    "decisions",
    "deploy",
    "job_artifacts",
    "jobs",
    "memory",
    "models",
    "reports",
    "scratchpad",
    "scripts",
    "signals",
}
FORBIDDEN_NAMES = {
    ".keys.enc",
    ".kill_switch",
    ".mode",
    "decisions_scored.jsonl",
    "live_prices.json",
    "run_status.json",
    "strategy.json",
    "trading.db",
}


def _manifest() -> dict[str, object]:
    assert MANIFEST.is_file(), "approved backend manifest is absent"
    assert DESTINATION.is_dir(), "backend snapshot destination is absent"
    return json.loads(MANIFEST.read_bytes())


def test_backend_manifest_is_fixed_complete_and_contains_no_globs_or_gitlink() -> None:
    document = _manifest()
    entries = document["entries"]

    assert document["component"] == "backend"
    assert document["source_commit"] == BACKEND_COMMIT
    assert document["source_tree"] == BACKEND_TREE
    assert document["source_prefix"] == "."
    assert document["destination_prefix"] == DESTINATION_PREFIX
    assert isinstance(entries, list) and len(entries) == 135

    source_paths = {entry["source_path"] for entry in entries}
    assert len(source_paths) == 135
    assert REQUIRED_SOURCE_PATHS <= source_paths
    assert "reference/ml4t" not in source_paths
    assert sorted(path for path in source_paths if "scratchpad" in PurePosixPath(path).name) == [
        "scratchpad.py"
    ]

    for entry in entries:
        source = entry["source_path"]
        destination = entry["destination_path"]
        assert not set(source) & set("*?[]")
        assert not set(destination) & set("*?[]")
        assert destination == f"{DESTINATION_PREFIX}/{source}"
        assert entry["mode"] in {"100644", "100755"}

        path = PurePosixPath(source)
        components = set(path.parts)
        assert not components & FORBIDDEN_COMPONENTS
        assert path.name not in FORBIDDEN_NAMES
        assert not path.name.startswith(".env")
        assert not (path.name.startswith("scratchpad") and path.suffix in {".json", ".jsonl"})
        assert path.suffix != ".pyc"


def test_backend_snapshot_is_exact_regular_single_link_reproduction() -> None:
    document = _manifest()
    expected = {entry["destination_path"] for entry in document["entries"]}
    actual: set[str] = set()

    pruned = {
        ".codegraph", ".dexter", ".pytest_cache", ".superpowers", ".venv", "__pycache__",
        "decisions", "deploy", "job_artifacts", "jobs", "memory", "models", "reports",
        "scratchpad", "signals",
    }
    for directory, names, filenames in os.walk(DESTINATION, followlinks=False):
        names[:] = sorted(name for name in names if name not in pruned)
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(ROOT)
        for name in names:
            metadata = (directory_path / name).lstat()
            assert stat.S_ISDIR(metadata.st_mode)
            assert not stat.S_ISLNK(metadata.st_mode)
        for name in filenames:
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
    assert expected == actual == tracked_paths
    introductions = subprocess.run(
        [
            "git", "-C", str(ROOT), "log", "--diff-filter=A", "--format=%H",
            "HEAD", "--", f"{DESTINATION_PREFIX}/pyproject.toml",
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    assert len(introductions) == 1
    backend_introduction = introductions[0]
    parent = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", f"{backend_introduction}^"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{parent}:{DESTINATION_PREFIX}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode != 0
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
            backend_introduction,
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
        f"component=backend revision={backend_introduction} result=PASS"
    )
