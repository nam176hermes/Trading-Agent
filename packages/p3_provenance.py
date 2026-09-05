"""Structural P3 phase-exit and protected-promotion receipts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, SourceIdentity, Text, Token
from packages.data_contracts import ArtifactRefV1


class PhaseExitReceipt(DigestModel):
    schema_version: Literal["p3-phase-exit-v1"]
    source: SourceIdentity
    epoch_id: Token
    primary_selection_ref: ArtifactRefV1
    exit_result_ref: ArtifactRefV1
    qualified_registry_head_ref: ArtifactRefV1
    closure_report_ref: ArtifactRefV1
    bundle_inventory_ref: ArtifactRefV1
    qualification_workflow: Text
    qualification_run_id: Annotated[int, Field(ge=0)]
    qualification_attempt: Annotated[int, Field(ge=0)]
    status: Literal["PASS"]
    authority: SafeAuthority


class PromotionReceipt(DigestModel):
    schema_version: Literal["p3-promotion-v1"]
    qualified_source: SourceIdentity
    promoted_source: SourceIdentity
    phase_exit_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    foundation_run_id: Annotated[int, Field(ge=0)]
    foundation_attempt: Annotated[int, Field(ge=0)]
    workflow_ref: Text
    status: Literal["PASS"]
    authority: SafeAuthority


def valid_phase_exit_output(raw: bytes) -> bool:
    try:
        PhaseExitReceipt.model_validate_json(raw)
        return True
    except Exception:
        return False


def valid_promotion_output(raw: bytes) -> bool:
    try:
        PromotionReceipt.model_validate_json(raw)
        return True
    except Exception:
        return False


__all__ = ["PhaseExitReceipt", "PromotionReceipt", "valid_phase_exit_output", "valid_promotion_output"]
