from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


MIGRATION = Path("alembic/versions/0012_p1_engine_projection_authority.py")


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0012_under_test", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_sends_0012_block_directly_to_driver_without_bind_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class Bind:
        def exec_driver_sql(self, statement: str) -> None:
            statements.append(statement)

    class Operations:
        @staticmethod
        def get_bind() -> Bind:
            return Bind()

        @staticmethod
        def execute(_statement: object) -> None:
            raise AssertionError("SQLAlchemy bind compilation seam used")

    migration = _load_migration()
    monkeypatch.setattr(migration, "op", Operations())

    migration.upgrade()

    assert len(statements) == 1
    assert "CREATE FUNCTION job_plane.ingest_p1_engine_event_batch_v2" in statements[0]
    assert (
        "^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\\.[0-9]*[1-9])$"
        in statements[0]
    )


def test_p1_projection_migration_is_minimal_forward_authority() -> None:
    source = MIGRATION.read_text()

    for expected in (
        'revision = "0012_p1_engine_projection_authority"',
        'down_revision = "0011_engine_backtest_worker_authority"',
        "ALTER TABLE public.engine_run_projections",
        "ADD COLUMN batch_sha256 char(64)",
        "ADD COLUMN semantic_digest char(64)",
        "ADD COLUMN request_message_id uuid",
        "engine_run_projection_result_authority_complete",
        "batch_sha256 IS NOT NULL",
        "semantic_digest IS NOT NULL",
        "request_message_id IS NOT NULL",
        "FOREIGN KEY (batch_sha256)",
        "CREATE FUNCTION job_plane.ingest_p1_engine_event_batch_v2",
        "CREATE FUNCTION job_plane.ingest_legacy_engine_job_result_v2",
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
        "AS $ingest_p1_engine_event_batch_v2$", 1
    )[1].split("$ingest_p1_engine_event_batch_v2$;", 1)[0]

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
        "AS $ingest_p1_engine_event_batch_v2$", 1
    )[1].split("$ingest_p1_engine_event_batch_v2$;", 1)[0]
    job_body = source.split(
        "AS $ingest_engine_job_result_v2$", 1
    )[1].split("$ingest_engine_job_result_v2$;", 1)[0]

    assert "public.ingest_engine_event_batch(v_legacy_document)" in batch_body
    assert "job_plane.ingest_p1_engine_event_batch_v2(" in job_body
    assert "job_plane.ingest_engine_job_result(" in job_body
    assert "IS DISTINCT FROM accepted.batch_sha256" in job_body


def test_legacy_job_wrapper_rejects_p1_marker_before_v1_binding() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_legacy_engine_job_result_v2$", 1
    )[1].split("$ingest_legacy_engine_job_result_v2$;", 1)[0]

    for expected in (
        "session_user <> 'trading_job_worker'",
        "octet_length(p_batch_document) > 67108864",
        "nautilus-p1-event-stream-v1",
        "job_plane.ingest_engine_job_result(",
        "legacy engine job result contains P1 authority",
    ):
        assert expected in body
    wrapper = source.split(
        "CREATE FUNCTION job_plane.ingest_legacy_engine_job_result_v2", 1
    )[1].split("$ingest_legacy_engine_job_result_v2$;", 1)[0]
    assert "SECURITY DEFINER" in wrapper
    assert "SET search_path = pg_catalog" in wrapper
    assert body.index("session_user <> 'trading_job_worker'") < body.index(
        "p_batch_document::jsonb"
    )
    assert body.index("octet_length(p_batch_document) > 67108864") < body.index(
        "p_batch_document::jsonb"
    )
    assert body.index("nautilus-p1-event-stream-v1") < body.index(
        "job_plane.ingest_engine_job_result("
    )


def test_p1_wrapper_closes_every_event_and_deterministic_message_identity() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_p1_engine_event_batch_v2$", 1
    )[1].split("$ingest_p1_engine_event_batch_v2$;", 1)[0]

    for expected in (
        "FOR v_p1_event, v_ordinal IN",
        "jsonb_object_keys(v_p1_envelope)",
        "jsonb_object_keys(v_p1_payload)",
        "jsonb_object_keys(v_p1_attribute)",
        "P1 event attributes are not closed",
        "P1 event authority differs from the request",
        "P1 event state transition is invalid",
        "public.digest(",
        "'sha1'",
        "v_expected_message_id",
        "v_semantic_events",
        "P1 semantic projection digest is invalid",
        "v_expected_ingestion_digest",
        "P1 ingestion identity is invalid",
        "jsonb_each(v_p1_attributes)",
        "^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\\.[0-9]*[1-9])$",
        "jsonb_array_elements_text",
        "::timestamptz",
    ):
        assert expected in body
    assert body.index("FOR v_p1_event, v_ordinal IN") < body.index(
        "public.ingest_engine_event_batch(v_legacy_document)"
    )
    assert "^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$" not in body
    assert body.count("(?:\\.[0-9]{6})?Z$") == 3
    assert body.count("~ '\\.000000Z$'") == 3
    assert "[0-9]{1,6}" not in body


def test_p1_wrapper_requires_exact_attribute_tuple_order() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_p1_engine_event_batch_v2$", 1
    )[1].split("$ingest_p1_engine_event_batch_v2$;", 1)[0]
    normalized = " ".join(body.split())

    assert "v_attribute_names IS DISTINCT FROM v_expected_attribute_names" in body
    for exact_order in (
        "'schema_version', 'sequence', 'simulation_time', 'origin', "
        "'runtime_family', 'engine_version', 'upstream_commit',",
        "'native_type', 'client_order_id', 'native_order_id', "
        "'target_id', 'source_signal_ids', 'side', 'quantity',",
        "'native_type', 'client_order_id', 'native_fill_id', 'side', "
        "'quantity', 'price', 'fee', 'fee_currency'",
    ):
        assert exact_order in normalized


def test_paper_backtest_heartbeat_policy_uses_only_private_job_id_helper() -> None:
    source = MIGRATION.read_text()
    normalized = " ".join(source.split())

    for policy in (
        "job_plane_worker_heartbeats_insert",
        "job_plane_worker_heartbeats_update",
    ):
        assert f"DROP POLICY {policy}" in source
        assert f"CREATE POLICY {policy}" in source
    assert "CREATE FUNCTION job_plane.paper_worker_job_id_allowed" in source
    assert "SECURITY DEFINER\n        STABLE\n        PARALLEL SAFE" in source
    assert (
        "job_plane.paper_worker_job_allowed( job_row.job_type, job_row.payload )"
        in normalized
    )
    assert "job_plane.paper_worker_job_id_allowed(current_job_id)" in source
    assert (
        "job_plane.paper_worker_job_id_allowed(text)\n          TO trading_job_worker"
    ) in source
    assert (
        "job_plane.paper_worker_job_allowed(text, jsonb)\n          TO trading_job_worker"
    ) not in source


def test_job_v2_checks_session_role_before_any_batch_ingestion() -> None:
    source = MIGRATION.read_text()
    body = source.split(
        "AS $ingest_engine_job_result_v2$", 1
    )[1].split("$ingest_engine_job_result_v2$;", 1)[0]

    for expected in (
        "session_user <> 'trading_job_worker'",
        "current_job public.jobs%ROWTYPE",
        "current_attempt public.job_attempts%ROWTYPE",
        "current_heartbeat public.worker_heartbeats%ROWTYPE",
        "job_plane.paper_worker_job_allowed",
        "FOR UPDATE",
        "trading-agent:engine-command:v1:",
        "current_attempt.attempt_number",
        "v_expected_request_message_id",
        "v_expected_correlation_id",
        "v_expected_causation_id",
        "v_expected_engine_run_id",
        "P1 job result request authority rejected",
        "P1 engine job result document exceeds the bound",
    ):
        assert expected in body
    ingest = body.index("job_plane.ingest_p1_engine_event_batch_v2(")
    assert body.index("session_user <> 'trading_job_worker'") < ingest
    assert body.index("SELECT job_row.* INTO current_job") < ingest
    assert body.index("SELECT attempt_row.* INTO current_attempt") < ingest
    assert body.index("SELECT heartbeat_row.* INTO current_heartbeat") < ingest
    assert body.index("P1 job result request authority rejected") < ingest
    assert body.index("octet_length(p_batch_document) > 67108864") < body.index(
        "p_batch_document::jsonb"
    )


def test_v2_grants_only_the_existing_worker_result_role() -> None:
    source = MIGRATION.read_text()

    assert (
        "job_plane.ingest_engine_job_result_v2(text, text, text, text, text)\n"
        "          TO trading_job_worker"
    ) in source
    assert (
        "REVOKE EXECUTE ON FUNCTION\n"
        "          job_plane.ingest_engine_job_result(text, text, text, text, text)\n"
        "          FROM trading_job_worker"
    ) in source
    assert (
        "job_plane.ingest_legacy_engine_job_result_v2(\n"
        "            text, text, text, text, text\n"
        "          )\n"
        "          TO trading_job_worker"
    ) in source
    assert "GRANT EXECUTE ON FUNCTION\n          job_plane.ingest_p1_engine_event_batch_v2" not in source
    assert "FROM PUBLIC, trading_jobs, trading_migrator, trading_reader" in source


def test_migration_source_is_explicitly_nonexecuting() -> None:
    source = MIGRATION.read_text()
    test_source = Path(__file__).read_text()

    assert "source authority only and is never applied by validation" in source
    assert ("import " + "psycopg") not in test_source
    assert ("disposable" + "_database") not in test_source
