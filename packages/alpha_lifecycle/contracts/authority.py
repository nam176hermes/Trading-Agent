"""Fail-closed P3 review, custody, and run-authority contracts."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, TypeAdapter, UUID4, model_validator

from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime

from .models import json_array, AlphaId, DigestModel, SafeAuthority, SemVer, Sha256, SourceIdentity, StrictModel, Text, Token
from .data import DateRange


Refs = Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array)]


class ReviewApproval(DigestModel):
    schema_version: Literal["p3-review-approval-v1"]
    source: SourceIdentity
    subject_digests: Annotated[tuple[Sha256, ...], BeforeValidator(json_array), Field(min_length=1, max_length=64)]
    operator_identity: Token
    reviewer_identity: Token
    review_execution_id: Token
    verdict: Literal["APPROVED", "REJECTED"]
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    evidence_ref: ArtifactRefV1
    authority: SafeAuthority


class _RunAuthorization(DigestModel):
    input_set_ref: ArtifactRefV1
    review_ref: ArtifactRefV1
    operation: Literal["BASELINES", "REGISTER_FAMILY", "OOS", "HOLDOUT", "PARITY", "PHASE_EXIT"]
    allowed_alpha_ids: Annotated[tuple[AlphaId, ...], BeforeValidator(json_array), Field(max_length=4)]
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    nonce: UUID
    authority: SafeAuthority

    @model_validator(mode="after")
    def _validity(self) -> "_RunAuthorization":
        if not self.issued_at < self.expires_at <= self.issued_at + timedelta(hours=24):
            raise ValueError("run authorization validity must be positive and at most 24h")
        return self


class RunAuthorization(_RunAuthorization):
    schema_version: Literal["p3-run-authorization-v1"]
    issuer_workflow: Text
    issuer_run_id: Annotated[int, Field(ge=0)]
    issuer_attempt: Annotated[int, Field(ge=0)]


class _IntegrationReceipt(DigestModel):
    source: SourceIdentity
    sql_proof_ref: ArtifactRefV1
    native_fixture_proof_ref: ArtifactRefV1
    cleanup_proof_ref: ArtifactRefV1
    status: Literal["PASS"]
    authority: SafeAuthority


class IntegrationReceipt(_IntegrationReceipt):
    schema_version: Literal["p3-integration-qualified-v1"]
    workflow_run_id: Annotated[int, Field(ge=0)]
    workflow_attempt: Annotated[int, Field(ge=0)]


class PrimarySelection(DigestModel):
    schema_version: Literal["p3-primary-selection-v1"]
    family_review_ref: ArtifactRefV1
    selection_policy_digest: Sha256
    primary_alpha_id: AlphaId | None
    primary_version: SemVer | None
    primary_candidate_head_ref: ArtifactRefV1 | None
    outcome: Literal["SELECTED", "NONE_QUALIFIED"]

    @model_validator(mode="after")
    def _selection(self) -> "PrimarySelection":
        values = (self.primary_alpha_id, self.primary_version, self.primary_candidate_head_ref)
        if (self.outcome == "SELECTED") != all(value is not None for value in values):
            raise ValueError("primary selection outcome and candidate fields disagree")
        if self.outcome == "NONE_QUALIFIED" and any(value is not None for value in values):
            raise ValueError("no-qualified outcome cannot name a candidate")
        return self


class FamilyReview(DigestModel):
    schema_version: Literal["p3-family-review-v1"]
    input_set_ref: ArtifactRefV1
    candidate_report_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=4, max_length=4)]
    trial_outcome_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=1, max_length=256)]
    review_ref: ArtifactRefV1
    complete_disclosure: Literal[True]


class HoldoutRequest(DigestModel):
    schema_version: Literal["p3-holdout-request-v1"]
    source: SourceIdentity
    primary_selection_ref: ArtifactRefV1
    policy_digest: Sha256
    holdout_input_set_ref: ArtifactRefV1
    custody_record_ref: ArtifactRefV1
    review_ref: ArtifactRefV1
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    logical_trial_id: Token
    authority: SafeAuthority

    @model_validator(mode="after")
    def _validity(self) -> "HoldoutRequest":
        if not self.issued_at < self.expires_at <= self.issued_at + timedelta(hours=24):
            raise ValueError("holdout request validity must be positive and at most 24h")
        return self


class CustodyRecord(DigestModel):
    schema_version: Literal["p3-custody-record-v1"]
    holdout_commitment: Sha256
    ciphertext_ref: ArtifactRefV1
    plaintext_bundle_digest: Sha256
    custodian_identity: Token
    research_identity: Token
    custodian_attestation_ref: ArtifactRefV1
    access_policy_digest: Sha256
    classification: Literal["HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND"]

    @model_validator(mode="after")
    def _separation(self) -> "CustodyRecord":
        if self.custodian_identity == self.research_identity:
            raise ValueError("custodian and research identities must be distinct")
        return self


class ExposureRecord(DigestModel):
    schema_version: Literal["p3-exposure-record-v1"]
    epoch_ref: ArtifactRefV1
    exposed_range: DateRange
    reason: Literal["OOS_RESULT", "HOLDOUT_RESULT", "EXTERNAL_PRIOR_KNOWLEDGE", "INFRA_DIAGNOSTIC"]
    outcome_ref: ArtifactRefV1 | None
    recorded_at: CanonicalUtcDateTime
    actor: Token


class LocalRunIdentity(StrictModel):
    issuer_kind: Literal["LOCAL"]
    issuer_identity: Token
    run_id: UUID4
    attempt: Annotated[int, Field(ge=1)]
    host_id: Sha256
    boot_id: UUID


class LocalRunAuthorization(_RunAuthorization):
    schema_version: Literal['p3-local-run-authorization-v1']
    identity: LocalRunIdentity


class LocalIntegrationReceipt(_IntegrationReceipt):
    schema_version: Literal['p3-local-integration-qualified-v1']
    identity: LocalRunIdentity


RunAuthorizationValue = Annotated[RunAuthorization | LocalRunAuthorization, Field(discriminator='schema_version')]
IntegrationReceiptValue = Annotated[IntegrationReceipt | LocalIntegrationReceipt, Field(discriminator='schema_version')]
RUN_AUTHORIZATION = TypeAdapter(RunAuthorizationValue)
INTEGRATION_RECEIPT = TypeAdapter(IntegrationReceiptValue)


class GithubRunIdentity(StrictModel):
    """Structural identity only; this does not authenticate a hosted run."""

    issuer_kind: Literal["GITHUB"]
    issuer_identity: Token
    repository: Literal["nam176hermes/Trading-Agent"]
    workflow_ref: Text
    run_id: Annotated[int, Field(ge=1)]
    attempt: Annotated[int, Field(ge=1)]


class QualificationBinding(StrictModel):
    gate: Literal[
        "HWC_PORTABLE_QUALIFIED", "P1_H_COMPLETE", "P1_LTS_READY",
        "P2_SOURCE_COMPLETE", "P2_RUNTIME_QUALIFIED", "P2_QUALIFIED",
        "ALPHA_REGISTRY_FOUNDATION", "P3_BASELINES_FROZEN",
        "P3_EVALUATION_PROTOCOL_FROZEN", "PRE_P3_READY", "P3_INTEGRATION", "P3_PHASE_EXIT",
    ]
    source: SourceIdentity
    policy_digest: Sha256
    input_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1


class QualificationReceipt(DigestModel):
    """Retained qualification evidence; digest validity is not authentication."""

    schema_version: Literal["local-phase-qualification-receipt-v1"]
    identity: Annotated[LocalRunIdentity | GithubRunIdentity, Field(discriminator="issuer_kind")]
    binding: QualificationBinding
    started_at: CanonicalUtcDateTime
    completed_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    evidence_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=1, max_length=64)]
    status: Literal["PASS"]
    authority: SafeAuthority

    @model_validator(mode="after")
    def _qualification_bounds(self) -> "QualificationReceipt":
        if not self.started_at <= self.completed_at < self.expires_at <= self.started_at + timedelta(hours=24):
            raise ValueError("qualification validity is unordered or exceeds 24h")
        if len({ref.content_sha256 for ref in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError("qualification evidence repeats")
        return self


class LocalQualificationApproval(DigestModel):
    """Current protected issuer decision pinning one immutable receipt."""

    schema_version: Literal["local-qualification-approval-v1"]
    issuer_identity: Token
    receipt_ref: ArtifactRefV1
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    revoked: bool
    authority: SafeAuthority

    @model_validator(mode="after")
    def _approval_validity(self) -> "LocalQualificationApproval":
        if not self.issued_at < self.expires_at <= self.issued_at + timedelta(hours=24):
            raise ValueError("local approval validity must be positive and at most 24h")
        return self


__all__ = ["LocalRunAuthorization", "LocalIntegrationReceipt", "LocalRunIdentity", "GithubRunIdentity", "QualificationBinding", "QualificationReceipt", "LocalQualificationApproval", "CustodyRecord", "ExposureRecord", "FamilyReview", "HoldoutRequest", "IntegrationReceipt", "PrimarySelection", "ReviewApproval", "RunAuthorization"]
