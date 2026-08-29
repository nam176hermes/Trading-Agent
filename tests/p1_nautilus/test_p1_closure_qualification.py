from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.materialize_nautilus_runtime_closure import (
    RuntimeClosureMaterializationError,
    _p1_source_bytes,
    _require_unique_p1_records,
)


ROOT = Path(__file__).parents[2]


def test_p1_source_bytes_disables_moving_git_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        observed.update(argv=argv, **kwargs)
        return SimpleNamespace(stdout=b"exact source\n")

    monkeypatch.setattr(subprocess, "run", run)
    commit = "a" * 40
    assert _p1_source_bytes(commit, "engines/nautilus/runtime_v1/main.py") == b"exact source\n"
    assert observed["argv"] == (
        "git",
        "--no-replace-objects",
        "show",
        f"{commit}:engines/nautilus/runtime_v1/main.py",
    )
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"


def test_p1_materializer_rejects_duplicate_path_or_target() -> None:
    first = {"path": "files/engine/a", "target": "/engine/a"}
    for duplicate in (
        {"path": "files/engine/a", "target": "/engine/b"},
        {"path": "files/engine/b", "target": "/engine/a"},
    ):
        with pytest.raises(RuntimeClosureMaterializationError, match="duplicated"):
            _require_unique_p1_records([first, duplicate])


def test_p1_qualification_defers_only_when_all_authority_is_absent() -> None:
    script = ROOT / "scripts/qualify_nautilus_sealed_imports.py"
    deferred = subprocess.run(
        ["uv", "run", "python", str(script), "--p1"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert deferred.returncode == 0
    assert deferred.stdout == (
        '{"schema":"trading-agent-p1-runtime-qualification/v1",'
        '"status":"DEFERRED"}\n'
    )
    assert deferred.stderr == ""

    partial = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(script),
            "--p1",
            "--base-runtime",
            "/tmp/missing-p1-runtime",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert partial.returncode == 2
    assert partial.stdout == ""
    assert "partial or invalid" in partial.stderr
