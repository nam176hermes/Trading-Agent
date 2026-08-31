from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0015_p1_accounting_closure_rotation.py"
ACCEPTED_MIGRATION = ROOT / "alembic/versions/0012_p1_engine_projection_authority.py"
PRE_0014_CLOSURE = "75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069"
OLD_CLOSURE = "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"
NEW_CLOSURE = "b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b"
PRIOR_PROSRC_SHA256 = "e6617353fe79c6e6ec0f6d1ecd824c4f28c2c52278dc1fbaf6e6d259426e2599"
ROTATED_PROSRC_SHA256 = "8972d3cf715cfd761e86d88446161c6c4a36e8b4fb61f76d02ed41bd227ee089"
PRIOR_DEFINITION_SHA256 = "81d1858d8e15a1422a893768d5402bb505847bf19a535be401c80398b47ef19d"
ROTATED_DEFINITION_SHA256 = "04ec80653561e0c40cd57d1920642dd6e1e878d0e11f4729cd4b97273e06dd5b"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p1_accounting_closure_0015", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prior_prosrc() -> str:
    source = ACCEPTED_MIGRATION.read_text(encoding="utf-8")
    delimiter = "$ingest_p1_engine_event_batch_v2$"
    body = source.split(delimiter, 1)[1].split(delimiter, 1)[0]
    return body.replace(PRE_0014_CLOSURE, OLD_CLOSURE)


def _postgresql_16_definition(prosrc: str) -> str:
    return (
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
        " LANGUAGE plpgsql\n SECURITY DEFINER\n"
        " SET search_path TO 'pg_catalog'\nAS $function$"
        + prosrc
        + "$function$\n"
    )


def test_accounting_closure_rotation_binds_exact_prior_and_result() -> None:
    prior = _prior_prosrc()
    rotated = prior.replace(OLD_CLOSURE, NEW_CLOSURE)
    assert prior.count(OLD_CLOSURE) == 2
    assert NEW_CLOSURE not in prior
    assert hashlib.sha256(prior.encode()).hexdigest() == PRIOR_PROSRC_SHA256
    assert hashlib.sha256(rotated.encode()).hexdigest() == ROTATED_PROSRC_SHA256
    assert hashlib.sha256(_postgresql_16_definition(prior).encode()).hexdigest() == (
        PRIOR_DEFINITION_SHA256
    )
    assert hashlib.sha256(_postgresql_16_definition(rotated).encode()).hexdigest() == (
        ROTATED_DEFINITION_SHA256
    )


def test_accounting_closure_rotation_is_exact_forward_only_driver_sql() -> None:
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
    assert migration.revision == "0015_p1_accounting_closure_rotation"
    assert migration.down_revision == "0014_p1_product_closure_rotation"
    assert statement.count(OLD_CLOSURE) == 1
    assert statement.count(NEW_CLOSURE) == 1
    for digest in (
        PRIOR_PROSRC_SHA256,
        ROTATED_PROSRC_SHA256,
        PRIOR_DEFINITION_SHA256,
        ROTATED_DEFINITION_SHA256,
    ):
        assert digest in statement
    assert "v_old_count <> 2" in statement
    assert "v_new_count <> 2" in statement
    assert statement.count("pg_catalog.set_config(") == 2
    assert statement.count("SELECT pg_catalog.count(*) = 1") == 2
    assert "pg_catalog.pg_get_functiondef" in statement
    assert "pg_catalog.aclexplode" in statement
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 accounting closure downgrade was accepted")
