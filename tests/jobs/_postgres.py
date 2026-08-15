from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
from types import MappingProxyType
from typing import Callable, NamedTuple

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from scripts.validate_disposable_postgres_approval import (
    BIND_HOST,
    CLUSTER_NAME,
    DISPOSABLE_DATABASE_NAME,
    FORBIDDEN_PORTS,
    SCOPES,
    DisposablePostgresApprovalContext,
    DisposablePostgresApprovalRejected,
    _runtime_setting_names,
    _source_identity,
    _utc_now,
    canonical_record_sha256,
    load_protected_approval_record,
    validate_disposable_postgres_approval,
    validate_source_binding_files,
)
from scripts.validate_disposable_postgres_fixture_plan import (
    DisposablePostgresFixturePlanRejected,
    DisposablePostgresFixtureSlot,
    lifecycle_actions_for,
    load_protected_fixture_plan,
    validate_disposable_postgres_fixture_plan,
)
from trading_control.db import DatabaseSettings


POSTGRES_BIN = Path("/usr/lib/postgresql/16/bin")
ROOT = Path(__file__).parents[2]
DISPOSABLE_DATABASE = DISPOSABLE_DATABASE_NAME
RUNTIME_DATABASE_IDENTIFIER = "trading_agent"
POSTGRES_EXECUTABLES = frozenset(
    {"initdb", "pg_ctl", "psql", "pg_dump", "pg_restore"}
)

_AUTHORITY_TOKEN = object()
_PLANNED_SLOT_COUNTERS: dict[tuple[str, str, str], int] = {}


class _ReviewedSqlSnapshot(NamedTuple):
    path: str
    sha256: str
    content: bytes


class _ValidatedDisposablePostgresAuthority(NamedTuple):
    token: object
    context: DisposablePostgresApprovalContext
    record_digest: str
    reviewed_sql: _ReviewedSqlSnapshot | None


class _RedDerivationState:
    __slots__ = ("phase",)

    def __init__(self) -> None:
        self.phase = "VALIDATED"


class _DisposablePostgresSession(NamedTuple):
    authority: _ValidatedDisposablePostgresAuthority
    root: Path
    data: Path
    socket_dir: Path
    home: Path
    log: Path
    dump: Path
    environment: tuple[tuple[str, str], ...]
    derivation_state: _RedDerivationState


class _PostgresCommand(NamedTuple):
    operation: str
    allowed_scopes: frozenset[str]
    binary: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    input_bytes: bytes | None
    reviewed_sql_path: str | None
    reviewed_sql_sha256: str | None


class DisposablePostgresCleanupError(RuntimeError):
    """The disposable cluster could not be proven stopped."""


class _DisposableRedDerivationWorkflow:
    __slots__ = ("_database", "_execute_reviewed")

    def __init__(
        self,
        database: psycopg.Connection,
        execute_reviewed: Callable[[], psycopg.Cursor],
    ) -> None:
        self._database = database
        self._execute_reviewed = execute_reviewed

    @property
    def database(self) -> psycopg.Connection:
        return self._database

    def execute_reviewed_sql(self) -> psycopg.Cursor:
        return self._execute_reviewed()


class DisposableRestoreWorkflow:
    __slots__ = ("_phase", "_source", "_source_session", "_target", "_target_session")

    def __init__(
        self,
        source: DatabaseSettings,
        source_session: _DisposablePostgresSession,
        target: DatabaseSettings,
        target_session: _DisposablePostgresSession,
    ) -> None:
        self._phase = "PREPARE_SOURCE"
        self._source = source
        self._source_session = source_session
        self._target = target
        self._target_session = target_session

    @property
    def source(self) -> DatabaseSettings:
        if self._phase not in {"PREPARE_SOURCE", "RESTORED"}:
            raise DisposablePostgresApprovalRejected(
                "disposable restore workflow is closed"
            )
        return self._source

    def restore(self) -> DatabaseSettings:
        if self._phase != "PREPARE_SOURCE":
            raise DisposablePostgresApprovalRejected(
                "disposable restore action is unavailable"
            )
        self._phase = "RESTORING"
        try:
            _prepare_secure_dump(self._source_session)
            _run_pg_dump(self._source_session)
            _validate_secure_dump(self._source_session)
            _drop_disposable_database(self._target)
            _prepare_empty_restore_target(self._target_session)
            _prepare_secure_dump(self._target_session)
            shutil.copyfile(
                self._source_session.dump,
                self._target_session.dump,
            )
            _validate_secure_dump(self._target_session)
            _run_pg_restore(self._target_session)
        except Exception:
            self._phase = "FAILED"
            raise
        self._phase = "RESTORED"
        return self._target

    def _close(self) -> None:
        self._phase = "CLOSED"


_BOTH_DISPOSABLE_SCOPES = frozenset(SCOPES)
_GREEN_ONLY_SCOPE = frozenset({"DISPOSABLE_PG_GREEN"})
_RED_ONLY_SCOPE = frozenset({"DISPOSABLE_PG_RED"})
_RED_DERIVATION_REVISION = "0006_job_transition_database_authority"
_REVIEWED_ACTION_REJECTION = (
    "reviewed RED action is unavailable for this derivation state"
)
_RED_SETUP_REJECTION = (
    "RED setup operation is unavailable for this preparation state"
)
_POSTGRES_OPERATION_SCOPES = MappingProxyType(
    {
        "initdb": _BOTH_DISPOSABLE_SCOPES,
        "pg_ctl_start": _BOTH_DISPOSABLE_SCOPES,
        "pg_ctl_status": _BOTH_DISPOSABLE_SCOPES,
        "pg_ctl_stop": _BOTH_DISPOSABLE_SCOPES,
        "green_base_psql": _GREEN_ONLY_SCOPE,
        "green_job_psql": _GREEN_ONLY_SCOPE,
        "green_restore_target_psql": _GREEN_ONLY_SCOPE,
        "red_base_psql": _RED_ONLY_SCOPE,
        "red_job_psql": _RED_ONLY_SCOPE,
        "pg_dump": _GREEN_ONLY_SCOPE,
        "pg_restore": _GREEN_ONLY_SCOPE,
    }
)


_ROLE_PASSWORDS = {
    "trading_owner": "test-only-owner-credential-0001",
    "trading_migrator": "test-only-migrator-credential-0002",
    "trading_reader": "test-only-reader-credential-0003",
    "trading_jobs": "test-only-shared-jobs-credential-0004",
    "trading_job_api": "test-only-job-api-credential-0005",
    "trading_job_worker": "test-only-job-worker-credential-0006",
    "trading_job_scheduler": "test-only-job-scheduler-credential-0007",
}
_DISPOSABLE_LOGIN_ROLES = frozenset(
    {
        "trading_owner",
        "trading_migrator",
        "trading_reader",
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    }
)
_BASE_ROLE_SQL_BYTES = (ROOT / "ops/postgres/provision-roles.sql").read_bytes()
_JOB_ROLE_SQL_BYTES = (
    ROOT / "ops/postgres/provision-job-roles.sql"
).read_bytes()


def _upgrade_to_revision(settings: DatabaseSettings, revision: str) -> None:
    engine = create_engine(settings.sqlalchemy_url())
    try:
        with engine.begin() as connection:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
    finally:
        engine.dispose()


def upgrade_to_head(settings: DatabaseSettings) -> None:
    """Upgrade only the caller-provided disposable database."""

    _upgrade_to_revision(settings, "head")


def _provision_base_roles(session: object) -> None:
    validated = _validated_session(session)
    scope = validated.authority.context.scope
    if scope == "DISPOSABLE_PG_GREEN":
        _run_green_base_psql(validated)
    else:
        state = _require_red_derivation_phase(validated, "CLUSTER_STARTED")
        _run_red_base_psql(validated)
        state.phase = "RED_BASE_SETUP"


def _provision_job_roles(session: object) -> None:
    validated = _validated_session(session)
    scope = validated.authority.context.scope
    if scope == "DISPOSABLE_PG_GREEN":
        _run_green_job_psql(validated)
    else:
        state = _require_red_derivation_phase(validated, "RED_UPGRADED_0004")
        _run_red_job_psql(validated)
        state.phase = "RED_PREPARED"


def _owner_settings(cluster: dict[str, object]) -> DatabaseSettings:
    return DatabaseSettings(
        host=cluster["host"],
        port=cluster["port"],
        database=DISPOSABLE_DATABASE,
        user="trading_owner",
        password=_ROLE_PASSWORDS["trading_owner"],
    )


@contextmanager
def disposable_database(
    *,
    operation_id: str | None = None,
    red_sql_file: Path | None = None,
    planned: bool = False,
):
    """Yield an isolated exact-0004 database ready for the 0005 migration."""

    with _disposable_postgres_session(
        operation_id=operation_id,
        red_sql_file=red_sql_file,
        planned=planned,
        expected_lifecycle_actions=lifecycle_actions_for("MIGRATE"),
    ) as session:
        yield _prepare_disposable_database(session)


@contextmanager
def disposable_restore_workflow(
    *,
    operation_id: str | None = None,
    planned: bool = False,
):
    """Yield one approval-bound source/target custom-format restore workflow."""

    with _disposable_postgres_session(
        operation_id=operation_id,
        planned=planned,
        expected_lifecycle_actions=lifecycle_actions_for("RESTORE"),
    ) as source_session:
        source = _prepare_disposable_database(source_session)
        with _disposable_postgres_session(
            operation_id=operation_id,
            planned=planned,
            expected_lifecycle_actions=lifecycle_actions_for("RESTORE"),
        ) as target_session:
            target = _prepare_disposable_database(target_session)
            workflow = DisposableRestoreWorkflow(
                source,
                source_session,
                target,
                target_session,
            )
            try:
                yield workflow
            finally:
                workflow._close()


@contextmanager
def disposable_red_derivation_database(
    *,
    operation_id: str | None = None,
    red_sql_file: Path | None = None,
):
    """Yield the one-shot reviewed-SQL action after exact RED preparation."""

    _record, scope, _stable_operation_id = _approval_inputs(operation_id)
    if scope != "DISPOSABLE_PG_RED" or red_sql_file is None:
        raise DisposablePostgresApprovalRejected(_REVIEWED_ACTION_REJECTION)
    with _disposable_postgres_session(
        operation_id=operation_id,
        red_sql_file=red_sql_file,
        expected_lifecycle_actions=lifecycle_actions_for("MIGRATE"),
    ) as session:
        owner = _prepare_disposable_database(session)
        state = _require_red_derivation_phase(session, "RED_PREPARED")
        state.phase = "RED_UPGRADING_0006"
        _upgrade_to_revision(owner, _RED_DERIVATION_REVISION)
        state.phase = "RED_UPGRADED_0006"
        snapshot = _validated_reviewed_sql_snapshot(session)
        connection = psycopg.connect(owner.conninfo(), autocommit=True)

        def execute_reviewed_sql() -> psycopg.Cursor:
            action_state = _require_red_derivation_phase(
                session,
                "REVIEWED_READY",
            )
            if (
                connection.closed
                or connection.autocommit is not True
                or connection.info.transaction_status != TransactionStatus.INTRANS
            ):
                raise DisposablePostgresApprovalRejected(
                    _REVIEWED_ACTION_REJECTION
                )
            if (
                session.authority.reviewed_sql is not snapshot
                or hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256
            ):
                raise DisposablePostgresApprovalRejected(
                    "reviewed RED action lacks an exact SQL byte binding"
                )
            action_state.phase = "REVIEWED_EXECUTING"
            try:
                return connection.execute(snapshot.content)
            finally:
                action_state.phase = "REVIEWED_USED"

        state = _require_red_derivation_phase(session, "RED_UPGRADED_0006")
        state.phase = "REVIEWED_READY"
        workflow = _DisposableRedDerivationWorkflow(
            connection,
            execute_reviewed_sql,
        )
        try:
            yield workflow
        finally:
            state.phase = "CLOSED"
            connection.close()


@contextmanager
def disposable_legacy_database(
    *,
    operation_id: str | None = None,
    red_sql_file: Path | None = None,
):
    """Yield an empty isolated database for explicit pre-0005 graph tests."""

    with _disposable_postgres_session(
        operation_id=operation_id,
        red_sql_file=red_sql_file,
        expected_lifecycle_actions=lifecycle_actions_for("MIGRATE"),
    ) as session:
        _provision_base_roles(session)
        yield _owner_settings(_public_cluster(session))


def disposable_role_settings(
    database: DatabaseSettings,
    role: str,
) -> DatabaseSettings:
    """Return credentials for one real LOGIN role in a disposable cluster."""

    if role not in _DISPOSABLE_LOGIN_ROLES:
        raise ValueError("disposable role is not allowed")
    return replace(database, user=role, password=_ROLE_PASSWORDS[role])


def _unused_tcp_port() -> int:
    while True:
        with socket.socket() as listener:
            listener.bind((BIND_HOST, 0))
            port = listener.getsockname()[1]
        if port not in FORBIDDEN_PORTS:
            return port


def _postgres_executable_path(binary: str) -> Path:
    if binary not in POSTGRES_EXECUTABLES:
        raise ValueError("PostgreSQL executable is not allowlisted")
    path = POSTGRES_BIN / binary
    if not path.is_file():
        pytest.skip("PostgreSQL 16 test binaries are unavailable")
    return path


def _expected_session_environment(
    home: Path,
    socket_dir: Path,
    port: int,
) -> tuple[tuple[str, str], ...]:
    values = {
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PGDATABASE": "postgres",
        "PGHOST": str(socket_dir),
        "PGPORT": str(port),
        "PGUSER": "postgres",
        "TZ": "UTC",
    }
    return tuple(sorted(values.items()))


def _validated_session(session: object) -> _DisposablePostgresSession:
    if not isinstance(session, _DisposablePostgresSession):
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL operation requires a private validated disposable session"
        )
    authority = session.authority
    if (
        not isinstance(authority, _ValidatedDisposablePostgresAuthority)
        or authority.token is not _AUTHORITY_TOKEN
    ):
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL operation requires a private validated disposable session"
        )
    context = authority.context
    expected_root = session.data.parent
    expected_environment = _expected_session_environment(
        session.home,
        session.socket_dir,
        context.port,
    )
    if (
        session.root != expected_root
        or session.root.parent != Path("/tmp")
        or not session.root.name.startswith("phase4-postgres-")
        or str(session.data) != context.pgdata
        or session.socket_dir != session.root / "socket"
        or session.home != session.root / "home"
        or session.log != session.root / "postgres.log"
        or session.dump != session.root / "trading-agent.dump"
        or session.environment != expected_environment
        or not isinstance(context.scope, str)
        or context.scope not in SCOPES
        or context.bind_host != BIND_HOST
        or isinstance(context.port, bool)
        or not isinstance(context.port, int)
        or context.port in FORBIDDEN_PORTS
        or not 1 <= context.port <= 65535
        or context.cluster_name != CLUSTER_NAME
        or context.database_name != DISPOSABLE_DATABASE
        or context.runtime_setting_names
        or not isinstance(session.derivation_state, _RedDerivationState)
    ):
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL session does not match validated disposable context"
        )
    return session


def _require_red_derivation_phase(
    session: object,
    phase: str,
) -> _RedDerivationState:
    validated = _validated_session(session)
    if validated.authority.context.scope != "DISPOSABLE_PG_RED":
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL operation is not allowed for disposable approval scope"
        )
    state = validated.derivation_state
    if state.phase != phase:
        raise DisposablePostgresApprovalRejected(_REVIEWED_ACTION_REJECTION)
    return state


def _validated_reviewed_sql_snapshot(
    session: object,
) -> _ReviewedSqlSnapshot:
    validated = _validated_session(session)
    if validated.authority.context.scope != "DISPOSABLE_PG_RED":
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL operation is not allowed for disposable approval scope"
        )
    context = validated.authority.context
    snapshot = validated.authority.reviewed_sql
    if (
        not isinstance(snapshot, _ReviewedSqlSnapshot)
        or not isinstance(context.red_sql_path, str)
        or not isinstance(context.red_sql_sha256, str)
        or type(snapshot.content) is not bytes
        or snapshot.path != context.red_sql_path
        or snapshot.sha256 != context.red_sql_sha256
        or hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256
    ):
        raise DisposablePostgresApprovalRejected(
            "reviewed RED action lacks an exact SQL byte binding"
        )
    return snapshot


def _prepare_disposable_database(
    session: object,
) -> DatabaseSettings:
    validated = _validated_session(session)
    cluster = _public_cluster(validated)
    _provision_base_roles(validated)
    owner = _owner_settings(cluster)
    _upgrade_to_revision(owner, "0004_durable_research_jobs")
    if validated.authority.context.scope == "DISPOSABLE_PG_RED":
        state = _require_red_derivation_phase(validated, "RED_BASE_SETUP")
        state.phase = "RED_UPGRADED_0004"
    _provision_job_roles(validated)
    return owner


def _prepare_empty_restore_target(session: object) -> DatabaseSettings:
    """Recreate the reviewed empty database without granting CREATEDB to its owner."""

    validated = _validated_session(session)
    cluster = _public_cluster(validated)
    owner = _owner_settings(cluster)
    maintenance = replace(owner, database="postgres")
    with psycopg.connect(maintenance.conninfo()) as connection:
        exists = connection.execute(
            "SELECT EXISTS (SELECT FROM pg_database WHERE datname = %s)",
            (DISPOSABLE_DATABASE,),
        ).fetchone()[0]
    if exists:
        raise DisposablePostgresApprovalRejected(
            "restore target database was not dropped before preparation"
        )
    # The custom archive intentionally does not contain cluster-global roles,
    # while pg_restore without --create does not restore database-level ACLs or
    # per-database role settings. Verify that the one-shot role rotation is
    # still exact, then create only the reviewed empty database baseline.
    _assert_restore_global_roles_are_exact(maintenance)
    _run_green_restore_target_psql(validated)
    return owner


def _assert_restore_global_roles_are_exact(maintenance: DatabaseSettings) -> None:
    restricted_login = (False, False, False, False, True, False, False, -1)
    restricted_nologin = (False, False, False, False, False, False, False, -1)
    expected = [
        ("trading_job_api", *restricted_login, "TimeZone=UTC"),
        ("trading_job_scheduler", *restricted_login, "TimeZone=UTC"),
        ("trading_job_worker", *restricted_login, "TimeZone=UTC"),
        ("trading_jobs", *restricted_nologin, ""),
        ("trading_migrator", *restricted_login, ""),
        ("trading_owner", *restricted_login, ""),
        ("trading_reader", *restricted_login, ""),
    ]
    role_names = [row[0] for row in expected]
    with psycopg.connect(maintenance.conninfo()) as connection:
        rows = connection.execute(
            """
            SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb,
                   rolcanlogin, rolreplication, rolbypassrls, rolconnlimit,
                   COALESCE(array_to_string(rolconfig, E'\\n'), '')
            FROM pg_catalog.pg_roles
            WHERE rolname = ANY(%s)
            ORDER BY rolname COLLATE "C"
            """,
            (role_names,),
        ).fetchall()
        memberships = connection.execute(
            """
            SELECT parent.rolname, member.rolname
            FROM pg_catalog.pg_auth_members AS membership
            JOIN pg_catalog.pg_roles AS parent
              ON parent.oid = membership.roleid
            JOIN pg_catalog.pg_roles AS member
              ON member.oid = membership.member
            WHERE parent.rolname = ANY(%s) OR member.rolname = ANY(%s)
            """,
            (role_names, role_names),
        ).fetchall()
    if rows != expected or memberships:
        raise DisposablePostgresApprovalRejected(
            "restore target global role authority is not exact"
        )


def _operation_scopes(operation: object) -> frozenset[str]:
    if not isinstance(operation, str):
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL command does not match validated disposable operation"
        )
    allowed_scopes = _POSTGRES_OPERATION_SCOPES.get(operation)
    if allowed_scopes is None:
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL command does not match validated disposable operation"
        )
    return allowed_scopes


def _require_operation_scope(
    session: object,
    operation: object,
) -> tuple[_DisposablePostgresSession, frozenset[str]]:
    validated = _validated_session(session)
    allowed_scopes = _operation_scopes(operation)
    if validated.authority.context.scope not in allowed_scopes:
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL operation is not allowed for disposable approval scope"
        )
    return validated, allowed_scopes


def _command(
    session: object,
    operation: str,
    binary: str,
    arguments: tuple[str, ...],
    *,
    input_bytes: bytes | None = None,
    reviewed_sql_path: str | None = None,
    reviewed_sql_sha256: str | None = None,
) -> _PostgresCommand:
    validated, allowed_scopes = _require_operation_scope(session, operation)
    return _PostgresCommand(
        operation=operation,
        allowed_scopes=allowed_scopes,
        binary=binary,
        arguments=arguments,
        environment=validated.environment,
        input_bytes=input_bytes,
        reviewed_sql_path=reviewed_sql_path,
        reviewed_sql_sha256=reviewed_sql_sha256,
    )


def _build_initdb_command(session: object) -> _PostgresCommand:
    validated = _validated_session(session)
    return _command(
        validated,
        "initdb",
        "initdb",
        (
            "-D",
            str(validated.data),
            "--username=postgres",
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
            "--encoding=UTF8",
            "--no-instructions",
        ),
    )


def _build_pg_ctl_start_command(session: object) -> _PostgresCommand:
    validated = _validated_session(session)
    context = validated.authority.context
    options = (
        f"-F -p {context.port} -h {BIND_HOST} -k {validated.socket_dir} "
        f"-c cluster_name={CLUSTER_NAME}"
    )
    return _command(
        validated,
        "pg_ctl_start",
        "pg_ctl",
        (
            "-D",
            str(validated.data),
            "-l",
            str(validated.log),
            "-o",
            options,
            "start",
        ),
    )


def _build_pg_ctl_status_command(session: object) -> _PostgresCommand:
    validated = _validated_session(session)
    return _command(
        validated,
        "pg_ctl_status",
        "pg_ctl",
        ("-D", str(validated.data), "status"),
    )


def _build_pg_ctl_stop_command(session: object) -> _PostgresCommand:
    validated = _validated_session(session)
    return _command(
        validated,
        "pg_ctl_stop",
        "pg_ctl",
        ("-D", str(validated.data), "stop", "-m", "immediate"),
    )


def _base_role_input() -> bytes:
    variables = {
        "owner_password": _ROLE_PASSWORDS["trading_owner"],
        "migrator_password": _ROLE_PASSWORDS["trading_migrator"],
        "reader_password": _ROLE_PASSWORDS["trading_reader"],
        "jobs_password": _ROLE_PASSWORDS["trading_jobs"],
    }
    assignments = "".join(
        f"\\set {name} '{value}'\n" for name, value in variables.items()
    )
    return assignments.encode("utf-8") + _disposable_sql(_BASE_ROLE_SQL_BYTES)


def _disposable_sql(source: bytes) -> bytes:
    runtime_identifier = RUNTIME_DATABASE_IDENTIFIER.encode("ascii")
    disposable_identifier = DISPOSABLE_DATABASE.encode("ascii")
    if runtime_identifier not in source or disposable_identifier in source:
        raise RuntimeError("disposable PostgreSQL template identity is invalid")
    expected_replacements = source.count(runtime_identifier)
    rendered = source.replace(runtime_identifier, disposable_identifier)
    if rendered.count(disposable_identifier) != expected_replacements:
        raise RuntimeError("disposable PostgreSQL template rendering is incomplete")
    return rendered


def _job_role_input() -> bytes:
    protected_session = """\
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SET log_min_duration_statement = -1;
SET log_min_duration_sample = -1;
SET log_parameter_max_length_on_error = 0;
SET log_duration = off;
SET debug_print_parse = off;
SET debug_print_rewritten = off;
SET debug_print_plan = off;
SET log_parser_stats = off;
SET log_planner_stats = off;
SET log_executor_stats = off;
SET log_statement_stats = off;
SET track_activities = off;
"""
    script = _disposable_sql(_JOB_ROLE_SQL_BYTES)
    for role in (
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    ):
        marker = f"\\password {role}\n".encode("utf-8")
        if script.count(marker) != 1:
            raise RuntimeError("fixed job role setup bytes are invalid")
        password = _ROLE_PASSWORDS[role].encode("utf-8")
        prompt_input = password + b"\n" + password + b"\n"
        script = script.replace(marker, marker + prompt_input, 1)
    return protected_session.encode("utf-8") + script


def _restore_target_input() -> bytes:
    database = DISPOSABLE_DATABASE
    return f"""\
\\set ON_ERROR_STOP on
CREATE DATABASE {database} OWNER trading_owner;
\\connect {database}
REVOKE CONNECT, TEMPORARY ON DATABASE {database}
  FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,
       trading_job_api, trading_job_worker, trading_job_scheduler;
GRANT CONNECT ON DATABASE {database}
  TO trading_migrator, trading_reader,
     trading_job_api, trading_job_worker, trading_job_scheduler;
GRANT TEMPORARY ON DATABASE {database} TO trading_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO trading_owner;
SET SESSION AUTHORIZATION trading_owner;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
RESET SESSION AUTHORIZATION;
ALTER ROLE trading_owner IN DATABASE {database} SET timezone = 'UTC';
ALTER ROLE trading_migrator IN DATABASE {database} SET timezone = 'UTC';
ALTER ROLE trading_reader IN DATABASE {database}
  SET default_transaction_read_only = on;
ALTER ROLE trading_reader IN DATABASE {database} SET timezone = 'UTC';
ALTER ROLE trading_jobs IN DATABASE {database} RESET ALL;
""".encode("utf-8")


def _build_green_base_psql_command(session: object) -> _PostgresCommand:
    return _command(
        session,
        "green_base_psql",
        "psql",
        ("-X", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--dbname=postgres"),
        input_bytes=_base_role_input(),
    )


def _build_green_job_psql_command(session: object) -> _PostgresCommand:
    return _command(
        session,
        "green_job_psql",
        "psql",
        (
            "-X",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            f"--dbname={DISPOSABLE_DATABASE}",
        ),
        input_bytes=_job_role_input(),
    )


def _build_green_restore_target_psql_command(session: object) -> _PostgresCommand:
    return _command(
        session,
        "green_restore_target_psql",
        "psql",
        ("-X", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--dbname=postgres"),
        input_bytes=_restore_target_input(),
    )


def _build_red_base_psql_command(session: object) -> _PostgresCommand:
    return _command(
        session,
        "red_base_psql",
        "psql",
        ("-X", "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--dbname=postgres"),
        input_bytes=_base_role_input(),
    )


def _build_red_job_psql_command(session: object) -> _PostgresCommand:
    return _command(
        session,
        "red_job_psql",
        "psql",
        (
            "-X",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            f"--dbname={DISPOSABLE_DATABASE}",
        ),
        input_bytes=_job_role_input(),
    )


def _build_pg_dump_command(session: object) -> _PostgresCommand:
    validated = _validated_session(session)
    return _command(
        validated,
        "pg_dump",
        "pg_dump",
        (
            "--format=custom",
            "--create",
            "--file",
            str(validated.dump),
            DISPOSABLE_DATABASE,
        ),
    )


def _build_pg_restore_command(session: object) -> _PostgresCommand:
    validated = _validated_session(session)
    return _command(
        validated,
        "pg_restore",
        "pg_restore",
        (
            "--exit-on-error",
            "--use-set-session-authorization",
            f"--dbname={DISPOSABLE_DATABASE}",
            str(validated.dump),
        ),
    )


def _expected_command(
    session: object,
    operation: object,
) -> _PostgresCommand:
    builders = {
        "initdb": _build_initdb_command,
        "pg_ctl_start": _build_pg_ctl_start_command,
        "pg_ctl_status": _build_pg_ctl_status_command,
        "pg_ctl_stop": _build_pg_ctl_stop_command,
        "green_base_psql": _build_green_base_psql_command,
        "green_job_psql": _build_green_job_psql_command,
        "green_restore_target_psql": _build_green_restore_target_psql_command,
        "red_base_psql": _build_red_base_psql_command,
        "red_job_psql": _build_red_job_psql_command,
        "pg_dump": _build_pg_dump_command,
        "pg_restore": _build_pg_restore_command,
    }
    if not isinstance(operation, str) or operation not in builders:
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL command does not match validated disposable operation"
        )
    return builders[operation](session)


def _execute_postgres_command(
    session: object,
    command: object,
) -> subprocess.CompletedProcess:
    validated = _validated_session(session)
    if not isinstance(command, _PostgresCommand):
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL command does not match validated disposable operation"
        )
    allowed_scopes = _operation_scopes(command.operation)
    if validated.authority.context.scope not in allowed_scopes:
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL operation is not allowed for disposable approval scope"
        )
    if command.allowed_scopes != allowed_scopes:
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL command does not match validated disposable operation"
        )
    expected = _expected_command(validated, command.operation)
    if command != expected:
        raise DisposablePostgresApprovalRejected(
            "PostgreSQL command does not match validated disposable operation"
        )
    required_red_phase = {
        "red_base_psql": "CLUSTER_STARTED",
        "red_job_psql": "RED_UPGRADED_0004",
    }.get(expected.operation)
    if (
        required_red_phase is not None
        and validated.derivation_state.phase != required_red_phase
    ):
        raise DisposablePostgresApprovalRejected(_RED_SETUP_REJECTION)
    executable = _postgres_executable_path(expected.binary)
    kwargs: dict[str, object] = {
        "check": expected.operation not in {"pg_ctl_status", "pg_ctl_stop"},
        "stdout": subprocess.DEVNULL,
        "stderr": (
            subprocess.DEVNULL
            if expected.operation in {"pg_ctl_status", "pg_ctl_stop"}
            else subprocess.PIPE
        ),
        "env": dict(expected.environment),
    }
    if expected.input_bytes is None:
        kwargs["text"] = True
    else:
        kwargs["input"] = expected.input_bytes
    completed = subprocess.run(
        [str(executable), *expected.arguments],
        **kwargs,
    )
    if completed.returncode == 0:
        if expected.operation == "red_base_psql":
            validated.derivation_state.phase = "RED_BASE_SETUP"
        elif expected.operation == "red_job_psql":
            validated.derivation_state.phase = "RED_PREPARED"
    return completed


def _run_initdb(session: object) -> subprocess.CompletedProcess:
    return _execute_postgres_command(session, _build_initdb_command(session))


def _run_pg_ctl_start(session: object) -> subprocess.CompletedProcess:
    return _execute_postgres_command(
        session,
        _build_pg_ctl_start_command(session),
    )


def _run_pg_ctl_status(session: object) -> subprocess.CompletedProcess:
    return _execute_postgres_command(
        session,
        _build_pg_ctl_status_command(session),
    )


def _run_pg_ctl_stop(session: object) -> subprocess.CompletedProcess:
    return _execute_postgres_command(
        session,
        _build_pg_ctl_stop_command(session),
    )


def _run_green_base_psql(session: object) -> subprocess.CompletedProcess:
    try:
        return _execute_postgres_command(
            session,
            _build_green_base_psql_command(session),
        )
    except subprocess.CalledProcessError:
        raise RuntimeError(
            "isolated PostgreSQL provisioning failed: provision-roles.sql"
        ) from None


def _run_green_job_psql(session: object) -> subprocess.CompletedProcess:
    try:
        return _execute_postgres_command(
            session,
            _build_green_job_psql_command(session),
        )
    except subprocess.CalledProcessError:
        raise RuntimeError(
            "isolated PostgreSQL provisioning failed: provision-job-roles.sql"
        ) from None


def _run_green_restore_target_psql(session: object) -> subprocess.CompletedProcess:
    try:
        return _execute_postgres_command(
            session,
            _build_green_restore_target_psql_command(session),
        )
    except subprocess.CalledProcessError:
        raise RuntimeError(
            "isolated PostgreSQL restore target preparation failed"
        ) from None


def _run_red_base_psql(session: object) -> subprocess.CompletedProcess:
    try:
        return _execute_postgres_command(
            session,
            _build_red_base_psql_command(session),
        )
    except subprocess.CalledProcessError:
        raise RuntimeError(
            "isolated PostgreSQL RED setup failed: provision-roles.sql"
        ) from None


def _run_red_job_psql(session: object) -> subprocess.CompletedProcess:
    try:
        return _execute_postgres_command(
            session,
            _build_red_job_psql_command(session),
        )
    except subprocess.CalledProcessError:
        raise RuntimeError(
            "isolated PostgreSQL RED setup failed: provision-job-roles.sql"
        ) from None


def _run_pg_dump(session: object) -> subprocess.CompletedProcess:
    return _execute_postgres_command(session, _build_pg_dump_command(session))


def _run_pg_restore(session: object) -> subprocess.CompletedProcess:
    return _execute_postgres_command(session, _build_pg_restore_command(session))


def _prepare_secure_dump(session: object) -> None:
    validated = _validated_session(session)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(validated.dump, flags, 0o600)
    except OSError:
        raise DisposablePostgresApprovalRejected(
            "disposable restore dump path is not securely precreated"
        ) from None
    os.close(descriptor)
    _validate_secure_dump(validated, allow_empty=True)


def _validate_secure_dump(
    session: object,
    *,
    allow_empty: bool = False,
) -> None:
    validated = _validated_session(session)
    try:
        metadata = validated.dump.lstat()
        resolved = validated.dump.resolve(strict=True)
    except OSError:
        raise DisposablePostgresApprovalRejected(
            "disposable restore dump security invariant failed"
        ) from None
    if (
        resolved != validated.dump
        or validated.dump.parent != validated.root
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or (not allow_empty and metadata.st_size <= 0)
    ):
        raise DisposablePostgresApprovalRejected(
            "disposable restore dump security invariant failed"
        )


def _drop_disposable_database(owner: DatabaseSettings) -> None:
    if (
        owner.database != DISPOSABLE_DATABASE
        or owner.host != BIND_HOST
        or owner.port in FORBIDDEN_PORTS
        or owner.user != "trading_owner"
    ):
        raise DisposablePostgresApprovalRejected(
            "restore target is not the exact disposable database"
        )
    maintenance = replace(owner, database="postgres")
    with psycopg.connect(maintenance.conninfo(), autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE {}").format(sql.Identifier(DISPOSABLE_DATABASE))
        )


def _current_test_path() -> str:
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    test_path = current_test.partition("::")[0]
    if not test_path:
        raise DisposablePostgresApprovalRejected(
            "current pytest test path is unavailable"
        )
    return test_path


def _current_source_identity() -> tuple[str, str]:
    return _source_identity(ROOT)


def _require_current_source_matches_approval(
    record: dict[str, object], source_commit: str, source_tree: str,
) -> None:
    source = record.get("source")
    if not isinstance(source, dict) or source.get("commit") != source_commit:
        raise DisposablePostgresApprovalRejected(
            "source commit does not match",
        )
    if source.get("tree") != source_tree:
        raise DisposablePostgresApprovalRejected(
            "source tree does not match",
        )


def _read_reviewed_sql_snapshot(
    red_sql_file: Path | None,
) -> _ReviewedSqlSnapshot | None:
    if red_sql_file is None:
        return None
    if red_sql_file.is_symlink():
        raise DisposablePostgresApprovalRejected(
            "RED SQL file is not an exact regular SQL source file"
        )
    try:
        resolved = red_sql_file.resolve(strict=True)
        relative = resolved.relative_to(ROOT.resolve(strict=True)).as_posix()
    except (OSError, ValueError):
        raise DisposablePostgresApprovalRejected(
            "RED SQL file is not a source file in this checkout"
        ) from None
    if not resolved.is_file() or resolved.suffix != ".sql":
        raise DisposablePostgresApprovalRejected(
            "RED SQL file is not an exact regular SQL source file"
        )
    try:
        content = resolved.read_bytes()
    except OSError:
        raise DisposablePostgresApprovalRejected(
            "RED SQL file cannot be captured as reviewed bytes"
        ) from None
    return _ReviewedSqlSnapshot(
        path=relative,
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
    )


def _approval_inputs(
    operation_id: str | None,
) -> tuple[dict[str, object], str, str]:
    allow = os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES", "")
    record_path = os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", "")
    scope = os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", "")
    if allow != "YES" or not record_path or scope not in SCOPES:
        pytest.skip("explicit disposable PostgreSQL authority is not present")
    _require_paper_safety_environment()
    approval_path = Path(record_path)
    try:
        resolved_approval_path = approval_path.resolve(strict=True)
    except OSError:
        resolved_approval_path = approval_path
    if resolved_approval_path.is_relative_to(ROOT.resolve(strict=True)):
        raise DisposablePostgresApprovalRejected(
            "approval record must remain outside the source checkout"
        )
    record = load_protected_approval_record(approval_path)
    record_scope = record.get("scope")
    if (
        isinstance(record_scope, str)
        and record_scope in SCOPES
        and record_scope != scope
    ):
        pytest.skip("approval record belongs to other disposable PostgreSQL scope")
    if operation_id is None:
        raise DisposablePostgresApprovalRejected(
            "database-starting test does not expose a stable operation id"
        )
    return record, scope, operation_id


def _require_paper_safety_environment() -> None:
    expected = {
        "TRADING_TEST_REQUESTED_MODE": "paper",
        "TRADING_TEST_EFFECTIVE_MODE": "paper",
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "LIVE_TRADING_ENABLED": "false",
        "TRADING_TEST_KILL_SWITCH": "INACTIVE",
    }
    if any(os.environ.get(name) != value for name, value in expected.items()):
        raise DisposablePostgresApprovalRejected(
            "paper safety baseline is not exact for disposable PostgreSQL"
        )


def _planned_fixture_slot(
    approval_record: dict[str, object],
    operation_id: str,
    expected_lifecycle_actions: tuple[str, ...],
    *, source_commit: str, source_tree: str,
) -> DisposablePostgresFixtureSlot:
    plan_path = os.environ.get("TRADING_TEST_DISPOSABLE_FIXTURE_PLAN", "")
    if not plan_path:
        raise DisposablePostgresApprovalRejected(
            "exact disposable PostgreSQL fixture plan is not present"
        )
    path = Path(plan_path)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        resolved = path
    if resolved.is_relative_to(ROOT.resolve(strict=True)):
        raise DisposablePostgresApprovalRejected(
            "fixture plan must remain outside the source checkout"
        )
    try:
        plan = load_protected_fixture_plan(path)
        slots = validate_disposable_postgres_fixture_plan(
            plan,
            approval_record,
            source_commit=source_commit,
            source_tree=source_tree,
            now=_utc_now(),
        )
    except DisposablePostgresFixturePlanRejected as error:
        raise DisposablePostgresApprovalRejected(str(error)) from None
    test_path = _current_test_path()
    matching = [
        slot
        for slot in slots
        if slot.test_path == test_path and slot.operation_id == operation_id
    ]
    digest = plan["canonical_record_sha256"]
    if not isinstance(digest, str):
        raise DisposablePostgresApprovalRejected(
            "fixture plan canonical digest is unavailable"
        )
    key = (digest, test_path, operation_id)
    ordinal = _PLANNED_SLOT_COUNTERS.get(key, 0) + 1
    if ordinal > len(matching):
        raise DisposablePostgresApprovalRejected(
            "fixture plan has no unused exact slot for this operation"
        )
    slot = matching[ordinal - 1]
    if slot.ordinal != ordinal:
        raise DisposablePostgresApprovalRejected(
            "fixture plan slot order is invalid"
        )
    if slot.lifecycle_actions != expected_lifecycle_actions:
        raise DisposablePostgresApprovalRejected(
            "fixture plan lifecycle does not match the runtime operation"
        )
    _PLANNED_SLOT_COUNTERS[key] = ordinal
    return slot


@contextmanager
def _fixture_root(
    approval_record: dict[str, object],
    operation_id: str,
    *,
    source_commit: str,
    source_tree: str,
    planned: bool,
    expected_lifecycle_actions: tuple[str, ...] | None,
):
    if not planned:
        with tempfile.TemporaryDirectory(
            prefix="phase4-postgres-",
            dir="/tmp",
        ) as temporary:
            root = Path(temporary)
            yield root, root / "data", _unused_tcp_port()
        return

    if expected_lifecycle_actions is None:
        raise DisposablePostgresApprovalRejected(
            "planned PostgreSQL session lacks an exact lifecycle binding"
        )
    slot = _planned_fixture_slot(
        approval_record,
        operation_id,
        expected_lifecycle_actions,
        source_commit=source_commit,
        source_tree=source_tree,
    )
    root = Path(slot.root)
    data = Path(slot.pgdata)
    if root.exists() or _loopback_listener_present(slot.port):
        raise DisposablePostgresApprovalRejected(
            "planned disposable PostgreSQL slot is already in use"
        )
    try:
        root.mkdir(mode=0o700)
    except OSError:
        raise DisposablePostgresApprovalRejected(
            "planned disposable PostgreSQL root cannot be created"
        ) from None
    yield root, data, slot.port


def _delete_planned_fixture_root(root: Path) -> None:
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise DisposablePostgresCleanupError(
            "planned disposable PostgreSQL cleanup could not be verified"
        ) from None
    if (
        root.parent != Path("/tmp")
        or not root.name.startswith("phase4-postgres-")
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
    ):
        raise DisposablePostgresCleanupError(
            "planned disposable PostgreSQL cleanup could not be verified"
        )
    shutil.rmtree(root)
    if root.exists():
        raise DisposablePostgresCleanupError(
            "planned disposable PostgreSQL cleanup could not be verified"
        )


def _authorize_disposable_postgres(
    record: dict[str, object],
    scope: str,
    operation_id: str,
    *,
    source_commit: str,
    source_tree: str,
    data: Path,
    port: int,
    red_sql_file: Path | None,
) -> _ValidatedDisposablePostgresAuthority:
    reviewed_sql = _read_reviewed_sql_snapshot(red_sql_file)
    red_sql_path = reviewed_sql.path if reviewed_sql is not None else None
    red_sql_sha256 = reviewed_sql.sha256 if reviewed_sql is not None else None
    context = DisposablePostgresApprovalContext(
        scope=scope,
        source_commit=source_commit,
        source_tree=source_tree,
        test_path=_current_test_path(),
        operation_id=operation_id,
        pgdata=str(data),
        bind_host=BIND_HOST,
        port=port,
        cluster_name=CLUSTER_NAME,
        database_name=DISPOSABLE_DATABASE,
        runtime_setting_names=_runtime_setting_names(),
        now=_utc_now(),
        red_sql_path=red_sql_path,
        red_sql_sha256=red_sql_sha256,
    )
    validate_disposable_postgres_approval(record, context)
    validate_source_binding_files(record, ROOT)
    return _ValidatedDisposablePostgresAuthority(
        token=_AUTHORITY_TOKEN,
        context=context,
        record_digest=canonical_record_sha256(record),
        reviewed_sql=reviewed_sql,
    )


def _public_cluster(session: object) -> dict[str, object]:
    validated = _validated_session(session)
    context = validated.authority.context
    return {
        "host": BIND_HOST,
        "port": context.port,
        "socket_dir": str(validated.socket_dir),
        "environment": dict(validated.environment),
        "operation_id": context.operation_id,
    }


def _loopback_listener_present(port: int) -> bool:
    if isinstance(port, bool) or not isinstance(port, int) or port in FORBIDDEN_PORTS:
        raise DisposablePostgresCleanupError(
            "disposable PostgreSQL cleanup could not be verified"
        )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.1)
        return probe.connect_ex((BIND_HOST, port)) == 0


def _ensure_disposable_postgres_stopped(session: object) -> None:
    validated = _validated_session(session)
    for attempt in range(2):
        try:
            stop_result = _run_pg_ctl_stop(validated)
            stop_returncode = stop_result.returncode
        except (OSError, subprocess.SubprocessError):
            stop_returncode = None
        try:
            status_result = _run_pg_ctl_status(validated)
            process_survives = status_result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            process_survives = True
        try:
            listener_survives = _loopback_listener_present(
                validated.authority.context.port
            )
        except OSError:
            listener_survives = True
        if (
            isinstance(stop_returncode, int)
            and not process_survives
            and not listener_survives
        ):
            if stop_returncode == 0 or attempt == 1:
                return
    raise DisposablePostgresCleanupError(
        "disposable PostgreSQL cleanup could not be verified"
    )


@contextmanager
def _disposable_postgres_session(
    *,
    operation_id: str | None = None,
    red_sql_file: Path | None = None,
    planned: bool = False,
    expected_lifecycle_actions: tuple[str, ...] | None = None,
):
    """Yield a private capability for one validated disposable cluster."""

    record, scope, stable_operation_id = _approval_inputs(operation_id)
    source_commit, source_tree = _current_source_identity()
    _require_current_source_matches_approval(
        record, source_commit, source_tree,
    )
    with _fixture_root(
        record,
        stable_operation_id,
        source_commit=source_commit,
        source_tree=source_tree,
        planned=planned,
        expected_lifecycle_actions=expected_lifecycle_actions,
    ) as (root, data, port):
        session: _DisposablePostgresSession | None = None
        initialized = False
        try:
            socket_dir = root / "socket"
            home = root / "home"
            socket_dir.mkdir()
            home.mkdir()
            authority = _authorize_disposable_postgres(
                record,
                scope,
                stable_operation_id,
                source_commit=source_commit,
                source_tree=source_tree,
                data=data,
                port=port,
                red_sql_file=red_sql_file,
            )
            for binary in ("initdb", "pg_ctl", "psql"):
                _postgres_executable_path(binary)
            session = _DisposablePostgresSession(
                authority=authority,
                root=root,
                data=data,
                socket_dir=socket_dir,
                home=home,
                log=root / "postgres.log",
                dump=root / "trading-agent.dump",
                environment=_expected_session_environment(home, socket_dir, port),
                derivation_state=_RedDerivationState(),
            )
            _run_initdb(session)
            initialized = True
            _run_pg_ctl_start(session)
            session.derivation_state.phase = "CLUSTER_STARTED"
            yield session
        finally:
            if session is not None:
                session.derivation_state.phase = "CLOSED"
                if initialized:
                    _ensure_disposable_postgres_stopped(session)
            if planned:
                _delete_planned_fixture_root(root)


@contextmanager
def disposable_postgres_cluster(
    *,
    operation_id: str | None = None,
    red_sql_file: Path | None = None,
):
    """Yield isolated connection metadata without an executable capability."""

    with _disposable_postgres_session(
        operation_id=operation_id,
        red_sql_file=red_sql_file,
    ) as session:
        yield _public_cluster(session)
