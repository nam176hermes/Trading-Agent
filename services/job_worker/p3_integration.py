"""Trusted-parent P3 fixture execution; no calculation child issues authority."""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Literal

from packages.alpha_lifecycle.authority import _FixtureAuthorization, AuthorityHeld
from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt, ReviewApproval
from packages.alpha_lifecycle.contracts.base import DigestModel, Sha256, SourceIdentity
from packages.engine_contracts import canonical_json_bytes, payload_digest
from packages.job_contracts import JobType
from packages.pre_p3_provenance import canonical_source_identity
from .p1_engine_spawn import build_p1_engine_spawn_provider
from .p3_fixture_native import build_native_fixture_inputs, run_native_fixture, NativeFixtureError
from .p3_fixture_sql import run_sql_fixture, REQUIRED_SQL_CHECKS
from .results import ResultValidator, _open_directory_chain

ROOT = Path(__file__).resolve().parents[2]


class FixturePlan(DigestModel):
    schema_version: Literal['p3-integration-fixture-plan-v1']
    source: SourceIdentity
    purpose: Literal['SYNTHETIC_ONLY']
    native_request_digest: Sha256
    policy_set_sha256: Sha256
    sql_revision: Literal['0021_p3_operation_authority']
    cleanup_policy: Literal['OWNED_ROOTS_ONLY']


@dataclass(frozen=True, slots=True)
class IntegrationExecution:
    receipt: IntegrationReceipt
    native_runs: tuple


class IntegrationExecutionError(RuntimeError):
    def __init__(self, message, *, outcome=None, native_runs=(), cleanup_unverified=False, control=None, blocked=False, reason_code=None):
        super().__init__(message)
        self.outcome = outcome
        self.native_runs = tuple(native_runs)
        self.cleanup_unverified = cleanup_unverified
        self.control = control
        self.blocked = blocked
        self.reason_code = reason_code


def _read_authority_bytes(path: Path) -> bytes:
    parent = _open_directory_chain(path.parent)
    descriptor = -1
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (not path.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or mode not in {0o400,0o600,0o440,0o640} or info.st_nlink != 1
            or (mode & 0o040 and info.st_gid not in {os.getegid(),*os.getgroups()})
            or not 1 <= info.st_size <= 65536):
            raise AuthorityHeld('HELD E_REVIEW_AUTHORITY: root-owned protected approval required')
        raw = os.read(descriptor,info.st_size+1)
        if len(raw) != info.st_size:
            raise AuthorityHeld('HELD E_REVIEW_AUTHORITY: protected approval size changed')
        return raw
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _read_review(path: Path, reference) -> ReviewApproval:
    raw = _read_authority_bytes(path)
    if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.content_sha256:
        raise AuthorityHeld('HELD E_REVIEW_AUTHORITY: protected approval bytes differ')
    review = ReviewApproval.model_validate_json(raw)
    if canonical_json_bytes(review) != raw:
        raise AuthorityHeld('HELD E_REVIEW_AUTHORITY: approval is not canonical')
    return review


class P3IntegrationFixtureExecutor:
    def __init__(self, *, store, closure_config, private_root: Path, review_file: Path):
        self.store = store
        self.closure_config = closure_config
        self.private_root = private_root
        self.review_file = review_file

    def _attest(self, job):
        try:
            if job.job_type is not JobType.ALPHA_CAMPAIGN or job.payload.logical_trial_id != 'p3-integration-fixture-v1':
                raise AuthorityHeld('HELD E_OPERATION: exact fixture job required')
            if subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,check=True,timeout=10).stdout:
                raise AuthorityHeld('HELD E_SOURCE: fixture executor requires a clean checkout')
            source = SourceIdentity.model_validate(canonical_source_identity(ROOT))
            if source != job.payload.expected_source:
                raise AuthorityHeld('HELD E_SOURCE: executor source differs from job')
            authorization = _FixtureAuthorization.model_validate_json(self.store.read_bytes(job.payload.authorization_ref))
            plan = FixturePlan.model_validate_json(self.store.read_bytes(job.payload.manifest_ref))
            now = datetime.now(UTC)
            if (authorization.fixture_plan_ref != job.payload.manifest_ref or plan.source != source
                or authorization.issuer_workflow != 'p3-authority.yml'
                or not authorization.issued_at <= now < authorization.expires_at):
                raise AuthorityHeld('HELD E_AUTHORITY: fixture scope/source/expiry differs')
            expected_environment = {
                'GITHUB_REPOSITORY':'nam176hermes/Trading-Agent','GITHUB_REF':'refs/heads/main',
                'GITHUB_SHA':source.commit_sha,'GITHUB_RUN_ID':str(authorization.issuer_run_id),
                'GITHUB_RUN_ATTEMPT':str(authorization.issuer_attempt),
            }
            if any(os.environ.get(key) != value for key,value in expected_environment.items()):
                raise AuthorityHeld('HELD E_WORKFLOW: fixture issuer differs from protected workflow')
            review = _read_review(self.review_file, authorization.review_ref)
            if (review.source != source or review.verdict != 'APPROVED'
                or review.operator_identity == review.reviewer_identity
                or plan.digest not in review.subject_digests
                or not review.issued_at <= now < review.expires_at):
                raise AuthorityHeld('HELD E_REVIEW_AUTHORITY: approval does not cover this fixture')
            self.store.read_bytes(review.evidence_ref)
            policy = (ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()
            if hashlib.sha256(policy).hexdigest() != plan.policy_set_sha256:
                raise AuthorityHeld('HELD E_POLICY: exact accepted policy required')
            return source, authorization, plan
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise AuthorityHeld("HELD E_AUTHORITY: protected fixture inputs cannot be re-attested") from error

    def run(self, job, *, heartbeat, preflight, progress) -> IntegrationExecution:
        runs = ()
        root = None
        try:
            source, authorization, plan = self._attest(job)
            fd = _open_directory_chain(self.private_root)
            try:
                info = os.fstat(fd)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise AuthorityHeld('HELD E_ROOT: private fixture root must be owned mode0700')
            finally:
                os.close(fd)
            preflight()
            root = Path(tempfile.mkdtemp(prefix='p3-native-',dir=self.private_root))
            inputs, transport = root/'inputs', root/'transport'
            inputs.mkdir(mode=0o700)
            transport.mkdir(mode=0o700)
            command, bindings = build_native_fixture_inputs(inputs)
            if payload_digest(command) != plan.native_request_digest:
                raise AuthorityHeld('HELD E_NATIVE_INPUT: approved fixed fixture differs')
            provider = build_p1_engine_spawn_provider(self.closure_config,transport,bindings)
            runs = run_native_fixture(job,command,provider,artifact_root=root/'runs',
                                      heartbeat=heartbeat,preflight=preflight)
            if (len(runs) != 3 or len({run.request.engine_run_id for run in runs}) != 3
                or len({run.result.semantic_sha256 for run in runs}) != 1):
                raise RuntimeError('three distinct consistent native executions required')
            progress()
            native_records = []
            for run in runs:
                stream_root = root/'runs'/f'r{run.replica}'
                raw = ResultValidator(stream_root,stream_root,stream_root)._read_p3_stream(job,run.outcome.stdout)
                stdout_ref = self.store.put_bytes(raw,media_type='application/jsonl')
                stderr = ResultValidator(stream_root,stream_root,stream_root)._read_p3_stream(
                    job,run.outcome.stderr,stream_name='stderr')
                stderr_ref = self.store.put_bytes(stderr,media_type='application/octet-stream')
                result = {key: str(value) if isinstance(value,Decimal) else value
                          for key,value in asdict(run.result).items() if key != 'events'}
                native_records.append({'replica':run.replica,'request':run.request,
                                       'outcome':asdict(run.outcome),'result':result,'stdout_ref':stdout_ref,'stderr_ref':stderr_ref})
            native_ref = self.store.put_bytes(canonical_json_bytes({
                'schema_version':'p3-native-fixture-proof-v1','source':source,'runs':native_records,
            }),media_type='application/json')
            sql = run_sql_fixture(source,progress=progress,heartbeat=heartbeat,
                                  owned_root=self.private_root/("sql-"+hashlib.sha256(
                                      f"{job.job_id}/{job.attempt_id}".encode()).hexdigest()[:32]))
            if (sql.get('sql_revision') != plan.sql_revision
                or sql.get('source') != source.model_dump(mode='json')
                or sql.get('cleanup',{}).get('root_absent') is not True
                or sql.get('cleanup',{}).get('server_stopped') is not True):
                raise AuthorityHeld('HELD E_SQL_PROOF: source, revision or cleanup differs')
            if not REQUIRED_SQL_CHECKS <= set(sql['checks']):
                raise AuthorityHeld('HELD E_SQL_COVERAGE: required SQL qualification vectors are missing')
            sql_ref = self.store.put_bytes(canonical_json_bytes(sql),media_type='application/json')
            progress()
            shutil.rmtree(root)
            if root.exists():
                raise RuntimeError('native fixture cleanup incomplete')
            cleanup_ref = self.store.put_bytes(canonical_json_bytes({
                'schema_version':'p3-fixture-cleanup-v1','source':source,'native_root_absent':True,
                'native_process_cleanup':all(run.outcome.termination_reason is None for run in runs),
                'sql_cleanup':sql['cleanup'],
            }),media_type='application/json')
            progress()
            preflight()
            if self._attest(job) != (source, authorization, plan):
                raise AuthorityHeld('HELD E_AUTHORITY: fixture authority changed during execution')
            receipt = dict(schema_version='p3-integration-qualified-v1',source=source,
                           sql_proof_ref=sql_ref,native_fixture_proof_ref=native_ref,cleanup_proof_ref=cleanup_ref,
                           workflow_run_id=authorization.issuer_run_id,workflow_attempt=authorization.issuer_attempt,
                           status='PASS',authority=dict(broker=False,live=False,network=False,production=False))
            receipt['digest'] = hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
            return IntegrationExecution(IntegrationReceipt.model_validate(receipt),runs)
        except Exception as error:
            outcome = error.outcome if isinstance(error,NativeFixtureError) else (runs[-1].outcome if runs else None)
            completed = error.completed if isinstance(error,NativeFixtureError) else runs
            if root is not None and root.exists():
                if outcome is not None and outcome.termination_reason in {'SESSION_CLEANUP_FAILED','PROCESS_TERMINATION_UNPROVEN'}:
                    raise IntegrationExecutionError(f'cleanup unverified; retained {root}',outcome=outcome,native_runs=completed) from error
                try:
                    shutil.rmtree(root)
                except OSError as cleanup_error:
                    raise IntegrationExecutionError(f'fixture failed and cleanup failed; retained {root}',outcome=outcome,native_runs=completed) from ExceptionGroup('fixture and cleanup failed',[error,cleanup_error])
            raise IntegrationExecutionError(str(error),outcome=outcome,native_runs=completed,
                cleanup_unverified=getattr(error,"cleanup_unverified",False),control=getattr(error,"control",None),
                blocked=isinstance(error,AuthorityHeld),reason_code=getattr(error,"reason_code",None)) from error
