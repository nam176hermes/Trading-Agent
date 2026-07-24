"""Add complete sanitized quarantine lineage.

Revision ID: 0002_quarantine_lineage
Revises: 0001_phase3_operational_store
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_quarantine_lineage"
down_revision = "0001_phase3_operational_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("migration_errors", sa.Column("source_hash", sa.String(64), nullable=False))
    op.add_column("migration_errors", sa.Column("legacy_value", sa.String(64)))
    op.add_column("migration_errors", sa.Column("normalization_version", sa.String(32), nullable=False))


def downgrade() -> None:
    raise NotImplementedError("Phase 3 schema rollback is restore-based")
