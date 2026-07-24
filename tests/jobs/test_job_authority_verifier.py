from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from packages.job_authority import verifier
from packages.job_authority.verifier import (
    AuthorityManifest,
    AuthorityEvidence,
    CatalogEvidence,
    FrozenAuthorityContract,
    MigrationAuthorityLiterals,
    UnsafeCatalogSettingError,
    Violation,
    capture_catalog,
    find_event_chain_violations,
    load_authority_manifest,
    load_frozen_contract,
    load_migration_authority_literals,
    load_migration_literals,
    verify_authority_manifest,
    verify_authority,
)
import scripts.verify_job_plane_authority as verifier_cli


CATALOG_QUERY_ID = "job-plane-catalog-v1"
CATALOG_SQL = "SELECT record_type, unsafe_key, canonical_line FROM catalog"
EVENT_CHAIN_QUERY_ID = "job-plane-event-chain-v1"
EVENT_CHAIN_SQL = "SELECT code, job_id, event_id, sequence FROM violations"

ROOT = Path(__file__).parents[2]
FROZEN_CONTRACT_PATH = (
    ROOT / "ops/postgres/job-plane-authority/query-contract-v1.json"
)
FROZEN_REPAIR_PATH = ROOT / "ops/postgres/job-plane-authority/acl-repair-v1.sql"
MIGRATION_0007_PATH = ROOT / "alembic/versions/0007_job_event_chain_authority.py"
AUTHORITY_MANIFEST_PATH = (
    ROOT / "ops/postgres/job-plane-authority/authority-manifest-v1.json"
)
CATALOG_AUTHORITY_TEST_PATH = ROOT / "tests/jobs/test_job_authority_catalog.py"
REVIEWED_0006_CATALOG_SHA256 = (
    "b2dd91dbb12d585579e69b81394a530128fe84bc1dd2c7ef7683c9353eb1e4d1"
)
REVIEWED_0007_CATALOG_SHA256 = (
    "1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40"
)


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _Connection:
    def __init__(self, responses: list[list[tuple[object, ...]]]) -> None:
        self._responses = iter(responses)
        self.queries: list[str] = []

    def execute(self, query: str) -> _Cursor:
        self.queries.append(query)
        return _Cursor(next(self._responses))


def _contract() -> FrozenAuthorityContract:
    return FrozenAuthorityContract(
        catalog_query_id=CATALOG_QUERY_ID,
        catalog_sql=CATALOG_SQL,
        event_chain_query_id=EVENT_CHAIN_QUERY_ID,
        event_chain_sql=EVENT_CHAIN_SQL,
    )


def _write_contract(path: Path, **overrides: object) -> None:
    document: dict[str, object] = {
        "catalog_query_id": CATALOG_QUERY_ID,
        "catalog_sql": CATALOG_SQL,
        "event_chain_query_id": EVENT_CHAIN_QUERY_ID,
        "event_chain_sql": EVENT_CHAIN_SQL,
    }
    document.update(overrides)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_migration(
    path: Path,
    *,
    revision: str = "0007_job_event_chain_authority",
    catalog_sql: str = CATALOG_SQL,
) -> None:
    path.write_text(
        "\n".join(
            (
                f"revision = {revision!r}",
                f"CATALOG_QUERY_ID = {CATALOG_QUERY_ID!r}",
                f"CATALOG_SNAPSHOT_SQL = {catalog_sql!r}",
                f"EVENT_CHAIN_QUERY_ID = {EVENT_CHAIN_QUERY_ID!r}",
                f"EVENT_CHAIN_VIOLATIONS_SQL = {EVENT_CHAIN_SQL!r}",
                "raise RuntimeError('migration must never execute')",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_records_are_exact_frozen_slotted_dataclasses() -> None:
    contract = _contract()
    catalog = CatalogEvidence(
        query_id=CATALOG_QUERY_ID,
        sha256="0" * 64,
        row_count=0,
        canonical_bytes=b"",
    )
    violation = Violation(
        code="NO_HISTORY",
        job_id="job-1",
        event_id=None,
        sequence=None,
    )
    authority = AuthorityEvidence(
        head="0007_job_event_chain_authority",
        catalog=catalog,
        event_chain_query_id=EVENT_CHAIN_QUERY_ID,
        violations=(violation,),
    )

    assert not hasattr(contract, "__dict__")
    assert not hasattr(catalog, "__dict__")
    assert not hasattr(violation, "__dict__")
    assert not hasattr(authority, "__dict__")
    with pytest.raises(FrozenInstanceError):
        contract.catalog_sql = "changed"  # type: ignore[misc]


def test_load_frozen_contract_accepts_only_the_four_exact_string_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contract.json"
    _write_contract(path)

    assert load_frozen_contract(path) == _contract()

    _write_contract(path, expected_catalog_sha256="0" * 64)
    with pytest.raises(ValueError, match="fields are missing or unknown"):
        load_frozen_contract(path)

    _write_contract(path, event_chain_sql=7)
    with pytest.raises(ValueError, match="non-empty strings"):
        load_frozen_contract(path)


def test_load_frozen_contract_rejects_non_utf8_and_duplicate_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contract.json"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="strict UTF-8 JSON"):
        load_frozen_contract(path)

    path.write_text(
        '{"catalog_query_id":"a","catalog_query_id":"b",'
        '"catalog_sql":"c","event_chain_query_id":"d",'
        '"event_chain_sql":"e"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate key"):
        load_frozen_contract(path)


def test_load_migration_literals_uses_ast_without_importing_migration(
    tmp_path: Path,
) -> None:
    migration = tmp_path / "0007.py"
    _write_migration(migration)

    assert load_migration_literals(migration) == _contract()


def test_committed_0007_literals_bind_the_reviewed_authority_inputs() -> None:
    contract = load_frozen_contract(FROZEN_CONTRACT_PATH)
    literals = load_migration_authority_literals(MIGRATION_0007_PATH)

    assert isinstance(literals, MigrationAuthorityLiterals)
    assert literals.contract == contract
    assert literals.acl_repair_sql.encode("utf-8") == FROZEN_REPAIR_PATH.read_bytes()
    assert literals.pre_catalog_sha256 == REVIEWED_0006_CATALOG_SHA256
    assert literals.post_catalog_sha256 == REVIEWED_0007_CATALOG_SHA256
    assert literals.pre_catalog_sha256 != literals.post_catalog_sha256


def test_committed_authority_manifest_binds_final_0007_and_all_frozen_inputs() -> None:
    manifest = load_authority_manifest(AUTHORITY_MANIFEST_PATH)

    assert isinstance(manifest, AuthorityManifest)
    assert manifest.exact_head == "0007_job_event_chain_authority"
    assert manifest.catalog_query_id == "job-plane-catalog-v1"
    assert manifest.pre_catalog_sha256 == REVIEWED_0006_CATALOG_SHA256
    assert manifest.post_catalog_sha256 == REVIEWED_0007_CATALOG_SHA256
    assert tuple(name for name, _filename, _sha256 in manifest.frozen_inputs) == (
        "migration_0006",
        "migration_0007",
        "query_contract",
        "acl_repair_sql",
        "catalog_0006_snapshot",
        "catalog_0007_snapshot",
        "catalog_0006_manifest",
        "catalog_0007_manifest",
    )
    assert all(len(sha256) == 64 and set(sha256) != {"0"} for _, _, sha256 in manifest.frozen_inputs)


def test_committed_authority_manifest_matches_sources_without_importing_0007() -> None:
    manifest = verify_authority_manifest(
        AUTHORITY_MANIFEST_PATH,
        root=ROOT,
        contract_path=FROZEN_CONTRACT_PATH,
        migration_path=MIGRATION_0007_PATH,
        repair_path=FROZEN_REPAIR_PATH,
    )

    assert manifest.exact_head == "0007_job_event_chain_authority"


def test_committed_0007_has_only_the_frozen_repair_as_a_catalog_mutation() -> None:
    source = MIGRATION_0007_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MIGRATION_0007_PATH))
    execute_arguments = [
        call.args[0]
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "execute"
        and len(call.args) == 1
    ]

    assert sum(
        isinstance(argument, ast.Name)
        and argument.id == "ACL_REPAIR_SQL"
        for argument in execute_arguments
    ) == 1
    assert "LOCK TABLE public.jobs, public.job_attempts, public.job_events" in source
    assert "IN SHARE ROW EXCLUSIVE MODE" in source
    assert "current_user <> 'trading_owner'" in source
    assert "session_user <> 'trading_owner'" in source
    assert "0007 requires PostgreSQL 16" in source
    assert "0007 requires exact 0006 head" in source
    assert "pg_catalog.pg_stat_activity" in source
    assert "CREATE FUNCTION" not in source
    assert "DROP FUNCTION" not in source


def test_authority_manifest_rejects_placeholder_input_hash(tmp_path: Path) -> None:
    document = json.loads(AUTHORITY_MANIFEST_PATH.read_text(encoding="utf-8"))
    document["frozen_inputs"]["migration_0007"]["sha256"] = "0" * 64
    path = tmp_path / "authority-manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="non-placeholder lowercase SHA-256"):
        load_authority_manifest(path)


@pytest.mark.parametrize(
    ("digest_field", "wrong_snapshot", "label"),
    (
        ("pre_sha256", "catalog_0007_snapshot", "pre"),
        ("post_sha256", "catalog_0006_snapshot", "post"),
    ),
)
def test_authority_manifest_rejects_valid_shape_with_wrong_catalog_digest(
    tmp_path: Path,
    digest_field: str,
    wrong_snapshot: str,
    label: str,
) -> None:
    document = json.loads(AUTHORITY_MANIFEST_PATH.read_text(encoding="utf-8"))
    document["catalog"][digest_field] = document["frozen_inputs"][wrong_snapshot][
        "sha256"
    ]
    path = tmp_path / "authority-manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=rf"{label}-catalog digest does not bind"):
        load_authority_manifest(path)


def test_future_green_upgrade_gate_is_approval_guarded_and_statically_exact() -> None:
    tree = ast.parse(
        CATALOG_AUTHORITY_TEST_PATH.read_text(encoding="utf-8"),
        filename=str(CATALOG_AUTHORITY_TEST_PATH),
    )
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name in (
        "test_0007_green_gate_requires_two_independent_reviewed_catalog_captures",
        "test_0007_rejects_catalog_drift_without_changing_pre_0007_authority",
        "test_0007_rejects_event_chain_violation_without_changing_pre_0007_authority",
    ):
        first_statement = functions[name].body[0]
        assert isinstance(first_statement, ast.Expr)
        assert isinstance(first_statement.value, ast.Call)
        assert isinstance(first_statement.value.func, ast.Name)
        assert first_statement.value.func.id == "_require_green_upgrade_authority"

    clean_gate = functions[
        "test_0007_green_gate_requires_two_independent_reviewed_catalog_captures"
    ]
    clean_calls = [
        node
        for node in ast.walk(clean_gate)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_fresh_0007_capture"
    ]
    assert len(clean_calls) == 2

    helper_source = ast.get_source_segment(
        CATALOG_AUTHORITY_TEST_PATH.read_text(encoding="utf-8"),
        functions["_upgrade_exactly_through_0007"],
    )
    assert helper_source is not None
    assert "EXACT_0005_HEAD, EXACT_0006_HEAD, EXACT_0007_HEAD" in helper_source

    catalog_source = CATALOG_AUTHORITY_TEST_PATH.read_text(encoding="utf-8")
    assert "GREEN_FORWARD_UPGRADE_OPERATION_ID" in catalog_source
    assert "GREEN_REJECTION_OPERATION_ID" in catalog_source
    assert "with pytest.raises(RuntimeError) as captured:" in catalog_source
    assert "assert str(captured.value) == expected_error" in catalog_source
    assert "assert after == before" in catalog_source


def test_future_green_rejection_expectations_match_0007_runtime_guards() -> None:
    catalog_tree = ast.parse(
        CATALOG_AUTHORITY_TEST_PATH.read_text(encoding="utf-8"),
        filename=str(CATALOG_AUTHORITY_TEST_PATH),
    )
    catalog_functions = {
        node.name: node
        for node in catalog_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    catalog_constants = {
        node.targets[0].id: node.value.value
        for node in catalog_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    rejection_helper = catalog_functions["_assert_0007_rejection_is_atomic"]
    raises_calls = [
        node
        for node in ast.walk(rejection_helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
        and node.func.attr == "raises"
    ]
    assert len(raises_calls) == 1
    assert len(raises_calls[0].args) == 1
    assert isinstance(raises_calls[0].args[0], ast.Name)
    assert raises_calls[0].args[0].id == "RuntimeError"

    rejection_expectations = set()
    for name in (
        "test_0007_rejects_catalog_drift_without_changing_pre_0007_authority",
        "test_0007_rejects_event_chain_violation_without_changing_pre_0007_authority",
    ):
        calls = [
            node
            for node in ast.walk(catalog_functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_assert_0007_rejection_is_atomic"
        ]
        assert len(calls) == 1
        assert len(calls[0].args) == 2
        expected_error = calls[0].args[1]
        assert isinstance(expected_error, ast.Name)
        rejection_expectations.add(catalog_constants[expected_error.id])

    migration_source = MIGRATION_0007_PATH.read_text(encoding="utf-8")
    migration_tree = ast.parse(migration_source, filename=str(MIGRATION_0007_PATH))
    migration_functions = {
        node.name: node
        for node in migration_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    upgrade_calls = [
        statement.value
        for statement in migration_functions["upgrade"].body
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
    ]
    catalog_guard_index, catalog_guard = next(
        (index, call)
        for index, call in enumerate(upgrade_calls)
        if isinstance(call.func, ast.Name)
        and call.func.id == "_require_catalog_digest"
    )
    assert len(catalog_guard.args) == 3
    assert isinstance(catalog_guard.args[2], ast.Constant)
    assert catalog_guard.args[2].value == "preflight"
    event_guard_index, event_guard = next(
        (index, call)
        for index, call in enumerate(upgrade_calls)
        if isinstance(call.func, ast.Name)
        and call.func.id == "_require_zero_event_chain_violations"
    )
    assert event_guard.args == []
    assert catalog_guard_index < event_guard_index
    assert (
        'raise RuntimeError(f"0007 {stage} catalog digest does not match review")'
        in migration_source
    )
    assert (
        'raise RuntimeError("0007 event-chain authority violations are present")'
        in migration_source
    )
    assert rejection_expectations == {
        f"0007 {catalog_guard.args[2].value} catalog digest does not match review",
        "0007 event-chain authority violations are present",
    }


@pytest.mark.parametrize(
    "source,error",
    (
        (
            "CATALOG_QUERY_ID = 'a'\nCATALOG_SNAPSHOT_SQL = 'b' + 'c'\n"
            "EVENT_CHAIN_QUERY_ID = 'd'\n"
            "EVENT_CHAIN_VIOLATIONS_SQL = 'e'\n",
            "literal string assignment",
        ),
        (
            "CATALOG_QUERY_ID = 'a'\nCATALOG_SNAPSHOT_SQL = 'b'\n"
            "EVENT_CHAIN_QUERY_ID = 'd'\n",
            "missing migration literal",
        ),
        (
            "CATALOG_QUERY_ID = 'a'\nCATALOG_QUERY_ID = 'b'\n"
            "CATALOG_SNAPSHOT_SQL = 'c'\nEVENT_CHAIN_QUERY_ID = 'd'\n"
            "EVENT_CHAIN_VIOLATIONS_SQL = 'e'\n",
            "duplicate migration literal",
        ),
    ),
)
def test_load_migration_literals_rejects_dynamic_missing_and_duplicate_values(
    tmp_path: Path,
    source: str,
    error: str,
) -> None:
    migration = tmp_path / "0007.py"
    migration.write_text(source, encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        load_migration_literals(migration)


def test_capture_catalog_uses_utf8_c_sort_and_one_terminal_newline() -> None:
    lines = [
        '{"kind":"schema","name":"é"}',
        '{"kind":"database","name":"trading_agent"}',
        '{"kind":"schema","name":"z"}',
    ]
    connection = _Connection(
        [[("SCHEMA", None, lines[0]), ("DATABASE", None, lines[1]), ("SCHEMA", None, lines[2])]]
    )

    evidence = capture_catalog(connection, _contract())

    expected = b"\n".join(sorted(line.encode("utf-8") for line in lines)) + b"\n"
    assert evidence == CatalogEvidence(
        query_id=CATALOG_QUERY_ID,
        sha256=hashlib.sha256(expected).hexdigest(),
        row_count=3,
        canonical_bytes=expected,
    )
    assert connection.queries == [CATALOG_SQL]


def test_capture_catalog_empty_result_is_empty_bytes() -> None:
    evidence = capture_catalog(_Connection([[]]), _contract())

    assert evidence.canonical_bytes == b""
    assert evidence.row_count == 0
    assert evidence.sha256 == hashlib.sha256(b"").hexdigest()


def test_unknown_role_setting_reveals_only_key_and_fails_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        [[("UNSAFE_ROLE_SETTING", "application_name", None)]]
    )

    def forbidden_hash(_value: bytes = b"") -> object:
        raise AssertionError("unsafe setting reached hashing")

    monkeypatch.setattr(verifier.hashlib, "sha256", forbidden_hash)
    with pytest.raises(UnsafeCatalogSettingError) as captured:
        capture_catalog(connection, _contract())

    assert str(captured.value) == "unknown role setting key: application_name"
    assert "value" not in str(captured.value)


def test_unknown_function_setting_reveals_only_key_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        [[("UNSAFE_FUNCTION_SETTING", "application_name", None)]]
    )

    def forbidden_hash(_value: bytes = b"") -> object:
        raise AssertionError("unsafe function setting reached hashing")

    monkeypatch.setattr(verifier.hashlib, "sha256", forbidden_hash)
    with pytest.raises(UnsafeCatalogSettingError) as captured:
        capture_catalog(connection, _contract())

    assert str(captured.value) == "unknown function setting key: application_name"


@pytest.mark.parametrize(
    "rows",
    (
        [("ROLE_SETTING", None, None)],
        [("UNSAFE_ROLE_SETTING", "unsafe", "must-not-exist")],
        [("ROLE_SETTING", None, "line", "extra")],
        [("ROLE_SETTING", None, 3)],
    ),
)
def test_capture_catalog_rejects_malformed_query_rows(
    rows: list[tuple[object, ...]],
) -> None:
    with pytest.raises(ValueError, match="catalog query returned malformed rows"):
        capture_catalog(_Connection([rows]), _contract())


def test_find_event_chain_violations_returns_exact_immutable_rows() -> None:
    connection = _Connection(
        [[("SEQUENCE_GAP", "job-1", "event-3", 3), ("NO_HISTORY", "job-2", None, None)]]
    )

    assert find_event_chain_violations(connection, _contract()) == (
        Violation("SEQUENCE_GAP", "job-1", "event-3", 3),
        Violation("NO_HISTORY", "job-2", None, None),
    )
    assert connection.queries == [EVENT_CHAIN_SQL]


def test_verify_authority_rejects_migration_contract_drift_before_querying(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract.json"
    migration = tmp_path / "0007.py"
    _write_contract(contract)
    _write_migration(migration, catalog_sql="SELECT drift")
    connection = _Connection([])

    with pytest.raises(ValueError, match="migration literals do not match"):
        verify_authority(connection, contract, migration)

    assert connection.queries == []


def test_verify_authority_checks_exact_head_before_and_after_queries(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract.json"
    migration = tmp_path / "0007.py"
    _write_contract(contract)
    _write_migration(migration)
    head = "0007_job_event_chain_authority"
    connection = _Connection(
        [
            [(head,)],
            [("DATABASE", None, '{"kind":"database"}')],
            [],
            [(head,)],
        ]
    )

    evidence = verify_authority(connection, contract, migration)

    assert evidence.head == head
    assert evidence.catalog.row_count == 1
    assert evidence.violations == ()
    assert connection.queries == [
        verifier.HEAD_SQL,
        CATALOG_SQL,
        EVENT_CHAIN_SQL,
        verifier.HEAD_SQL,
    ]


@pytest.mark.parametrize(
    "before,after,error",
    (
        (
            "0006_job_transition_database_authority",
            "0006_job_transition_database_authority",
            "does not match migration revision",
        ),
        (
            "0007_job_event_chain_authority",
            "0006_job_transition_database_authority",
            "changed during verification",
        ),
    ),
)
def test_verify_authority_rejects_wrong_or_changing_head(
    tmp_path: Path,
    before: str,
    after: str,
    error: str,
) -> None:
    contract = tmp_path / "contract.json"
    migration = tmp_path / "0007.py"
    _write_contract(contract)
    _write_migration(migration)
    responses: list[list[tuple[object, ...]]] = [[(before,)]]
    if before == "0007_job_event_chain_authority":
        responses.extend([[], [], [(after,)]])
    connection = _Connection(responses)

    with pytest.raises(ValueError, match=error):
        verify_authority(connection, contract, migration)


@pytest.mark.parametrize("rows", ([], [("a",), ("b",)], [(7,)]))
def test_head_query_requires_one_nonempty_text_row(
    tmp_path: Path,
    rows: list[tuple[object, ...]],
) -> None:
    contract = tmp_path / "contract.json"
    migration = tmp_path / "0007.py"
    _write_contract(contract)
    _write_migration(migration)

    with pytest.raises(ValueError, match="Alembic head query returned malformed rows"):
        verify_authority(_Connection([rows]), contract, migration)


def test_cli_enforces_read_only_before_any_frozen_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    contract = tmp_path / "contract.json"
    migration = tmp_path / "0007.py"
    _write_contract(contract)
    _write_migration(migration)
    secret_conninfo = "host=secret.example password=NEVER_DISCLOSE"
    events: list[object] = []

    class Transaction:
        def __enter__(self) -> None:
            events.append("transaction-enter")

        def __exit__(self, *_args: object) -> None:
            events.append("transaction-exit")

    class Connection:
        def __enter__(self):
            events.append("connection-enter")
            return self

        def __exit__(self, *_args: object) -> None:
            events.append("connection-exit")

        def transaction(self) -> Transaction:
            events.append("transaction-created")
            return Transaction()

        def execute(self, query: str) -> None:
            events.append(("execute", query))

    connection = Connection()

    def connect(conninfo: str, *, options: str):
        assert conninfo == secret_conninfo
        events.append(("connect", options))
        return connection

    catalog = CatalogEvidence(
        query_id=CATALOG_QUERY_ID,
        sha256="0" * 64,
        row_count=0,
        canonical_bytes=b"",
    )

    def verify(
        received_connection: object,
        received_contract: Path,
        received_migration: Path,
    ) -> AuthorityEvidence:
        assert received_connection is connection
        assert received_contract == contract
        assert received_migration == migration
        events.append("verify")
        return AuthorityEvidence(
            head="0007_job_event_chain_authority",
            catalog=catalog,
            event_chain_query_id=EVENT_CHAIN_QUERY_ID,
            violations=(),
        )

    monkeypatch.setattr(verifier_cli.psycopg, "connect", connect)
    monkeypatch.setattr(verifier_cli, "verify_authority", verify)
    monkeypatch.setenv("TEST_AUTHORITY_CONNINFO", secret_conninfo)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_job_plane_authority.py",
            "--contract",
            os.fspath(contract),
            "--migration",
            os.fspath(migration),
            "--conninfo-env",
            "TEST_AUTHORITY_CONNINFO",
        ],
    )

    assert verifier_cli.main() == 0
    assert events == [
        ("connect", "-c default_transaction_read_only=on"),
        "connection-enter",
        "transaction-created",
        "transaction-enter",
        ("execute", "SET TRANSACTION READ ONLY"),
        "verify",
        "transaction-exit",
        "connection-exit",
    ]
    output = capsys.readouterr().out
    assert "0007_job_event_chain_authority" in output
    assert secret_conninfo not in output
