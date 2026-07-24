from __future__ import annotations

from dataclasses import dataclass

from .phase3b_sources import PHASE3B_NORMALIZATION_VERSION


APPROVED_REVISION = "0003_contract_lineage_repair"


class Phase3BApprovalRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Phase3BApprovalContext:
    approval_enabled: str
    actual_inventory_hash: str
    approved_inventory_hash: str
    actual_revision: str
    approved_revision: str
    actual_normalization_version: str
    approved_normalization_version: str
    database_host: str
    database_port: int
    database_name: str
    database_role: str
    requested_mode: str
    live_execution_enabled: str
    live_trading_approved: str
    kill_switch_active: bool
    production_credential_names: tuple[str, ...]


def validate_phase3b_approval(context: Phase3BApprovalContext) -> None:
    if context.approval_enabled.strip().lower() != "true":
        raise Phase3BApprovalRejected("Phase 3B apply approval is not enabled")
    if (
        context.actual_inventory_hash != context.approved_inventory_hash
        or len(context.actual_inventory_hash) != 64
    ):
        raise Phase3BApprovalRejected("Phase 3B source inventory is not approved")
    if (
        context.actual_revision != APPROVED_REVISION
        or context.approved_revision != APPROVED_REVISION
    ):
        raise Phase3BApprovalRejected("Phase 3B Alembic revision is not approved")
    if (
        context.actual_normalization_version != PHASE3B_NORMALIZATION_VERSION
        or context.approved_normalization_version != PHASE3B_NORMALIZATION_VERSION
    ):
        raise Phase3BApprovalRejected("Phase 3B normalization version is not approved")
    if (
        context.database_host != "127.0.0.1"
        or context.database_port != 55432
        or context.database_name != "trading_agent"
        or context.database_role != "trading_migrator"
    ):
        raise Phase3BApprovalRejected("Phase 3B database identity is not approved")
    if context.requested_mode.strip().lower() != "paper":
        raise Phase3BApprovalRejected("Phase 3B requires paper mode")
    if (
        context.live_execution_enabled.strip().lower() != "false"
        or context.live_trading_approved.strip().lower() != "false"
    ):
        raise Phase3BApprovalRejected("Phase 3B requires false live gates")
    if context.kill_switch_active:
        raise Phase3BApprovalRejected("Phase 3B requires inactive kill switch")
    if context.production_credential_names:
        raise Phase3BApprovalRejected("production credential is present in apply process")
