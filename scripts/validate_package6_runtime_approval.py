#!/usr/bin/env python3
"""Validate exact, candidate-bound authority for Package 6 paper runtime.

Validation is deliberately side-effect free: this module does not create
paths, open sockets or databases, inspect credentials, or start processes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Mapping, NamedTuple, NoReturn, cast
import weakref

from packages.runtime_release.paper_backend.provider_free_fixture import (
    ProviderFreeFixture,
    load_provider_free_fixture,
)
from packages.runtime_release.staging_v2 import (
    PACKAGE6_APPROVAL_SHA256_ENV,
    STAGING_ACTIVATION_PATH_ENV,
    STAGING_AUTHORITY_PATH_ENV,
    STAGING_SCOPE,
    STAGING_SCOPE_ENV,
    StagingAuthorityMaterial,
    load_staging_authority_material,
)

_TOP_FIELDS = frozenset(
    {
        "record_kind",
        "schema_version",
        "record_id",
        "scope",
        "source",
        "validity",
        "review",
        "operations",
        "source_bindings",
        "constraints",
        "postgres_authority",
        "fixture_authority",
        "request",
        "authority_digests",
        "canonical_record_sha256",
    }
)
_FIELDS = {
    "source": frozenset({"commit", "tree"}),
    "validity": frozenset({"approved_at_utc", "expires_at_utc"}),
    "review": frozenset(
        {
            "decision",
            "operator_identity",
            "reviewer_identity",
            "runtime_greenlight",
        }
    ),
    "operation": frozenset(
        {
            "operation_id",
            "action",
            "component",
            "argv",
            "cwd",
            "bind_host",
            "port",
            "executable_sha256",
        }
    ),
    "binding": frozenset({"path", "sha256"}),
    "constraints": frozenset(
        {
            "disposable_root",
            "evidence_root",
            "max_processes",
            "startup_timeout_seconds",
            "operation_timeout_seconds",
            "cleanup_timeout_seconds",
            "max_output_bytes",
            "live_execution_approved",
            "live_trading_approved",
            "systemd_allowed",
            "persistent_services_allowed",
            "network_policy",
        }
    ),
    "postgres": frozenset(
        {
            "approval_sha256",
            "bind_host",
            "port",
            "database_name",
            "pgdata",
            "cluster_name",
            "service_roles",
        }
    ),
    "fixture": frozenset({"fixture_sha256", "provenance", "path"}),
    "request": frozenset(
        {
            "job_type",
            "actor",
            "idempotency_key",
            "expected_job_count",
        }
    ),
    "authority_digests": frozenset(
        {"release", "application", "backend", "command", "semantic", "fixture", "safety"}
    ),
}
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}\Z")
_RECORD_ID = re.compile(r"PACKAGE6_RUNTIME_[A-Z0-9_-]{1,80}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{2,127}\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
_IDEMPOTENCY = re.compile(r"foundation:manual:snapshot:[a-z0-9][a-z0-9._-]{2,80}\Z")
_PLACEHOLDERS = frozenset(
    {"", "TBD", "TODO", "UNKNOWN", "CHANGEME", "PLACEHOLDER", "REQUIRES_REVIEWER_INPUT"}
)
_FORBIDDEN_POSTGRES_PORTS = frozenset({0, 22, 80, 443, 3002, 5432, 8401, 55432})
PACKAGE6_JOB_API_PORT = 8401
PACKAGE6_SOURCE_BINDING_PATHS = (
    "Makefile",
    "alembic/versions/0004_durable_research_jobs.py",
    "alembic/versions/0005_job_plane_role_split.py",
    "alembic/versions/0006_job_transition_database_authority.py",
    "alembic/versions/0007_job_event_chain_authority.py",
    "apps/job_api/__init__.py",
    "apps/job_api/app.py",
    "apps/job_api/auth.py",
    "apps/job_api/config.py",
    "apps/job_api/contracts.py",
    "apps/job_api/errors.py",
    "apps/job_api/main.py",
    "docs/implementation/foundation-paper-runtime-dashboard.md",
    "docs/implementation/foundation-paper-runtime-db.md",
    "docs/implementation/foundation-paper-runtime-final.md",
    "docs/implementation/foundation-paper-runtime-request.md",
    "docs/implementation/foundation-paper-runtime-result.md",
    "docs/implementation/foundation-paper-runtime-rollback.md",
    "docs/implementation/foundation-paper-runtime-worker.md",
    "ops/postgres/provision-job-roles.sql",
    "ops/release-v2/provision-root.sh",
    "ops/release-v2/verify-stage.py",
    "packages/__init__.py",
    "packages/job_contracts/fingerprint.py",
    "packages/job_contracts/transitions.py",
    "packages/runtime_release/backend_policy.py",
    "packages/runtime_release/config.py",
    "packages/runtime_release/paper_application/command_registry.py",
    "packages/runtime_release/paper_application/environment.py",
    "packages/runtime_release/paper_application/job_contracts_api.py",
    "packages/runtime_release/paper_application/job_contracts_enums.py",
    "packages/runtime_release/paper_application/job_contracts_init.py",
    "packages/runtime_release/paper_application/job_contracts_payloads.py",
    "packages/runtime_release/paper_application/pyproject.toml",
    "packages/runtime_release/paper_application/results.py",
    "packages/runtime_release/paper_application/runtime_release_config.py",
    "packages/runtime_release/paper_application/runtime_release_init.py",
    "packages/runtime_release/paper_application/runtime_release_job_plane.py",
    "packages/runtime_release/paper_application/runtime_release_semantic.py",
    "packages/runtime_release/paper_application/safety.py",
    "packages/runtime_release/paper_application/safety_exporter.py",
    "packages/runtime_release/paper_application/uv.lock",
    "packages/runtime_release/paper_backend/job_attribution.py",
    "packages/runtime_release/paper_backend/paper_main.py",
    "packages/runtime_release/paper_backend/paper_runtime_manifest.json",
    "packages/runtime_release/paper_backend/provider_free_fixture.py",
    "packages/runtime_release/paper_backend/research_semantics.py",
    "packages/runtime_release/staging_v2.py",
    "packages/runtime_release/v2.py",
    "packages/safety_evidence.py",
    "schemas/package6-paper-runtime-approval.schema.json",
    "scripts/run_required_runtime_pytest.py",
    "scripts/validate_package6_runtime_approval.py",
    "services/__init__.py",
    "services/job_store/__init__.py",
    "services/job_store/config.py",
    "services/job_store/errors.py",
    "services/job_store/records.py",
    "services/job_store/repository.py",
    "services/job_store/worker_repository.py",
    "services/job_worker/__init__.py",
    "services/job_worker/artifacts.py",
    "services/job_worker/environment.py",
    "services/job_worker/errors.py",
    "services/job_worker/main.py",
    "services/job_worker/process_runner.py",
    "services/job_worker/recovery.py",
    "services/job_worker/results.py",
    "services/job_worker/safety_state.py",
    "services/job_worker/worker.py",
    "services/paper_runtime/__init__.py",
    "services/paper_runtime/controller.py",
    "services/paper_runtime/evidence.py",
    "services/paper_runtime/integration.py",
    "services/safety_state_exporter/__init__.py",
    "tests/foundation/test_package6_runtime_integration.py",
)
_FORBIDDEN_TERMS = (
    "systemctl",
    "systemd",
    "service ",
    "/etc/",
    "/opt/trading-agent",
    "/var/lib/trading-agent",
    "production",
    "binance",
    "coinbase",
    "kraken",
    "alpaca",
    "broker",
    "exchange",
    "/order",
    "create_order",
    "place_order",
    "credential",
    "secret",
    "live-trading",
    "live_execution",
)


class Package6ApprovalRejected(ValueError):
    """The approval or its exact invocation context is not authorized."""


class Package6ApprovalContext(NamedTuple):
    source_commit: str
    source_tree: str
    operation_ids: tuple[str, ...]
    disposable_postgres_approval_sha256: str
    postgres_bind_host: str
    postgres_port: int
    postgres_database_name: str
    postgres_pgdata: str
    postgres_cluster_name: str
    postgres_service_roles: tuple[str, ...]
    now: datetime
    source_root: Path
    staging_scope: str
    staging_authority_path: Path
    staging_activation_path: Path


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class ValidatedOperation:
    operation_id: str
    action: str
    component: str
    argv: tuple[str, ...]
    cwd: Path
    bind_host: str | None
    port: int | None
    executable_sha256: str | None


@dataclass(frozen=True, slots=True)
class ValidatedPostgresAuthority:
    approval_sha256: str
    bind_host: str
    port: int
    database_name: str
    pgdata: Path
    cluster_name: str
    service_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidatedFixtureAuthority:
    sha256: str
    provenance: str
    path: Path


@dataclass(frozen=True, slots=True)
class ValidatedRequestAuthority:
    job_type: str
    actor: str
    idempotency_key: str
    expected_job_count: int


@dataclass(frozen=True, slots=True)
class ValidatedListenerAuthority:
    host: str
    port: int


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class ValidatedPackage6Capability:
    source_commit: str
    source_tree: str
    operation_ids: tuple[str, ...]
    operations: Mapping[str, ValidatedOperation]
    source_bindings: tuple[tuple[str, str], ...]
    approval_sha256: str
    canonical_record_sha256: str
    fixture_sha256: str
    postgres: ValidatedPostgresAuthority
    fixture: ValidatedFixtureAuthority
    request: ValidatedRequestAuthority
    listener: ValidatedListenerAuthority
    authority_digests: Mapping[str, str]
    disposable_root: Path
    evidence_root: Path
    max_processes: int
    startup_timeout_seconds: int
    operation_timeout_seconds: int
    cleanup_timeout_seconds: int
    max_output_bytes: int
    source_root: Path
    staging_material: StagingAuthorityMaterial
    fixture_material: ProviderFreeFixture

    def __repr__(self) -> str:
        return (
            "ValidatedPackage6Capability("
            f"candidate={self.source_commit[:12]}, operations={len(self.operations)})"
        )


_ISSUED_CAPABILITIES: weakref.WeakSet[ValidatedPackage6Capability] = weakref.WeakSet()


def is_issued_capability(value: object) -> bool:
    return isinstance(value, ValidatedPackage6Capability) and value in _ISSUED_CAPABILITIES


def _reject(message: str) -> NoReturn:
    raise Package6ApprovalRejected(message)


def _exact(value: object, fields: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _reject(f"{label} fields are missing or unknown")
    return value


def _no_placeholders(value: object) -> None:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if (
            normalized in _PLACEHOLDERS
            or "${" in value
            or "{{" in value
            or "<PLACEHOLDER" in normalized
        ):
            _reject("approval contains a placeholder")
    elif isinstance(value, dict):
        for item in value.values():
            _no_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            _no_placeholders(item)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _reject("approval timestamp is malformed")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _reject("approval timestamp is not real")


def _valid_identity(value: object) -> bool:
    return isinstance(value, str) and _IDENTITY.fullmatch(value) is not None


def _absolute_tmp_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or "\\" in value:
        _reject(f"{label} root is invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != os.path.normpath(value):
        _reject(f"{label} root is not canonical")
    try:
        path.relative_to("/tmp")
    except ValueError:
        _reject(f"{label} root must be below /tmp")
    forbidden = str(path).lower()
    if any(term in forbidden for term in ("/production", "/etc/", "/var/", "/opt/")):
        _reject(f"{label} root is a production root")
    return path


def _relative_binding(value: object) -> str:
    if not isinstance(value, str) or "\\" in value:
        _reject("source binding path is invalid")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or not pure.parts
        or pure.as_posix() != value
    ):
        _reject("source binding path is invalid")
    return value


def canonical_record_sha256(record: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {key: value for key, value in record.items() if key != "canonical_record_sha256"},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def package6_authority_digests(
    material: StagingAuthorityMaterial,
    fixture: ProviderFreeFixture,
) -> Mapping[str, str]:
    """Map validated candidate bytes to the seven stable approval meanings."""

    return MappingProxyType(
        {
            "release": material.production_release_authority_sha256,
            "application": material.application_artifact_sha256,
            "backend": material.backend_artifact_sha256,
            "command": material.command_authority_sha256,
            "semantic": material.semantic_policy_sha256,
            "fixture": fixture.sha256,
            "safety": material.safety_snapshot_sha256,
        }
    )


def validate_source_binding_files(
    record: Mapping[str, object], source_root: Path
) -> None:
    document = _exact(record, _TOP_FIELDS, "top-level approval")
    try:
        root_info = source_root.lstat()
        root = source_root.resolve(strict=True)
    except OSError:
        _reject("source binding root is unavailable")
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        _reject("source binding root is unsafe")
    bindings = document["source_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(
        PACKAGE6_SOURCE_BINDING_PATHS
    ):
        _reject("source bindings are missing or excessive")
    observed_paths: list[str] = []
    for raw in bindings:
        binding = _exact(raw, _FIELDS["binding"], "source binding")
        relative = _relative_binding(binding["path"])
        observed_paths.append(relative)
        digest = binding["sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            _reject("source binding digest is invalid")
        path = root / relative
        try:
            info = path.lstat()
        except OSError:
            _reject("source binding file is unavailable")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            _reject("source binding is not an exact regular file")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            _reject("source binding file is unavailable")
        if (
            resolved != path
        ):
            _reject("source binding is not an exact regular file")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            _reject("source binding ownership or mode is unsafe")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            _reject("source binding digest does not match")
    if tuple(observed_paths) != PACKAGE6_SOURCE_BINDING_PATHS:
        _reject("source bindings are not in canonical order")


def _operation(raw: object) -> ValidatedOperation:
    operation = _exact(raw, _FIELDS["operation"], "operation")
    operation_id = operation["operation_id"]
    action = operation["action"]
    component = operation["component"]
    argv = operation["argv"]
    executable_sha256 = operation["executable_sha256"]
    if not isinstance(operation_id, str) or _ID.fullmatch(operation_id) is None:
        _reject("operation id is invalid")
    if action not in {"START", "STOP"}:
        _reject("operation action is not approved")
    if component not in {"JOB_API", "WORKER"}:
        _reject("operation component is not approved")
    if not isinstance(argv, list) or len(argv) > 16 or any(
        not isinstance(item, str) or not item or len(item) > 512 for item in argv
    ):
        _reject("operation argv is invalid")
    command = " ".join(argv).lower()
    if any(term in command for term in _FORBIDDEN_TERMS):
        _reject("operation argv contains a forbidden authority")
    if action == "STOP":
        if argv or executable_sha256 is not None:
            _reject("STOP argv must be empty signal authority")
    elif not argv:
        _reject("START argv is invalid")
    elif (
        not isinstance(executable_sha256, str)
        or _SHA256.fullmatch(executable_sha256) is None
    ):
        _reject("START executable digest is invalid")
    if action == "START" and component == "JOB_API" and tuple(argv[1:]) != (
        "-I",
        "-m",
        "apps.job_api.main",
    ):
        _reject("operation argv shape is not the exact Job API entrypoint")
    if action == "START" and component == "WORKER" and tuple(argv[1:]) != (
        "-I",
        "-m",
        "services.job_worker.main",
    ):
        _reject("operation argv shape is not the exact worker entrypoint")
    if action == "START" and component in {"JOB_API", "WORKER"} and (
        Path(argv[0]).name not in {"python3.11", "python"}
        or not Path(argv[0]).is_absolute()
    ):
        _reject("operation executable is not the exact bound interpreter")
    cwd = _absolute_tmp_path(operation["cwd"], "operation cwd")
    if action == "START" and component in {"JOB_API", "WORKER"} and argv[0] != str(
        cwd / ".venv/bin/python3.11"
    ):
        _reject("operation executable is outside the immutable artifact root")
    host, port = operation["bind_host"], operation["port"]
    if component == "JOB_API":
        if host != "127.0.0.1":
            _reject("listener host must be exact loopback")
        if (
            type(port) is not int
            or port != PACKAGE6_JOB_API_PORT
        ):
            _reject("listener port is forbidden or ambiguous")
    elif host is not None or port is not None:
        _reject("non-listener operation cannot carry a host or port")
    value = ValidatedOperation()
    for name, item in (
        ("operation_id", operation_id),
        ("action", action),
        ("component", component),
        ("argv", tuple(argv)),
        ("cwd", cwd),
        ("bind_host", host),
        ("port", port),
        ("executable_sha256", executable_sha256),
    ):
        object.__setattr__(value, name, item)
    return value


def validate_package6_runtime_approval(
    record: Mapping[str, object],
    context: Package6ApprovalContext,
    *,
    approval_bytes: bytes | None = None,
) -> ValidatedPackage6Capability:
    if not isinstance(context, Package6ApprovalContext):
        raise TypeError("exact Package 6 approval context is required")
    document = _exact(record, _TOP_FIELDS, "top-level approval")
    exact_approval_bytes = (
        approval_bytes
        if approval_bytes is not None
        else json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    if approval_bytes is not None:
        try:
            decoded_bytes = json.loads(approval_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _reject("approval bytes are invalid")
        if decoded_bytes != document:
            _reject("approval bytes do not match the validated record")
    _no_placeholders(document)
    source = _exact(document["source"], _FIELDS["source"], "source")
    validity = _exact(document["validity"], _FIELDS["validity"], "validity")
    review = _exact(document["review"], _FIELDS["review"], "review")
    constraints = _exact(
        document["constraints"], _FIELDS["constraints"], "constraints"
    )
    postgres = _exact(document["postgres_authority"], _FIELDS["postgres"], "PostgreSQL")
    fixture = _exact(document["fixture_authority"], _FIELDS["fixture"], "fixture")
    request = _exact(document["request"], _FIELDS["request"], "request")
    authority_digests = _exact(
        document["authority_digests"],
        _FIELDS["authority_digests"],
        "authority digests",
    )
    if (
        document["record_kind"] != "PACKAGE6_PAPER_RUNTIME_APPROVAL"
        or document["schema_version"] != 2
        or not isinstance(document["record_id"], str)
        or _RECORD_ID.fullmatch(document["record_id"]) is None
        or document["scope"] != "PACKAGE6_PAPER_RUNTIME"
    ):
        _reject("approval identity is invalid")
    if (
        source["commit"] != context.source_commit
        or source["tree"] != context.source_tree
        or not isinstance(source["commit"], str)
        or _GIT.fullmatch(source["commit"]) is None
        or not isinstance(source["tree"], str)
        or _GIT.fullmatch(source["tree"]) is None
    ):
        _reject("approval candidate does not match")
    approved_at, expires_at = _timestamp(validity["approved_at_utc"]), _timestamp(
        validity["expires_at_utc"]
    )
    if expires_at <= approved_at or expires_at - approved_at > timedelta(minutes=30):
        _reject("approval validity window is excessive")
    if context.now.tzinfo is None or not approved_at <= context.now <= expires_at:
        _reject("approval is expired or not yet valid")
    if (
        review["decision"] != "APPROVED"
        or review["runtime_greenlight"] != "APPROVE PACKAGE 6 RUNTIME"
        or any(not _valid_identity(review[key]) for key in (
            "operator_identity",
            "reviewer_identity",
        ))
    ):
        _reject("review authority is invalid")
    raw_operations = document["operations"]
    if not isinstance(raw_operations, list) or not 2 <= len(raw_operations) <= 8:
        _reject("approved operations are missing or excessive")
    operations = tuple(_operation(item) for item in raw_operations)
    ids = tuple(item.operation_id for item in operations)
    if ids != (
        "job-api.start",
        "job-api.stop",
        "worker.start",
        "worker.stop",
    ):
        _reject("approved operation set lacks canonical START and STOP operations")
    components: dict[str, set[str]] = {}
    for item in operations:
        components.setdefault(item.component, set()).add(item.action)
    if any(actions != {"START", "STOP"} for actions in components.values()):
        _reject("runtime startup requires explicit paired START and STOP actions")
    if len({item.cwd for item in operations}) != 1:
        _reject("operations do not share one immutable artifact root")
    for component in ("JOB_API", "WORKER"):
        pair = [item for item in operations if item.component == component]
        if (
            len(pair) != 2
            or pair[0].cwd != pair[1].cwd
            or pair[0].bind_host != pair[1].bind_host
            or pair[0].port != pair[1].port
        ):
            _reject("START and STOP operation identity does not match")
    if ids != context.operation_ids or len(ids) != len(set(ids)):
        _reject("approved operation set does not match")
    bindings = document["source_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(
        PACKAGE6_SOURCE_BINDING_PATHS
    ):
        _reject("source bindings are missing or excessive")
    validated_bindings = []
    for raw in bindings:
        binding = _exact(raw, _FIELDS["binding"], "source binding")
        relative = _relative_binding(binding["path"])
        digest = binding["sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            _reject("source binding digest is invalid")
        validated_bindings.append((relative, digest))
    if tuple(path for path, _digest in validated_bindings) != PACKAGE6_SOURCE_BINDING_PATHS:
        _reject("source bindings are not in canonical order")
    disposable_root = _absolute_tmp_path(constraints["disposable_root"], "disposable")
    evidence_root = _absolute_tmp_path(constraints["evidence_root"], "evidence")
    if disposable_root == evidence_root or disposable_root in evidence_root.parents:
        _reject("evidence root must be outside disposable root")
    bounded = {
        "max_processes": (1, 4),
        "startup_timeout_seconds": (1, 60),
        "operation_timeout_seconds": (1, 600),
        "cleanup_timeout_seconds": (1, 60),
        "max_output_bytes": (1024, 1_048_576),
    }
    for key, (minimum, maximum) in bounded.items():
        value = constraints[key]
        if type(value) is not int or not minimum <= value <= maximum:
            _reject(f"{key.replace('_', ' ')} is unbounded")
    max_processes = cast(int, constraints["max_processes"])
    if max_processes < len(components):
        _reject("maximum process count is smaller than approved components")
    if constraints["systemd_allowed"] is not False:
        _reject("systemd operations are forbidden")
    if constraints["persistent_services_allowed"] is not False:
        _reject("persistent services are forbidden")
    if (
        constraints["live_execution_approved"] is not False
        or constraints["live_trading_approved"] is not False
    ):
        _reject("live authority must remain false")
    if constraints["network_policy"] != "LOOPBACK_ONLY":
        _reject("network policy must be loopback only")
    if (
        not isinstance(postgres["service_roles"], list)
        or any(not isinstance(role, str) for role in postgres["service_roles"])
    ):
        _reject("PostgreSQL service role identity is invalid")
    if (
        postgres["approval_sha256"] != context.disposable_postgres_approval_sha256
        or postgres["bind_host"] != context.postgres_bind_host
        or postgres["port"] != context.postgres_port
        or postgres["database_name"] != context.postgres_database_name
        or postgres["pgdata"] != context.postgres_pgdata
        or postgres["cluster_name"] != context.postgres_cluster_name
        or tuple(postgres["service_roles"])
        != context.postgres_service_roles
    ):
        _reject("PostgreSQL authority does not match separately validated approval")
    if (
        postgres["bind_host"] != "127.0.0.1"
        or type(postgres["port"]) is not int
        or postgres["port"] in _FORBIDDEN_POSTGRES_PORTS
        or postgres["database_name"] != "trading_agent_disposable_test"
        or postgres["cluster_name"] != "trading-agent-disposable-tests"
        or postgres["service_roles"]
        != ["trading_job_api", "trading_job_worker"]
    ):
        _reject("PostgreSQL target is not disposable loopback")
    pgdata = _absolute_tmp_path(postgres["pgdata"], "PostgreSQL data")
    if disposable_root not in (pgdata, *pgdata.parents):
        _reject("PostgreSQL data is outside disposable root")
    if (
        fixture["provenance"] != "DETERMINISTIC_PROVIDER_FREE_V1"
        or not isinstance(fixture["fixture_sha256"], str)
        or _SHA256.fullmatch(fixture["fixture_sha256"]) is None
    ):
        _reject("fixture authority is not deterministic and provider-free")
    fixture_path = _absolute_tmp_path(fixture["path"], "fixture authority")
    if (
        request["job_type"] != "SNAPSHOT"
        or request["actor"] != "FOUNDATION_VALIDATION"
        or request["expected_job_count"] != 1
        or not isinstance(request["idempotency_key"], str)
        or _IDEMPOTENCY.fullmatch(request["idempotency_key"]) is None
    ):
        _reject("runtime request is not the single approved SNAPSHOT")
    if any(
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
        for value in authority_digests.values()
    ) or authority_digests["fixture"] != fixture["fixture_sha256"]:
        _reject("authority digest set is invalid")
    digest = document["canonical_record_sha256"]
    if (
        not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or digest != canonical_record_sha256(document)
    ):
        _reject("approval canonical digest does not match")
    approval_sha256 = hashlib.sha256(exact_approval_bytes).hexdigest()
    if context.staging_scope != STAGING_SCOPE:
        _reject("staging scope does not match")
    try:
        material = load_staging_authority_material(
            {
                STAGING_SCOPE_ENV: context.staging_scope,
                STAGING_AUTHORITY_PATH_ENV: str(context.staging_authority_path),
                STAGING_ACTIVATION_PATH_ENV: str(context.staging_activation_path),
                PACKAGE6_APPROVAL_SHA256_ENV: approval_sha256,
            },
            now=context.now,
        )
        loaded_fixture = load_provider_free_fixture(
            fixture_path,
            expected_backend_commit=context.source_commit,
            expected_package6_approval_sha256=approval_sha256,
            now=context.now,
            trusted_uid=os.geteuid(),
        )
    except Exception:
        _reject("staging or fixture authority is unavailable")
    if (
        material.source_commit != context.source_commit
        or material.source_tree != context.source_tree
        or material.authority_path != context.staging_authority_path
        or material.activation_path != context.staging_activation_path
        or material.package6_approval_sha256 != approval_sha256
        or loaded_fixture.provenance != fixture["provenance"]
        or fixture["fixture_sha256"] != loaded_fixture.sha256
    ):
        _reject("staging or fixture authority does not match approval")
    expected_operation_root = material.application_root
    for operation in operations:
        if operation.cwd != expected_operation_root:
            _reject("operation cwd does not match staged application artifact")
        if operation.action == "START" and (
            operation.argv[0] != str(material.application_python)
            or operation.executable_sha256 != material.application_python_sha256
        ):
            _reject("operation interpreter does not match staged application artifact")
    validate_source_binding_files(document, context.source_root)
    recomputed_digests = package6_authority_digests(material, loaded_fixture)
    if dict(authority_digests) != dict(recomputed_digests):
        _reject("authority digest set does not match staged candidate")
    capability = ValidatedPackage6Capability()
    postgres_approval_sha256 = cast(str, postgres["approval_sha256"])
    postgres_bind_host = cast(str, postgres["bind_host"])
    postgres_port = cast(int, postgres["port"])
    postgres_database_name = cast(str, postgres["database_name"])
    postgres_cluster_name = cast(str, postgres["cluster_name"])
    postgres_service_roles = cast(list[str], postgres["service_roles"])
    fixture_sha256 = cast(str, fixture["fixture_sha256"])
    fixture_provenance = cast(str, fixture["provenance"])
    validated_request = ValidatedRequestAuthority(
        job_type=cast(str, request["job_type"]),
        actor=cast(str, request["actor"]),
        idempotency_key=cast(str, request["idempotency_key"]),
        expected_job_count=cast(int, request["expected_job_count"]),
    )
    values = {
        "source_commit": context.source_commit,
        "source_tree": context.source_tree,
        "operation_ids": ids,
        "operations": MappingProxyType({item.operation_id: item for item in operations}),
        "source_bindings": tuple(validated_bindings),
        "approval_sha256": approval_sha256,
        "canonical_record_sha256": digest,
        "fixture_sha256": fixture_sha256,
        "postgres": ValidatedPostgresAuthority(
            approval_sha256=postgres_approval_sha256,
            bind_host=postgres_bind_host,
            port=postgres_port,
            database_name=postgres_database_name,
            pgdata=pgdata,
            cluster_name=postgres_cluster_name,
            service_roles=tuple(postgres_service_roles),
        ),
        "fixture": ValidatedFixtureAuthority(
            sha256=fixture_sha256,
            provenance=fixture_provenance,
            path=fixture_path,
        ),
        "request": validated_request,
        "listener": ValidatedListenerAuthority(
            host="127.0.0.1", port=PACKAGE6_JOB_API_PORT
        ),
        "authority_digests": MappingProxyType(dict(authority_digests)),
        "disposable_root": disposable_root,
        "evidence_root": evidence_root,
        "source_root": context.source_root,
        "staging_material": material,
        "fixture_material": loaded_fixture,
        **{key: constraints[key] for key in bounded},
    }
    for name, value in values.items():
        object.__setattr__(capability, name, value)
    _ISSUED_CAPABILITIES.add(capability)
    return capability


__all__ = [
    "Package6ApprovalContext",
    "Package6ApprovalRejected",
    "PACKAGE6_JOB_API_PORT",
    "PACKAGE6_SOURCE_BINDING_PATHS",
    "ValidatedFixtureAuthority",
    "ValidatedListenerAuthority",
    "ValidatedOperation",
    "ValidatedPackage6Capability",
    "ValidatedPostgresAuthority",
    "ValidatedRequestAuthority",
    "canonical_record_sha256",
    "is_issued_capability",
    "package6_authority_digests",
    "validate_package6_runtime_approval",
    "validate_source_binding_files",
]
