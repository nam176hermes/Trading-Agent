from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0017_p1_request_digest_authority.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p1_request_digest_0017", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p1_request_digest_rotation_is_exact_forward_only_driver_sql() -> None:
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
    assert migration.revision == "0017_p1_request_digest_authority"
    assert migration.down_revision == "0016_p1_result_authority_repair"
    assert "engine_request_sha256" in statement
    assert "v_metadata - ''engine_request_sha256''" in statement
    assert "v_total <> 24 OR v_valid <> 24" in statement
    assert "!~ ''^[0-9a-f]{64}$''" in statement
    assert statement.count("pg_catalog.set_config(") == 2
    assert statement.count("pg_catalog.aclexplode") == 2
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 request-digest authority downgrade was accepted")
