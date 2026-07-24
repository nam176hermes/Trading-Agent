"""Repair Phase 3 contract values and source lineage.

Revision ID: 0003_contract_lineage_repair
Revises: 0002_quarantine_lineage
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_contract_lineage_repair"
down_revision = "0002_quarantine_lineage"
branch_labels = None
depends_on = None

PROVENANCE = "('EXACT','DERIVED','LEGACY_ESTIMATED','UNKNOWN')"
REASONS = "(" + ",".join(
    f"'{item}'" for item in (
        "SOURCE_FIELD_MISSING",
        "SOURCE_LINK_NOT_FOUND",
        "AMBIGUOUS_SOURCE_MATCH",
        "PRICE_TIMESTAMP_MISMATCH",
        "SNIPPET_SOURCE_MISSING",
        "SYMBOL_EVIDENCE_MISSING",
        "UNKNOWN_ASSET",
        "LOWER_QUALITY_SOURCE_IGNORED",
        "EQUAL_QUALITY_CONFLICT",
    )
) + ")"


def upgrade() -> None:
    op.add_column(
        "decisions", sa.Column("price_at_decision", sa.Numeric(30, 12))
    )
    op.add_column(
        "decisions",
        sa.Column(
            "price_provenance_quality", sa.String(32), nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column("decisions", sa.Column("report_snippet", sa.Text()))
    op.add_column(
        "decisions",
        sa.Column(
            "snippet_provenance_quality", sa.String(32), nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.create_check_constraint(
        "ck_decisions_price_provenance", "decisions",
        f"price_provenance_quality IN {PROVENANCE}",
    )
    op.create_check_constraint(
        "ck_decisions_snippet_provenance", "decisions",
        f"snippet_provenance_quality IN {PROVENANCE}",
    )

    op.add_column(
        "cost_sessions",
        sa.Column(
            "symbols_provenance_quality", sa.String(32), nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "cost_sessions",
        sa.Column(
            "symbols_evidence_state", sa.String(16), nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.create_check_constraint(
        "ck_cost_sessions_symbols_provenance", "cost_sessions",
        f"symbols_provenance_quality IN {PROVENANCE}",
    )
    op.create_check_constraint(
        "ck_cost_sessions_symbols_state", "cost_sessions",
        "symbols_evidence_state IN ('EVIDENCED','UNKNOWN')",
    )

    op.create_table(
        "phase3b_backfill_runs",
        sa.Column("backfill_run_id", sa.String(36), primary_key=True),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("source_root", sa.Text(), nullable=False),
        sa.Column("source_inventory_hash", sa.String(64), nullable=False),
        sa.Column("code_commit", sa.String(64), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("rows_seen", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_updated", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_unchanged", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_unknown", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_conflicted", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "domain IN ('decision-price','decision-snippet','cost-symbols','asset-lineage')",
            name="ck_phase3b_backfill_runs_domain",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED')",
            name="ck_phase3b_backfill_runs_status",
        ),
    )

    op.create_table(
        "decision_field_lineage",
        sa.Column("lineage_id", sa.String(64), primary_key=True),
        sa.Column(
            "decision_id", sa.String(128),
            sa.ForeignKey("decisions.decision_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("field_name", sa.String(32), nullable=False),
        sa.Column("value_text", sa.Text()),
        sa.Column("value_numeric", sa.Numeric(30, 12)),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger(), nullable=False),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("canonical_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column(
            "backfill_run_id", sa.String(36),
            sa.ForeignKey("phase3b_backfill_runs.backfill_run_id"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "decision_id", "field_name", "source_hash", "source_record_index",
            "normalization_version", name="uq_decision_field_lineage_identity",
        ),
        sa.CheckConstraint(
            "field_name IN ('price_at_decision','report_snippet')",
            name="ck_decision_field_lineage_field",
        ),
        sa.CheckConstraint(
            f"provenance_quality IN {PROVENANCE}",
            name="ck_decision_field_lineage_provenance",
        ),
        sa.CheckConstraint(
            "(field_name='price_at_decision' AND value_text IS NULL) OR "
            "(field_name='report_snippet' AND value_numeric IS NULL)",
            name="ck_decision_field_lineage_value_shape",
        ),
    )
    op.create_index(
        "ix_decision_field_lineage_decision", "decision_field_lineage",
        ["decision_id", "field_name"],
    )

    op.create_table(
        "cost_session_assets",
        sa.Column("cost_session_asset_id", sa.String(64), primary_key=True),
        sa.Column(
            "cost_session_id", sa.String(128),
            sa.ForeignKey("cost_sessions.cost_session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id", sa.String(128), sa.ForeignKey("assets.asset_id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger(), nullable=False),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("canonical_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "backfill_run_id", sa.String(36),
            sa.ForeignKey("phase3b_backfill_runs.backfill_run_id"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "cost_session_id", "asset_id", "source_hash", "source_record_index",
            "normalization_version", name="uq_cost_session_assets_identity",
        ),
        sa.CheckConstraint(
            f"provenance_quality IN {PROVENANCE}",
            name="ck_cost_session_assets_provenance",
        ),
    )
    op.create_index(
        "ix_cost_session_assets_asset", "cost_session_assets", ["asset_id"]
    )

    op.create_table(
        "asset_source_lineage",
        sa.Column("asset_source_lineage_id", sa.String(64), primary_key=True),
        sa.Column(
            "asset_id", sa.String(128), sa.ForeignKey("assets.asset_id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_record_index", sa.BigInteger()),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("normalization_version", sa.String(32), nullable=False),
        sa.Column("provenance_quality", sa.String(32), nullable=False),
        sa.Column("canonical_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "backfill_run_id", sa.String(36),
            sa.ForeignKey("phase3b_backfill_runs.backfill_run_id"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "asset_id", "source_type", "source_path", "source_hash",
            "source_record_index", "source_field", "normalization_version",
            name="uq_asset_source_lineage_identity",
        ),
        sa.CheckConstraint(
            f"provenance_quality IN {PROVENANCE}",
            name="ck_asset_source_lineage_provenance",
        ),
    )
    op.create_index(
        "ix_asset_source_lineage_asset", "asset_source_lineage",
        ["asset_id", "source_type"],
    )

    op.create_table(
        "phase3b_backfill_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column(
            "backfill_run_id", sa.String(36),
            sa.ForeignKey("phase3b_backfill_runs.backfill_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("field_name", sa.String(32)),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32)),
        sa.Column("source_path", sa.Text()),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("source_record_index", sa.BigInteger()),
        sa.Column("incoming_fingerprint", sa.String(64)),
        sa.Column("stored_fingerprint", sa.String(64)),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"reason_code IN {REASONS}", name="ck_phase3b_backfill_events_reason"
        ),
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "phase3b_backfill_runs, decision_field_lineage, cost_session_assets, "
        "asset_source_lineage, phase3b_backfill_events TO trading_migrator"
    )
    op.execute(
        "GRANT SELECT ON phase3b_backfill_runs, decision_field_lineage, "
        "cost_session_assets, asset_source_lineage, phase3b_backfill_events "
        "TO trading_reader"
    )


def downgrade() -> None:
    raise NotImplementedError("Phase 3B schema rollback is restore-based")
