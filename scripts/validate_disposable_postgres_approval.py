#!/usr/bin/env python3
"""Validate exact external authority for disposable PostgreSQL tests.

This module performs validation only.  It never discovers or invokes a
PostgreSQL executable and never opens a database connection.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Iterable, Mapping, NamedTuple, Sequence


SCOPES = frozenset({"DISPOSABLE_PG_RED", "DISPOSABLE_PG_GREEN"})
FORBIDDEN_PORTS = (3002, 8401, 55432)
PGDATA_PREFIX = "/tmp/phase4-postgres-"
BIND_HOST = "127.0.0.1"
CLUSTER_NAME = "trading-agent-disposable-tests"
DISPOSABLE_DATABASE_NAME = "trading_agent_disposable_test"
APPROVAL_SOURCE_BINDING_PATHS = (
    "alembic/versions/0001_phase3_operational_store.py",
    "alembic/versions/0002_quarantine_lineage.py",
    "alembic/versions/0003_contract_lineage_repair.py",
    "alembic/versions/0004_durable_research_jobs.py",
    "alembic/versions/0005_job_plane_role_split.py",
    "alembic/versions/0006_job_transition_database_authority.py",
    "alembic/versions/0007_job_event_chain_authority.py",
    "alembic/versions/0008_trading_domain_ledger.py",
    "alembic/versions/0009_canonical_market_data.py",
    "ops/postgres/provision-job-roles.sql",
    "ops/postgres/provision-roles.sql",
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
        "approved_operations",
        "source_bindings",
        "constraints",
        "red_sql_binding",
        "canonical_record_sha256",
    }
)
_SOURCE_FIELDS = frozenset({"commit", "tree"})
_VALIDITY_FIELDS = frozenset({"approved_at_utc", "expires_at_utc"})
_REVIEW_FIELDS = frozenset(
    {"decision", "operator_identity", "reviewer_identity"}
)
_OPERATION_FIELDS = frozenset({"test_path", "operation_id"})
_SOURCE_BINDING_FIELDS = frozenset({"path", "sha256"})
_CONSTRAINT_FIELDS = frozenset(
    {
        "pgdata_prefix",
        "bind_host",
        "port_allocation",
        "forbidden_ports",
        "cluster_name",
        "database_name",
        "runtime_settings_policy",
    }
)
_RED_SQL_FIELDS = frozenset({"operation_id", "sql_path", "sql_sha256"})

_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECORD_ID = re.compile(r"^DISPOSABLE_POSTGRES_TEST_[A-Z0-9_-]{1,80}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{2,127}$")
_OPERATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_PGDATA = re.compile(r"^/tmp/phase4-postgres-[A-Za-z0-9._-]+(?:/.*)?$")

_PLACEHOLDERS = frozenset(
    {
        "",
        "CHANGEME",
        "PLACEHOLDER",
        "REQUIRES_REVIEWER_INPUT",
        "TBD",
        "TODO",
        "UNKNOWN",
    }
)
_RUNTIME_SETTING_NAMES = frozenset(
    {
        "DATABASE_URL",
        "PGDATA",
        "PGDATABASE",
        "PGHOST",
        "PGHOSTADDR",
        "PGPASSFILE",
        "PGPASSWORD",
        "PGPORT",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGUSER",
        "POSTGRES_URL",
        "TRADING_DATABASE_URL",
    }
)
_RUNTIME_SETTING_PREFIXES = (
    "TRADING_DATABASE_",
    "TRADING_DB_",
    "TRADING_POSTGRES_",
)


class DisposablePostgresApprovalRejected(ValueError):
    """The record or exact invocation context is not authorized."""


class DisposablePostgresApprovalScopeMismatch(DisposablePostgresApprovalRejected):
    """A protected record belongs to another disposable-test scope."""


class DisposablePostgresApprovalContext(NamedTuple):
    scope: str
    source_commit: str
    source_tree: str
    test_path: str
    operation_id: str
    pgdata: str
    bind_host: str
    port: int
    cluster_name: str
    database_name: str
    runtime_setting_names: frozenset[str]
    now: datetime
    red_sql_path: str | None = None
    red_sql_sha256: str | None = None


class _ValidatedRedSqlBinding(NamedTuple):
    operation_id: str
    sql_path: str
    sql_sha256: str


class _ValidatedOperationAuthority(NamedTuple):
    operations: tuple[tuple[str, str], ...]
    approved_pairs: frozenset[tuple[str, str]]
    approved_by_id: Mapping[str, str]
    red_sql_binding: _ValidatedRedSqlBinding | None


def _reject(message: str) -> None:
    raise DisposablePostgresApprovalRejected(message)


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _reject(f"{label} fields are missing or unknown")
    return value


def _reject_placeholders(value: object) -> None:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if (
            normalized in _PLACEHOLDERS
            or "${" in value
            or "{{" in value
            or "<PLACEHOLDER" in normalized
        ):
            _reject("approval record contains a placeholder")
    elif isinstance(value, dict):
        for item in value.values():
            _reject_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            _reject_placeholders(item)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _reject(f"{label} is not an exact UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _reject(f"{label} is not a real UTC timestamp")


def _valid_test_path(value: object) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and value == path.as_posix()
        and len(path.parts) >= 2
        and path.parts[0] == "tests"
        and ".." not in path.parts
        and path.suffix == ".py"
    )


def _valid_sql_path(value: object) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and value == path.as_posix()
        and len(path.parts) >= 2
        and path.parts[0] in {"ops", "tests"}
        and ".." not in path.parts
        and path.suffix == ".sql"
    )


def _validated_source_bindings(
    document: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    bindings = document["source_bindings"]
    if not isinstance(bindings, list) or len(bindings) != len(
        APPROVAL_SOURCE_BINDING_PATHS
    ):
        _reject("source bindings are missing or excessive")
    validated: list[tuple[str, str]] = []
    for index, raw_binding in enumerate(bindings):
        binding = _exact_mapping(
            raw_binding,
            _SOURCE_BINDING_FIELDS,
            f"source binding {index}",
        )
        path = binding["path"]
        digest = binding["sha256"]
        if (
            not isinstance(path, str)
            or path not in APPROVAL_SOURCE_BINDING_PATHS
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            _reject("source binding is invalid")
        validated.append((path, digest))
    if tuple(path for path, _digest in validated) != APPROVAL_SOURCE_BINDING_PATHS:
        _reject("source bindings are not in canonical order")
    return tuple(validated)


def validate_source_binding_files(
    record: Mapping[str, object],
    source_root: Path,
) -> None:
    """Verify the record's exact migration/provisioning bytes without mutation."""

    document = _exact_mapping(record, _TOP_FIELDS, "top-level approval")
    bindings = _validated_source_bindings(document)
    try:
        root = source_root.resolve(strict=True)
    except OSError:
        _reject("source binding root is unavailable")
    for relative_path, expected_digest in bindings:
        path = root / relative_path
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            _reject("source binding file is unavailable")
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or resolved != path
        ):
            _reject("source binding file is not an exact regular source file")
        try:
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _reject("source binding file cannot be read")
        if actual_digest != expected_digest:
            _reject("source binding digest does not match")


def canonical_record_sha256(record: Mapping[str, object]) -> str:
    unsigned = {
        key: value for key, value in record.items() if key != "canonical_record_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validated_operation_authority(
    document: Mapping[str, object],
    scope: str,
) -> _ValidatedOperationAuthority:
    operations = document["approved_operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 256:
        _reject("approved operations are missing or excessive")
    validated_operations: list[tuple[str, str]] = []
    approved_pairs: set[tuple[str, str]] = set()
    approved_by_id: dict[str, str] = {}
    for index, raw_operation in enumerate(operations):
        operation = _exact_mapping(
            raw_operation,
            _OPERATION_FIELDS,
            f"approved operation {index}",
        )
        test_path = operation["test_path"]
        operation_id = operation["operation_id"]
        if not _valid_test_path(test_path):
            _reject("approved test path is invalid")
        if (
            not isinstance(operation_id, str)
            or _OPERATION_ID.fullmatch(operation_id) is None
        ):
            _reject("approved operation id is invalid")
        if operation_id in approved_by_id:
            _reject("approved operation ids must be globally unique")
        pair = (test_path, operation_id)
        approved_by_id[operation_id] = test_path
        approved_pairs.add(pair)
        validated_operations.append(pair)

    red_binding_value = document["red_sql_binding"]
    red_binding: _ValidatedRedSqlBinding | None
    if red_binding_value is None:
        red_binding = None
    else:
        raw_binding = _exact_mapping(
            red_binding_value,
            _RED_SQL_FIELDS,
            "RED SQL binding",
        )
        if scope != "DISPOSABLE_PG_RED":
            _reject("GREEN approval cannot carry a RED SQL binding")
        operation_id = raw_binding["operation_id"]
        sql_path = raw_binding["sql_path"]
        sql_sha256 = raw_binding["sql_sha256"]
        if (
            not isinstance(operation_id, str)
            or _OPERATION_ID.fullmatch(operation_id) is None
            or not _valid_sql_path(sql_path)
            or not isinstance(sql_sha256, str)
            or _SHA256.fullmatch(sql_sha256) is None
        ):
            _reject("RED SQL binding is invalid")
        if operation_id not in approved_by_id:
            _reject("RED SQL operation is not in the approved operation list")
        red_binding = _ValidatedRedSqlBinding(
            operation_id=operation_id,
            sql_path=sql_path,
            sql_sha256=sql_sha256,
        )

    return _ValidatedOperationAuthority(
        operations=tuple(validated_operations),
        approved_pairs=frozenset(approved_pairs),
        approved_by_id=approved_by_id,
        red_sql_binding=red_binding,
    )


def validate_disposable_postgres_approval(
    record: Mapping[str, object],
    context: DisposablePostgresApprovalContext,
) -> None:
    """Purely validate one parsed record against one exact invocation context."""

    if not isinstance(context, DisposablePostgresApprovalContext):
        _reject("approval context is invalid")
    document = _exact_mapping(record, _TOP_FIELDS, "top-level approval")
    _reject_placeholders(document)

    if document["record_kind"] != "DISPOSABLE_POSTGRES_TEST_APPROVAL":
        _reject("record kind is not disposable PostgreSQL test authority")
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 1
    ):
        _reject("schema version is not supported")
    if (
        not isinstance(document["record_id"], str)
        or _RECORD_ID.fullmatch(document["record_id"]) is None
    ):
        _reject("record id is invalid")

    scope = document["scope"]
    if not isinstance(scope, str) or scope not in SCOPES:
        _reject("record scope is invalid")
    if not isinstance(context.scope, str) or context.scope not in SCOPES:
        _reject("requested scope is invalid")
    if scope != context.scope:
        raise DisposablePostgresApprovalScopeMismatch(
            "approval record belongs to another disposable PostgreSQL scope"
        )

    source = _exact_mapping(document["source"], _SOURCE_FIELDS, "source")
    for name in ("commit", "tree"):
        if (
            not isinstance(source[name], str)
            or _GIT_OBJECT.fullmatch(source[name]) is None
        ):
            _reject(f"source {name} is invalid")
    if source["commit"] != context.source_commit:
        _reject("source commit does not match")
    if source["tree"] != context.source_tree:
        _reject("source tree does not match")

    validity = _exact_mapping(document["validity"], _VALIDITY_FIELDS, "validity")
    approved_at = _timestamp(validity["approved_at_utc"], "approval time")
    expires_at = _timestamp(validity["expires_at_utc"], "expiry time")
    if (
        not isinstance(context.now, datetime)
        or context.now.tzinfo is None
        or context.now.utcoffset() is None
    ):
        _reject("validation time is not timezone-aware")
    now = context.now.astimezone(UTC)
    if expires_at <= approved_at or expires_at - approved_at > timedelta(hours=24):
        _reject("approval validity window is invalid")
    if now < approved_at or now >= expires_at:
        _reject("approval record is not currently valid")

    review = _exact_mapping(document["review"], _REVIEW_FIELDS, "review")
    if review["decision"] != "APPROVED":
        _reject("review decision is not approved")
    identities: list[str] = []
    for name in ("operator_identity", "reviewer_identity"):
        identity = review[name]
        if not isinstance(identity, str) or _IDENTITY.fullmatch(identity) is None:
            _reject(f"{name} is invalid")
        identities.append(identity)
    if identities[0].casefold() == identities[1].casefold():
        _reject("operator and reviewer identities must be distinct")

    operation_authority = _validated_operation_authority(document, scope)
    _validated_source_bindings(document)
    if not _valid_test_path(context.test_path):
        _reject("current test path is invalid")
    if (
        not isinstance(context.operation_id, str)
        or _OPERATION_ID.fullmatch(context.operation_id) is None
    ):
        _reject("current operation id is invalid")
    if (
        context.test_path,
        context.operation_id,
    ) not in operation_authority.approved_pairs:
        _reject("test path and operation id are not approved")

    constraints = _exact_mapping(
        document["constraints"],
        _CONSTRAINT_FIELDS,
        "constraints",
    )
    expected_constraints = {
        "pgdata_prefix": PGDATA_PREFIX,
        "bind_host": BIND_HOST,
        "port_allocation": "EXPLICITLY_APPROVED",
        "forbidden_ports": list(FORBIDDEN_PORTS),
        "cluster_name": CLUSTER_NAME,
        "database_name": DISPOSABLE_DATABASE_NAME,
        "runtime_settings_policy": "REJECT_IF_PRESENT",
    }
    if constraints != expected_constraints:
        _reject("disposable PostgreSQL constraints are not exact")

    if (
        not isinstance(context.pgdata, str)
        or _PGDATA.fullmatch(context.pgdata) is None
        or ".." in PurePosixPath(context.pgdata).parts
    ):
        _reject("PGDATA is outside the approved disposable test prefix")
    if context.bind_host != BIND_HOST:
        _reject("PostgreSQL bind is not loopback-only")
    if (
        isinstance(context.port, bool)
        or not isinstance(context.port, int)
        or not 1 <= context.port <= 65535
        or context.port in FORBIDDEN_PORTS
    ):
        _reject("PostgreSQL port is forbidden or invalid")
    if context.cluster_name != CLUSTER_NAME:
        _reject("disposable PostgreSQL cluster marker is missing")
    if context.database_name != DISPOSABLE_DATABASE_NAME:
        _reject("disposable PostgreSQL database identifier is not exact")
    if not isinstance(context.runtime_setting_names, frozenset) or not all(
        isinstance(name, str) for name in context.runtime_setting_names
    ):
        _reject("runtime database settings context is invalid")
    if context.runtime_setting_names:
        _reject("runtime database settings are present")

    red_binding = operation_authority.red_sql_binding

    sql_requested = (
        context.red_sql_path is not None or context.red_sql_sha256 is not None
    )
    if sql_requested:
        if (
            scope != "DISPOSABLE_PG_RED"
            or red_binding is None
            or context.red_sql_path is None
            or context.red_sql_sha256 is None
            or red_binding.operation_id != context.operation_id
            or red_binding.sql_path != context.red_sql_path
            or red_binding.sql_sha256 != context.red_sql_sha256
        ):
            _reject("RED SQL execution is not bound to the reviewed file and operation")

    digest = document["canonical_record_sha256"]
    if (
        not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or digest != canonical_record_sha256(document)
    ):
        _reject("canonical record digest does not match")


def validate_disposable_postgres_approval_record(
    record: Mapping[str, object],
    *,
    expected_scope: str,
    expected_commit: str,
    expected_tree: str,
    expected_sql_sha256: str | None,
    runtime_setting_names: frozenset[str],
    now: datetime,
) -> None:
    """Preflight every approved operation through the harness decision."""

    if not isinstance(expected_scope, str) or expected_scope not in SCOPES:
        _reject("expected scope is invalid")
    if (
        not isinstance(expected_commit, str)
        or _GIT_OBJECT.fullmatch(expected_commit) is None
    ):
        _reject("expected source commit is invalid")
    if (
        not isinstance(expected_tree, str)
        or _GIT_OBJECT.fullmatch(expected_tree) is None
    ):
        _reject("expected source tree is invalid")
    if expected_sql_sha256 is not None and (
        not isinstance(expected_sql_sha256, str)
        or _SHA256.fullmatch(expected_sql_sha256) is None
    ):
        _reject("expected reviewed SQL hash is invalid")

    document = _exact_mapping(record, _TOP_FIELDS, "top-level approval")
    _reject_placeholders(document)
    scope = document["scope"]
    if not isinstance(scope, str) or scope not in SCOPES:
        _reject("record scope is invalid")
    operation_authority = _validated_operation_authority(document, scope)
    _validated_source_bindings(document)
    binding = operation_authority.red_sql_binding
    if expected_sql_sha256 is not None and binding is None:
        _reject("expected reviewed SQL hash has no RED SQL binding")

    for test_path, operation_id in operation_authority.operations:
        requests_sql = binding is not None and operation_id == binding.operation_id
        context = DisposablePostgresApprovalContext(
            scope=expected_scope,
            source_commit=expected_commit,
            source_tree=expected_tree,
            test_path=test_path,
            operation_id=operation_id,
            pgdata="/tmp/phase4-postgres-preflight/data",
            bind_host=BIND_HOST,
            port=49152,
            cluster_name=CLUSTER_NAME,
            database_name=DISPOSABLE_DATABASE_NAME,
            runtime_setting_names=runtime_setting_names,
            now=now,
            red_sql_path=binding.sql_path if requests_sql else None,
            red_sql_sha256=(
                expected_sql_sha256 or binding.sql_sha256
                if requests_sql
                else None
            ),
        )
        validate_disposable_postgres_approval(document, context)


def _reject_duplicate_members(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            _reject("approval record contains a duplicate member")
        document[key] = value
    return document


def load_protected_approval_record(path: Path) -> dict[str, object]:
    """Load a same-user, non-symlinked, non-shared approval record."""

    if not path.is_absolute():
        _reject("approval record path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _reject("approval record cannot be opened as a protected file")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _reject("approval record is not a regular file")
        if metadata.st_uid != os.geteuid():
            _reject("approval record owner does not match the current user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            _reject("approval record permissions are not private")
        if not 1 <= metadata.st_size <= 65536:
            _reject("approval record size is invalid")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject("approval record is not strict UTF-8 JSON")
    if not isinstance(value, dict):
        _reject("approval record top level is not an object")
    return value


def _source_identity(root: Path) -> tuple[str, str]:
    values: list[str] = []
    for revision in ("HEAD", "HEAD^{tree}"):
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", revision],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            _reject("source identity cannot be resolved")
        value = result.stdout.strip()
        if _GIT_OBJECT.fullmatch(value) is None:
            _reject("source identity is invalid")
        values.append(value)
    try:
        status_result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        _reject("source checkout cleanliness cannot be resolved")
    if status_result.stdout:
        _reject("source checkout is not clean")
    return values[0], values[1]


def _is_runtime_setting_name(name: str) -> bool:
    return name in _RUNTIME_SETTING_NAMES or name.startswith(_RUNTIME_SETTING_PREFIXES)


def _runtime_setting_names() -> frozenset[str]:
    return frozenset(name for name in os.environ if _is_runtime_setting_name(name))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one exact disposable PostgreSQL approval record."
    )
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--expected-scope", required=True, choices=sorted(SCOPES))
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-sql-sha256")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        record = load_protected_approval_record(arguments.record)
        validate_disposable_postgres_approval_record(
            record,
            expected_scope=arguments.expected_scope,
            expected_commit=arguments.expected_commit,
            expected_tree=arguments.expected_tree,
            expected_sql_sha256=arguments.expected_sql_sha256,
            runtime_setting_names=_runtime_setting_names(),
            now=_utc_now(),
        )
        validate_source_binding_files(record, Path(__file__).parents[1])
    except DisposablePostgresApprovalRejected as error:
        print(f"REJECTED: {error}", file=sys.stderr)
        return 1
    print("VALID: disposable PostgreSQL authority record matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
