from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable, cast

import pytest

import tests.foundation._package6_staging_fixture as staging_fixture
from tests.foundation._package6_staging_fixture import (
    Package6StagingLease,
    Package6StagingLeaseError,
    create_package6_staging_lease,
    package6_staging_lease,
)
import packages.runtime_release.staging_v2 as staging_v2
from packages.runtime_release.staging_v2 import (
    STAGING_SCOPE,
    build_staging_activation_v2,
    build_staging_release_authority_v2,
    canonical_digest,
    canonical_json_bytes,
)
from scripts.validate_package6_runtime_approval import (
    PACKAGE6_CUSTODIAN_SOURCE_PATHS,
    PACKAGE6_JOB_API_ENVIRONMENT_KEYS,
    PACKAGE6_SOURCE_BINDING_PATHS,
    PACKAGE6_WORKER_ENVIRONMENT_KEYS,
    Package6ApprovalContext,
    Package6ApprovalRejected,
    canonical_record_sha256,
    validate_package6_runtime_approval,
    validate_source_binding_files,
)


COMMIT = "7dd5336e050d255e6d75bf907dc792011f322b99"
TREE = "679847e0f3738d71d1ab130512d9fe285a9bcd80"
PG_APPROVAL = "a" * 64
FIXTURE = "b" * 64
CANONICAL_BINDINGS = PACKAGE6_SOURCE_BINDING_PATHS
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class StagingMaterial:
    private: Path
    stage: Path
    application: Path
    application_python: Path
    backend_python: Path
    authority_path: Path
    authority_sha256: str
    fixture_path: Path
    fixture_sha256: str
    safety_sha256: str
    semantic_sha256: str
    generated: datetime
    expires: datetime
    application_sha256: str
    backend_sha256: str
    command_sha256: str
    stage_sha256: str


def _object(document: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], document[key])


def _objects(document: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], document[key])


def _write_fixed(path: Path, value: object, mode: int = 0o444) -> str:
    raw = canonical_json_bytes(value)
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(mode)
    return hashlib.sha256(raw).hexdigest()


def _staging_material(root: Path, *, lease: Package6StagingLease) -> StagingMaterial:
    lease.assert_valid()
    private = lease.root
    for path in private.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
    stage = private / "stage"
    application = stage / "application"
    backend = stage / "backend"
    for component in (application, backend):
        (component / ".venv/bin").mkdir(parents=True, exist_ok=True)
    application_python = application / ".venv/bin/python3.11"
    backend_python = backend / ".venv/bin/python3.11"
    for path, raw in (
        (application_python, b"application-python\n"),
        (backend_python, b"backend-python\n"),
        (application / "app.py", b"PAPER = True\n"),
        (backend / "paper_main.py", b"PAPER = True\n"),
    ):
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(raw)
    for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.name == "python3.11" else 0o444)
    stage.chmod(0o555)

    runtime = private / "runtime"
    semantic = private / "semantic"
    authority = private / "authority"
    for path in (runtime, semantic, authority):
        path.mkdir(mode=0o700, exist_ok=True)
    (semantic / "input").mkdir(mode=0o711, exist_ok=True)
    (semantic / "input").chmod(0o711)
    for name in ("artifacts", "reports", "scratch", "signals"):
        (runtime / name).mkdir(mode=0o700, exist_ok=True)
    generated = NOW
    expires = generated + timedelta(minutes=30)
    timestamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
    safety = {
        "effective_mode": "PAPER",
        "expires_at": timestamp(expires),
        "exporter_commit": COMMIT,
        "generated_at": timestamp(generated),
        "kill_switch_state": "INACTIVE",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "requested_mode": "PAPER",
        "schema_version": 1,
        "source_fingerprint": "d" * 64,
    }
    safety_path = runtime / "safety-state.json"
    safety_sha256 = _write_fixed(safety_path, safety, 0o600)
    semantic_path = semantic / "active.json"
    semantic_sha256 = _write_fixed(
        semantic_path,
        {
            "classification": "PACKAGE6_PROVIDER_FREE_SEMANTIC",
            "schema_version": 1,
            "source_commit": COMMIT,
        },
    )
    runtime_paths = {
        "artifact_root": runtime / "artifacts",
        "reports_root": runtime / "reports",
        "safety_snapshot": safety_path,
        "scratch_root": runtime / "scratch",
        "semantic_authority": semantic_path,
        "semantic_input_root": semantic / "input",
        "signals_root": runtime / "signals",
    }
    static, _ = build_staging_release_authority_v2(
        stage,
        source_commit=COMMIT,
        source_tree=TREE,
        disposable_root=private,
        production_release_authority_sha256="e" * 64,
        runtime_paths=runtime_paths,
        generated_at=generated,
        expires_at=expires,
    )
    authority_path = authority / "release-authority-v2.json"
    authority_sha256 = _write_fixed(authority_path, static)
    fixture_path = authority / "fixture.json"
    fixture_sha256 = _write_fixed(
        fixture_path,
        {
            "schema_version": 1,
            "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "as_of": timestamp(generated),
            "assets": {
                "BTC": {
                    "current_price": 1,
                    "market_cap": 2,
                    "total_volume": 3,
                    "price_change_percentage_24h": 0,
                }
            },
        },
    )
    components = cast(dict[str, dict[str, str]], static["components"])
    return StagingMaterial(
        private=private,
        stage=stage,
        application=application,
        application_python=application_python,
        backend_python=backend_python,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
        fixture_path=fixture_path,
        fixture_sha256=fixture_sha256,
        safety_sha256=safety_sha256,
        semantic_sha256=semantic_sha256,
        generated=generated,
        expires=expires,
        application_sha256=components["application"]["artifact_set_sha256"],
        backend_sha256=components["backend"]["artifact_set_sha256"],
        command_sha256=canonical_digest(static["command_manifest"]),
        stage_sha256=cast(dict[str, str], static["stage"])["file_set_sha256"],
    )


def _record(root: Path, *, lease: Package6StagingLease) -> dict[str, object]:
    source_root = root / "source"
    source_root.mkdir(exist_ok=True)
    bindings = []
    for relative in CANONICAL_BINDINGS:
        binding = source_root / relative
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(f"# bound: {relative}\n", encoding="utf-8")
        binding.chmod(0o600)
        bindings.append(
            {"path": relative, "sha256": hashlib.sha256(binding.read_bytes()).hexdigest()}
        )
    native_source_set = []
    for relative in PACKAGE6_CUSTODIAN_SOURCE_PATHS:
        binding = source_root / relative
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(f"/* native bound: {relative} */\n", encoding="utf-8")
        binding.chmod(0o600)
        native_source_set.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(binding.read_bytes()).hexdigest(),
            }
        )
    material = _staging_material(root, lease=lease)
    fixture_path = material.fixture_path
    fixture_authority_path = material.private / "authority/fixture-authority.json"
    interpreter = material.application_python
    interpreter_sha256 = hashlib.sha256(interpreter.read_bytes()).hexdigest()
    document: dict[str, object] = {
        "record_kind": "PACKAGE6_PAPER_RUNTIME_APPROVAL",
        "schema_version": "3",
        "record_id": "PACKAGE6_RUNTIME_FOUNDATION_001",
        "scope": "PACKAGE6_PAPER_RUNTIME",
        "source": {"commit": COMMIT, "tree": TREE},
        "validity": {
            "approved_at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at_utc": (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "review": {
            "decision": "APPROVED",
            "operator_identity": "operator.example",
            "reviewer_identity": "reviewer.example",
            "runtime_greenlight": "APPROVE PACKAGE 6 RUNTIME",
        },
        "operations": [
            {
                "operation_id": "job-api.start",
                "action": "START",
                "component": "JOB_API",
                "argv": [str(interpreter), "-I", "-m", "apps.job_api.main"],
                "cwd": str(material.application),
                "bind_host": "127.0.0.1",
                "port": "8401",
                "executable_sha256": interpreter_sha256,
            },
            {
                "operation_id": "job-api.stop",
                "action": "STOP",
                "component": "JOB_API",
                "argv": [],
                "cwd": str(material.application),
                "bind_host": "127.0.0.1",
                "port": "8401",
                "executable_sha256": None,
            },
            {
                "operation_id": "worker.start",
                "action": "START",
                "component": "WORKER",
                "argv": [str(interpreter), "-I", "-m", "services.job_worker.main"],
                "cwd": str(material.application),
                "bind_host": None,
                "port": None,
                "executable_sha256": interpreter_sha256,
            },
            {
                "operation_id": "worker.stop",
                "action": "STOP",
                "component": "WORKER",
                "argv": [],
                "cwd": str(material.application),
                "bind_host": None,
                "port": None,
                "executable_sha256": None,
            },
        ],
        "source_bindings": bindings,
        "custodian_authority": {
            "authority_mode": "DISPOSABLE_TEST_NATIVE_ONLY",
            "helper_binary_sha256": "8" * 64,
            "native_source_set": native_source_set,
            "native_source_set_sha256": hashlib.sha256(
                json.dumps(
                    native_source_set,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "protocol_version": "1",
            "protocol_features": [],
            "endpoint_authority": "PREOPENED_UNIX_SEQPACKET_DESCRIPTOR",
            "production_socket_activation": False,
            "operations": [
                "START",
                "STOP",
                "STATUS",
                "RECOVER",
                "RUN_ONCE",
                "READ_TRANSCRIPT",
                "PUBLISH_BUNDLE",
                "ACK",
            ],
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "stage_sha256": material.stage_sha256,
            "fixture_identity": {
                "sha256": material.fixture_sha256,
                "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            },
            "child_environment_contract": {
                "job_api": list(PACKAGE6_JOB_API_ENVIRONMENT_KEYS),
                "worker": list(PACKAGE6_WORKER_ENVIRONMENT_KEYS),
            },
            "mode": "PAPER",
            "live_execution_approved": False,
            "live_trading_approved": False,
        },
        "constraints": {
            "disposable_root": str(material.private / "disposable"),
            "evidence_root": str(material.private / "evidence"),
            "max_processes": "2",
            "startup_timeout_seconds": "10",
            "operation_timeout_seconds": "30",
            "cleanup_timeout_seconds": "10",
            "max_output_bytes": "65536",
            "live_execution_approved": False,
            "live_trading_approved": False,
            "systemd_allowed": False,
            "persistent_services_allowed": False,
            "network_policy": "LOOPBACK_ONLY",
        },
        "postgres_authority": {
            "approval_sha256": PG_APPROVAL,
            "bind_host": "127.0.0.1",
            "port": "18432",
            "database_name": "trading_agent_disposable_test",
            "pgdata": str(material.private / "disposable" / "pgdata"),
            "cluster_name": "trading-agent-disposable-tests",
            "service_roles": ["trading_job_api", "trading_job_worker"],
        },
        "fixture_authority": {
            "fixture_sha256": material.fixture_sha256,
            "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "path": str(fixture_authority_path),
        },
        "request": {
            "job_type": "SNAPSHOT",
            "actor": "FOUNDATION_VALIDATION",
            "idempotency_key": "foundation:manual:snapshot:foundation-001",
            "expected_job_count": "1",
        },
        "authority_digests": {
            "release": "e" * 64,
            "application": material.application_sha256,
            "backend": material.backend_sha256,
            "command": material.command_sha256,
            "semantic": "f" * 64,
            "fixture": material.fixture_sha256,
            "safety": material.safety_sha256,
            "stage": material.stage_sha256,
        },
        "canonical_record_sha256": "0" * 64,
    }
    document["canonical_record_sha256"] = canonical_record_sha256(document)
    approval_sha256 = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    activation, _ = build_staging_activation_v2(
        authority_sha256=material.authority_sha256,
        package6_approval_sha256=approval_sha256,
        safety_snapshot_sha256=material.safety_sha256,
        safety_exporter_commit=COMMIT,
        safety_source_fingerprint="d" * 64,
        semantic_active_authority_sha256=material.semantic_sha256,
        semantic_version_manifest_sha256=material.semantic_sha256,
        semantic_input_fingerprint="1" * 64,
        semantic_manifest_version="package6-provider-free-v1",
        semantic_policy_sha256="f" * 64,
        semantic_generated_at=material.generated,
        semantic_expires_at=material.expires,
        generated_at=material.generated,
        expires_at=material.expires,
    )
    activation_path = material.private / "authority/release-activation-v2.json"
    _write_fixed(activation_path, activation)
    fixture_authority = {
        "schema_version": 1,
        "classification": "PACKAGE6_PROVIDER_FREE_FIXTURE",
        "package6_approval_sha256": approval_sha256,
        "backend_commit": COMMIT,
        "generated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixture_path": str(fixture_path),
        "fixture_sha256": material.fixture_sha256,
    }
    _write_fixed(fixture_authority_path, fixture_authority)
    return document


def _context(root: Path, *, lease: Package6StagingLease) -> Package6ApprovalContext:
    return Package6ApprovalContext(
        source_commit=COMMIT,
        source_tree=TREE,
        operation_ids=(
            "job-api.start",
            "job-api.stop",
            "worker.start",
            "worker.stop",
        ),
        disposable_postgres_approval_sha256=PG_APPROVAL,
        postgres_bind_host="127.0.0.1",
        postgres_port=18432,
        postgres_database_name="trading_agent_disposable_test",
        postgres_pgdata=str(lease.root / "disposable" / "pgdata"),
        postgres_cluster_name="trading-agent-disposable-tests",
        postgres_service_roles=("trading_job_api", "trading_job_worker"),
        now=NOW + timedelta(minutes=1),
        source_root=root / "source",
        staging_scope=STAGING_SCOPE,
        staging_authority_path=lease.root / "authority/release-authority-v2.json",
        staging_activation_path=lease.root / "authority/release-activation-v2.json",
        custodian_helper_binary_sha256="8" * 64,
    )


def test_staging_material_rejects_a_private_root_outside_tmp() -> None:
    """A portable runner root must not become Package-6 staging authority."""

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as raw_root:
        with pytest.raises(ValueError):
            staging_v2._validate_private_root(Path(raw_root))


def test_staging_material_rejects_tmp_itself() -> None:
    """The trusted ancestor is never itself Package-6 staging material."""

    with pytest.raises(ValueError):
        staging_v2._validate_private_root(Path("/tmp"))


def test_package6_staging_lease_is_a_private_direct_tmp_child(
    package6_staging_lease: Package6StagingLease,
) -> None:
    info = package6_staging_lease.root.lstat()

    assert package6_staging_lease.root.parent == Path("/tmp")
    assert stat.S_IMODE(info.st_mode) == 0o700
    assert info.st_uid == os.geteuid()
    package6_staging_lease.assert_valid()


def test_package6_staging_lease_ignores_hostile_ambient_temp_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The special direct-/tmp lease neither reads nor rewrites temp settings."""

    ambient_roots: dict[str, Path] = {}
    for variable in ("TMPDIR", "TEMP", "TMP", "RUNNER_TEMP"):
        root = tmp_path / "hostile-ambient" / variable.lower()
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        ambient_roots[variable] = root
        monkeypatch.setenv(variable, str(root))

    issued_dirs: list[object] = []
    real_mkdtemp = staging_fixture.tempfile.mkdtemp

    def record_direct_lease_dir(*args: object, **kwargs: object) -> str:
        issued_dirs.append(kwargs.get("dir"))
        return real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(staging_fixture.tempfile, "mkdtemp", record_direct_lease_dir)
    lease = create_package6_staging_lease()
    try:
        assert issued_dirs == ["/tmp"]
        assert lease.root.parent == Path("/tmp")
        assert lease.root != tmp_path
        assert not lease.root.is_relative_to(tmp_path)
        assert all(
            not lease.root.is_relative_to(root)
            for root in ambient_roots.values()
        )
        assert {
            variable: os.environ[variable] for variable in ambient_roots
        } == {variable: str(root) for variable, root in ambient_roots.items()}
        lease.assert_valid()
    finally:
        lease.cleanup()

    assert {
        variable: os.environ[variable] for variable in ambient_roots
    } == {variable: str(root) for variable, root in ambient_roots.items()}


def test_package6_staging_lease_rejects_a_symlink_replacement_without_deleting_it() -> None:
    lease = create_package6_staging_lease()
    original = lease.root
    replacement_target: Path | None = None
    try:
        original.rmdir()
        replacement_target = Path(tempfile.mkdtemp(dir="/tmp"))
        original.symlink_to(replacement_target, target_is_directory=True)

        with pytest.raises(ValueError):
            lease.assert_valid()
        with pytest.raises(Package6StagingLeaseError, match="cleanup refused"):
            lease.cleanup()

        assert original.is_symlink()
        assert replacement_target.is_dir()
    finally:
        if original.is_symlink():
            original.unlink()
        elif original.exists():
            original.rmdir()
        if replacement_target is not None and replacement_target.exists():
            replacement_target.rmdir()


@pytest.mark.parametrize("unsafe_mode", (0o755, 0o770, 0o1700))
def test_package6_staging_lease_rejects_nonprivate_issued_modes(
    package6_staging_lease: Package6StagingLease,
    unsafe_mode: int,
) -> None:
    try:
        package6_staging_lease.root.chmod(unsafe_mode)
        with pytest.raises(ValueError):
            package6_staging_lease.assert_valid()
    finally:
        package6_staging_lease.root.chmod(0o700)


def test_package6_staging_lease_rejects_wrong_owner_metadata_model(
    package6_staging_lease: Package6StagingLease,
) -> None:
    """Model an unrepresentable foreign uid without changing filesystem ownership."""

    info = package6_staging_lease.root.lstat()
    values = list(info)
    values[4] = os.geteuid() + 1
    wrong_owner = os.stat_result(values)

    with pytest.raises(ValueError):
        staging_fixture._validate_issued_info(
            wrong_owner, package6_staging_lease.identity
        )


def test_staging_material_rejects_an_unsafe_intermediate_under_tmp(
    package6_staging_lease: Package6StagingLease,
) -> None:
    unsafe_intermediate = package6_staging_lease.root / "unsafe-intermediate"
    candidate = unsafe_intermediate / "candidate"
    unsafe_intermediate.mkdir(mode=0o770)
    unsafe_intermediate.chmod(0o770)
    candidate.mkdir(mode=0o700)

    with pytest.raises(ValueError):
        staging_v2._validate_private_root(candidate)


def test_package6_staging_lease_has_only_the_approved_test_consumers() -> None:
    root = Path(__file__).resolve().parents[2]
    helper_module = "tests.foundation._package6_staging_fixture"
    helper_basename = "_package6_staging_fixture"
    permitted = {
        "tests/foundation/test_package6_runtime_approval.py",
        "tests/foundation/test_package6_runtime_controller.py",
        "tests/foundation/test_package6_controller_closure.py",
        "tests/runtime_release/test_v2_runtime_config.py",
    }

    def resolved_import_module(
        node: ast.ImportFrom, source: Path
    ) -> str | None:
        """Resolve a static ``from`` target relative to its source package."""

        if not node.level:
            return node.module
        package = source.with_suffix("").parts[:-1]
        if node.level > len(package) + 1:
            return None
        package = package[: len(package) - node.level + 1]
        module = () if node.module is None else tuple(node.module.split("."))
        return ".".join((*package, *module))

    def consumes_helper(tree: ast.AST, source: Path) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == helper_module
                or alias.name.startswith(helper_module + ".")
                for alias in node.names
            ):
                return True
            if isinstance(node, ast.ImportFrom):
                module = resolved_import_module(node, source)
                if module == helper_module or (
                    module is not None and module.startswith(helper_module + ".")
                ):
                    return True
                if (
                    module == "tests.foundation"
                    and any(alias.name == helper_basename for alias in node.names)
                ):
                    return True
            if isinstance(node, ast.Attribute) and node.attr == helper_basename:
                return True
            if isinstance(node, ast.Call) and node.args:
                function = node.func
                is_dynamic_import = (
                    isinstance(function, ast.Name)
                    and function.id in {"__import__", "import_module"}
                ) or (
                    isinstance(function, ast.Attribute)
                    and function.attr == "import_module"
                )
                if (
                    is_dynamic_import
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and (
                        node.args[0].value == helper_module
                        or node.args[0].value.lstrip(".").endswith(
                            helper_basename
                        )
                    )
                ):
                    return True
        return False

    relative_import_cases = (
        (
            Path("tests/foundation/probe.py"),
            "from . import _package6_staging_fixture",
        ),
        (
            Path("tests/foundation/probe.py"),
            "from ._package6_staging_fixture import Package6StagingLease",
        ),
        (
            Path("tests/foundation/probe.py"),
            "from ..foundation import _package6_staging_fixture",
        ),
        (
            Path("tests/nested/deep/probe.py"),
            "from ...foundation import _package6_staging_fixture",
        ),
        (
            Path("tests/foundation/probe.py"),
            "import importlib\n"
            "importlib.import_module('._package6_staging_fixture', package=__package__)",
        ),
    )
    assert all(
        consumes_helper(ast.parse(source), relative)
        for relative, source in relative_import_cases
    )

    consumers: set[str] = set()
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if {".git", ".venv", "node_modules", ".pytest_cache"} & set(
            relative.parts
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if consumes_helper(tree, relative):
            consumers.add(relative.as_posix())

    assert consumers == permitted

    prohibited_paths = [root / "Makefile"]
    for directory in ("packages", "services", "ops", "scripts"):
        prohibited_paths.extend((root / directory).rglob("*"))
    needles = (
        helper_module.encode(),
        b"Package6StagingLease",
        b"create_package6_staging_lease",
        b"package6_staging_lease",
    )
    for path in prohibited_paths:
        if path.is_file():
            content = path.read_bytes()
            assert not any(needle in content for needle in needles), path


def test_package6_staging_lease_forwarding_chain_is_explicit() -> None:
    root = Path(__file__).resolve().parents[2]

    def function(path: Path, name: str) -> ast.FunctionDef:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def accepts_lease(node: ast.FunctionDef) -> bool:
        return any(
            argument.arg == "lease"
            for argument in (*node.args.args, *node.args.kwonlyargs)
        )

    def passes_lease(node: ast.FunctionDef, target: str) -> bool:
        return any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == target
            and any(
                keyword.arg == "lease"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "lease"
                for keyword in call.keywords
            )
            for call in ast.walk(node)
        )

    approval = function(
        root / "tests/foundation/test_package6_runtime_approval.py", "_record"
    )
    controller = function(
        root / "tests/foundation/test_package6_runtime_controller.py",
        "_sealed_runtime_fixture",
    )
    closure = function(
        root / "tests/foundation/test_package6_controller_closure.py",
        "_finalizer_arguments",
    )

    assert accepts_lease(approval) and passes_lease(approval, "_staging_material")
    assert accepts_lease(controller) and passes_lease(controller, "_record")
    assert accepts_lease(closure) and passes_lease(
        closure, "_sealed_runtime_fixture"
    )


def _resign(document: dict[str, object]) -> dict[str, object]:
    document["canonical_record_sha256"] = canonical_record_sha256(document)
    return document


def _rebind_dynamic_authorities(
    document: dict[str, object], root: Path, *, lease: Package6StagingLease
) -> dict[str, object]:
    _resign(document)
    approval_sha256 = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    for relative in (
        "package6-staging/authority/release-activation-v2.json",
        "package6-staging/authority/fixture-authority.json",
    ):
        path = lease.root / relative.removeprefix("package6-staging/")
        value = json.loads(path.read_text(encoding="utf-8"))
        value["package6_approval_sha256"] = approval_sha256
        _write_fixed(path, value)
    return document


def test_exact_candidate_approval_returns_private_capability(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)

    capability = validate_package6_runtime_approval(
        document, _context(tmp_path, lease=package6_staging_lease)
    )
    validate_source_binding_files(document, tmp_path / "source")

    assert capability.source_commit == COMMIT
    assert capability.source_tree == TREE
    assert capability.custodian.authority_mode == "DISPOSABLE_TEST_NATIVE_ONLY"
    assert capability.operation_ids == _context(
        tmp_path, lease=package6_staging_lease
    ).operation_ids
    assert capability.fixture_sha256 == _object(document, "fixture_authority")[
        "fixture_sha256"
    ]
    assert "approval_sha256" not in repr(capability)


def test_job_api_uses_only_canonical_port_8401(
    tmp_path: Path, package6_staging_lease: Package6StagingLease
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    for operation in _objects(document, "operations"):
        if operation["component"] == "JOB_API":
            operation["port"] = "8401"
    _resign(document)

    capability = validate_package6_runtime_approval(
        document, _context(tmp_path, lease=package6_staging_lease)
    )

    assert capability.operations["job-api.start"].port == 8401
    assert capability.operations["job-api.stop"].port == 8401


@pytest.mark.parametrize("operation_id", ("job-api.start", "job-api.stop"))
@pytest.mark.parametrize(
    "invalid_port",
    (
        True,
        8401,
        8401.0,
        "8.401e3",
        "+8401",
        "-8401",
        " 8401",
        "8401 ",
        "08401",
        "",
        "٨٤٠١",
        "0",
        "65536",
        "8402",
    ),
)
def test_job_api_port_rejects_every_noncanonical_document_representation(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
    operation_id: str,
    invalid_port: object,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    operation = next(
        item
        for item in _objects(document, "operations")
        if item["operation_id"] == operation_id
    )
    operation["port"] = invalid_port
    _resign(document)

    with pytest.raises(Package6ApprovalRejected):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


def test_arbitrary_executable_and_interpreter_escape_are_rejected(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
) -> None:
    for argv in (
        ["/bin/sh", "-c", "true"],
        ["/usr/bin/env", "python3.11", "-m", "apps.job_api.main"],
        ["/usr/bin/python3.11", "-c", "print('escape')"],
        ["/usr/bin/python3.11", "-I", "-m", "unapproved.module"],
    ):
        document = _record(tmp_path, lease=package6_staging_lease)
        _objects(document, "operations")[0]["argv"] = argv
        _resign(document)
        with pytest.raises(Package6ApprovalRejected, match="argv|executable|shape"):
            validate_package6_runtime_approval(
                document, _context(tmp_path, lease=package6_staging_lease)
            )


def test_stop_operations_are_signal_authority_not_new_commands(
    tmp_path: Path, package6_staging_lease: Package6StagingLease
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    for operation in _objects(document, "operations"):
        if operation["action"] == "STOP":
            operation["argv"] = []
    _resign(document)

    capability = validate_package6_runtime_approval(
        document, _context(tmp_path, lease=package6_staging_lease)
    )

    assert capability.operations["job-api.stop"].argv == ()
    assert capability.operations["worker.stop"].argv == ()


def test_capability_retains_all_typed_runtime_authority(
    tmp_path: Path, package6_staging_lease: Package6StagingLease
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    capability = validate_package6_runtime_approval(
        document, _context(tmp_path, lease=package6_staging_lease)
    )

    assert capability.postgres.approval_sha256 == PG_APPROVAL
    assert capability.request.idempotency_key == _object(document, "request")[
        "idempotency_key"
    ]
    assert capability.fixture.path == Path(
        cast(str, _object(document, "fixture_authority")["path"])
    )
    assert capability.listener.host == "127.0.0.1"
    assert capability.listener.port == 8401
    assert set(capability.authority_digests) == {
        "release",
        "application",
        "backend",
        "command",
        "semantic",
        "fixture",
        "safety",
        "stage",
    }
    assert capability.custodian.helper_binary_sha256 == "8" * 64
    assert capability.custodian.protocol_version == 1
    assert capability.custodian.protocol_features == ()
    assert capability.custodian.operations == (
        "START",
        "STOP",
        "STATUS",
        "RECOVER",
        "RUN_ONCE",
        "READ_TRANSCRIPT",
        "PUBLISH_BUNDLE",
        "ACK",
    )
    assert capability.custodian.mode == "PAPER"


@pytest.mark.parametrize(
    "key",
    (
        "release",
        "application",
        "backend",
        "command",
        "semantic",
        "fixture",
        "safety",
        "stage",
    ),
)
def test_each_syntactically_valid_wrong_authority_digest_is_rejected(
    tmp_path: Path, package6_staging_lease: Package6StagingLease, key: str
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    _object(document, "authority_digests")[key] = "9" * 64
    _rebind_dynamic_authorities(document, tmp_path, lease=package6_staging_lease)

    with pytest.raises(Package6ApprovalRejected, match="digest"):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("staging_scope", "PACKAGE6_OTHER"),
        ("staging_authority_path", Path("/tmp/wrong-authority.json")),
        ("staging_activation_path", Path("/tmp/wrong-activation.json")),
        ("source_root", Path("/tmp/wrong-source")),
    ],
)
def test_exact_nonambient_staging_context_is_required(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
    field: str,
    value: object,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)

    with pytest.raises(Package6ApprovalRejected):
        validate_package6_runtime_approval(
            document,
            _context(tmp_path, lease=package6_staging_lease)._replace(**{field: value}),
        )


@pytest.mark.parametrize(
    "relative",
    (
        "package6-staging/stage/application/app.py",
        "package6-staging/stage/application/.venv/bin/python3.11",
        "package6-staging/runtime/safety-state.json",
        "package6-staging/semantic/active.json",
        "package6-staging/authority/fixture.json",
    ),
)
def test_staged_candidate_dynamic_and_fixture_byte_drift_is_rejected(
    tmp_path: Path, package6_staging_lease: Package6StagingLease, relative: str
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    path = package6_staging_lease.root / relative.removeprefix("package6-staging/")
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b"drift\n")
    path.chmod(
        0o555 if path.name == "python3.11" else 0o600 if "safety-state" in relative else 0o444
    )

    with pytest.raises(Package6ApprovalRejected, match="staging|fixture"):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


def test_source_binding_set_is_canonical_complete_and_ordered(
    tmp_path: Path, package6_staging_lease: Package6StagingLease
) -> None:
    from scripts.validate_package6_runtime_approval import (
        PACKAGE6_SOURCE_BINDING_PATHS,
    )
    from packages.runtime_release.v2 import (
        PAPER_APPLICATION_SOURCE_MAPPING,
        PAPER_BACKEND_SOURCE_MAPPING,
    )

    document = _record(tmp_path, lease=package6_staging_lease)
    assert tuple(item["path"] for item in _objects(document, "source_bindings")) == (
        PACKAGE6_SOURCE_BINDING_PATHS
    )
    release_sources = {
        source
        for _, source in (
            *PAPER_APPLICATION_SOURCE_MAPPING,
            *PAPER_BACKEND_SOURCE_MAPPING,
        )
    }
    assert release_sources <= set(PACKAGE6_SOURCE_BINDING_PATHS)
    assert "services/job_worker/engine_spawn_interface.py" in release_sources
    assert tuple(
        path for path in PACKAGE6_SOURCE_BINDING_PATHS if path in release_sources
    ) == tuple(sorted(release_sources, key=os.fsencode))

    _objects(document, "source_bindings").reverse()
    _resign(document)
    with pytest.raises(Package6ApprovalRejected, match="canonical order"):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


def test_package6_behavior_sources_are_bound_at_exact_reviewed_cardinality() -> None:
    required_behavior_sources = {
        "ops/release-v2/provision-root.sh",
        "ops/release-v2/verify-stage.py",
        "packages/runtime_release/config.py",
        "scripts/build_p1_package6_host_authority.py",
    }
    schema = json.loads(
        Path("schemas/package6-paper-runtime-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )
    binding_schema = schema["properties"]["source_bindings"]

    assert required_behavior_sources <= set(PACKAGE6_SOURCE_BINDING_PATHS)
    assert (
        binding_schema["minItems"]
        == binding_schema["maxItems"]
        == len(PACKAGE6_SOURCE_BINDING_PATHS)
        == 79
    )


def test_public_schema_matches_and_enforces_canonical_source_binding_count(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
) -> None:
    from scripts.validate_package6_runtime_approval import (
        PACKAGE6_SOURCE_BINDING_PATHS,
    )

    schema = json.loads(
        Path("schemas/package6-paper-runtime-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )
    binding_schema = schema["properties"]["source_bindings"]
    assert binding_schema["minItems"] == len(PACKAGE6_SOURCE_BINDING_PATHS)
    assert binding_schema["maxItems"] == len(PACKAGE6_SOURCE_BINDING_PATHS)
    document = _record(tmp_path, lease=package6_staging_lease)
    validate_package6_runtime_approval(
        document, _context(tmp_path, lease=package6_staging_lease)
    )
    for count in (36, 73, len(PACKAGE6_SOURCE_BINDING_PATHS) - 1):
        stale = dict(document)
        stale["source_bindings"] = _objects(document, "source_bindings")[:count]
        _resign(stale)
        with pytest.raises(Package6ApprovalRejected):
            validate_package6_runtime_approval(
                stale, _context(tmp_path, lease=package6_staging_lease)
            )


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("missing-authority", "fields"),
        ("extra-authority-field", "fields"),
        ("production-native-authority", "disposable tests"),
        ("helper-binary", "helper"),
        ("missing-native-source", "source"),
        ("extra-native-source", "source"),
        ("reordered-native-source", "canonical"),
        ("native-source-entry-digest", "source"),
        ("native-source-set-digest", "source"),
        ("protocol-version", "protocol"),
        ("protocol-feature", "protocol"),
        ("endpoint", "endpoint"),
        ("socket-activation", "activation"),
        ("missing-operation", "operation"),
        ("unknown-operation", "operation"),
        ("reordered-operation", "operation"),
        ("candidate-commit", "candidate"),
        ("candidate-tree", "candidate"),
        ("stage", "stage"),
        ("fixture-sha", "fixture"),
        ("fixture-provenance", "fixture"),
        ("missing-environment-key", "environment"),
        ("unknown-environment-key", "environment"),
        ("reordered-environment-key", "environment"),
        ("mode", "paper"),
        ("live-execution", "live"),
        ("live-trading", "live"),
    ),
)
def test_each_native_custodian_authority_tamper_or_omission_is_rejected(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
    case: str,
    message: str,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    authority = _object(document, "custodian_authority")
    if case == "missing-authority":
        document.pop("custodian_authority")
    elif case == "extra-authority-field":
        authority["socket_path"] = "/tmp/forbidden.sock"
    elif case == "production-native-authority":
        authority["authority_mode"] = "PRODUCTION_NATIVE"
    elif case == "helper-binary":
        authority["helper_binary_sha256"] = "7" * 64
    elif case == "missing-native-source":
        cast(list[object], authority["native_source_set"]).pop()
    elif case == "extra-native-source":
        cast(list[object], authority["native_source_set"]).append(
            {"path": "native/package6_custodian/src/extra.c", "sha256": "1" * 64}
        )
    elif case == "reordered-native-source":
        cast(list[object], authority["native_source_set"]).reverse()
    elif case == "native-source-entry-digest":
        cast(list[dict[str, object]], authority["native_source_set"])[0][
            "sha256"
        ] = "1" * 64
    elif case == "native-source-set-digest":
        authority["native_source_set_sha256"] = "1" * 64
    elif case == "protocol-version":
        authority["protocol_version"] = 2
    elif case == "protocol-feature":
        authority["protocol_features"] = ["PIDFD_DELEGATION"]
    elif case == "endpoint":
        authority["endpoint_authority"] = "FILESYSTEM_SOCKET_PATH"
    elif case == "socket-activation":
        authority["production_socket_activation"] = True
    elif case == "missing-operation":
        cast(list[str], authority["operations"]).pop()
    elif case == "unknown-operation":
        cast(list[str], authority["operations"])[0] = "SPAWN"
    elif case == "reordered-operation":
        cast(list[str], authority["operations"]).reverse()
    elif case == "candidate-commit":
        authority["candidate_commit"] = "1" * 40
    elif case == "candidate-tree":
        authority["candidate_tree"] = "1" * 40
    elif case == "stage":
        authority["stage_sha256"] = "1" * 64
    elif case == "fixture-sha":
        cast(dict[str, object], authority["fixture_identity"])["sha256"] = "1" * 64
    elif case == "fixture-provenance":
        cast(dict[str, object], authority["fixture_identity"])[
            "provenance"
        ] = "EXTERNAL_PROVIDER"
    elif case == "missing-environment-key":
        cast(
            dict[str, list[str]],
            authority["child_environment_contract"],
        )["job_api"].pop()
    elif case == "unknown-environment-key":
        cast(
            dict[str, list[str]],
            authority["child_environment_contract"],
        )["worker"][0] = "SECRET_VALUE"
    elif case == "reordered-environment-key":
        cast(
            dict[str, list[str]],
            authority["child_environment_contract"],
        )["job_api"].reverse()
    elif case == "mode":
        authority["mode"] = "LIVE"
    elif case == "live-execution":
        authority["live_execution_approved"] = True
    elif case == "live-trading":
        authority["live_trading_approved"] = True
    else:  # pragma: no cover - exhaustive cases
        raise AssertionError(case)
    _rebind_dynamic_authorities(document, tmp_path, lease=package6_staging_lease)

    with pytest.raises(Package6ApprovalRejected, match=message):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


@pytest.mark.parametrize("invalid_count", (True, 1, 1.0))
def test_runtime_request_expected_job_count_requires_exact_integer(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
    invalid_count: object,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    _object(document, "request")["expected_job_count"] = invalid_count
    _rebind_dynamic_authorities(document, tmp_path, lease=package6_staging_lease)

    with pytest.raises(Package6ApprovalRejected, match="single approved SNAPSHOT"):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


@pytest.mark.parametrize(
    ("section", "field", "invalid_value"),
    (
        (None, "schema_version", 3.0),
        ("custodian_authority", "protocol_version", 1.0),
        ("constraints", "max_processes", 2.0),
        ("constraints", "startup_timeout_seconds", 10.0),
        ("constraints", "operation_timeout_seconds", 30.0),
        ("constraints", "cleanup_timeout_seconds", 10.0),
        ("constraints", "max_output_bytes", 65536.0),
        ("postgres_authority", "port", 18432.0),
        ("request", "expected_job_count", 1.0),
    ),
)
def test_all_approval_integer_authorities_reject_json_numbers(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
    section: str | None,
    field: str,
    invalid_value: float,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    target = document if section is None else _object(document, section)
    target[field] = invalid_value
    _rebind_dynamic_authorities(document, tmp_path, lease=package6_staging_lease)

    with pytest.raises(Package6ApprovalRejected):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


def test_approval_schema_has_no_ambiguous_json_numeric_authorities() -> None:
    schema = json.loads(
        Path("schemas/package6-paper-runtime-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )

    def visit(value: object) -> None:
        if isinstance(value, dict):
            declared_type = value.get("type")
            assert declared_type != "integer"
            if isinstance(declared_type, list):
                assert "integer" not in declared_type
            if "const" in value:
                assert type(value["const"]) not in (int, float)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)


def test_native_source_file_drift_is_rejected_against_bound_bytes(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    path = tmp_path / "source" / PACKAGE6_CUSTODIAN_SOURCE_PATHS[0]
    path.write_bytes(path.read_bytes() + b"drift\n")

    with pytest.raises(Package6ApprovalRejected, match="source"):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


def test_json_schema_and_validator_have_exact_native_authority_field_parity() -> None:
    schema = json.loads(
        Path("schemas/package6-paper-runtime-approval.schema.json").read_text(
            encoding="utf-8"
        )
    )
    required = set(schema["required"])
    properties = set(schema["properties"])
    custodian = schema["properties"]["custodian_authority"]
    custodian_required = set(custodian["required"])
    custodian_properties = set(custodian["properties"])

    assert required == properties
    assert "custodian_authority" in required
    assert custodian_required == custodian_properties == {
        "authority_mode",
        "helper_binary_sha256",
        "native_source_set",
        "native_source_set_sha256",
        "protocol_version",
        "protocol_features",
        "endpoint_authority",
        "production_socket_activation",
        "operations",
        "candidate_commit",
        "candidate_tree",
        "stage_sha256",
        "fixture_identity",
        "child_environment_contract",
        "mode",
        "live_execution_approved",
        "live_trading_approved",
    }
    native_sources = custodian["properties"]["native_source_set"]
    assert native_sources["minItems"] == len(PACKAGE6_CUSTODIAN_SOURCE_PATHS)
    assert native_sources["maxItems"] == len(PACKAGE6_CUSTODIAN_SOURCE_PATHS)
    assert schema["properties"]["schema_version"] == {
        "type": "string",
        "const": "3",
    }
    assert custodian["properties"]["protocol_version"] == {
        "type": "string",
        "const": "1",
    }
    assert schema["properties"]["request"]["properties"][
        "expected_job_count"
    ] == {"type": "string", "const": "1"}
    assert schema["$defs"]["operation"]["properties"]["port"] == {
        "enum": ["8401", None]
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d.update(extra=True), "fields"),
        (lambda d: d.pop("scope"), "fields"),
        (
            lambda d: _object(d, "review").update(operator_identity="TBD"),
            "placeholder",
        ),
        (
            lambda d: _object(d, "validity").update(expires_at_utc="not-a-time"),
            "timestamp",
        ),
        (lambda d: _object(d, "source").update(commit="f" * 40), "candidate"),
        (
            lambda d: _objects(d, "operations")[0].update(
                operation_id="other.start"
            ),
            "operation",
        ),
        (
            lambda d: _objects(d, "operations")[0].update(bind_host="0.0.0.0"),
            "loopback",
        ),
        (lambda d: _objects(d, "operations")[0].update(port="0"), "port"),
        (
            lambda d: _objects(d, "operations")[0].update(port="55432"),
            "port",
        ),
        (
            lambda d: _object(d, "constraints").update(max_processes=0),
            "process",
        ),
        (
            lambda d: _object(d, "constraints").update(
                operation_timeout_seconds=3601
            ),
            "timeout",
        ),
        (
            lambda d: _object(d, "constraints").update(systemd_allowed=True),
            "systemd",
        ),
        (
            lambda d: _object(d, "constraints").update(
                persistent_services_allowed=True
            ),
            "persistent",
        ),
        (
            lambda d: _object(d, "constraints").update(
                live_execution_approved=True
            ),
            "live",
        ),
        (
            lambda d: _object(d, "constraints").update(
                live_trading_approved=True
            ),
            "live",
        ),
        (
            lambda d: _objects(d, "operations")[0].update(
                argv=["systemctl", "start", "trading-job-api"]
            ),
            "forbidden",
        ),
        (
            lambda d: _objects(d, "operations")[0].update(
                argv=["curl", "https://api.binance.com/order"]
            ),
            "forbidden",
        ),
        (
            lambda d: _object(d, "constraints").update(
                evidence_root="/var/lib/trading-agent/evidence"
            ),
            "root",
        ),
        (
            lambda d: _object(d, "postgres_authority").update(
                approval_sha256="c" * 64
            ),
            "PostgreSQL",
        ),
        (
            lambda d: _object(d, "fixture_authority").update(
                provenance="COINGECKO_PUBLIC"
            ),
            "fixture",
        ),
    ],
)
def test_forbidden_runtime_boundaries_fail_closed(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
    mutation: Callable[[dict[str, object]], object],
    message: str,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    mutation(document)
    _resign(document)

    with pytest.raises(Package6ApprovalRejected, match=message):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


def test_expired_and_excessive_validity_fail_closed(
    tmp_path: Path, package6_staging_lease: Package6StagingLease
) -> None:
    for approved, expires, now in (
        ("2026-07-26T12:00:00Z", "2026-07-26T12:31:00Z", "2026-07-26T12:10:00Z"),
        ("2026-07-26T12:00:00Z", "2026-07-26T12:30:00Z", "2026-07-26T12:31:00Z"),
    ):
        document = _record(tmp_path, lease=package6_staging_lease)
        document["validity"] = {
            "approved_at_utc": approved,
            "expires_at_utc": expires,
        }
        _resign(document)
        context = _context(tmp_path, lease=package6_staging_lease)._replace(
            now=datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        )
        with pytest.raises(Package6ApprovalRejected, match="validity|expired"):
            validate_package6_runtime_approval(document, context)


def test_startup_requires_paired_explicit_start_and_stop(
    tmp_path: Path, package6_staging_lease: Package6StagingLease
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    document["operations"] = _objects(document, "operations")[:-1]
    _resign(document)

    with pytest.raises(Package6ApprovalRejected, match="START.*STOP"):
        validate_package6_runtime_approval(
            document, _context(tmp_path, lease=package6_staging_lease)
        )


@pytest.mark.parametrize(
    "relative",
    (
        "ops/release-v2/provision-root.sh",
        "ops/release-v2/verify-stage.py",
        "packages/runtime_release/config.py",
    ),
)
def test_each_new_behavior_source_binding_rejects_file_drift(
    tmp_path: Path, package6_staging_lease: Package6StagingLease, relative: str
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    binding = tmp_path / "source" / relative
    binding.write_bytes(binding.read_bytes() + b"# drift\n")

    with pytest.raises(Package6ApprovalRejected, match="digest"):
        validate_source_binding_files(document, tmp_path / "source")


def test_source_binding_rejects_drift_symlink_non_regular_and_traversal(
    tmp_path: Path,
    package6_staging_lease: Package6StagingLease,
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    binding = tmp_path / "source" / CANONICAL_BINDINGS[0]
    binding.write_text("DRIFT = True\n", encoding="utf-8")
    with pytest.raises(Package6ApprovalRejected, match="digest"):
        validate_source_binding_files(document, tmp_path / "source")

    binding.unlink()
    binding.symlink_to(tmp_path / "target.py")
    with pytest.raises(Package6ApprovalRejected, match="regular"):
        validate_source_binding_files(document, tmp_path / "source")

    document = _record(tmp_path, lease=package6_staging_lease)
    _objects(document, "source_bindings")[0]["path"] = "../bound.py"
    _resign(document)
    with pytest.raises(Package6ApprovalRejected, match="path"):
        validate_source_binding_files(document, tmp_path / "source")


def test_validator_is_side_effect_free(
    tmp_path: Path, monkeypatch, package6_staging_lease: Package6StagingLease
) -> None:
    document = _record(tmp_path, lease=package6_staging_lease)
    called: list[str] = []
    monkeypatch.setattr(os, "system", lambda _value: called.append("system"))

    validate_package6_runtime_approval(
        document, _context(tmp_path, lease=package6_staging_lease)
    )

    assert called == []
    assert not (tmp_path / "disposable").exists()
