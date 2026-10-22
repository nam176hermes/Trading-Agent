"""Protected request validation for fixed P3 workflow operations."""

from __future__ import annotations

import os
import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from packages.alpha_lifecycle.contracts.authority import ReviewApproval, RunAuthorization, LocalRunAuthorization, LocalRunIdentity
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


class _FixtureAuthorizationFields(DigestModel):
    """Private fixture request; it cannot authorize an official research operation."""

    fixture_plan_ref: ArtifactRefV1
    review_ref: ArtifactRefV1
    operation: Literal["PARITY"]
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    nonce: UUID
    authority: SafeAuthority

    @model_validator(mode="after")
    def _validity(self) -> "_FixtureAuthorizationFields":
        if not self.issued_at < self.expires_at <= self.issued_at + timedelta(hours=24):
            raise ValueError("fixture authorization validity must be positive and at most 24h")
        return self


class _FixtureAuthorization(_FixtureAuthorizationFields):
    schema_version: Literal['p3-fixture-authorization-v1']
    issuer_workflow: Text
    issuer_run_id: Annotated[int, Field(ge=1)]
    issuer_attempt: Annotated[int, Field(ge=1)]


class _LocalFixtureAuthorization(_FixtureAuthorizationFields):
    schema_version: Literal['p3-local-fixture-authorization-v1']
    identity: LocalRunIdentity


FIXTURE_AUTHORIZATION = TypeAdapter(Annotated[_FixtureAuthorization | _LocalFixtureAuthorization,
    Field(discriminator='schema_version')])


class _RequestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["p3-authority-request-file-v1"]
    execution_source: SourceIdentity
    authorization: Annotated[RunAuthorization | _FixtureAuthorization, Field(discriminator="schema_version")]
    operation_input: P3OperationInput | None = None


class _LocalRequestFile(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True,strict=True)
    schema_version: Literal['p3-local-authority-request-v1']
    execution_source: SourceIdentity
    authorization: Annotated[LocalRunAuthorization | _LocalFixtureAuthorization,Field(discriminator='schema_version')]
    operation_input: P3OperationInput | None = None


_LOCAL_ROOT = Path('/etc/trading-agent/local-qualification')
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def local_run_directory(identity: LocalRunIdentity) -> Path:
    identity = LocalRunIdentity.model_validate(identity)
    return Path('/run/trading-agent-p3/local')/identity.issuer_identity/f'{identity.run_id}-{identity.attempt}'


def validate_local_operation_authority(authorization, source, *, root=_SOURCE_ROOT):
    """Fresh protected request plus source qualification; no self-issued approval."""
    path = _LOCAL_ROOT/authorization.identity.issuer_identity/'operations'/f'{authorization.nonce}.json'
    request = _validate_local_request(path,source,authorization.operation,root=root)
    if request.authorization != authorization:
        raise AuthorityHeld('HELD E_AUTHORITY: local protected operation changed')
    return request


def _validate_local_request(path, source, operation, *, root=_SOURCE_ROOT):
    from packages.runtime_release.config import read_protected_canonical_json_current, ProtectedAuthorityError
    from packages.alpha_lifecycle.local_qualification import _host_identity
    from packages.project_status import require_p3_source_ready
    try:
        document,digest = read_protected_canonical_json_current(path)
        request = _LocalRequestFile.model_validate_json(canonical_json_bytes(document))
        auth = request.authorization
        expected = _LOCAL_ROOT/auth.identity.issuer_identity/'operations'/f'{auth.nonce}.json'
        now = datetime.now(UTC)
        if path != expected or request.execution_source != source or auth.operation != operation:
            raise ValueError('local request source, operation or protected path differs')
        if not auth.issued_at <= now < auth.expires_at:
            raise ValueError('local operation authority is not current')
        if _host_identity() != (auth.identity.host_id,auth.identity.boot_id):
            raise ValueError('local operation host or boot differs')
        if isinstance(auth,_LocalFixtureAuthorization) != (request.operation_input is None):
            raise ValueError('local fixture and research inputs differ')
        prerequisite = require_p3_source_ready(root,auth.identity)
        if (prerequisite is None or prerequisite.binding.source != source or auth.expires_at > prerequisite.expires_at):
            raise ValueError('local operation source or validity exceeds qualification')
        if request.operation_input is not None:
            # Official campaigns additionally need their InputSet admitted. The
            # fixture lane never reaches here, so it can never certify official
            # research. Every boundary re-runs this; it is not a lease.
            from packages.alpha_lifecycle.local_research import read_local_research_inputs
            from packages.local_pre_p3 import POLICY_PATH
            research = read_local_research_inputs(auth.identity,source=source,
                policy_digest=hashlib.sha256(canonical_json_bytes(
                    json.loads((root/POLICY_PATH).read_bytes()))).hexdigest(),
                input_set_ref=request.operation_input.input_set_ref)
            if auth.expires_at > research.expires_at:
                raise ValueError('local operation validity exceeds research inputs qualification')
        if (read_protected_canonical_json_current(path)[1] != digest
            or _host_identity() != (auth.identity.host_id,auth.identity.boot_id)
            or not now <= datetime.now(UTC) < auth.expires_at):
            raise ValueError('local operation authority changed during admission')
        return request
    except (OSError,ValueError,ProtectedAuthorityError) as error:
        raise AuthorityHeld('HELD E_LOCAL_AUTHORITY: '+str(error)) from error


def validate_workflow_operation(value: str) -> str:
    try:
        return WORKFLOW_OPERATIONS[value]
    except (KeyError, TypeError) as error:
        raise AuthorityHeld("HELD E_OPERATION: workflow operation is not allowlisted") from error


def validate_request(
    path: Path,
    expected_source: SourceIdentity | None,
    operation: str,
) -> _RequestFile | _LocalRequestFile:
    if path.is_relative_to(_LOCAL_ROOT):
        return _validate_local_request(path,expected_source,operation)
    from packages.alpha_lifecycle.github_run import RUN_ROOT
    if path.is_relative_to(RUN_ROOT):
        return _validate_github_request(path,expected_source,operation)
    # GITHUB requests are published by the protected host controller only; a
    # caller-owned request file can no longer carry GitHub authority.
    raise AuthorityHeld("HELD E_AUTHORITY: request must be root-published at its protected path")


def _validate_github_request(path: Path, source: SourceIdentity | None, operation: str) -> _RequestFile:
    """Root-published request at the run-derived path of an API-attested run."""
    from packages.runtime_release.config import read_protected_canonical_json_current, ProtectedAuthorityError
    from packages.alpha_lifecycle.github_run import github_run_directory, GithubRunHeld
    try:
        document, _ = read_protected_canonical_json_current(path)
        request = _RequestFile.model_validate_json(canonical_json_bytes(document))
        auth = request.authorization
        expected = github_run_directory(auth.issuer_run_id, auth.issuer_attempt)/'operations'/f'{auth.nonce}.json'
    except (OSError, ValueError, ProtectedAuthorityError, GithubRunHeld) as error:
        raise AuthorityHeld('HELD E_AUTHORITY: protected GitHub request is unavailable') from error
    if path != expected:
        raise AuthorityHeld('HELD E_AUTHORITY: GitHub request is not at its run-derived path')
    if source is None or request.execution_source != source:
        raise AuthorityHeld('HELD E_SOURCE: request is not bound to current source')
    if auth.operation != operation:
        raise AuthorityHeld('HELD E_OPERATION: authorization operation differs')
    if not auth.issued_at <= datetime.now(UTC) < auth.expires_at:
        raise AuthorityHeld('HELD E_AUTHORITY: authorization is not current')
    if isinstance(auth, _FixtureAuthorization) != (request.operation_input is None):
        raise AuthorityHeld('HELD E_OPERATION: fixture and research inputs differ')
    run = _validate_workflow_context(auth, source, workflow_operation=(
        'p3-integration-fixture-v1' if request.operation_input is None else request.operation_input.workflow_operation))
    if request.operation_input is not None:
        from packages.alpha_lifecycle.github_research import read_github_research_inputs
        try:
            read_github_research_inputs(run, source=source, input_set_ref=request.operation_input.input_set_ref)
        except GithubRunHeld as error:
            raise AuthorityHeld('HELD E_RESEARCH_INPUTS: '+str(error)) from error
    return request


# In-session stages are admitted only under the HOLDOUT run that owns the session.
_SESSION_STAGES = frozenset({'p3-native-parity-v1', 'p3-phase-exit-v1'})


def _validate_workflow_context(authorization, source: SourceIdentity, *, workflow_operation: str,
                               admission: bool = True):
    """Bind a GITHUB authorization to the controller's API attestation of its run.

    The environment is never consulted: any process can set GITHUB_* variables.
    """
    from packages.runtime_release.config import read_protected_canonical_json_current, ProtectedAuthorityError
    from packages.alpha_lifecycle.github_run import (
        GithubRunHeld, WORKFLOW, github_run_directory, read_github_run, require_admissible_mode,
    )
    if authorization.issuer_workflow != WORKFLOW:
        raise AuthorityHeld("HELD E_ISSUER: authorization issuer workflow differs")
    if WORKFLOW_OPERATIONS.get(workflow_operation) != authorization.operation:
        raise AuthorityHeld("HELD E_OPERATION: workflow operation and authorization differ")
    try:
        run, _ = read_github_run(authorization.issuer_run_id, authorization.issuer_attempt, admission=admission)
        require_admissible_mode(run)
    except GithubRunHeld as error:
        raise AuthorityHeld("HELD E_ISSUER: "+str(error)) from error
    if run.head_sha != source.commit_sha:
        raise AuthorityHeld("HELD E_ISSUER: attested run executes another source")
    # Exact dispatch match: one OOS dispatch never admits another arm, the selection, or a
    # PARITY/PHASE_EXIT outside its HOLDOUT session.
    if run.operation == workflow_operation and workflow_operation not in _SESSION_STAGES:
        return run
    if run.operation != 'p3-holdout-primary-v1' or workflow_operation not in _SESSION_STAGES:
        raise AuthorityHeld("HELD E_ISSUER: authorization operation differs from the attested run")
    try:
        directory = github_run_directory(authorization.issuer_run_id, authorization.issuer_attempt)
        session, _ = read_protected_canonical_json_current(directory/'session.json')
    except (OSError, ProtectedAuthorityError, GithubRunHeld) as error:
        raise AuthorityHeld("HELD E_SESSION: session stage requires the protected HOLDOUT session") from error
    if (session.get('schema_version') != 'p3-holdout-session-v1' or session.get('source') != source.model_dump(mode='json')
            or (session.get('workflow_run_id'), session.get('workflow_attempt'))
            != (authorization.issuer_run_id, authorization.issuer_attempt)):
        raise AuthorityHeld("HELD E_SESSION: protected session belongs to another run or source")
    return run


def validate_operation_issuer(authorization, source, *, workflow_operation: str, admission: bool = True,
                              root=_SOURCE_ROOT):
    if isinstance(authorization,(LocalRunAuthorization,_LocalFixtureAuthorization)):
        return validate_local_operation_authority(authorization,source,root=root)
    _validate_workflow_context(authorization,source,workflow_operation=workflow_operation,admission=admission)


def build_alpha_campaign_payload(
    authorization: RunAuthorization | LocalRunAuthorization | _FixtureAuthorization | _LocalFixtureAuthorization,
    source: SourceIdentity,
    workflow_operation: str,
    *, operation_input: P3OperationInput | None = None,
) -> AlphaCampaignPayload:
    """Build the one closed worker payload represented by a protected request."""

    operation = validate_workflow_operation(workflow_operation)
    if authorization.operation != operation:
        raise AuthorityHeld("HELD E_OPERATION: authorization operation differs")
    fixture = isinstance(authorization, (_FixtureAuthorization,_LocalFixtureAuthorization))
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


def validate_operation_review(authorization, source, operation_input, store, *, now):
    """Authenticate review metadata before a caller opens its referenced evidence."""
    from packages.alpha_lifecycle.pit_evidence import _reference
    _reference(authorization.review_ref,65536)
    raw=store.read_bytes(authorization.review_ref)
    review=ReviewApproval.model_validate_json(raw)
    if (canonical_json_bytes(review)!=raw
        or review.source!=source or review.verdict!='APPROVED'
        or review.operator_identity==review.reviewer_identity
        or not isinstance(now,datetime) or now.tzinfo is None
        or not review.issued_at<=authorization.issued_at<=now<authorization.expires_at<=review.expires_at
        or operation_input.digest not in review.subject_digests):
        raise ValueError('operation input is not covered by a current independent review')
    return review


def stage_alpha_campaign_payload(store, authorization, source, workflow_operation, manifest_bytes, *, operation_input=None):
    """Publish exact approved input bytes to CAS and read them back before enqueue."""
    payload = build_alpha_campaign_payload(authorization, source, workflow_operation, operation_input=operation_input)
    reference = payload.manifest_ref
    if (len(manifest_bytes) != reference.size_bytes
        or hashlib.sha256(manifest_bytes).hexdigest() != reference.content_sha256):
        raise AuthorityHeld("HELD E_MANIFEST: staged manifest differs from approval")
    if operation_input is None:
        store.read_bytes(authorization.review_ref)
    else:
        try:
            now = datetime.now(UTC)
            review=validate_operation_review(authorization,source,operation_input,store,now=now)
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
