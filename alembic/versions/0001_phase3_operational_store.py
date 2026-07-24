"""Create the Phase 3 operational store.

Revision ID: 0001_phase3_operational_store
Revises: None
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_phase3_operational_store"
down_revision = None
branch_labels = None
depends_on = None

PROVENANCE = "provenance_quality IN ('EXACT','DERIVED','LEGACY_ESTIMATED','UNKNOWN')"


def _metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    timestamps = lambda: (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    sa.Table(
        "migration_runs", metadata,
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("code_commit", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("source_root", sa.Text, nullable=False),
        sa.Column("source_inventory_hash", sa.String(64), nullable=False),
        sa.Column("records_seen", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_updated", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_invalid", sa.BigInteger, nullable=False, server_default="0"),
        sa.CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED')", name="ck_migration_runs_status"),
    )
    sa.Table(
        "assets", metadata,
        sa.Column("asset_id", sa.String(128), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=False),
        sa.Column("instrument_type", sa.String(32), nullable=False),
        sa.Column("base_currency", sa.String(32), nullable=False),
        sa.Column("quote_currency", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("asset_class", "instrument_type", "base_currency", "quote_currency", name="uq_assets_identity"),
        sa.CheckConstraint("asset_class IN ('CRYPTO','EQUITY','ETF','FUTURE','FOREX','UNKNOWN')", name="ck_assets_class"),
        sa.CheckConstraint("status IN ('ACTIVE','DISABLED','UNKNOWN')", name="ck_assets_status"),
    )
    sa.Index("ix_assets_symbol", metadata.tables["assets"].c.symbol)
    sa.Table(
        "market_reports", metadata,
        sa.Column("report_id", sa.String(128), primary_key=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("freshness_status", sa.String(16), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger),
        sa.Column("source_record_fingerprint", sa.String(64), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("known_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        *timestamps(),
        sa.UniqueConstraint("source_hash", "normalization_version", name="uq_market_reports_source"),
        sa.CheckConstraint(PROVENANCE, name="ck_market_reports_provenance"),
        sa.CheckConstraint("freshness_status IN ('FRESH','STALE','NO_DATA','UNKNOWN')", name="ck_market_reports_freshness"),
    )
    sa.Index("ix_market_reports_as_of", metadata.tables["market_reports"].c.as_of.desc(), metadata.tables["market_reports"].c.report_id.desc())
    sa.Table(
        "market_asset_snapshots", metadata,
        sa.Column("snapshot_id", sa.String(128), primary_key=True),
        sa.Column("report_id", sa.String(128), sa.ForeignKey("market_reports.report_id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(128), sa.ForeignKey("assets.asset_id"), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(8, 6)),
        sa.Column("risk_level", sa.String(16)),
        sa.Column("stop_loss", sa.Numeric(30, 12)),
        sa.Column("target", sa.Numeric(30, 12)),
        sa.Column("raw_evidence_ref", sa.Text),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger, nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        sa.UniqueConstraint("report_id", "asset_id", name="uq_market_asset_snapshots_report_asset"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_market_asset_snapshots_confidence"),
        sa.CheckConstraint(PROVENANCE, name="ck_market_asset_snapshots_provenance"),
    )
    sa.Table(
        "decisions", metadata,
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column("asset_id", sa.String(128), sa.ForeignKey("assets.asset_id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_id", sa.String(128), sa.ForeignKey("market_reports.report_id")),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger, nullable=False),
        sa.Column("source_record_fingerprint", sa.String(64), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("known_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        *timestamps(),
        sa.UniqueConstraint("source_hash", "source_record_index", "normalization_version", name="uq_decisions_source_record"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_decisions_confidence"),
        sa.CheckConstraint("action IN ('BUY','SELL','HOLD','STRONG_BUY','STRONG_SELL','WAIT','NO_SIGNAL','WATCH_FOR_ENTRY')", name="ck_decisions_action"),
        sa.CheckConstraint(PROVENANCE, name="ck_decisions_provenance"),
    )
    decisions = metadata.tables["decisions"]
    sa.Index("ix_decisions_as_of", decisions.c.as_of.desc(), decisions.c.decision_id.desc())
    sa.Index("ix_decisions_asset_as_of", decisions.c.asset_id, decisions.c.as_of.desc(), decisions.c.decision_id.desc())
    sa.Index("ix_decisions_action_as_of", decisions.c.action, decisions.c.as_of.desc(), decisions.c.decision_id.desc())
    sa.Table(
        "decision_signal_snapshots", metadata,
        sa.Column("decision_id", sa.String(128), sa.ForeignKey("decisions.decision_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("close", sa.Numeric(30, 12), nullable=False),
        sa.Column("rsi_14", sa.Numeric(12, 6), nullable=False),
        sa.Column("macd_line", sa.Numeric(30, 12), nullable=False),
        sa.Column("macd_signal_line", sa.Numeric(30, 12), nullable=False),
        sa.Column("macd_histogram", sa.Numeric(30, 12), nullable=False),
        sa.Column("sma_200", sa.Numeric(30, 12), nullable=False),
        sa.Column("price_vs_sma200", sa.String(32), nullable=False),
        sa.Column("volume_24h", sa.Numeric(30, 8), nullable=False),
        sa.Column("volume_30d_avg", sa.Numeric(30, 8), nullable=False),
        sa.Column("volume_trend_ratio", sa.Numeric(20, 8), nullable=False),
        sa.Column("signal", sa.String(64)),
        sa.Column("calculated_at", sa.DateTime(timezone=True)),
    )
    sa.Table(
        "signals", metadata,
        sa.Column("signal_id", sa.String(128), primary_key=True),
        sa.Column("asset_id", sa.String(128), sa.ForeignKey("assets.asset_id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("source_report_id", sa.String(128), sa.ForeignKey("market_reports.report_id")),
        sa.Column("model_id", sa.String(128)), sa.Column("model_version", sa.String(128)),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger, nullable=False),
        sa.Column("source_record_fingerprint", sa.String(64), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        sa.UniqueConstraint("source_hash", "source_record_index", "normalization_version", name="uq_signals_source_record"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_signals_confidence"),
        sa.CheckConstraint(PROVENANCE, name="ck_signals_provenance"),
    )
    signals = metadata.tables["signals"]
    sa.Index("ix_signals_as_of", signals.c.as_of.desc(), signals.c.signal_id.desc())
    sa.Table(
        "capability_evidence", metadata,
        sa.Column("evidence_id", sa.String(128), primary_key=True),
        sa.Column("capability_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)), sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("metric", sa.Numeric(30, 12)), sa.Column("threshold", sa.Numeric(30, 12)),
        sa.Column("benchmark_run_id", sa.String(128)), sa.Column("evidence_ref", sa.Text),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        sa.CheckConstraint("status IN ('PASS','FAIL','STALE','UNKNOWN')", name="ck_capability_evidence_status"),
        sa.CheckConstraint(PROVENANCE, name="ck_capability_evidence_provenance"),
    )
    cap = metadata.tables["capability_evidence"]
    sa.Index("ix_capability_evidence_latest", cap.c.capability_id, cap.c.last_run_at.desc())
    sa.Table(
        "cost_summaries", metadata,
        sa.Column("cost_summary_id", sa.String(128), primary_key=True),
        sa.Column("evidence_quality", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total_sessions", sa.Integer, nullable=False),
        sa.Column("total_llm_calls", sa.BigInteger), sa.Column("total_tool_calls", sa.BigInteger),
        sa.Column("amount", sa.Numeric(30, 12)), sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False), sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False), sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        sa.CheckConstraint("evidence_quality IN ('EXACT','ESTIMATED','UNKNOWN')", name="ck_cost_summaries_quality"),
        sa.CheckConstraint(PROVENANCE, name="ck_cost_summaries_provenance"),
    )
    sa.Table(
        "cost_sessions", metadata,
        sa.Column("cost_session_id", sa.String(128), primary_key=True),
        sa.Column("cost_summary_id", sa.String(128), sa.ForeignKey("cost_summaries.cost_summary_id", ondelete="CASCADE"), nullable=False),
        sa.Column("session", sa.String(256), nullable=False), sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("steps", sa.Integer, nullable=False), sa.Column("llm_calls", sa.Integer, nullable=False),
        sa.Column("tool_calls", sa.Integer, nullable=False), sa.Column("decisions", sa.Integer, nullable=False),
        sa.Column("estimated_cost", sa.Numeric(30, 12), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False), sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("source_hash", "source_record_index", name="uq_cost_sessions_source_record"),
    )
    sa.Table(
        "system_status_snapshots", metadata,
        sa.Column("status_snapshot_id", sa.String(128), primary_key=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_mode", sa.String(16), nullable=False), sa.Column("effective_mode", sa.String(16), nullable=False),
        sa.Column("kill_switch_state", sa.String(16), nullable=False), sa.Column("orders_count", sa.BigInteger, nullable=False),
        sa.Column("trades_count", sa.BigInteger, nullable=False), sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False), sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False), sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("migration_run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        sa.CheckConstraint("requested_mode IN ('PAPER','DRYRUN','LIVE')", name="ck_system_status_requested_mode"),
        sa.CheckConstraint("effective_mode IN ('PAPER','DRYRUN','LIVE')", name="ck_system_status_effective_mode"),
        sa.CheckConstraint(PROVENANCE, name="ck_system_status_provenance"),
    )
    sa.Table(
        "migration_source_files", metadata,
        sa.Column("source_file_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(64), nullable=False), sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False), sa.Column("source_size", sa.BigInteger, nullable=False),
        sa.Column("source_mtime", sa.DateTime(timezone=True)), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("records_seen", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("records_invalid", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("run_id", "domain", "source_hash", name="uq_migration_source_files_identity"),
    )
    sa.Table(
        "migration_source_chunks", metadata,
        sa.Column("chunk_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False), sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("first_record_index", sa.BigInteger, nullable=False), sa.Column("last_record_index", sa.BigInteger, nullable=False),
        sa.Column("chunk_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "source_hash", "domain", "first_record_index", name="uq_migration_source_chunks_identity"),
        sa.CheckConstraint("status IN ('PENDING','COMMITTED','FAILED')", name="ck_migration_source_chunks_status"),
    )
    sa.Table(
        "migration_errors", metadata,
        sa.Column("error_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False), sa.Column("source_record_index", sa.BigInteger),
        sa.Column("error_code", sa.String(64), nullable=False), sa.Column("error_message_sanitized", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    sa.Table(
        "audit_events", metadata,
        sa.Column("audit_event_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("migration_runs.run_id")),
        sa.Column("event_code", sa.String(64), nullable=False), sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("source_path", sa.Text), sa.Column("source_record_index", sa.BigInteger),
        sa.Column("source_record_fingerprint", sa.String(64)), sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    return metadata


def upgrade() -> None:
    metadata = _metadata()
    metadata.create_all(op.get_bind(), checkfirst=False)
    op.execute("GRANT USAGE ON SCHEMA public TO trading_migrator, trading_reader")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trading_migrator")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO trading_migrator")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO trading_reader")
    op.execute("ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO trading_migrator")
    op.execute("ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO trading_migrator")
    op.execute("ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner IN SCHEMA public GRANT SELECT ON TABLES TO trading_reader")


def downgrade() -> None:
    raise NotImplementedError("Phase 3 schema rollback is restore-based")
