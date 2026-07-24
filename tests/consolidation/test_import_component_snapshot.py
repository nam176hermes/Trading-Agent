from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
IMPORT = ROOT / "scripts/import_component_snapshot.py"
VERIFY = ROOT / "scripts/verify_component_snapshot.py"
AUDIT = ROOT / "scripts/audit_canonical_repo.py"

sys.path.insert(0, str(ROOT / "scripts"))
import import_component_snapshot as importer  # noqa: E402


@pytest.fixture
def tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="consolidation-cli-", dir="/tmp") as directory:
        yield Path(directory)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _repository(path: Path, files: dict[str, bytes]) -> tuple[str, str]:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Consolidation Test")
    _git(path, "config", "user.email", "consolidation@example.invalid")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "fixture")
    return _git(path, "rev-parse", "HEAD"), _git(path, "rev-parse", "HEAD^{tree}")


def _authority(tmp_path: Path) -> tuple[Path, Path]:
    core = tmp_path / "core-source"
    backend = tmp_path / "backend-source"
    dashboard = tmp_path / "dashboard-source"
    core_commit, core_tree = _repository(core, {"README.md": b"core\n"})
    backend_commit, backend_tree = _repository(
        backend,
        {
            ".dexter/scratchpad/session.jsonl": b"runtime state\n",
            "AGENTS.md": b"backend instructions\n",
            "README.md": b"backend\n",
            "main.py": b"print('approved')\n",
            "pyproject.toml": b"[project]\nname='fixture'\nversion='0'\n",
            "scratchpad.py": b"def record_note():\n    return 'source module'\n",
            "scratchpad/runtime.py": b"GENERATED = True\n",
            "scratchpad-history.jsonl": b"runtime state\n",
            "scratchpad.json": b"{}\n",
            "uv.lock": b"version = 1\n",
            "tests/scratchpad-state.json": b"{}\n",
            "tests/scratchpad-state.jsonl": b"runtime state\n",
            "tests/test_safe.py": b"def test_safe():\n    assert True\n",
        },
    )
    dashboard_commit, _ = _repository(
        dashboard,
        {
            "trading-agent/AGENTS.md": b"dashboard instructions\n",
            "trading-agent/package.json": b"{}\n",
            "trading-agent/package-lock.json": b"{}\n",
            "trading-agent/src/app.ts": b"export const value = 1;\n",
            "outside.txt": b"not part of the subtree\n",
        },
    )
    dashboard_tree = _git(
        dashboard, "rev-parse", f"{dashboard_commit}:trading-agent",
    )
    document = {
        "schema_version": 1,
        "sealed_phase4b_metadata_sha256": "a" * 64,
        "components": {
            "core": {
                "repository": str(core),
                "commit": core_commit,
                "tree": core_tree,
                "source_prefix": ".",
                "destination_prefix": ".",
            },
            "backend": {
                "repository": str(backend),
                "commit": backend_commit,
                "tree": backend_tree,
                "source_prefix": ".",
                "destination_prefix": "legacy/research-backend",
            },
            "dashboard": {
                "repository": str(dashboard),
                "commit": dashboard_commit,
                "tree": dashboard_tree,
                "source_prefix": "trading-agent",
                "destination_prefix": "apps/dashboard",
            },
        },
    }
    authority = tmp_path / "authority.json"
    authority.write_text(json.dumps(document), encoding="utf-8")
    return authority, backend


def _run(script: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(argument) for argument in arguments)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def _propose(tmp_path: Path) -> tuple[Path, Path, Path]:
    authority, backend = _authority(tmp_path)
    manifest = tmp_path / "approved.json"
    result = _run(
        IMPORT,
        "propose", "--authority", authority, "--component", "backend",
        "--output", manifest,
    )
    assert result.returncode == 0, result.stderr
    return authority, backend, manifest


def _apply(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    authority, backend, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    result = _run(
        IMPORT,
        "apply", "--authority", authority, "--manifest", manifest,
        "--root", canonical,
    )
    assert result.returncode == 0, result.stderr
    return authority, backend, manifest, canonical


def test_propose_creates_explicit_private_fsynced_manifest_and_never_overwrites(
    tmp_path: Path,
) -> None:
    authority, _ = _authority(tmp_path)
    manifest = tmp_path / "proposal.json"

    first = _run(
        IMPORT,
        "propose", "--authority", authority, "--component", "backend",
        "--output", manifest,
    )
    assert first.returncode == 0, first.stderr
    original = manifest.read_bytes()
    second = _run(
        IMPORT,
        "propose", "--authority", authority, "--component", "backend",
        "--output", manifest,
    )

    assert set(first.stdout.strip().split()) == {
        "component=backend",
        "files=7",
        f"tree={json.loads(original)['source_tree']}",
        f"aggregate={json.loads(original)['aggregate_sha256']}",
    }
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    assert original.endswith(b"\n")
    assert second.returncode != 0
    assert second.stderr.strip() == "E_DESTINATION"
    assert manifest.read_bytes() == original


def test_backend_proposal_preserves_exact_scratchpad_module_not_runtime_artifacts(
    tmp_path: Path,
) -> None:
    _, _, manifest = _propose(tmp_path)
    document = json.loads(manifest.read_bytes())
    sources = {entry["source_path"] for entry in document["entries"]}

    assert "scratchpad.py" in sources
    assert sources.isdisjoint(
        {
            ".dexter/scratchpad/session.jsonl",
            "scratchpad/runtime.py",
            "scratchpad-history.jsonl",
            "scratchpad.json",
            "tests/scratchpad-state.json",
            "tests/scratchpad-state.jsonl",
        }
    )


def test_apply_uses_approved_git_objects_not_mutated_source_worktree_and_is_once_only(
    tmp_path: Path,
) -> None:
    authority, backend, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (backend / "main.py").write_text("SECRET_WORKTREE_VALUE\n", encoding="utf-8")

    first = _run(
        IMPORT,
        "apply", "--authority", authority, "--manifest", manifest,
        "--root", canonical,
    )
    second = _run(
        IMPORT,
        "apply", "--authority", authority, "--manifest", manifest,
        "--root", canonical,
    )

    destination = canonical / "legacy/research-backend"
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "component=backend result=PASS"
    assert (destination / "main.py").read_bytes() == b"print('approved')\n"
    assert second.returncode != 0
    assert second.stderr.strip() == "E_DESTINATION: legacy/research-backend"


def test_apply_rejects_manifest_source_mismatch_without_partial_destination(
    tmp_path: Path,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    document = json.loads(manifest.read_bytes())
    document["entries"][0]["sha256"] = "b" * 64
    entries = json.dumps(
        document["entries"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    document["aggregate_sha256"] = hashlib.sha256(entries).hexdigest()
    manifest.write_bytes(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    canonical = tmp_path / "canonical"
    canonical.mkdir()

    result = _run(
        IMPORT,
        "apply", "--authority", authority, "--manifest", manifest,
        "--root", canonical,
    )

    assert result.returncode != 0
    assert result.stderr.strip().split(":", 1)[0] == "E_TAMPER"
    assert not (canonical / "legacy/research-backend").exists()
    assert not list(canonical.rglob(".backend-import-*"))


def test_apply_rejects_destination_symlink_without_touching_target(tmp_path: Path) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    target = tmp_path / "outside"
    (canonical / "legacy").mkdir(parents=True)
    target.mkdir()
    (canonical / "legacy/research-backend").symlink_to(target, target_is_directory=True)

    result = _run(
        IMPORT,
        "apply", "--authority", authority, "--manifest", manifest,
        "--root", canonical,
    )

    assert result.returncode != 0
    assert result.stderr.strip() == "E_DESTINATION: legacy/research-backend"
    assert not list(target.iterdir())


def test_apply_rechecks_exact_private_tree_before_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    original = importer._write_entry
    injected = False

    def inject_unexpected(
        temporary_descriptor: int,
        loaded: object,
        entry: object,
    ) -> None:
        nonlocal injected
        original(temporary_descriptor, loaded, entry)  # type: ignore[arg-type]
        if not injected:
            descriptor = os.open(
                "unexpected.py", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600, dir_fd=temporary_descriptor,
            )
            os.write(descriptor, b"unexpected\n")
            os.close(descriptor)
            injected = True

    monkeypatch.setattr(importer, "_write_entry", inject_unexpected)

    with pytest.raises(importer.CliError) as raised:
        importer.apply_snapshot(authority, manifest, canonical)

    assert raised.value.code == "E_DESTINATION"
    assert not (canonical / "legacy/research-backend").exists()
    assert not list(canonical.rglob(".backend-import-*"))
    assert not (canonical / "legacy").exists()


def test_apply_atomic_noreplace_never_overwrites_race_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    original = getattr(importer, "_rename_noreplace", None)
    assert callable(original)

    def create_destination_then_rename(parent: int, source: str, destination: str) -> None:
        os.mkdir(destination, 0o700, dir_fd=parent)
        destination_descriptor = os.open(
            destination, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent,
        )
        marker = os.open(
            "race-created", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600, dir_fd=destination_descriptor,
        )
        os.write(marker, b"must survive\n")
        os.close(marker)
        os.close(destination_descriptor)
        original(parent, source, destination)

    monkeypatch.setattr(importer, "_rename_noreplace", create_destination_then_rename)

    with pytest.raises(importer.CliError) as raised:
        importer.apply_snapshot(authority, manifest, canonical)

    assert raised.value.code == "E_DESTINATION"
    assert (canonical / "legacy/research-backend/race-created").read_bytes() == b"must survive\n"
    assert not list(canonical.rglob(".backend-import-*"))


def test_apply_rejects_post_rename_private_tree_mutation_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    original = getattr(importer, "_rename_noreplace", None)
    assert callable(original)

    def attack_rename(parent: int, source: str, destination: str) -> None:
        original(parent, source, destination)
        destination_descriptor = os.open(
            destination, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent,
        )
        legacy_descriptor = os.open(
            "main.py", os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
            dir_fd=destination_descriptor,
        )
        os.write(legacy_descriptor, b"post-rename mutation\n")
        os.close(legacy_descriptor)
        os.close(destination_descriptor)

    monkeypatch.setattr(importer, "_rename_noreplace", attack_rename)

    with pytest.raises(importer.CliError) as raised:
        importer.apply_snapshot(authority, manifest, canonical)

    assert raised.value.code == "E_DESTINATION"
    assert not (canonical / "legacy/research-backend").exists()
    assert not list(canonical.rglob(".backend-import-*"))
    assert not (canonical / "legacy").exists()


def test_apply_preserves_unowned_destination_moved_in_after_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    original = getattr(importer, "_rename_noreplace", None)
    assert callable(original)

    def move_owned_then_replace(parent: int, source: str, destination: str) -> None:
        original(parent, source, destination)
        os.rename(
            destination, f"{destination}.moved-owned",
            src_dir_fd=parent, dst_dir_fd=parent,
        )
        os.mkdir(destination, 0o700, dir_fd=parent)
        destination_descriptor = os.open(
            destination, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent,
        )
        try:
            marker = os.open(
                "must-survive", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600, dir_fd=destination_descriptor,
            )
            try:
                os.write(marker, b"must-survive\n")
            finally:
                os.close(marker)
        finally:
            os.close(destination_descriptor)

    monkeypatch.setattr(importer, "_rename_noreplace", move_owned_then_replace)

    with pytest.raises(importer.CliError) as raised:
        importer.apply_snapshot(authority, manifest, canonical)

    assert raised.value.code == "E_DESTINATION"
    assert (
        canonical / "legacy/research-backend/must-survive"
    ).read_bytes() == b"must-survive\n"
    assert not list(canonical.rglob(".backend-import-*"))


def test_apply_fsync_failure_after_rename_removes_destination_temp_and_new_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    rename = getattr(importer, "_rename_noreplace", None)
    assert callable(rename)
    real_fsync = importer.os.fsync
    renamed = False

    def remember_rename(parent: int, source: str, destination: str) -> None:
        nonlocal renamed
        rename(parent, source, destination)
        renamed = True

    def fail_after_rename(descriptor: int) -> None:
        if renamed:
            raise OSError("injected post-rename fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(importer, "_rename_noreplace", remember_rename)
    monkeypatch.setattr(importer.os, "fsync", fail_after_rename)

    with pytest.raises(importer.CliError) as raised:
        importer.apply_snapshot(authority, manifest, canonical)

    assert raised.value.code == "E_DESTINATION"
    assert not (canonical / "legacy/research-backend").exists()
    assert not list(canonical.rglob(".backend-import-*"))
    assert not (canonical / "legacy").exists()


def test_apply_rejection_preserves_preexisting_empty_intermediate_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    (canonical / "legacy").mkdir(parents=True)
    original = importer._write_entry

    def reject_after_first_write(
        temporary_descriptor: int, loaded: object, entry: object,
    ) -> None:
        original(temporary_descriptor, loaded, entry)  # type: ignore[arg-type]
        raise importer.CliError("E_DESTINATION")

    monkeypatch.setattr(importer, "_write_entry", reject_after_first_write)

    with pytest.raises(importer.CliError):
        importer.apply_snapshot(authority, manifest, canonical)

    assert (canonical / "legacy").is_dir()
    assert not list((canonical / "legacy").iterdir())


def test_apply_fails_closed_and_cleans_up_when_renameat2_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, _, manifest = _propose(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()

    class MissingRenameAt2:
        pass

    monkeypatch.setattr(importer.ctypes, "CDLL", lambda *args, **kwargs: MissingRenameAt2())

    with pytest.raises(importer.CliError) as raised:
        importer.apply_snapshot(authority, manifest, canonical)

    assert raised.value.code == "E_DESTINATION"
    assert not (canonical / "legacy").exists()
    assert not list(canonical.rglob(".backend-import-*"))


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        ("missing", "legacy/research-backend/main.py"),
        ("extra", "legacy/research-backend/extra.py"),
        ("modified", "legacy/research-backend/main.py"),
        ("symlink", "legacy/research-backend/main.py"),
        ("hardlink", "legacy/research-backend/main.py"),
        ("writable", "legacy/research-backend/main.py"),
    ],
)
def test_verifier_rejects_non_exact_or_unsafe_destination(
    tmp_path: Path, mutation: str, expected_path: str,
) -> None:
    authority, _, manifest, canonical = _apply(tmp_path)
    target = canonical / "legacy/research-backend/main.py"
    if mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (target.parent / "extra.py").write_text("extra\n", encoding="utf-8")
    elif mutation == "modified":
        target.write_text("changed\n", encoding="utf-8")
    elif mutation == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "approved.json")
    elif mutation == "hardlink":
        content = target.read_bytes()
        target.unlink()
        outside = tmp_path / "outside-hardlink"
        outside.write_bytes(content)
        os.link(outside, target)
    else:
        target.chmod(0o666)

    result = _run(
        VERIFY,
        "--authority", authority, "--manifest", manifest, "--root", canonical,
    )

    assert result.returncode != 0
    assert result.stderr.strip() == f"E_TAMPER: {expected_path}"


def test_verifier_reads_exact_revision_objects_independently_of_worktree(
    tmp_path: Path,
) -> None:
    authority, _, manifest, canonical = _apply(tmp_path)
    _git(canonical, "init", "-q")
    _git(canonical, "config", "user.name", "Consolidation Test")
    _git(canonical, "config", "user.email", "consolidation@example.invalid")
    _git(canonical, "add", ".")
    _git(canonical, "commit", "-qm", "import")
    revision = _git(canonical, "rev-parse", "HEAD")
    (canonical / "legacy/research-backend/main.py").write_text(
        "working tree changed\n", encoding="utf-8",
    )

    working = _run(
        VERIFY,
        "--authority", authority, "--manifest", manifest, "--root", canonical,
    )
    committed = _run(
        VERIFY,
        "--authority", authority, "--manifest", manifest, "--root", canonical,
        "--revision", revision,
    )

    assert working.returncode != 0
    assert working.stderr.strip() == "E_TAMPER: legacy/research-backend/main.py"
    assert committed.returncode == 0, committed.stderr
    assert committed.stdout.strip() == f"component=backend revision={revision} result=PASS"


def test_failures_are_code_only_and_redact_secret_bearing_absolute_paths(
    tmp_path: Path,
) -> None:
    authority, _ = _authority(tmp_path)
    secret_directory = tmp_path / "token-super-secret"
    secret_directory.mkdir()
    output = secret_directory / "proposal.json"
    document = json.loads(authority.read_text(encoding="utf-8"))
    document["components"]["backend"]["tree"] = "f" * 40
    authority.write_text(json.dumps(document), encoding="utf-8")

    result = _run(
        IMPORT,
        "propose", "--authority", authority, "--component", "backend",
        "--output", output,
    )

    assert result.returncode != 0
    assert result.stderr.strip() == "E_AUTHORITY"
    assert "token-super-secret" not in result.stderr
    assert str(tmp_path) not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("script", [IMPORT, VERIFY, AUDIT])
def test_argument_failures_use_only_the_stable_argument_code(script: Path) -> None:
    result = _run(script)

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip() == "E_ARGUMENT"
