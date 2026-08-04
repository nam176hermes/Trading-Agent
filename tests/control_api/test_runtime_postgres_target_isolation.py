from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from control_api.repositories.capabilities import LegacyCapabilityRepository
from control_api.repositories.costs import LegacyCostRepository
from control_api.repositories.decisions import LegacyDecisionRepository
from scripts import run_required_runtime_pytest
from tests.control_api._disposable_runtime import (
    _write_legacy_fixture,
    database_env,
)
from tests.control_api._postgres_catalog import (
    CatalogSnapshot,
    _canonicalize_constraint_definition,
)
from tests.control_api.test_foundation_postgres_runtime_parity import (
    _write_sanitized_catalog_evidence,
)
from trading_control.db import DatabaseSettings


ROOT = Path(__file__).parents[2]
TARGETS = {
    "tests/control_api/test_postgres_api.py": {
        "control-api-postgres-api-green-v1"
    },
    "tests/control_api/test_postgres_repositories.py": {
        "control-api-postgres-repositories-green-v1"
    },
    "tests/control_api/test_alembic_schema.py": {
        "control-api-alembic-head-cycle-v1",
        "control-api-application-role-permissions-v1",
        "control-api-alembic-0007-to-0008-v1",
        "control-api-alembic-0008-to-0009-v1",
    },
    "tests/control_api/test_foundation_postgres_runtime_parity.py": {
        "foundation-postgres-restore-green-v1"
    },
    "tests/control_api/test_dual_read.py": {"control-api-dual-read-green-v1"},
    "tests/event_ledger/test_snapshot_postgres_runtime.py": {
        "event-ledger-durability-runtime-green-v1"
    },
    "tests/market_data/test_postgres_runtime.py": {
        "market-data-canonical-persistence-runtime-green-v1",
        "market-data-empty-head-runtime-green-v1",
    },
}
FORBIDDEN_SOURCE = (
    "postgres-reader.env",
    "/home/thenam176/.hermes/crypto-research",
    '"55431"',
    '"55432"',
)


def _operation_ids(source: str, relative_path: str) -> set[str]:
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
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {
            "disposable_database",
            "disposable_restore_workflow",
        }:
            continue
        keyword = next(
            (item.value for item in node.keywords if item.arg == "operation_id"),
            None,
        )
        if isinstance(keyword, ast.Constant) and isinstance(keyword.value, str):
            result.add(keyword.value)
        elif isinstance(keyword, ast.Name) and keyword.id in constants:
            result.add(constants[keyword.id])
    return result


def _all_runtime_calls_are_planned(source: str, relative_path: str) -> bool:
    tree = ast.parse(source, filename=relative_path)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id
        in {"disposable_database", "disposable_restore_workflow"}
    ]
    return bool(calls) and all(
        any(
            keyword.arg == "planned"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in call.keywords
        )
        for call in calls
    )


def test_required_runtime_postgres_targets_are_disposable_only() -> None:
    for relative_path, expected_operation_ids in TARGETS.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert all(fragment not in source for fragment in FORBIDDEN_SOURCE)
        assert _operation_ids(source, relative_path) == expected_operation_ids
        assert _all_runtime_calls_are_planned(source, relative_path)


def test_make_targets_select_only_reviewed_disposable_modules() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert (
        "test-runtime-postgres:\n"
        "\tuv run python scripts/run_required_runtime_pytest.py \\\n"
        "\t\ttests/control_api/test_postgres_api.py \\\n"
        "\t\ttests/control_api/test_postgres_repositories.py \\\n"
        "\t\ttests/control_api/test_alembic_schema.py \\\n"
        "\t\ttests/control_api/test_foundation_postgres_runtime_parity.py"
    ) in makefile
    assert (
        "test-event-ledger-runtime-postgres:\n"
        "\tuv run python scripts/run_required_runtime_pytest.py \\\n"
        "\t\ttests/event_ledger/test_snapshot_postgres_runtime.py"
    ) in makefile
    assert (
        "test-market-data-runtime-postgres:\n"
        "\tuv run python scripts/run_required_runtime_pytest.py \\\n"
        "\t\ttests/market_data/test_postgres_runtime.py"
    ) in makefile
    assert (
        "test-runtime-dual-read:\n"
        "\tuv run python scripts/run_required_runtime_pytest.py \\\n"
        "\t\ttests/control_api/test_dual_read.py"
    ) in makefile


def test_required_runtime_runner_rejects_selected_skips(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = (ROOT / "scripts/run_required_runtime_pytest.py").read_text(
        encoding="utf-8"
    )
    assert "if result == 0 and plugin.skipped:" in source
    assert "return 1" in source

    def skipped_main(_arguments, *, plugins):
        plugins[0].pytest_runtest_logreport(
            SimpleNamespace(skipped=True, nodeid="synthetic::required")
        )
        return 0

    monkeypatch.setattr(run_required_runtime_pytest.pytest, "main", skipped_main)
    assert run_required_runtime_pytest.main(["synthetic.py"]) == 1
    assert capsys.readouterr().err == "required runtime tests skipped: 1\n"


def test_disposable_repository_fixture_is_synthetic_and_paper_only(
    tmp_path: Path,
) -> None:
    _write_legacy_fixture(tmp_path)
    assert LegacyDecisionRepository(tmp_path).list(page=1, page_size=10).total == 3
    assert len(LegacyCapabilityRepository(tmp_path).list()) == 9
    assert LegacyCostRepository(tmp_path).get().total_sessions == 1

    fixed_value = "fixed-test-only-password"
    env = database_env(
        DatabaseSettings(
            host="127.0.0.1",
            port=49152,
            database="trading_agent_disposable_test",
            user="trading_reader",
            password=fixed_value,
        ),
        tmp_path,
    )
    assert env["TRADING_DATA_ROOT"] == str(tmp_path)
    assert env["TRADING_DATABASE_NAME"].endswith("_disposable_test")
    assert env["TRADING_MODE"] == "paper"
    assert env["LIVE_EXECUTION_ENABLED"] == "false"
    assert env["LIVE_TRADING_APPROVED"] == "false"
    assert env["LIVE_TRADING_ENABLED"] == "false"


def test_catalog_restore_evidence_is_sanitized_private_and_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = CatalogSnapshot(
        semantic_digests={"owner": "a" * 64},
        semantic_row_counts={"owner": 1},
        semantic_subgroup_digests={"owner": {"extension_owner": "c" * 64}},
        semantic_subgroup_row_counts={"owner": {"extension_owner": 1}},
        semantic_rows={"owner": (("extension_owner", "pgcrypto", "trading_owner"),)},
        raw_acl_sha256="b" * 64,
        raw_acl_row_count=1,
        table_row_counts={"alembic_version": 1},
    )
    restored = CatalogSnapshot(
        semantic_digests={"owner": "d" * 64},
        semantic_row_counts={"owner": 1},
        semantic_subgroup_digests={"owner": {"extension_owner": "e" * 64}},
        semantic_subgroup_row_counts={"owner": {"extension_owner": 1}},
        semantic_rows={"owner": (("extension_owner", "pgcrypto", "trading_migrator"),)},
        raw_acl_sha256="b" * 64,
        raw_acl_row_count=1,
        table_row_counts={"alembic_version": 1},
    )
    with tempfile.TemporaryDirectory(
        prefix="foundation-postgres-evidence-",
        dir="/tmp",
    ) as raw:
        monkeypatch.setenv("TRADING_TEST_POSTGRES_EVIDENCE_DIR", raw)
        path = _write_sanitized_catalog_evidence(source, restored)
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["semantic_groups_equal"] is False
        assert document["row_counts_equal"] is True
        assert document["differing_semantic_subgroups"] == ["owner.extension_owner"]
        assert document["semantic_row_differences"] == {
            "owner": {
                "restored_only": [
                    ["extension_owner", "pgcrypto", "trading_migrator"]
                ],
                "source_only": [["extension_owner", "pgcrypto", "trading_owner"]],
            }
        }
        assert "password" not in path.read_text(encoding="utf-8").lower()
        assert path.stat().st_mode & 0o077 == 0


def test_default_acl_restore_authority_is_effective_not_raw_row_structural() -> None:
    source_path = ROOT / "tests/control_api/_postgres_catalog.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    effective_source = ast.get_source_segment(
        source,
        functions["_effective_acl_rows"],
    )
    structural_source = ast.get_source_segment(
        source,
        functions["_structural_rows"],
    )

    assert effective_source is not None
    assert structural_source is not None
    assert '"default_acl"' in effective_source
    assert "pg_catalog.pg_default_acl" in effective_source
    assert "d.defaclobjtype" in effective_source
    assert "x.privilege_type" in effective_source
    assert "x.is_grantable" in effective_source
    assert "pg_catalog.pg_default_acl" not in structural_source

    parity_path = (
        ROOT / "tests/control_api/test_foundation_postgres_runtime_parity.py"
    )
    parity_source = parity_path.read_text(encoding="utf-8")
    parity_tree = ast.parse(parity_source, filename=str(parity_path))
    parity_functions = {
        node.name: node
        for node in parity_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    restore_source = ast.get_source_segment(
        parity_source,
        parity_functions[
            "test_custom_dump_restore_preserves_0008_catalog_acl_and_event_authority"
        ],
    )
    assert restore_source is not None
    assert (
        "source_catalog.semantic_digests == restored_catalog.semantic_digests"
        in restore_source
    )
    assert "raw_acl_row_count ==" not in restore_source


def test_constraint_definition_normalization_is_narrow_and_value_preserving() -> None:
    migrated = (
        "CHECK (status::text = ANY (ARRAY['ACTIVE'::character varying, "
        "'DISABLED'::character varying]::text[]))"
    )
    restored = (
        "CHECK (status::text = ANY (ARRAY['ACTIVE'::character varying::text, "
        "'DISABLED'::character varying::text]))"
    )
    changed = restored.replace("'DISABLED'", "'UNKNOWN'")

    assert _canonicalize_constraint_definition(migrated) == (
        _canonicalize_constraint_definition(restored)
    )
    assert _canonicalize_constraint_definition(migrated) != (
        _canonicalize_constraint_definition(changed)
    )
