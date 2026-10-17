"""Structural P3 phase-exit and protected-promotion receipts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, SourceIdentity, Text, Token, Sha256, StrictModel
from packages.data_contracts import ArtifactRefV1
from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity,GithubRunIdentity


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


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("blocker codes must be an array")
    return tuple(value)


Gate = Literal["PASS", "HELD"]


class P3StatusGates(StrictModel):
    pipeline_complete: Gate
    family_disclosed: Gate
    primary_selected: Gate
    holdout_pass: Gate
    native_parity_pass: Gate
    registry_lineage: Gate
    protected_promotion: Gate
    phase_complete: Gate


class P3SourceStatus(DigestModel):
    schema_version: Literal["p3-source-status-v1"]
    qualified_source: SourceIdentity | None
    phase_exit_receipt_digest: Sha256 | None
    promotion_receipt_digest: Sha256 | None
    current_semantic_closure_digest: Sha256
    gates: P3StatusGates
    blocker_codes: Annotated[
        tuple[Annotated[str, Field(pattern=r"^E_[A-Z0-9_]+$", max_length=96)], ...],
        BeforeValidator(_tuple), Field(max_length=64),
    ]
    authority: SafeAuthority


def valid_status_output(raw: bytes) -> bool:
    try:
        P3SourceStatus.model_validate_json(raw)
        return True
    except Exception:
        return False


class PhaseExitReceiptV2(DigestModel):
    """Explicit issuer and terminal outcome; root custody/hosted attestation is external."""
    schema_version: Literal['p3-phase-exit-v2']
    source: SourceIdentity
    identity: Annotated[
        LocalRunIdentity | GithubRunIdentity,
        Field(discriminator='issuer_kind'),
    ]
    policy_digest: Sha256
    input_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1
    job_id: Text
    attempt_id: Text
    authorization_ref: ArtifactRefV1
    terminal_observation_ref: ArtifactRefV1
    publication_ref: ArtifactRefV1 | None
    closure_report_ref: ArtifactRefV1 | None
    result_ref: ArtifactRefV1 | None
    registry_head_ref: ArtifactRefV1 | None
    outcome: Literal['PASS','FAIL','NONE_QUALIFIED','HELD']
    authority: SafeAuthority

    @model_validator(mode='after')
    def _publication(self):
        committed=self.outcome in {'PASS','FAIL'}
        if any((ref is not None)!=committed for ref in
            (self.publication_ref,self.closure_report_ref,self.registry_head_ref)):
            raise ValueError('phase exit outcome and committed publication differ')
        if (self.result_ref is None)!=(self.outcome=='HELD'):
            raise ValueError('phase exit result and outcome differ')
        return self


class LocalP3Status(DigestModel):
    schema_version: Literal['p3-local-source-status-v1']
    issuer_kind: Literal['LOCAL']
    qualified_source: SourceIdentity | None
    phase_exit_receipt_digest: Sha256 | None
    source_qualification: Gate
    pipeline_complete: Gate
    economic_outcome: Literal['PASS','FAIL','NONE_QUALIFIED','HELD']
    local_complete: Gate
    protected_promotion: Literal['HELD']
    published_complete: Literal['HELD']
    blocker_codes: Annotated[tuple[Annotated[str,Field(pattern=r'^E_[A-Z0-9_]+$')],...],BeforeValidator(_tuple),Field(max_length=64)]
    authority: SafeAuthority
