from __future__ import annotations

from dataclasses import dataclass

from .writer import ApplyRejected

APPROVED_SOURCE_ROOT = "/home/thenam176/.hermes/crypto-research"
APPROVED_INVENTORY_HASH = (
    "dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce"
)
APPROVED_NORMALIZATION_VERSION = "phase3-v1"
APPROVED_ALEMBIC_REVISION = "0002_quarantine_lineage"


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    explicit_apply: bool
    source_root: str
    actual_inventory_hash: str
    approval_enabled: str
    approved_inventory_hash: str
    normalization_version: str
    approved_normalization_version: str
    alembic_revision: str
    approved_alembic_revision: str
    database_host: str
    database_port: int
    database_name: str
    database_role: str
    expected_canonical_rows: int
    actual_canonical_rows: int
    expected_quarantine_rows: int
    actual_quarantine_rows: int
    requested_mode: str
    live_execution_enabled: str
    live_trading_approved: str
    kill_switch_active: bool
    production_credential_names: tuple[str, ...]


def validate_real_apply_approval(context: ApprovalContext) -> None:
    if not context.explicit_apply:
        raise ApplyRejected("explicit --apply is required")
    if context.source_root != APPROVED_SOURCE_ROOT:
        raise ApplyRejected("approved source root does not match")
    if context.approval_enabled.strip().lower() != "true":
        raise ApplyRejected("explicit real apply approval is not enabled")
    if (
        context.actual_inventory_hash != APPROVED_INVENTORY_HASH
        or context.approved_inventory_hash != APPROVED_INVENTORY_HASH
    ):
        raise ApplyRejected("approved source inventory does not match")
    if (
        context.normalization_version != APPROVED_NORMALIZATION_VERSION
        or context.approved_normalization_version != APPROVED_NORMALIZATION_VERSION
    ):
        raise ApplyRejected("approved normalization version does not match")
    if (
        context.alembic_revision != APPROVED_ALEMBIC_REVISION
        or context.approved_alembic_revision != APPROVED_ALEMBIC_REVISION
    ):
        raise ApplyRejected("approved Alembic revision does not match")
    if (
        context.database_host,
        context.database_port,
        context.database_name,
        context.database_role,
    ) != ("127.0.0.1", 55432, "trading_agent", "trading_migrator"):
        raise ApplyRejected("target database identity does not match")
    if (
        context.actual_canonical_rows != context.expected_canonical_rows
        or context.actual_quarantine_rows != context.expected_quarantine_rows
    ):
        raise ApplyRejected("target pre-apply counts do not match")
    if context.requested_mode.strip().lower() != "paper":
        raise ApplyRejected("requested mode must remain paper")
    if any(
        value.strip().lower() != "false"
        for value in (
            context.live_execution_enabled,
            context.live_trading_approved,
        )
    ):
        raise ApplyRejected("both live gates must remain false")
    if context.kill_switch_active:
        raise ApplyRejected("kill switch must remain inactive")
    if context.production_credential_names:
        raise ApplyRejected("production execution credential is present")
