"""Fail-closed P3 review, custody, and run-authority contracts."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime

from .base import AlphaId, DigestModel, SafeAuthority, SemVer, Sha256, SourceIdentity, Text, Token
from .data import DateRange


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


Refs = Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple)]


class ReviewApproval(DigestModel):
    schema_version: Literal["p3-review-approval-v1"]
    source: SourceIdentity
    subject_digests: Annotated[tuple[Sha256, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=64)]
    operator_identity: Token
    reviewer_identity: Token
    review_execution_id: Token
    verdict: Literal["APPROVED", "REJECTED"]
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    evidence_ref: ArtifactRefV1
    authority: SafeAuthority


class RunAuthorization(DigestModel):
    schema_version: Literal["p3-run-authorization-v1"]
    input_set_ref: ArtifactRefV1
    review_ref: ArtifactRefV1
    operation: Literal["BASELINES", "REGISTER_FAMILY", "OOS", "HOLDOUT", "PARITY", "PHASE_EXIT"]
    allowed_alpha_ids: Annotated[tuple[AlphaId, ...], BeforeValidator(_tuple), Field(max_length=4)]
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    nonce: UUID
    issuer_workflow: Text
    issuer_run_id: Annotated[int, Field(ge=0)]
    issuer_attempt: Annotated[int, Field(ge=0)]
    authority: SafeAuthority

    @model_validator(mode="after")
    def _validity(self) -> "RunAuthorization":
        if not self.issued_at < self.expires_at <= self.issued_at + timedelta(hours=24):
            raise ValueError("run authorization validity must be positive and at most 24h")
        return self


class IntegrationReceipt(DigestModel):
    schema_version: Literal["p3-integration-qualified-v1"]
    source: SourceIdentity
    sql_proof_ref: ArtifactRefV1
    native_fixture_proof_ref: ArtifactRefV1
    cleanup_proof_ref: ArtifactRefV1
    workflow_run_id: Annotated[int, Field(ge=0)]
    workflow_attempt: Annotated[int, Field(ge=0)]
    status: Literal["PASS"]
    authority: SafeAuthority


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
    candidate_report_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=4, max_length=4)]
    trial_outcome_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=256)]
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


__all__ = ["CustodyRecord", "ExposureRecord", "FamilyReview", "HoldoutRequest", "IntegrationReceipt", "PrimarySelection", "ReviewApproval", "RunAuthorization"]
