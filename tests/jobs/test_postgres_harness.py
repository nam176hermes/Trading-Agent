from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import psycopg
from psycopg.pq import TransactionStatus
import pytest

from scripts.validate_disposable_postgres_approval import (
    DisposablePostgresApprovalRejected,
)
from scripts.validate_disposable_postgres_fixture_plan import (
    canonical_record_sha256 as fixture_plan_sha256,
    lifecycle_actions_for,
)
from tests.jobs import _postgres
from tests.jobs._postgres import (
    DISPOSABLE_DATABASE,
    ROOT,
    disposable_database,
    disposable_postgres_cluster,
    disposable_restore_workflow,
    disposable_role_settings,
)
from tests.jobs.test_disposable_postgres_approval import (
    COMMIT,
    NOW,
    OPERATION_ID,
    TEST_PATH,
    TREE,
    build_record,
    refresh_digest,
    write_record,
)
from tests.jobs.test_disposable_postgres_fixture_plan import _plan as fixture_plan
from trading_control.db import DatabaseSettings


HARNESS_ENV_OPERATION = "postgres-harness-isolated-environment-v1"
HARNESS_DATABASE_OPERATION = "postgres-harness-base-provisioning-v1"

GREEN_SCOPE = "DISPOSABLE_PG_GREEN"
RED_SCOPE = "DISPOSABLE_PG_RED"
REVIEWED_ACTION_REJECTION = (
    "^reviewed RED action is unavailable for this derivation state$"
)
RED_SETUP_REJECTION = (
    "^RED setup operation is unavailable for this preparation state$"
)
EXPECTED_OPERATION_BUILDERS = {
    "initdb": "_build_initdb_command",
    "pg_ctl_start": "_build_pg_ctl_start_command",
    "pg_ctl_status": "_build_pg_ctl_status_command",
    "pg_ctl_stop": "_build_pg_ctl_stop_command",
    "green_base_psql": "_build_green_base_psql_command",
    "green_job_psql": "_build_green_job_psql_command",
    "green_restore_target_psql": "_build_green_restore_target_psql_command",
    "red_base_psql": "_build_red_base_psql_command",
    "red_job_psql": "_build_red_job_psql_command",
    "pg_dump": "_build_pg_dump_command",
    "pg_restore": "_build_pg_restore_command",
}
EXPECTED_OPERATION_SCOPES = {
    "initdb": frozenset({GREEN_SCOPE, RED_SCOPE}),
    "pg_ctl_start": frozenset({GREEN_SCOPE, RED_SCOPE}),
    "pg_ctl_status": frozenset({GREEN_SCOPE, RED_SCOPE}),
    "pg_ctl_stop": frozenset({GREEN_SCOPE, RED_SCOPE}),
    "green_base_psql": frozenset({GREEN_SCOPE}),
    "green_job_psql": frozenset({GREEN_SCOPE}),
    "green_restore_target_psql": frozenset({GREEN_SCOPE}),
    "red_base_psql": frozenset({RED_SCOPE}),
    "red_job_psql": frozenset({RED_SCOPE}),
    "pg_dump": frozenset({GREEN_SCOPE}),
    "pg_restore": frozenset({GREEN_SCOPE}),
}


@pytest.fixture
def protected_record_dir():
    with tempfile.TemporaryDirectory(
        prefix="disposable-postgres-harness-",
        dir="/tmp",
    ) as raw:
        yield Path(raw)


def test_verification_sources_have_no_protected_runtime_configuration() -> None:
    harness_source = (ROOT / "tests/jobs/_postgres.py").read_text(
        encoding="utf-8"
    )
    schema_test_source = (
        ROOT / "tests/control_api/test_alembic_schema.py"
    ).read_text(encoding="utf-8")
    verification_source = harness_source + schema_test_source

    assert "Path.home" not in verification_source
    assert ".config/trading-agent" not in verification_source
    assert "postgres-admin.env" not in verification_source
    assert "postgres-migrator.env" not in verification_source
    assert "postgres-reader.env" not in verification_source


def test_temp_cluster_exports_only_an_isolated_allowlisted_environment() -> None:
    with disposable_postgres_cluster(operation_id=HARNESS_ENV_OPERATION) as cluster:
        environment = cluster["environment"]

        assert set(environment) == {
            "HOME",
            "LANG",
            "LC_ALL",
            "PGDATABASE",
            "PGHOST",
            "PGPORT",
            "PGUSER",
            "TZ",
        }
        assert Path(environment["HOME"]).is_relative_to(Path("/tmp"))
        assert Path(environment["PGHOST"]).is_relative_to(Path("/tmp"))
        assert environment["PGUSER"] == "postgres"
        assert environment["PGDATABASE"] == "postgres"


def test_disposable_database_provisions_exact_0004_role_preconditions() -> None:
    with disposable_database(operation_id=HARNESS_DATABASE_OPERATION) as owner:
        assert owner.user == "trading_owner"
        for role in (
            "trading_owner",
            "trading_migrator",
            "trading_reader",
            "trading_job_api",
            "trading_job_worker",
            "trading_job_scheduler",
        ):
            settings = disposable_role_settings(owner, role)
            assert settings.user == role
            assert settings.host == owner.host
            assert settings.port == owner.port
            assert settings.database == owner.database

        with psycopg.connect(owner.conninfo()) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "0004_durable_research_jobs"
            assert connection.execute(
                """
                SELECT count(*)
                FROM pg_roles
                WHERE rolname IN (
                  'trading_job_api',
                  'trading_job_worker',
                  'trading_job_scheduler'
                )
                  AND rolcanlogin
                  AND NOT rolsuper
                  AND NOT rolcreatedb
                  AND NOT rolcreaterole
                  AND NOT rolinherit
                  AND NOT rolreplication
                  AND NOT rolbypassrls
                """
            ).fetchone()[0] == 3


@pytest.mark.parametrize("role", ("trading_jobs", "postgres", "", "unknown"))
def test_disposable_role_settings_rejects_non_service_logins(role: str) -> None:
    fixed_value = "fixed-test-only-owner-password"
    owner = DatabaseSettings(
        host="127.0.0.1",
        port=5432,
        database="disposable_test_only",
        user="trading_owner",
        password=fixed_value,
    )

    with pytest.raises(ValueError, match="disposable role is not allowed"):
        disposable_role_settings(owner, role)


def _set_controls(
    monkeypatch: pytest.MonkeyPatch,
    record: Path,
    *,
    scope: str = "DISPOSABLE_PG_GREEN",
) -> None:
    monkeypatch.setenv("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES", "YES")
    monkeypatch.setenv("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", str(record))
    monkeypatch.setenv("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", scope)
    monkeypatch.setenv("TRADING_TEST_REQUESTED_MODE", "paper")
    monkeypatch.setenv("TRADING_TEST_EFFECTIVE_MODE", "paper")
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LIVE_TRADING_APPROVED", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("TRADING_TEST_KILL_SWITCH", "INACTIVE")
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        f"{TEST_PATH}::test_mocked_cluster_lifecycle (call)",
    )


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("TRADING_TEST_REQUESTED_MODE", "live"),
        ("TRADING_TEST_EFFECTIVE_MODE", "unknown"),
        ("LIVE_EXECUTION_ENABLED", "true"),
        ("LIVE_TRADING_APPROVED", "true"),
        ("LIVE_TRADING_ENABLED", "true"),
        ("TRADING_TEST_KILL_SWITCH", "ACTIVE"),
    ),
)
def test_approval_cannot_bypass_exact_paper_safety_baseline(
    name: str,
    value: str,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = write_record(
        protected_record_dir / "approval.json",
        build_record(),
    )
    _set_controls(monkeypatch, record)
    monkeypatch.setenv(name, value)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        _postgres.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )
    with pytest.raises(
        DisposablePostgresApprovalRejected,
        match="paper safety baseline is not exact",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pass
    assert calls == []


def _red_record(sql_file: Path) -> dict[str, object]:
    document = build_record(scope=RED_SCOPE)
    document["red_sql_binding"] = {
        "operation_id": OPERATION_ID,
        "sql_path": sql_file.relative_to(ROOT).as_posix(),
        "sql_sha256": hashlib.sha256(sql_file.read_bytes()).hexdigest(),
    }
    refresh_digest(document)
    return document


def _install_mocked_postgres_boundary(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[list[str], dict[str, object]]],
    sessions: list[object],
    *,
    on_initdb=None,
) -> None:
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda binary: Path("/mock/postgresql") / binary,
    )

    def fake_run(command, **kwargs):
        rendered = [str(part) for part in command]
        calls.append((rendered, kwargs))
        if Path(rendered[0]).name == "initdb" and on_initdb is not None:
            on_initdb()
        returncode = 3 if rendered[-1] == "status" else 0
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(_postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(_postgres, "_loopback_listener_present", lambda _port: False)
    original_run_initdb = _postgres._run_initdb

    def capture_session(session):
        sessions.append(session)
        return original_run_initdb(session)

    monkeypatch.setattr(_postgres, "_run_initdb", capture_session)


class _FakeRedConnection:
    def __init__(self, events: list[str] | None = None) -> None:
        self.autocommit = True
        self.closed = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self._num_transactions = 0
        self.queries: list[bytes] = []
        self._events = events

    @contextmanager
    def transaction(self, *, force_rollback: bool = False):
        assert force_rollback is True
        assert self._num_transactions == 0
        self._num_transactions = 1
        self.info.transaction_status = TransactionStatus.INTRANS
        if self._events is not None:
            self._events.append("transaction_begin")
        try:
            yield object()
        finally:
            if self._events is not None:
                self._events.append("rollback")
            self.info.transaction_status = TransactionStatus.IDLE
            self._num_transactions = 0

    def execute(self, query: bytes):
        self.queries.append(query)
        if self._events is not None:
            self._events.append("reviewed_sql")
        return object()

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "missing_control",
    (
        "TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES",
        "TRADING_TEST_DISPOSABLE_APPROVAL_RECORD",
        "TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE",
    ),
)
def test_missing_control_skips_before_executable_discovery_or_call(
    missing_control: str,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    monkeypatch.delenv(missing_control)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda _binary: pytest.fail("PostgreSQL executable discovery was reached"),
        raising=False,
    )
    monkeypatch.setattr(
        _postgres.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an executable call was reached"),
    )

    with pytest.raises(
        pytest.skip.Exception,
        match="explicit disposable PostgreSQL authority",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pytest.fail("unapproved cluster was yielded")


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("missing_field", "top-level approval fields are missing or unknown"),
        ("expired", "approval record is not currently valid"),
        ("source_drift", "source commit does not match"),
    ),
)
def test_invalid_record_rejects_before_executable_discovery_or_call(
    case: str,
    reason: str,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = build_record()
    if case == "missing_field":
        document.pop("review")
    elif case == "expired":
        document["validity"]["expires_at_utc"] = "2026-07-16T18:00:00Z"
    elif case == "source_drift":
        document["source"]["commit"] = "d" * 40
    refresh_digest(document)
    record = write_record(protected_record_dir / "approval.json", document)
    _set_controls(monkeypatch, record)
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda _binary: pytest.fail("PostgreSQL executable discovery was reached"),
    )
    monkeypatch.setattr(
        _postgres.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an executable call was reached"),
    )

    with pytest.raises(DisposablePostgresApprovalRejected, match=f"^{reason}$"):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pytest.fail("invalid-record cluster was yielded")


@pytest.mark.parametrize("malformed_scope", ([], {}, True, 1))
def test_malformed_record_scope_has_pure_cli_harness_rejection_parity(
    malformed_scope: object,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = build_record()
    document["scope"] = malformed_scope
    refresh_digest(document)
    record = write_record(protected_record_dir / "approval.json", document)
    _set_controls(monkeypatch, record, scope=GREEN_SCOPE)
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda _binary: pytest.fail("PostgreSQL executable discovery was reached"),
    )
    monkeypatch.setattr(
        _postgres.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an executable call was reached"),
    )

    with pytest.raises(
        DisposablePostgresApprovalRejected,
        match="^record scope is invalid$",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pytest.fail("malformed-scope cluster was yielded")


def test_other_scope_skips_before_executable_discovery_or_call(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_document = build_record(scope="DISPOSABLE_PG_RED")
    record = write_record(protected_record_dir / "approval.json", record_document)
    _set_controls(monkeypatch, record, scope="DISPOSABLE_PG_GREEN")
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda _binary: pytest.fail("PostgreSQL executable discovery was reached"),
        raising=False,
    )
    monkeypatch.setattr(
        _postgres.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("an executable call was reached"),
    )

    with pytest.raises(
        pytest.skip.Exception,
        match="other disposable PostgreSQL scope",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pytest.fail("wrong-scope cluster was yielded")


def test_approval_record_inside_source_tree_rejects_before_executable_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(
        prefix=".mock-disposable-approval-",
        dir=ROOT,
    ) as raw:
        record = write_record(Path(raw) / "approval.json", build_record())
        _set_controls(monkeypatch, record)
        monkeypatch.setattr(
            _postgres,
            "_current_source_identity",
            lambda: pytest.fail("source identity discovery was reached"),
        )
        monkeypatch.setattr(
            _postgres,
            "_postgres_executable_path",
            lambda _binary: pytest.fail("PostgreSQL executable discovery was reached"),
        )

        with pytest.raises(
            DisposablePostgresApprovalRejected,
            match="^approval record must remain outside the source checkout$",
        ):
            with disposable_postgres_cluster(operation_id=OPERATION_ID):
                pytest.fail("source-tree approval record yielded a cluster")


def test_runtime_database_setting_rejects_before_executable_discovery(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    monkeypatch.setenv("PGDATA", "present")
    monkeypatch.setattr(
        _postgres,
        "_current_source_identity",
        lambda: (COMMIT, TREE),
        raising=False,
    )
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda _binary: pytest.fail("PostgreSQL executable discovery was reached"),
        raising=False,
    )

    with pytest.raises(
        DisposablePostgresApprovalRejected,
        match="^runtime database settings are present$",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pytest.fail("cluster with runtime settings was yielded")


def test_raw_postgres_executor_and_yielded_authority_are_not_exposed() -> None:
    assert not hasattr(_postgres, "_run_postgres_executable")


def _require_operation_specific_command_api() -> None:
    required = (
        "_build_initdb_command",
        "_build_pg_ctl_start_command",
        "_build_pg_ctl_status_command",
        "_build_pg_ctl_stop_command",
        "_build_green_base_psql_command",
        "_build_green_job_psql_command",
        "_build_red_base_psql_command",
        "_build_red_job_psql_command",
        "_build_pg_dump_command",
        "_build_pg_restore_command",
        "_execute_postgres_command",
        "_run_initdb",
        "_run_pg_ctl_start",
        "_run_pg_ctl_status",
        "_run_pg_ctl_stop",
        "_run_green_base_psql",
        "_run_green_job_psql",
        "_run_red_base_psql",
        "_run_red_job_psql",
        "_run_pg_dump",
        "_run_pg_restore",
        "_loopback_listener_present",
        "DisposablePostgresCleanupError",
    )
    missing = [name for name in required if not hasattr(_postgres, name)]
    assert missing == []


def test_operation_scope_matrix_is_explicit_and_complete() -> None:
    assert dict(_postgres._POSTGRES_OPERATION_SCOPES) == EXPECTED_OPERATION_SCOPES
    assert set(EXPECTED_OPERATION_BUILDERS) == set(EXPECTED_OPERATION_SCOPES)
    for builder_name in EXPECTED_OPERATION_BUILDERS.values():
        assert hasattr(_postgres, builder_name)


def test_fixed_job_setup_bytes_keep_each_password_prompt_input_adjacent() -> None:
    setup_bytes = _postgres._job_role_input()
    assert b"\\i " not in setup_bytes
    for role in (
        "trading_job_api",
        "trading_job_worker",
        "trading_job_scheduler",
    ):
        password = _postgres._ROLE_PASSWORDS[role].encode("utf-8")
        expected = b"\\password " + role.encode("utf-8") + b"\n"
        expected += password + b"\n" + password + b"\n"
        assert setup_bytes.count(expected) == 1


@pytest.mark.parametrize("scope", (GREEN_SCOPE, RED_SCOPE))
def test_scope_matrix_allows_exact_family_and_rejects_every_cross_scope_call(
    scope: str,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_file = ROOT / "ops/postgres/provision-roles.sql"
    document = build_record() if scope == GREEN_SCOPE else _red_record(sql_file)
    record = write_record(protected_record_dir / "approval.json", document)
    _set_controls(monkeypatch, record, scope=scope)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)

    red_sql_file = sql_file if scope == RED_SCOPE else None
    with disposable_postgres_cluster(
        operation_id=OPERATION_ID,
        red_sql_file=red_sql_file,
    ):
        session = sessions[0]
        for operation, builder_name in EXPECTED_OPERATION_BUILDERS.items():
            builder = getattr(_postgres, builder_name)
            allowed_scopes = EXPECTED_OPERATION_SCOPES[operation]
            if scope in allowed_scopes:
                command = builder(session)
                assert command.operation == operation
                assert command.allowed_scopes == allowed_scopes
                if operation == "red_job_psql":
                    session.derivation_state.phase = "RED_UPGRADED_0004"
                _postgres._execute_postgres_command(session, command)
                continue

            before = len(calls)
            with pytest.raises(
                DisposablePostgresApprovalRejected,
                match=(
                    "^PostgreSQL operation is not allowed for disposable "
                    "approval scope$"
                ),
            ):
                builder(session)
            forged = _postgres._PostgresCommand(
                operation=operation,
                allowed_scopes=allowed_scopes,
                binary="psql",
                arguments=(),
                environment=session.environment,
                input_bytes=None,
                reviewed_sql_path=None,
                reviewed_sql_sha256=None,
            )
            with pytest.raises(
                DisposablePostgresApprovalRejected,
                match=(
                    "^PostgreSQL operation is not allowed for disposable "
                    "approval scope$"
                ),
            ):
                _postgres._execute_postgres_command(session, forged)
            assert len(calls) == before


def test_red_database_context_routes_through_red_setup_only(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = write_record(
        protected_record_dir / "approval.json",
        build_record(scope=RED_SCOPE),
    )
    _set_controls(monkeypatch, record, scope=RED_SCOPE)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)
    routes: list[str] = []
    monkeypatch.setattr(
        _postgres,
        "_run_green_base_psql",
        lambda _session: routes.append("green_base"),
    )
    monkeypatch.setattr(
        _postgres,
        "_run_green_job_psql",
        lambda _session: routes.append("green_job"),
    )
    monkeypatch.setattr(
        _postgres,
        "_run_red_base_psql",
        lambda _session: routes.append("red_base"),
        raising=False,
    )
    monkeypatch.setattr(
        _postgres,
        "_run_red_job_psql",
        lambda _session: routes.append("red_job"),
        raising=False,
    )
    monkeypatch.setattr(_postgres, "_upgrade_to_revision", lambda *_args: None)

    with disposable_database(
        operation_id=OPERATION_ID,
    ):
        pass

    assert routes == ["red_base", "red_job"]


def test_low_level_red_session_cannot_execute_reviewed_sql_before_setup(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_file = ROOT / "ops/postgres/provision-roles.sql"
    record = write_record(
        protected_record_dir / "approval.json",
        _red_record(sql_file),
    )
    _set_controls(monkeypatch, record, scope=RED_SCOPE)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)

    assert not hasattr(_postgres, "_build_red_psql_command")
    assert not hasattr(_postgres, "_run_red_psql")

    with _postgres._disposable_postgres_session(
        operation_id=OPERATION_ID,
        red_sql_file=sql_file,
    ) as session:
        wrong_order = _postgres._build_red_job_psql_command(session)
        before = len(calls)
        with pytest.raises(
            DisposablePostgresApprovalRejected,
            match=RED_SETUP_REJECTION,
        ):
            _postgres._execute_postgres_command(session, wrong_order)
        assert len(calls) == before

    assert [
        Path(command[0]).name
        for command, _kwargs in calls
        if Path(command[0]).name == "psql"
    ] == []


def test_red_derivation_workflow_has_exact_task5_order_and_closed_surface(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_file = ROOT / "ops/postgres/provision-roles.sql"
    record = write_record(
        protected_record_dir / "approval.json",
        _red_record(sql_file),
    )
    _set_controls(monkeypatch, record, scope=RED_SCOPE)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)
    events: list[str] = []
    original_red_base = _postgres._run_red_base_psql
    original_red_job = _postgres._run_red_job_psql
    connection = _FakeRedConnection(events)

    def red_base(session):
        events.append("red_base_setup")
        return original_red_base(session)

    def upgrade(_settings, revision):
        if revision == "0004_durable_research_jobs":
            events.append("upgrade_0004")
        elif revision == "0006_job_transition_database_authority":
            events.append("upgrade_0006")
        else:
            raise AssertionError(f"unexpected revision: {revision}")

    def red_job(session):
        events.append("red_job_setup")
        return original_red_job(session)

    def connect(conninfo, *, autocommit):
        assert f"dbname={DISPOSABLE_DATABASE}" in conninfo
        assert autocommit is True
        events.append("connect")
        return connection

    monkeypatch.setattr(_postgres, "_run_red_base_psql", red_base)
    monkeypatch.setattr(_postgres, "_upgrade_to_revision", upgrade)
    monkeypatch.setattr(_postgres, "_run_red_job_psql", red_job)
    monkeypatch.setattr(_postgres.psycopg, "connect", connect)

    with _postgres.disposable_red_derivation_database(
        operation_id=OPERATION_ID,
        red_sql_file=sql_file,
    ) as workflow:
        assert workflow.database is connection
        assert {
            name for name in dir(workflow) if not name.startswith("_")
        } == {"database", "execute_reviewed_sql"}
        assert inspect.signature(workflow.execute_reviewed_sql).parameters == {}
        events.append("capture_pre_catalog")
        with workflow.database.transaction(force_rollback=True):
            workflow.execute_reviewed_sql()
            events.append("capture_post_catalog")
        events.append("prove_unchanged")

    assert events == [
        "red_base_setup",
        "upgrade_0004",
        "red_job_setup",
        "upgrade_0006",
        "connect",
        "capture_pre_catalog",
        "transaction_begin",
        "reviewed_sql",
        "capture_post_catalog",
        "rollback",
        "prove_unchanged",
    ]
    assert connection.queries == [sql_file.read_bytes()]
    assert connection.queries[0] is sessions[0].authority.reviewed_sql.content
    assert connection.closed is True
    assert all(kwargs.get("input") != sql_file.read_bytes() for _, kwargs in calls)


def test_reviewed_action_is_zero_argument_one_shot_and_workflow_scoped(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_file = ROOT / "ops/postgres/provision-roles.sql"
    record = write_record(
        protected_record_dir / "approval.json",
        _red_record(sql_file),
    )
    _set_controls(monkeypatch, record, scope=RED_SCOPE)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)
    monkeypatch.setattr(_postgres, "_upgrade_to_revision", lambda *_args: None)
    connection = _FakeRedConnection()
    monkeypatch.setattr(
        _postgres.psycopg,
        "connect",
        lambda _conninfo, *, autocommit: connection,
    )

    with _postgres.disposable_red_derivation_database(
        operation_id=OPERATION_ID,
        red_sql_file=sql_file,
    ) as workflow:
        action = workflow.execute_reviewed_sql
        assert workflow.database is connection
        before = len(calls)
        with pytest.raises(TypeError):
            action(b"alternate SQL")
        assert len(calls) == before
        with pytest.raises(
            DisposablePostgresApprovalRejected,
            match=REVIEWED_ACTION_REJECTION,
        ):
            action()
        assert connection.queries == []
        connection.autocommit = False
        connection.info.transaction_status = TransactionStatus.INTRANS
        with pytest.raises(
            DisposablePostgresApprovalRejected,
            match=REVIEWED_ACTION_REJECTION,
        ):
            action()
        connection.autocommit = True
        connection.info.transaction_status = TransactionStatus.IDLE
        other_connection = _FakeRedConnection()
        with other_connection.transaction(force_rollback=True):
            with pytest.raises(
                DisposablePostgresApprovalRejected,
                match=REVIEWED_ACTION_REJECTION,
            ):
                action()
        assert connection.queries == []
        with connection.transaction(force_rollback=True):
            action()
        after_first = len(calls)
        with pytest.raises(
            DisposablePostgresApprovalRejected,
            match=REVIEWED_ACTION_REJECTION,
        ):
            action()
        assert len(calls) == after_first
        assert connection.queries == [sql_file.read_bytes()]

    with pytest.raises(
        DisposablePostgresApprovalRejected,
        match=REVIEWED_ACTION_REJECTION,
    ):
        action()
    assert len(calls) == after_first + 2
    assert connection.closed is True


def test_green_cannot_create_red_derivation_action_before_subprocess(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_file = ROOT / "ops/postgres/provision-roles.sql"
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record, scope=GREEN_SCOPE)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)

    with pytest.raises(DisposablePostgresApprovalRejected):
        with _postgres.disposable_red_derivation_database(
            operation_id=OPERATION_ID,
            red_sql_file=sql_file,
        ):
            pytest.fail("GREEN yielded a RED derivation workflow")
    assert calls == []


def test_mocked_valid_record_binds_safe_cluster_and_always_stops(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_operation_specific_command_api()
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    preflight_events: list[str] = []

    def source_identity() -> tuple[str, str]:
        preflight_events.append("SOURCE_IDENTITY")
        return COMMIT, TREE

    monkeypatch.setattr(
        _postgres,
        "_current_source_identity",
        source_identity,
        raising=False,
    )
    original_temporary_directory = _postgres.tempfile.TemporaryDirectory

    def guarded_temporary_directory(*args, **kwargs):
        assert preflight_events == ["SOURCE_IDENTITY"]
        preflight_events.append("FIXTURE_ROOT")
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        _postgres.tempfile,
        "TemporaryDirectory",
        guarded_temporary_directory,
    )
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda binary: Path("/mock/postgresql") / binary,
        raising=False,
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append(([str(part) for part in command], kwargs))
        returncode = 3 if command[-1] == "status" else 0
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(_postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(_postgres, "_loopback_listener_present", lambda _port: False)
    sessions: list[object] = []
    original_run_initdb = _postgres._run_initdb

    def capture_session(session):
        sessions.append(session)
        return original_run_initdb(session)

    monkeypatch.setattr(_postgres, "_run_initdb", capture_session)

    class BodyFailure(RuntimeError):
        pass

    with pytest.raises(BodyFailure):
        with disposable_postgres_cluster(operation_id=OPERATION_ID) as cluster:
            assert cluster["host"] == "127.0.0.1"
            assert cluster["port"] not in {3002, 8401, 55432}
            assert "_authority" not in cluster
            assert len(sessions) == 1
            session = sessions[0]
            _postgres._run_green_base_psql(session)
            _postgres._run_green_job_psql(session)
            _postgres._run_pg_dump(session)
            _postgres._run_pg_restore(session)
            raise BodyFailure("prove finally cleanup")

    executable_names = [Path(command[0]).name for command, _kwargs in calls]
    assert executable_names == [
        "initdb",
        "pg_ctl",
        "psql",
        "psql",
        "pg_dump",
        "pg_restore",
        "pg_ctl",
        "pg_ctl",
    ]
    initdb_command = calls[0][0]
    data = Path(initdb_command[initdb_command.index("-D") + 1])
    assert data.is_relative_to(Path("/tmp"))
    assert data.parent.name.startswith("phase4-postgres-")

    start_command = calls[1][0]
    options = start_command[start_command.index("-o") + 1]
    assert "-h 127.0.0.1" in options
    assert "cluster_name=trading-agent-disposable-tests" in options
    assert start_command[-1] == "start"

    stop_command = calls[6][0]
    assert stop_command[-3:] == ["stop", "-m", "immediate"]
    status_command = calls[7][0]
    assert status_command[-1] == "status"

    dump_command = calls[4][0]
    assert dump_command[1:] == [
        "--format=custom",
        "--create",
        "--file",
        str(session.dump),
        DISPOSABLE_DATABASE,
    ]
    restore_command = calls[5][0]
    assert restore_command[1:] == [
        "--exit-on-error",
        "--use-set-session-authorization",
        f"--dbname={DISPOSABLE_DATABASE}",
        str(session.dump),
    ]

    monkeypatch.setattr(
        _postgres,
        "_current_source_identity",
        lambda: (COMMIT, "9" * 40),
    )
    monkeypatch.setattr(
        _postgres.tempfile,
        "TemporaryDirectory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source mismatch reached fixture-root allocation"),
        ),
    )
    with pytest.raises(
        DisposablePostgresApprovalRejected,
        match="source tree does not match",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pytest.fail("source-mismatched authority reached a cluster")


def test_restore_recreates_exact_target_without_transient_createdb() -> None:
    workflow_source = inspect.getsource(_postgres.DisposableRestoreWorkflow.restore)
    drop = workflow_source.index("_drop_disposable_database(self._target)")
    prepare = workflow_source.index(
        "_prepare_empty_restore_target(self._target_session)"
    )
    restore = workflow_source.index("_run_pg_restore(self._target_session)")
    assert drop < prepare < restore

    prepare_source = inspect.getsource(_postgres._prepare_empty_restore_target)
    assert prepare_source.index("_assert_restore_global_roles_are_exact(maintenance)") < (
        prepare_source.index("_run_green_restore_target_psql(validated)")
    )
    assert "_provision_base_roles" not in prepare_source
    assert "_provision_job_roles" not in prepare_source
    assert "rolcreatedb" not in prepare_source

    global_roles_source = inspect.getsource(
        _postgres._assert_restore_global_roles_are_exact
    )
    assert global_roles_source.count('"TimeZone=UTC"') == 3
    assert '"timezone=UTC"' not in global_roles_source

    target_input_source = inspect.getsource(_postgres._restore_target_input)
    assert "CREATE DATABASE" in target_input_source
    assert " OWNER trading_owner" in target_input_source
    assert "SET SESSION AUTHORIZATION trading_owner" in target_input_source
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public" in target_input_source
    assert "RESET SESSION AUTHORIZATION" in target_input_source
    assert " LOGIN" not in target_input_source
    assert " CREATEDB" not in target_input_source

    restore_source = inspect.getsource(_postgres._build_pg_restore_command)
    assert '"--create"' not in restore_source
    assert 'f"--dbname={DISPOSABLE_DATABASE}"' in restore_source


def test_restore_target_input_contains_database_local_authority_only() -> None:
    statement = _postgres._restore_target_input().decode("utf-8")
    assert statement.count("CREATE DATABASE") == 1
    assert f"CREATE DATABASE {DISPOSABLE_DATABASE} OWNER trading_owner;" in statement
    assert "REVOKE CONNECT, TEMPORARY ON DATABASE" in statement
    assert "GRANT CONNECT ON DATABASE" in statement
    assert "GRANT TEMPORARY ON DATABASE" in statement
    assert "ALTER SCHEMA public OWNER TO trading_owner;" in statement
    assert "ALTER ROLE trading_reader IN DATABASE" in statement
    assert "ALTER ROLE trading_jobs IN DATABASE" in statement
    assert all(
        forbidden not in statement
        for forbidden in (
            "CREATE ROLE",
            "DROP ROLE",
            "ALTER ROLE trading_jobs LOGIN",
            "ALTER ROLE trading_jobs NOLOGIN",
            "CREATEDB",
            "PASSWORD",
        )
    )


def test_planned_session_uses_only_predeclared_root_and_port(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = write_record(
        protected_record_dir / "approval.json",
        build_record(),
    )
    plan = protected_record_dir / "fixture-plan.json"
    plan.write_text(
        json.dumps(fixture_plan()),
        encoding="utf-8",
    )
    plan.chmod(0o600)
    _set_controls(monkeypatch, approval)
    monkeypatch.setenv("TRADING_TEST_DISPOSABLE_FIXTURE_PLAN", str(plan))
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)

    expected_root = Path("/tmp/phase4-postgres-fixture-plan-01")
    assert not expected_root.exists()
    with _postgres._disposable_postgres_session(
        operation_id=OPERATION_ID,
        planned=True,
        expected_lifecycle_actions=lifecycle_actions_for("MIGRATE"),
    ) as session:
        assert session.root == expected_root
        assert session.data == expected_root / "data"
        assert session.authority.context.port == 49152
        assert expected_root.is_dir()
    assert not expected_root.exists()


@pytest.mark.parametrize(
    ("lifecycle_kind", "factory"),
    (
        ("RESTORE", disposable_database),
        ("MIGRATE", disposable_restore_workflow),
    ),
)
def test_planned_public_runtime_api_rejects_a_different_lifecycle_before_initdb(
    lifecycle_kind: str,
    factory,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = write_record(
        protected_record_dir / "approval.json",
        build_record(),
    )
    plan_document = fixture_plan()
    plan_document["greenlight"]["operation_lifecycles"][0][  # type: ignore[index]
        "lifecycle_actions"
    ] = list(lifecycle_actions_for(lifecycle_kind))
    plan_document["canonical_record_sha256"] = fixture_plan_sha256(plan_document)
    plan = protected_record_dir / "fixture-plan.json"
    plan.write_text(json.dumps(plan_document), encoding="utf-8")
    plan.chmod(0o600)
    _set_controls(monkeypatch, approval)
    monkeypatch.setenv("TRADING_TEST_DISPOSABLE_FIXTURE_PLAN", str(plan))
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)

    with pytest.raises(
        DisposablePostgresApprovalRejected,
        match="fixture plan lifecycle does not match the runtime operation",
    ):
        with factory(operation_id=OPERATION_ID, planned=True):
            pass


def test_forged_postgres_commands_reject_without_subprocess_call(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_operation_specific_command_api()
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda binary: Path("/mock/postgresql") / binary,
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        returncode = 3 if command[-1] == "status" else 0
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(_postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(_postgres, "_loopback_listener_present", lambda _port: False)
    sessions: list[object] = []
    original_run_initdb = _postgres._run_initdb

    def capture_session(session):
        sessions.append(session)
        return original_run_initdb(session)

    monkeypatch.setattr(_postgres, "_run_initdb", capture_session)

    with disposable_postgres_cluster(operation_id=OPERATION_ID):
        session = sessions[0]
        initdb = _postgres._build_initdb_command(session)
        start = _postgres._build_pg_ctl_start_command(session)
        dump = _postgres._build_pg_dump_command(session)
        alternate_data = str(Path(initdb.arguments[1]).parent / "other-data")
        alternate_data_args = list(initdb.arguments)
        alternate_data_args[1] = alternate_data
        alternate_host_args = tuple(
            part.replace("-h 127.0.0.1", "-h 0.0.0.0")
            for part in start.arguments
        )
        forbidden_port_args = tuple(
            part.replace(f"-p {session.authority.context.port}", "-p 55432")
            for part in start.arguments
        )
        alternate_database_args = tuple(
            "other_database" if part == DISPOSABLE_DATABASE else part
            for part in dump.arguments
        )
        forged = (
            initdb._replace(arguments=tuple(alternate_data_args)),
            start._replace(arguments=alternate_host_args),
            start._replace(arguments=forbidden_port_args),
            dump._replace(arguments=alternate_database_args),
            dump._replace(arguments=(*dump.arguments, "service=forbidden")),
            initdb._replace(binary="psql"),
            initdb._replace(input_bytes=b"SELECT 1"),
            initdb._replace(environment=(*initdb.environment, ("PGPORT", "55432"))),
            initdb._replace(arguments=(*initdb.arguments, "--help")),
        )
        before = len(calls)
        for command in forged:
            with pytest.raises(
                DisposablePostgresApprovalRejected,
                match=(
                    "^PostgreSQL command does not match validated "
                    "disposable operation$"
                ),
            ):
                _postgres._execute_postgres_command(session, command)
        assert len(calls) == before


def test_red_derivation_owns_one_fixed_autocommit_connection(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_operation_specific_command_api()
    sql_file = ROOT / "ops/postgres/provision-roles.sql"
    original_sql = sql_file.read_bytes()
    record = write_record(
        protected_record_dir / "approval.json",
        _red_record(sql_file),
    )
    _set_controls(monkeypatch, record, scope=RED_SCOPE)
    calls: list[tuple[list[str], dict[str, object]]] = []
    sessions: list[object] = []
    _install_mocked_postgres_boundary(monkeypatch, calls, sessions)
    monkeypatch.setattr(_postgres, "_upgrade_to_revision", lambda *_args: None)
    connection = _FakeRedConnection()
    connect_calls: list[tuple[str, bool]] = []

    def connect(conninfo: str, *, autocommit: bool):
        connect_calls.append((conninfo, autocommit))
        return connection

    monkeypatch.setattr(_postgres.psycopg, "connect", connect)

    with _postgres.disposable_red_derivation_database(
        operation_id=OPERATION_ID,
        red_sql_file=sql_file,
    ) as workflow:
        assert workflow.database is connection
        before = len(calls)
        with connection.transaction(force_rollback=True):
            workflow.execute_reviewed_sql()
        assert len(calls) == before

    assert len(connect_calls) == 1
    conninfo, autocommit = connect_calls[0]
    assert f"dbname={DISPOSABLE_DATABASE}" in conninfo
    assert "user=trading_owner" in conninfo
    assert autocommit is True
    assert connection.queries == [original_sql]
    assert all(kwargs.get("input") != original_sql for _, kwargs in calls)


def test_reviewed_red_sql_uses_one_byte_snapshot_after_source_path_replacement(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sql = b"SELECT 'reviewed snapshot';\n"
    replacement_sql = b"SELECT 'unreviewed replacement';\n"
    with tempfile.TemporaryDirectory(
        prefix=".reviewed-red-sql-",
        dir=ROOT / "tests/jobs",
    ) as raw:
        sql_file = Path(raw) / "reviewed.sql"
        sql_file.write_bytes(original_sql)
        record = write_record(
            protected_record_dir / "approval.json",
            _red_record(sql_file),
        )
        _set_controls(monkeypatch, record, scope=RED_SCOPE)
        reads: list[Path] = []
        original_read_bytes = Path.read_bytes

        def counted_read_bytes(path: Path) -> bytes:
            if path.resolve() == sql_file.resolve():
                reads.append(path.resolve())
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
        calls: list[tuple[list[str], dict[str, object]]] = []
        sessions: list[object] = []

        def replace_reviewed_path() -> None:
            replacement = sql_file.with_name("replacement.sql")
            replacement.write_bytes(replacement_sql)
            replacement.replace(sql_file)

        _install_mocked_postgres_boundary(
            monkeypatch,
            calls,
            sessions,
            on_initdb=replace_reviewed_path,
        )

        monkeypatch.setattr(_postgres, "_upgrade_to_revision", lambda *_args: None)
        connection = _FakeRedConnection()
        monkeypatch.setattr(
            _postgres.psycopg,
            "connect",
            lambda _conninfo, *, autocommit: connection,
        )

        with _postgres.disposable_red_derivation_database(
            operation_id=OPERATION_ID,
            red_sql_file=sql_file,
        ) as workflow:
            assert workflow.database is connection
            with connection.transaction(force_rollback=True):
                workflow.execute_reviewed_sql()

        assert connection.queries == [original_sql]
        assert connection.queries[0] is sessions[0].authority.reviewed_sql.content
        assert connection.queries != [replacement_sql]
        assert all(kwargs.get("input") != original_sql for _, kwargs in calls)
        assert reads == [sql_file.resolve()]


def test_cleanup_stop_failure_with_surviving_context_raises_fixed_error(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_operation_specific_command_api()
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda binary: Path("/mock/postgresql") / binary,
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        rendered = [str(part) for part in command]
        calls.append(rendered)
        if rendered[-3:] == ["stop", "-m", "immediate"]:
            return subprocess.CompletedProcess(command, 1)
        if rendered[-1] == "status":
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(_postgres, "_loopback_listener_present", lambda _port: False)

    with pytest.raises(
        _postgres.DisposablePostgresCleanupError,
        match="^disposable PostgreSQL cleanup could not be verified$",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pass

    assert sum(command[-3:] == ["stop", "-m", "immediate"] for command in calls) == 2
    assert sum(command[-1] == "status" for command in calls) == 2


def test_cleanup_stop_failure_forces_one_bounded_fallback_when_absent(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_operation_specific_command_api()
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda binary: Path("/mock/postgresql") / binary,
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        rendered = [str(part) for part in command]
        calls.append(rendered)
        if rendered[-3:] == ["stop", "-m", "immediate"]:
            return subprocess.CompletedProcess(command, 1)
        if rendered[-1] == "status":
            return subprocess.CompletedProcess(command, 3)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(_postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(_postgres, "_loopback_listener_present", lambda _port: False)

    with disposable_postgres_cluster(operation_id=OPERATION_ID):
        pass

    assert sum(command[-3:] == ["stop", "-m", "immediate"] for command in calls) == 2
    assert sum(command[-1] == "status" for command in calls) == 2


def test_cleanup_listener_survivor_raises_after_bounded_fallbacks(
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_operation_specific_command_api()
    record = write_record(protected_record_dir / "approval.json", build_record())
    _set_controls(monkeypatch, record)
    monkeypatch.setattr(_postgres, "_current_source_identity", lambda: (COMMIT, TREE))
    monkeypatch.setattr(_postgres, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        _postgres,
        "_postgres_executable_path",
        lambda binary: Path("/mock/postgresql") / binary,
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        rendered = [str(part) for part in command]
        calls.append(rendered)
        returncode = 3 if rendered[-1] == "status" else 0
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(_postgres.subprocess, "run", fake_run)
    monkeypatch.setattr(_postgres, "_loopback_listener_present", lambda _port: True)

    with pytest.raises(
        _postgres.DisposablePostgresCleanupError,
        match="^disposable PostgreSQL cleanup could not be verified$",
    ):
        with disposable_postgres_cluster(operation_id=OPERATION_ID):
            pass

    assert sum(command[-3:] == ["stop", "-m", "immediate"] for command in calls) == 2
    assert sum(command[-1] == "status" for command in calls) == 2


def test_os_assigned_port_retries_forbidden_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigned = iter((55432, 49152))
    binds: list[tuple[str, int]] = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, address):
            binds.append(address)

        def getsockname(self):
            return ("127.0.0.1", next(assigned))

    monkeypatch.setattr(_postgres.socket, "socket", FakeSocket)
    assert _postgres._unused_tcp_port() == 49152
    assert binds == [("127.0.0.1", 0), ("127.0.0.1", 0)]


REVIEWED_DATABASE_CALLSITE_FILES = (
    "tests/control_api/test_phase3b_transactions.py",
    "tests/control_api/test_real_data_apply.py",
    "tests/control_api/test_fixture_importer.py",
    "tests/control_api/test_alembic_schema.py",
    "tests/control_api/test_postgres_api.py",
    "tests/control_api/test_postgres_repositories.py",
    "tests/control_api/test_dual_read.py",
    "tests/control_api/test_foundation_postgres_runtime_parity.py",
    "tests/event_ledger/test_snapshot_postgres_runtime.py",
    "tests/market_data/test_postgres_runtime.py",
    "tests/jobs/test_repository_queries.py",
    "tests/jobs/test_worker_leases.py",
    "tests/jobs/test_repository_transactions.py",
    "tests/jobs/test_worker_claims.py",
    "tests/jobs/test_scheduler_repository.py",
    "tests/jobs/test_worker_recovery.py",
    "tests/jobs/test_job_role_permissions.py",
    "tests/jobs/test_job_transition_authority.py",
    "tests/jobs/test_repository_enqueue.py",
    "tests/jobs/test_alembic_jobs_schema.py",
)


def test_all_reviewed_database_calls_have_unique_explicit_operation_ids() -> None:
    operation_ids: list[str] = []
    missing: list[str] = []
    for relative_path in REVIEWED_DATABASE_CALLSITE_FILES:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {
                "disposable_database",
                "disposable_legacy_database",
                "disposable_restore_workflow",
            }:
                continue
            keyword = next(
                (item for item in node.keywords if item.arg == "operation_id"),
                None,
            )
            if keyword is None:
                missing.append(f"{relative_path}:{node.lineno}")
                continue
            value = (
                keyword.value.value
                if isinstance(keyword.value, ast.Constant)
                else constants.get(keyword.value.id)
                if isinstance(keyword.value, ast.Name)
                else None
            )
            if not isinstance(value, str) or not value:
                missing.append(f"{relative_path}:{node.lineno}")
                continue
            operation_ids.append(value)

    assert missing == []
    assert len(operation_ids) == 26
    assert len(set(operation_ids)) == 26
