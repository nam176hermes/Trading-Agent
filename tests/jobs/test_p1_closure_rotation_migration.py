from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0014_p1_product_closure_rotation.py"
OLD_CLOSURE = "75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069"
NEW_CLOSURE = "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"


def _load_migration() -> ModuleType:
    assert MIGRATION.is_file()
    spec = importlib.util.spec_from_file_location("p1_closure_rotation_0014", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p1_closure_rotation_is_exact_forward_only_driver_sql() -> None:
    migration = _load_migration()
    observed: dict[str, object] = {}

    class Bind:
        def exec_driver_sql(
            self, statement: str, *, execution_options: dict[str, object]
        ) -> None:
            observed.update(
                statement=statement, execution_options=execution_options
            )

    migration.op = SimpleNamespace(get_bind=lambda: Bind())
    migration.upgrade()
    statement = observed["statement"]
    assert isinstance(statement, str)
    assert migration.revision == "0014_p1_product_closure_rotation"
    assert migration.down_revision == "0013_engine_backtest_enqueue_authority"
    assert statement.count(OLD_CLOSURE) == 1
    assert statement.count(NEW_CLOSURE) == 1
    assert "pg_get_functiondef" in statement
    assert "v_old_count <> 2" in statement
    assert "v_new_count <> 2" in statement
    assert "proowner" in statement and "proacl" in statement
    assert "prosecdef" in statement and "provolatile" in statement
    assert "proparallel" in statement and "proconfig" in statement
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 closure rotation downgrade was accepted")
