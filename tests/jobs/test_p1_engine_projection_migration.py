from __future__ import annotations

from pathlib import Path


MIGRATION = Path("alembic/versions/0012_p1_engine_projection_authority.py")


def test_p1_projection_migration_is_minimal_forward_authority() -> None:
    source = MIGRATION.read_text()

    for expected in (
        'revision = "0012_p1_engine_projection_authority"',
        'down_revision = "0011_engine_backtest_worker_authority"',
        "ALTER TABLE public.engine_run_projections",
        "ADD COLUMN batch_sha256 char(64)",
        "ADD COLUMN semantic_digest char(64)",
        "engine_run_projection_result_authority_complete",
        "batch_sha256 IS NOT NULL",
        "semantic_digest IS NOT NULL",
        "FOREIGN KEY (batch_sha256)",
        "CREATE FUNCTION public.ingest_engine_event_batch_v2",
        "CREATE FUNCTION job_plane.ingest_engine_job_result_v2",
        "CREATE FUNCTION public.engine_run_completion_append_guard",
        "CREATE TRIGGER engine_events_reject_after_p1_completion",
        "completed P1 engine run cannot advance",
        "RuntimeError",
    ):
        assert expected in source


def test_p1_wrapper_revalidates_metadata_against_completion_before_projection() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_engine_event_batch_v2$", 1
    )[1].split("$ingest_engine_event_batch_v2$;", 1)[0]

    for expected in (
        "octet_length(p_batch_document) > 67108864",
        "nautilus-p1-event-stream-v1",
        "v_total <> 23 OR v_valid <> 23",
        "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        "p1_product_closure_sha256",
        "request_message_id",
        "semantic_digest",
        "target_count",
        "order_count",
        "fill_count",
        "final_cash",
        "final_position",
        "realized_pnl",
        "unrealized_pnl",
        "jsonb_path_query_first",
        "jsonb_path_query_array",
        "jsonb_typeof(v_metadata -> 'target_count') <> 'number'",
        "P1 completion differs from validation metadata",
    ):
        assert expected in body
    assert body.index("octet_length(p_batch_document)") < body.index(
        "p_batch_document::jsonb"
    )
    assert body.index("P1 completion differs from validation metadata") < body.index(
        "public.ingest_engine_event_batch(v_legacy_document)"
    ) < body.index("UPDATE public.engine_run_projections")


def test_v2_keeps_v1_ingest_and_job_binding_as_atomic_authority() -> None:
    source = MIGRATION.read_text()
    batch_body = source.split(
        "AS $ingest_engine_event_batch_v2$", 1
    )[1].split("$ingest_engine_event_batch_v2$;", 1)[0]
    job_body = source.split(
        "AS $ingest_engine_job_result_v2$", 1
    )[1].split("$ingest_engine_job_result_v2$;", 1)[0]

    assert "RETURN QUERY SELECT *" in batch_body
    assert "public.ingest_engine_event_batch(p_batch_document)" in batch_body
    assert "public.ingest_engine_event_batch(v_legacy_document)" in batch_body
    assert "public.ingest_engine_event_batch_v2(p_batch_document)" in job_body
    assert "job_plane.ingest_engine_job_result(" in job_body
    assert "IS DISTINCT FROM accepted.batch_sha256" in job_body


def test_v2_grants_only_the_existing_worker_result_role() -> None:
    source = MIGRATION.read_text()

    assert (
        "job_plane.ingest_engine_job_result_v2(text, text, text, text, text)\n"
        "          TO trading_job_worker"
    ) in source
    assert "GRANT EXECUTE ON FUNCTION\n          public.ingest_engine_event_batch_v2" not in source
    assert "FROM PUBLIC, trading_jobs, trading_migrator, trading_reader" in source


def test_migration_source_is_explicitly_nonexecuting() -> None:
    source = MIGRATION.read_text()
    test_source = Path(__file__).read_text()

    assert "source authority only and is never applied by validation" in source
    assert ("import " + "psycopg") not in test_source
    assert ("disposable" + "_database") not in test_source
