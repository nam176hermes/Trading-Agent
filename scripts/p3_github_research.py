"""Root side of the GITHUB research producer (operation `p3-research-inputs-v1`).

The producer runs as its own UID inside an authenticated `p3-authority.yml` run and only proposes.
Root then does each of the following itself:

- creates the run-derived custody roots before the producer starts;
- publishes the exact commitment bytes through the outbox (the attest job signs them);
- after the run completes, authenticates the finished run from the REST API, verifies the GitHub
  attestation, observes the full custody closure, seals the custody and writes one create-only
  registry record.

Admission (`github_research.read_github_research_batch`) trusts only that record and the sealed
custody, never the producer's own output.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Annotated, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import Field  # noqa: E402

from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity  # noqa: E402
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256  # noqa: E402
from packages.alpha_lifecycle.github_research import (  # noqa: E402
    RECORD_PATH, REQUEST_PATH, REQUEST_REVIEW_PATH, RESEARCH_BASE, RESEARCH_OPERATION, GithubResearchBatchRecord,
    authenticate_completed_run, research_roots, validate_research_batch_request,
)
from packages.alpha_lifecycle.github_run import ExpectedRun, GithubRunAttestation, GithubRunHeld  # noqa: E402
from packages.data_contracts import ArtifactRefV1  # noqa: E402
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes  # noqa: E402

PRODUCER_USER = 'ta-p3-producer'
COMMITMENT_NAME = 'p3-research-commitment-v1.json'
PROPOSAL_NAME = 'proposal.json'
_MAX_OBJECT = 1073741824


class GithubResearchProposal(DigestModel):
    """What the producer reports on stdout; root re-reads every object it names."""
    schema_version: Literal['p3-github-research-proposal-v1']
    identity: GithubRunIdentity
    head_sha: Annotated[str, Field(pattern=r'^[0-9a-f]{40}$')]
    input_set_ref: ArtifactRefV1
    environment_ref: ArtifactRefV1
    pit_proof_ref: ArtifactRefV1
    commitment_ref: ArtifactRefV1
    backup_receipt_ref: ArtifactRefV1
    backup_inventory_ref: ArtifactRefV1
    completed_at: CanonicalUtcDateTime
    authority: SafeAuthority


class RootCasReader:
    """Root reads a role-owned content-addressed store: exact locator, no links, size and digest checked."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        ref = ArtifactRefV1.model_validate(ref)
        if ref.locator != ref.content_sha256 + '.blob' or not 0 < ref.size_bytes <= _MAX_OBJECT:
            raise ValueError('research CAS reference is not an exact bounded blob')
        fd = os.open(self.root/ref.locator, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != ref.size_bytes:
                raise ValueError('research CAS object is not the referenced regular file')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(ref.size_bytes + 1)
        finally:
            os.close(fd)
        if len(raw) != ref.size_bytes or hashlib.sha256(raw).hexdigest() != ref.content_sha256:
            raise ValueError('research CAS object differs from its reference')
        return raw


def create_research_custody(run_id: int, attempt: int, *, producer_uid: int, producer_gid: int) -> dict[str, Path]:
    """Exclusive, run-derived roots before the producer starts; an existing root is HELD."""
    roots = research_roots(run_id, attempt)
    info = os.lstat(RESEARCH_BASE)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise GithubRunHeld('research custody base is not a protected root directory')
    try:
        roots['work'].parent.mkdir(mode=0o755)
    except FileExistsError as error:
        raise GithubRunHeld('research custody for this run already exists; it is never reused') from error
    for key in ('work', 'backup'):
        roots[key].mkdir(mode=0o700)
        os.chown(roots[key], producer_uid, producer_gid)
    return roots


def stage_integration_receipt(roots: dict[str, Path], ref: ArtifactRefV1, source) -> None:
    """Copy the fixture integration receipt the request names into the producer's store (0400)."""
    ref = ArtifactRefV1.model_validate(ref)
    raw = source.read_bytes(ref)
    destination = roots['work']/ref.locator
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    work = os.stat(roots['work'])
    os.chown(destination, work.st_uid, work.st_gid)


def research_files(run: GithubRunAttestation, proposal_raw: bytes) -> dict[str, bytes]:
    """Validate the producer's proposal against the run and return the outbox commitment bytes."""
    from packages.alpha_lifecycle.research_custody import P3ResearchBatchCommitmentV2
    proposal = GithubResearchProposal.model_validate_json(proposal_raw)
    identity = run.identity
    if (run.mode != 'GITHUB' or run.operation != RESEARCH_OPERATION or proposal.identity != identity
            or proposal.head_sha != run.head_sha):
        raise GithubRunHeld('research proposal does not belong to this run')
    roots = research_roots(identity.run_id, identity.attempt)
    raw = RootCasReader(roots['work']).read_bytes(proposal.commitment_ref)
    commitment = P3ResearchBatchCommitmentV2.model_validate_json(raw)
    if ((commitment.producer_run_id, commitment.producer_attempt) != (identity.run_id, identity.attempt)
            or commitment.source.commit_sha != run.head_sha or commitment.batch_ordinal != 1
            or commitment.backup_receipt_ref != proposal.backup_receipt_ref):
        raise GithubRunHeld('research commitment does not bind this run')
    fd = os.open(roots['work'].parent/PROPOSAL_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical_json_bytes(proposal) + b'\n')
    return {COMMITMENT_NAME: raw}


def research_closure(input_set_ref: ArtifactRefV1, reader) -> tuple[dict[str, ArtifactRefV1], ArtifactRefV1]:
    """Every object the InputSet graph names, by typed walk; returns (closure, backup inventory ref).

    Backup objects are taken from the backup's own object inventory. An unknown reference kind
    cannot appear: each model below is strict and every child is named explicitly.
    """
    from packages.alpha_lifecycle.contracts.data import PITProof
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.pit_suite import PITAdversarialSuiteReceiptV1
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceiptV2, P3ResearchBatchCommitmentV2
    closure: dict[str, ArtifactRefV1] = {}

    def add(ref) -> ArtifactRefV1:
        ref = ArtifactRefV1.model_validate(ref)
        if closure.setdefault(ref.locator, ref) != ref:
            raise ValueError('one research object is named with two references')
        return ref

    def model(ref, contract):
        return contract.model_validate_json(reader.read_bytes(add(ref)))

    inputs = model(input_set_ref, InputSet)
    for ref in (inputs.environment_ref, inputs.fold_manifest_ref, inputs.regime_threshold_ref,
                inputs.integration_receipt_ref):
        add(ref)
    add(inputs.dataset_evidence_ref)  # its rows and snapshot are backup objects
    pit = model(inputs.pit_proof_ref, PITProof)
    suite = model(pit.no_future_suite_ref, PITAdversarialSuiteReceiptV1)
    for ref in (suite.suite_manifest_ref, suite.collection_ref, suite.report_ref, pit.dataset_ref,
                pit.fold_manifest_ref):
        add(ref)
    commitment = model(pit.revision_proof_ref, P3ResearchBatchCommitmentV2)
    backup = model(commitment.backup_receipt_ref, P3ResearchBackupReceiptV2)
    inventory_ref = ArtifactRefV1(content_sha256=backup.object_inventory_content_sha256,
                                  size_bytes=backup.object_inventory_size_bytes, media_type='application/json',
                                  locator=backup.object_inventory_content_sha256 + '.blob')
    return closure, inventory_ref


def _entries(root: Path) -> set[str]:
    names = set()
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith('.blob'):
                raise GithubRunHeld('research custody holds a non-blob entry')
            if entry.stat(follow_symlinks=False).st_nlink != 1:  # no second name outside custody
                raise GithubRunHeld('research custody object has another hard link')
            names.add(entry.name)
    return names


def observe_custody(roots: dict[str, Path], input_set_ref: ArtifactRefV1) -> tuple[dict[str, ArtifactRefV1], ArtifactRefV1]:
    """Exhaustiveness is observed: work = closure ∪ backup objects; backup = backup objects ∪ inventory."""
    work_reader, backup_reader = RootCasReader(roots['work']), RootCasReader(roots['backup'])
    closure, inventory_ref = research_closure(input_set_ref, work_reader)
    inventory = [ArtifactRefV1.model_validate(item) for item in json.loads(backup_reader.read_bytes(inventory_ref))]
    objects = {ref.locator for ref in inventory}
    if len(objects) != len(inventory):
        raise GithubRunHeld('backup object inventory repeats an object')
    if _entries(roots['backup']) != objects | {inventory_ref.locator}:
        raise GithubRunHeld('backup custody differs from its object inventory')
    if _entries(roots['work']) != objects | set(closure):
        raise GithubRunHeld('research work store differs from the InputSet closure and backup objects')
    for ref in inventory:  # every backup copy is the object the producer read
        if backup_reader.read_bytes(ref) != work_reader.read_bytes(ref):
            raise GithubRunHeld('backup copy differs from its source object')
    for ref in closure.values():
        work_reader.read_bytes(ref)
    return closure, inventory_ref


def seal_research_custody(roots: dict[str, Path]) -> None:
    """work: root, dirs 0555, files 0444 (campaign-readable). backup: root 0500, files 0400 (identity only)."""
    for key, directory_mode, file_mode in (('work', 0o555, 0o444), ('backup', 0o500, 0o400)):
        root = roots[key]
        for name in _entries(root):
            path = root/name
            os.chown(path, 0, 0, follow_symlinks=False)
            os.chmod(path, file_mode, follow_symlinks=False)
        os.chown(root, 0, 0)
        os.chmod(root, directory_mode)


def verify_commitment_attestation(path: Path, head_sha: str) -> tuple[str, datetime]:
    """`gh attestation verify` of the exact outbox bytes; returns (subject sha256, Tlog time)."""
    result = subprocess.run(
        ['gh', 'attestation', 'verify', str(path), '--repo', 'nam176hermes/Trading-Agent',
         '--signer-workflow', 'nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml',
         '--source-ref', 'refs/heads/main', '--source-digest', head_sha, '--signer-digest', head_sha,
         '--deny-self-hosted-runners', '--format', 'json'],
        capture_output=True, timeout=60, check=False)
    if result.returncode:
        raise GithubRunHeld('research commitment attestation does not verify')
    return parse_attestation(json.loads(result.stdout))


def parse_attestation(verified) -> tuple[str, datetime]:
    """The single attested subject and its transparency-log time; anything else is HELD."""
    try:
        entry, = verified
        subjects = entry['verificationResult']['statement']['subject']
        subject, = subjects
        times = [item['timestamp'] for item in entry['verificationResult']['verifiedTimestamps']
                 if item.get('type') == 'Tlog']
        moment = datetime.fromisoformat(times[0])
        digest = subject['digest']['sha256']
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise GithubRunHeld('research attestation result has no single subject and Tlog time') from error
    if moment.tzinfo is None or not isinstance(digest, str):
        raise GithubRunHeld('research attestation time or subject is invalid')
    return digest, moment.astimezone(UTC)  # gh reports the Tlog time in the host's local offset


def producer_idle() -> None:
    """No producer process may still hold custody files open when root observes and seals them."""
    result = subprocess.run(['pgrep', '-u', PRODUCER_USER], capture_output=True, timeout=10, check=False)
    if result.returncode != 1:
        raise GithubRunHeld('a research producer process is still running or pgrep failed')


def register_research_batch(api, *, head_sha: str, actor: str, run_id: int, attempt: int, request_pin: str,
                            outbox_root: Path, now: datetime, verify=verify_commitment_attestation,
                            observe=observe_custody, seal=seal_research_custody, backup_identity=None,
                            publish=None, idle=producer_idle) -> GithubResearchBatchRecord:
    """Register the completed producer run's batch once; every precondition failure is HELD."""
    from packages.alpha_lifecycle.contracts.data import PITProof
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.github_run import _protected
    from packages.alpha_lifecycle.research_custody import P3ResearchBackupReceiptV2, P3ResearchBatchCommitmentV2
    from packages.local_authority import _publish
    expected = ExpectedRun(head_sha=head_sha, actor=actor, operation=RESEARCH_OPERATION,
                           runner_name=f'p3-jit-{run_id}-{attempt}', research_inputs_sha256=request_pin)
    times = authenticate_completed_run(lambda path: api.get(path), run_id=run_id, attempt=attempt, expected=expected)
    started = datetime.fromisoformat(times['run_started_at'].replace('Z', '+00:00'))
    finished = datetime.fromisoformat(times['job_completed_at'].replace('Z', '+00:00'))
    roots = research_roots(run_id, attempt)
    proposal = GithubResearchProposal.model_validate_json((roots['work'].parent/PROPOSAL_NAME).read_bytes())
    outbox = Path(outbox_root)/f'{run_id}-{attempt}'/COMMITMENT_NAME
    raw = outbox.read_bytes()
    subject, attested_at = verify(outbox, head_sha)
    reader = RootCasReader(roots['work'])
    commitment = P3ResearchBatchCommitmentV2.model_validate_json(raw)
    backup = P3ResearchBackupReceiptV2.model_validate_json(reader.read_bytes(proposal.backup_receipt_ref))
    inputs = InputSet.model_validate_json(reader.read_bytes(proposal.input_set_ref))
    pit = PITProof.model_validate_json(reader.read_bytes(inputs.pit_proof_ref))
    digest = hashlib.sha256(raw).hexdigest()
    if not (digest == subject == proposal.commitment_ref.content_sha256 == pit.revision_proof_ref.content_sha256
            and reader.read_bytes(proposal.commitment_ref) == raw):
        raise GithubRunHeld('research commitment bytes, attestation subject and InputSet graph differ')
    if ((commitment.producer_run_id, commitment.producer_attempt) != (run_id, attempt)
            or (backup.producer_run_id, backup.producer_attempt) != (run_id, attempt)
            or commitment.source.commit_sha != head_sha or inputs.source != commitment.source
            or proposal.pit_proof_ref != inputs.pit_proof_ref or proposal.environment_ref != inputs.environment_ref
            or commitment.backup_receipt_ref != proposal.backup_receipt_ref):
        raise GithubRunHeld('research batch does not bind the registered run')
    if not (started <= commitment.frozen_at <= commitment.completed_at <= backup.verified_at <= commitment.issued_at
            <= finished <= attested_at <= now):
        raise GithubRunHeld('research batch timing is outside the authenticated run window')
    request, request_sha256 = _protected(REQUEST_PATH)
    review, review_sha256 = _protected(REQUEST_REVIEW_PATH)
    # Validity at the attempt's API start time: the request was current when the run began.
    reviewed = validate_research_batch_request(request, request_sha256, review, source=commitment.source,
                                               pin_sha256=request_pin, at=started)
    if (reviewed.policy_digest != commitment.policy_digest
            or reviewed.integration_receipt_ref != inputs.integration_receipt_ref):
        raise GithubRunHeld('research batch does not follow its reviewed request')
    idle()
    closure, inventory_ref = observe(roots, proposal.input_set_ref)
    if inventory_ref != proposal.backup_inventory_ref:
        raise GithubRunHeld('research backup inventory differs from the proposal')
    value = dict(schema_version='p3-github-research-batch-record-v1',
                 identity=proposal.identity.model_dump(mode='json'), head_sha=head_sha,
                 request_sha256=request_sha256, request_review_sha256=review_sha256,
                 commitment_ref=proposal.commitment_ref.model_dump(mode='json'),
                 backup_receipt_ref=proposal.backup_receipt_ref.model_dump(mode='json'),
                 backup_inventory_sha256=inventory_ref.content_sha256,
                 backup_inventory_size_bytes=inventory_ref.size_bytes,
                 input_set_ref=proposal.input_set_ref.model_dump(mode='json'),
                 environment_ref=inputs.environment_ref.model_dump(mode='json'),
                 pit_proof_ref=inputs.pit_proof_ref.model_dump(mode='json'),
                 roots={key: str(path) for key, path in roots.items()},
                 backup_root_identity=[0, 0, 0, 0],
                 attestation_subject_sha256=subject, attestation_timestamp=_utc(attested_at),
                 run_started_at=_utc(started), job_completed_at=_utc(finished), registered_at=_utc(now),
                 authority=dict(broker=False, live=False, network=False, production=False))

    def build():
        document = dict(value)
        document['digest'] = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
        return GithubResearchBatchRecord.model_validate_json(canonical_json_bytes(document))
    build()  # every record field is valid before custody is sealed (sealing is irreversible)
    if any(RECORD_PATH.parent.glob('*.json')):
        raise GithubRunHeld('a research batch is already registered; only the first batch exists')
    seal(roots)
    evidence = roots['evidence']
    evidence.mkdir(mode=0o755)
    for ref in (proposal.input_set_ref, inputs.environment_ref, inputs.pit_proof_ref, pit.no_future_suite_ref,
                proposal.commitment_ref, proposal.backup_receipt_ref):
        _publish(evidence/ref.locator, reader.read_bytes(ref), mode=0o444)
    os.chmod(evidence, 0o555)
    if backup_identity is None:
        from packages.alpha_lifecycle.local_research import protected_backup_identity as backup_identity
    value['backup_root_identity'] = list(backup_identity(roots['backup']))
    record = build()
    if publish is None:
        from scripts.p3_local_controller import publish_protected

        def publish(path, raw):
            publish_protected(path, raw, once=True)
    publish(RECORD_PATH, canonical_json_bytes(record) + b'\n')
    return record


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace('+00:00', 'Z')


__all__ = ['COMMITMENT_NAME', 'GithubResearchProposal', 'PRODUCER_USER', 'RootCasReader', 'create_research_custody',
           'observe_custody', 'parse_attestation', 'producer_idle', 'register_research_batch', 'research_closure', 'research_files',
           'seal_research_custody', 'stage_integration_receipt', 'verify_commitment_attestation']
