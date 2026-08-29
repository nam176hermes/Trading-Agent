from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0014_p1_product_closure_rotation.py"
ACCEPTED_MIGRATION = ROOT / "alembic/versions/0012_p1_engine_projection_authority.py"
OLD_CLOSURE = "75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069"
NEW_CLOSURE = "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"
PRIOR_PROSRC_SHA256 = "2550c8513692664f383abc828f0245cc3f7554b20d6f58e0b125714626fc6cae"
ROTATED_PROSRC_SHA256 = "e6617353fe79c6e6ec0f6d1ecd824c4f28c2c52278dc1fbaf6e6d259426e2599"


def _load_migration() -> ModuleType:
    assert MIGRATION.is_file()
    spec = importlib.util.spec_from_file_location("p1_closure_rotation_0014", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _accepted_prosrc() -> str:
    source = ACCEPTED_MIGRATION.read_text(encoding="utf-8")
    delimiter = "$ingest_p1_engine_event_batch_v2$"
    assert source.count(delimiter) == 2
    return source.split(delimiter, 1)[1].split(delimiter, 1)[0]


def test_p1_closure_rotation_binds_the_exact_accepted_function_body() -> None:
    prior = _accepted_prosrc()
    assert prior.count(OLD_CLOSURE) == 2
    assert NEW_CLOSURE not in prior
    assert hashlib.sha256(prior.encode()).hexdigest() == PRIOR_PROSRC_SHA256
    rotated = prior.replace(OLD_CLOSURE, NEW_CLOSURE)
    assert hashlib.sha256(rotated.encode()).hexdigest() == ROTATED_PROSRC_SHA256

    body_drift = prior.replace("v_document jsonb;", "v_document jsonb; -- drift", 1)
    assert body_drift.count(OLD_CLOSURE) == 2
    assert hashlib.sha256(body_drift.encode()).hexdigest() != PRIOR_PROSRC_SHA256


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
    assert PRIOR_PROSRC_SHA256 in statement
    assert ROTATED_PROSRC_SHA256 in statement
    assert "pg_get_userbyid(function_row.proowner)" in statement
    assert "'trading_owner'" in statement
    assert statement.count("= 'trading_owner'") == 2
    assert "aclexplode" in statement and "acldefault" in statement
    assert "acl.grantee = function_row.proowner" in statement
    assert statement.count("SELECT count(*) = 1") == 2
    assert "acl.is_grantable" in statement
    assert "prosecdef" in statement and "provolatile" in statement
    assert "proparallel" in statement and "proconfig" in statement
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 closure rotation downgrade was accepted")
