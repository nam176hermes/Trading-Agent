"""Protected GITHUB-lane research batch: pre-run request, root registry record and admission graph.

The OFFICIAL GITHUB research InputSet is produced by operation `p3-research-inputs-v1` of the
protected `p3-authority.yml` inside a run that the root controller authenticated through the REST
API. Authority does not come from the producer's own output. It comes from:

- a reviewed pre-run request whose file digest the run attestation pins
  (`research_inputs_sha256` carries the request digest for this operation);
- a root registry record written only after the completed run, the attest job, the GitHub
  attestation of the exact commitment bytes and an observed custody closure were checked;
- issuer-sealed custody (a root 0500 backup root, observed by identity only) plus a
  root-owned evidence store holding the few small objects admission reads.

Every admission boundary calls again. Nothing here is a lease.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle import github_run as _lane
from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity, ReviewApproval
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity, StrictModel, Text
from packages.alpha_lifecycle.contracts.models import json_array
from packages.alpha_lifecycle.github_run import (
    REPOSITORY, RESEARCH_INPUTS_PATH, RESEARCH_REVIEW_PATH, WORKFLOW, WORKFLOW_PATH, ExpectedRun, GithubResearchInputs,
    GithubResearchInputsV2, GithubSyntheticInputs, GithubRunAttestation, GithubRunHeld, _api, _protected, github_run_directory,
    parse_github_research_inputs,
)
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes

RESEARCH_OPERATION = 'p3-research-inputs-v1'
RESEARCH_BASE = Path('/srv/trading-agent-p3/research')
REQUEST_PATH = Path('/etc/trading-agent-p3/github/research-batch-request.json')
REQUEST_REVIEW_PATH = REQUEST_PATH.with_name('research-batch-request-review.json')
REGISTRY_ROOT = Path('/etc/trading-agent-p3/github/research-batches')
# Only the first batch exists: the sealer starts without predecessor revisions.
RECORD_PATH = REGISTRY_ROOT/'0001.json'
REQUEST_MAX_VALIDITY = timedelta(hours=6)


class GithubResearchBatchRequest(DigestModel):
    """The reviewed pre-run request for the one FIRST research batch."""
    schema_version: Literal['p3-github-research-batch-request-v1']
    source: SourceIdentity
    policy_digest: Sha256
    segment: Literal['RESEARCH']
    start: Literal['2018-01-01']
    end: Literal['2025-08-31']
    # The committed M7 decision: the monthly archive only for this one day.
    monthly_exceptions: tuple[Literal['2018-02-08']]
    batch_ordinal: Literal[1]
    predecessor_commitment_sha256: None
    integration_receipt_ref: ArtifactRefV1
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    authority: SafeAuthority

    @model_validator(mode='after')
    def _window(self):
        if not self.issued_at < self.expires_at <= self.issued_at + REQUEST_MAX_VALIDITY:
            raise ValueError('research batch request validity is not bounded')
        return self


def research_roots(run_id: int, attempt: int) -> dict[str, Path]:
    """The single derivation of a producer run's custody roots; never from argv or env."""
    github_run_directory(run_id, attempt)
    base = RESEARCH_BASE/f'{run_id}-{attempt}'
    return dict(work=base/'work', backup=base/'backup', evidence=base/'evidence')


def validate_research_batch_request(request_document: Mapping[str, Any], request_sha256: str,
                                    review_document: Mapping[str, Any], *, source, pin_sha256: str,
                                    at: datetime) -> GithubResearchBatchRequest:
    """Pure check of the request file against the run pin, the source and an independent review."""
    try:
        request = GithubResearchBatchRequest.model_validate_json(canonical_json_bytes(request_document))
        review = ReviewApproval.model_validate_json(canonical_json_bytes(review_document))
        source = SourceIdentity.model_validate(source)
    except ValueError as error:
        raise GithubRunHeld('research batch request or review is invalid') from error
    at = _lane._now(at)
    if request_sha256 != pin_sha256:
        raise GithubRunHeld('research batch request differs from the run pin')
    if request.source != source or review.source != source:
        raise GithubRunHeld('research batch request binds another source')
    if not request.issued_at <= at < request.expires_at:
        raise GithubRunHeld('research batch request is not current')
    if (review.verdict != 'APPROVED' or review.operator_identity == review.reviewer_identity
            or request.digest not in review.subject_digests
            or not review.issued_at <= request.issued_at < request.expires_at <= review.expires_at):
        raise GithubRunHeld('research batch request is not covered by a current independent review')
    return request


def read_github_research_batch_request(run: GithubRunAttestation, *, source,
                                       now: datetime | None = None) -> GithubResearchBatchRequest:
    """The producer's check while its run attestation exists; GITHUB mode only."""
    if run.mode != 'GITHUB' or run.operation != RESEARCH_OPERATION:
        raise GithubRunHeld('research batches are produced only by a genuine p3-research-inputs-v1 run')
    request, request_sha256 = _protected(REQUEST_PATH)
    review, _ = _protected(REQUEST_REVIEW_PATH)
    return validate_research_batch_request(request, request_sha256, review, source=source,
                                           pin_sha256=run.research_inputs_sha256, at=_lane._now(now))


class ResearchRoots(StrictModel):
    work: Text
    backup: Text
    evidence: Text


class GithubResearchBatchRecord(DigestModel):
    """Root registry entry written after the completed run and its attestation were verified."""
    schema_version: Literal['p3-github-research-batch-record-v1']
    identity: GithubRunIdentity
    head_sha: Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
    request_sha256: Sha256
    request_review_sha256: Sha256
    commitment_ref: ArtifactRefV1
    backup_receipt_ref: ArtifactRefV1
    backup_inventory_sha256: Sha256
    backup_inventory_size_bytes: Annotated[int, Field(ge=1, le=67108864)]
    input_set_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1
    pit_proof_ref: ArtifactRefV1
    roots: ResearchRoots
    backup_root_identity: Annotated[tuple[int, int, int, int], BeforeValidator(json_array)]
    attestation_subject_sha256: Sha256
    attestation_timestamp: CanonicalUtcDateTime
    run_started_at: CanonicalUtcDateTime
    job_completed_at: CanonicalUtcDateTime
    registered_at: CanonicalUtcDateTime
    authority: SafeAuthority

    @model_validator(mode='after')
    def _bindings(self):
        identity = self.identity
        if (identity.issuer_identity != 'p3-authority' or identity.workflow_ref != WORKFLOW
                or identity.repository != REPOSITORY):
            raise ValueError('research batch record names another producer')
        expected = research_roots(identity.run_id, identity.attempt)
        if {key: Path(value) for key, value in self.roots.model_dump().items()} != expected:
            raise ValueError('research batch record roots are not derived from its run')
        if self.attestation_subject_sha256 != self.commitment_ref.content_sha256:
            raise ValueError('research batch attestation subject is not the commitment')
        if not self.run_started_at <= self.job_completed_at <= self.attestation_timestamp <= self.registered_at:
            raise ValueError('research batch record timing differs')
        return self


def _evidence_store(root: Path):
    from packages.alpha_lifecycle.local_qualification import ProtectedQualificationStore
    return ProtectedQualificationStore(root)


def _backup_identity(path: Path) -> tuple[int, int, int, int]:
    from packages.alpha_lifecycle.local_research import protected_backup_identity
    return protected_backup_identity(path)


def validate_github_research_graph(record: GithubResearchBatchRecord, receipt, store) -> None:
    """The GITHUB analogue of `validate_local_research_graph`, over the root evidence store."""
    from packages.alpha_lifecycle.contracts.data import PITProof
    from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, InputSet
    from packages.alpha_lifecycle.pit_suite import PITAdversarialSuiteReceiptV1
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceiptV2, P3ResearchBatchCommitmentV2
    inputs = _read(store, record.input_set_ref, InputSet)
    if inputs.synthetic_generation_ref is not None:
        raise GithubRunHeld('OFFICIAL research cannot admit synthetic purpose')
    _read(store, inputs.environment_ref, EnvironmentIdentity)
    pit = _read(store, inputs.pit_proof_ref, PITProof)
    commitment = _read(store, record.commitment_ref, P3ResearchBatchCommitmentV2)
    backup = _read(store, record.backup_receipt_ref, P3ResearchBackupReceiptV2)
    # The PIT suite must have executed inside the same producer run, not in a LOCAL qualification.
    suite = _read(store, pit.no_future_suite_ref, PITAdversarialSuiteReceiptV1)
    run = (record.identity.run_id, record.identity.attempt)
    if (receipt.input_ref != record.input_set_ref
            or receipt.environment_ref != inputs.environment_ref or record.environment_ref != inputs.environment_ref
            or receipt.integration_receipt_ref != inputs.integration_receipt_ref
            or receipt.source != inputs.source or receipt.policy_digest != inputs.policy_digest
            or record.pit_proof_ref != inputs.pit_proof_ref
            or record.head_sha != inputs.source.commit_sha
            or pit.revision_proof_ref != record.commitment_ref
            or pit.dataset_ref != inputs.dataset_evidence_ref
            or pit.fold_manifest_ref != inputs.fold_manifest_ref
            or pit.vintage_class != 'RETROSPECTIVE_CURRENT_ARCHIVE'
            or pit.historical_vintage_verified
            or pit.limitations != ('retrospective.current_archive',)
            or suite.source != inputs.source
            or (suite.qualification.run_id, suite.qualification.run_attempt) != tuple(map(str, run))
            or (commitment.producer_run_id, commitment.producer_attempt) != run
            or (backup.producer_run_id, backup.producer_attempt) != run
            or commitment.batch_ordinal != 1 or commitment.predecessor_commitment_content_sha256 is not None
            or commitment.source != inputs.source or backup.source != inputs.source
            or commitment.policy_digest != inputs.policy_digest
            or commitment.dataset_content_sha256 != inputs.dataset_evidence_ref.content_sha256
            or commitment.backup_receipt_ref != record.backup_receipt_ref
            or backup.object_inventory_content_sha256 != record.backup_inventory_sha256
            or backup.object_inventory_size_bytes != record.backup_inventory_size_bytes
            or backup.inventory_content_sha256 != commitment.inventory_content_sha256
            or backup.dataset_content_sha256 != commitment.dataset_content_sha256
            or backup.snapshot_content_sha256 != commitment.snapshot_content_sha256
            or backup.object_count < commitment.inventory_entry_count
            or backup.total_bytes < commitment.inventory_size_bytes + inputs.dataset_evidence_ref.size_bytes
            or not (record.run_started_at <= commitment.frozen_at and commitment.completed_at <= backup.verified_at
                    <= commitment.issued_at <= record.job_completed_at)
            or not record.registered_at <= receipt.issued_at):
        raise GithubRunHeld('GitHub research batch subject, producer, custody or timing differs')


def read_github_research_batch(receipt, *, source) -> GithubResearchBatchRecord:
    """Admit an OFFICIAL v2 receipt through the root registry record and sealed custody."""
    document, digest = _protected(RECORD_PATH)
    try:
        record = GithubResearchBatchRecord.model_validate_json(canonical_json_bytes(document))
    except ValueError as error:
        raise GithubRunHeld('GitHub research batch record is invalid') from error
    if digest != receipt.batch_record_sha256 or record.head_sha != SourceIdentity.model_validate(source).commit_sha:
        raise GithubRunHeld('GitHub research batch record differs from the receipt or source')
    backup = Path(record.roots.backup)
    try:
        if _backup_identity(backup) != record.backup_root_identity:
            raise GithubRunHeld('GitHub research backup custody root changed')
        validate_github_research_graph(record, receipt, _evidence_store(Path(record.roots.evidence)))
        # Recheck after the semantic reads, including a replacement or withdrawal during them.
        if _backup_identity(backup) != record.backup_root_identity:
            raise GithubRunHeld('GitHub research backup custody root changed during admission')
    except (OSError, ValueError) as error:
        if isinstance(error, GithubRunHeld):
            raise
        raise GithubRunHeld('GitHub research custody or evidence is unavailable') from error
    if _protected(RECORD_PATH)[1] != digest:
        raise GithubRunHeld('GitHub research batch record changed during admission')
    return record


def authenticate_completed_run(fetch, *, run_id: int, attempt: int, expected: ExpectedRun) -> dict[str, str]:
    """Authenticate a finished producer run from the API, after its attestation was revoked.

    Same dispatch bindings as `authenticate_run`, but the run and both jobs must have succeeded;
    returns the per-attempt start and the `p3` job completion time.
    """
    github_run_directory(run_id, attempt)
    if expected.operation != RESEARCH_OPERATION:
        raise GithubRunHeld('only a research producer run is registered after completion')
    base = f'/repos/{REPOSITORY}/actions/runs/{run_id}'
    run, _ = _api(fetch, f'{base}/attempts/{attempt}')
    actor = (run.get('actor') or {}).get('login')
    trigger = (run.get('triggering_actor') or {}).get('login')
    if (run.get('id') != run_id or run.get('run_attempt') != attempt or run.get('path') != WORKFLOW_PATH
            or run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main'
            or run.get('head_sha') != expected.head_sha or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or actor != expected.actor or trigger != expected.actor
            or run.get('display_title') != f'p3 {expected.operation}'
            or (run.get('repository') or {}).get('full_name') != REPOSITORY
            or not isinstance(run.get('run_started_at'), str)):
        raise GithubRunHeld('completed GitHub run differs from the dispatched producer operation')
    jobs, _ = _api(fetch, f'{base}/attempts/{attempt}/jobs')
    entries = jobs.get('jobs')
    if not isinstance(entries, list) or jobs.get('total_count') != len(entries):
        raise GithubRunHeld('GitHub run jobs listing is incomplete')
    named = {job.get('name'): job for job in entries if isinstance(job, dict)}
    p3, attest = named.get('p3'), named.get('attest-research-inputs')
    if (len(named) != len(entries) or p3 is None or attest is None
            or p3.get('conclusion') != 'success' or attest.get('conclusion') != 'success'
            or p3.get('runner_name') != expected.runner_name
            or f'p3-run-{run_id}-{attempt}' not in (p3.get('labels') or ())
            or not isinstance(p3.get('completed_at'), str)
            or any(job is not p3 and job.get('runner_name') == expected.runner_name for job in named.values())):
        raise GithubRunHeld('completed GitHub run jobs differ from the bound producer and attestation')
    return dict(run_started_at=run['run_started_at'], job_completed_at=p3['completed_at'])


def read_github_research_inputs(run: GithubRunAttestation, *, source: SourceIdentity,
                                input_set_ref: ArtifactRefV1,
                                now: datetime | None = None) -> GithubResearchInputs | GithubResearchInputsV2 | GithubSyntheticInputs:
    """Authenticate the reviewed InputSet admission pinned by this run."""
    document, digest = _protected(RESEARCH_INPUTS_PATH)
    try:
        inputs = parse_github_research_inputs(document)
    except ValueError as error:
        raise GithubRunHeld('research inputs receipt is invalid') from error
    current = _lane._now(now)
    if digest != run.research_inputs_sha256:
        raise GithubRunHeld('research inputs receipt differs from the run pin')
    if isinstance(inputs, GithubSyntheticInputs) and (
            run.mode != 'GITHUB' or inputs.identity != run.identity or run.head_sha != source.commit_sha):
        raise GithubRunHeld('synthetic rehearsal requires its exact genuine GitHub run and source')
    if run.mode == 'FAKE' and inputs.campaign != 'REHEARSAL':
        raise GithubRunHeld('recorded-API runs may only use REHEARSAL inputs')
    if inputs.source != source or inputs.input_ref != input_set_ref:
        raise GithubRunHeld('research inputs receipt binds another source or InputSet')
    if not inputs.issued_at <= current < inputs.expires_at:
        raise GithubRunHeld('research inputs receipt is not current')
    document, _ = _protected(RESEARCH_REVIEW_PATH)
    try:
        review = ReviewApproval.model_validate_json(canonical_json_bytes(document))
    except ValueError as error:
        raise GithubRunHeld('research inputs review is invalid') from error
    raw = canonical_json_bytes(review)
    if (inputs.review_ref.content_sha256 != hashlib.sha256(raw).hexdigest()
            or inputs.review_ref.size_bytes != len(raw) or review.source != source or review.verdict != 'APPROVED'
            or review.operator_identity == review.reviewer_identity
            or not {inputs.input_ref.content_sha256, inputs.binding_digest} <= set(review.subject_digests)
            or not review.issued_at <= inputs.issued_at < inputs.expires_at <= review.expires_at):
        raise GithubRunHeld('research inputs are not covered by a current independent review')
    if inputs.campaign == 'OFFICIAL':
        # A review alone is not producer provenance: OFFICIAL inputs admit only through the root
        # registry record of a verified GITHUB research batch and its sealed custody.
        if not isinstance(inputs, GithubResearchInputsV2) or inputs.batch_record_sha256 not in review.subject_digests:
            raise GithubRunHeld('research inputs review does not cover the research batch record')
        read_github_research_batch(inputs, source=source)
    return inputs


__all__ = ['GithubResearchBatchRecord', 'GithubResearchBatchRequest', 'RECORD_PATH', 'REGISTRY_ROOT',
           'REQUEST_PATH', 'REQUEST_REVIEW_PATH', 'RESEARCH_BASE', 'RESEARCH_OPERATION',
           'authenticate_completed_run', 'read_github_research_batch', 'read_github_research_batch_request',
           'read_github_research_inputs',
           'research_roots', 'validate_github_research_graph', 'validate_research_batch_request']
