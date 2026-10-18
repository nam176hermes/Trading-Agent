"""One bounded owner for an already approved three-job calculation lifetime.

This private owner never enqueues or approves jobs. The official coordinator and
worker own fresh lease/safety fences, terminal SQL results and exact readback.
"""
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import time
from typing import Annotated, Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import BeforeValidator, Field, TypeAdapter, model_validator

from packages.alpha_lifecycle.contracts.base import StrictModel, SourceIdentity, Sha256
from packages.alpha_lifecycle.contracts.models import json_array
from packages.alpha_lifecycle.contracts.authority import (CustodyRecord, RUN_AUTHORIZATION,
    RunAuthorizationValue, LocalRunAuthorization, LocalRunIdentity, GithubRunIdentity)
from packages.alpha_lifecycle.authority import local_run_directory
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView
from packages.alpha_lifecycle.operation_input import P3OperationInput, HoldoutInput, NativeParityInput, PhaseExitInput
from packages.alpha_lifecycle.replica_store import _read
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload, JobType
from packages.runtime_release.config import read_protected_canonical_json_current
from services.job_store.records import ClaimedJob, validate_p3_job_id
from services.job_store.worker_repository import WorkerRepository
from .recovery import ProcessIdentity, ProcProcessInspector

if TYPE_CHECKING:
    from packages.alpha_lifecycle.holdout import HoldoutOperationResult
    from packages.alpha_lifecycle.contracts.results import ParityResult
    from packages.alpha_lifecycle.native_request import NativeRequest
    from services.job_store.p3_sql import PublicationProposal
    from .p3_host_profile import ANY_OFFICIAL_PROFILE
    from .p3_spawn import CompleteP3Closure
    from .p3_native_spawn import P3NativeSpawnProvider
    from .p3_native_runner import NativeReplica
    from .process_runner import HeartbeatDecision, HeartbeatInstruction
    from .safety_state import SafetyEvidence

_STAGES = ('HOLDOUT', 'PARITY', 'PHASE_EXIT')
# Hosted HOLDOUT→PARITY→PHASE_EXIT session bound. The measured LOCAL session took
# 48 min 47 s including two mid-session reviews, so the former 37 minutes could not hold.
HOSTED_SESSION_MINUTES = 150


class SessionAttempt(StrictModel):
    operation: Literal['HOLDOUT', 'PARITY', 'PHASE_EXIT']
    job_id: str
    attempt_id: str
    worker_id: str
    lease_token_sha256: Sha256

    @model_validator(mode='after')
    def _identities(self) -> 'SessionAttempt':
        for identity in (self.job_id, self.attempt_id, self.worker_id):
            validate_p3_job_id(identity)
        return self


class SessionStageProfile(StrictModel):
    schema_version: Literal['p3-holdout-session-stage-v1']
    session_sha256: Sha256
    host_profile_sha256: Sha256
    attempts: Annotated[tuple[SessionAttempt, ...], BeforeValidator(json_array), Field(min_length=1, max_length=3)]


class _SessionProfileFields(StrictModel):
    source: SourceIdentity
    environment_ref: ArtifactRefV1
    primary_selection_ref: ArtifactRefV1
    custody_record_ref: ArtifactRefV1
    holdout_commitment: Sha256
    closure_sha256: Sha256
    parent: ProcessIdentity
    boot_id: UUID
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    workflow_deadline: CanonicalUtcDateTime

    @model_validator(mode='after')
    def _bounded(self) -> '_SessionProfileFields':
        if not self.issued_at < self.expires_at <= self.workflow_deadline:
            raise ValueError('session must fit its protected and enclosing workflow deadlines')
        return self


class SessionProfile(_SessionProfileFields):
    schema_version: Literal['p3-holdout-session-v1']
    workflow_run_id: Annotated[int, Field(gt=0)]
    workflow_attempt: Annotated[int, Field(gt=0)]

    @model_validator(mode='after')
    def _hosted_bound(self):
        if self.workflow_deadline > self.issued_at + timedelta(minutes=HOSTED_SESSION_MINUTES):
            raise ValueError('hosted session exceeds its workflow maximum')
        return self


class LocalSessionProfile(_SessionProfileFields):
    schema_version: Literal['p3-local-holdout-session-v1']
    identity: LocalRunIdentity

    @model_validator(mode='after')
    def _boot(self):
        if self.identity.boot_id != self.boot_id:
            raise ValueError('local session boot identity differs')
        return self


SESSION_PROFILE = TypeAdapter(Annotated[SessionProfile | LocalSessionProfile,Field(discriminator='schema_version')])


class _OwnerObservationFields(StrictModel):
    """Untrusted worker observation; the root controller must inspect the process."""
    source: SourceIdentity
    job_id: str
    host_profile_sha256: Sha256
    parent: ProcessIdentity
    issued_at: CanonicalUtcDateTime
    workflow_deadline: CanonicalUtcDateTime

    @model_validator(mode='after')
    def _bounds(self):
        validate_p3_job_id(self.job_id)
        if not self.issued_at < self.workflow_deadline:
            raise ValueError('owner observation deadline is invalid')
        return self


class LocalSessionOwnerObservation(_OwnerObservationFields):
    schema_version: Literal['p3-local-session-owner-observation-v1']
    identity: LocalRunIdentity


class GithubSessionOwnerObservation(_OwnerObservationFields):
    schema_version: Literal['p3-session-owner-observation-v1']
    identity: GithubRunIdentity


class _SessionLaunchFields(StrictModel):
    source: SourceIdentity
    job_id: str
    host_profile_sha256: Sha256
    worker_pid: Annotated[int, Field(gt=0)]
    worker_start_ticks: Annotated[int, Field(ge=0)]
    worker_uid: Annotated[int, Field(gt=0)]
    controller_pid: Annotated[int, Field(gt=0)]
    issued_at: CanonicalUtcDateTime
    workflow_deadline: CanonicalUtcDateTime


class LocalSessionLaunch(_SessionLaunchFields):
    schema_version: Literal['p3-local-session-launch-v1']
    identity: LocalRunIdentity


class GithubSessionLaunch(_SessionLaunchFields):
    schema_version: Literal['p3-session-launch-v1']
    identity: GithubRunIdentity


def session_lane(identity):
    """(launch, observation, session) models and observation schema for the issuer lane."""
    if isinstance(identity, GithubRunIdentity):
        return GithubSessionLaunch, GithubSessionOwnerObservation, SessionProfile, 'p3-session-owner-observation-v1'
    return LocalSessionLaunch, LocalSessionOwnerObservation, LocalSessionProfile, 'p3-local-session-owner-observation-v1'


def session_belongs_to(session, identity) -> bool:
    """The session was parsed by its lane's model; this binds it to the admitted run."""
    if isinstance(identity, GithubRunIdentity):
        return ((getattr(session, 'workflow_run_id', None), getattr(session, 'workflow_attempt', None))
                == (identity.run_id, identity.attempt))
    return getattr(session, 'identity', None) == identity


def await_session_owner(profile, closure, *, directory: Path, identity, host_profile_digest, expires_at, guard):
    """Observe the post-exec worker before SQL, then await exact root admission."""
    from io import BytesIO
    from .artifacts import ArtifactWriter
    from packages.runtime_release.config import ProtectedAuthorityError
    now = datetime.now(UTC)
    if expires_at <= now:
        raise ValueError('session authority already expired')
    launch_model, observation_model, session_model, observation_schema = session_lane(identity)
    path = directory/'session.json'
    launch_path = path.with_name('session-launch.json')
    # Root may still be publishing immediately after fork; no SQL or observation yet.
    publish_deadline = time.monotonic() + min(60, (expires_at-now).total_seconds())
    while True:
        guard()
        try:
            launch_document, _ = read_protected_canonical_json_current(launch_path)
            break
        except ProtectedAuthorityError:
            try:
                launch_path.lstat()
            except FileNotFoundError:
                if time.monotonic() >= publish_deadline:
                    raise ValueError('protected transport launch observation absent') from None
                time.sleep(0.25)
                continue
            raise
    launch = launch_model.model_validate_json(canonical_json_bytes(launch_document))
    deadline = launch.workflow_deadline
    parent = ProcProcessInspector().inspect(os.getpid())
    if (parent is None or launch.identity != identity or launch.source != closure.source
        or launch.job_id != profile.job_id or launch.host_profile_sha256 != host_profile_digest
        or launch.worker_pid != os.getpid() or launch.worker_start_ticks != parent.start_ticks
        or launch.worker_uid != os.geteuid() or launch.controller_pid != os.getppid()
        or not launch.issued_at <= datetime.now(UTC) < deadline <= expires_at):
        raise ValueError('session owner differs from protected transport launch')
    observation = observation_model.model_validate_json(canonical_json_bytes(dict(
        schema_version=observation_schema, identity=identity,
        source=closure.source, job_id=profile.job_id, host_profile_sha256=host_profile_digest,
        parent=dict(pid=parent.pid, process_group=parent.process_group, start_ticks=parent.start_ticks,
                    command_fingerprint=parent.command_fingerprint),
        issued_at=launch.issued_at.isoformat().replace('+00:00','Z'),
        workflow_deadline=deadline.isoformat().replace('+00:00','Z'))))
    guard()
    # O_EXCL intentionally prevents restarting this logical owner handshake.
    writer = ArtifactWriter(Path(profile.output_root))
    writer.capture_stream(profile.job_id, 'session-owner', 'observation-claim', BytesIO(b''))
    pending = writer.capture_stream(profile.job_id, 'session-owner',
        'observation-pending', BytesIO(canonical_json_bytes(observation)))
    temporary = writer.root/pending.relative_ref
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, temporary.with_name('observation.log'))
    monotonic_deadline = time.monotonic() + (deadline-datetime.now(UTC)).total_seconds()
    while time.monotonic() < monotonic_deadline and datetime.now(UTC) < deadline:
        guard()
        if read_protected_canonical_json_current(launch_path)[0] != launch_document:
            raise ValueError('protected transport launch changed')
        if ProcProcessInspector().inspect(os.getpid()) != parent:
            raise ValueError('session owner changed during admission')
        try:
            document, _ = read_protected_canonical_json_current(path)
        except ProtectedAuthorityError:
            try:
                path.lstat()
            except FileNotFoundError:
                time.sleep(0.25)
                continue
            raise
        session = session_model.model_validate_json(canonical_json_bytes(document))
        if (session.parent != parent or not session_belongs_to(session, identity)
            or session.source != closure.source or session.closure_sha256 != closure.closure_sha256
            or canonical_json_bytes(session.environment_ref) != canonical_json_bytes(profile.environment_ref)
            or session.issued_at != observation.issued_at or session.workflow_deadline != deadline
            or not session.issued_at <= datetime.now(UTC) < session.expires_at):
            raise ValueError('protected session differs from observed owner/deadline')
        guard()
        return observation
    raise ValueError('session owner admission deadline expired')


def session_identity(profile) -> tuple[int,int] | LocalRunIdentity:
    if isinstance(profile,LocalSessionProfile):
        return profile.identity
    return profile.workflow_run_id,profile.workflow_attempt


SessionRefKind = Literal['NATIVE_COMMITMENT', 'PARITY_PAIR']
_SESSION_REF_NAMES = {'NATIVE_COMMITMENT': 'native-commitment', 'PARITY_PAIR': 'parity-pair'}


class SessionRefObservation(StrictModel):
    """Untrusted worker note of a session-held reference the next intent must name.

    The references hold no protected prices. The live session still rejects any
    PARITY or PHASE_EXIT body that differs from what it holds, so a wrong note
    can only block the campaign, never widen it.
    """
    schema_version: Literal['p3-session-ref-observation-v1']
    job_id: str
    kind: SessionRefKind
    ref: ArtifactRefV1


def publish_session_ref(output_root, job_id: str, kind: SessionRefKind, ref: ArtifactRefV1) -> Path:
    """Write the note once, fsynced, under the worker output root; never overwrite."""
    from io import BytesIO
    from .artifacts import ArtifactWriter
    validate_p3_job_id(job_id)
    note = SessionRefObservation(schema_version='p3-session-ref-observation-v1', job_id=job_id, kind=kind, ref=ref)
    writer = ArtifactWriter(Path(output_root))
    name = _SESSION_REF_NAMES[kind]
    pending = writer.capture_stream(job_id, 'session-refs', name + '-pending', BytesIO(canonical_json_bytes(note)))
    temporary = writer.root/pending.relative_ref
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    target = temporary.with_name(name + '.log')
    os.link(temporary, target)  # FileExistsError: one note per job and kind.
    os.unlink(temporary)
    return target


def read_session_ref(output_root, job_id: str, kind: SessionRefKind, *, worker_uid: int,
                     store: LocalArtifactStore, expected_source: SourceIdentity) -> ArtifactRefV1:
    """Owner-observation custody checks, then authenticate the referenced artifact.

    Run under the worker UID (the root controller reaches it through `as_worker`). Nothing from the
    note or artifact is returned or raised: every failure becomes one fixed error without its chain,
    and the result is rebuilt from checked values.
    """
    try:
        return _authenticated_session_ref(output_root, job_id, kind, worker_uid=worker_uid,
                                          store=store, expected_source=expected_source)
    except Exception:
        raise ValueError('session reference note rejected') from None


def _authenticated_session_ref(output_root, job_id, kind, *, worker_uid, store, expected_source):
    import hashlib
    import stat
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
    from packages.alpha_lifecycle.native_request import NativeCommitment
    from packages.alpha_lifecycle.parity import SessionParityPair
    from .results import _open_directory_chain
    validate_p3_job_id(job_id)
    path = Path(output_root)/job_id/'session-refs'/(_SESSION_REF_NAMES[kind] + '.log')
    parent_fd = _open_directory_chain(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != worker_uid or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 1 <= info.st_size <= 4096):
                raise ValueError('custody')
            note = SessionRefObservation.model_validate_json(stream.read(4097))
    finally:
        os.close(parent_fd)
    ref = note.ref
    if (note.job_id != job_id or note.kind != kind or ref.media_type != 'application/json'
        or ref.locator != ref.content_sha256 + '.blob' or not 1 <= ref.size_bytes <= 65536):
        raise ValueError('shape')
    raw = store.read_bytes(ref)
    if len(raw) != ref.size_bytes or hashlib.sha256(raw).hexdigest() != ref.content_sha256:
        raise ValueError('bytes')
    # The live session always retains the native-bound pair (build_parity_pair with its parent proof).
    value = (NativeCommitment if kind == 'NATIVE_COMMITMENT' else SessionParityPair).model_validate_json(raw)
    if canonical_json_bytes(value) != raw:
        raise ValueError('canonical')
    bound = value.source if isinstance(value, NativeCommitment) else _read(store, value.manifest_ref, HoldoutManifest).source
    if bound != expected_source:
        raise ValueError('source')
    return ArtifactRefV1(content_sha256=ref.content_sha256, locator=ref.content_sha256 + '.blob',
                         size_bytes=len(raw), media_type='application/json')


class P3HoldoutSession:
    def __init__(self, profile_path: Path, *, store: LocalArtifactStore) -> None:
        if type(store) is not LocalArtifactStore:
            raise ValueError('session requires the concrete retained store')
        self._digest: str
        document, self._digest = read_protected_canonical_json_current(profile_path)
        self.profile = SESSION_PROFILE.validate_json(canonical_json_bytes(document))
        from packages.alpha_lifecycle.github_run import github_run_directory
        expected = (local_run_directory(self.profile.identity)/'session.json' if isinstance(self.profile,LocalSessionProfile)
            else github_run_directory(self.profile.workflow_run_id, self.profile.workflow_attempt)/'session.json')
        if profile_path != expected:
            raise ValueError('session profile must use its workflow-owned path')
        self._run_binding = None
        if isinstance(self.profile, SessionProfile):
            # The hosted session stays bound to the attestation it started under (PR77-05).
            self._run_binding = self._hosted_run_binding()
        self._path: Path = profile_path
        self._store: LocalArtifactStore = store
        self._view: HoldoutCalculationView | None = None
        self._manifest: ArtifactRefV1 | None = None
        self._request: ArtifactRefV1 | None = None
        self._spec: ArtifactRefV1 | None = None
        self.native_commitment_ref: ArtifactRefV1 | None = None
        self.native_parent_proof_ref: ArtifactRefV1 | None = None
        self.parity_pair_ref: ArtifactRefV1 | None = None
        self.holdout_result: HoldoutOperationResult | None = None
        self._outputs: dict[str, ArtifactRefV1] = {}
        self.completed_jobs: list[str] = []
        self._native_receipts: tuple[ArtifactRefV1, ...] = ()
        self._active: tuple[ClaimedJob, Callable[[], None], str, RunAuthorizationValue] | None = None
        self._closed: bool = False
        self._release_attempted: bool = False
        self._native_spawn_issued: bool = False
        self._stage: int = 0
        self._attempts: list[SessionAttempt] = []
        self._stage_digest: str | None = None
        self._host_identity: bytes | None = None
        self._deadline: float = time.monotonic() + max(0, (self.profile.expires_at-datetime.now(UTC)).total_seconds())
        self._check_owner()

    def __enter__(self) -> 'P3HoldoutSession':
        self._check_owner()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _hosted_run_binding(self):
        from packages.alpha_lifecycle.github_run import read_github_run
        run, _ = read_github_run(self.profile.workflow_run_id, self.profile.workflow_attempt, admission=False)
        return run.mode, run.head_sha, run.operation, run.runner_name, run.research_inputs_sha256, run.host_id, run.boot_id

    def _check_owner(self) -> None:
        if isinstance(self.profile,LocalSessionProfile):
            from packages.alpha_lifecycle.local_qualification import _host_identity
            if _host_identity() != (self.profile.identity.host_id,self.profile.identity.boot_id):
                self.close()
                raise ValueError('local session host identity changed')
        else:
            # In-stage fence: the run attestation must still be present (revocation is by
            # deletion) and bound to this host/boot; admission freshness is not re-required.
            from packages.alpha_lifecycle.github_run import GithubRunHeld
            try:
                current = self._hosted_run_binding()
            except GithubRunHeld:
                current = None
            if current is None or current != self._run_binding:
                self.close()
                raise ValueError('hosted session run attestation was revoked or changed')
        if (self._closed or time.monotonic() >= self._deadline
            or not self.profile.issued_at <= datetime.now(UTC) < self.profile.expires_at
            or os.getpid() != self.profile.parent.pid
            or UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()) != self.profile.boot_id
            or ProcProcessInspector().inspect(os.getpid()) != self.profile.parent):
            self.close()
            raise ValueError('session closed, expired or parent identity changed')

    def _check_view_access(self) -> None:
        self._check_owner()
        if self._active is None or datetime.now(UTC) >= self._active[3].expires_at:
            self.close()
            raise ValueError('session has no current approved stage')

    def fence(self) -> None:
        """Call before each replica and durable write, using this stage's lease."""
        self._fence_closure()

    def _fence_closure(self) -> 'CompleteP3Closure':
        try:
            self._check_view_access()
            active = self._active
            if active is None:
                raise ValueError('session has no active job')
            claim, control, digest, authorization = active
            control()
            if read_protected_canonical_json_current(self._path)[1] != self._digest:
                raise ValueError('session profile changed')
            if read_protected_canonical_json_current(self._path.with_name('stage.json'))[1] != self._stage_digest:
                raise ValueError('session protected attempt chain changed')
            _, closure = self._host(claim, digest)
            identity = (authorization.identity if isinstance(authorization,LocalRunAuthorization)
                else (authorization.issuer_run_id,authorization.issuer_attempt))
            if identity != session_identity(self.profile):
                raise ValueError('stage belongs to another workflow attempt')
            self._check_view_access()
            return closure
        except BaseException:
            self.close()
            raise

    def _host(self, claim: ClaimedJob, digest: str, *, admission: bool = False) -> tuple['ANY_OFFICIAL_PROFILE', 'CompleteP3Closure']:
        from .p3_host_profile import read_official_profile
        profile, closure = read_official_profile(self._path.with_name('profile.json'), digest,
            job_id=claim.job_id, payload=claim.payload, admission=admission)
        identity = canonical_json_bytes(profile.model_dump(mode='json', exclude={'job_id', 'payload', 'custodian_endpoint'}))
        if (closure.source != self.profile.source or closure.environment_ref != self.profile.environment_ref
            or closure.closure_sha256 != self.profile.closure_sha256
            or Path(profile.store_root) != self._store._root
            or self._host_identity is not None and identity != self._host_identity):
            raise ValueError('session source, environment, runtime or transport changed')
        if self._host_identity is None:
            self._host_identity = identity
        return profile, closure

    @contextmanager
    def stage(self, claim: ClaimedJob, *, fence: Callable[[], None]) -> Generator['P3HoldoutSession', None, None]:
        """Worker-scoped stage; leaving it revokes access until the next admission.

        A successful context is not a durable job result. The owning worker must
        finish canonical publication/readback inside this context before moving
        on. Any exception closes the session; no stage or release can be retried.
        """
        try:
            self._check_owner()
            if (self._active is not None or self._stage >= len(_STAGES)
                or type(claim) is not ClaimedJob or claim.job_type is not JobType.ALPHA_CAMPAIGN
                or type(claim.payload) is not AlphaCampaignPayload
                or claim.payload.operation != _STAGES[self._stage]
                or claim.payload.expected_source != self.profile.source
                or claim.lease_expires_at <= datetime.now(UTC)
                or any(claim.job_id == prior.job_id or claim.attempt_id == prior.attempt_id for prior in self._attempts)):
                raise ValueError('session requires the next distinct current job attempt')
            _, digest = read_protected_canonical_json_current(self._path.with_name('profile.json'))
            document, stage_digest = read_protected_canonical_json_current(self._path.with_name('stage.json'))
            stage = SessionStageProfile.model_validate_json(canonical_json_bytes(document))
            current = SessionAttempt.model_validate(dict(operation=claim.payload.operation,
                job_id=claim.job_id, attempt_id=claim.attempt_id, worker_id=claim.worker_id,
                lease_token_sha256=claim.lease_token_sha256))
            if (stage.session_sha256 != self._digest or stage.host_profile_sha256 != digest
                or stage.attempts != (*self._attempts, current)):
                raise ValueError('session protected attempt chain differs from current claim')
            self._stage_digest = stage_digest
            _ = self._host(claim, digest, admission=True)
            authorization = _read(self._store, claim.payload.authorization_ref, RUN_AUTHORIZATION)
            intent = _read(self._store, claim.payload.manifest_ref, P3OperationInput)
            body = intent.body
            if isinstance(body, HoldoutInput):
                custody = _read(self._store, body.custody_record_ref, CustodyRecord)
                if (body.primary_selection_ref != self.profile.primary_selection_ref
                    or body.environment_ref != self.profile.environment_ref
                    or body.custody_record_ref != self.profile.custody_record_ref
                    or custody.holdout_commitment != self.profile.holdout_commitment):
                    raise ValueError('session holdout commitment or primary differs')
            elif isinstance(body, NativeParityInput):
                if (body.holdout_manifest_ref != self._manifest or body.instrument_spec_ref != self._spec
                    or self.native_commitment_ref is None or body.native_request_ref != self.native_commitment_ref):
                    raise ValueError('session parity differs from its released view')
            elif not isinstance(body, PhaseExitInput) or body.primary_selection_ref != self.profile.primary_selection_ref:
                raise ValueError('session exit differs from its primary')
            elif self.holdout_result is not None:
                expected = self.holdout_result
                if (body.holdout_request_ref != expected.holdout_request_ref
                    or body.holdout_evaluation_ref != expected.holdout_evaluation_ref
                    or body.holdout_replay_ref != expected.holdout_replay_ref
                    or body.executable_ref != expected.executable_ref
                    or body.baseline_executable_ref != expected.baseline_executable_ref
                    or self.parity_pair_ref is None or body.parity_ref != self.parity_pair_ref):
                    raise ValueError('session exit differs from its completed stages')
            self._active = claim, fence, digest, authorization
            self.fence()
            self._attempts.append(current)
            yield self
            self._check_owner()
            self._stage += 1
        except BaseException:
            self.close()
            raise
        finally:
            self._active = None
            if self._stage == len(_STAGES):
                self.close()

    def release(self, repository: WorkerRepository, *, trace_id: str) -> tuple[ArtifactRefV1, ArtifactRefV1]:
        from .p3_holdout_release import release_holdout_view
        self.fence()
        active = self._active
        if active is None or active[3].operation != 'HOLDOUT' or self._release_attempted:
            self.close()
            raise ValueError('session permits exactly one holdout release attempt')
        self._release_attempted = True  # Uncertain acknowledgement never permits a second request.
        try:
            profile, _ = self._host(active[0], active[2])
            if profile.custodian_endpoint is None:
                raise ValueError('session has no protected custodian endpoint')
            view, request_ref, manifest_ref = release_holdout_view(active[0], repository, self._store,
                endpoint=profile.custodian_endpoint, fence=self.fence, trace_id=trace_id)
            self._view = view
            manifest = _read(view, manifest_ref, HoldoutManifest)
            payload = active[0].payload
            if type(payload) is not AlphaCampaignPayload:
                raise ValueError('session requires the exact campaign payload')
            intent = _read(self._store, payload.manifest_ref, P3OperationInput)
            if (not isinstance(intent.body, HoldoutInput) or manifest.source != self.profile.source
                or manifest.primary_selection_ref != self.profile.primary_selection_ref
                or manifest.environment_ref != self.profile.environment_ref):
                raise ValueError('released view differs from session')
            self._manifest, self._spec = manifest_ref, intent.body.instrument_spec_ref
            self._request = request_ref
            view.bind_lifetime(self._check_view_access)
            from packages.alpha_lifecycle.native_request import prepare_native_commitment
            commitment = prepare_native_commitment(manifest_ref, self._spec, view)
            self.native_commitment_ref = self.put_bytes(canonical_json_bytes(commitment), media_type='application/json')
            publish_session_ref(profile.output_root, active[0].job_id, 'NATIVE_COMMITMENT', self.native_commitment_ref)
            self.fence()
            return request_ref, manifest_ref
        except BaseException:
            self.close()
            raise

    def spawn_inputs(self, claim: ClaimedJob) -> tuple[HoldoutCalculationView, ArtifactRefV1, ArtifactRefV1, ArtifactRefV1, 'CompleteP3Closure']:
        closure = self._fence_closure()
        if self._view is None:
            raise ValueError('session has no released view')
        if (self._active is None or self._active[0] != claim or self._active[3].operation != 'HOLDOUT'
            or self._request is None or self._manifest is None or self._spec is None):
            raise ValueError('holdout driver differs from its released current claim')
        return self._view, self._request, self._manifest, self._spec, closure

    @property
    def view(self) -> HoldoutCalculationView:
        self.fence()
        if self._view is None:
            raise ValueError('session has no released view')
        return self._view

    def native_requests(self) -> tuple['NativeRequest', 'NativeRequest']:
        from packages.alpha_lifecycle.native_request import prepare_native_request, validate_native_commitment
        view = self.view
        if (self._active is None or self._active[3].operation != 'PARITY'
            or self._manifest is None or self._spec is None or self.native_commitment_ref is None):
            raise ValueError('native requests require the current parity stage')
        _ = validate_native_commitment(self.native_commitment_ref, self._manifest, self._spec, view, self)
        return (prepare_native_request(self._manifest, self._spec, view, role='PRIMARY'),
            prepare_native_request(self._manifest, self._spec, view, role='SELECTED_BASELINE'))

    def prepare_exit(self) -> 'PublicationProposal':
        from .p3_publication_producer import prepare_phase_exit
        view = self.view
        if self._active is None or self._active[3].operation != 'PHASE_EXIT':
            raise ValueError('exit proposal requires the current phase-exit stage')
        claim, _, _, authorization = self._active
        payload = claim.payload
        if type(payload) is not AlphaCampaignPayload:
            raise ValueError('session requires the exact campaign payload')
        return prepare_phase_exit(_read(self, payload.manifest_ref, P3OperationInput),
            expected_source=self.profile.source, job_id=claim.job_id,
            observed_at=authorization.issued_at, expires_at=authorization.expires_at, store=self, holdout_view=view,
            native_parent_proof_ref=self.native_parent_proof_ref)

    def native_spawn_provider(self) -> 'P3NativeSpawnProvider':
        from .p3_native_spawn import P3NativeSpawnProvider
        from packages.alpha_lifecycle.native_request import NativeCommitment
        if self._native_spawn_issued:
            raise ValueError('native launch owner was already issued for this session')
        self._native_spawn_issued = True
        requests = self.native_requests()
        if self._active is None or self.native_commitment_ref is None:
            raise ValueError('native launch requires the active parity stage')
        return P3NativeSpawnProvider(self._path.with_name('native.json'), self._active[0],
            session_sha256=self._digest, requests=requests,
            commitment=_read(self, self.native_commitment_ref, NativeCommitment), fence=self._fence_closure)

    def execute_native(self, *,
        heartbeat: Callable[[ProcessIdentity], 'HeartbeatDecision | HeartbeatInstruction'],
        preflight: Callable[[], 'SafetyEvidence'],
    ) -> tuple['NativeReplica', ...]:
        """Keep native execution and result retention inside the current parity stage."""
        from .p3_native_runner import run_native_replicas, retain_native_proof
        try:
            provider = self.native_spawn_provider()
            active = self._active
            if active is None:
                raise ValueError('native execution requires the active parity stage')
            profile, _ = self._host(active[0], active[2])
            runs = run_native_replicas(provider, view=self.view, output=self,
                artifact_root=Path(profile.output_root)/'native', heartbeat=heartbeat, preflight=preflight)
            self.native_parent_proof_ref, self._native_receipts = retain_native_proof(provider, runs, self)
            return runs
        except BaseException:
            self.close()
            raise

    def calculate_parity(self, *,
        heartbeat: Callable[[ProcessIdentity], 'HeartbeatDecision | HeartbeatInstruction'],
        preflight: Callable[[], 'SafetyEvidence'],
    ) -> 'ParityResult':
        from decimal import Decimal
        from packages.alpha_lifecycle.contracts.execution import InstrumentSpec
        from packages.alpha_lifecycle.parity import compare_executable_results, build_parity_pair
        self.fence()
        active, held = self._active, self.holdout_result
        if active is None or held is None or self._manifest is None or self._spec is None:
            raise ValueError('native parity requires this session completed holdout')
        payload = active[0].payload
        if not isinstance(payload, AlphaCampaignPayload):
            raise ValueError('session requires campaign payload')
        intent = _read(self, payload.manifest_ref, P3OperationInput)
        if (not isinstance(intent.body, NativeParityInput)
            or intent.body.primary_reference_ref != held.executable_ref
            or intent.body.baseline_reference_ref != held.baseline_executable_ref):
            raise ValueError('native intent differs from this session reference results')
        runs = self.execute_native(heartbeat=heartbeat, preflight=preflight)
        spec = _read(self.view, self._spec, InstrumentSpec)
        results = []
        refs = []
        for index, reference in enumerate((held.executable_ref, held.baseline_executable_ref)):
            a, b, c = runs[index*3:index*3+3]
            r1, r2, r3 = self._native_receipts[index*3:index*3+3]
            result = compare_executable_results(reference, (a.result_ref, b.result_ref, c.result_ref),
                (r1, r2, r3), self, quote_quantum=Decimal(spec.quote_quantum))
            results.append(result)
            refs.append(self.put_bytes(canonical_json_bytes(result), media_type='application/json'))
        pair = build_parity_pair(manifest_ref=self._manifest, instrument_spec_ref=self._spec,
            primary_reference_ref=held.executable_ref, baseline_reference_ref=held.baseline_executable_ref,
            primary_parity_ref=refs[0], baseline_parity_ref=refs[1], store=self,
            native_parent_proof_ref=self.native_parent_proof_ref)
        self.parity_pair_ref = self.put_bytes(canonical_json_bytes(pair), media_type='application/json')
        publish_session_ref(self._host(active[0], active[2])[0].output_root, active[0].job_id, 'PARITY_PAIR', self.parity_pair_ref)
        return results[0]

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        self._check_view_access()
        return self._store.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        self.fence()
        ref = self._store.put_bytes(value, media_type=media_type)
        self._outputs[ref.content_sha256] = ref
        return ref

    def close(self) -> None:
        self._closed = True
        self._active = None
        if self._view is not None:
            self._view.close()
            self._view = None
