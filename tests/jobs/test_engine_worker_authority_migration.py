from __future__ import annotations

import hashlib
import re
from pathlib import Path


MIGRATION = Path(
    "alembic/versions/0011_engine_backtest_worker_authority.py"
)
TRANSITION_AUTHORITY = Path(
    "alembic/versions/0006_job_transition_database_authority.py"
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
        "p_job_id text",
        "p_attempt_id text",
        "p_worker_id text",
        "p_lease_token text",
        "session_user <> 'trading_job_worker'",
        "current_job.state <> 'RUNNING'",
        "current_attempt.outcome <> 'RUNNING'",
        "current_attempt.attempt_number <> current_job.attempt_count",
        "current_job.lease_owner IS DISTINCT FROM p_worker_id",
        "current_job.lease_token IS DISTINCT FROM p_lease_token",
        "current_attempt.lease_expires_at <= statement_timestamp()",
        "accepted.job_id IS DISTINCT FROM p_job_id",
        "accepted.attempt_id IS DISTINCT FROM p_attempt_id",
        "public.ingest_engine_event_batch(p_batch_document)",
        "ON CONFLICT (job_id) DO NOTHING",
        "binding.batch_sha256 IS DISTINCT FROM accepted.batch_sha256",
        "USING ERRCODE = 'P2D01'",
        "RuntimeError",
    ):
        assert expected in source


def test_paper_worker_capabilities_derive_only_from_pinned_reviewed_bodies() -> None:
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
        "source_hashes text[]",
        "public.digest",
        "pg_get_function_result",
        "pg_get_userbyid(procedure_row.proowner) = 'trading_owner'",
        "procedure_row.prosecdef",
        "procedure_row.proconfig = ARRAY['search_path=pg_catalog']",
        "paper worker source ACL drifted",
        "occurrences <> 1",
        "paper worker source predicate drifted",
        "source_body, reviewed_predicate, paper_predicate",
        "paper worker promoted body postflight failed",
        "paper worker target catalog postflight failed",
        "paper worker target ACL postflight failed",
        "paper worker support catalog postflight failed",
        "paper worker support ACL postflight failed",
        "job_plane.paper_worker_job_allowed(job_row.job_type, job_row.payload)",
        "0011 parent head changed during migration",
    ):
        assert proof in source
    assert "regexp_matches" not in source
    assert "regexp_replace" not in source
    for pinned_hash in (
        "0755231aaba81d1692581ff62b44f5babb03dcc0ed352687fc3a67b0ad2ee80a",
        "a7a402f871b4790f74dd774e5b8ce9417294dc78f6bca436062acddb933fa905",
        "47d09d4157c6992e8e21c3f6f08866e32176e8fda04a74bb2b4f49582ad5233d",
        "4a19cdf22297ee97dc7e31f354f42c020075e9a61e3cd411fb9dc8b160c88181",
        "90d7cda5361ccc9e2364821baad7a8a5c4ce56f5d3552b7ba835e980eb175b82",
    ):
        assert pinned_hash in source


def test_each_frozen_hash_matches_the_complete_reviewed_0006_function_body() -> None:
    migration = MIGRATION.read_text()
    source = TRANSITION_AUTHORITY.read_text()
    expected = {
        "worker_claim_snapshot": (
            "0755231aaba81d1692581ff62b44f5babb03dcc0ed352687fc3a67b0ad2ee80a"
        ),
        "worker_start_snapshot": (
            "a7a402f871b4790f74dd774e5b8ce9417294dc78f6bca436062acddb933fa905"
        ),
        "worker_control_snapshot_lease": (
            "47d09d4157c6992e8e21c3f6f08866e32176e8fda04a74bb2b4f49582ad5233d"
        ),
        "worker_finalize_snapshot": (
            "4a19cdf22297ee97dc7e31f354f42c020075e9a61e3cd411fb9dc8b160c88181"
        ),
        "worker_recover_expired_snapshot": (
            "90d7cda5361ccc9e2364821baad7a8a5c4ce56f5d3552b7ba835e980eb175b82"
        ),
    }

    for function_name, expected_sha256 in expected.items():
        bodies = re.findall(
            rf"CREATE FUNCTION job_plane\.{function_name}\(.*?"
            rf"AS \$function\$(.*?)\$function\$;",
            source,
            flags=re.DOTALL,
        )
        assert len(bodies) == 1
        assert hashlib.sha256(bodies[0].encode()).hexdigest() == expected_sha256
        assert bodies[0].count("job_row.job_type = 'SNAPSHOT'") == 1
        assert migration.count(expected_sha256) >= 1


def test_paper_worker_payload_gate_is_exact_and_legacy_fail_closed() -> None:
    source = MIGRATION.read_text()

    for proof in (
        "CREATE FUNCTION job_plane.paper_worker_job_allowed",
        "WHEN p_job_type = 'SNAPSHOT' THEN true",
        "WHEN p_job_type IS NULL",
        "OR p_job_type <> 'BACKTEST'",
        "p_payload IS DISTINCT FROM jsonb_build_object(",
        "p_payload -> 'engine_backtest' IS DISTINCT FROM",
        "'engine_configuration'",
        "'instrument_catalog'",
        "'strategy_configuration'",
        "'market_data'",
        "'start_time'",
        "'end_time'",
        "artifact.value IS NOT DISTINCT FROM jsonb_build_object(",
        "jsonb_typeof(artifact.value -> 'artifact_id') = 'string'",
        "jsonb_typeof(artifact.value -> 'sha256') = 'string'",
        "jsonb_typeof(artifact.value -> 'media_type') = 'string'",
        "'application/jsonl'",
        "to_char(to_date(",
        ")::timestamp without time zone",
        "job_row.job_type = 'BACKTEST'",
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
        "GRANT EXECUTE ON FUNCTION job_plane.ingest_engine_job_result(\n"
        "          text, text, text, text, text"
        in source
    )
    assert "GRANT SELECT ON TABLE public.engine_events" not in source
    assert "GRANT SELECT ON TABLE public.engine_run_projections" not in source
