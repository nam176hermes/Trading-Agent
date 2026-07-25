from __future__ import annotations

import ast
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib

import pytest

from packages.runtime_release.v2 import (
    PAPER_APPLICATION_SOURCE_MAPPING,
    PAPER_ARTIFACT_CLASS,
    PAPER_BACKEND_ENTRYPOINT,
    PAPER_BACKEND_SOURCE_MAPPING,
    PAPER_BACKEND_SOURCE_PATHS,
    PAPER_FORCED_ENVIRONMENT,
    PAPER_PYTHON_RUNTIME_PROVENANCE,
    PAPER_RUNTIME_MANIFEST,
    ReleaseAuthorityV2Error,
    construct_paper_application_artifact,
    construct_paper_backend_artifact,
    construct_python_runtime,
    construct_python_runtime_archive,
    inspect_paper_application_artifact,
    inspect_paper_backend_artifact,
    inspect_python_runtime,
    paper_command_manifest,
    python_runtime_core_sha256,
    verify_python_runtime_execution,
)


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_SOURCE = ROOT
INSTALL_ROOT = Path("/opt/trading-agent-v2/releases/" + "a" * 40)


FORBIDDEN_ARTIFACT_PATHS = {
    "main.py",
    "live_execution_policy.py",
    "execute_live.py",
    "broker.py",
    "trading_agent.py",
    "exchange/adapter.py",
    "exchange/ccxt_bridge.py",
    "exchange/executor.py",
    "exchange/secrets.py",
}
FORBIDDEN_SYMBOLS = {
    "create_order",
    "place_order",
    "execute_live",
    "get_exchange",
    "get_exchange_credentials",
    "load_dotenv",
    "mode_file",
    "set_mode",
}


def _files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _python_surface(root: Path) -> tuple[set[str], set[str], set[str]]:
    imports: set[str] = set()
    definitions: set[str] = set()
    calls: set[str] = set()
    for relative in sorted(_files(root)):
        if not relative.endswith(".py"):
            continue
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions.add(node.name)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
    return imports, definitions, calls


def test_paper_backend_repository_mapping_stays_outside_imported_snapshot() -> None:
    mapping = dict(PAPER_BACKEND_SOURCE_MAPPING)

    assert tuple(mapping) == PAPER_BACKEND_SOURCE_PATHS
    assert mapping["job_attribution.py"] == "legacy/research-backend/job_attribution.py"
    assert mapping["research_semantics.py"] == (
        "packages/runtime_release/paper_backend/research_semantics.py"
    )
    assert mapping["paper_main.py"] == "packages/runtime_release/paper_backend/paper_main.py"
    assert mapping["paper_runtime_manifest.json"] == (
        "packages/runtime_release/paper_backend/paper_runtime_manifest.json"
    )
    assert all((ROOT / source).is_file() for source in mapping.values())


def test_paper_python_runtime_provenance_is_exact_and_code_owned() -> None:
    assert PAPER_PYTHON_RUNTIME_PROVENANCE == {
        "identity": "CPython 3.11.15",
        "normalized_core_sha256": (
            "39632162b32a97b4ccd3f3dd5f79d0735137f9247401835d1287b433dc83dcf7"
        ),
        "upstream_archive": (
            "cpython-3.11.15+20260414-x86_64-unknown-linux-gnu-"
            "install_only_stripped.tar.gz"
        ),
        "upstream_archive_sha256": (
            "b702a19b26cbd007abf9ccbaa45dfdff99e9dbd646d89c9f3c9bb7b501aea44f"
        ),
    }
    assert PAPER_RUNTIME_MANIFEST["python_runtime"] == PAPER_PYTHON_RUNTIME_PROVENANCE


def _tiny_runtime_source(root: Path, launcher: str = "#!/bin/sh\nexit 0\n") -> Path:
    source = root / "source-runtime"
    (source / "bin").mkdir(parents=True)
    (source / "lib/python3.11").mkdir(parents=True)
    (source / "bin/python3.11").write_text(launcher, encoding="utf-8")
    (source / "bin/python3.11").chmod(0o755)
    (source / "lib/python3.11/os.py").write_text("name = 'posix'\n", encoding="utf-8")
    return source


def _construct_tiny_runtime(tmp_path: Path) -> tuple[Path, str]:
    source = _tiny_runtime_source(tmp_path)
    digest = python_runtime_core_sha256(source, allow_internal_source_links=True)
    runtime = tmp_path / "runtime"
    construct_python_runtime(source, runtime, expected_core_sha256=digest)
    return runtime, digest


def _runtime_archive(
    path: Path,
    members: list[tuple[tarfile.TarInfo, bytes | None]],
) -> str:
    with tarfile.open(path, "w:gz") as archive:
        for member, raw in members:
            archive.addfile(member, io.BytesIO(raw) if raw is not None else None)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular_member(
    name: str,
    raw: bytes,
    mode: int = 0o644,
) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(raw)
    member.mode = mode
    return member, raw


def test_python_runtime_archive_is_digest_bound_and_projects_runtime(tmp_path: Path) -> None:
    source = _tiny_runtime_source(tmp_path)
    core_sha256 = python_runtime_core_sha256(source, allow_internal_source_links=True)
    archive = tmp_path / "runtime.tar.gz"
    archive_sha256 = _runtime_archive(
        archive,
        [
            _regular_member(
                "python/bin/python3.11",
                (source / "bin/python3.11").read_bytes(),
                0o755,
            ),
            _regular_member(
                "python/lib/python3.11/os.py",
                (source / "lib/python3.11/os.py").read_bytes(),
            ),
        ],
    )
    destination = tmp_path / "runtime"

    evidence = construct_python_runtime_archive(
        archive,
        destination,
        expected_archive_sha256=archive_sha256,
        expected_core_sha256=core_sha256,
    )

    assert evidence["archive_sha256"] == archive_sha256
    assert evidence["normalized_core_sha256"] == core_sha256
    assert inspect_python_runtime(
        destination,
        require_empty_site_packages=True,
        expected_core_sha256=core_sha256,
    )["core_sha256"] == core_sha256


@pytest.mark.parametrize("attack", ["noncanonical_path", "pax_header"])
def test_python_runtime_archive_rejects_ambiguous_tar_metadata(
    tmp_path: Path,
    attack: str,
) -> None:
    source = _tiny_runtime_source(tmp_path)
    core_sha256 = python_runtime_core_sha256(source, allow_internal_source_links=True)
    executable_name = (
        "python//bin/python3.11"
        if attack == "noncanonical_path"
        else "python/bin/python3.11"
    )
    executable, executable_raw = _regular_member(
        executable_name,
        (source / "bin/python3.11").read_bytes(),
        0o755,
    )
    if attack == "pax_header":
        executable.pax_headers = {"comment": "ambiguous-runtime-metadata"}
    archive = tmp_path / f"{attack}.tar.gz"
    archive_sha256 = _runtime_archive(
        archive,
        [
            (executable, executable_raw),
            _regular_member(
                "python/lib/python3.11/os.py",
                (source / "lib/python3.11/os.py").read_bytes(),
            ),
        ],
    )
    destination = tmp_path / "runtime"

    with pytest.raises(ReleaseAuthorityV2Error):
        construct_python_runtime_archive(
            archive,
            destination,
            expected_archive_sha256=archive_sha256,
            expected_core_sha256=core_sha256,
        )

    assert not destination.exists()


def test_python_runtime_archive_rejects_wrong_digest_before_projection(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    _runtime_archive(
        archive,
        [_regular_member("python/bin/python3.11", b"#!/bin/sh\nexit 0\n", 0o755)],
    )
    destination = tmp_path / "runtime"

    with pytest.raises(ReleaseAuthorityV2Error):
        construct_python_runtime_archive(
            archive,
            destination,
            expected_archive_sha256="0" * 64,
            expected_core_sha256="0" * 64,
        )

    assert not destination.exists()


@pytest.mark.parametrize("attack", ["traversal", "escaping_symlink", "hardlink"])
def test_python_runtime_archive_rejects_unsafe_member(tmp_path: Path, attack: str) -> None:
    archive = tmp_path / f"{attack}.tar.gz"
    members: list[tuple[tarfile.TarInfo, bytes | None]] = [
        _regular_member("python/bin/python3.11", b"#!/bin/sh\nexit 0\n", 0o755),
    ]
    if attack == "traversal":
        members.append(_regular_member("python/../../escape", b"escape\n"))
    else:
        member = tarfile.TarInfo("python/lib/python3.11/attack")
        member.type = tarfile.SYMTYPE if attack == "escaping_symlink" else tarfile.LNKTYPE
        member.linkname = "../../../../outside"
        members.append((member, None))
    archive_sha256 = _runtime_archive(archive, members)

    with pytest.raises(ReleaseAuthorityV2Error):
        construct_python_runtime_archive(
            archive,
            tmp_path / "runtime",
            expected_archive_sha256=archive_sha256,
            expected_core_sha256="0" * 64,
        )

    assert not (tmp_path / "escape").exists()


def test_python_runtime_projector_rejects_external_source_symlink(tmp_path: Path) -> None:
    source = _tiny_runtime_source(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("unsafe = True\n", encoding="utf-8")
    (source / "lib/python3.11/external.py").symlink_to(outside)

    with pytest.raises(ReleaseAuthorityV2Error):
        construct_python_runtime(
            source,
            tmp_path / "runtime",
            expected_core_sha256="0" * 64,
        )


@pytest.mark.parametrize("mutation", ["stdlib", "launcher", "provenance", "hardlink"])
def test_python_runtime_inspector_rejects_runtime_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    runtime, digest = _construct_tiny_runtime(tmp_path)
    if mutation == "stdlib":
        (runtime / "lib/python3.11/os.py").write_text("unsafe = True\n", encoding="utf-8")
    elif mutation == "launcher":
        (runtime / "bin/pip").write_text("#!/bin/sh\n", encoding="utf-8")
    elif mutation == "provenance":
        (runtime / "runtime-provenance.json").write_text("{}\n", encoding="utf-8")
    else:
        os.link(runtime / "lib/python3.11/os.py", runtime / "lib/python3.11/alias.py")

    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_python_runtime(
            runtime,
            expected_core_sha256=digest,
            require_empty_site_packages=True,
        )


def test_fake_python_identity_is_not_executed_before_code_owned_core_check(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "executed"
    launcher = f"#!/bin/sh\nprintf ran >{marker}\n"
    source = _tiny_runtime_source(tmp_path, launcher)
    digest = python_runtime_core_sha256(source, allow_internal_source_links=True)
    runtime = tmp_path / "runtime"
    construct_python_runtime(source, runtime, expected_core_sha256=digest)

    with pytest.raises(ReleaseAuthorityV2Error):
        verify_python_runtime_execution(runtime)
    assert not marker.exists()


def test_built_paper_backend_has_no_live_authority_surface(tmp_path: Path) -> None:
    artifact = tmp_path / "paper-backend"

    construct_paper_backend_artifact(REPOSITORY_SOURCE, artifact)
    evidence = inspect_paper_backend_artifact(
        artifact,
        paper_command_manifest(INSTALL_ROOT),
    )

    files = _files(artifact)
    imports, definitions, calls = _python_surface(artifact)
    assert evidence["decision"] == "GO"
    assert evidence["artifact_class"] == PAPER_ARTIFACT_CLASS
    assert evidence["entrypoint"] == PAPER_BACKEND_ENTRYPOINT
    assert files == set(PAPER_BACKEND_SOURCE_PATHS)
    assert files.isdisjoint(FORBIDDEN_ARTIFACT_PATHS)
    assert not {"ccxt", "alpaca", "broker", "execute_live", "live_execution_policy"}.intersection(
        name.split(".")[0] for name in imports
    )
    assert definitions.isdisjoint(FORBIDDEN_SYMBOLS)
    assert calls.isdisjoint(FORBIDDEN_SYMBOLS)
    assert evidence["forced_environment"] == PAPER_FORCED_ENVIRONMENT


@pytest.mark.parametrize(
    "unsafe_import",
    [
        "import live_data\n",
        "import requests\n",
        "dynamic = __import__('live_data')\n",
    ],
)
def test_paper_artifact_rejects_imports_outside_closed_module_set(
    tmp_path: Path,
    unsafe_import: str,
) -> None:
    artifact = tmp_path / "paper-backend"
    construct_paper_backend_artifact(REPOSITORY_SOURCE, artifact)
    target = artifact / "research_semantics.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n" + unsafe_import,
        encoding="utf-8",
    )

    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_paper_backend_artifact(artifact, paper_command_manifest(INSTALL_ROOT))


def test_built_artifact_rejects_every_forbidden_live_surface(tmp_path: Path) -> None:
    for relative in sorted(FORBIDDEN_ARTIFACT_PATHS):
        artifact = tmp_path / relative.replace("/", "-")
        construct_paper_backend_artifact(REPOSITORY_SOURCE, artifact)
        target = artifact / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def place_order():\n    return 'live'\n", encoding="utf-8")

        with pytest.raises(ReleaseAuthorityV2Error):
            inspect_paper_backend_artifact(artifact, paper_command_manifest(INSTALL_ROOT))


def test_paper_artifact_rejects_live_module_hidden_in_venv_site_packages(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "paper-backend"
    construct_paper_backend_artifact(REPOSITORY_SOURCE, artifact)
    injected = artifact / ".venv/lib/python3.11/site-packages/execute_live.py"
    injected.parent.mkdir(parents=True)
    injected.write_text(
        "def create_order():\n    return 'live'\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_paper_backend_artifact(artifact, paper_command_manifest(INSTALL_ROOT))


def test_paper_artifact_rejects_system_site_package_inheritance(tmp_path: Path) -> None:
    artifact = tmp_path / "paper-backend"
    construct_paper_backend_artifact(REPOSITORY_SOURCE, artifact)
    config = artifact / ".venv/pyvenv.cfg"
    config.parent.mkdir(parents=True)
    config.write_text(
        "home = /usr/bin\ninclude-system-site-packages = true\nversion = 3.11.0\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_paper_backend_artifact(artifact, paper_command_manifest(INSTALL_ROOT))


def test_paper_application_mapping_is_a_positive_allowlist_without_live_surfaces() -> None:
    stage_paths = [artifact for artifact, _ in PAPER_APPLICATION_SOURCE_MAPPING]
    source_paths = [source for _, source in PAPER_APPLICATION_SOURCE_MAPPING]

    assert stage_paths == sorted(stage_paths, key=os.fsencode)
    assert len(stage_paths) == len(set(stage_paths))
    assert len(source_paths) == len(set(source_paths))
    assert "pyproject.toml" in stage_paths
    assert "uv.lock" in stage_paths
    assert "services/job_worker/main.py" in stage_paths
    assert "services/job_worker/command_registry.py" in stage_paths
    assert not any(path.startswith("apps/dashboard/") for path in source_paths)
    assert not any(path.startswith("apps/control_api/") for path in source_paths)
    assert not any("migrate" in path for path in source_paths)
    assert not any("asset_registry" in path for path in source_paths)


def test_paper_application_dependency_closure_excludes_migration_packages() -> None:
    mapping = dict(PAPER_APPLICATION_SOURCE_MAPPING)
    assert mapping["uv.lock"] == "packages/runtime_release/paper_application/uv.lock"
    pyproject = tomllib.loads((REPOSITORY_SOURCE / mapping["pyproject.toml"]).read_text(encoding="utf-8"))
    direct_dependencies = {
        dependency.split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].lower()
        for dependency in pyproject["project"]["dependencies"]
    }
    lock = (REPOSITORY_SOURCE / mapping["uv.lock"]).read_text(encoding="utf-8").lower()

    assert direct_dependencies == {
        "fastapi",
        "psycopg",
        "psycopg-pool",
        "pydantic",
        "uvicorn",
    }
    assert set(pyproject["tool"]["uv"]["constraint-dependencies"]) == {
        "annotated-types==0.7.0",
        "anyio==4.14.1",
    }
    for forbidden in ("alembic", "mako", "sqlalchemy", "greenlet"):
        assert f'name = "{forbidden}"' not in lock


def test_paper_application_projection_is_exact_and_rejects_injected_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "paper-application"

    construct_paper_application_artifact(REPOSITORY_SOURCE, destination)

    expected = {artifact for artifact, _ in PAPER_APPLICATION_SOURCE_MAPPING}
    assert _files(destination) == expected
    inspect_paper_application_artifact(destination)

    injected = destination / "apps/control_api/trading_control/migrate.py"
    injected.parent.mkdir(parents=True)
    injected.write_text("def apply(): pass\n", encoding="utf-8")
    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_paper_application_artifact(destination)


def test_paper_application_projection_has_a_closed_import_graph(tmp_path: Path) -> None:
    destination = tmp_path / "paper-application"
    construct_paper_application_artifact(REPOSITORY_SOURCE, destination)
    modules = (
        "packages.job_contracts.transitions",
        "apps.job_api.app",
        "services.job_store.worker_repository",
        "services.job_worker.process_runner",
        "services.job_worker.worker",
    )
    probe = (
        "import importlib, pathlib, sys; "
        "sys.path.insert(0, str(pathlib.Path.cwd())); "
        f"[importlib.import_module(name) for name in {modules!r}]"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe],
        cwd=destination,
        env={
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_paper_application_projection_is_independent_of_ambient_umask(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "paper-application"
    previous_umask = os.umask(0o002)
    try:
        construct_paper_application_artifact(REPOSITORY_SOURCE, destination)
    finally:
        os.umask(previous_umask)

    assert all(path.stat().st_mode & 0o022 == 0 for path in destination.rglob("*"))
    inspect_paper_application_artifact(destination)


def test_paper_application_inspector_delegates_validated_runtime_symlinks(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "paper-application"
    construct_paper_application_artifact(REPOSITORY_SOURCE, artifact)
    runtime_source = _tiny_runtime_source(tmp_path / "runtime-source")
    source_core_sha256 = python_runtime_core_sha256(runtime_source)
    construct_python_runtime(
        runtime_source,
        artifact / ".venv",
        expected_core_sha256=source_core_sha256,
    )
    runtime_link = artifact / ".venv/lib/os-link.py"
    runtime_link.symlink_to("python3.11/os.py")
    assert runtime_link.is_symlink()
    runtime_core_sha256 = python_runtime_core_sha256(
        artifact / ".venv",
        allow_internal_source_links=True,
    )

    inspect_paper_application_artifact(
        artifact,
        test_expected_python_runtime_core_sha256=runtime_core_sha256,
    )


@pytest.mark.parametrize("package", ["alembic", "Mako", "SQLAlchemy", "greenlet"])
def test_paper_application_inspector_rejects_migration_site_packages(
    tmp_path: Path,
    package: str,
) -> None:
    artifact = tmp_path / "paper-application"
    construct_paper_application_artifact(REPOSITORY_SOURCE, artifact)
    runtime_source = _tiny_runtime_source(tmp_path / "runtime-source")
    runtime_core_sha256 = python_runtime_core_sha256(runtime_source)
    construct_python_runtime(
        runtime_source,
        artifact / ".venv",
        expected_core_sha256=runtime_core_sha256,
    )
    injected = artifact / ".venv/lib/python3.11/site-packages" / package
    injected.mkdir(mode=0o700)
    (injected / "__init__.py").write_text("# forbidden\n", encoding="utf-8")

    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_paper_application_artifact(
            artifact,
            test_expected_python_runtime_core_sha256=runtime_core_sha256,
        )


def test_whole_stage_builder_and_composer_have_no_dashboard_or_node_surface() -> None:
    builder = (REPOSITORY_SOURCE / "ops/release-v2/build-stage.sh").read_text(
        encoding="utf-8"
    )
    verifier = (REPOSITORY_SOURCE / "ops/release-v2/verify-stage.py").read_text(
        encoding="utf-8"
    )
    release_source = (REPOSITORY_SOURCE / "packages/runtime_release/v2.py").read_text(
        encoding="utf-8"
    )

    for forbidden in (
        "$STAGE/dashboard",
        "npm_config_offline",
        '"$NPM" ci',
        '"$NPM" run build',
        "--node-executable",
        "--node-identity",
        "dashboard_node",
    ):
        assert forbidden not in builder
        assert forbidden not in verifier
        assert forbidden not in release_source


def test_whole_stage_builder_uses_lock_bound_wheelhouse_not_a_broad_uv_cache() -> None:
    builder = (REPOSITORY_SOURCE / "ops/release-v2/build-stage.sh").read_text(
        encoding="utf-8"
    )

    assert "--wheelhouse" in builder
    assert "offline_wheelhouse.py" in builder
    assert "verify_offline_wheelhouse" in builder
    assert "--find-links" in builder
    assert "--no-index" in builder
    assert "--no-cache" in builder
    assert "PINNED_UV_SHA256='cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4'" in builder
    assert "project-pinned-uv" in builder
    assert '"$BUILD_UV" export' in builder
    assert '"$BUILD_UV" pip sync' in builder
    assert '"$UV" export' not in builder
    assert '"$UV" pip sync' not in builder
    assert "canonicalize_installed_site_packages" in builder
    assert "dependency-manifest.json" in builder
    assert '--python "$APP_VENV/bin/python3.11"' in builder
    assert '"$APP_VENV/bin/python3.11" -I -B -c' in builder
    assert '"$BACKEND_VENV/bin/python3.11" -I -B -c' in builder
    assert "--require-hashes" in builder
    assert "--only-binary=:all:" in builder
    assert '"$UV" sync' not in builder
    for forbidden in ("--uv-cache", "UV_CACHE=", "UV_CACHE_DIR=", 'cp -a --reflink=never -- "$UV_CACHE/."'):
        assert forbidden not in builder


def test_paper_command_catalog_is_fixed_snapshot_entrypoint_only() -> None:
    document = paper_command_manifest(INSTALL_ROOT)

    assert document["schema_version"] == 3
    assert len(document["commands"]) == 1
    command = document["commands"][0]
    assert command["job_type"] == "SNAPSHOT"
    assert command["argv"] == [
        str(INSTALL_ROOT / "backend/.venv/bin/python3.11"),
        "-I",
        "-B",
        PAPER_BACKEND_ENTRYPOINT,
    ]
    assert command["environment_policy"] == "CANONICAL_PAPER_CHILD_V1"
    serialized = repr(document).lower()
    assert "--mode" not in serialized
    assert "live" not in serialized
    assert "order" not in serialized
    assert "broker" not in serialized


@pytest.mark.parametrize(
    "argv_tail",
    [
        ["main.py", "--mode", "snapshot", "--research-only"],
        [PAPER_BACKEND_ENTRYPOINT, "--mode", "live"],
        ["execute_live.py"],
        ["broker.py", "place-order"],
    ],
)
def test_paper_artifact_rejects_noncanonical_command_exposure(
    tmp_path: Path,
    argv_tail: list[str],
) -> None:
    artifact = tmp_path / "paper-backend"
    construct_paper_backend_artifact(REPOSITORY_SOURCE, artifact)
    document = paper_command_manifest(INSTALL_ROOT)
    document["commands"][0]["argv"] = [
        str(INSTALL_ROOT / "backend/.venv/bin/python3.11"),
        "-I",
        "-B",
        *argv_tail,
    ]

    with pytest.raises(ReleaseAuthorityV2Error):
        inspect_paper_backend_artifact(artifact, document)
