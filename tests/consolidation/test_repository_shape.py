from __future__ import annotations

import ast
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_LINKS = {
    "crypto-research",
    "legacy-trading-agent",
    "trading-dashboard",
}
REQUIRED_COMPONENT_FILES = {
    "AGENTS.md",
    "pyproject.toml",
    "uv.lock",
    "legacy/research-backend/AGENTS.md",
    "legacy/research-backend/pyproject.toml",
    "legacy/research-backend/uv.lock",
    "apps/dashboard/AGENTS.md",
    "apps/dashboard/package.json",
    "apps/dashboard/package-lock.json",
}
MAKE_TARGETS = {
    "audit",
    "audit-release",
    "audit-portable",
    "audit-python-source",
    "audit-dependencies-production",
    "audit-dependencies-dev",
    "audit-dependencies",
    "generate-contracts",
    "check-contracts",
    "check-d0-closure",
    "check-test-skips",
    "check-critical-coverage",
    "check-secrets",
    "test",
    "test-portable-embedded-proof",
    "test-core",
    "test-consolidation",
    "test-production",
    "test-runtime-release",
    "test-runtime-release-host",
    "test-runtime-postgres",
    "test-runtime-dual-read",
    "test-security",
    "test-backend",
    "test-dashboard",
    "typecheck-dashboard",
    "lint-dashboard",
    "build-dashboard",
    "prepare-root-test-install",
    "test-all-private",
    "test-all-portable-private",
    "test-all",
    "ci",
    "ci-private",
    "ci-portable",
    "ci-portable-private",
}
POSTGRES_TEMPLATE = "ops/postgres/postgres.env.example"
FORBIDDEN_POSTGRES_TEMPLATE = "ops/postgres/.env.example"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class RepositoryShapeError(RuntimeError):
    """A current repository path cannot be inspected safely."""


def _git_at(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        [
            "git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-C", str(root), *arguments,
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    ).stdout


def _git(*arguments: str) -> bytes:
    return _git_at(ROOT, *arguments)


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value != path.as_posix()
        or "." in path.parts
        or ".." in path.parts
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RepositoryShapeError("unsafe repository path")
    return value


def _tracked_modes(root: Path = ROOT) -> dict[str, str]:
    result: dict[str, str] = {}
    raw = _git_at(root, "ls-files", "-s", "-z")
    if raw and not raw.endswith(b"\0"):
        raise RepositoryShapeError("malformed Git index")
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode = metadata.split(b" ", 1)[0].decode("ascii")
            relative = _safe_relative(encoded_path.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise RepositoryShapeError("malformed Git index") from error
        if relative in result:
            raise RepositoryShapeError("duplicate Git index path")
        result[relative] = mode
    return result


def _current_paths(root: Path = ROOT) -> list[str]:
    raw = _git_at(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "-z",
    )
    if raw and not raw.endswith(b"\0"):
        raise RepositoryShapeError("malformed Git path list")
    result: set[str] = set()
    for encoded_path in raw.split(b"\0"):
        if not encoded_path:
            continue
        try:
            relative = _safe_relative(encoded_path.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise RepositoryShapeError("malformed Git path list") from error
        if relative in result:
            raise RepositoryShapeError("duplicate Git path")
        result.add(relative)
    return sorted(result, key=lambda value: value.encode("utf-8"))


def _read_current_regular(
    root: Path, relative: str, *, maximum_bytes: int = MAX_SOURCE_BYTES,
) -> bytes:
    relative = _safe_relative(relative)
    if (
        _NOFOLLOW == 0
        or _CLOEXEC == 0
        or _NONBLOCK == 0
        or _DIRECTORY == 0
        or maximum_bytes < 0
    ):
        raise RepositoryShapeError(relative)
    directory_descriptors: list[int] = []
    descriptor: int | None = None
    try:
        current_directory = os.open(
            root,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC | _NONBLOCK,
        )
        directory_descriptors.append(current_directory)
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            current_directory = os.open(
                part,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC | _NONBLOCK,
                dir_fd=current_directory,
            )
            directory_descriptors.append(current_directory)
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _NONBLOCK,
            dir_fd=current_directory,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum_bytes:
            raise RepositoryShapeError(relative)
        content = bytearray()
        while len(content) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > maximum_bytes or len(content) != metadata.st_size:
            raise RepositoryShapeError(relative)
        final_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or final_metadata.st_size > maximum_bytes
            or (
                final_metadata.st_dev,
                final_metadata.st_ino,
                final_metadata.st_mode,
                final_metadata.st_size,
                final_metadata.st_mtime_ns,
                final_metadata.st_ctime_ns,
            )
            != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        ):
            raise RepositoryShapeError(relative)
        return bytes(content)
    except RepositoryShapeError:
        raise
    except OSError as error:
        raise RepositoryShapeError(relative) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)


def _core_backend_import_offenders(root: Path = ROOT) -> list[str]:
    offenders: list[str] = []
    for relative in _current_paths(root):
        if not relative.endswith(".py"):
            continue
        if relative.startswith("legacy/research-backend/"):
            continue
        if relative == "tests/consolidation/test_repository_shape.py":
            continue
        tree = ast.parse(_read_current_regular(root, relative), filename=relative)
        imports_backend = any(
            (
                isinstance(node, ast.Import)
                and any(
                    alias.name == "legacy"
                    or alias.name.startswith("legacy.")
                    or "research_backend" in alias.name
                    for alias in node.names
                )
            )
            or (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == "legacy"
                    or node.module.startswith("legacy.")
                    or "research_backend" in node.module
                )
            )
            for node in ast.walk(tree)
        )
        injects_backend_path = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "insert"}
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
            and node.func.value.attr == "path"
            and any(
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and "legacy/research-backend" in argument.value
                for argument in node.args
            )
            for node in ast.walk(tree)
        )
        if imports_backend or injects_backend_path:
            offenders.append(relative)
    return offenders


def test_repository_has_one_standalone_git_directory() -> None:
    root_git = ROOT / ".git"
    assert root_git.is_dir()
    assert not root_git.is_symlink()

    nested_git: list[str] = []
    for directory, names, files in os.walk(ROOT, followlinks=False):
        current = Path(directory)
        if current == ROOT and ".git" in names:
            names.remove(".git")
        elif ".git" in names or ".git" in files:
            nested_git.append((current / ".git").relative_to(ROOT).as_posix())
        for name in list(names):
            candidate = current / name
            if candidate.is_symlink():
                names.remove(name)

    assert nested_git == []


def test_index_has_no_gitlinks_or_tracked_symlinks() -> None:
    modes = _tracked_modes()
    gitlinks = sorted(path for path, mode in modes.items() if mode == "160000")
    symlinks = sorted(path for path, mode in modes.items() if mode == "120000")

    assert gitlinks == []
    assert symlinks == []
    assert REPOSITORY_LINKS.isdisjoint(modes)


def test_component_boundaries_have_local_locks_and_instructions() -> None:
    modes = _tracked_modes()

    assert REQUIRED_COMPONENT_FILES <= modes.keys()
    for relative in REQUIRED_COMPONENT_FILES:
        metadata = (ROOT / relative).lstat()
        assert stat.S_ISREG(metadata.st_mode), relative


def test_postgres_template_uses_a_non_forbidden_secret_free_path() -> None:
    modes = _tracked_modes()

    assert FORBIDDEN_POSTGRES_TEMPLATE not in modes
    assert not os.path.lexists(ROOT / FORBIDDEN_POSTGRES_TEMPLATE)
    assert modes.get(POSTGRES_TEMPLATE) == "100644"
    assert stat.S_ISREG((ROOT / POSTGRES_TEMPLATE).lstat().st_mode)

    template = _read_current_regular(ROOT, POSTGRES_TEMPLATE).decode("utf-8")
    assignments = {
        key: value
        for line in template.splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", 1)]
    }
    sensitive_values = {
        key: value
        for key, value in assignments.items()
        if any(marker in key for marker in ("PASSWORD", "SECRET", "TOKEN", "KEY"))
    }
    assert sensitive_values == {"TRADING_DATABASE_PASSWORD": "replace-locally"}


def test_core_python_does_not_import_research_backend_in_process() -> None:
    assert _core_backend_import_offenders() == []


def test_makefile_exposes_safe_component_orchestration() -> None:
    makefile = _read_current_regular(ROOT, "Makefile").decode("utf-8")
    targets = {
        match.group(1)
        for match in re.finditer(
            r"^([A-Za-z0-9_-]+)[ \t]*:[^=\n]*$", makefile, re.MULTILINE
        )
    }
    assert MAKE_TARGETS <= targets

    test_all_private = re.search(
        r"^test-all-private\s*:(.*)$", makefile, re.MULTILINE
    )
    assert test_all_private is not None
    prerequisites = set(test_all_private.group(1).split())
    assert prerequisites == {
        "audit",
        "check-d0-closure",
        "check-contracts",
        "check-secrets",
        "test",
        "test-backend",
        "test-dashboard",
        "typecheck-dashboard",
        "lint-dashboard",
    }
    assert "build-dashboard" not in prerequisites
    test_recipe = re.search(r"^test:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert test_recipe is not None
    normalized_test_recipe = test_recipe.group(1).replace("\\\n", " ")
    assert "--ignore=tests/runtime_release" not in normalized_test_recipe
    assert '-m "not runtime_postgres and not host_coupled"' in normalized_test_recipe

    runtime_release_recipe = re.search(
        r"^test-runtime-release:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert runtime_release_recipe is not None
    assert '-m "not host_coupled" tests/runtime_release' in runtime_release_recipe.group(1)

    host_release_recipe = re.search(
        r"^test-runtime-release-host:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert host_release_recipe is not None
    assert '-m "host_coupled" tests/runtime_release' in host_release_recipe.group(1)

    ci_gate = re.search(r"^ci\s*:(.*)$", makefile, re.MULTILINE)
    assert ci_gate is not None
    assert ci_gate.group(1).split() == ["ci-portable"]

    ci_private_gate = re.search(r"^ci-private\s*:(.*)$", makefile, re.MULTILINE)
    assert ci_private_gate is not None
    assert ci_private_gate.group(1).split() == []

    assert "uv export --frozen --no-dev" in makefile
    assert "uv export --frozen --all-groups" in makefile
    assert "uv export --frozen --extra test" in makefile
    assert "npm audit --omit=dev" in makefile
    assert "npm audit --audit-level=moderate" in makefile

    logical_makefile = makefile.replace("\\\n", " ")
    phony = re.search(r"^\.PHONY\s*:(.*)$", logical_makefile, re.MULTILINE)
    assert phony is not None
    assert MAKE_TARGETS <= set(phony.group(1).split())

    forbidden_recipe_fragments = {
        "npm ci",
        "uvicorn",
        "next dev",
        "next start",
        "alembic upgrade",
        "systemctl",
        "psql",
    }
    assert not {
        fragment for fragment in forbidden_recipe_fragments if fragment in makefile
    }


def test_portable_ci_targets_are_explicit_and_retain_all_non_runtime_gates() -> None:
    makefile = _read_current_regular(ROOT, "Makefile").decode("utf-8")
    targets = {
        match.group(1): match.group(2).split()
        for match in re.finditer(
            r"^([A-Za-z0-9_-]+)[ \t]*:([^=\n]*)$", makefile, re.MULTILINE
        )
    }
    portable_targets = {
        "audit-portable",
        "test-portable-embedded-proof",
        "test-all-portable-private",
        "test-all-portable-topology-private",
        "ci-portable",
        "ci-portable-private",
        "check-test-governance-topology",
    }
    logical_makefile = makefile.replace("\\\n", " ")
    phony = re.search(r"^\.PHONY\s*:(.*)$", logical_makefile, re.MULTILINE)

    assert phony is not None
    assert portable_targets <= set(phony.group(1).split())
    assert targets["test-portable-embedded-proof"] == ["test"]
    assert (
        "test-portable-embedded-proof: ROOT_PYTEST_ARGS := --portable-embedded-proof"
        in makefile
    )
    assert targets["test-all-portable-private"] == [
        "audit-portable",
        "check-d0-closure",
        "check-contracts",
        "check-secrets",
        "test-portable-embedded-proof",
        "test-backend",
        "test-dashboard",
        "typecheck-dashboard",
        "lint-dashboard",
    ]
    assert targets["ci-portable"] == []
    assert targets["ci-portable-private"] == []
    assert targets["test-all-portable-topology-private"] == [
        "audit-portable",
        "check-d0-closure",
        "check-contracts",
        "check-secrets",
        "test-backend",
        "test-dashboard",
        "typecheck-dashboard",
        "lint-dashboard",
        "ci-portable-topology",
    ]

    strict_aggregate = targets["test-all-private"]
    assert "test" in strict_aggregate
    assert "test-portable-embedded-proof" not in strict_aggregate
    root_recipe = re.search(r"^test:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    backend_recipe = re.search(r"^test-backend:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    dashboard_recipe = re.search(r"^test-dashboard:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert root_recipe is not None
    assert backend_recipe is not None
    assert dashboard_recipe is not None
    assert root_recipe.group(1).count("$(ROOT_PYTEST_ARGS)") == 1
    assert "--portable-embedded-proof" not in backend_recipe.group(1)
    assert "--portable-embedded-proof" not in dashboard_recipe.group(1)
    assert "export PYTEST_ADDOPTS" not in makefile

    portable_private_recipe = re.search(
        r"^ci-portable-private:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert portable_private_recipe is not None
    assert portable_private_recipe.group(1).strip() == (
        "$(MAKE) ci-common-private ci-portable-topology check-portable-defect-closure check-p0-baseline check-test-governance-topology "
        "check-p0-ci-closure artifact-firewall-check audit-delivery-contract"
    )
    common_private_recipe = re.search(
        r"^ci-common-private:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert common_private_recipe is not None
    assert common_private_recipe.group(1).count("prepare-root-test-install") == 1
    for gate in ("audit-portable", "check-d0-closure", "check-contracts", "check-secrets", "test-backend", "test-dashboard", "typecheck-dashboard", "lint-dashboard", "build-dashboard", "audit-python-source", "audit-dependencies"):
        assert gate in common_private_recipe.group(1)

    portable_recipe = re.search(
        r"^ci-portable:\n((?:\t.*\n)+)", makefile, re.MULTILINE
    )
    assert portable_recipe is not None
    portable_recipe_text = portable_recipe.group(1)
    portable_make_targets = re.findall(
        r"\$\(MAKE\)\s+([A-Za-z0-9_-]+)", portable_recipe_text
    )
    assert portable_make_targets == ["ci-portable-private"]
    assert "ci" not in portable_make_targets
    assert "ci-private" not in portable_make_targets
    assert (
        'ci_tmpdir=$$(mktemp -d "$${RUNNER_TEMP:?}/'
        'trading-agent-ci-portable.XXXXXXXXXX")'
        in portable_recipe_text
    )
    assert "/tmp/trading-agent-ci-portable.XXXXXXXXXX" not in portable_recipe_text
    assert 'chmod 0700 "$$ci_tmpdir"' in portable_recipe_text
    assert 'test "$$(stat -c \'%u:%a\' -- "$$ci_tmpdir")" = "$$(id -u):700"' in portable_recipe_text
    assert "cleanup_ci_tmpdir() {" in portable_recipe_text
    assert 'find -P "$$ci_tmpdir" -xdev -type d -exec chmod u+rwx -- {} +' in portable_recipe_text
    assert "trap 'cleanup_ci_tmpdir' EXIT" in portable_recipe_text
    assert portable_recipe_text.count(
        'TMPDIR="$$ci_tmpdir" TEMP="$$ci_tmpdir" TMP="$$ci_tmpdir"'
    ) == 1

    workflow = _read_current_regular(ROOT, ".github/workflows/foundation.yml").decode("utf-8")
    workflow_run_values = re.findall(r"^\s*run:\s*([^\n#]+?)\s*$", workflow, re.MULTILINE)
    make_run_values = [value for value in workflow_run_values if value.startswith("make ")]
    assert make_run_values == ["make ci-portable NONINTERACTIVE=1"]
    assert "make ci" not in make_run_values


def test_foundation_workflow_delegates_to_the_canonical_local_ci_gate() -> None:
    workflow = _read_current_regular(ROOT, ".github/workflows/foundation.yml").decode("utf-8")
    workflow_run_values = re.findall(r"^\s*run:\s*([^\n#]+?)\s*$", workflow, re.MULTILINE)
    make_run_values = [value for value in workflow_run_values if value.startswith("make ")]

    assert "uses: actions/checkout@v4\n        with:\n          fetch-depth: 0" in workflow
    assert make_run_values == ["make ci-portable NONINTERACTIVE=1"]
    assert "make ci" not in make_run_values
    assert "run: make test-all" not in workflow
    assert "run: make build-dashboard" not in workflow
    assert "pip-audit" not in workflow
    assert "npm audit" not in workflow
    assert "bandit" not in workflow


@pytest.mark.parametrize(
    "workflow",
    ["foundation.yml", "host-authority.yml"],
)
def test_hosted_workflow_has_exactly_one_node_22_runtime(workflow: str) -> None:
    """Break caught: a hosted workflow duplicates or downgrades its Node pin."""
    content = _read_current_regular(
        ROOT, f".github/workflows/{workflow}",
    ).decode("utf-8")
    node_versions = re.findall(
        r"^[ \t]+node-version:[ \t]*([^\s#]+)[ \t]*(?:#.*)?$",
        content,
        re.MULTILINE,
    )

    assert node_versions == ["22"]


def _init_shape_fixture(repository: Path) -> None:
    repository.mkdir()
    subprocess.run(
        ["git", "-C", str(repository), "init", "-q"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Shape Test"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "git", "-C", str(repository), "config", "user.email",
            "shape@example.invalid",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    (repository / "core.py").write_text("SAFE = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "core.py"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "fixture"],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def test_core_import_scan_detects_unstaged_tracked_violation(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init_shape_fixture(repository)
    assert _core_backend_import_offenders(repository) == []

    (repository / "core.py").write_text(
        "import legacy.research_backend\n", encoding="utf-8",
    )

    assert _core_backend_import_offenders(repository) == ["core.py"]


def test_core_import_scan_detects_untracked_nonignored_violation(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _init_shape_fixture(repository)
    (repository / "new_core.py").write_text(
        "from legacy import research_backend\n", encoding="utf-8",
    )

    assert _core_backend_import_offenders(repository) == ["new_core.py"]


def test_current_content_reader_rejects_symlink_and_nonregular(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _init_shape_fixture(repository)
    outside = tmp_path / "outside.py"
    outside.write_text("import legacy.research_backend\n", encoding="utf-8")
    (repository / "linked.py").symlink_to(outside)
    linked_directory = tmp_path / "linked-directory"
    linked_directory.mkdir()
    (linked_directory / "nested.py").write_text("SAFE = True\n", encoding="utf-8")
    (repository / "parent").symlink_to(linked_directory, target_is_directory=True)
    (repository / "directory.py").mkdir()

    with pytest.raises(RepositoryShapeError):
        _read_current_regular(repository, "linked.py")
    with pytest.raises(RepositoryShapeError):
        _read_current_regular(repository, "directory.py")
    with pytest.raises(RepositoryShapeError):
        _read_current_regular(repository, "parent/nested.py")
    with pytest.raises(RepositoryShapeError):
        _read_current_regular(repository, "core.py", maximum_bytes=4)
    with pytest.raises(RepositoryShapeError):
        _read_current_regular(repository, "missing.py")


def test_current_content_reader_rejects_same_size_in_place_read_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    _init_shape_fixture(repository)
    target = repository / "racing.py"
    violating = b"import legacy.research_backend\n"
    safe = (b"#" * (len(violating) - 1)) + b"\n"
    assert len(safe) == len(violating)
    target.write_bytes(safe)
    initial = target.stat()
    real_read = os.read
    observed: list[bytes] = []

    def race_after_safe_read(descriptor: int, amount: int) -> bytes:
        chunk = real_read(descriptor, amount)
        if chunk and not observed:
            observed.append(chunk)
            target.write_bytes(violating)
            os.utime(
                target,
                ns=(initial.st_atime_ns, initial.st_mtime_ns + 2_000_000_000),
            )
        return chunk

    monkeypatch.setattr(os, "read", race_after_safe_read)

    with pytest.raises(RepositoryShapeError):
        _read_current_regular(repository, "racing.py")
    assert observed == [safe]
    assert target.read_bytes() == violating
