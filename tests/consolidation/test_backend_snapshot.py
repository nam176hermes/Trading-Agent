from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "ops/consolidation/source-authority.json"
MANIFEST = ROOT / "ops/consolidation/backend-source-manifest.json"
DESTINATION = ROOT / "legacy/research-backend"
VERIFY = ROOT / "scripts/verify_component_snapshot.py"

BACKEND_COMMIT = "59578f984b72d5d03583a2c06b15a53a224b31c8"
BACKEND_TREE = "54e688e9f144aecd2ee204ab95953f7c57069d3c"
DESTINATION_PREFIX = "legacy/research-backend"

# The approved manifest remains immutable evidence for the atomic backend import.
# These two Phase 4 files were introduced later in the monorepo, so they are
# bound separately instead of being misrepresented as members of the source tree.
POST_IMPORT_EXTENSIONS = {
    "legacy/research-backend/nautilus_parity_adapter.py": {
        "git_blob": "8234ea6923216899895338b05897e8b02193c469",
        "introduced_commit": "9722d838936efae88076d3a04c5f270c2e3db85f",
        "mode": "100644",
        "sha256": "7234431eedfd36b03bf449547fd199bf677f26ca383dff179135037b539964e7",
        "size": 32283,
    },
    "legacy/research-backend/tests/test_nautilus_parity_adapter.py": {
        "git_blob": "df2935c2c355eb3061c0f72d2ec3e4fe24d49785",
        "introduced_commit": "9722d838936efae88076d3a04c5f270c2e3db85f",
        "mode": "100644",
        "sha256": "25e5bee6e4d24bd4f4f29c6f990741fd25452af9191012d0ce56d58cf56fe497",
        "size": 19641,
    },
}


def _assert_exact_backend_inventory(
    *,
    imported: set[str],
    extensions: set[str],
    physical: set[str],
    tracked: set[str],
) -> None:
    assert imported.isdisjoint(extensions)
    assert imported | extensions == physical == tracked


def _assert_extension_bytes(path: Path, record: dict[str, object]) -> None:
    value = path.read_bytes()
    assert len(value) == record["size"]
    assert hashlib.sha256(value).hexdigest() == record["sha256"]


def _assert_post_import_extensions(imported: set[str]) -> None:
    extensions = set(POST_IMPORT_EXTENSIONS)
    assert extensions == {
        "legacy/research-backend/nautilus_parity_adapter.py",
        "legacy/research-backend/tests/test_nautilus_parity_adapter.py",
    }
    assert imported.isdisjoint(extensions)

    for relative, record in POST_IMPORT_EXTENSIONS.items():
        path = ROOT / relative
        metadata = path.lstat()
        expected_mode = int(str(record["mode"])[-3:], 8)
        assert stat.S_ISREG(metadata.st_mode)
        assert not stat.S_ISLNK(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert stat.S_IMODE(metadata.st_mode) == expected_mode
        _assert_extension_bytes(path, record)

        tree_record = subprocess.run(
            ["git", "-C", str(ROOT), "ls-tree", "HEAD", "--", relative],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        metadata_text, observed_path = tree_record.split("\t", 1)
        mode, object_type, git_blob = metadata_text.split(" ", 2)
        assert observed_path == relative
        assert mode == record["mode"]
        assert object_type == "blob"
        assert git_blob == record["git_blob"]

        introductions = subprocess.run(
            [
                "git", "-C", str(ROOT), "log", "--diff-filter=A", "--format=%H",
                "HEAD", "--", relative,
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.splitlines()
        assert introductions == [record["introduced_commit"]]

        committed = subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "blob", git_blob],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        assert committed == path.read_bytes()

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
    imported = {entry["destination_path"] for entry in document["entries"]}
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
    _assert_post_import_extensions(imported)
    _assert_exact_backend_inventory(
        imported=imported,
        extensions=set(POST_IMPORT_EXTENSIONS),
        physical=actual,
        tracked=tracked_paths,
    )
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


def test_backend_inventory_contract_rejects_unlisted_post_import_path() -> None:
    with pytest.raises(AssertionError):
        _assert_exact_backend_inventory(
            imported={"legacy/research-backend/main.py"},
            extensions={"legacy/research-backend/nautilus_parity_adapter.py"},
            physical={
                "legacy/research-backend/main.py",
                "legacy/research-backend/nautilus_parity_adapter.py",
                "legacy/research-backend/unreviewed.py",
            },
            tracked={
                "legacy/research-backend/main.py",
                "legacy/research-backend/nautilus_parity_adapter.py",
                "legacy/research-backend/unreviewed.py",
            },
        )


def test_backend_extension_contract_rejects_byte_drift(tmp_path: Path) -> None:
    extension = tmp_path / "nautilus_parity_adapter.py"
    extension.write_bytes(b"drifted adapter bytes\n")

    with pytest.raises(AssertionError):
        _assert_extension_bytes(
            extension,
            POST_IMPORT_EXTENSIONS[
                "legacy/research-backend/nautilus_parity_adapter.py"
            ],
        )
