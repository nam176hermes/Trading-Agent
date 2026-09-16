"""Private P3 native launch; never reinterpret a P1 command or prepared token."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import hashlib
import os
from pathlib import Path, PurePosixPath
from threading import Lock
import time
from types import MappingProxyType
from typing import Literal, TYPE_CHECKING
import weakref

from packages.alpha_lifecycle.contracts.base import StrictModel, SourceIdentity, Sha256
from packages.alpha_lifecycle.native_request import NativeCommitment, NativeRequest, NativeRole, native_step_bytes
from packages.alpha_lifecycle.sandbox_policy import (
    SANDBOX_ARGS, FILESYSTEM_ARGS, ENVIRONMENT_ARGS, ROOT_READONLY_ARGS,
    BWRAP_VERSION, MAX_ARGV_BYTES, MAX_CLOSURE_FILES, MAX_CLOSURE_BYTES, socket_filter_bytes,
)
from packages.data_contracts import ArtifactRefV1
from packages.job_contracts import AlphaCampaignPayload, JobType
from packages.engine_contracts.serialization import canonical_json_bytes, payload_digest
from packages.pre_p3_provenance import canonical_source_identity, _git
from packages.runtime_release.config import _absolute, read_protected_canonical_json_current
from services.job_store.records import ClaimedJob
from scripts.run_p3_native_child import COMMAND as _COMMAND, MAX_REQUEST_BYTES, RESOURCE_LIMITS
from .engine_spawn import (
    EngineBuiltSpawn, EngineSpawnLineage, _closure_target_directories,
    _sealed_closure_file_snapshot, _sealed_memfd, _sealed_sandbox_snapshot,
)
from .nautilus_closure import NautilusClosureConfig
from .p1_nautilus_closure import attest_p1_nautilus_closure
from .p1_engine_spawn import validate_p1_engine_closure_attestation
from .p3_spawn_interface import P3NativePreparedSpawnMarker

if TYPE_CHECKING:
    from .p1_closure_attestation import P1EngineClosureAttestation

ROOT = Path(__file__).resolve().parents[2]
_SOURCE_FILES = (('scripts/run_p3_native_child.py', '/p3-native/entry.py'),
    ('engines/nautilus/p3_next_open.py', '/p3-native/adapter.py'))
_ORDER = tuple((role, replica) for role in ('PRIMARY', 'SELECTED_BASELINE') for replica in ('R1', 'R2', 'R3'))


class NativeLaunchProfile(StrictModel):
    schema_version: Literal['p3-native-launch-profile-v1']
    source: SourceIdentity
    session_sha256: Sha256
    native_commitment_ref: ArtifactRefV1
    job_id: str
    attempt_id: str
    runtime_root: str
    artifact_directory: str
    sandbox_executable: str
    sandbox_sha256: Sha256


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class PreparedP3NativeSpawn(P3NativePreparedSpawnMarker):
    _provider: P3NativeSpawnProvider


class P3NativeSpawnProvider:
    def __init__(self, profile_path: Path, claim: ClaimedJob, *, session_sha256: str,
        requests: tuple[NativeRequest, NativeRequest], commitment: NativeCommitment,
        fence: Callable[[], None]) -> None:
        self._claim: ClaimedJob = claim
        self._fence: Callable[[], None] = fence
        self._requests: tuple[NativeRequest, NativeRequest] = requests
        self._session_digest: str = session_sha256
        self._commitment: NativeCommitment = commitment
        self._path: Path = profile_path
        self._profile_digest: str
        document, self._profile_digest = read_protected_canonical_json_current(self._path)
        self._profile: NativeLaunchProfile = NativeLaunchProfile.model_validate_json(canonical_json_bytes(document))
        self._next: int = 0
        self._failed: bool = False
        self._pending: bool = False
        self._lock: Lock = Lock()
        self._issued: weakref.WeakKeyDictionary[PreparedP3NativeSpawn,
            tuple[NativeRequest, str, P1EngineClosureAttestation, tuple[bytes, bytes], float]] = weakref.WeakKeyDictionary()
        _ = self._current()

    def _current(self) -> tuple[P1EngineClosureAttestation, tuple[bytes, bytes]]:
        profile = self._profile
        self._fence()
        commitment = canonical_json_bytes(self._commitment)
        if (type(self._claim) is not ClaimedJob or self._claim.job_type is not JobType.ALPHA_CAMPAIGN
            or type(self._claim.payload) is not AlphaCampaignPayload or self._claim.payload.operation != 'PARITY'
            or profile.source != self._claim.payload.expected_source or profile.session_sha256 != self._session_digest
            or profile.native_commitment_ref.content_sha256 != hashlib.sha256(commitment).hexdigest()
            or profile.native_commitment_ref.size_bytes != len(commitment)
            or profile.native_commitment_ref.media_type != 'application/json'
            or self._commitment.source != profile.source
            or any((request.source, request.environment_ref, request.manifest_ref, request.instrument_spec_ref)
                != (self._commitment.source, self._commitment.environment_ref,
                    self._commitment.manifest_ref, self._commitment.instrument_spec_ref) for request in self._requests)
            or tuple(request.role for request in self._requests) != ('PRIMARY', 'SELECTED_BASELINE')
            or tuple(hashlib.sha256(canonical_json_bytes(request)).hexdigest() for request in self._requests)
                != (self._commitment.primary_request_sha256, self._commitment.baseline_request_sha256)
            or (profile.job_id, profile.attempt_id) != (self._claim.job_id, self._claim.attempt_id)
            or read_protected_canonical_json_current(self._path)[1] != self._profile_digest
            or canonical_source_identity(ROOT) != profile.source.model_dump(mode='json')):
            raise ValueError('P3 native profile differs from the current session/claim/source')
        closure = validate_p1_engine_closure_attestation(attest_p1_nautilus_closure(NautilusClosureConfig(
            runtime_root=_absolute(profile.runtime_root), artifact_directory=_absolute(profile.artifact_directory),
            sandbox_executable=_absolute(profile.sandbox_executable))))
        if closure.sandbox.version != BWRAP_VERSION or closure.sandbox.executable_sha256 != profile.sandbox_sha256:
            raise ValueError('P3 native sandbox differs from the protected profile')
        first, second = (_git(ROOT, 'show', f'{profile.source.commit_sha}:{path}') for path, _ in _SOURCE_FILES)
        self._fence()
        return closure, (first, second)

    def prepare(self, role: NativeRole, replica: Literal['R1', 'R2', 'R3']) -> PreparedP3NativeSpawn:
        with self._lock:
            if self._failed:
                raise ValueError('P3 native launch owner failed; no further replica is permitted')
            if self._pending or self._next >= len(_ORDER) or (role, replica) != _ORDER[self._next]:
                raise ValueError('P3 native requires six distinct ordered launches without retry')
            try:
                closure, sources = self._current()
            except BaseException:
                self._failed = True
                raise
            request = self._requests[0 if role == 'PRIMARY' else 1]
            if request.role != role:
                raise ValueError('P3 native role differs')
            token = PreparedP3NativeSpawn()
            object.__setattr__(token, '_provider', self)
            self._issued[token] = request, replica, closure, sources, time.monotonic()+300
            self._pending = True
            self._next += 1
            return token

    def _consume(self, token: PreparedP3NativeSpawn, *, job_id: str, attempt_id: str) -> EngineBuiltSpawn:
        with self._lock:
            record = self._issued.pop(token, None) if type(token) is PreparedP3NativeSpawn else None
        if record is None:
            raise ValueError('P3 native launch is unissued or consumed')
        request, replica, closure, sources, deadline = record
        descriptors: list[int] = []
        transferred = False
        try:
            if ((job_id, attempt_id) != (self._claim.job_id, self._claim.attempt_id)
                or time.monotonic() >= deadline or self._current() != (closure, sources)):
                raise ValueError('P3 native launch changed or expired')
            mounts = (*closure.mounts, closure.closure_manifest, closure.product_lineage)
            targets = tuple(m.target for m in mounts)
            if (not 0 < len(mounts) <= MAX_CLOSURE_FILES or len(set(targets)) != len(targets)
                or sum(m.size for m in mounts) > MAX_CLOSURE_BYTES
                or any(t.is_relative_to('/p3-native') or t.is_relative_to('/inputs') for t in targets)):
                raise ValueError('P3 native inventory is incomplete, overlapping or oversized')
            sandbox_fd = _sealed_sandbox_snapshot(closure.sandbox); descriptors.append(sandbox_fd)
            argv = [f'/proc/self/fd/{sandbox_fd}', *SANDBOX_ARGS, *FILESYSTEM_ARGS]
            for directory in sorted(set(_closure_target_directories(mounts)) | {
                PurePosixPath('/p3-native'), PurePosixPath('/inputs')}):
                argv.extend(('--dir', str(directory)))
            for mount in mounts:
                fd = _sealed_closure_file_snapshot(mount); descriptors.append(fd)
                argv.extend(('--perms', f'{mount.mode:04o}', '--ro-bind-data', str(fd), str(mount.target)))
            for (_, target), raw in zip(_SOURCE_FILES, sources, strict=True):
                fd = _sealed_memfd('p3-native-source', raw, mode=0o400); descriptors.append(fd)
                argv.extend(('--perms', '0400', '--ro-bind-data', str(fd), target))
            raw = native_step_bytes(request)
            if not 0 < len(raw) <= MAX_REQUEST_BYTES:
                raise ValueError('P3 native steps exceed transport bound')
            fd = _sealed_memfd('p3-native-request', raw, mode=0o400); descriptors.append(fd)
            argv.extend(('--perms', '0400', '--ro-bind-data', str(fd), '/inputs/p3-native.json'))
            seccomp = _sealed_memfd('p3-native-seccomp', socket_filter_bytes(), mode=0o400); descriptors.append(seccomp)
            argv.extend(('--seccomp', str(seccomp), *ROOT_READONLY_ARGS, '--chdir', '/', *ENVIRONMENT_ARGS, *_COMMAND))
            self._fence()
            if (read_protected_canonical_json_current(self._path)[1] != self._profile_digest
                or time.monotonic() >= deadline or sum(len(arg.encode())+1 for arg in argv) > MAX_ARGV_BYTES):
                raise ValueError('P3 native preparation exceeded its bound')
            policy = payload_digest(dict(schema_version='p3-native-sandbox-v1', args=SANDBOX_ARGS,
                filesystem=FILESYSTEM_ARGS, environment=ENVIRONMENT_ARGS, root=ROOT_READONLY_ARGS,
                command=_COMMAND, resource_limits=RESOURCE_LIMITS, wall_seconds=300,
                max_request_bytes=MAX_REQUEST_BYTES, bwrap_version=BWRAP_VERSION,
                seccomp_sha256=hashlib.sha256(socket_filter_bytes()).hexdigest()))
            extension = payload_digest(dict(schema_version='p3-native-closure-v1', source=request.source,
                engine_closure_sha256=closure.closure_sha256, policy_sha256=policy,
                sandbox_sha256=closure.sandbox.executable_sha256,
                sources=[dict(target=target, sha256=hashlib.sha256(value).hexdigest())
                    for (_, target), value in zip(_SOURCE_FILES, sources, strict=True)]))
            request_sha = payload_digest(request)
            fingerprint = payload_digest(dict(job_id=job_id, attempt_id=attempt_id,
                lease_sha256=self._claim.lease_token_sha256, request_sha256=request_sha, replica=replica,
                profile_sha256=self._profile_digest, closure_sha256=extension))
            result = EngineBuiltSpawn(tuple(argv), Path('/'), MappingProxyType({}), tuple(descriptors),
                tuple(descriptors), 300, 'p3-native-output-v1', fingerprint, request.source.commit_sha,
                EngineSpawnLineage(extension, policy, request_sha))
            transferred = True
            return result
        finally:
            with self._lock:
                self._pending = False
                self._failed = not transferred
            if not transferred:
                for fd in descriptors: os.close(fd)


def consume_prepared_p3_native_spawn(token: PreparedP3NativeSpawn, *, job_id: str, attempt_id: str) -> EngineBuiltSpawn:
    if type(token) is not PreparedP3NativeSpawn or type(getattr(token, '_provider', None)) is not P3NativeSpawnProvider:
        raise ValueError('P3 native launch is unissued')
    return token._provider._consume(token, job_id=job_id, attempt_id=attempt_id)
