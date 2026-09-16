"""One bounded owner for an already approved three-job calculation lifetime.

This private owner does not enqueue, approve, finalize jobs or enable the official
late lane. The worker owns fresh lease/safety fences and SQL result recovery.
"""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import time
from typing import Annotated, Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle.contracts.base import StrictModel, SourceIdentity, Sha256
from packages.alpha_lifecycle.contracts.models import json_array
from packages.alpha_lifecycle.contracts.authority import CustodyRecord, RunAuthorization
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
    from packages.alpha_lifecycle.native_request import NativeRequest
    from services.job_store.p3_sql import PublicationProposal
    from .p3_host_profile import OfficialHostProfile

_STAGES = ('HOLDOUT', 'PARITY', 'PHASE_EXIT')


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


class SessionProfile(StrictModel):
    schema_version: Literal['p3-holdout-session-v1']
    source: SourceIdentity
    environment_ref: ArtifactRefV1
    primary_selection_ref: ArtifactRefV1
    custody_record_ref: ArtifactRefV1
    holdout_commitment: Sha256
    closure_sha256: Sha256
    workflow_run_id: Annotated[int, Field(gt=0)]
    workflow_attempt: Annotated[int, Field(gt=0)]
    parent: ProcessIdentity
    boot_id: UUID
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    workflow_deadline: CanonicalUtcDateTime

    @model_validator(mode='after')
    def _bounded(self) -> 'SessionProfile':
        if not self.issued_at < self.expires_at <= self.workflow_deadline <= self.issued_at + timedelta(minutes=37):
            raise ValueError('session must fit its protected and enclosing workflow deadlines')
        return self


class P3HoldoutSession:
    def __init__(self, profile_path: Path, *, store: LocalArtifactStore) -> None:
        if type(store) is not LocalArtifactStore:
            raise ValueError('session requires the concrete retained store')
        document, self._digest = read_protected_canonical_json_current(profile_path)
        self.profile = SessionProfile.model_validate_json(canonical_json_bytes(document))
        expected = Path(f'/run/trading-agent-p3/{self.profile.workflow_run_id}-{self.profile.workflow_attempt}/session.json')
        if profile_path != expected:
            raise ValueError('session profile must use its workflow-owned path')
        self._path, self._store = profile_path, store
        self._view: HoldoutCalculationView | None = None
        self._manifest: ArtifactRefV1 | None = None
        self._spec: ArtifactRefV1 | None = None
        self.native_commitment_ref: ArtifactRefV1 | None = None
        self._active: tuple[ClaimedJob, Callable[[], None], str, RunAuthorization] | None = None
        self._closed = self._release_attempted = False
        self._stage = 0
        self._attempts: list[SessionAttempt] = []
        self._stage_digest: str | None = None
        self._host_identity: bytes | None = None
        self._deadline = time.monotonic() + max(0, (self.profile.expires_at-datetime.now(UTC)).total_seconds())
        self._check_owner()

    def __enter__(self) -> 'P3HoldoutSession':
        self._check_owner()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _check_owner(self) -> None:
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
            self._host(claim, digest)
            if (authorization.issuer_run_id != self.profile.workflow_run_id
                or authorization.issuer_attempt != self.profile.workflow_attempt):
                raise ValueError('stage belongs to another workflow attempt')
            self._check_view_access()
        except BaseException:
            self.close()
            raise

    def _host(self, claim: ClaimedJob, digest: str) -> 'OfficialHostProfile':
        from .p3_host_profile import read_official_profile
        profile, closure = read_official_profile(self._path.with_name('profile.json'), digest,
            job_id=claim.job_id, payload=claim.payload, context=os.environ)
        identity = canonical_json_bytes(profile.model_dump(mode='json', exclude={'job_id', 'payload', 'custodian_endpoint'}))
        if (closure.source != self.profile.source or closure.environment_ref != self.profile.environment_ref
            or closure.closure_sha256 != self.profile.closure_sha256
            or Path(profile.store_root) != self._store._root
            or self._host_identity is not None and identity != self._host_identity):
            raise ValueError('session source, environment, runtime or transport changed')
        if self._host_identity is None:
            self._host_identity = identity
        return profile

    @contextmanager
    def stage(self, claim: ClaimedJob, *, fence: Callable[[], None]) -> Iterator['P3HoldoutSession']:
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
            self._host(claim, digest)
            authorization = _read(self._store, claim.payload.authorization_ref, RunAuthorization)
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
            profile = self._host(active[0], active[2])
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
            view.bind_lifetime(self._check_view_access)
            from packages.alpha_lifecycle.native_request import prepare_native_commitment
            commitment = prepare_native_commitment(manifest_ref, self._spec, view)
            self.native_commitment_ref = self.put_bytes(canonical_json_bytes(commitment), media_type='application/json')
            self.fence()
            return request_ref, manifest_ref
        except BaseException:
            self.close()
            raise

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
        validate_native_commitment(self.native_commitment_ref, self._manifest, self._spec, view, self)
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
            observed_at=authorization.issued_at, expires_at=authorization.expires_at, store=self, holdout_view=view)

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        self._check_view_access()
        return self._store.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        self.fence()
        return self._store.put_bytes(value, media_type=media_type)

    def close(self) -> None:
        self._closed = True
        self._active = None
        if self._view is not None:
            self._view.close()
            self._view = None
