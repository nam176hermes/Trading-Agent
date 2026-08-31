from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0018_p1_paper_closure_rotation.py"
ACCEPTED_MIGRATION = ROOT / "alembic/versions/0012_p1_engine_projection_authority.py"
OLD_CLOSURE = "b3bbb22552b896612ef93f78a61087d95fb1c061afb6102753e9f4d614b3963b"
NEW_CLOSURE = "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
PRIOR_SOURCE_SHA256 = "f914250fd1baca39063ed355d8a663e7aeb58c58367f699e1a92fb4602a7419f"
ROTATED_SOURCE_SHA256 = "a4b63e0cf7f431c3bd9132e0fef4ec2f79a455761c69e570bb64fc0714ccd88c"
PRIOR_DEFINITION_SHA256 = "19bd7f67d5344ba4b2ee4473488fafe7c063db33d6518ebf6ea68aa6e8edf023"
ROTATED_DEFINITION_SHA256 = "b0e45b32f4e864e6a841e88047686f55b436ae58b7ce393d777c3b34643ee784"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p1_paper_closure_0018", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_prosrc() -> str:
    source = ACCEPTED_MIGRATION.read_text(encoding="utf-8")
    delimiter = "$ingest_p1_engine_event_batch_v2$"
    body = source.split(delimiter, 1)[1].split(delimiter, 1)[0]
    body = body.replace(
        "75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069",
        OLD_CLOSURE,
    ).replace("88889999aaaabbbb", "89ab89ab89ab89ab")
    body = body.replace(
        """          v_legacy_document := public.canonical_domain_json_string(
            (v_document - 'validation_metadata' - 'validator_id')::text
          );""",
        """          v_legacy_document := public.canonical_domain_json(
            v_document - 'validation_metadata' - 'validator_id'
          );""",
    )
    replacements = (
        (
            "              'engine_upstream_commit', 'engine_version', 'event_count',",
            "              'engine_request_sha256', 'engine_upstream_commit',\n"
            "              'engine_version', 'event_count',",
        ),
        ("v_total <> 23 OR v_valid <> 23", "v_total <> 24 OR v_valid <> 24"),
        (
            "                 'engine_upstream_commit', 'engine_version', 'fees',",
            "                 'engine_request_sha256', 'engine_upstream_commit',\n"
            "                 'engine_version', 'fees',",
        ),
        (
            "             OR v_metadata ->> 'p1_product_closure_sha256' IS DISTINCT FROM\n"
            f"                  '{OLD_CLOSURE}'",
            "             OR v_metadata ->> 'p1_product_closure_sha256' IS DISTINCT FROM\n"
            f"                  '{OLD_CLOSURE}'\n"
            "             OR v_metadata ->> 'engine_request_sha256'\n"
            "                  !~ '^[0-9a-f]{64}$'",
        ),
        (
            "'validation_metadata', v_metadata,",
            "'validation_metadata', v_metadata - 'engine_request_sha256',",
        ),
    )
    for old, new in replacements:
        assert body.count(old) == 1 and new not in body
        body = body.replace(old, new)
    return body


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


def test_paper_closure_rotation_binds_exact_prior_and_result() -> None:
    prior = _current_prosrc()
    rotated = prior.replace(OLD_CLOSURE, NEW_CLOSURE)
    assert prior.count(OLD_CLOSURE) == 2 and NEW_CLOSURE not in prior
    assert hashlib.sha256(prior.encode()).hexdigest() == PRIOR_SOURCE_SHA256
    assert hashlib.sha256(rotated.encode()).hexdigest() == ROTATED_SOURCE_SHA256
    assert hashlib.sha256(_postgresql_16_definition(prior).encode()).hexdigest() == PRIOR_DEFINITION_SHA256
    assert hashlib.sha256(_postgresql_16_definition(rotated).encode()).hexdigest() == ROTATED_DEFINITION_SHA256


def test_paper_closure_rotation_is_exact_forward_only_driver_sql() -> None:
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
    assert migration.revision == "0018_p1_paper_closure_rotation"
    assert migration.down_revision == "0017_p1_request_digest_authority"
    assert statement.count(OLD_CLOSURE) == 1
    assert statement.count(NEW_CLOSURE) == 1
    for digest in (
        PRIOR_SOURCE_SHA256,
        ROTATED_SOURCE_SHA256,
        PRIOR_DEFINITION_SHA256,
        ROTATED_DEFINITION_SHA256,
    ):
        assert digest in statement
    assert "v_old_count <> 2" in statement
    assert "v_new_count <> 2" in statement
    assert statement.count("pg_catalog.set_config(") == 2
    assert statement.count("pg_catalog.aclexplode") == 2
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 paper closure downgrade was accepted")
