from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts/audit_canonical_repo.py"
IMPORT = ROOT / "scripts/import_component_snapshot.py"


@pytest.fixture
def tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="canonical-audit-", dir="/tmp") as directory:
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


def _init(repository: Path, files: dict[str, bytes] | None = None) -> tuple[str, str]:
    repository.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Consolidation Test")
    _git(repository, "config", "user.email", "consolidation@example.invalid")
    for relative, content in (files or {"README.md": b"fixture\n"}).items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "fixture")
    return _git(repository, "rev-parse", "HEAD"), _git(repository, "rev-parse", "HEAD^{tree}")


def _run(root: object, *options: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT), "--root", str(root), *options],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def _run_import(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(IMPORT), *(str(argument) for argument in arguments)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def _sources(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    core = tmp_path / "core-source"
    backend = tmp_path / "backend-source"
    dashboard = tmp_path / "dashboard-source"
    core_commit, core_tree = _init(core, {"README.md": b"core\n"})
    backend_commit, backend_tree = _init(
        backend,
        {
            "AGENTS.md": b"backend instructions\n",
            "README.md": b"backend\n",
            "main.py": b"print('paper only')\n",
            "pyproject.toml": b"[project]\nname='backend'\nversion='0'\n",
            "uv.lock": b"version = 1\n",
            "tests/test_safe.py": b"def test_safe():\n    assert True\n",
        },
    )
    dashboard_commit, _ = _init(
        dashboard,
        {
            "trading-agent/AGENTS.md": b"dashboard instructions\n",
            "trading-agent/package.json": b"{}\n",
            "trading-agent/package-lock.json": b"{}\n",
            "trading-agent/src/app.ts": b"export const safe = true;\n",
        },
    )
    dashboard_tree = _git(
        dashboard, "rev-parse", f"{dashboard_commit}:trading-agent",
    )
    document: dict[str, object] = {
        "schema_version": 1,
        "sealed_phase4b_metadata_sha256": "a" * 64,
        "components": {
            "core": {
                "repository": str(core), "commit": core_commit, "tree": core_tree,
                "source_prefix": ".", "destination_prefix": ".",
            },
            "backend": {
                "repository": str(backend), "commit": backend_commit,
                "tree": backend_tree, "source_prefix": ".",
                "destination_prefix": "legacy/research-backend",
            },
            "dashboard": {
                "repository": str(dashboard), "commit": dashboard_commit,
                "tree": dashboard_tree, "source_prefix": "trading-agent",
                "destination_prefix": "apps/dashboard",
            },
        },
    }
    authority = tmp_path / "source-authority.json"
    authority.write_text(json.dumps(document), encoding="utf-8")
    return authority, document


def _valid_root(tmp_path: Path, backend_tamper: str | None = None) -> Path:
    authority, _ = _sources(tmp_path)
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    _git(canonical, "init", "-q")
    _git(canonical, "config", "user.name", "Consolidation Test")
    _git(canonical, "config", "user.email", "consolidation@example.invalid")
    base_files = {
        "AGENTS.md": b"canonical instructions\n",
        "pyproject.toml": b"[project]\nname='canonical'\nversion='0'\n",
        "uv.lock": b"version = 1\n",
    }
    for relative, content in base_files.items():
        (canonical / relative).write_bytes(content)
    consolidation = canonical / "ops/consolidation"
    consolidation.mkdir(parents=True)
    shutil.copyfile(authority, consolidation / "source-authority.json")
    _git(canonical, "add", ".")
    _git(canonical, "commit", "-qm", "base")

    for component, manifest_name in (
        ("backend", "backend-source-manifest.json"),
        ("dashboard", "dashboard-source-manifest.json"),
    ):
        manifest = consolidation / manifest_name
        proposed = _run_import(
            "propose", "--authority", consolidation / "source-authority.json",
            "--component", component, "--output", manifest,
        )
        assert proposed.returncode == 0, proposed.stderr
        manifest.chmod(0o644)
        applied = _run_import(
            "apply", "--authority", consolidation / "source-authority.json",
            "--manifest", manifest, "--root", canonical,
        )
        assert applied.returncode == 0, applied.stderr
        if component == "backend" and backend_tamper is not None:
            backend = canonical / "legacy/research-backend"
            if backend_tamper == "missing":
                (backend / "main.py").unlink()
            elif backend_tamper == "extra":
                (backend / "unexpected.py").write_text(
                    "credential-value-must-not-leak\n", encoding="utf-8",
                )
            elif backend_tamper == "modified":
                (backend / "main.py").write_text(
                    "credential-value-must-not-leak\n", encoding="utf-8",
                )
            else:
                raise AssertionError(f"unsupported fixture tamper: {backend_tamper}")
        _git(canonical, "add", ".")
        _git(canonical, "commit", "-qm", f"import {component}")
    return canonical


def _tampered_import_root(tmp_path: Path, tamper: str) -> Path:
    return _valid_root(tmp_path, backend_tamper=tamper)


def _rewrite_json(path: Path, replacement: tuple[str, object]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    key, value = replacement
    document[key] = value
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _canonical_checkpoint() -> tuple[str, str]:
    return _git(ROOT, "rev-parse", "HEAD"), _git(ROOT, "status", "--porcelain=v1")


def _assert_isolated_rejection(
    tmp_path: Path,
    repository: Path,
    result: subprocess.CompletedProcess[str],
    expected_error: str,
    canonical_before: tuple[str, str],
    *redacted: str,
) -> None:
    assert repository.is_relative_to(tmp_path)
    assert result.returncode != 0
    assert result.stderr.strip() == expected_error
    for value in redacted:
        assert value not in result.stdout
        assert value not in result.stderr
    assert _canonical_checkpoint() == canonical_before


def test_rejects_relative_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)

    result = _run("repository", cwd=tmp_path)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_ROOT"


def test_rejects_symlinked_root_without_exposing_target(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)

    result = _run(alias)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_ROOT"
    assert str(repository) not in result.stderr


def test_rejects_linked_worktree_git_file(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    _init(primary)
    linked = tmp_path / "linked"
    _git(primary, "worktree", "add", "-q", "-b", "linked", str(linked))

    result = _run(linked)

    assert (linked / ".git").is_file()
    assert result.returncode != 0
    assert result.stderr.strip() == "E_ROOT: .git"


def test_rejects_git_common_directory_outside_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    external = tmp_path / "external-common"
    (repository / ".git").rename(external)
    (repository / ".git").mkdir()
    shutil.copyfile(external / "HEAD", repository / ".git/HEAD")
    shutil.copyfile(external / "index", repository / ".git/index")
    (repository / ".git/commondir").write_text(str(external), encoding="utf-8")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_ROOT: .git"
    assert str(external) not in result.stderr


def test_rejects_nested_git_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    nested = repository / "vendor/tool"
    _init(nested)

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_NESTED_GIT: vendor/tool/.git"


def test_rejects_gitlink_index_entry(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit, _ = _init(repository)
    _git(repository, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor")
    _git(repository, "commit", "-qm", "gitlink")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_NESTED_GIT: vendor"


def test_rejects_tracked_symlink(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    (repository / "linked-source").symlink_to("README.md")
    _git(repository, "add", "linked-source")
    _git(repository, "commit", "-qm", "tracked link")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_TRACKED_LINK: linked-source"


def test_rejects_forbidden_tracked_name(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    (repository / ".env.production").write_text("TOKEN=redacted\n", encoding="utf-8")
    _git(repository, "add", ".env.production")
    _git(repository, "commit", "-qm", "forbidden")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_FORBIDDEN: .env.production"
    assert "TOKEN" not in result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        ".mode",
        "legacy/research-backend/.mode",
        "apps/dashboard/credentials/session.txt",
        "apps/dashboard/secrets/key.txt",
        "apps/dashboard/runtime/state.json",
        "apps/dashboard/data/runtime/state.json",
        "legacy/research-backend/jobs/job.json",
        "legacy/research-backend/job_artifacts/job.json",
        "legacy/research-backend/scratchpad.json",
        "legacy/research-backend/scratchpad-state.json",
        "legacy/research-backend/scratchpad-history.jsonl",
        "legacy/research-backend/scratchpad/runtime.py",
        "legacy/research-backend/nested/scratchpad/runtime.py",
        "legacy/research-backend/run_status.json",
        "legacy/research-backend/live_prices.json",
        "legacy/research-backend/decisions_scored.jsonl",
        "legacy/research-backend/strategy.json",
        "legacy/research-backend/state/custom-state.jsonl",
        "legacy/research-backend/.dexter/state.json",
        "legacy/research-backend/.codegraph/state.db",
        "legacy/research-backend/.venv/bin/python",
        "legacy/research-backend/.cache/state",
        "legacy/research-backend/decisions/state.json",
        "legacy/research-backend/memory/state.json",
        "legacy/research-backend/models/model.bin",
        "legacy/research-backend/signals/signal.json",
        "legacy/research-backend/reports/report.json",
        "node_modules/package/index.js",
        "build/output.bin",
        "dist/output.bin",
        "runtime/state.json",
        "data/runtime/state.json",
        "application.log",
        "module.pyc",
    ],
)
def test_rejects_all_current_forbidden_tracked_families(
    tmp_path: Path, relative: str,
) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"forbidden but never printed\n")
    _git(repository, "add", "-f", "--", relative)
    _git(repository, "commit", "-qm", "forbidden family")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == f"E_FORBIDDEN: {relative}"
    assert "forbidden but never printed" not in result.stderr


def test_allows_exact_backend_scratchpad_source_module(tmp_path: Path) -> None:
    repository = _valid_root(tmp_path)
    module = repository / "legacy/research-backend/scratchpad.py"
    module.write_text(
        "def record_note():\n    return 'source module'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "--", "legacy/research-backend/scratchpad.py")
    _git(repository, "commit", "-qm", "add scratchpad source module")

    result = _run(repository)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "apps/dashboard/src/lib/credentials/session.txt",
        "apps/dashboard/src/lib/service-credential.json",
        "apps/dashboard/src/lib/client-secret.txt",
        "apps/dashboard/credentials/session.txt",
        "apps/dashboard/service-credential.json",
        "apps/dashboard/client-secret.txt",
    ],
)
def test_rejects_dashboard_nested_credentials_and_secret_names(
    tmp_path: Path, relative: str,
) -> None:
    repository = tmp_path / "repository"
    _init(repository)
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"dashboard secret never printed\n")
    _git(repository, "add", "-f", "--", relative)
    _git(repository, "commit", "-qm", "dashboard forbidden family")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == f"E_FORBIDDEN: {relative}"
    assert "dashboard secret never printed" not in result.stderr


def test_rejects_missing_required_lock_and_instruction_files(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init(repository)

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_REQUIRED: AGENTS.md"


def test_release_rejects_any_dirty_staged_unstaged_or_untracked_state(tmp_path: Path) -> None:
    repository = _valid_root(tmp_path)
    (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    result = _run(repository, "--release")

    assert result.returncode != 0
    assert result.stderr.strip() == "E_DIRTY: untracked.txt"


def test_audit_git_calls_disable_repository_fsmonitor_and_hooks(tmp_path: Path) -> None:
    repository = _valid_root(tmp_path)
    fsmonitor_marker = tmp_path / "fsmonitor-executed"
    hook_marker = tmp_path / "hook-executed"
    fsmonitor = tmp_path / "malicious-fsmonitor.sh"
    hooks = tmp_path / "malicious-hooks"
    hooks.mkdir()
    fsmonitor.write_text(
        f"#!/bin/sh\n: > '{fsmonitor_marker}'\nexit 0\n", encoding="utf-8",
    )
    fsmonitor.chmod(0o700)
    post_index_change = hooks / "post-index-change"
    post_index_change.write_text(
        f"#!/bin/sh\n: > '{hook_marker}'\nexit 0\n", encoding="utf-8",
    )
    post_index_change.chmod(0o700)
    _git(repository, "config", "core.fsmonitor", str(fsmonitor))
    _git(repository, "config", "core.hooksPath", str(hooks))
    (repository / "AGENTS.md").touch()

    result = _run(repository)

    assert result.returncode == 0, result.stderr
    assert not fsmonitor_marker.exists()
    assert not hook_marker.exists()


def test_source_marker_policy_is_exact_without_self_matching() -> None:
    expected_global = (
        b".local/share/" + b"codex-worktrees",
        b"/home/thenam176/projects/" + b"trading-dashboard",
        b"/home/thenam176/projects/" + b"trading-agent-migration",
    )
    expected_component = (
        b"/home/thenam176/" + b".hermes",
        b"~/" + b".hermes",
    )
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys;"
                f"sys.path.insert(0,{str(ROOT / 'scripts')!r});"
                "import audit_canonical_repo as audit;"
                "print(json.dumps([[value.decode() for value in audit._GLOBAL_SOURCE_MARKERS],"
                "[value.decode() for value in audit._COMPONENT_SOURCE_MARKERS]]))"
            ),
        ],
        cwd=ROOT,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    runtime_global, runtime_component = json.loads(probe.stdout)

    assert tuple(value.encode() for value in runtime_global) == expected_global
    assert tuple(value.encode() for value in runtime_component) == expected_component
    all_markers = expected_global + expected_component
    for relative in (
        "scripts/audit_canonical_repo.py",
        "tests/consolidation/test_absolute_source_paths.py",
        "tests/consolidation/test_audit_canonical_repo.py",
    ):
        source = (ROOT / relative).read_bytes()
        assert not any(marker in source for marker in all_markers), relative


def test_base_audit_rejects_current_executable_source_checkout_path(tmp_path: Path) -> None:
    repository = _valid_root(tmp_path)
    target = repository / "apps/dashboard/src/legacy-path.ts"
    forbidden_source = "/home/thenam176/projects/" + "trading-dashboard"
    assert forbidden_source == bytes.fromhex(
        "2f686f6d652f7468656e616d3137362f70726f6a656374732f"
        "74726164696e672d64617368626f617264"
    ).decode("ascii")
    target.write_text(
        f'export const source = "{forbidden_source}";\n',
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "bad source path")

    result = _run(repository)

    assert result.returncode != 0
    assert result.stderr.strip() == "E_SOURCE_PATH: apps/dashboard/src/legacy-path.ts"
    assert "/home/thenam176" not in result.stderr


def test_json_success_has_exact_one_root_status_and_component_keys(tmp_path: Path) -> None:
    repository = _valid_root(tmp_path)

    result = _run(repository, "--json")
    payload = json.loads(result.stdout)

    assert result.returncode == 0, result.stderr
    assert set(payload) == {
        "schema_version", "root", "head", "branch", "status", "components", "result",
    }
    assert payload["root"] == str(repository)
    assert payload["head"] == _git(repository, "rev-parse", "HEAD")
    assert payload["branch"] == _git(repository, "branch", "--show-current")
    assert payload["status"] == "clean"
    assert set(payload["components"]) == {"core", "backend", "dashboard"}
    assert payload["result"] == "PASS"


def test_json_failure_redacts_untrusted_absolute_root_from_all_output(tmp_path: Path) -> None:
    secret_root = tmp_path / "token-super-secret-root"

    result = _run(secret_root, "--json")
    payload = json.loads(result.stdout)

    assert result.returncode != 0
    assert set(payload) == {
        "schema_version", "root", "head", "branch", "status", "components", "result",
    }
    assert payload["root"] == ""
    assert "token-super-secret-root" not in result.stdout
    assert "token-super-secret-root" not in result.stderr


def test_json_argument_failure_still_returns_exact_redacted_schema() -> None:
    result = subprocess.run(
        [sys.executable, str(AUDIT), "--json"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)

    assert result.returncode != 0
    assert set(payload) == {
        "schema_version", "root", "head", "branch", "status", "components", "result",
    }
    assert payload["root"] == ""
    assert payload["result"] == "E_ARGUMENT"
    assert result.stderr.strip() == "E_ARGUMENT"


@pytest.mark.parametrize(
    ("tamper", "expected_path"),
    [
        ("missing", "legacy/research-backend/main.py"),
        ("extra", "legacy/research-backend/unexpected.py"),
        ("modified", "legacy/research-backend/main.py"),
    ],
)
def test_final_audit_rejects_isolated_imported_file_tamper(
    tmp_path: Path, tamper: str, expected_path: str,
) -> None:
    canonical_before = _canonical_checkpoint()
    repository = _tampered_import_root(tmp_path, tamper)

    result = _run(repository)

    _assert_isolated_rejection(
        tmp_path,
        repository,
        result,
        f"E_TAMPER: {expected_path}",
        canonical_before,
        "credential-value-must-not-leak",
    )


def test_final_audit_rejects_changed_manifest_aggregate(tmp_path: Path) -> None:
    canonical_before = _canonical_checkpoint()
    repository = _valid_root(tmp_path)
    manifest = repository / "ops/consolidation/backend-source-manifest.json"
    _rewrite_json(manifest, ("aggregate_sha256", "f" * 64))

    result = _run(repository)

    _assert_isolated_rejection(
        tmp_path, repository, result, "E_MANIFEST", canonical_before,
    )


def test_final_audit_rejects_authority_commit_tree_mismatch(tmp_path: Path) -> None:
    canonical_before = _canonical_checkpoint()
    repository = _valid_root(tmp_path)
    authority = repository / "ops/consolidation/source-authority.json"
    document = json.loads(authority.read_text(encoding="utf-8"))
    backend = document["components"]["backend"]
    source = Path(backend["repository"])
    (source / "README.md").write_text("changed authority source\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-qm", "second source revision")
    backend["commit"] = _git(source, "rev-parse", "HEAD")
    authority.write_text(json.dumps(document), encoding="utf-8")

    result = _run(repository)

    _assert_isolated_rejection(
        tmp_path, repository, result, "E_AUTHORITY", canonical_before, str(source),
    )


def test_final_audit_rejects_isolated_nested_git(tmp_path: Path) -> None:
    canonical_before = _canonical_checkpoint()
    repository = _valid_root(tmp_path)
    _init(repository / "vendor/tool")

    result = _run(repository)

    _assert_isolated_rejection(
        tmp_path,
        repository,
        result,
        "E_NESTED_GIT: vendor/tool/.git",
        canonical_before,
    )


def test_final_audit_rejects_tracked_forbidden_file_without_content(
    tmp_path: Path,
) -> None:
    canonical_before = _canonical_checkpoint()
    repository = _valid_root(tmp_path)
    sensitive_value = "credential-value-must-not-leak"
    target = repository / "legacy/research-backend/.env.production"
    target.write_text(sensitive_value + "\n", encoding="utf-8")
    _git(repository, "add", "-f", "--", "legacy/research-backend/.env.production")
    _git(repository, "commit", "-qm", "isolated forbidden fixture")

    result = _run(repository)

    _assert_isolated_rejection(
        tmp_path,
        repository,
        result,
        "E_FORBIDDEN: legacy/research-backend/.env.production",
        canonical_before,
        sensitive_value,
    )


def test_final_audit_release_rejects_dirty_isolated_copy(tmp_path: Path) -> None:
    canonical_before = _canonical_checkpoint()
    repository = _valid_root(tmp_path)
    dirty = repository / "operator-note.txt"
    dirty.write_text("credential-value-must-not-leak\n", encoding="utf-8")

    result = _run(repository, "--release")

    _assert_isolated_rejection(
        tmp_path,
        repository,
        result,
        "E_DIRTY: operator-note.txt",
        canonical_before,
        "credential-value-must-not-leak",
    )
