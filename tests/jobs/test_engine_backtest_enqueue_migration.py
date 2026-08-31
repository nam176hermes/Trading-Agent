from __future__ import annotations

from pathlib import Path


MIGRATION = Path(
    "alembic/versions/0013_engine_backtest_enqueue_authority.py"
)


def _function_body() -> str:
    source = MIGRATION.read_text()
    return source.split("AS $api_enqueue_engine_backtest$", 1)[1].split(
        "$api_enqueue_engine_backtest$;", 1
    )[0]


def test_engine_backtest_enqueue_is_forward_only_exact_authority() -> None:
    source = MIGRATION.read_text()

    for expected in (
        'revision = "0013_engine_backtest_enqueue_authority"',
        'down_revision = "0012_p1_engine_projection_authority"',
        "CREATE FUNCTION job_plane.api_enqueue_engine_backtest(",
        "SECURITY DEFINER",
        "VOLATILE",
        "PARALLEL UNSAFE",
        "SET search_path = pg_catalog",
        "RuntimeError",
    ):
        assert expected in source


def test_engine_backtest_enqueue_revalidates_role_shape_and_fingerprint() -> None:
    body = _function_body()

    for expected in (
        "session_user <> 'trading_job_api'",
        "job_plane.paper_worker_job_allowed('BACKTEST', p_payload)",
        "public.canonical_domain_json(p_payload)",
        "public.digest(",
        "'sha256'",
        "p_payload_fingerprint IS DISTINCT FROM v_payload_fingerprint",
    ):
        assert expected in body
    assert body.index("session_user <> 'trading_job_api'") < body.index(
        "INSERT INTO public.jobs"
    )
    assert body.index("paper_worker_job_allowed") < body.index(
        "INSERT INTO public.jobs"
    )
    assert body.index("public.digest(") < body.index("INSERT INTO public.jobs")


def test_engine_backtest_enqueue_fixes_identity_and_appends_one_atomic_event() -> None:
    body = _function_body()

    for expected in (
        "p_job_id, 'BACKTEST', 'QUEUED', p_payload",
        "p_idempotency_key, 'OPERATOR', p_actor_id",
        "p_priority, 2",
        "ON CONFLICT (job_type, idempotency_key) DO NOTHING",
        "p_event_id, inserted_job_id, NULL, 1, NULL, 'QUEUED'",
        "'ENQUEUED', 'OPERATOR', p_actor_id, p_trace_id, '{}'::jsonb",
        "job_row.job_type = 'BACKTEST'",
        "CONSTRAINT = 'job_plane_idempotency_identity'",
        "outcome := 'DEDUPLICATED'",
    ):
        assert expected in body


def test_engine_backtest_enqueue_acl_is_closed_to_every_other_role() -> None:
    source = MIGRATION.read_text()
    signature = (
        "job_plane.api_enqueue_engine_backtest(\n"
        "            text, jsonb, text, text, text, smallint, text, text\n"
        "          )"
    )

    assert f"REVOKE ALL PRIVILEGES ON FUNCTION\n          {signature}" in source
    assert (
        "FROM PUBLIC, trading_jobs, trading_migrator, trading_reader,\n"
        "               trading_job_api, trading_job_worker, trading_job_scheduler"
    ) in source
    assert (
        f"GRANT EXECUTE ON FUNCTION\n          {signature}\n"
        "          TO trading_job_api"
    ) in source
    assert "GRANT INSERT" not in source
