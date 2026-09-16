"""P3 publication-ordering contracts."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime

from .models import json_array, AlphaId, DigestModel, SemVer, Sha256, StrictModel, Text, Token


Refs = Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=1, max_length=8)]
Stage = Literal["REGISTER", "RESEARCH_DECISION", "EXIT_DECISION"]


class ExpectedHead(StrictModel):
    alpha_id: AlphaId
    version: SemVer
    sequence: Annotated[int, Field(ge=0)]
    event_digest: Sha256 | None

    @model_validator(mode="after")
    def _head(self) -> "ExpectedHead":
        if (self.sequence == 0) != (self.event_digest is None):
            raise ValueError("expected head sequence zero iff digest is null")
        return self


class PrePublicationEvidence(DigestModel):
    schema_version: Literal["p3-pre-publication-evidence-v1"]
    stage: Stage
    input_set_ref: ArtifactRefV1
    baseline_selection_ref: ArtifactRefV1
    qualification_bundle_ref: ArtifactRefV1 | None
    exit_result_ref: ArtifactRefV1 | None
    candidate_record_refs: Refs

    @model_validator(mode="after")
    def _stage_refs(self) -> "PrePublicationEvidence":
        expected = {
            "REGISTER": (False, False),
            "RESEARCH_DECISION": (True, False),
            "EXIT_DECISION": (True, True),
        }[self.stage]
        actual = (self.qualification_bundle_ref is not None, self.exit_result_ref is not None)
        if actual != expected:
            raise ValueError("pre-publication references do not match stage")
        return self


class PublicationRequest(DigestModel):
    schema_version: Literal["p3-publication-request-v1"]
    idempotency_key: Token
    semantic_request_digest: Sha256
    stage: Stage
    evidence_ref: ArtifactRefV1
    expected_heads: Annotated[tuple[ExpectedHead, ...], BeforeValidator(json_array), Field(min_length=1, max_length=4)]
    proposed_event_refs: Refs
    job_id: Text


class PublicationReceipt(DigestModel):
    schema_version: Literal["p3-publication-receipt-v1"]
    request_ref: ArtifactRefV1
    ledger_event_ids: Annotated[tuple[UUID, ...], BeforeValidator(json_array), Field(min_length=1, max_length=8)]
    registry_event_refs: Refs
    commit_result_ref: ArtifactRefV1
    committed_at: CanonicalUtcDateTime
    outcome: Literal["COMMITTED"]


class CampaignClosureReport(DigestModel):
    schema_version: Literal["p3-campaign-closure-report-v1"]
    stage: Stage
    prepublication_ref: ArtifactRefV1
    publication_ref: ArtifactRefV1
    qualification_ref: ArtifactRefV1 | None
    exit_result_ref: ArtifactRefV1 | None
    projection_digest: Sha256 | None
    projection_status: Literal["READY", "PENDING"]


class RegistrationProof(DigestModel):
    schema_version: Literal["p3-registration-proof-v1"]
    input_set_ref: ArtifactRefV1
    baseline_selection_ref: ArtifactRefV1
    candidate_head_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=4, max_length=4)]
    publication_ref: ArtifactRefV1


class JobCommitResult(DigestModel):
    schema_version: Literal["p3-job-commit-result-v1"]
    job_id: Text
    idempotency_key: Token
    semantic_request_digest: Sha256
    prepublication_ref: ArtifactRefV1
    ledger_event_ids: Annotated[
        tuple[UUID, ...], BeforeValidator(json_array), Field(min_length=1, max_length=8)
    ]
    registry_event_refs: Annotated[
        tuple[ArtifactRefV1, ...], BeforeValidator(json_array), Field(min_length=1, max_length=8)
    ]
    alpha_outcome: Literal["NOT_EVALUATED", "PASS", "FAIL"]

    def bound_to(
        self, request: PublicationRequest, *, ledger_event_ids: tuple[UUID, ...] | None = None
    ) -> "JobCommitResult":
        """Validate response identity; a valid digest alone does not bind a commit."""
        if (
            self.job_id != request.job_id
            or self.idempotency_key != request.idempotency_key
            or self.semantic_request_digest != request.semantic_request_digest
            or self.prepublication_ref != request.evidence_ref
            or self.registry_event_refs != request.proposed_event_refs
            or len(self.ledger_event_ids) != len(self.registry_event_refs)
            or len(set(self.ledger_event_ids)) != len(self.ledger_event_ids)
            or (self.alpha_outcome == "NOT_EVALUATED") != (request.stage == "REGISTER")
            or (ledger_event_ids is not None and self.ledger_event_ids != ledger_event_ids)
        ):
            raise ValueError("P3 commit result does not match publication request")
        return self


__all__ = ["CampaignClosureReport", "ExpectedHead", "PrePublicationEvidence", "PublicationReceipt", "PublicationRequest", "RegistrationProof"]
