from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import replace

import pytest

from packages.runtime_release.manifest import (
    ReleasePolicy,
    build_release,
    phase4_app_release_policy,
    phase4_backend_release_policy,
    verify_release,
    create_manifest,
    write_manifest,
    _remove_bytecode,
)
from packages.runtime_release.backend_policy import (
    APPROVED_PHASE4_BACKEND_COMMIT,
    PHASE4_BACKEND_AUDITED_PATHS, phase4_backend_policy,
    require_approved_backend_commit,
    verify_phase4_backend_release,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_path() -> Path:
    """Use the Linux filesystem so ownership and mode tests are meaningful."""
    path = Path(tempfile.mkdtemp(prefix="phase4-release-test-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    (repo / "app.py").write_text("VERSION = 'committed'\n")
    (repo / ".env").write_text("SECRET=committed-but-excluded\n")
    (repo / "private.keys.enc").write_text("ciphertext\n")
    (repo / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.0.0'\nrequires-python='>=3.11,<3.12'\n"
    )
    (repo / "uv.lock").write_text("version = 1\nrevision = 3\nrequires-python = '>=3.11, <3.12'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _policy(**overrides: object) -> ReleasePolicy:
    values: dict[str, object] = {
        "expected_uid": os.getuid(),
        "expected_gid": os.getgid(),
        "python_executable": sys.executable,
        "install_dependencies": False,
    }
    values.update(overrides)
    return ReleasePolicy(**values)


def _verify_policy(policy: ReleasePolicy, commit: str, python_identity: str) -> ReleasePolicy:
    return replace(
        policy,
        expected_git_commit=commit,
        expected_python_identity=python_identity,
    )


def test_build_exports_exact_commit_not_dirty_worktree(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    (repo / "app.py").write_text("VERSION = 'dirty-secret-value'\n")
    (repo / "untracked.py").write_text("must not ship\n")
    destination = tmp_path / "release"

    result = build_release(repo, commit, destination, _policy(create_venv=False))

    assert (destination / "app.py").read_text() == "VERSION = 'committed'\n"
    assert not (destination / "untracked.py").exists()
    assert destination.stat().st_mode & 0o7777 == 0o755
    assert result.commit == commit


def test_build_excludes_sensitive_and_policy_paths(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    (repo / "omit.txt").write_text("excluded")
    _git(repo, "add", "omit.txt")
    _git(repo, "commit", "-qm", "add excluded file")
    commit = _git(repo, "rev-parse", "HEAD")
    destination = tmp_path / "release"

    result = build_release(
        repo,
        commit,
        destination,
        _policy(create_venv=False, exclusions=("omit.txt",)),
    )

    assert not (destination / ".env").exists()
    assert not (destination / "private.keys.enc").exists()
    assert not (destination / "omit.txt").exists()
    assert all(entry["path"] not in {".env", "private.keys.enc", "omit.txt"} for entry in result.entries)


def test_build_creates_copied_python311_venv_and_external_manifest(tmp_path: Path) -> None:
    if sys.version_info[:2] != (3, 11):
        pytest.skip("requires the repository's Python 3.11 interpreter")
    repo, commit = _repo(tmp_path)
    destination = tmp_path / "release"

    result = build_release(repo, commit, destination, _policy())

    venv_python = destination / ".venv/bin/python"
    assert venv_python.exists()
    assert not venv_python.is_symlink()
    identity = subprocess.run(
        [str(venv_python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert identity == "3.11"
    assert result.manifest_path.parent == destination.parent
    assert not result.manifest_path.is_relative_to(destination)
    assert result.manifest_path.exists()
    assert result.python_identity == f"CPython {platform.python_version()}"
    verify_release(
        destination,
        result.manifest_path,
        result.digest,
        _verify_policy(_policy(), commit, result.python_identity),
    )


def test_build_with_venv_is_identical_across_destinations_and_relocatable(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    first = tmp_path / "random-looking-release-one"
    second = tmp_path / "different-release-two"

    first_result = build_release(repo, commit, first, _policy())
    second_result = build_release(repo, commit, second, _policy())

    assert first_result.entries == second_result.entries
    assert first_result.digest == second_result.digest
    assert first_result.manifest_path.read_bytes() == second_result.manifest_path.read_bytes()
    published = tmp_path / "published-final-name"
    second.rename(published)
    completed = subprocess.run(
        [str(published / ".venv/bin/python"), "-c", "print('published-ok')"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == "published-ok\n"
    staging_fragments = ("random-looking-release-one", "different-release-two", ".random-looking-release-one.")
    for entry in first_result.entries:
        if entry["type"] == "file":
            content = (first / entry["path"]).read_bytes()
            assert not any(fragment.encode() in content for fragment in staging_fragments)


@pytest.mark.host_coupled
def test_actual_locked_app_build_is_offline_copied_symlink_free_and_runnable(tmp_path: Path) -> None:
    commit = _git(REPOSITORY_ROOT, "rev-parse", "HEAD")
    wheelhouse = os.environ.get("TRADING_RUNTIME_RELEASE_WHEELHOUSE")
    if not wheelhouse:
        pytest.fail("host release wheelhouse is not configured")
    policy = phase4_app_release_policy(
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        python_executable=sys.executable,
        install_dependencies=True,
        uv_offline=True,
        offline_wheelhouse=wheelhouse,
        remove_console_scripts=True,
    )
    first_root = tmp_path / "actual-first"
    second_root = tmp_path / "actual-second"

    first = build_release(REPOSITORY_ROOT, commit, first_root, policy)
    second = build_release(REPOSITORY_ROOT, commit, second_root, policy)

    assert first.entries == second.entries
    assert first.digest == second.digest
    for release in (first_root, second_root):
        interpreter = release / ".venv/bin/python3.11"
        assert interpreter.is_file()
        assert not interpreter.is_symlink()
        assert interpreter.stat().st_mode & 0o111
        assert not any(path.is_symlink() for path in release.rglob("*"))
        assert not any(path.suffix == ".pyc" for path in release.rglob("*.pyc"))
        assert not (release / ".uv-build-cache").exists()
        assert (release / ".venv/lib/python3.11/site-packages/runtime-release-source.pth").read_text() == (
            "../../../..\n../../../../apps/control_api\n"
        )
        for release_file in release.rglob("*"):
            if release_file.is_file():
                assert os.fsencode(wheelhouse) not in release_file.read_bytes()
    published = tmp_path / "actual-published"
    second_root.rename(published)
    interpreter = published / ".venv/bin/python3.11"
    imported = subprocess.run(
        [
            str(interpreter),
            "-c",
            (
                "import json, sys; import apps.job_api.main; import services.job_worker.main; "
                "import uvicorn; print(json.dumps({'uvicorn': uvicorn.__version__, 'sys_path': sys.path}))"
            ),
        ],
        cwd=published,
        check=True,
        capture_output=True,
        text=True,
    )
    imported_payload = json.loads(imported.stdout)
    assert imported_payload["uvicorn"]
    resolved_import_paths = {
        Path(path or published).resolve() for path in imported_payload["sys_path"]
    }
    assert REPOSITORY_ROOT.resolve() not in resolved_import_paths
    assert published.resolve() in resolved_import_paths
    assert (published / "apps/control_api").resolve() in resolved_import_paths
    uvicorn_help = subprocess.run(
        [str(interpreter), "-m", "uvicorn", "--help"],
        cwd=published,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Usage: python -m uvicorn" in uvicorn_help.stdout
    assert not (published / ".venv/bin/uvicorn").exists()


def test_build_modes_and_digest_are_independent_of_umask(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    executable = repo / "run.sh"
    executable.write_text("#!/bin/sh\nprintf 'ok\\n'\n")
    executable.chmod(0o755)
    nested = repo / "nested"
    nested.mkdir()
    (nested / "data.txt").write_text("data\n")
    _git(repo, "add", "run.sh", "nested/data.txt")
    _git(repo, "commit", "-qm", "add normalized modes")
    commit = _git(repo, "rev-parse", "HEAD")

    old_umask = os.umask(0o022)
    try:
        first = build_release(repo, commit, tmp_path / "umask-022", _policy(create_venv=False))
        os.umask(0o077)
        second = build_release(repo, commit, tmp_path / "umask-077", _policy(create_venv=False))
    finally:
        os.umask(old_umask)

    assert first.entries == second.entries
    assert first.digest == second.digest
    modes = {entry["path"]: entry["mode"] for entry in first.entries}
    assert modes["run.sh"] == "0755"
    assert modes["nested"] == "0755"
    assert modes["nested/data.txt"] == "0644"
    assert first.manifest_path.stat().st_mode & 0o7777 == 0o644
    assert second.manifest_path.stat().st_mode & 0o7777 == 0o644


def test_release_finalization_removes_path_bound_python_bytecode(tmp_path: Path) -> None:
    package = tmp_path / ".venv/lib/python3.11/site-packages/example"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-311.pyc").write_bytes(b"path-bound-bytecode")
    (package / "module.py").write_text("VALUE = 1\n")

    _remove_bytecode(tmp_path / ".venv")

    assert (package / "module.py").is_file()
    assert not cache.exists()


def test_phase4_app_policy_excludes_only_three_code_owned_link_paths(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    for name in ("crypto-research", "legacy-trading-agent", "trading-dashboard"):
        (repo / name).symlink_to(f"/external/{name}")
    _git(repo, "add", "crypto-research", "legacy-trading-agent", "trading-dashboard")
    _git(repo, "commit", "-qm", "add linked projects")
    commit = _git(repo, "rev-parse", "HEAD")
    policy = phase4_app_release_policy(
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        python_executable=sys.executable,
        create_venv=False,
        install_dependencies=False,
    )

    result = build_release(repo, commit, tmp_path / "release", policy)

    paths = {entry["path"] for entry in result.entries}
    assert paths.isdisjoint({"crypto-research", "legacy-trading-agent", "trading-dashboard"})


def test_phase4_backend_policy_exports_only_code_owned_audited_files(tmp_path: Path) -> None:
    repo = tmp_path / "backend"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    tracked = {
        "main.py": "print('snapshot')\n",
        "research_semantics.py": "VALUE = 1\n",
        "pyproject.toml": "[project]\nname='fixture'\nversion='0.0.0'\n",
        "uv.lock": "version = 1\nrevision = 3\n",
        ".keys.enc": "ciphertext\n",
        ".mode": "paper\n",
        "reports/report.json": "{}\n",
        "tests/test_main.py": "raise AssertionError\n",
        "trading-pipeline.sh": "#!/bin/sh\n",
        "unexpected.py": "SHOULD_NOT_SHIP = True\n",
    }
    for name, content in tracked.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "backend fixture")
    commit = _git(repo, "rev-parse", "HEAD")

    policy = phase4_backend_release_policy(
        audited_paths=("main.py", "research_semantics.py", "pyproject.toml", "uv.lock"),
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        python_executable=sys.executable,
        create_venv=False,
        install_dependencies=False,
    )
    result = build_release(repo, commit, tmp_path / "backend-release", policy)

    paths = {entry["path"] for entry in result.entries}
    assert paths == {"main.py", "pyproject.toml", "research_semantics.py", "uv.lock"}
    assert result.commit == commit
    assert policy.release_type == "phase4-backend"


def test_phase4_backend_policy_fails_if_any_audited_file_is_absent(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    policy = phase4_backend_release_policy(
        audited_paths=("app.py", "missing-required.py"),
        expected_uid=os.getuid(), expected_gid=os.getgid(),
        python_executable=sys.executable, create_venv=False,
        install_dependencies=False,
    )

    with pytest.raises(ValueError, match="release build failed"):
        build_release(repo, commit, tmp_path / "backend-release", policy)


def test_phase4_backend_audited_set_has_lock_and_no_runtime_or_deploy_paths() -> None:
    paths = set(PHASE4_BACKEND_AUDITED_PATHS)

    assert {"main.py", "research_semantics.py", "pyproject.toml", "uv.lock", "SOUL.md"} <= paths
    assert len(paths) == len(PHASE4_BACKEND_AUDITED_PATHS)
    forbidden_parts = {
        ".env", ".keys.enc", ".mode", ".kill_switch", "tests", "reports",
        "signals", "memory", "models", "logs", "decisions", ".codegraph", ".dexter",
    }
    assert all(not forbidden_parts.intersection(Path(path).parts) for path in paths)
    assert all(not path.endswith((".sh", ".db", ".sqlite", ".sqlite3")) for path in paths)
    assert APPROVED_PHASE4_BACKEND_COMMIT == "41f055b48033714c660f44cc20498b7545366e75"
    with pytest.raises(ValueError, match="backend commit is not approved"):
        require_approved_backend_commit("a" * 40)


def test_backend_verifier_rejects_self_consistent_manifest_with_extra_source_file(tmp_path: Path) -> None:
    repo = tmp_path / "backend-exact"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    for name in PHASE4_BACKEND_AUDITED_PATHS:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "# fixture\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "exact backend fixture")
    commit = _git(repo, "rev-parse", "HEAD")
    policy = phase4_backend_policy(
        expected_uid=os.getuid(), expected_gid=os.getgid(),
        python_executable=sys.executable, create_venv=False,
        install_dependencies=False,
    )
    release = tmp_path / "backend-release"
    result = build_release(repo, commit, release, policy)
    assert verify_phase4_backend_release(
        release, result.manifest_path, result.digest,
        expected_commit=commit, expected_python_identity=result.python_identity,
        expected_uid=os.getuid(), expected_gid=os.getgid(),
    )

    (release / "unexpected.py").write_text("SECRET = False\n")
    entries = create_manifest(release, policy)
    extra_manifest = tmp_path / "extra.manifest.json"
    extra_digest = write_manifest(
        entries, extra_manifest, release_type="phase4-backend",
        git_commit=commit, python_identity=result.python_identity,
    )
    with pytest.raises(ValueError, match="source set mismatch"):
        verify_phase4_backend_release(
            release, extra_manifest, extra_digest,
            expected_commit=commit, expected_python_identity=result.python_identity,
            expected_uid=os.getuid(), expected_gid=os.getgid(),
        )


@pytest.mark.parametrize("link_path", ["unexpected-link", "nested/trading-dashboard"])
def test_phase4_app_policy_rejects_any_other_tracked_symlink(tmp_path: Path, link_path: str) -> None:
    repo, _ = _repo(tmp_path)
    link = repo / link_path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("/external/unexpected")
    _git(repo, "add", link_path)
    _git(repo, "commit", "-qm", "add unexpected link")
    commit = _git(repo, "rev-parse", "HEAD")
    policy = phase4_app_release_policy(
        expected_uid=os.getuid(),
        expected_gid=os.getgid(),
        python_executable=sys.executable,
        create_venv=False,
        install_dependencies=False,
    )

    with pytest.raises(ValueError, match="release build failed"):
        build_release(repo, commit, tmp_path / "release", policy)


def test_build_refuses_non_commit_object_destination_reuse_and_bad_python(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    destination = tmp_path / "release"
    destination.mkdir()

    with pytest.raises(ValueError, match="release build failed"):
        build_release(repo, commit, destination, _policy(create_venv=False))
    with pytest.raises(ValueError, match="release build failed"):
        build_release(repo, "HEAD^{tree}", tmp_path / "other", _policy(create_venv=False))
    with pytest.raises(ValueError, match="release build failed"):
        build_release(
            repo,
            commit,
            tmp_path / "third",
            _policy(create_venv=True, python_executable="/bin/false"),
        )


def test_build_errors_redact_source_commit_and_destination(tmp_path: Path) -> None:
    source = tmp_path / "source-secret-name"
    source.mkdir()
    _git(source, "init", "-q")
    destination = tmp_path / "destination-secret-name"
    supplied_commit = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    with pytest.raises(ValueError) as caught:
        build_release(source, supplied_commit, destination, _policy(create_venv=False))

    message = str(caught.value)
    assert message == "release build failed"
    assert str(source) not in message
    assert str(destination) not in message
    assert supplied_commit not in message
    formatted = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert str(source) not in formatted
    assert str(destination) not in formatted
    assert supplied_commit not in formatted


@pytest.mark.parametrize("script", ["build_phase4_release.py", "verify_phase4_release.py"])
def test_release_cli_help_runs_from_repository_checkout(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / script), "--help"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
    assert "--release-kind" in completed.stdout
