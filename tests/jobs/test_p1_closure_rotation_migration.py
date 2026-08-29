from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0014_p1_product_closure_rotation.py"
ACCEPTED_MIGRATION = ROOT / "alembic/versions/0012_p1_engine_projection_authority.py"
OLD_CLOSURE = "75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069"
NEW_CLOSURE = "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"
PRIOR_PROSRC_SHA256 = "2550c8513692664f383abc828f0245cc3f7554b20d6f58e0b125714626fc6cae"
ROTATED_PROSRC_SHA256 = "e6617353fe79c6e6ec0f6d1ecd824c4f28c2c52278dc1fbaf6e6d259426e2599"
PRIOR_DEFINITION_SHA256 = "ba6af6b6b9b771b94a80f72303521ab56c99a622b77c29df8df9105de5ec42a4"
ROTATED_DEFINITION_SHA256 = "81d1858d8e15a1422a893768d5402bb505847bf19a535be401c80398b47ef19d"


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


def _postgresql_16_definition(prosrc: str) -> str:
    header = (
        "CREATE OR REPLACE FUNCTION job_plane.ingest_p1_engine_event_batch_v2("
        "p_batch_document text, p_expected_request_message_id uuid, "
        "p_expected_correlation_id uuid, p_expected_causation_id uuid, "
        "p_expected_engine_run_id uuid, p_expected_config_digest text, "
        "p_expected_catalog_digest text, p_expected_data_digest text, "
        "p_expected_producer_identity text, p_expected_source_commit text)\n"
        " RETURNS TABLE(batch_sha256 character, ingestion_digest character, "
        "job_id character varying, attempt_id character varying, "
        "engine_run_id uuid, event_count bigint, first_sequence bigint, "
        "last_sequence bigint, last_digest character)\n"
        " LANGUAGE plpgsql\n"
        " SECURITY DEFINER\n"
        " SET search_path TO 'pg_catalog'\n"
        "AS $function$"
    )
    return header + prosrc + "$function$\n"


def test_p1_closure_rotation_binds_the_exact_accepted_function_body() -> None:
    prior = _accepted_prosrc()
    assert prior.count(OLD_CLOSURE) == 2
    assert NEW_CLOSURE not in prior
    assert hashlib.sha256(prior.encode()).hexdigest() == PRIOR_PROSRC_SHA256
    rotated = prior.replace(OLD_CLOSURE, NEW_CLOSURE)
    assert hashlib.sha256(rotated.encode()).hexdigest() == ROTATED_PROSRC_SHA256
    prior_definition = _postgresql_16_definition(prior)
    rotated_definition = _postgresql_16_definition(rotated)
    assert hashlib.sha256(prior_definition.encode()).hexdigest() == (
        PRIOR_DEFINITION_SHA256
    )
    assert hashlib.sha256(rotated_definition.encode()).hexdigest() == (
        ROTATED_DEFINITION_SHA256
    )

    body_drift = prior.replace("v_document jsonb;", "v_document jsonb; -- drift", 1)
    assert body_drift.count(OLD_CLOSURE) == 2
    assert hashlib.sha256(body_drift.encode()).hexdigest() != PRIOR_PROSRC_SHA256
    for header_drift in (
        prior_definition.replace("batch_sha256 character", "batch_digest character"),
        prior_definition.replace("last_digest character)", "last_digest text)"),
        prior_definition.replace(
            "p_expected_source_commit text)",
            "p_drifted_source_commit text)",
        ),
    ):
        assert prior in header_drift
        assert hashlib.sha256(header_drift.encode()).hexdigest() != (
            PRIOR_DEFINITION_SHA256
        )
    rotated_header_drift = rotated_definition.replace(
        "last_digest character)", "last_digest text)"
    )
    assert rotated in rotated_header_drift
    assert hashlib.sha256(rotated_header_drift.encode()).hexdigest() != (
        ROTATED_DEFINITION_SHA256
    )


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
    assert PRIOR_DEFINITION_SHA256 in statement
    assert ROTATED_DEFINITION_SHA256 in statement
    assert "pg_get_userbyid(function_row.proowner)" in statement
    assert "'trading_owner'" in statement
    assert len(re.findall(
        r"pg_catalog\.pg_get_userbyid\(function_row\.proowner\)\s*=\s*"
        r"'trading_owner'",
        statement,
    )) == 2
    assert "'trading_migrator'" not in statement
    assert "aclexplode" in statement and "acldefault" in statement
    assert "acl.grantee = function_row.proowner" in statement
    assert statement.count("SELECT pg_catalog.count(*) = 1") == 2
    assert "acl.is_grantable" in statement
    assert "prosecdef" in statement and "provolatile" in statement
    assert "proparallel" in statement and "proconfig" in statement
    assert "FROM pg_catalog.pg_proc" in statement
    assert "pg_catalog.set_config(" in statement
    assert "pg_catalog.current_setting(" in statement
    assert "pg_catalog.coalesce(" not in statement
    assert statement.count("COALESCE(") == 2
    assert "v_function pg_catalog.regprocedure;" in statement
    assert "v_function pg_catalog.regprocedure :=" not in statement
    exact_lookup = (
        "job_plane.ingest_p1_engine_event_batch_v2("
        "pg_catalog.text,pg_catalog.uuid,pg_catalog.uuid,pg_catalog.uuid,"
        "pg_catalog.uuid,pg_catalog.text,pg_catalog.text,pg_catalog.text,"
        "pg_catalog.text,pg_catalog.text)"
    )
    assert exact_lookup in statement
    assert statement.index("pg_catalog.current_setting(") < statement.index(
        "v_function :="
    )
    for builtin in (
        "acldefault",
        "aclexplode",
        "bool_and",
        "convert_to",
        "count",
        "encode",
        "length",
        "pg_get_functiondef",
        "pg_get_userbyid",
        "replace",
        "sha256",
        "set_config",
        "current_setting",
    ):
        assert re.search(
            rf"(?<!pg_catalog\.)\b{builtin}\s*\(", statement
        ) is None
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 closure rotation downgrade was accepted")
