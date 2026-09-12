"""Protected request validation for fixed P3 workflow operations."""

from __future__ import annotations

import os
import hashlib
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.alpha_lifecycle.contracts.authority import ReviewApproval, RunAuthorization
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity, Text
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes
from packages.job_contracts import AlphaCampaignOperation, AlphaCampaignPayload


WORKFLOW_OPERATIONS = {
    "p3-integration-fixture-v1": "PARITY",
    "p3-baselines-v1": "BASELINES",
    "p3-register-family-v1": "REGISTER_FAMILY",
    "p3-oos-a0-v1": "OOS",
    "p3-oos-a1-v1": "OOS",
    "p3-oos-a2-v1": "OOS",
    "p3-oos-a3-v1": "OOS",
    "p3-select-primary-v1": "OOS",
    "p3-holdout-primary-v1": "HOLDOUT",
    "p3-native-parity-v1": "PARITY",
    "p3-phase-exit-v1": "PHASE_EXIT",
}


class AuthorityHeld(RuntimeError):
    """Required protected source, reviewer, or host authority is unavailable."""


class _FixtureAuthorization(DigestModel):
    """Private fixture request; it cannot authorize an official research operation."""

    schema_version: Literal["p3-fixture-authorization-v1"]
    fixture_plan_ref: ArtifactRefV1
    review_ref: ArtifactRefV1
    operation: Literal["PARITY"]
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    nonce: UUID
    issuer_workflow: Text
    issuer_run_id: Annotated[int, Field(ge=1)]
    issuer_attempt: Annotated[int, Field(ge=1)]
    authority: SafeAuthority

    @model_validator(mode="after")
    def _validity(self) -> "_FixtureAuthorization":
        if not self.issued_at < self.expires_at <= self.issued_at + timedelta(hours=24):
            raise ValueError("fixture authorization validity must be positive and at most 24h")
        return self


class _RequestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["p3-authority-request-file-v1"]
    execution_source: SourceIdentity
    authorization: Annotated[RunAuthorization | _FixtureAuthorization, Field(discriminator="schema_version")]
    operation_input: P3OperationInput | None = None


def validate_workflow_operation(value: str) -> str:
    try:
        return WORKFLOW_OPERATIONS[value]
    except (KeyError, TypeError) as error:
        raise AuthorityHeld("HELD E_OPERATION: workflow operation is not allowlisted") from error


def validate_request(
    path: Path,
    expected_source: SourceIdentity | None,
    operation: str,
) -> _RequestFile:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
    except OSError as error:
        raise AuthorityHeld("HELD E_AUTHORITY: request file is unavailable") from error
    try:
        if (
            not path.is_absolute()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or not 1 <= info.st_size <= 65_536
        ):
            raise AuthorityHeld("HELD E_AUTHORITY: request file must be private regular 0600")
        raw = os.read(descriptor, info.st_size + 1)
        if len(raw) != info.st_size:
            raise AuthorityHeld("HELD E_AUTHORITY: request file identity changed")
        request = _RequestFile.model_validate_json(raw)
    except AuthorityHeld:
        raise
    except Exception as error:
        raise AuthorityHeld("HELD E_AUTHORITY: request contract is invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if expected_source is None or request.execution_source != expected_source:
        raise AuthorityHeld("HELD E_SOURCE: request is not bound to current source")
    if request.authorization.operation != operation:
        raise AuthorityHeld("HELD E_OPERATION: authorization operation differs")
    if (
        request.authorization.issuer_workflow != "p3-authority.yml"
        or request.authorization.issuer_run_id < 1
        or request.authorization.issuer_attempt < 1
    ):
        raise AuthorityHeld("HELD E_AUTHORITY: authorization issuer is invalid")
    now = datetime.now(UTC)
    if not request.authorization.issued_at <= now < request.authorization.expires_at:
        raise AuthorityHeld("HELD E_AUTHORITY: authorization is not current")
    if isinstance(request.authorization, _FixtureAuthorization):
        if request.operation_input is not None:
            raise AuthorityHeld("HELD E_AUTHORITY: fixture cannot carry an official operation input")
    elif request.operation_input is None:
        raise AuthorityHeld("HELD E_OPERATION: official operation input is required")
    if isinstance(request.authorization, RunAuthorization):
        expected_context = {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "nam176hermes/Trading-Agent",
            "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": expected_source.commit_sha,
            "GITHUB_REF_PROTECTED": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_RUN_ID": str(request.authorization.issuer_run_id),
            "GITHUB_RUN_ATTEMPT": str(request.authorization.issuer_attempt),
            "GITHUB_WORKFLOW_REF": "nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main",
        }
        if any(os.environ.get(key) != value for key, value in expected_context.items()):
            raise AuthorityHeld("HELD E_ISSUER: request differs from the protected-main workflow context")
    return request


def build_alpha_campaign_payload(
    authorization: RunAuthorization | _FixtureAuthorization,
    source: SourceIdentity,
    workflow_operation: str,
    *, operation_input: P3OperationInput | None = None,
) -> AlphaCampaignPayload:
    """Build the one closed worker payload represented by a protected request."""

    operation = validate_workflow_operation(workflow_operation)
    if authorization.operation != operation:
        raise AuthorityHeld("HELD E_OPERATION: authorization operation differs")
    fixture = isinstance(authorization, _FixtureAuthorization)
    if fixture != (workflow_operation == "p3-integration-fixture-v1"):
        raise AuthorityHeld("HELD E_AUTHORITY: fixture and research authorization scopes differ")
    if fixture:
        if operation_input is not None:
            raise AuthorityHeld("HELD E_OPERATION: fixture cannot carry an official operation input")
        manifest_ref = authorization.fixture_plan_ref
    else:
        if operation_input is None:
            raise AuthorityHeld("HELD E_OPERATION: official operation input is required")
        operation_input = P3OperationInput.model_validate(operation_input)
        if (operation_input.workflow_operation != workflow_operation
            or operation_input.operation != authorization.operation
            or operation_input.input_set_ref != authorization.input_set_ref
            or operation_input.allowed_alpha_ids != authorization.allowed_alpha_ids):
            raise AuthorityHeld("HELD E_OPERATION: operation input differs from authorization")
        intent_raw = canonical_json_bytes(operation_input)
        intent_digest = hashlib.sha256(intent_raw).hexdigest()
        manifest_ref = ArtifactRefV1(content_sha256=intent_digest, size_bytes=len(intent_raw),
            media_type="application/json", locator=f"{intent_digest}.blob")
    raw = canonical_json_bytes(authorization)
    digest = hashlib.sha256(raw).hexdigest()
    authorization_ref = ArtifactRefV1(
        content_sha256=digest,
        size_bytes=len(raw),
        media_type="application/json",
        locator=f"{digest}.blob",
    )
    return AlphaCampaignPayload(
        schema_version="p3-alpha-campaign-payload-v1",
        operation=AlphaCampaignOperation(operation),
        manifest_ref=manifest_ref,
        authorization_ref=authorization_ref,
        expected_source=source,
        logical_trial_id=workflow_operation,
    )


def stage_alpha_campaign_payload(store, authorization, source, workflow_operation, manifest_bytes, *, operation_input=None):
    """Publish exact approved input bytes to CAS and read them back before enqueue."""
    payload = build_alpha_campaign_payload(authorization, source, workflow_operation, operation_input=operation_input)
    reference = payload.manifest_ref
    if (len(manifest_bytes) != reference.size_bytes
        or hashlib.sha256(manifest_bytes).hexdigest() != reference.content_sha256):
        raise AuthorityHeld("HELD E_MANIFEST: staged manifest differs from approval")
    review_raw = store.read_bytes(authorization.review_ref)
    if operation_input is not None:
        try:
            review = ReviewApproval.model_validate_json(review_raw)
            now = datetime.now(UTC)
            if (canonical_json_bytes(review) != review_raw
                or review.source != source or review.verdict != "APPROVED"
                or review.operator_identity == review.reviewer_identity
                or not review.issued_at <= authorization.issued_at <= now < authorization.expires_at <= review.expires_at
                or operation_input.digest not in review.subject_digests):
                raise ValueError("operation input is not covered by a current independent review")
            store.read_bytes(review.evidence_ref)
            from packages.alpha_lifecycle.operation_input import SelectPrimaryInput
            if isinstance(operation_input.body,SelectPrimaryInput):
                from packages.alpha_lifecycle.baseline_campaign import _read
                from packages.alpha_lifecycle.contracts.authority import FamilyReview
                from packages.alpha_lifecycle.primary_selection import validate_family_review_approval
                family=_read(store,operation_input.body.family_review_ref,FamilyReview)
                inner=validate_family_review_approval(family,source,store)
                if (family.input_set_ref!=operation_input.input_set_ref
                    or inner.operator_identity!=review.operator_identity
                    or not inner.issued_at<=authorization.issued_at<=now<authorization.expires_at<=inner.expires_at):
                    raise ValueError('family review is not current for this authorized selection')
        except (ValueError, OSError) as error:
            raise AuthorityHeld("HELD E_REVIEW_AUTHORITY: operation review is invalid") from error
    manifest_ref = store.put_bytes(manifest_bytes,media_type=reference.media_type)
    raw = canonical_json_bytes(authorization)
    authorization_ref = store.put_bytes(raw,media_type="application/json")
    if (manifest_ref != reference or authorization_ref != payload.authorization_ref
        or store.read_bytes(reference) != manifest_bytes
        or store.read_bytes(authorization_ref) != raw):
        raise AuthorityHeld("HELD E_CAS: approved inputs failed read-back verification")
    return payload


__all__ = [
    "AuthorityHeld", "WORKFLOW_OPERATIONS", "build_alpha_campaign_payload",
    "validate_request", "validate_workflow_operation",
]
