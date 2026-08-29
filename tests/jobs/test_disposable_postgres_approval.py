from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import ModuleType

import pytest


ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "schemas/disposable-postgres-test-approval.schema.json"
VALIDATOR = ROOT / "scripts/validate_disposable_postgres_approval.py"

COMMIT = "a" * 40
TREE = "b" * 40
NOW = datetime(2026, 7, 16, 18, 0, 0, tzinfo=UTC)
TEST_PATH = "tests/jobs/test_postgres_harness.py"
OPERATION_ID = "postgres-harness-mocked-lifecycle-v1"
SAFE_PGDATA = "/tmp/phase4-postgres-mocked/data"
SAFE_PORT = 49152
RED_SQL_PATH = "tests/jobs/fixtures/derive-red-catalog.sql"
RED_SQL_SHA256 = "c" * 64
CATALOG_TEST_PATH = "tests/jobs/test_job_authority_catalog.py"
CATALOG_OPERATION_ID = "jobs-authority-catalog-red-v1"
DERIVATION_OPERATION_ID = "jobs-authority-catalog-derivation-red-v1"
EVENT_TEST_PATH = "tests/jobs/test_job_event_chain_authority.py"
EVENT_OPERATION_ID = "jobs-event-chain-authority-red-v1"
SOURCE_BINDING_PATHS = (
    "alembic/versions/0001_phase3_operational_store.py",
    "alembic/versions/0002_quarantine_lineage.py",
    "alembic/versions/0003_contract_lineage_repair.py",
    "alembic/versions/0004_durable_research_jobs.py",
    "alembic/versions/0005_job_plane_role_split.py",
    "alembic/versions/0006_job_transition_database_authority.py",
    "alembic/versions/0007_job_event_chain_authority.py",
    "alembic/versions/0008_trading_domain_ledger.py",
    "alembic/versions/0009_canonical_market_data.py",
    "alembic/versions/0010_engine_event_ledger.py",
    "alembic/versions/0011_engine_backtest_worker_authority.py",
    "alembic/versions/0012_p1_engine_projection_authority.py",
    "alembic/versions/0013_engine_backtest_enqueue_authority.py",
    "ops/postgres/provision-job-roles.sql",
    "ops/postgres/provision-roles.sql",
)


def _canonical_digest(document: dict[str, object]) -> str:
    unsigned = {
        key: value
        for key, value in document.items()
        if key != "canonical_record_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def refresh_digest(document: dict[str, object]) -> None:
    document["canonical_record_sha256"] = _canonical_digest(document)


def build_record(
    *,
    scope: str = "DISPOSABLE_PG_GREEN",
    test_path: str = TEST_PATH,
    operation_id: str = OPERATION_ID,
) -> dict[str, object]:
    document: dict[str, object] = {
        "record_kind": "DISPOSABLE_POSTGRES_TEST_APPROVAL",
        "schema_version": 1,
        "record_id": "DISPOSABLE_POSTGRES_TEST_20260716_A",
        "scope": scope,
        "source": {"commit": COMMIT, "tree": TREE},
        "validity": {
            "approved_at_utc": "2026-07-16T17:00:00Z",
            "expires_at_utc": "2026-07-16T19:00:00Z",
        },
        "review": {
            "decision": "APPROVED",
            "operator_identity": "operator.example",
            "reviewer_identity": "reviewer.example",
        },
        "approved_operations": [
            {"test_path": test_path, "operation_id": operation_id}
        ],
        "source_bindings": [
            {
                "path": path,
                "sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest(),
            }
            for path in SOURCE_BINDING_PATHS
        ],
        "constraints": {
            "pgdata_prefix": "/tmp/phase4-postgres-",
            "bind_host": "127.0.0.1",
            "port_allocation": "EXPLICITLY_APPROVED",
            "forbidden_ports": [3002, 8401, 55432],
            "cluster_name": "trading-agent-disposable-tests",
            "database_name": "trading_agent_disposable_test",
            "runtime_settings_policy": "REJECT_IF_PRESENT",
        },
        "red_sql_binding": None,
        "canonical_record_sha256": "0" * 64,
    }
    refresh_digest(document)
    return document


def write_record(path: Path, document: dict[str, object]) -> Path:
    path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


@pytest.fixture
def protected_record_dir():
    with tempfile.TemporaryDirectory(
        prefix="disposable-postgres-approval-",
        dir="/tmp",
    ) as raw:
        yield Path(raw)


def load_validator() -> ModuleType:
    if not VALIDATOR.is_file():
        pytest.fail("disposable PostgreSQL validator has not been implemented")
    spec = importlib.util.spec_from_file_location(
        "disposable_postgres_approval_validator_under_test",
        VALIDATOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context(module: ModuleType, **changes: object):
    values: dict[str, object] = {
        "scope": "DISPOSABLE_PG_GREEN",
        "source_commit": COMMIT,
        "source_tree": TREE,
        "test_path": TEST_PATH,
        "operation_id": OPERATION_ID,
        "pgdata": SAFE_PGDATA,
        "bind_host": "127.0.0.1",
        "port": SAFE_PORT,
        "cluster_name": "trading-agent-disposable-tests",
        "database_name": "trading_agent_disposable_test",
        "runtime_setting_names": frozenset(),
        "now": NOW,
        "red_sql_path": None,
        "red_sql_sha256": None,
    }
    values.update(changes)
    return module.DisposablePostgresApprovalContext(**values)


def test_schema_and_validator_are_closed_explicit_artifacts() -> None:
    assert SCHEMA.is_file()
    assert VALIDATOR.is_file()

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
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


def test_canonical_record_matches_draft_2020_schema_and_validator_binding_order() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    document = build_record()
    dashboard_root = ROOT / "apps" / "dashboard"
    script = """
const fs = require('fs');
const Ajv2020 = require(process.argv[1] + '/node_modules/@redocly/ajv/dist/2020').default;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const validate = new Ajv2020({allErrors: true, strict: false}).compile(input.schema);
const valid = validate(input.document);
process.stdout.write(JSON.stringify({valid, errors: validate.errors}));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(dashboard_root)],
        input=json.dumps({"schema": schema, "document": document}),
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result == {"valid": True, "errors": None}

    prefix_items = schema["properties"]["source_bindings"]["prefixItems"]
    schema_paths = tuple(
        schema["$defs"][item["$ref"].removeprefix("#/$defs/")]["allOf"][1][
            "properties"
        ]["path"]["const"]
        for item in prefix_items
    )
    module = load_validator()
    assert schema_paths == module.APPROVAL_SOURCE_BINDING_PATHS
    assert schema["properties"]["source_bindings"]["minItems"] == len(
        module.APPROVAL_SOURCE_BINDING_PATHS
    )
    assert schema["properties"]["source_bindings"]["maxItems"] == len(
        module.APPROVAL_SOURCE_BINDING_PATHS
    )


def test_pure_validator_accepts_exact_complete_record() -> None:
    module = load_validator()
    module.validate_disposable_postgres_approval(build_record(), context(module))
    module.validate_source_binding_files(build_record(), ROOT)


def test_source_binding_bytes_and_canonical_order_fail_closed() -> None:
    module = load_validator()
    drifted = build_record()
    drifted["source_bindings"][0]["sha256"] = "f" * 64  # type: ignore[index]
    refresh_digest(drifted)
    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match="source binding digest does not match",
    ):
        module.validate_source_binding_files(drifted, ROOT)

    reordered = build_record()
    reordered["source_bindings"][0], reordered["source_bindings"][1] = (  # type: ignore[index]
        reordered["source_bindings"][1],  # type: ignore[index]
        reordered["source_bindings"][0],  # type: ignore[index]
    )
    refresh_digest(reordered)
    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match="source bindings are not in canonical order",
    ):
        module.validate_disposable_postgres_approval(reordered, context(module))


@pytest.mark.parametrize(
    "case",
    (
        "missing_top_level",
        "unknown_top_level",
        "unknown_nested",
        "placeholder",
        "expired",
        "wrong_scope",
        "source_commit",
        "source_tree",
        "same_identity",
        "unapproved_test_path",
        "unapproved_operation",
        "digest",
    ),
)
def test_pure_validator_rejects_incomplete_or_drifted_records(case: str) -> None:
    module = load_validator()
    document = build_record()
    expected_context = context(module)

    if case == "missing_top_level":
        document.pop("review")
    elif case == "unknown_top_level":
        document["unexpected"] = "value"
    elif case == "unknown_nested":
        document["source"]["unexpected"] = "value"  # type: ignore[index]
    elif case == "placeholder":
        document["review"]["reviewer_identity"] = (  # type: ignore[index]
            "REQUIRES_REVIEWER_INPUT"
        )
    elif case == "expired":
        document["validity"]["expires_at_utc"] = (  # type: ignore[index]
            "2026-07-16T18:00:00Z"
        )
    elif case == "wrong_scope":
        document["scope"] = "DISPOSABLE_PG_RED"
    elif case == "source_commit":
        document["source"]["commit"] = "d" * 40  # type: ignore[index]
    elif case == "source_tree":
        document["source"]["tree"] = "e" * 40  # type: ignore[index]
    elif case == "same_identity":
        document["review"]["reviewer_identity"] = (  # type: ignore[index]
            "operator.example"
        )
    elif case == "unapproved_test_path":
        expected_context = context(module, test_path="tests/jobs/test_other.py")
    elif case == "unapproved_operation":
        expected_context = context(module, operation_id="postgres-harness-other-v1")
    elif case == "digest":
        document["canonical_record_sha256"] = "f" * 64

    if case != "digest":
        refresh_digest(document)
    with pytest.raises(module.DisposablePostgresApprovalRejected):
        module.validate_disposable_postgres_approval(document, expected_context)


@pytest.mark.parametrize(
    ("change", "value"),
    (
        ("pgdata", "/var/tmp/phase4-postgres-not-approved/data"),
        ("bind_host", "0.0.0.0"),
        ("port", 3002),
        ("port", 8401),
        ("port", 55432),
        ("cluster_name", "postgres"),
        ("database_name", "trading_agent"),
        ("runtime_setting_names", frozenset({"PGDATA"})),
        ("runtime_setting_names", frozenset({"DATABASE_URL"})),
    ),
)
def test_pure_validator_rejects_unsafe_runtime_context(
    change: str,
    value: object,
) -> None:
    module = load_validator()
    with pytest.raises(module.DisposablePostgresApprovalRejected):
        module.validate_disposable_postgres_approval(
            build_record(),
            context(module, **{change: value}),
        )


def _red_record() -> dict[str, object]:
    document = build_record(scope="DISPOSABLE_PG_RED")
    document["red_sql_binding"] = {
        "operation_id": OPERATION_ID,
        "sql_path": RED_SQL_PATH,
        "sql_sha256": RED_SQL_SHA256,
    }
    refresh_digest(document)
    return document


def _three_operation_red_record() -> dict[str, object]:
    document = build_record(
        scope="DISPOSABLE_PG_RED",
        test_path=CATALOG_TEST_PATH,
        operation_id=CATALOG_OPERATION_ID,
    )
    document["approved_operations"] = [
        {
            "test_path": CATALOG_TEST_PATH,
            "operation_id": CATALOG_OPERATION_ID,
        },
        {
            "test_path": CATALOG_TEST_PATH,
            "operation_id": DERIVATION_OPERATION_ID,
        },
        {
            "test_path": EVENT_TEST_PATH,
            "operation_id": EVENT_OPERATION_ID,
        },
    ]
    document["red_sql_binding"] = {
        "operation_id": DERIVATION_OPERATION_ID,
        "sql_path": RED_SQL_PATH,
        "sql_sha256": RED_SQL_SHA256,
    }
    refresh_digest(document)
    return document


def test_three_task5_operations_share_one_binding_without_cross_path_rejection(
) -> None:
    module = load_validator()
    document = _three_operation_red_record()

    for test_path, operation_id, requests_sql in (
        (CATALOG_TEST_PATH, CATALOG_OPERATION_ID, False),
        (CATALOG_TEST_PATH, DERIVATION_OPERATION_ID, True),
        (EVENT_TEST_PATH, EVENT_OPERATION_ID, False),
    ):
        module.validate_disposable_postgres_approval(
            document,
            context(
                module,
                scope="DISPOSABLE_PG_RED",
                test_path=test_path,
                operation_id=operation_id,
                red_sql_path=RED_SQL_PATH if requests_sql else None,
                red_sql_sha256=RED_SQL_SHA256 if requests_sql else None,
            ),
        )

    for expected_sql_sha256 in (None, RED_SQL_SHA256):
        module.validate_disposable_postgres_approval_record(
            document,
            expected_scope="DISPOSABLE_PG_RED",
            expected_commit=COMMIT,
            expected_tree=TREE,
            expected_sql_sha256=expected_sql_sha256,
            runtime_setting_names=frozenset(),
            now=NOW,
        )


def test_record_preflight_preserves_runtime_setting_rejection() -> None:
    module = load_validator()

    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match="runtime database settings are present",
    ):
        module.validate_disposable_postgres_approval_record(
            _three_operation_red_record(),
            expected_scope="DISPOSABLE_PG_RED",
            expected_commit=COMMIT,
            expected_tree=TREE,
            expected_sql_sha256=RED_SQL_SHA256,
            runtime_setting_names=frozenset({"PGDATA"}),
            now=NOW,
        )


@pytest.mark.parametrize(
    "name",
    (
        "TRADING_DATABASE_HOST",
        "TRADING_DATABASE_PORT",
        "TRADING_DATABASE_NAME",
        "TRADING_DATABASE_USER",
        "TRADING_DATABASE_PASSWORD",
    ),
)
def test_runtime_setting_inventory_includes_application_connection_names(
    name: str,
) -> None:
    module = load_validator()

    assert module._is_runtime_setting_name(name)


def test_operation_ids_must_be_globally_unique_across_test_paths() -> None:
    module = load_validator()
    document = _three_operation_red_record()
    document["approved_operations"].append(  # type: ignore[union-attr]
        {
            "test_path": "tests/jobs/test_other_authority.py",
            "operation_id": EVENT_OPERATION_ID,
        }
    )
    refresh_digest(document)

    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match="globally unique",
    ):
        module.validate_disposable_postgres_approval(
            document,
            context(
                module,
                scope="DISPOSABLE_PG_RED",
                test_path=CATALOG_TEST_PATH,
                operation_id=CATALOG_OPERATION_ID,
            ),
        )


def test_red_sql_requires_exact_operation_path_and_reviewed_hash() -> None:
    module = load_validator()
    red_context = context(
        module,
        scope="DISPOSABLE_PG_RED",
        red_sql_path=RED_SQL_PATH,
        red_sql_sha256=RED_SQL_SHA256,
    )
    module.validate_disposable_postgres_approval(_red_record(), red_context)

    for change in (
        {"red_sql_path": "tests/jobs/fixtures/other.sql"},
        {"red_sql_sha256": "d" * 64},
        {"operation_id": "postgres-harness-other-v1"},
    ):
        changed_context = {
            "red_sql_path": RED_SQL_PATH,
            "red_sql_sha256": RED_SQL_SHA256,
        }
        changed_context.update(change)
        with pytest.raises(module.DisposablePostgresApprovalRejected):
            module.validate_disposable_postgres_approval(
                _red_record(),
                context(
                    module,
                    scope="DISPOSABLE_PG_RED",
                    **changed_context,
                ),
            )

    missing_binding = _red_record()
    missing_binding["red_sql_binding"] = None
    refresh_digest(missing_binding)
    with pytest.raises(module.DisposablePostgresApprovalRejected):
        module.validate_disposable_postgres_approval(missing_binding, red_context)


def test_protected_record_loader_rejects_loose_mode_and_symlink(
    protected_record_dir: Path,
) -> None:
    module = load_validator()
    record = write_record(protected_record_dir / "approval.json", build_record())
    assert module.load_protected_approval_record(record) == build_record()

    record.chmod(0o644)
    with pytest.raises(module.DisposablePostgresApprovalRejected):
        module.load_protected_approval_record(record)

    record.chmod(0o600)
    link = protected_record_dir / "approval-link.json"
    link.symlink_to(record)
    with pytest.raises(module.DisposablePostgresApprovalRejected):
        module.load_protected_approval_record(link)


@pytest.mark.parametrize("valid", (True, False))
def test_cli_and_pure_function_make_the_same_decision(
    valid: bool,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_validator()
    document = build_record()
    if not valid:
        document["source"]["tree"] = "e" * 40  # type: ignore[index]
        refresh_digest(document)
    record = write_record(protected_record_dir / "approval.json", document)

    monkeypatch.setattr(module, "_source_identity", lambda _root: (COMMIT, TREE))
    monkeypatch.setattr(module, "_utc_now", lambda: NOW)
    monkeypatch.setattr(module, "_runtime_setting_names", lambda: frozenset())

    try:
        module.validate_disposable_postgres_approval_record(
            document,
            expected_scope="DISPOSABLE_PG_GREEN",
            expected_commit=COMMIT,
            expected_tree=TREE,
            expected_sql_sha256=None,
            runtime_setting_names=frozenset(),
            now=NOW,
        )
    except module.DisposablePostgresApprovalRejected:
        pure_result = 1
    else:
        pure_result = 0

    cli_result = module.main(
        [
            "--record",
            str(record),
            "--expected-scope",
            "DISPOSABLE_PG_GREEN",
            "--expected-commit",
            COMMIT,
            "--expected-tree",
            TREE,
        ]
    )
    assert cli_result == pure_result
    output = capsys.readouterr()
    assert ("VALID:" in output.out) is valid
    assert ("REJECTED:" in output.err) is (not valid)


def test_source_identity_rejects_a_dirty_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_validator()
    results = iter(
        (
            subprocess.CompletedProcess([], 0, stdout=f"{COMMIT}\n"),
            subprocess.CompletedProcess([], 0, stdout=f"{TREE}\n"),
            subprocess.CompletedProcess([], 0, stdout=" M changed.py\n"),
        )
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: next(results),
    )

    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match="checkout is not clean",
    ):
        module._source_identity(ROOT)


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("schema_bool", "schema version is not supported"),
        ("schema_float", "schema version is not supported"),
        ("scope_list", "record scope is invalid"),
        ("source_scalar", "source fields are missing or unknown"),
        (
            "operations_mapping",
            "approved operations are missing or excessive",
        ),
        ("constraints_list", "constraints fields are missing or unknown"),
        ("red_binding_list", "RED SQL binding fields are missing or unknown"),
    ),
)
def test_exact_json_types_have_fixed_pure_and_cli_rejection(
    case: str,
    reason: str,
    protected_record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_validator()
    document = build_record()
    if case == "schema_bool":
        document["schema_version"] = True
    elif case == "schema_float":
        document["schema_version"] = 1.0
    elif case == "scope_list":
        document["scope"] = []
    elif case == "source_scalar":
        document["source"] = "invalid"
    elif case == "operations_mapping":
        document["approved_operations"] = {}
    elif case == "constraints_list":
        document["constraints"] = []
    elif case == "red_binding_list":
        document["red_sql_binding"] = []
    refresh_digest(document)
    record = write_record(protected_record_dir / "approval.json", document)

    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match=f"^{re.escape(reason)}$",
    ):
        module.validate_disposable_postgres_approval(document, context(module))

    monkeypatch.setattr(module, "_source_identity", lambda _root: (COMMIT, TREE))
    monkeypatch.setattr(module, "_utc_now", lambda: NOW)
    monkeypatch.setattr(module, "_runtime_setting_names", lambda: frozenset())
    try:
        cli_result = module.main(
            [
                "--record",
                str(record),
                "--expected-scope",
                "DISPOSABLE_PG_GREEN",
                "--expected-commit",
                COMMIT,
                "--expected-tree",
                TREE,
            ]
        )
    except (TypeError, AttributeError) as error:
        pytest.fail(f"CLI leaked raw {type(error).__name__}")
    assert cli_result == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"REJECTED: {reason}\n"


@pytest.mark.parametrize(
    ("change", "value", "reason"),
    (
        ("scope", [], "requested scope is invalid"),
        ("operation_id", [], "current operation id is invalid"),
        ("now", "invalid", "validation time is not timezone-aware"),
        ("port", True, "PostgreSQL port is forbidden or invalid"),
        (
            "runtime_setting_names",
            [],
            "runtime database settings context is invalid",
        ),
    ),
)
def test_wrong_context_types_raise_fixed_approval_rejection(
    change: str,
    value: object,
    reason: str,
) -> None:
    module = load_validator()
    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match=f"^{re.escape(reason)}$",
    ):
        module.validate_disposable_postgres_approval(
            build_record(),
            context(module, **{change: value}),
        )


def test_wrong_context_object_raises_fixed_approval_rejection() -> None:
    module = load_validator()
    with pytest.raises(
        module.DisposablePostgresApprovalRejected,
        match="^approval context is invalid$",
    ):
        module.validate_disposable_postgres_approval(build_record(), object())


def _live_three_operation_red_record() -> dict[str, object]:
    document = _three_operation_red_record()
    now = datetime.now(UTC).replace(microsecond=0)
    document["validity"] = {
        "approved_at_utc": (now - timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "expires_at_utc": (now + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    refresh_digest(document)
    return document


def _safe_cli_environment() -> dict[str, str]:
    fixed_names = {
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
    return {
        name: value
        for name, value in os.environ.items()
        if name not in fixed_names
        and not name.startswith(
            ("TRADING_DATABASE_", "TRADING_DB_", "TRADING_POSTGRES_")
        )
    }


def _planned_cli(record: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--record",
            str(record),
            "--expected-scope",
            "DISPOSABLE_PG_RED",
            "--expected-commit",
            COMMIT,
            "--expected-tree",
            TREE,
            "--expected-sql-sha256",
            RED_SQL_SHA256,
            *extra,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_safe_cli_environment(),
    )


def test_exact_planned_cli_accepts_the_three_operation_red_record(
    protected_record_dir: Path,
) -> None:
    record = write_record(
        protected_record_dir / "approval.json",
        _live_three_operation_red_record(),
    )

    result = _planned_cli(record)

    assert result.returncode == 0
    assert result.stdout == "VALID: disposable PostgreSQL authority record matches\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    "case",
    (
        "scope",
        "commit",
        "tree",
        "sql_hash",
        "duplicate_operation_id",
        "unapproved_binding",
        "invalid_protected_file",
    ),
)
def test_exact_planned_cli_rejects_every_review_or_file_drift(
    case: str,
    protected_record_dir: Path,
) -> None:
    document = _live_three_operation_red_record()
    extra: tuple[str, ...] = ()
    if case == "scope":
        extra = ("--expected-scope", "DISPOSABLE_PG_GREEN")
    elif case == "commit":
        extra = ("--expected-commit", "d" * 40)
    elif case == "tree":
        extra = ("--expected-tree", "e" * 40)
    elif case == "sql_hash":
        extra = ("--expected-sql-sha256", "d" * 64)
    elif case == "duplicate_operation_id":
        document["approved_operations"].append(  # type: ignore[union-attr]
            {
                "test_path": "tests/jobs/test_other_authority.py",
                "operation_id": EVENT_OPERATION_ID,
            }
        )
        refresh_digest(document)
    elif case == "unapproved_binding":
        document["red_sql_binding"]["operation_id"] = (  # type: ignore[index]
            "unapproved-binding-v1"
        )
        refresh_digest(document)
    record = write_record(protected_record_dir / "approval.json", document)
    if case == "invalid_protected_file":
        record.chmod(0o644)

    result = _planned_cli(record, *extra)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("REJECTED: ")
    assert "operator.example" not in result.stderr
    assert "reviewer.example" not in result.stderr
