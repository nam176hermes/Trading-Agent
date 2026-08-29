#!/usr/bin/env python3
"""Build and consume disposable Package 6 authority for the P1 vertical slice."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Callable, Mapping, Sequence, cast
from weakref import WeakSet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.runtime_release.config import RuntimeAuthorityV2
from packages.runtime_release.offline_wheelhouse import verify_offline_wheelhouse
from packages.runtime_release.semantic import semantic_policy_digest_v2
from packages.runtime_release.staging_v2 import (
    PACKAGE6_APPROVAL_SHA256_ENV,
    STAGING_ACTIVATION_PATH_ENV,
    STAGING_AUTHORITY_PATH_ENV,
    STAGING_SCOPE,
    STAGING_SCOPE_ENV,
    build_staging_activation_v2,
    build_staging_release_authority_v2,
    canonical_digest,
    canonical_json_bytes,
)
from packages.safety_evidence import (
    CANONICAL_SAFETY_SOURCE_ROOT,
    safety_source_fingerprint,
)
from scripts.validate_disposable_postgres_approval import (
    DisposablePostgresApprovalContext,
    _runtime_setting_names,
    load_protected_approval_record,
    validate_disposable_postgres_approval,
    validate_disposable_postgres_approval_record,
    validate_source_binding_files as validate_postgres_source_binding_files,
)
from scripts.validate_package6_runtime_approval import (
    PACKAGE6_CUSTODIAN_OPERATIONS,
    PACKAGE6_CUSTODIAN_SOURCE_PATHS,
    PACKAGE6_JOB_API_ENVIRONMENT_KEYS,
    PACKAGE6_SOURCE_BINDING_PATHS,
    PACKAGE6_WORKER_ENVIRONMENT_KEYS,
    Package6ApprovalContext,
    ValidatedPackage6Capability,
    canonical_record_sha256,
    is_issued_capability,
    validate_package6_runtime_approval,
)
from services.job_store.config import P1_DISPOSABLE_DATABASE_REVISION
from services.job_worker.command_registry import (
    WorkerRuntimeAuthority,
    _issue_p1_staging_safety_authority_refresher,
    attest_worker_runtime_authority,
)
from services.safety_state_exporter.exporter import SafetyStateExporter


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{2,127}\Z")
_POSTGRES_SLOT = re.compile(r"phase4-postgres-[A-Za-z0-9._-]+\Z")
_CHILD_ENV = frozenset({
    STAGING_SCOPE_ENV,
    STAGING_AUTHORITY_PATH_ENV,
    STAGING_ACTIVATION_PATH_ENV,
    PACKAGE6_APPROVAL_SHA256_ENV,
    "LC_CTYPE",
})
_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
}


class HostAuthorityError(RuntimeError):
    """Sanitized operational failure."""


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class _ValidatedP1Operation:
    package6_capability: ValidatedPackage6Capability
    authority_pin: tuple[object, ...]
    semantic_sha256: str
    arguments: tuple[str, ...]


_ISSUED_P1_OPERATIONS: WeakSet[_ValidatedP1Operation] = WeakSet()


def _digest_file(path: Path) -> str:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise HostAuthorityError("authority input is unsafe")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _export_p1_safety_snapshot(
    output_path: Path,
    *,
    exporter_commit: str,
    clock: Callable[[], datetime] | None = None,
) -> None:
    SafetyStateExporter(
        canonical_source_root=CANONICAL_SAFETY_SOURCE_ROOT,
        mounted_source_root=CANONICAL_SAFETY_SOURCE_ROOT,
        output_path=output_path,
        exporter_commit=exporter_commit,
        gate_source={
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        },
        clock=clock,
    ).export_once()


def _require_digest(path: Path, expected: str) -> None:
    if _SHA256.fullmatch(expected) is None or _digest_file(path) != expected:
        raise HostAuthorityError("authority input digest does not match")


def _planned_postgres_slot_root(pgdata: Path) -> Path:
    slot = pgdata.parent
    if (
        not pgdata.is_absolute()
        or ".." in pgdata.parts
        or pgdata.name != "data"
        or slot.parent != Path("/tmp")
        or _POSTGRES_SLOT.fullmatch(slot.name) is None
    ):
        raise HostAuthorityError("PostgreSQL data root is not an exact planned slot")
    return slot


def _canonical_write(path: Path, value: object, mode: int) -> str:
    raw = canonical_json_bytes(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    return hashlib.sha256(raw).hexdigest()


def _read_canonical(path: Path, *, maximum: int = 1024 * 1024) -> tuple[dict[str, object], bytes]:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o444
            or info.st_size > maximum
        ):
            raise HostAuthorityError("bound authority record is unsafe")
        chunks: list[bytes] = []
        observed = 0
        while chunk := os.read(descriptor, min(65536, maximum + 1 - observed)):
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum:
                raise HostAuthorityError("bound authority record is invalid")
        raw = b"".join(chunks)
        if len(raw) != info.st_size:
            raise HostAuthorityError("bound authority record changed")
    finally:
        os.close(descriptor)
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostAuthorityError("bound authority record is invalid") from exc
    if not isinstance(document, dict):
        raise HostAuthorityError("bound authority record is invalid")
    return document, raw


def _git(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(source), *arguments],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_identity(source: Path) -> tuple[str, str]:
    if _git(source, "status", "--porcelain=v1", "--untracked-files=all"):
        raise HostAuthorityError("source checkout is dirty")
    commit = _git(source, "rev-parse", "HEAD^{commit}")
    tree = _git(source, "rev-parse", "HEAD^{tree}")
    if _GIT.fullmatch(commit) is None or _GIT.fullmatch(tree) is None:
        raise HostAuthorityError("source identity is invalid")
    return commit, tree


def _private_root(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    info = resolved.lstat()
    if (
        resolved.parent != Path("/tmp")
        or resolved == Path("/tmp")
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise HostAuthorityError("disposable root is not a private direct /tmp child")
    return resolved


def _bindings(source: Path, paths: Sequence[str]) -> list[dict[str, str]]:
    return [
        {"path": relative, "sha256": _digest_file(source / relative)}
        for relative in paths
    ]


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _operation(
    operation_id: str,
    action: str,
    component: str,
    application_root: Path,
    application_python: Path,
    application_python_sha256: str,
) -> dict[str, object]:
    start = action == "START"
    module = "apps.job_api.main" if component == "JOB_API" else "services.job_worker.main"
    return {
        "operation_id": operation_id,
        "action": action,
        "component": component,
        "argv": [str(application_python), "-I", "-m", module] if start else [],
        "cwd": str(application_root),
        "bind_host": "127.0.0.1" if component == "JOB_API" else None,
        "port": "8401" if component == "JOB_API" else None,
        "executable_sha256": application_python_sha256 if start else None,
    }


def _offline_build_argv(arguments: argparse.Namespace, clone: Path, stage: Path) -> list[str]:
    return [
        "/bin/bash",
        str(clone / "ops/release-v2/build-stage.sh"),
        "--repo", str(clone),
        "--commit", arguments.source_commit,
        "--output", str(stage),
        "--prior-release-sha256", arguments.prior_release_sha256,
        "--python-runtime-archive", str(arguments.python_runtime_archive),
        "--uv", str(arguments.uv),
        "--wheelhouse", str(arguments.wheelhouse),
    ]


def _p1_semantic_document(
    source_root: Path,
    application_python: Path,
    source_commit: str,
    source_tree: str,
) -> dict[str, object]:
    return {
        "classification": "PACKAGE6_PROVIDER_FREE_SEMANTIC",
        "p1_operation": {
            "application_python": str(application_python),
            "builder_sha256": _digest_file(
                source_root / "scripts/build_p1_package6_host_authority.py"
            ),
            "constraints": {
                "live_authorized": False,
                "network_trading_authorized": False,
                "production_authorized": False,
            },
            "database_revision": P1_DISPOSABLE_DATABASE_REVISION,
            "execution_steps": [
                "AUTHENTICATED_JOB_API_ENQUEUE",
                "EXACTLY_ONE_WORKER_RUN_ONCE",
                "DURABLE_SUCCESS_AND_PARITY",
            ],
            "job_type": "BACKTEST",
            "operation_id": "p1-vertical-slice.execute-once",
            "source_commit": source_commit,
            "source_tree": source_tree,
            "vertical_runner_sha256": _digest_file(
                source_root / "scripts/run_p1_nautilus_vertical_slice.py"
            ),
        },
        "schema_version": 2,
        "source_commit": source_commit,
    }


def _p1_semantic_policy_digest(
    source_commit: str,
    active_path: Path,
    input_root: Path,
    active_sha256: str,
) -> str:
    return canonical_digest(
        {
            "base_policy_sha256": semantic_policy_digest_v2(
                source_commit, active_path, input_root=input_root
            ),
            "p1_operation_sha256": active_sha256,
            "schema_version": "p1-package6-semantic-policy/v1",
        }
    )


def _prepare_static(arguments: argparse.Namespace) -> dict[str, object]:
    source = arguments.source_root.resolve(strict=True)
    commit, tree = _source_identity(source)
    if (commit, tree) != (arguments.source_commit, arguments.source_tree):
        raise HostAuthorityError("source identity does not match the requested candidate")
    if (
        _IDENTITY.fullmatch(arguments.operator_identity) is None
        or _IDENTITY.fullmatch(arguments.reviewer_identity) is None
        or arguments.operator_identity == arguments.reviewer_identity
    ):
        raise HostAuthorityError("independent operator and reviewer identities are required")
    root = _private_root(arguments.disposable_root)
    postgres_slot_root = _planned_postgres_slot_root(arguments.pgdata)
    for path, expected in (
        (arguments.python_runtime_archive, arguments.python_runtime_archive_sha256),
        (arguments.uv, arguments.uv_sha256),
        (arguments.postgres_approval, arguments.postgres_approval_sha256),
    ):
        _require_digest(path.resolve(strict=True), expected)
    approval = load_protected_approval_record(arguments.postgres_approval)
    approval_now = datetime.now(UTC)
    runtime_setting_names = _runtime_setting_names()
    validate_disposable_postgres_approval_record(
        approval,
        expected_scope="DISPOSABLE_PG_GREEN",
        expected_commit=commit,
        expected_tree=tree,
        expected_sql_sha256=None,
        runtime_setting_names=runtime_setting_names,
        now=approval_now,
    )
    validate_disposable_postgres_approval(
        approval,
        DisposablePostgresApprovalContext(
            scope="DISPOSABLE_PG_GREEN",
            source_commit=commit,
            source_tree=tree,
            test_path="tests/p1_nautilus/test_vertical_slice_e2e.py",
            operation_id="p1-vertical-slice-v1",
            pgdata=str(arguments.pgdata),
            bind_host="127.0.0.1",
            port=arguments.pg_port,
            cluster_name="trading-agent-disposable-tests",
            database_name="trading_agent_disposable_test",
            runtime_setting_names=runtime_setting_names,
            now=approval_now,
        ),
    )
    validate_postgres_source_binding_files(approval, source)
    if (
        _SHA256.fullmatch(arguments.prior_release_sha256) is None
        or verify_offline_wheelhouse(
            arguments.wheelhouse.resolve(strict=True),
            source / "packages/runtime_release/paper_application/uv.lock",
        )
        != arguments.wheelhouse_sha256
    ):
        raise HostAuthorityError("offline wheelhouse authority does not match")
    clone = root / "source"
    subprocess.run(
        ["/usr/bin/git", "clone", "--no-local", "--no-hardlinks", "--no-tags", str(source), str(clone)],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(clone), "checkout", "--detach", arguments.source_commit],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
    )
    if _source_identity(clone) != (commit, tree):
        raise HostAuthorityError("private clone identity does not match")
    stage = root / "stage"
    subprocess.run(
        _offline_build_argv(arguments, clone, stage),
        env={**_GIT_ENV, "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root)},
        check=True,
        capture_output=True,
    )
    native_root = root / "native"
    native_root.mkdir(mode=0o700)
    subprocess.run(
        [
            "/usr/bin/make", "-C", str(clone / "native/package6_custodian"),
            f"BUILD_DIR={native_root}",
            f"PYTHON={stage / 'application/.venv/bin/python3.11'}", "build",
        ],
        env={"HOME": str(root), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )
    authority_root = root / "authority"
    runtime_root = root / "runtime"
    semantic_root = root / "semantic"
    for path in (authority_root, runtime_root, semantic_root):
        path.mkdir(mode=0o700)
    (root / "evidence").mkdir(mode=0o700)
    for name in ("artifacts", "reports", "scratch", "signals"):
        (runtime_root / name).mkdir(mode=0o700)
    (semantic_root / "input").mkdir(mode=0o711)
    generated = datetime.now(UTC).replace(microsecond=0)
    expires = generated + timedelta(minutes=30)
    safety_path = runtime_root / "safety-state.json"
    _export_p1_safety_snapshot(
        safety_path,
        exporter_commit=commit,
        clock=lambda: generated,
    )
    safety_sha256 = _digest_file(safety_path)
    semantic_path = semantic_root / "active.json"
    application_python = stage / "application/.venv/bin/python3.11"
    semantic_sha256 = _canonical_write(
        semantic_path,
        _p1_semantic_document(clone, application_python, commit, tree),
        0o444,
    )
    semantic_policy = _p1_semantic_policy_digest(
        commit,
        semantic_path,
        semantic_root / "input",
        semantic_sha256,
    )
    production_authority_sha256 = _digest_file(Path(str(stage) + ".authority.json"))
    runtime_paths = {
        "artifact_root": runtime_root / "artifacts",
        "reports_root": runtime_root / "reports",
        "safety_snapshot": safety_path,
        "scratch_root": runtime_root / "scratch",
        "semantic_authority": semantic_path,
        "semantic_input_root": semantic_root / "input",
        "signals_root": runtime_root / "signals",
    }
    static, _ = build_staging_release_authority_v2(
        stage,
        source_commit=commit,
        source_tree=tree,
        disposable_root=root,
        production_release_authority_sha256=production_authority_sha256,
        runtime_paths=runtime_paths,
        generated_at=generated,
        expires_at=expires,
    )
    authority_path = authority_root / "release-authority-v2.json"
    authority_sha256 = _canonical_write(authority_path, static, 0o444)
    fixture_path = authority_root / "fixture.json"
    fixture_sha256 = _canonical_write(
        fixture_path,
        {
            "assets": {"BTC": {"current_price": 1, "market_cap": 2, "price_change_percentage_24h": 0, "total_volume": 3}},
            "as_of": _timestamp(generated),
            "provenance": "DETERMINISTIC_PROVIDER_FREE_V1",
            "schema_version": 1,
        },
        0o444,
    )
    components = cast(Mapping[str, Mapping[str, str]], static["components"])
    stage_document = cast(Mapping[str, str], static["stage"])
    native_sources = _bindings(clone, PACKAGE6_CUSTODIAN_SOURCE_PATHS)
    custodian_sha256 = _digest_file(native_root / "package6-custodian")
    application_python_sha256 = _digest_file(application_python)
    fixture_authority_path = authority_root / "fixture-authority.json"
    operations = [
        _operation("job-api.start", "START", "JOB_API", stage / "application", application_python, application_python_sha256),
        _operation("job-api.stop", "STOP", "JOB_API", stage / "application", application_python, application_python_sha256),
        _operation("worker.start", "START", "WORKER", stage / "application", application_python, application_python_sha256),
        _operation("worker.stop", "STOP", "WORKER", stage / "application", application_python, application_python_sha256),
    ]
    record: dict[str, object] = {
        "record_kind": "PACKAGE6_PAPER_RUNTIME_APPROVAL",
        "schema_version": "3",
        "record_id": "PACKAGE6_RUNTIME_P1_VERTICAL_SLICE_001",
        "scope": "PACKAGE6_PAPER_RUNTIME",
        "source": {"commit": commit, "tree": tree},
        "validity": {"approved_at_utc": _timestamp(generated), "expires_at_utc": _timestamp(expires)},
        "review": {"decision": "APPROVED", "operator_identity": arguments.operator_identity, "reviewer_identity": arguments.reviewer_identity, "runtime_greenlight": "APPROVE PACKAGE 6 RUNTIME"},
        "operations": operations,
        "source_bindings": _bindings(clone, PACKAGE6_SOURCE_BINDING_PATHS),
        "custodian_authority": {
            "authority_mode": "DISPOSABLE_TEST_NATIVE_ONLY",
            "helper_binary_sha256": custodian_sha256,
            "native_source_set": native_sources,
            "native_source_set_sha256": hashlib.sha256(json.dumps(native_sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "protocol_version": "1", "protocol_features": [],
            "endpoint_authority": "PREOPENED_UNIX_SEQPACKET_DESCRIPTOR",
            "production_socket_activation": False,
            "operations": list(PACKAGE6_CUSTODIAN_OPERATIONS),
            "candidate_commit": commit, "candidate_tree": tree,
            "stage_sha256": stage_document["file_set_sha256"],
            "fixture_identity": {"sha256": fixture_sha256, "provenance": "DETERMINISTIC_PROVIDER_FREE_V1"},
            "child_environment_contract": {"job_api": list(PACKAGE6_JOB_API_ENVIRONMENT_KEYS), "worker": list(PACKAGE6_WORKER_ENVIRONMENT_KEYS)},
            "mode": "PAPER", "live_execution_approved": False, "live_trading_approved": False,
        },
        "constraints": {
            "disposable_root": str(postgres_slot_root), "evidence_root": str(root / "evidence"),
            "max_processes": "2", "startup_timeout_seconds": "10", "operation_timeout_seconds": "30",
            "cleanup_timeout_seconds": "10", "max_output_bytes": "65536",
            "live_execution_approved": False, "live_trading_approved": False,
            "systemd_allowed": False, "persistent_services_allowed": False, "network_policy": "LOOPBACK_ONLY",
        },
        "postgres_authority": {
            "approval_sha256": arguments.postgres_approval_sha256, "bind_host": "127.0.0.1",
            "port": str(arguments.pg_port), "database_name": "trading_agent_disposable_test",
            "pgdata": str(arguments.pgdata), "cluster_name": "trading-agent-disposable-tests",
            "service_roles": ["trading_job_api", "trading_job_worker"],
        },
        "fixture_authority": {"fixture_sha256": fixture_sha256, "provenance": "DETERMINISTIC_PROVIDER_FREE_V1", "path": str(fixture_authority_path)},
        "request": {"job_type": "SNAPSHOT", "actor": "FOUNDATION_VALIDATION", "idempotency_key": "foundation:manual:snapshot:p1-vertical-slice-001", "expected_job_count": "1"},
        "authority_digests": {
            "release": production_authority_sha256,
            "application": components["application"]["artifact_set_sha256"],
            "backend": components["backend"]["artifact_set_sha256"],
            "command": canonical_digest(static["command_manifest"]),
            "semantic": semantic_policy, "fixture": fixture_sha256, "safety": safety_sha256,
            "stage": stage_document["file_set_sha256"],
        },
        "canonical_record_sha256": "0" * 64,
    }
    record["canonical_record_sha256"] = canonical_record_sha256(record)
    approval_path = authority_root / "package6-approval.json"
    approval_sha256 = _canonical_write(approval_path, record, 0o444)
    activation, _ = build_staging_activation_v2(
        authority_sha256=authority_sha256, package6_approval_sha256=approval_sha256,
        safety_snapshot_sha256=safety_sha256, safety_exporter_commit=commit,
        safety_source_fingerprint=safety_source_fingerprint(CANONICAL_SAFETY_SOURCE_ROOT),
        semantic_active_authority_sha256=semantic_sha256,
        semantic_version_manifest_sha256=semantic_sha256,
        semantic_input_fingerprint=semantic_sha256,
        semantic_manifest_version="package6-provider-free-v1", semantic_policy_sha256=semantic_policy,
        semantic_generated_at=generated, semantic_expires_at=expires,
        generated_at=generated, expires_at=expires,
    )
    activation_path = authority_root / "release-activation-v2.json"
    _canonical_write(activation_path, activation, 0o444)
    _canonical_write(
        fixture_authority_path,
        {"backend_commit": commit, "classification": "PACKAGE6_PROVIDER_FREE_FIXTURE", "expires_at": _timestamp(expires), "fixture_path": str(fixture_path), "fixture_sha256": fixture_sha256, "generated_at": _timestamp(generated), "package6_approval_sha256": approval_sha256, "schema_version": 1},
        0o444,
    )
    validate_package6_runtime_approval(
        record,
        Package6ApprovalContext(
            source_commit=commit,
            source_tree=tree,
            operation_ids=("job-api.start", "job-api.stop", "worker.start", "worker.stop"),
            disposable_postgres_approval_sha256=arguments.postgres_approval_sha256,
            postgres_bind_host="127.0.0.1",
            postgres_port=arguments.pg_port,
            postgres_database_name="trading_agent_disposable_test",
            postgres_pgdata=str(arguments.pgdata),
            postgres_cluster_name="trading-agent-disposable-tests",
            postgres_service_roles=("trading_job_api", "trading_job_worker"),
            now=generated,
            source_root=clone,
            staging_scope=STAGING_SCOPE,
            staging_authority_path=authority_path,
            staging_activation_path=activation_path,
            custodian_helper_binary_sha256=custodian_sha256,
        ),
        approval_bytes=canonical_json_bytes(record),
    )
    return {"status": "PREPARED", "source_commit": commit, "source_tree": tree, "live_authorized": False, "network_trading_authorized": False, "production_authorized": False}


def _replace_activation(authority: RuntimeAuthorityV2, safety_sha256: str) -> None:
    material = authority._material
    if material is None:
        raise HostAuthorityError("staging material is unavailable")
    activation, _digest = build_staging_activation_v2(
        authority_sha256=material.authority_sha256,
        package6_approval_sha256=material.package6_approval_sha256,
        safety_snapshot_sha256=safety_sha256,
        safety_exporter_commit=authority.source_commit,
        safety_source_fingerprint=authority.safety.source_fingerprint,
        semantic_active_authority_sha256=material.semantic_active_authority_sha256,
        semantic_version_manifest_sha256=material.semantic_version_manifest_sha256,
        semantic_input_fingerprint=material.semantic_input_fingerprint,
        semantic_manifest_version=material.semantic_manifest_version,
        semantic_policy_sha256=material.semantic_policy_sha256,
        semantic_generated_at=material.semantic_generated_at,
        semantic_expires_at=material.semantic_expires_at,
        generated_at=material.authority_generated_at,
        expires_at=material.authority_expires_at,
    )
    _canonical_write(material.activation_path, activation, 0o444)


def _validate_execution_capability(
    authority: RuntimeAuthorityV2,
    arguments: Sequence[str],
) -> _ValidatedP1Operation:
    if tuple(arguments).count("--execute") != 1:
        raise HostAuthorityError("P1 execution operation is not exact")
    material = authority._material
    if material is None:
        raise HostAuthorityError("staging material is unavailable")
    approval_path = material.activation_path.parent / "package6-approval.json"
    approval, approval_raw = _read_canonical(approval_path)
    if hashlib.sha256(approval_raw).hexdigest() != authority.package6_approval_sha256:
        raise HostAuthorityError("Package 6 approval binding changed")
    postgres = cast(Mapping[str, object], approval.get("postgres_authority"))
    custodian = cast(Mapping[str, object], approval.get("custodian_authority"))
    try:
        package6_capability = validate_package6_runtime_approval(
            approval,
            Package6ApprovalContext(
                source_commit=authority.source_commit,
                source_tree=authority.source_tree,
                operation_ids=(
                    "job-api.start",
                    "job-api.stop",
                    "worker.start",
                    "worker.stop",
                ),
                disposable_postgres_approval_sha256=cast(
                    str, postgres["approval_sha256"]
                ),
                postgres_bind_host="127.0.0.1",
                postgres_port=int(cast(str, postgres["port"])),
                postgres_database_name="trading_agent_disposable_test",
                postgres_pgdata=cast(str, postgres["pgdata"]),
                postgres_cluster_name="trading-agent-disposable-tests",
                postgres_service_roles=(
                    "trading_job_api",
                    "trading_job_worker",
                ),
                now=datetime.now(UTC),
                source_root=ROOT,
                staging_scope=STAGING_SCOPE,
                staging_authority_path=material.authority_path,
                staging_activation_path=material.activation_path,
                custodian_helper_binary_sha256=cast(
                    str, custodian["helper_binary_sha256"]
                ),
            ),
            approval_bytes=approval_raw,
        )
    except Exception as exc:
        raise HostAuthorityError("Package 6 approval is not current") from exc
    if not is_issued_capability(package6_capability):
        raise HostAuthorityError("Package 6 capability was not issued")
    semantic, semantic_raw = _read_canonical(
        authority.runtime_paths.semantic_authority
    )
    semantic_sha256 = hashlib.sha256(semantic_raw).hexdigest()
    expected_semantic = _p1_semantic_document(
        ROOT,
        authority.application_python,
        authority.source_commit,
        authority.source_tree,
    )
    if (
        semantic != expected_semantic
        or semantic_sha256
        != authority.semantic_evidence.active_authority_sha256
        or authority.semantic_evidence.policy_sha256
        != _p1_semantic_policy_digest(
            authority.source_commit,
            authority.runtime_paths.semantic_authority,
            authority.runtime_paths.semantic_input_root,
            semantic_sha256,
        )
    ):
        raise HostAuthorityError("P1 execution operation is not approved")
    operation = _ValidatedP1Operation(
        package6_capability=package6_capability,
        authority_pin=authority._authority_pin,
        semantic_sha256=semantic_sha256,
        arguments=tuple(arguments),
    )
    _ISSUED_P1_OPERATIONS.add(operation)
    return operation


def _consume_p1_operation(
    operation: _ValidatedP1Operation,
    authority: WorkerRuntimeAuthority,
    arguments: Sequence[str],
    *,
    refresh_dynamic_evidence: Callable[[], None],
) -> int:
    if (
        type(operation) is not _ValidatedP1Operation
        or operation not in _ISSUED_P1_OPERATIONS
        or not is_issued_capability(operation.package6_capability)
        or operation.authority_pin != authority.authority_pin
        or operation.semantic_sha256
        != authority.semantic_evidence.active_authority_sha256
        or operation.arguments != tuple(arguments)
    ):
        raise HostAuthorityError("P1 execution capability changed")
    refresher = _issue_p1_staging_safety_authority_refresher(
        authority,
        refresh_dynamic_evidence=refresh_dynamic_evidence,
        operation_token=operation,
        operation_binding=(
            operation.authority_pin,
            operation.semantic_sha256,
            operation.arguments,
        ),
    )
    if not refresher.matches_operation(
        operation_token=operation,
        authority_pin=operation.authority_pin,
        semantic_sha256=operation.semantic_sha256,
        arguments=operation.arguments,
    ):
        raise HostAuthorityError("P1 safety capability changed")
    _ISSUED_P1_OPERATIONS.discard(operation)
    refreshed = refresher.refresh(authority)
    import scripts.run_p1_nautilus_vertical_slice as vertical

    return vertical.main(
        list(arguments),
        worker_authority=refreshed,
        safety_authority_refresher=refresher,
    )


def _activate_and_exec(arguments: Sequence[str]) -> int:
    if set(os.environ) - _CHILD_ENV:
        raise HostAuthorityError("ambient execution environment is not allowlisted")
    initial: WorkerRuntimeAuthority = attest_worker_runtime_authority()
    authority = initial.runtime_authority
    if authority.scope != STAGING_SCOPE or Path(sys.executable) != authority.application_python:
        raise HostAuthorityError("staged application interpreter is not exact")
    operation = _validate_execution_capability(authority, arguments)
    def rotate_dynamic_evidence() -> None:
        _export_p1_safety_snapshot(
            authority.runtime_paths.safety_snapshot,
            exporter_commit=authority.source_commit,
        )
        _replace_activation(
            authority, _digest_file(authority.runtime_paths.safety_snapshot)
        )

    return _consume_p1_operation(
        operation,
        initial,
        arguments,
        refresh_dynamic_evidence=rotate_dynamic_evidence,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare-static")
    for name, kind in (
        ("source-root", Path), ("source-commit", str), ("source-tree", str),
        ("disposable-root", Path), ("python-runtime-archive", Path),
        ("python-runtime-archive-sha256", str), ("uv", Path), ("uv-sha256", str),
        ("wheelhouse", Path), ("wheelhouse-sha256", str),
        ("prior-release-sha256", str), ("postgres-approval", Path),
        ("postgres-approval-sha256", str), ("pgdata", Path), ("pg-port", int),
        ("operator-identity", str), ("reviewer-identity", str),
    ):
        prepare.add_argument(f"--{name}", type=kind, required=True)
    execute = subcommands.add_parser("activate-and-exec")
    execute.add_argument("vertical_arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "prepare-static":
            print(json.dumps(_prepare_static(arguments), sort_keys=True, separators=(",", ":")))
            return 0
        vertical_arguments = arguments.vertical_arguments
        if vertical_arguments[:1] == ["--"]:
            vertical_arguments = vertical_arguments[1:]
        return _activate_and_exec(vertical_arguments)
    except Exception:
        print('{"reason":"P1_PACKAGE6_HOST_AUTHORITY_BLOCKED","status":"BLOCKED"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
