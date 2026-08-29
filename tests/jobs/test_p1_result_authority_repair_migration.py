from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0016_p1_result_authority_repair.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p1_result_authority_0016", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p1_result_authority_repair_is_exact_forward_only_driver_sql() -> None:
    migration = _load_migration()
    observed: dict[str, object] = {}

    class Bind:
        def exec_driver_sql(
            self, statement: str, *, execution_options: dict[str, object]
        ) -> None:
            observed.update(statement=statement, execution_options=execution_options)

    migration.op = SimpleNamespace(get_bind=lambda: Bind())
    migration.upgrade()
    statement = observed["statement"]
    assert isinstance(statement, str)
    assert migration.revision == "0016_p1_result_authority_repair"
    assert migration.down_revision == "0015_p1_accounting_closure_rotation"
    assert statement.count("88889999aaaabbbb") == 1
    assert statement.count("89ab89ab89ab89ab") == 1
    assert "6e8cae1e8f9f120fbf79fc0a9eb444ce0c1163b708e35ec71ec813561c20f445" in statement
    assert "69704d5c3ac1516339095865238eb650da4b2bda0f95a3b5a675b50f00a389b5" in statement
    assert "aea1129235f91d7645741c04912590a31cc3e667df43867c8b3a7cecfec9b743" in statement
    assert "6d76e0cadddd6f204cb445f38e7bc7462ac92342be3cca5fa639071bf182db2a" in statement
    assert "4f9c03425a69edf9844a1ae9188660ac7ea4285e5a1ecd87e8e6ecc31be6ec78" in statement
    assert "42daedaeeb38b9d9f18f8c030ea5d28e3b38c25a5ec592d94b18d6be697b0c3c" in statement
    assert "job_plane.paper_worker_job_allowed(" in statement
    assert "DROP POLICY job_plane_worker_artifacts_insert" in statement
    assert statement.count("pg_catalog.set_config(") == 2
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 result-authority repair downgrade was accepted")
