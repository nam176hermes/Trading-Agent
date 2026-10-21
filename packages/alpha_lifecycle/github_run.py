"""Protected GITHUB-lane run attestation and research-input admission.

A GitHub-hosted run cannot be authenticated from inside its job: any process can set
`GITHUB_*` variables. The GITHUB lane therefore trusts exactly one thing, a root-owned
`github-run.json` that a task-owned host controller publishes after observing the run
through the GitHub REST API (`authenticate_run`). Worker-side consumers read it only
through `read_protected_canonical_json_current`; nothing here reads the environment.

`github-run.json` also pins the digest of the reviewed research-input receipt, so an
OFFICIAL campaign InputSet and a REHEARSAL one can never be exchanged, and a FAKE
(recorded-API) attestation is admitted only together with a REHEARSAL receipt.

Every admission boundary calls again; nothing here is a lease.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity, ReviewApproval
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity, StrictModel, Token
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes


RUN_ROOT = Path('/run/trading-agent-p3')
RESEARCH_INPUTS_PATH = Path('/etc/trading-agent-p3/github/research-inputs.json')
RESEARCH_REVIEW_PATH = RESEARCH_INPUTS_PATH.with_name('research-inputs-review.json')
REPOSITORY = 'nam176hermes/Trading-Agent'
WORKFLOW = 'p3-authority.yml'
WORKFLOW_PATH = '.github/workflows/p3-authority.yml'
# Admission needs a recent API observation; in-stage fences need only presence.
ADMISSION_MAX_AGE = timedelta(seconds=120)
_MAX_RESPONSE = 1_048_576


class GithubRunHeld(ValueError):
    """The run or its research inputs are not currently authenticated."""


def github_run_directory(run_id: int, attempt: int) -> Path:
    """The single derivation of a GITHUB run's protected directory."""
    if (type(run_id) is not int or type(attempt) is not int
            or not 1 <= run_id < 10**20 or not 1 <= attempt < 10**6):
        raise GithubRunHeld('GitHub run identity is invalid')
    return RUN_ROOT/f'{run_id}-{attempt}'


def github_run_identity(authorization) -> GithubRunIdentity:
    """Structural identity of an official GITHUB authorization (authenticated elsewhere)."""
    if authorization.issuer_workflow != WORKFLOW:
        raise GithubRunHeld('authorization was not issued for p3-authority.yml')
    github_run_directory(authorization.issuer_run_id, authorization.issuer_attempt)
    return GithubRunIdentity(issuer_kind='GITHUB', issuer_identity='p3-authority', repository=REPOSITORY,
                             workflow_ref=WORKFLOW, run_id=authorization.issuer_run_id,
                             attempt=authorization.issuer_attempt)


class ResponseDigests(StrictModel):
    run: Sha256
    jobs: Sha256
    branch: Sha256


class GithubRunAttestation(DigestModel):
    schema_version: Literal['p3-github-run-attestation-v1']
    # FAKE marks a recorded-API rehearsal; it never authenticates an OFFICIAL campaign.
    mode: Literal['GITHUB', 'FAKE']
    identity: GithubRunIdentity
    head_sha: Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
    operation: Annotated[str, Field(pattern=r'^p3-[a-z0-9-]{1,64}$')]
    runner_name: Annotated[str, Field(pattern=r'^[A-Za-z0-9._-]{1,128}$')]
    observed_at: CanonicalUtcDateTime
    response_sha256: ResponseDigests
    host_id: Sha256
    boot_id: UUID
    research_inputs_sha256: Sha256
    authority: SafeAuthority


class GithubResearchInputs(DigestModel):
    """Reviewed admission of one campaign InputSet for the GITHUB lane."""
    schema_version: Literal['p3-github-research-inputs-v1']
    campaign: Literal['OFFICIAL', 'REHEARSAL']
    source: SourceIdentity
    policy_digest: Sha256
    input_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1
    integration_receipt_ref: ArtifactRefV1
    review_ref: ArtifactRefV1
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    authority: SafeAuthority

    @property
    def binding_digest(self) -> str:
        """What the review covers: everything except the review reference itself."""
        body = self.model_dump(mode='json', exclude={'digest', 'review_ref'})
        return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _now(now: datetime | None) -> datetime:
    value = datetime.now(UTC) if now is None else now
    if value.tzinfo is None:
        raise GithubRunHeld('admission time must be timezone-aware')
    return value


def _protected(path: Path):
    from packages.runtime_release.config import ProtectedAuthorityError, read_protected_canonical_json_current
    try:
        return read_protected_canonical_json_current(path)
    except (OSError, ProtectedAuthorityError) as error:
        raise GithubRunHeld('protected GitHub lane document is unavailable') from error


def read_github_run(run_id: int, attempt: int, *, admission: bool,
                    now: datetime | None = None) -> tuple[GithubRunAttestation, str]:
    """Read the controller's attestation for this exact run on this host/boot.

    `admission=True` additionally requires an observation younger than
    ADMISSION_MAX_AGE. Revocation is by deletion, so in-stage fences detect it too.
    """
    from packages.alpha_lifecycle.local_qualification import _host_identity
    document, digest = _protected(github_run_directory(run_id, attempt)/'github-run.json')
    try:
        run = GithubRunAttestation.model_validate_json(canonical_json_bytes(document))
    except ValueError as error:
        raise GithubRunHeld('GitHub run attestation is invalid') from error
    identity = run.identity
    if (identity.issuer_identity != 'p3-authority' or identity.workflow_ref != WORKFLOW
            or (identity.run_id, identity.attempt) != (run_id, attempt)):
        raise GithubRunHeld('GitHub run attestation belongs to another run')
    if _host_identity() != (run.host_id, run.boot_id):
        raise GithubRunHeld('GitHub run attestation belongs to another host or boot')
    current = _now(now)
    if run.observed_at > current + timedelta(seconds=5):
        raise GithubRunHeld('GitHub run attestation is from the future')
    if admission and current - run.observed_at > ADMISSION_MAX_AGE:
        raise GithubRunHeld('GitHub run attestation is stale for admission')
    return run, digest


def require_admissible_mode(run: GithubRunAttestation) -> None:
    """A recorded-API (FAKE) run admits nothing unless it pins a REHEARSAL receipt.

    Checked for every GITHUB admission, including fixture and in-session stages that carry
    no operation input, so FAKE evidence can never look like a genuine hosted run.
    """
    if run.mode == 'GITHUB':
        return
    document, digest = _protected(RESEARCH_INPUTS_PATH)
    try:
        inputs = GithubResearchInputs.model_validate_json(canonical_json_bytes(document))
    except ValueError as error:
        raise GithubRunHeld('research inputs receipt is invalid') from error
    if digest != run.research_inputs_sha256 or inputs.campaign != 'REHEARSAL':
        raise GithubRunHeld('recorded-API runs may only use pinned REHEARSAL inputs')


def read_github_research_inputs(run: GithubRunAttestation, *, source: SourceIdentity,
                                input_set_ref: ArtifactRefV1,
                                now: datetime | None = None) -> GithubResearchInputs:
    """Authenticate the reviewed InputSet admission pinned by this run."""
    document, digest = _protected(RESEARCH_INPUTS_PATH)
    try:
        inputs = GithubResearchInputs.model_validate_json(canonical_json_bytes(document))
    except ValueError as error:
        raise GithubRunHeld('research inputs receipt is invalid') from error
    current = _now(now)
    if digest != run.research_inputs_sha256:
        raise GithubRunHeld('research inputs receipt differs from the run pin')
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
    return inputs


SOURCE_READY_NAME = 'source-ready.json'


class GithubSourceReady(DigestModel):
    """The controller's protected observation that this run's exact source is P3-ready.

    Staged role processes have no GitHub `origin`, `gh` or token, so they cannot re-derive the
    attested HWC/PRE_P3 status themselves. The root controller derives it from the
    protected-main checkout, republishes it on every attestation refresh and publishes it next to
    `github-run.json`; consumers check it offline against their own source and its age. Revocation is
    by deletion, like the attestation.
    """
    schema_version: Literal['p3-github-source-ready-v1']
    mode: Literal['GITHUB', 'FAKE']
    identity: GithubRunIdentity
    source: SourceIdentity
    project_status_sha256: Sha256
    observed_at: CanonicalUtcDateTime
    authority: SafeAuthority


def _current_source(root: Path):
    from packages.pre_p3_provenance import canonical_source_identity
    return canonical_source_identity(root)


def read_github_source_ready(identity: GithubRunIdentity, root: Path, *,
                             now: datetime | None = None) -> GithubSourceReady:
    """Admit this process's source only through its own run's attestation and source-ready record.

    The controller re-observes and republishes the record on every attestation refresh, so a gate
    that drops to HELD mid-run stops later admissions once the record is older than ADMISSION_MAX_AGE.
    """
    run, _ = read_github_run(identity.run_id, identity.attempt, admission=False)
    document, _ = _protected(github_run_directory(identity.run_id, identity.attempt)/SOURCE_READY_NAME)
    try:
        ready = GithubSourceReady.model_validate_json(canonical_json_bytes(document))
        current = SourceIdentity.model_validate(_current_source(root))
    except ValueError as error:
        raise GithubRunHeld('GitHub source-ready record is invalid') from error
    if (ready.identity != identity or run.identity != identity or ready.mode != run.mode
            or ready.source != current or current.commit_sha != run.head_sha):
        raise GithubRunHeld('GitHub source-ready record does not bind this run and source')
    if not timedelta(0) <= _now(now) - ready.observed_at <= ADMISSION_MAX_AGE:
        raise GithubRunHeld('GitHub source-ready record is not current')
    return ready


class ExpectedRun(StrictModel):
    """What the controller dispatched; compared with the API, never trusted alone."""
    head_sha: Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
    actor: Annotated[str, Field(pattern=r'^[A-Za-z0-9-]{1,39}$')]
    operation: Annotated[str, Field(pattern=r'^p3-[a-z0-9-]{1,64}$')]
    runner_name: Annotated[str, Field(pattern=r'^[A-Za-z0-9._-]{1,128}$')]
    research_inputs_sha256: Sha256


Fetch = Callable[[str], bytes]


def _api(fetch: Fetch, path: str) -> tuple[Mapping[str, Any], str]:
    raw = fetch(path)
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE:
        raise GithubRunHeld('GitHub API response is missing or oversized')
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise GithubRunHeld('GitHub API response is not JSON') from error
    if not isinstance(value, dict):
        raise GithubRunHeld('GitHub API response is not an object')
    return value, hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def authenticate_run(fetch: Fetch, *, run_id: int, attempt: int, expected: ExpectedRun,
                     mode: Literal['GITHUB', 'FAKE'], host: tuple[str, UUID],
                     now: datetime | None = None) -> GithubRunAttestation:
    """Build the attestation from the API; any unexpected value is HELD."""
    github_run_directory(run_id, attempt)
    base = f'/repos/{REPOSITORY}/actions/runs/{run_id}'
    run, run_digest = _api(fetch, base)
    actor = (run.get('actor') or {}).get('login')
    trigger = (run.get('triggering_actor') or {}).get('login')
    if (run.get('id') != run_id or run.get('run_attempt') != attempt or run.get('path') != WORKFLOW_PATH
            or run.get('event') != 'workflow_dispatch' or run.get('head_branch') != 'main'
            or run.get('head_sha') != expected.head_sha or run.get('status') != 'in_progress'
            or actor != expected.actor or trigger != expected.actor
            or run.get('display_title') != f'p3 {expected.operation}'
            or (run.get('repository') or {}).get('full_name') != REPOSITORY):
        raise GithubRunHeld('GitHub run differs from the dispatched protected-main operation')
    jobs, jobs_digest = _api(fetch, f'{base}/attempts/{attempt}/jobs')
    label = f'p3-run-{run_id}-{attempt}'
    entries = jobs.get('jobs')
    if not isinstance(entries, list) or jobs.get('total_count') != len(entries):
        raise GithubRunHeld('GitHub run jobs listing is incomplete')
    p3 = [job for job in entries if isinstance(job, dict) and job.get('name') == 'p3']
    if (len(p3) != 1 or p3[0].get('status') != 'in_progress' or p3[0].get('run_id') != run_id
            or p3[0].get('run_attempt') != attempt
            or p3[0].get('runner_name') != expected.runner_name
            or label not in (p3[0].get('labels') or ())):
        raise GithubRunHeld('GitHub run is not executing on the bound just-in-time runner')
    # The bound runner must never have taken any other job of this run.
    if any(isinstance(job, dict) and job is not p3[0] and job.get('runner_name') == expected.runner_name
           for job in entries):
        raise GithubRunHeld('bound runner executed an unexpected job')
    branch, branch_digest = _api(fetch, f'/repos/{REPOSITORY}/branches/main')
    if branch.get('name') != 'main' or branch.get('protected') is not True:
        raise GithubRunHeld('main is not protected')
    value = dict(schema_version='p3-github-run-attestation-v1', mode=mode,
        identity=dict(issuer_kind='GITHUB', issuer_identity='p3-authority', repository=REPOSITORY,
                      workflow_ref=WORKFLOW, run_id=run_id, attempt=attempt),
        head_sha=expected.head_sha, operation=expected.operation, runner_name=expected.runner_name,
        observed_at=_now(now).isoformat().replace('+00:00', 'Z'),
        response_sha256=dict(run=run_digest, jobs=jobs_digest, branch=branch_digest),
        host_id=host[0], boot_id=str(host[1]), research_inputs_sha256=expected.research_inputs_sha256,
        authority=dict(broker=False, live=False, network=False, production=False))
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return GithubRunAttestation.model_validate_json(canonical_json_bytes(value))


__all__ = [
    'ADMISSION_MAX_AGE', 'ExpectedRun', 'GithubResearchInputs', 'GithubRunAttestation', 'GithubRunHeld',
    'RESEARCH_INPUTS_PATH', 'RESEARCH_REVIEW_PATH', 'authenticate_run', 'require_admissible_mode', 'github_run_directory', 'github_run_identity', 'read_github_research_inputs',
    'read_github_run', 'GithubSourceReady', 'SOURCE_READY_NAME', 'read_github_source_ready',
]
