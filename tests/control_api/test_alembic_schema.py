from __future__ import annotations

from pathlib import Path

import pytest
import psycopg
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


EXPECTED_TABLES = {
    "alembic_version",
    "assets",
    "market_reports",
    "market_asset_snapshots",
    "decisions",
    "decision_signal_snapshots",
    "signals",
    "capability_evidence",
    "cost_summaries",
    "cost_sessions",
    "system_status_snapshots",
    "migration_runs",
    "migration_source_files",
    "migration_source_chunks",
    "migration_errors",
    "audit_events",
    "decision_field_lineage",
    "cost_session_assets",
    "asset_source_lineage",
    "phase3b_backfill_runs",
    "phase3b_backfill_events",
    "jobs",
    "job_attempts",
    "job_events",
    "scheduler_heartbeats",
    "job_artifacts",
    "worker_heartbeats",
    "domain_events",
    "event_append_idempotency",
    "event_outbox",
    "event_publications",
    "consumer_inbox",
    "aggregate_snapshots",
    "market_data_snapshots",
    "market_data_candles",
    "engine_event_batch_receipts",
    "engine_events",
    "engine_run_projections",
    "engine_job_results",
}
EXACT_HEAD = "0014_p1_product_closure_rotation"
P1_PROJECTION_REVISION = "0013_engine_backtest_enqueue_authority"
P1_OLD_CLOSURE_SHA256 = (
    "75467781b920e7172917a96d162fb6e2a3e8f9afee9eff065ef0ed220f623069"
)
P1_NEW_CLOSURE_SHA256 = (
    "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"
)
P1_INGEST_REGPROCEDURE = (
    "job_plane.ingest_p1_engine_event_batch_v2("
    "text,uuid,uuid,uuid,uuid,text,text,text,text,text)"
)


def _p1_ingest_authority(connection: Connection) -> tuple[object, ...]:
    return tuple(
        connection.exec_driver_sql(
            "SELECT pg_get_functiondef(function_row.oid), "
            "function_row.proowner, function_row.proacl::text, "
            "function_row.prosecdef, function_row.provolatile, "
            "function_row.proparallel, function_row.proconfig "
            "FROM pg_catalog.pg_proc AS function_row "
            "WHERE function_row.oid = to_regprocedure(%s)",
            (P1_INGEST_REGPROCEDURE,),
        ).one()
    )


def alembic_config() -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    return config


def test_empty_database_upgrades_to_deterministic_head() -> None:
    with disposable_database(
        operation_id="control-api-alembic-head-cycle-v1",
        planned=True,
    ) as settings:
        engine = create_engine(settings.sqlalchemy_url())
        with engine.connect() as connection:
            config = alembic_config()
            config.attributes["connection"] = connection
            command.upgrade(config, P1_PROJECTION_REVISION)
            before = _p1_ingest_authority(connection)
            before_definition = before[0]
            assert isinstance(before_definition, str)
            assert before_definition.count(P1_OLD_CLOSURE_SHA256) == 2
            assert P1_NEW_CLOSURE_SHA256 not in before_definition

            wrong_prior = before_definition.replace(
                P1_OLD_CLOSURE_SHA256, P1_NEW_CLOSURE_SHA256, 1
            )
            connection.exec_driver_sql(
                wrong_prior,
                execution_options={"no_parameters": True},
            )
            connection.commit()
            with pytest.raises(DBAPIError) as rejected:
                command.upgrade(config, "head")
            assert getattr(rejected.value.orig, "sqlstate", None) == "P2D08"
            connection.rollback()
            connection.exec_driver_sql(
                before_definition,
                execution_options={"no_parameters": True},
            )
            connection.commit()

            command.upgrade(config, "head")
            after = _p1_ingest_authority(connection)
            assert after[0] == before_definition.replace(
                P1_OLD_CLOSURE_SHA256, P1_NEW_CLOSURE_SHA256
            )
            assert after[1:] == before[1:]
            command.upgrade(config, "head")
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == EXPECTED_TABLES

            required_indexes = {
                "ix_market_reports_as_of",
                "ix_decisions_as_of",
                "ix_decisions_asset_as_of",
                "ix_decisions_action_as_of",
                "ix_signals_as_of",
                "ix_capability_evidence_latest",
                "domain_events_stream_sequence_idx",
                "event_outbox_topic_event_idx",
                "market_data_snapshots_lookup_idx",
                "market_data_snapshots_digest_idx",
                "market_data_candles_snapshot_sequence_idx",
                "engine_event_receipts_run_sequence_idx",
                "engine_events_run_sequence_idx",
                "engine_events_batch_idx",
            }
            actual_indexes = {
                index["name"]
                for table in EXPECTED_TABLES
                for index in inspector.get_indexes(table)
            }
            assert required_indexes <= actual_indexes

            unique_names = {
                constraint["name"]
                for table in EXPECTED_TABLES
                for constraint in inspector.get_unique_constraints(table)
            }
            assert {
                "uq_market_reports_source",
                "uq_market_asset_snapshots_report_asset",
                "uq_decisions_source_record",
                "uq_signals_source_record",
                "uq_migration_source_chunks_identity",
                "market_data_snapshot_identity",
            } <= unique_names

            context = MigrationContext.configure(connection)
            current = set(context.get_current_heads())
            script = ScriptDirectory.from_config(config)
            assert current == set(script.get_heads()) == {
                EXACT_HEAD
            }

            decision_columns = {
                item["name"]: item for item in inspector.get_columns("decisions")
            }
            assert decision_columns["price_at_decision"]["nullable"] is True
            assert decision_columns["report_snippet"]["nullable"] is True
            assert decision_columns["price_provenance_quality"]["nullable"] is False
            assert decision_columns["snippet_provenance_quality"]["nullable"] is False
            confidence_type = decision_columns["confidence"]["type"]
            price_type = decision_columns["price_at_decision"]["type"]
            assert (confidence_type.precision, confidence_type.scale) == (8, 6)
            assert (price_type.precision, price_type.scale) == (30, 12)

            cost_columns = {
                item["name"]: item for item in inspector.get_columns("cost_sessions")
            }
            assert cost_columns["symbols_provenance_quality"]["nullable"] is False
            assert cost_columns["symbols_evidence_state"]["nullable"] is False

            engine_projection_columns = {
                item["name"]: item
                for item in inspector.get_columns("engine_run_projections")
            }
            assert engine_projection_columns["batch_sha256"]["nullable"] is True
            assert engine_projection_columns["semantic_digest"]["nullable"] is True
            assert engine_projection_columns["request_message_id"]["nullable"] is True

            foreign_keys = sum(
                (inspector.get_foreign_keys(table) for table in EXPECTED_TABLES),
                start=[],
            )
            checks = sum(
                (inspector.get_check_constraints(table) for table in EXPECTED_TABLES),
                start=[],
            )
            assert len(foreign_keys) >= 15
            assert {item["name"] for item in checks} >= {
                "ck_decisions_action",
                "ck_decisions_confidence",
                "ck_signals_confidence",
                "ck_market_reports_provenance",
                "ck_decisions_price_provenance",
                "ck_decisions_snippet_provenance",
                "ck_decision_field_lineage_field",
                "ck_cost_sessions_symbols_state",
                "ck_phase3b_backfill_runs_status",
                "ck_phase3b_backfill_events_reason",
                "engine_run_projection_result_authority_complete",
            }

            projection_foreign_keys = {
                item["name"]
                for item in inspector.get_foreign_keys("engine_run_projections")
            }
            assert "engine_run_projection_batch_fkey" in projection_foreign_keys
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'job_plane.ingest_p1_engine_event_batch_v2("
                "text,uuid,uuid,uuid,uuid,text,text,text,text,text)')"
            ).scalar_one() is not None
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'job_plane.ingest_engine_job_result_v2(text,text,text,text,text)')"
            ).scalar_one() is not None
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'job_plane.ingest_legacy_engine_job_result_v2("
                "text,text,text,text,text)')"
            ).scalar_one() is not None
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'job_plane.paper_worker_job_id_allowed(text)')"
            ).scalar_one() is not None
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'public.engine_run_completion_append_guard()')"
            ).scalar_one() is not None
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM pg_catalog.pg_policies "
                "WHERE schemaname = 'public' "
                "AND tablename = 'worker_heartbeats' "
                "AND policyname IN ("
                "'job_plane_worker_heartbeats_insert', "
                "'job_plane_worker_heartbeats_update') "
                "AND roles = ARRAY['trading_job_worker']::name[] "
                "AND with_check LIKE "
                "'%paper_worker_job_id_allowed(current_job_id)%'"
            ).scalar_one() == 2
            assert connection.exec_driver_sql(
                "SELECT has_function_privilege("
                "'trading_job_worker', "
                "'job_plane.ingest_p1_engine_event_batch_v2("
                "text,uuid,uuid,uuid,uuid,text,text,text,text,text)', "
                "'EXECUTE')"
            ).scalar_one() is False
            assert connection.exec_driver_sql(
                "SELECT has_function_privilege("
                "'trading_job_worker', "
                "'job_plane.ingest_engine_job_result_v2("
                "text,text,text,text,text)', 'EXECUTE')"
            ).scalar_one() is True
            assert connection.exec_driver_sql(
                "SELECT has_function_privilege("
                "'trading_job_worker', "
                "'job_plane.ingest_legacy_engine_job_result_v2("
                "text,text,text,text,text)', 'EXECUTE')"
            ).scalar_one() is True
            assert connection.exec_driver_sql(
                "SELECT has_function_privilege("
                "'trading_job_worker', "
                "'job_plane.ingest_engine_job_result("
                "text,text,text,text,text)', 'EXECUTE')"
            ).scalar_one() is False
            assert connection.exec_driver_sql(
                "SELECT has_function_privilege("
                "'trading_job_worker', "
                "'job_plane.paper_worker_job_id_allowed(text)', 'EXECUTE')"
            ).scalar_one() is True
            assert connection.exec_driver_sql(
                "SELECT has_function_privilege("
                "'trading_job_worker', "
                "'job_plane.paper_worker_job_allowed(text,jsonb)', 'EXECUTE')"
            ).scalar_one() is False
            for role in (
                "trading_jobs",
                "trading_migrator",
                "trading_reader",
                "trading_job_api",
                "trading_job_scheduler",
            ):
                assert connection.exec_driver_sql(
                    "SELECT has_function_privilege("
                    f"'{role}', "
                    "'job_plane.paper_worker_job_id_allowed(text)', 'EXECUTE')"
                ).scalar_one() is False

            assert {
                "uq_decision_field_lineage_identity",
                "uq_cost_session_assets_identity",
                "uq_asset_source_lineage_identity",
            } <= unique_names

            with pytest.raises(RuntimeError, match="forward-only"):
                command.downgrade(config, "0005_job_plane_role_split")
            assert set(
                MigrationContext.configure(connection).get_current_heads()
            ) == {EXACT_HEAD}
        engine.dispose()


def test_application_role_permissions_are_least_privilege() -> None:
    with disposable_database(
        operation_id="control-api-application-role-permissions-v1",
        planned=True,
    ) as owner:
        upgrade_to_head(owner)
        migrator = disposable_role_settings(owner, "trading_migrator")
        reader = disposable_role_settings(owner, "trading_reader")
        run_id = "permission-probe"
        insert_sql = """
            INSERT INTO migration_runs (
              run_id, started_at, status, code_commit, schema_version,
              normalization_version, source_root, source_inventory_hash
            ) VALUES (%s, now(), 'RUNNING', 'probe', '1.0.0', 'phase3-v1',
                      '/synthetic', %s)
        """
        with psycopg.connect(migrator.conninfo()) as connection:
            assert connection.execute("SELECT current_user").fetchone()[0] == (
                "trading_migrator"
            )
            connection.execute(insert_sql, (run_id, "0" * 64))
            connection.rollback()

        with psycopg.connect(reader.conninfo()) as connection:
            assert connection.execute("SELECT current_user").fetchone()[0] == (
                "trading_reader"
            )
            assert connection.execute("SELECT count(*) FROM migration_runs").fetchone()
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                connection.execute(insert_sql, (run_id, "0" * 64))
            connection.rollback()
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                connection.execute("UPDATE migration_runs SET status='FAILED'")

        with psycopg.connect(migrator.conninfo(), autocommit=True) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute("CREATE ROLE phase3_forbidden_probe")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute("CREATE DATABASE phase3_forbidden_probe")

        for role in (
            "trading_job_api",
            "trading_job_worker",
            "trading_job_scheduler",
        ):
            settings = disposable_role_settings(owner, role)
            with psycopg.connect(settings.conninfo()) as connection:
                assert connection.execute("SELECT current_user").fetchone()[0] == role
                assert connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()[0] == EXACT_HEAD

        with psycopg.connect(owner.conninfo()) as connection:
            assert connection.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'trading_jobs'"
            ).fetchone()[0] is False


def test_prior_supported_0007_revision_upgrades_to_exact_0008() -> None:
    with disposable_database(
        operation_id="control-api-alembic-0007-to-0008-v1",
        planned=True,
    ) as owner:
        _upgrade_to_revision(owner, "0007_job_event_chain_authority")
        with psycopg.connect(owner.conninfo()) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "0007_job_event_chain_authority"
            assert connection.execute(
                "SELECT to_regclass('public.domain_events')"
            ).fetchone()[0] is None

        _upgrade_to_revision(owner, "0008_trading_domain_ledger")
        with psycopg.connect(owner.conninfo()) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "0008_trading_domain_ledger"
            assert connection.execute(
                "SELECT to_regclass('public.domain_events')"
            ).fetchone()[0] == "domain_events"
            assert connection.execute(
                "SELECT to_regprocedure('public.append_domain_event(uuid,uuid,bigint,text,text,text,text)')"
            ).fetchone()[0] is not None
            assert connection.execute(
                "SELECT has_database_privilege("
                "'trading_jobs', current_database(), 'CONNECT')"
            ).fetchone()[0] is False


def test_prior_0008_revision_upgrades_to_exact_0009() -> None:
    with disposable_database(
        operation_id="control-api-alembic-0008-to-0009-v1",
        planned=True,
    ) as owner:
        _upgrade_to_revision(owner, "0008_trading_domain_ledger")
        with psycopg.connect(owner.conninfo()) as connection:
            assert connection.execute(
                "SELECT to_regclass('public.market_data_snapshots')"
            ).fetchone()[0] is None

        _upgrade_to_revision(owner, "0009_canonical_market_data")
        with psycopg.connect(owner.conninfo()) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "0009_canonical_market_data"
            assert connection.execute(
                "SELECT to_regclass('public.market_data_snapshots')"
            ).fetchone()[0] == "market_data_snapshots"
            assert connection.execute(
                "SELECT to_regprocedure('public.save_market_data_snapshot(text)')"
            ).fetchone()[0] is not None
