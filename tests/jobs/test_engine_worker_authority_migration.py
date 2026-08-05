from __future__ import annotations

from pathlib import Path


MIGRATION = Path(
    "alembic/versions/0011_engine_backtest_worker_authority.py"
)


def test_engine_worker_authority_is_forward_chained_and_fail_closed() -> None:
    source = MIGRATION.read_text()

    for expected in (
        'revision = "0011_engine_backtest_worker_authority"',
        'down_revision = "0010_engine_event_ledger"',
        "CREATE TABLE public.engine_job_results",
        "PRIMARY KEY",
        "batch_sha256 char(64) NOT NULL UNIQUE",
        "CREATE FUNCTION job_plane.ingest_engine_job_result",
        "session_user <> 'trading_job_worker'",
        "public.ingest_engine_event_batch(p_batch_document)",
        "ON CONFLICT (job_id) DO NOTHING",
        "binding.batch_sha256 IS DISTINCT FROM accepted.batch_sha256",
        "USING ERRCODE = 'P2D01'",
        "RuntimeError",
    ):
        assert expected in source


def test_paper_worker_capabilities_clone_exact_reviewed_predicate_once() -> None:
    source = MIGRATION.read_text()

    for capability in (
        "worker_claim_paper",
        "worker_start_paper",
        "worker_control_paper_lease",
        "worker_finalize_paper",
        "worker_recover_expired_paper",
    ):
        assert capability in source
        assert f"GRANT EXECUTE ON FUNCTION job_plane.{capability}" in source
    for proof in (
        "pg_get_functiondef",
        "regexp_matches",
        "regexp_replace",
        "occurrences <> 1",
        "paper worker source predicate drifted",
        "ANY (ARRAY[''SNAPSHOT''::text, ''BACKTEST''::text])",
        "0011 parent head changed during migration",
    ):
        assert proof in source


def test_runtime_role_gets_only_required_job_result_authority() -> None:
    source = MIGRATION.read_text()

    assert (
        "GRANT SELECT ON TABLE public.engine_event_batch_receipts\n"
        "          TO trading_job_worker"
    ) in source
    assert "GRANT SELECT ON TABLE public.engine_job_results" in source
    assert (
        "GRANT EXECUTE ON FUNCTION job_plane.ingest_engine_job_result(text)"
        in source
    )
    assert "GRANT SELECT ON TABLE public.engine_events" not in source
    assert "GRANT SELECT ON TABLE public.engine_run_projections" not in source
