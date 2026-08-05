from __future__ import annotations

from pathlib import Path


MIGRATION = Path("alembic/versions/0010_engine_event_ledger.py")


def test_engine_event_migration_is_forward_only_and_chained() -> None:
    source = MIGRATION.read_text()

    for expected in (
        'revision = "0010_engine_event_ledger"',
        'down_revision = "0009_canonical_market_data"',
        "CREATE TABLE public.engine_event_batch_receipts",
        "CREATE TABLE public.engine_events",
        "CREATE TABLE public.engine_run_projections",
        "FOREIGN KEY (batch_sha256)",
        "UNIQUE (engine_run_id, stream_sequence)",
        "engine_event_records_append_only",
        "BEFORE TRUNCATE ON public.engine_events",
        "RuntimeError",
    ):
        assert expected in source


def test_engine_event_write_authority_is_one_protected_atomic_function() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "CREATE FUNCTION public.ingest_engine_event_batch", 1
    )[1].split("$ingest_engine_event_batch$;", 1)[0]

    for expected in (
        "SECURITY DEFINER",
        "SET search_path = pg_catalog",
        "pg_advisory_xact_lock",
        "INSERT INTO public.engine_event_batch_receipts",
        "INSERT INTO public.engine_events",
        "INSERT INTO public.engine_run_projections",
        "ON CONFLICT (engine_run_id) DO UPDATE",
        "sum(grouped.type_count)",
        "P2D01",
        "P2D02",
        "P2D03",
        "P2D04",
        "engine_run_id=%s;expected=%s;actual=%s",
    ):
        assert expected in body
    assert body.index("INSERT INTO public.engine_event_batch_receipts") < body.index(
        "INSERT INTO public.engine_events"
    ) < body.index("INSERT INTO public.engine_run_projections")


def test_database_rederives_canonical_event_and_batch_seals_before_mutation() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_engine_event_batch$", 1
    )[1].split("$ingest_engine_event_batch$;", 1)[0]
    validation = body.split("pg_advisory_xact_lock", 1)[0]

    for expected in (
        "octet_length(p_batch_document) > 67108864",
        "canonical_domain_json_string(p_batch_document)",
        "canonical_domain_json_string(v_canonical_json_text)",
        "public.digest(convert_to(v_canonical_json_text, 'UTF8'), 'sha256')",
        "string_agg(item ->> 'canonical_json' || chr(10)",
        "v_computed_batch_sha256 IS DISTINCT FROM v_batch_sha256",
        "v_envelope ->> 'message_id'",
        "v_envelope ->> 'engine_run_id'",
        "v_envelope #>> '{payload,event_type}'",
        "v_envelope #>> '{payload,family}'",
        "jsonb_array_length(v_document -> 'events') <> v_event_count",
        "engine-event sequence is blocked inside batch",
    ):
        assert expected in validation
    assert validation.index("octet_length(p_batch_document)") < validation.index(
        "p_batch_document::jsonb"
    )
    assert "INSERT INTO public.engine_events" not in validation


def test_exact_retry_and_conflict_are_checked_before_sequence_or_any_insert() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_engine_event_batch$", 1
    )[1].split("$ingest_engine_event_batch$;", 1)[0]
    retry = body.split("INSERT INTO public.engine_event_batch_receipts", 1)[0]

    assert "FROM public.engine_event_batch_receipts AS receipt" in retry
    assert "RETURN QUERY SELECT" in retry
    assert "conflicting engine-event batch receipt" in retry
    assert "conflicting engine-event message identity" in retry
    assert "v_first_sequence <> v_expected_sequence" in retry
    assert retry.index("conflicting engine-event batch receipt") < retry.index(
        "v_first_sequence <> v_expected_sequence"
    )


def test_cross_run_message_identity_is_serialized_and_always_typed_conflict() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_engine_event_batch$", 1
    )[1].split("$ingest_engine_event_batch$;", 1)[0]
    lock = body.split("Globally identical message IDs", 1)[1].split(
        "SELECT stored.digest INTO v_existing_digest", 1
    )[0]
    insert = body.split("INSERT INTO public.engine_events", 1)[1].split(
        "END LOOP;", 1
    )[0]

    assert "ORDER BY (item ->> 'message_id')::uuid" in lock
    assert "hashtextextended(v_message_id::text, 23)" in lock
    assert "v_engine_run_id" not in lock
    assert body.index("hashtextextended(v_message_id::text, 23)") < body.index(
        "SELECT stored.digest INTO v_existing_digest"
    )
    assert "EXCEPTION WHEN unique_violation THEN" in insert
    assert "conflicting engine-event uniqueness authority" in insert
    assert "USING ERRCODE = 'P2D01'" in insert


def test_projection_recovery_replays_only_append_only_event_rows() -> None:
    source = MIGRATION.read_text()
    recovery = source.split(
        "CREATE FUNCTION public.recover_engine_run_projections", 1
    )[1].split("$recover_engine_run_projections$;", 1)[0]

    for expected in (
        "LOCK TABLE public.engine_events IN SHARE MODE",
        "FROM public.engine_events AS stored",
        "row_number() OVER",
        "durable engine-event sequence is not recoverable",
        "ON CONFLICT (engine_run_id) DO UPDATE",
        "ORDER BY counts.event_type COLLATE \"C\"",
        "ORDER BY projection.engine_run_id",
    ):
        assert expected in recovery
    assert recovery.index("LOCK TABLE public.engine_events IN SHARE MODE") < recovery.index(
        "IF EXISTS ("
    ) < recovery.index("INSERT INTO public.engine_run_projections")


def test_recovery_and_ingest_use_incompatible_relation_lock_modes() -> None:
    source = MIGRATION.read_text()
    ingestion = source.split(
        "AS $ingest_engine_event_batch$", 1
    )[1].split("$ingest_engine_event_batch$;", 1)[0]
    recovery = source.split(
        "AS $recover_engine_run_projections$", 1
    )[1].split("$recover_engine_run_projections$;", 1)[0]

    # PostgreSQL INSERT takes ROW EXCLUSIVE on this relation; explicit SHARE
    # conflicts with it. This source pairing makes either operation observe the
    # other transaction wholly before or wholly after projection replay.
    assert "INSERT INTO public.engine_events" in ingestion
    assert "LOCK TABLE public.engine_events IN SHARE MODE" in recovery
    assert "sees all or none of every ingest transaction" in recovery


def test_engine_event_relations_and_functions_have_no_public_authority() -> None:
    source = MIGRATION.read_text()

    for relation in (
        "engine_event_batch_receipts",
        "engine_events",
        "engine_run_projections",
    ):
        assert f"REVOKE ALL PRIVILEGES ON TABLE public.{relation} FROM PUBLIC" in source
    assert (
        "REVOKE ALL PRIVILEGES ON FUNCTION "
        "public.ingest_engine_event_batch(text) FROM PUBLIC"
    ) in source
    assert "Runtime role grants are intentionally absent" in source
    assert "GRANT " not in source


def test_source_contract_is_explicitly_not_database_runtime_proof() -> None:
    source = MIGRATION.read_text()
    test_source = Path(__file__).read_text()

    assert "Source-contract proof does not prove PostgreSQL runtime behavior" in source
    assert "Runtime proof requires a separately approved disposable PostgreSQL fixture" in source
    assert ("import " + "psycopg") not in test_source
    assert ("disposable" + "_database") not in test_source
