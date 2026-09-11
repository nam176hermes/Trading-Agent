"""One-use P3 driver launch from explicitly injected complete host attestation.

This module does not discover or issue protected host authority. Its composition
must supply a reviewed P3 attestor; paper and native-engine attestations cannot
stand in for it. The official worker profile remains separately gated.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import stat
import time
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock
from types import MappingProxyType
from collections.abc import Mapping
from typing import Callable

from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload
from packages.alpha_lifecycle.baseline_campaign import ReadbackStore, _read
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, InputSet
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.sandbox_policy import (
    BWRAP_CAPABILITIES, BWRAP_VERSION, MAX_ARGV_BYTES,
    MAX_CLOSURE_BYTES, MAX_CLOSURE_FILES, SANDBOX_PROFILE_SHA256,
    SANDBOX_ARGS, FILESYSTEM_ARGS, ENVIRONMENT_ARGS, DRIVER_ENTRY, ROOT_READONLY_ARGS,
    RO_FILE_FLAG, RO_DIRECTORY_FLAG, RW_DIRECTORY_FLAG,
)
from packages.data_contracts import ArtifactRefV1
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload, JobType
from packages.pre_p3_provenance import canonical_source_identity, _git, _parse_tree, _read_blobs
from packages.runtime_release.v2 import inspect_python_runtime
from services.job_store.worker_repository import ClaimedJob
from .command_registry import p3_command_spec
from .engine_spawn import _sealed_memfd
from .p3_spawn_interface import P3PreparedSpawnMarker, P3SpawnError
from .p3_output import P3OutputCustody
from .results import _open_directory_chain

ROOT = Path(__file__).resolve().parents[2]
_TTL_NS = 5 * 60 * 1_000_000_000
_RELEASE = PurePosixPath('/p3/release')
_PYTHON = PurePosixPath('/p3/python')


@dataclass(frozen=True, slots=True)
class P3ClosureMount:
    source: Path
    target: PurePosixPath
    identity: tuple[int, int]
    size: int
    mode: int
    sha256: str


@dataclass(frozen=True, slots=True)
class P3Sandbox:
    executable: Path
    identity: tuple[int, int]
    executable_sha256: str
    mode: int
    profile_sha256: str
    version: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompleteP3Closure:
    source: SourceIdentity
    environment_ref: ArtifactRefV1
    python_root: Path
    mounts: tuple[P3ClosureMount, ...]
    sandbox: P3Sandbox
    closure_sha256: str


@dataclass(frozen=True, slots=True)
class P3SpawnLineage:
    closure_sha256: str
    sandbox_policy_sha256: str
    operation_input_sha256: str
    claim_sha256: str

    def as_metadata(self) -> dict[str, str]:
        return dict(p3_closure_sha256=self.closure_sha256,
            p3_sandbox_policy_sha256=self.sandbox_policy_sha256,
            p3_operation_input_sha256=self.operation_input_sha256,p3_claim_sha256=self.claim_sha256)


@dataclass(frozen=True, slots=True)
class P3BuiltSpawn:
    job_id: str
    attempt_id: str
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    pass_fds: tuple[int, ...]
    close_after_spawn_fds: tuple[int, ...]
    timeout_seconds: int
    result_validator_id: str
    capability_fingerprint: str
    source_revision: str
    lineage: P3SpawnLineage
    output_custody: P3OutputCustody


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class PreparedP3Spawn(P3PreparedSpawnMarker):
    _provider: P3SpawnProvider

    def __repr__(self) -> str:
        return 'PreparedP3Spawn(validated=True)'


def _digest(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _directory_identity(path: Path) -> tuple[int, int]:
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('P3 transport directory path is invalid')
    fd = _open_directory_chain(path)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('P3 transport directory must be private and owned')
        return info.st_dev, info.st_ino
    finally:
        os.close(fd)


def _release_files(source: SourceIdentity) -> dict[PurePosixPath, tuple[str, bytes]]:
    if _git(ROOT,'status','--porcelain') or SourceIdentity.model_validate(canonical_source_identity(ROOT)) != source:
        raise ValueError('P3 driver source differs from current source')
    entries = _parse_tree(_git(ROOT, 'ls-tree', '-rz', '--full-tree', source.commit_sha))
    files = {p.decode():(mode,raw) for (mode,_,p),raw in zip(entries,_read_blobs(ROOT,entries),strict=True)}
    inventory = json.loads(files['docs/implementation/p3/p3-driver-files-v1.json'][1])
    paths = inventory['paths']
    if (set(inventory) != {'schema_version','paths'} or inventory['schema_version'] != 'p3-driver-source-files-v1'
        or not isinstance(paths,list) or paths != sorted(set(paths))
        or any(not isinstance(p,str) or p.startswith('/') or '..' in PurePosixPath(p).parts for p in paths)
        or any(files[p][0] not in {'100644','100755'} for p in paths)):
        raise ValueError('P3 source projection differs from its reviewed inventory')
    return {_RELEASE/p:files[p] for p in paths}


def _file_bytes(path: Path, identity, size, mode, digest) -> bytes:
    if not path.is_absolute() or '..' in path.parts or type(size) is not int or not 0 <= size <= 268435456:
        raise ValueError('P3 closure file path or size is invalid')
    parent = _open_directory_chain(path.parent)
    fd = -1
    try:
        fd = os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=parent)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid not in {0,os.geteuid()}
            or (before.st_dev,before.st_ino) != identity or before.st_size != size
            or stat.S_IMODE(before.st_mode) != mode):
            raise ValueError('P3 closure file identity differs')
        with os.fdopen(fd,'rb',closefd=False) as stream:
            raw = stream.read(size+1)
        after = os.fstat(fd)
        if (len(raw) != size or hashlib.sha256(raw).hexdigest() != digest
            or (before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError('P3 closure bytes changed during verification')
        return raw
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent)


class P3SpawnProvider:
    def __init__(self, *, attest_closure: Callable[[], CompleteP3Closure], store,
        store_root: Path, output_root: Path):
        if not callable(attest_closure):
            raise TypeError('complete P3 attestor is required')
        if type(store) is not LocalArtifactStore or store._root != store_root:
            raise ValueError('P3 validated store must equal the mounted store')
        self._attest_closure, self._store = attest_closure, store
        self._store_root, self._output_root = store_root, output_root
        self._issued = weakref.WeakKeyDictionary()
        self._lock = Lock()

    def _inputs(self, job, closure):
        if (platform.system() != 'Linux' or platform.machine().lower() != 'x86_64'
            or type(job) is not ClaimedJob or job.job_type is not JobType.ALPHA_CAMPAIGN
            or type(job.payload) is not AlphaCampaignPayload
            or job.payload.operation not in {'BASELINES','REGISTER_FAMILY'}
            or job.lease_expires_at <= datetime.now(UTC)):
            raise ValueError('P3 driver requires a current official campaign claim')
        spec = p3_command_spec(job.payload)
        if spec.argv_prefix != ('-I','-B','scripts/run_p3_alpha_campaign.py'):
            raise ValueError('P3 driver command differs')
        if (type(closure) is not CompleteP3Closure or closure.source != job.payload.expected_source
            or type(closure.sandbox) is not P3Sandbox
            or closure.sandbox.profile_sha256 != SANDBOX_PROFILE_SHA256
            or closure.sandbox.version != BWRAP_VERSION or closure.sandbox.capabilities != BWRAP_CAPABILITIES):
            raise ValueError('complete P3 source attestation is required')
        intent = _read(self._store, job.payload.manifest_ref, P3OperationInput)
        authorization = _read(self._store, job.payload.authorization_ref, RunAuthorization)
        payload = stage_alpha_campaign_payload(ReadbackStore(self._store,self._store), authorization,
            closure.source, job.payload.logical_trial_id, canonical_json_bytes(intent), operation_input=intent)
        inputs = _read(self._store, intent.input_set_ref, InputSet)
        environment = _read(self._store, closure.environment_ref, EnvironmentIdentity)
        if (payload != job.payload or inputs.source != closure.source
            or inputs.environment_ref != closure.environment_ref
            or environment.python_version != '3.11'
            or environment.sandbox_policy_digest != closure.sandbox.profile_sha256):
            raise ValueError('P3 input, source, environment or authorization differs')
        return spec, environment

    def prepare(self, job: ClaimedJob) -> PreparedP3Spawn:
        try:
            closure = self._attest_closure()
            self._inputs(job, closure)
            identities = tuple(_directory_identity(p) for p in (self._store_root,self._output_root))
            if identities[0] == identities[1] or self._store_root in self._output_root.parents or self._output_root in self._store_root.parents:
                raise ValueError('P3 input and output roots overlap')
            token = PreparedP3Spawn()
            object.__setattr__(token, '_provider', self)
            with self._lock:
                self._issued[token] = (job, closure, identities, time.monotonic_ns()+_TTL_NS)
            return token
        except Exception as error:
            raise P3SpawnError('P3_SPAWN_HELD','P3 capability preparation failed') from error

    def _consume(self, token):
        with self._lock:
            record = self._issued.pop(token, None) if type(token) is PreparedP3Spawn else None
        if record is None:
            raise P3SpawnError('P3_CAPABILITY_INVALID','P3 capability is invalid or already consumed')
        descriptors = []
        output_parent = -1
        created_output = None
        custody = None
        try:
            job, closure, identities, deadline = record
            if time.monotonic_ns() >= deadline or self._attest_closure() != closure:
                raise ValueError('P3 capability expired or closure changed')
            spec, environment = self._inputs(job, closure)
            if tuple(_directory_identity(p) for p in (self._store_root,self._output_root)) != identities:
                raise ValueError('P3 transport roots changed')
            inspect_python_runtime(closure.python_root, require_empty_site_packages=False)
            release = _release_files(closure.source)
            if hashlib.sha256(release[_RELEASE/'uv.lock'][1]).hexdigest() != environment.root_lock_digest:
                raise ValueError('P3 Python dependency lock differs')
            if (not isinstance(closure.mounts,tuple) or not 1 <= len(closure.mounts) <= MAX_CLOSURE_FILES
                or any(type(m) is not P3ClosureMount or type(m.size) is not int or not 0 <= m.size <= 268435456 for m in closure.mounts)
                or sum(m.size for m in closure.mounts) > MAX_CLOSURE_BYTES):
                raise ValueError('P3 closure exceeds its bounded inventory')
            targets = tuple(m.target for m in closure.mounts)
            if not targets or targets != tuple(sorted(set(targets))):
                raise ValueError('P3 closure inventory must be sorted and unique')
            runtime = {_PYTHON/p.relative_to(closure.python_root).as_posix():p
                for p in closure.python_root.rglob('*') if not p.is_dir()}
            if {t for t in targets if t.is_relative_to(_RELEASE)} != set(release) or {t for t in targets if t.is_relative_to(_PYTHON)} != set(runtime):
                raise ValueError('P3 release or Python/dependency inventory is incomplete')
            if not any(m.target == _PYTHON/'bin/python3.11' and m.mode in {0o500,0o555} for m in closure.mounts):
                raise ValueError('P3 Python entrypoint is not executable')
            inventory = [dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in closure.mounts]
            fingerprint = _digest(dict(source=closure.source,environment_ref=closure.environment_ref,
                files=inventory,sandbox_sha256=closure.sandbox.executable_sha256,
                sandbox_policy_sha256=closure.sandbox.profile_sha256))
            if fingerprint != closure.closure_sha256:
                raise ValueError('P3 closure digest differs')
            sandbox = closure.sandbox
            if sandbox.mode not in {0o500,0o555,0o755}:
                raise ValueError('P3 sandbox executable mode is invalid')
            raw = _file_bytes(sandbox.executable,sandbox.identity,sandbox.executable.stat(follow_symlinks=False).st_size,
                sandbox.mode,sandbox.executable_sha256)
            sandbox_fd = _sealed_memfd('p3-sandbox',raw,mode=0o500)
            descriptors.append(sandbox_fd)
            argv = [f'/proc/self/fd/{sandbox_fd}',*SANDBOX_ARGS,*FILESYSTEM_ARGS]
            directories = {PurePosixPath('/p3'),PurePosixPath('/p3/bin'),PurePosixPath('/p3/inputs'),
                PurePosixPath('/p3/store'),PurePosixPath('/p3/output')}
            for target in targets:
                directories.update(p for p in target.parents if p != PurePosixPath('/'))
            soft,_ = resource.getrlimit(resource.RLIMIT_NOFILE)
            if (sum(len(str(t).encode())+80 for t in targets)+sum(len(str(p).encode())+12 for p in directories)+8192 > MAX_ARGV_BYTES
                or (soft != resource.RLIM_INFINITY and len(os.listdir('/proc/self/fd'))+len(targets)+16 >= soft)):
                raise ValueError('P3 closure exceeds argv or descriptor limits')
            for directory in sorted(directories,key=lambda p:(len(p.parts),str(p))):
                argv.extend(('--dir',str(directory)))
            for mount in closure.mounts:
                if (not mount.target.is_absolute()
                    or '..' in mount.target.parts or mount.mode not in {0o400,0o500,0o444,0o555}
                    or not (mount.target.is_relative_to(_RELEASE) or mount.target.is_relative_to(_PYTHON)
                        or any(mount.target.is_relative_to(PurePosixPath(p)) for p in ('/lib','/lib64','/usr/lib')))):
                    raise ValueError('P3 closure mount role is invalid')
                raw = _file_bytes(mount.source,mount.identity,mount.size,mount.mode,mount.sha256)
                if (mount.target in release and (raw != release[mount.target][1]
                    or bool(mount.mode & 0o111) != (release[mount.target][0] == '100755'))
                    or mount.target in runtime and mount.source != runtime[mount.target]):
                    raise ValueError('P3 mounted bytes differ from source or runtime')
                fd = _sealed_memfd('p3-closure',raw,mode=mount.mode)
                descriptors.append(fd)
                argv.extend(('--perms',f'{mount.mode:o}',RO_FILE_FLAG,str(fd),str(mount.target)))
            argv.extend(('--perms','500',RO_FILE_FLAG,str(sandbox_fd),'/p3/bin/bwrap'))
            references = {'manifest-ref':job.payload.manifest_ref,'source':closure.source,
                'environment-ref':closure.environment_ref,'authorization-ref':job.payload.authorization_ref}
            for name, value in references.items():
                fd = _sealed_memfd('p3-input',canonical_json_bytes(value),mode=0o400)
                descriptors.append(fd)
                argv.extend(('--perms','400',RO_FILE_FLAG,str(fd),f'/p3/inputs/{name}.json'))
            runtime_mounts = [str(_PYTHON),*(str(t) for t in targets if not t.is_relative_to(_PYTHON) and not t.is_relative_to(_RELEASE))]
            runtime_fd = _sealed_memfd('p3-runtime-mounts',canonical_json_bytes(runtime_mounts),mode=0o400)
            descriptors.append(runtime_fd)
            argv.extend(('--perms','400',RO_FILE_FLAG,str(runtime_fd),'/p3/inputs/runtime-mounts.json'))
            store_fd = _open_directory_chain(self._store_root)
            descriptors.append(store_fd)
            output_parent = _open_directory_chain(self._output_root)
            descriptors.append(output_parent)
            if tuple((os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in (store_fd,output_parent)) != identities:
                raise ValueError('P3 transport roots changed before pinning')
            run_name = hashlib.sha256(f'{job.job_id}/{job.attempt_id}'.encode()).hexdigest()
            os.mkdir(run_name,mode=0o700,dir_fd=output_parent)
            created_output = run_name
            output_fd = os.open(run_name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=output_parent)
            descriptors.append(output_fd)
            output_info = os.fstat(output_fd)
            if output_info.st_uid != os.geteuid() or stat.S_IMODE(output_info.st_mode) != 0o700:
                raise ValueError('P3 attempt output directory is not private and owned')
            argv.extend((RO_DIRECTORY_FLAG,str(store_fd),'/p3/store',
                RW_DIRECTORY_FLAG,str(output_fd),'/p3/output',
                *ROOT_READONLY_ARGS,'--chdir','/p3/release',
                *ENVIRONMENT_ARGS,*DRIVER_ENTRY))
            for name in references:
                argv.extend((f'--{name}',f'/p3/inputs/{name}.json'))
            argv.extend(('--store','/p3/store','--release','/p3/release','--python','/p3/python/bin/python3.11',
                '--sandbox-policy-digest',environment.sandbox_policy_digest,'--logical-trial-id',job.payload.logical_trial_id,
                '--job-id',job.job_id,'--output','/p3/output','--sandbox','/p3/bin/bwrap',
                '--runtime-mounts-ref','/p3/inputs/runtime-mounts.json'))
            if time.monotonic_ns() >= deadline or sum(len(a.encode())+1 for a in argv) > MAX_ARGV_BYTES:
                raise ValueError('P3 capability expired during verification')
            self._inputs(job, closure)
            if time.monotonic_ns() >= deadline:
                raise ValueError('P3 capability expired during final input validation')
            claim_digest = _digest(dict(job_id=job.job_id,attempt_id=job.attempt_id,worker_id=job.worker_id,
                lease_token_sha256=job.lease_token_sha256,payload=job.payload,closure=fingerprint,
                argv_prefix=spec.argv_prefix,timeout_seconds=spec.timeout_seconds,
                result_validator=spec.result_validator_id,transport_identities=identities,
                output_identity=(output_info.st_dev,output_info.st_ino)))
            custody = P3OutputCustody(output_parent,output_fd,run_name,self._store)
            os.close(output_parent)
            descriptors.remove(output_parent)
            output_parent = -1
            return P3BuiltSpawn(job.job_id,job.attempt_id,tuple(argv),Path('/'),MappingProxyType({}),tuple(descriptors),tuple(descriptors),
                spec.timeout_seconds,spec.result_validator_id,claim_digest,closure.source.commit_sha,
                P3SpawnLineage(fingerprint,environment.sandbox_policy_digest,job.payload.manifest_ref.content_sha256,claim_digest),custody)
        except BaseException as error:
            if custody is not None:
                custody.abandon()
            elif created_output is not None:
                try:
                    os.rmdir(created_output,dir_fd=output_parent)
                except OSError:
                    # Preserve unexpected contents; only an empty task directory is ours to remove.
                    pass
            for fd in descriptors:
                os.close(fd)
            if not isinstance(error, Exception):
                raise
            raise P3SpawnError('P3_SPAWN_HELD','P3 capability consumption failed') from error


def consume_prepared_p3_spawn(token: PreparedP3Spawn) -> P3BuiltSpawn:
    provider = getattr(token,'_provider',None)
    if type(token) is not PreparedP3Spawn or type(provider) is not P3SpawnProvider:
        raise P3SpawnError('P3_CAPABILITY_INVALID','exact issued P3 capability is required')
    return provider._consume(token)
