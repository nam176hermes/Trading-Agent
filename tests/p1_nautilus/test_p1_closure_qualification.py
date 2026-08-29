from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.materialize_nautilus_runtime_closure as materializer
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
        "/usr/bin/git",
        "--no-replace-objects",
        "show",
        f"{commit}:engines/nautilus/runtime_v1/main.py",
    )
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["PATH"] == "/usr/bin:/bin"


def test_p1_source_bytes_ignores_hostile_path_for_nonexistent_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "fake-git-ran"
    fake_git = fake_bin / "git"
    source = ROOT / "engines/nautilus/runtime_v1/main.py"
    fake_git.write_text(
        "#!/bin/sh\n"
        f"/usr/bin/touch {marker}\n"
        f"/usr/bin/cat {source}\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", os.fspath(fake_bin))

    with pytest.raises(
        RuntimeClosureMaterializationError,
        match="cannot supply the runtime inventory",
    ):
        _p1_source_bytes("f" * 40, "engines/nautilus/runtime_v1/main.py")

    assert not marker.exists()


def test_p1_source_bytes_rejects_writable_pinned_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commit = subprocess.check_output(
        ("/usr/bin/git", "rev-parse", "HEAD"),
        cwd=ROOT,
        text=True,
    ).strip()
    fake_git = tmp_path / "git"
    fake_git.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_git.chmod(0o777)
    monkeypatch.setattr(materializer, "_SYSTEM_GIT", fake_git, raising=False)

    with pytest.raises(
        RuntimeClosureMaterializationError,
        match="trusted system Git executable is invalid",
    ):
        _p1_source_bytes(commit, "engines/nautilus/runtime_v1/main.py")


def test_p1_inherited_copy_rejects_replacement_after_attestation(
    tmp_path: Path,
) -> None:
    accepted = b"accepted G1 executable\n"
    replacement = b"replacement executable\n"
    base_runtime = tmp_path / "candidate"
    source = base_runtime / "files/usr/bin/python3.12"
    source.parent.mkdir(parents=True)
    source.write_bytes(accepted)
    source.chmod(0o500)
    record = {
        "mode": "0500",
        "path": "files/usr/bin/python3.12",
        "sha256": hashlib.sha256(accepted).hexdigest(),
        "size": len(accepted),
        "target": "/usr/bin/python3.12",
    }

    source.chmod(0o600)
    source.write_bytes(replacement)
    source.chmod(0o500)

    with pytest.raises(
        RuntimeClosureMaterializationError,
        match="G1 runtime file authority drifted during copy",
    ):
        materializer._copy_p1_inherited_file(base_runtime, tmp_path / "staging", record)


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
