"""Worker-owned, fail-closed engine process preparation.

This module deliberately has no default engine, sandbox, release, or ambient
environment lookup.  A caller must supply a verifier that returns the complete
release closure and OS-sandbox proof as one typed attestation.
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import hmac
import os
import platform
import re
import stat
import threading
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from packages.engine_contracts import (
    ArtifactReference,
    EngineCommandEnvelope,
    RunBacktest,
    RunBacktestSimulation,
    canonical_json_bytes,
)
from .engine_spawn_interface import EnginePreparedSpawnMarker, EngineSpawnError


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SOURCE_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)
_VALIDATOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)
_BWRAP_VERSION = re.compile(
    r"^bubblewrap (?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
    re.ASCII,
)
_PREPARED_TTL_NS = 5 * 60 * 1_000_000_000
_INPUT_TARGET = PurePosixPath("/inputs/request.json")
_SIDECAR_TARGET = PurePosixPath("/inputs/request.sha256")
_ZERO_ORDER_INPUT_ARTIFACT_NAMES = (
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
)
_SIMULATION_INPUT_ARTIFACT_NAMES = (
    *_ZERO_ORDER_INPUT_ARTIFACT_NAMES,
    "simulation_scenario",
)
_SANDBOX_PROFILE_DOCUMENT = (
    b"trading-agent-engine-bwrap-v2:die-with-parent,user,pid,net,new-session,"
    b"clearenv,sealed-file-closure,fd-ro-bind-data-inputs,proc,dev,tmpfs"
)
_REQUIRED_SANDBOX_PROFILE_SHA256 = hashlib.sha256(
    _SANDBOX_PROFILE_DOCUMENT
).hexdigest()
_SANDBOX_OWNED_TARGETS = tuple(
    PurePosixPath(value) for value in ("/inputs", "/proc", "/dev", "/tmp")
)
_REQUIRED_SANDBOX_CAPABILITIES = ("--perms", "--ro-bind-data")
_F_ADD_SEALS = 1033
_F_GET_SEALS = 1034
_F_SEAL_SEAL = 0x0001
_F_SEAL_SHRINK = 0x0002
_F_SEAL_GROW = 0x0004
_F_SEAL_WRITE = 0x0008
_REQUIRED_MEMFD_SEALS = (
    _F_SEAL_WRITE | _F_SEAL_GROW | _F_SEAL_SHRINK | _F_SEAL_SEAL
)
_MFD_CLOEXEC = 0x0001
_MFD_ALLOW_SEALING = 0x0002
_MEMFD_CREATE_SYSCALLS = {
    "x86_64": 319,
    "amd64": 319,
    "aarch64": 279,
    "arm64": 279,
}


def _blocked(reason: str, message: str) -> None:
    raise EngineSpawnError(reason, message)


@dataclass(frozen=True, slots=True)
class OsSandboxProof:
    """Identity and reviewed profile proof for the mandatory OS sandbox."""

    executable: Path
    identity: tuple[int, int]
    executable_sha256: str
    profile_sha256: str
    version: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadOnlyClosureMount:
    """One exact file in the fully attested runtime closure inventory."""

    source: Path
    target: PurePosixPath
    identity: tuple[int, int]
    size: int
    mode: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CompleteEngineClosureAttestation:
    """Complete immutable engine release plus mandatory sandbox authority.

    The closure digest is produced by the external release verifier.  It must
    cover the exact file inventory in ``mounts`` and the reviewed command
    specification; this provider re-runs that verifier at consumption and
    independently seals every listed file.
    """

    profile: str
    source_commit: str
    closure_sha256: str
    mounts: tuple[ReadOnlyClosureMount, ...]
    entrypoint: PurePosixPath
    argv_prefix: tuple[str, ...]
    timeout_seconds: int
    result_validator_id: str
    sandbox: OsSandboxProof
    semantic_profile: str | None = None


@dataclass(frozen=True, slots=True)
class HashBoundEngineInput:
    """One externally provisioned artifact bound to a `RunBacktest` field."""

    name: str
    reference: ArtifactReference
    source: Path
    identity: tuple[int, int]
    size: int
    mode: int
    sha256: str


@dataclass(frozen=True, slots=True)
class EngineSpawnLineage:
    closure_sha256: str
    sandbox_profile_sha256: str
    request_sha256: str

    def as_metadata(self) -> dict[str, str]:
        return {
            "engine_closure_sha256": self.closure_sha256,
            "os_sandbox_profile_sha256": self.sandbox_profile_sha256,
            "engine_request_sha256": self.request_sha256,
        }


@dataclass(frozen=True, slots=True)
class EngineBuiltSpawn:
    """Complete engine launch revealed only to ``ProcessRunner`` at Popen."""

    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    pass_fds: tuple[int, ...]
    close_after_spawn_fds: tuple[int, ...]
    timeout_seconds: int
    result_validator_id: str
    capability_fingerprint: str
    source_revision: str
    lineage: EngineSpawnLineage


@dataclass(frozen=True, slots=True)
class _InputIdentity:
    device: int
    inode: int
    size: int
    mode: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    closure: CompleteEngineClosureAttestation
    request: RunBacktest | RunBacktestSimulation
    inputs: tuple[HashBoundEngineInput, ...]
    inputs_sha256: str
    request_sha256: str
    request_fd: int
    sidecar_fd: int
    root_fd: int
    run_fd: int
    run_name: str
    request_identity: _InputIdentity
    sidecar_identity: _InputIdentity
    issued_at_ns: int
    deadline_ns: int
    fingerprint: str


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    eq=False,
    repr=False,
    weakref_slot=True,
)
class PreparedEngineSpawn(EnginePreparedSpawnMarker):
    """Opaque, provider-bound, one-use engine process authority."""

    _provider: EngineSpawnProvider
    _record: _PreparedRecord

    def __repr__(self) -> str:
        return "PreparedEngineSpawn(validated=True)"

    def __del__(self) -> None:
        try:
            self._provider._abandon(self)
        except BaseException:
            pass


def _read_fd(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _write_fd(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short immutable snapshot write")
        remaining = remaining[written:]


def _memfd_create(name: str) -> int:
    creator = getattr(os, "memfd_create", None)
    if callable(creator):
        return creator(name, _MFD_CLOEXEC | _MFD_ALLOW_SEALING)
    syscall_number = _MEMFD_CREATE_SYSCALLS.get(platform.machine().lower())
    if platform.system() != "Linux" or syscall_number is None:
        _blocked(
            "ENGINE_IMMUTABLE_SNAPSHOT_UNAVAILABLE",
            "sealed memory files are unavailable",
        )
    encoded = name.encode("ascii")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    result = int(
        libc.syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_char_p(encoded),
            ctypes.c_uint(_MFD_CLOEXEC | _MFD_ALLOW_SEALING),
        )
    )
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def _sealed_memfd(name: str, value: bytes, *, mode: int) -> int:
    writer_fd = reader_fd = -1
    try:
        writer_fd = _memfd_create(name)
        _write_fd(writer_fd, value)
        os.fchmod(writer_fd, mode)
        fcntl.fcntl(writer_fd, _F_ADD_SEALS, _REQUIRED_MEMFD_SEALS)
        if fcntl.fcntl(writer_fd, _F_GET_SEALS) != _REQUIRED_MEMFD_SEALS:
            _blocked(
                "ENGINE_IMMUTABLE_SNAPSHOT_UNAVAILABLE",
                "memory file seals are incomplete",
            )
        reader_fd = os.open(
            f"/proc/self/fd/{writer_fd}",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(reader_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size != len(value)
            or stat.S_IMODE(opened.st_mode) != mode
            or fcntl.fcntl(reader_fd, _F_GET_SEALS) != _REQUIRED_MEMFD_SEALS
            or _read_fd(reader_fd, len(value)) != value
        ):
            _blocked(
                "ENGINE_IMMUTABLE_SNAPSHOT_UNAVAILABLE",
                "sealed memory file cannot be proven",
            )
        os.close(writer_fd)
        writer_fd = -1
        result = reader_fd
        reader_fd = -1
        return result
    except EngineSpawnError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise EngineSpawnError(
            "ENGINE_IMMUTABLE_SNAPSHOT_UNAVAILABLE",
            "sealed memory file creation failed",
        ) from exc
    finally:
        for descriptor in (reader_fd, writer_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _sealed_sandbox_snapshot(proof: OsSandboxProof) -> int:
    source_fd = -1
    try:
        source_fd = os.open(
            proof.executable,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != proof.identity
            or not opened.st_mode & 0o111
            or opened.st_mode & 0o222
        ):
            _blocked(
                "ENGINE_SANDBOX_PROOF_INVALID",
                "sandbox executable identity changed before pinning",
            )
        value = _read_fd(source_fd, opened.st_size)
        if len(value) != opened.st_size or not hmac.compare_digest(
            hashlib.sha256(value).hexdigest(), proof.executable_sha256
        ):
            _blocked(
                "ENGINE_SANDBOX_PROOF_INVALID",
                "sandbox executable bytes changed before pinning",
            )
        return _sealed_memfd("engine-sandbox", value, mode=0o500)
    except EngineSpawnError:
        raise
    except OSError as exc:
        raise EngineSpawnError(
            "ENGINE_SANDBOX_PROOF_INVALID",
            "sandbox executable cannot be pinned",
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def _verified_closure_file(mount: ReadOnlyClosureMount) -> bytes:
    source_fd = -1
    try:
        source_fd = os.open(
            mount.source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(source_fd)
        if (
            (opened.st_dev, opened.st_ino) != mount.identity
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_size != mount.size
            or stat.S_IMODE(opened.st_mode) != mount.mode
            or opened.st_mode & 0o222
        ):
            _blocked(
                "ENGINE_CLOSURE_STALE",
                "closure file identity changed before snapshotting",
            )
        value = _read_fd(source_fd, mount.size)
        if len(value) != mount.size or not hmac.compare_digest(
            hashlib.sha256(value).hexdigest(), mount.sha256
        ):
            _blocked("ENGINE_CLOSURE_STALE", "closure file bytes changed")
        return value
    except EngineSpawnError:
        raise
    except OSError as exc:
        raise EngineSpawnError(
            "ENGINE_CLOSURE_STALE", "closure file cannot be snapshotted"
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def _sealed_closure_file_snapshot(mount: ReadOnlyClosureMount) -> int:
    return _sealed_memfd(
        "engine-closure-file",
        _verified_closure_file(mount),
        mode=mount.mode,
    )


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode):
            _blocked("ENGINE_SANDBOX_PROOF_INVALID", "sandbox executable is not a regular file")
        for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
            digest.update(chunk)
    except EngineSpawnError:
        raise
    except OSError as exc:
        raise EngineSpawnError(
            "ENGINE_SANDBOX_PROOF_INVALID", "sandbox executable cannot be read"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _safe_absolute_path(path: object) -> bool:
    return isinstance(path, Path) and path.is_absolute() and ".." not in path.parts


def _safe_target(path: object) -> bool:
    return (
        isinstance(path, PurePosixPath)
        and path.is_absolute()
        and ".." not in path.parts
        and path != PurePosixPath("/")
        and not any(
            path == reserved or path.is_relative_to(reserved)
            for reserved in _SANDBOX_OWNED_TARGETS
        )
    )


def _observed_identity(path: Path, *, reason: str) -> tuple[os.stat_result, tuple[int, int]]:
    try:
        observed = path.lstat()
    except OSError as exc:
        raise EngineSpawnError(reason, "attested closure path is unavailable") from exc
    if stat.S_ISLNK(observed.st_mode):
        _blocked(reason, "attested closure path is a symlink")
    return observed, (observed.st_dev, observed.st_ino)


def _validate_entrypoint(attestation: CompleteEngineClosureAttestation) -> None:
    matching = [
        mount for mount in attestation.mounts if mount.target == attestation.entrypoint
    ]
    if len(matching) != 1 or not matching[0].mode & 0o111:
        _blocked(
            "ENGINE_CLOSURE_INVALID",
            "entrypoint is not one exact executable closure file",
        )


def _closure_target_directories(
    mounts: tuple[ReadOnlyClosureMount, ...],
) -> tuple[PurePosixPath, ...]:
    directories = {_INPUT_TARGET.parent}
    for mount in mounts:
        candidate = mount.target.parent
        while candidate != PurePosixPath("/"):
            directories.add(candidate)
            candidate = candidate.parent
    return tuple(
        sorted(directories, key=lambda value: (len(value.parts), str(value)))
    )


def _validate_closure(value: object) -> CompleteEngineClosureAttestation:
    if type(value) is not CompleteEngineClosureAttestation:
        _blocked(
            "ENGINE_CLOSURE_UNAVAILABLE",
            "typed complete engine closure attestation is required",
        )
    attestation = value
    if (
        attestation.profile not in {"zero-order", "execution-simulation"}
        or (
            attestation.semantic_profile
            not in {None, "nautilus-execution-simulation-v2"}
        )
        or (
            attestation.profile == "execution-simulation"
            and attestation.semantic_profile != "nautilus-execution-simulation-v2"
        )
        or (
            attestation.profile == "zero-order"
            and attestation.semantic_profile is not None
        )
        or not isinstance(attestation.source_commit, str)
        or _SOURCE_COMMIT.fullmatch(attestation.source_commit) is None
        or not isinstance(attestation.closure_sha256, str)
        or _SHA256.fullmatch(attestation.closure_sha256) is None
        or type(attestation.mounts) is not tuple
        or not attestation.mounts
        or type(attestation.argv_prefix) is not tuple
        or isinstance(attestation.timeout_seconds, bool)
        or not isinstance(attestation.timeout_seconds, int)
        or attestation.timeout_seconds <= 0
        or not isinstance(attestation.result_validator_id, str)
        or _VALIDATOR_ID.fullmatch(attestation.result_validator_id) is None
        or not isinstance(attestation.entrypoint, PurePosixPath)
        or not attestation.entrypoint.is_absolute()
        or ".." in attestation.entrypoint.parts
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "complete engine closure shape is invalid")
    if any(
        not isinstance(argument, str) or not argument or "\x00" in argument
        for argument in attestation.argv_prefix
    ):
        _blocked("ENGINE_CLOSURE_INVALID", "engine argument specification is invalid")

    proof = attestation.sandbox
    if (
        type(proof) is not OsSandboxProof
        or not _safe_absolute_path(proof.executable)
        or type(proof.identity) is not tuple
        or len(proof.identity) != 2
        or any(
            isinstance(component, bool)
            or not isinstance(component, int)
            or component < 0
            for component in proof.identity
        )
        or not isinstance(proof.executable_sha256, str)
        or _SHA256.fullmatch(proof.executable_sha256) is None
        or not isinstance(proof.profile_sha256, str)
        or _SHA256.fullmatch(proof.profile_sha256) is None
        or not isinstance(proof.version, str)
        or type(proof.capabilities) is not tuple
    ):
        _blocked("ENGINE_SANDBOX_PROOF_UNAVAILABLE", "typed OS sandbox proof is required")
    if (
        proof.profile_sha256 != _REQUIRED_SANDBOX_PROFILE_SHA256
        or _BWRAP_VERSION.fullmatch(proof.version) is None
        or proof.capabilities != _REQUIRED_SANDBOX_CAPABILITIES
    ):
        _blocked(
            "ENGINE_SANDBOX_PROOF_INVALID",
            "OS sandbox profile or FD-data capability is not reviewed",
        )
    observed, identity = _observed_identity(
        proof.executable, reason="ENGINE_SANDBOX_PROOF_INVALID"
    )
    if (
        identity != proof.identity
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_uid not in {0, os.geteuid()}
        or not observed.st_mode & 0o111
        or observed.st_mode & 0o022
        or not hmac.compare_digest(_sha256_path(proof.executable), proof.executable_sha256)
    ):
        _blocked("ENGINE_SANDBOX_PROOF_INVALID", "OS sandbox proof is stale or unsafe")

    targets: set[PurePosixPath] = set()
    for mount in attestation.mounts:
        if (
            type(mount) is not ReadOnlyClosureMount
            or not _safe_absolute_path(mount.source)
            or not _safe_target(mount.target)
            or type(mount.identity) is not tuple
            or len(mount.identity) != 2
            or any(
                isinstance(component, bool)
                or not isinstance(component, int)
                or component < 0
                for component in mount.identity
            )
            or isinstance(mount.size, bool)
            or not isinstance(mount.size, int)
            or mount.size < 0
            or isinstance(mount.mode, bool)
            or not isinstance(mount.mode, int)
            or mount.mode < 0
            or mount.mode > 0o777
            or not mount.mode & 0o400
            or mount.mode & 0o222
            or not isinstance(mount.sha256, str)
            or _SHA256.fullmatch(mount.sha256) is None
            or any(
                mount.target == existing
                or mount.target.is_relative_to(existing)
                or existing.is_relative_to(mount.target)
                for existing in targets
            )
        ):
            _blocked("ENGINE_CLOSURE_INVALID", "closure mount is invalid")
        observed, identity = _observed_identity(
            mount.source, reason="ENGINE_CLOSURE_STALE"
        )
        if (
            identity != mount.identity
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_size != mount.size
            or stat.S_IMODE(observed.st_mode) != mount.mode
            or observed.st_mode & 0o222
        ):
            _blocked("ENGINE_CLOSURE_STALE", "closure file identity changed")
        _verified_closure_file(mount)
        targets.add(mount.target)
    _validate_entrypoint(attestation)
    return attestation


def _input_target(name: str, reference: ArtifactReference) -> PurePosixPath:
    extension = ".jsonl" if reference.media_type == "application/jsonl" else ".json"
    return PurePosixPath("/inputs/artifacts") / f"{name}-{reference.sha256}{extension}"


def _input_mount(value: HashBoundEngineInput) -> ReadOnlyClosureMount:
    return ReadOnlyClosureMount(
        source=value.source,
        target=PurePosixPath("/engine/input-verification-placeholder"),
        identity=value.identity,
        size=value.size,
        mode=value.mode,
        sha256=value.sha256,
    )


def _validate_inputs(
    value: object, request: RunBacktest | RunBacktestSimulation
) -> tuple[HashBoundEngineInput, ...]:
    if type(request) is RunBacktest:
        names = _ZERO_ORDER_INPUT_ARTIFACT_NAMES
    elif type(request) is RunBacktestSimulation:
        names = _SIMULATION_INPUT_ARTIFACT_NAMES
    else:
        _blocked("ENGINE_INPUT_AUTHORITY_INVALID", "exact backtest command is required")
    if type(value) is not tuple or len(value) != len(names):
        _blocked("ENGINE_INPUT_AUTHORITY_INVALID", "complete hash-bound inputs are required")
    expected_references = tuple(getattr(request, name) for name in names)
    validated: list[HashBoundEngineInput] = []
    for name, expected_reference, artifact in zip(
        names, expected_references, value, strict=True
    ):
        if (
            type(artifact) is not HashBoundEngineInput
            or artifact.name != name
            or artifact.reference != expected_reference
            or not _safe_absolute_path(artifact.source)
            or type(artifact.identity) is not tuple
            or len(artifact.identity) != 2
            or any(
                isinstance(component, bool)
                or not isinstance(component, int)
                or component < 0
                for component in artifact.identity
            )
            or isinstance(artifact.size, bool)
            or not isinstance(artifact.size, int)
            or artifact.size <= 0
            or artifact.size > 8 * 1024 * 1024
            or artifact.mode != 0o400
            or artifact.sha256 != expected_reference.sha256
        ):
            _blocked("ENGINE_INPUT_AUTHORITY_INVALID", "engine input authority is invalid")
        observed, identity = _observed_identity(
            artifact.source, reason="ENGINE_INPUT_STALE"
        )
        if (
            identity != artifact.identity
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_size != artifact.size
            or stat.S_IMODE(observed.st_mode) != artifact.mode
            or observed.st_mode & 0o222
        ):
            _blocked("ENGINE_INPUT_STALE", "engine artifact identity changed")
        _verified_closure_file(_input_mount(artifact))
        validated.append(artifact)
    return tuple(validated)


def _inputs_sha256(inputs: tuple[HashBoundEngineInput, ...]) -> str:
    value = "".join(
        f"{item.name}:{item.reference.artifact_id}:{item.sha256}:{item.size}:{item.mode}"
        for item in inputs
    )
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class EngineSpawnProvider:
    """The only owner of engine argv/environment/cwd and input transport."""

    def __init__(
        self,
        *,
        transport_root: Path,
        attest_closure: Callable[[], CompleteEngineClosureAttestation],
        attest_inputs: Callable[
            [RunBacktest | RunBacktestSimulation],
            tuple[HashBoundEngineInput, ...],
        ]
        | None = None,
        monotonic_ns: Callable[[], int],
    ) -> None:
        if not _safe_absolute_path(transport_root):
            raise ValueError("engine transport root must be canonical and absolute")
        if not callable(attest_closure):
            raise TypeError("complete engine closure attestor is required")
        if attest_inputs is not None and not callable(attest_inputs):
            raise TypeError("engine input attestor must be callable")
        if not callable(monotonic_ns):
            raise TypeError("monotonic clock is required")
        self._transport_root = transport_root
        self._attest_closure = attest_closure
        self._attest_inputs = attest_inputs
        self._monotonic_ns = monotonic_ns
        self._issued: weakref.WeakSet[PreparedEngineSpawn] = weakref.WeakSet()
        self._issue_lock = threading.Lock()

    def _abandon(self, prepared: PreparedEngineSpawn) -> None:
        with self._issue_lock:
            if prepared not in self._issued:
                return
            self._issued.discard(prepared)
            record = prepared._record
        for descriptor in (
            record.sidecar_fd,
            record.request_fd,
            record.run_fd,
            record.root_fd,
        ):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _current_closure(self) -> CompleteEngineClosureAttestation:
        try:
            value = self._attest_closure()
        except EngineSpawnError:
            raise
        except BaseException as exc:
            raise EngineSpawnError(
                "ENGINE_CLOSURE_UNAVAILABLE", "complete closure attestation failed"
            ) from exc
        return _validate_closure(value)

    def _current_inputs(
        self, request: RunBacktest | RunBacktestSimulation
    ) -> tuple[HashBoundEngineInput, ...]:
        if self._attest_inputs is None:
            return ()
        try:
            value = self._attest_inputs(request)
        except EngineSpawnError:
            raise
        except BaseException as exc:
            raise EngineSpawnError(
                "ENGINE_INPUT_AUTHORITY_UNAVAILABLE",
                "engine input authority attestation failed",
            ) from exc
        return _validate_inputs(value, request)

    def _open_transport_root(self) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            observed = self._transport_root.lstat()
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
                _blocked("ENGINE_TRANSPORT_UNSAFE", "transport root is not a real directory")
            if observed.st_uid != os.getuid() or observed.st_mode & 0o077:
                _blocked("ENGINE_TRANSPORT_UNSAFE", "transport root is not private")
            descriptor = os.open(self._transport_root, flags)
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino)
                != (observed.st_dev, observed.st_ino)
                or opened.st_uid != os.getuid()
                or opened.st_mode & 0o077
            ):
                _blocked("ENGINE_TRANSPORT_UNSAFE", "transport root identity changed")
            return descriptor
        except EngineSpawnError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise EngineSpawnError(
                "ENGINE_TRANSPORT_UNSAFE", "transport root cannot be opened safely"
            ) from exc

    @staticmethod
    def _create_file(run_fd: int, name: str, value: bytes) -> tuple[int, _InputIdentity]:
        write_fd = -1
        read_fd = -1
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            write_fd = os.open(name, flags, 0o400, dir_fd=run_fd)
            os.fchmod(write_fd, 0o400)
            remaining = memoryview(value)
            while remaining:
                written = os.write(write_fd, remaining)
                if written <= 0:
                    raise OSError("short transport write")
                remaining = remaining[written:]
            os.fsync(write_fd)
            os.close(write_fd)
            write_fd = -1
            read_fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=run_fd,
            )
            observed = os.fstat(read_fd)
            identity = _InputIdentity(
                observed.st_dev,
                observed.st_ino,
                observed.st_size,
                stat.S_IMODE(observed.st_mode),
                hashlib.sha256(value).hexdigest(),
            )
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_nlink != 1
                or observed.st_uid != os.getuid()
                or identity.mode != 0o400
                or identity.size != len(value)
            ):
                _blocked("ENGINE_TRANSPORT_UNSAFE", "sealed input is not a private regular file")
            return read_fd, identity
        except BaseException:
            if write_fd >= 0:
                os.close(write_fd)
            if read_fd >= 0:
                os.close(read_fd)
            raise

    def prepare(self, envelope: EngineCommandEnvelope) -> PreparedEngineSpawn:
        if type(envelope) is not EngineCommandEnvelope or type(envelope.payload) not in {
            RunBacktest,
            RunBacktestSimulation,
        }:
            _blocked("ENGINE_REQUEST_INVALID", "exact backtest envelope is required")
        closure = self._current_closure()
        expected_profile = (
            "zero-order"
            if type(envelope.payload) is RunBacktest
            else "execution-simulation"
        )
        if closure.profile != expected_profile:
            _blocked(
                "ENGINE_CLOSURE_PROFILE_MISMATCH",
                "engine closure profile does not match the command",
            )
        inputs = self._current_inputs(envelope.payload)
        inputs_sha256 = _inputs_sha256(inputs)
        request = canonical_json_bytes(envelope)
        request_sha256 = hashlib.sha256(request).hexdigest()
        sidecar = request_sha256.encode("ascii") + b"\n"
        run_name = f"run-{envelope.engine_run_id.hex}"
        root_fd = self._open_transport_root()
        run_fd = request_fd = sidecar_fd = -1
        try:
            try:
                os.mkdir(run_name, mode=0o700, dir_fd=root_fd)
            except FileExistsError as exc:
                raise EngineSpawnError(
                    "ENGINE_TRANSPORT_PREEXISTING",
                    "engine run transport already exists",
                ) from exc
            run_fd = os.open(
                run_name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
            os.fchmod(run_fd, 0o700)
            request_fd, request_identity = self._create_file(run_fd, "request.json", request)
            sidecar_fd, sidecar_identity = self._create_file(
                run_fd, "request.sha256", sidecar
            )
            os.fsync(run_fd)
            issued_at = self._monotonic_ns()
            if isinstance(issued_at, bool) or not isinstance(issued_at, int):
                _blocked("ENGINE_CLOCK_INVALID", "monotonic clock returned an invalid value")
            fingerprint = hashlib.sha256(
                (
                    closure.closure_sha256
                    + closure.sandbox.profile_sha256
                    + request_sha256
                    + inputs_sha256
                    + str(issued_at)
                ).encode("ascii")
            ).hexdigest()
            record = _PreparedRecord(
                closure,
                envelope.payload,
                inputs,
                inputs_sha256,
                request_sha256,
                request_fd,
                sidecar_fd,
                root_fd,
                run_fd,
                run_name,
                request_identity,
                sidecar_identity,
                issued_at,
                issued_at + _PREPARED_TTL_NS,
                fingerprint,
            )
            prepared = PreparedEngineSpawn()
            object.__setattr__(prepared, "_provider", self)
            object.__setattr__(prepared, "_record", record)
            with self._issue_lock:
                self._issued.add(prepared)
            return prepared
        except BaseException:
            for descriptor in (sidecar_fd, request_fd, run_fd, root_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise

    @staticmethod
    def _verify_input(
        run_fd: int, name: str, descriptor: int, expected: _InputIdentity
    ) -> bytes:
        try:
            opened = os.fstat(descriptor)
            named = os.stat(name, dir_fd=run_fd, follow_symlinks=False)
        except OSError as exc:
            raise EngineSpawnError(
                "ENGINE_INPUT_STALE", "sealed engine input is unavailable"
            ) from exc
        opened_identity = (opened.st_dev, opened.st_ino)
        named_identity = (named.st_dev, named.st_ino)
        if (
            opened_identity != (expected.device, expected.inode)
            or named_identity != opened_identity
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or opened.st_uid != os.getuid()
            or named.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o400
            or stat.S_IMODE(named.st_mode) != 0o400
            or opened.st_size != expected.size
            or named.st_size != expected.size
        ):
            _blocked("ENGINE_INPUT_STALE", "sealed engine input identity changed")
        value = _read_fd(descriptor, expected.size)
        if len(value) != expected.size or not hmac.compare_digest(
            hashlib.sha256(value).hexdigest(), expected.sha256
        ):
            _blocked("ENGINE_INPUT_STALE", "sealed engine input bytes changed")
        return value

    def _consume(self, prepared: PreparedEngineSpawn) -> EngineBuiltSpawn:
        with self._issue_lock:
            if type(prepared) is not PreparedEngineSpawn or prepared not in self._issued:
                _blocked(
                    "ENGINE_PREPARED_SPAWN_INVALID",
                    "current prepared engine spawn is required",
                )
            self._issued.discard(prepared)
            record = prepared._record
        launch_fds: list[int] = []
        transfer_fds = False
        try:
            now = self._monotonic_ns()
            if isinstance(now, bool) or not isinstance(now, int):
                _blocked("ENGINE_CLOCK_INVALID", "monotonic clock returned an invalid value")
            if now > record.deadline_ns:
                _blocked("ENGINE_PREPARED_SPAWN_EXPIRED", "prepared engine spawn expired")
            closure = self._current_closure()
            if closure != record.closure:
                _blocked("ENGINE_CLOSURE_STALE", "complete engine closure changed before spawn")
            inputs = self._current_inputs(record.request)
            if inputs != record.inputs or _inputs_sha256(inputs) != record.inputs_sha256:
                _blocked("ENGINE_INPUT_STALE", "hash-bound engine inputs changed before spawn")
            run_named = os.stat(record.run_name, dir_fd=record.root_fd, follow_symlinks=False)
            run_opened = os.fstat(record.run_fd)
            if (
                not stat.S_ISDIR(run_named.st_mode)
                or (run_named.st_dev, run_named.st_ino)
                != (run_opened.st_dev, run_opened.st_ino)
            ):
                _blocked("ENGINE_INPUT_STALE", "engine transport directory changed")
            request = self._verify_input(
                record.run_fd, "request.json", record.request_fd, record.request_identity
            )
            sidecar = self._verify_input(
                record.run_fd,
                "request.sha256",
                record.sidecar_fd,
                record.sidecar_identity,
            )
            actual_request_sha256 = hashlib.sha256(request).hexdigest()
            if (
                not hmac.compare_digest(actual_request_sha256, record.request_sha256)
                or sidecar != actual_request_sha256.encode("ascii") + b"\n"
            ):
                _blocked("ENGINE_INPUT_STALE", "engine request and digest sidecar diverged")
            now = self._monotonic_ns()
            if isinstance(now, bool) or not isinstance(now, int):
                _blocked("ENGINE_CLOCK_INVALID", "monotonic clock returned an invalid value")
            if now > record.deadline_ns:
                _blocked(
                    "ENGINE_PREPARED_SPAWN_EXPIRED",
                    "prepared engine spawn expired during final attestation",
                )

            request_snapshot_fd = _sealed_memfd(
                "engine-request", request, mode=0o400
            )
            launch_fds.append(request_snapshot_fd)
            sidecar_snapshot_fd = _sealed_memfd(
                "engine-request-sha256", sidecar, mode=0o400
            )
            launch_fds.append(sidecar_snapshot_fd)
            input_snapshot_fds: list[int] = []
            for artifact in inputs:
                descriptor = _sealed_memfd(
                    f"engine-input-{artifact.name}",
                    _verified_closure_file(_input_mount(artifact)),
                    mode=0o400,
                )
                input_snapshot_fds.append(descriptor)
                launch_fds.append(descriptor)
            sandbox_snapshot_fd = _sealed_sandbox_snapshot(closure.sandbox)
            launch_fds.append(sandbox_snapshot_fd)
            closure_snapshot_fds: list[int] = []
            for mount in closure.mounts:
                descriptor = _sealed_closure_file_snapshot(mount)
                closure_snapshot_fds.append(descriptor)
                launch_fds.append(descriptor)
            mount_fds = tuple(closure_snapshot_fds)

            directory_arguments = tuple(
                argument
                for directory in _closure_target_directories(closure.mounts)
                for argument in ("--dir", str(directory))
            )
            mount_arguments = tuple(
                argument
                for mount, descriptor in zip(closure.mounts, mount_fds, strict=True)
                for argument in (
                    "--perms",
                    f"{mount.mode:04o}",
                    "--ro-bind-data",
                    str(descriptor),
                    str(mount.target),
                )
            )
            input_arguments = tuple(
                argument
                for artifact, descriptor in zip(inputs, input_snapshot_fds, strict=True)
                for argument in (
                    "--perms",
                    "0400",
                    "--ro-bind-data",
                    str(descriptor),
                    str(_input_target(artifact.name, artifact.reference)),
                )
            )
            argv = (
                f"/proc/self/fd/{sandbox_snapshot_fd}",
                "--die-with-parent",
                "--unshare-user",
                "--unshare-pid",
                "--unshare-net",
                "--new-session",
                "--clearenv",
                *directory_arguments,
                "--dir",
                "/inputs/artifacts",
                *mount_arguments,
                *input_arguments,
                "--perms",
                "0400",
                "--ro-bind-data",
                str(request_snapshot_fd),
                str(_INPUT_TARGET),
                "--perms",
                "0400",
                "--ro-bind-data",
                str(sidecar_snapshot_fd),
                str(_SIDECAR_TARGET),
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--chdir",
                "/",
                str(closure.entrypoint),
                *closure.argv_prefix,
                str(_INPUT_TARGET),
                str(_SIDECAR_TARGET),
            )
            inherited = tuple(launch_fds)
            transfer_fds = True
            return EngineBuiltSpawn(
                argv=argv,
                cwd=Path("/"),
                environment=MappingProxyType({}),
                pass_fds=inherited,
                close_after_spawn_fds=inherited,
                timeout_seconds=closure.timeout_seconds,
                result_validator_id=closure.result_validator_id,
                capability_fingerprint=record.fingerprint,
                source_revision=closure.source_commit,
                lineage=EngineSpawnLineage(
                    closure.closure_sha256,
                    closure.sandbox.profile_sha256,
                    record.request_sha256,
                ),
            )
        except OSError as exc:
            raise EngineSpawnError(
                "ENGINE_INPUT_STALE", "engine spawn authority cannot be revalidated"
            ) from exc
        finally:
            for descriptor in (
                record.sidecar_fd,
                record.request_fd,
                record.run_fd,
                record.root_fd,
            ):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if not transfer_fds:
                for descriptor in launch_fds:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def consume_prepared_engine_spawn(prepared: PreparedEngineSpawn) -> EngineBuiltSpawn:
    """Consume one provider-issued authority at the runner's Popen boundary."""

    if type(prepared) is not PreparedEngineSpawn:
        _blocked("ENGINE_PREPARED_SPAWN_INVALID", "prepared engine spawn type is invalid")
    try:
        provider = prepared._provider
    except AttributeError as exc:
        raise EngineSpawnError(
            "ENGINE_PREPARED_SPAWN_INVALID", "prepared engine spawn is unissued"
        ) from exc
    return provider._consume(prepared)


__all__ = [
    "CompleteEngineClosureAttestation",
    "EngineBuiltSpawn",
    "EngineSpawnError",
    "EngineSpawnLineage",
    "EngineSpawnProvider",
    "HashBoundEngineInput",
    "OsSandboxProof",
    "PreparedEngineSpawn",
    "ReadOnlyClosureMount",
    "consume_prepared_engine_spawn",
]
