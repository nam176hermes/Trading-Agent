"""Tracked bounded process harness for approved Package 6 operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess
import time
from urllib.request import urlopen
import weakref
from uuid import uuid4
from typing import Callable

from packages.runtime_release.paper_backend.provider_free_fixture import (
    load_provider_free_fixture,
)
from packages.runtime_release.staging_v2 import (
    PACKAGE6_APPROVAL_SHA256_ENV,
    STAGING_ACTIVATION_PATH_ENV,
    STAGING_AUTHORITY_PATH_ENV,
    STAGING_SCOPE_ENV,
    load_staging_authority_material,
)
from scripts.validate_package6_runtime_approval import (
    ValidatedPackage6Capability,
    is_issued_capability,
)


_READINESS_PROBE_TIMEOUT_SECONDS = 5.0


def _raise_readiness_probe_timeout(_signum: int, _frame: object) -> None:
    raise TimeoutError("readiness probe exceeded approved timeout")


class SourceDrift(RuntimeError):
    """Candidate identity changed immediately before process creation."""


class EvidenceIncomplete(RuntimeError):
    """The evidence bundle cannot support controller verification."""


@dataclass(frozen=True, slots=True)
class SpawnEnvironmentEvidence:
    component: str
    operation_id: str
    pid: int
    process_group: int
    start_ticks: int
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessEvidence:
    operation_id: str
    argv: tuple[str, ...]
    cwd: str
    shell: bool
    stdin_closed: bool
    pid: int
    process_group: int
    exit_code: int | None
    timed_out: bool
    stdout_sha256: str
    stdout_size: int
    stdout_truncated: bool
    stderr_sha256: str
    stderr_size: int
    stderr_truncated: bool
    pid_alive: bool
    listener_alive: bool
    start_ticks: int
    process_group_alive: bool
    listener_negative_probes: int
    cleanup_proven: bool
    environment: SpawnEnvironmentEvidence


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    root: Path
    process: ProcessEvidence


@dataclass(frozen=True, slots=True)
class TrackedProcessIdentity:
    operation_id: str
    component: str
    pid: int
    process_group: int
    start_ticks: int
    environment: SpawnEnvironmentEvidence


@dataclass(frozen=True, slots=True)
class StopEvidence:
    operation_id: str
    pid: int
    process_group: int
    start_ticks: int
    listener_negative_probes: int
    exit_code: int
    cleanup_proven: bool


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    operation_id: str
    pid: int
    start_ticks: int
    listener_inode: int
    attempts: int
    status: str


@dataclass(frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True)
class RuntimeChildAuthorities:
    job_api_credentials: Path
    worker_credentials: Path
    fixture_authority: Path
    staging_scope: str
    staging_authority: Path
    staging_activation: Path
    package6_approval_sha256: str
    _parent: ValidatedPackage6Capability
    _authority_pin: tuple[object, ...]
    _dynamic_pin: tuple[object, ...]
    _path_identities: tuple[tuple[int, int], ...]
    _credential_pins: tuple[tuple[object, ...], ...]


_ISSUED_CHILD_AUTHORITIES: weakref.WeakSet[RuntimeChildAuthorities] = weakref.WeakSet()
_JOB_API_CREDENTIAL_NAMES = (
    "database-host", "database-port", "database-name", "database-password",
    "job-api-principal-type", "job-api-principal-id", "job-api-token",
)
_WORKER_CREDENTIAL_NAMES = (
    "database-host", "database-port", "database-name", "database-password",
)
_MAX_CREDENTIAL_BYTES = 4096


def _pin_credential_directory(
    path: Path, required_names: tuple[str, ...]
) -> tuple[object, ...]:
    directory_fd = -1
    try:
        directory_fd = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        directory = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.geteuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
            or tuple(sorted(os.listdir(directory_fd))) != tuple(sorted(required_names))
        ):
            raise ValueError
        pins: list[tuple[object, ...]] = []
        for name in required_names:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            try:
                before = os.fstat(descriptor)
                mode = stat.S_IMODE(before.st_mode)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_uid != os.geteuid()
                    or mode & 0o022
                    or not 1 <= before.st_size <= _MAX_CREDENTIAL_BYTES
                ):
                    raise ValueError
                raw = os.read(descriptor, _MAX_CREDENTIAL_BYTES + 1)
                after = os.fstat(descriptor)
                value = raw.decode("utf-8")
                if (
                    any(
                        getattr(before, field) != getattr(after, field)
                        for field in (
                            "st_dev", "st_ino", "st_uid", "st_gid", "st_mode",
                            "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
                        )
                    )
                    or len(raw) != before.st_size
                    or not value
                    or value.strip() != value
                    or any(character in value for character in "\x00\n\r")
                ):
                    raise ValueError
                pins.append((
                    name, before.st_dev, before.st_ino, before.st_uid,
                    before.st_gid, mode, before.st_nlink, before.st_size,
                    hashlib.sha256(raw).digest(),
                ))
            finally:
                os.close(descriptor)
        return (directory.st_dev, directory.st_ino, tuple(pins))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("runtime credential directory policy is invalid") from error
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def issue_runtime_child_authorities(
    capability: ValidatedPackage6Capability,
    *,
    job_api_credentials: Path,
    worker_credentials: Path,
) -> RuntimeChildAuthorities:
    if not is_issued_capability(capability):
        raise TypeError("validated Package 6 capability is required")
    credential_pins = (
        _pin_credential_directory(job_api_credentials, _JOB_API_CREDENTIAL_NAMES),
        _pin_credential_directory(worker_credentials, _WORKER_CREDENTIAL_NAMES),
    )
    material = _reload_child_material(capability)
    fixture_authority = capability.fixture.path
    load_provider_free_fixture(
        fixture_authority,
        expected_backend_commit=capability.source_commit,
        expected_package6_approval_sha256=capability.approval_sha256,
        now=datetime.now(UTC),
        trusted_uid=os.geteuid(),
    )
    from services.job_store.config import read_systemd_credential

    def credential(directory: Path, name: str) -> str:
        return read_systemd_credential(
            {"CREDENTIALS_DIRECTORY": str(directory)}, name
        )

    expected_database = {
        "database-host": capability.postgres.bind_host,
        "database-port": str(capability.postgres.port),
        "database-name": capability.postgres.database_name,
    }
    for directory in (job_api_credentials, worker_credentials):
        if any(
            credential(directory, name) != expected
            for name, expected in expected_database.items()
        ):
            raise ValueError("runtime credential database identity does not match")
        credential(directory, "database-password")
    if (
        credential(job_api_credentials, "job-api-principal-type") != "OPERATOR"
        or credential(job_api_credentials, "job-api-principal-id")
        != "foundation-validation"
    ):
        raise ValueError("Job API request identity does not match approval")
    credential(job_api_credentials, "job-api-token")
    value = RuntimeChildAuthorities()
    object.__setattr__(value, "job_api_credentials", job_api_credentials)
    object.__setattr__(value, "worker_credentials", worker_credentials)
    object.__setattr__(value, "fixture_authority", fixture_authority)
    object.__setattr__(value, "staging_scope", material.scope)
    object.__setattr__(value, "staging_authority", material.authority_path)
    object.__setattr__(value, "staging_activation", material.activation_path)
    object.__setattr__(
        value, "package6_approval_sha256", material.package6_approval_sha256
    )
    object.__setattr__(value, "_parent", capability)
    object.__setattr__(value, "_authority_pin", material.authority_pin)
    object.__setattr__(value, "_dynamic_pin", material.dynamic_evidence_pin)
    object.__setattr__(
        value,
        "_path_identities",
        tuple(
            (path.lstat().st_dev, path.lstat().st_ino)
            for path in (
                job_api_credentials,
                worker_credentials,
                material.authority_path,
                material.activation_path,
                fixture_authority,
            )
        ),
    )
    object.__setattr__(value, "_credential_pins", credential_pins)
    _ISSUED_CHILD_AUTHORITIES.add(value)
    return value


def _reload_child_material(capability: ValidatedPackage6Capability):
    try:
        return load_staging_authority_material(
            {
                STAGING_SCOPE_ENV: capability.staging_material.scope,
                STAGING_AUTHORITY_PATH_ENV: str(
                    capability.staging_material.authority_path
                ),
                STAGING_ACTIVATION_PATH_ENV: str(
                    capability.staging_material.activation_path
                ),
                PACKAGE6_APPROVAL_SHA256_ENV: capability.approval_sha256,
            },
            now=datetime.now(UTC),
        )
    except Exception as error:
        raise SourceDrift("runtime child authority is unavailable") from error


def _attest_child_authorities(
    capability: ValidatedPackage6Capability,
    child: RuntimeChildAuthorities,
) -> None:
    if (
        child not in _ISSUED_CHILD_AUTHORITIES
        or child._parent is not capability
    ):
        raise TypeError("issued runtime child authorities are required")
    material = _reload_child_material(capability)
    paths = (
        child.job_api_credentials,
        child.worker_credentials,
        child.staging_authority,
        child.staging_activation,
        child.fixture_authority,
    )
    try:
        metadata = tuple(path.lstat() for path in paths)
        identities = tuple((info.st_dev, info.st_ino) for info in metadata)
        fixture = load_provider_free_fixture(
            child.fixture_authority,
            expected_backend_commit=capability.source_commit,
            expected_package6_approval_sha256=capability.approval_sha256,
            now=datetime.now(UTC),
            trusted_uid=os.geteuid(),
        )
        credential_pins = (
            _pin_credential_directory(
                child.job_api_credentials, _JOB_API_CREDENTIAL_NAMES
            ),
            _pin_credential_directory(
                child.worker_credentials, _WORKER_CREDENTIAL_NAMES
            ),
        )
    except Exception as error:
        raise SourceDrift("runtime child authority is unavailable") from error
    if (
        identities != child._path_identities
        or credential_pins != child._credential_pins
        or any(
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            for info in metadata[:2]
        )
        or material.authority_pin != child._authority_pin
        or material.dynamic_evidence_pin != child._dynamic_pin
        or material.scope != child.staging_scope
        or material.authority_path != child.staging_authority
        or material.activation_path != child.staging_activation
        or material.package6_approval_sha256 != child.package6_approval_sha256
        or fixture.sha256 != capability.fixture.sha256
        or fixture.provenance != capability.fixture.provenance
    ):
        raise SourceDrift("runtime child authority changed before spawn")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_exclusive(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_private_directory(path: Path, *, create: bool) -> int:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path.parts[:2] != ("/", "tmp")
    ):
        raise EvidenceIncomplete("evidence directory path is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = os.open("/", flags)
    try:
        for index, component in enumerate(path.parts[1:]):
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
            info = os.fstat(current)
            final = index == len(path.parts[1:]) - 1
            if not stat.S_ISDIR(info.st_mode):
                raise EvidenceIncomplete("evidence directory is not regular")
            if index > 0 and (
                info.st_uid != os.geteuid() or info.st_mode & 0o022
            ):
                raise EvidenceIncomplete("evidence directory ancestor is unsafe")
            if final and (
                info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise EvidenceIncomplete("evidence directory policy is invalid")
        return current
    except Exception:
        os.close(current)
        raise


def _write_exclusive_at(directory_fd: int, name: str, raw: bytes) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | os.O_CLOEXEC,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(raw)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _start_ticks(pid: int) -> int:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return -1


def _process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Package6Controller:
    """Consume one validated private capability; never accepts raw approval data."""

    def __init__(
        self,
        capability: ValidatedPackage6Capability,
        *,
        child_authorities: RuntimeChildAuthorities | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not is_issued_capability(capability):
            raise TypeError("validated Package 6 capability is required")
        self._capability = capability
        self._monotonic = monotonic
        if child_authorities is not None and child_authorities not in _ISSUED_CHILD_AUTHORITIES:
            raise TypeError("issued runtime child authorities are required")
        if child_authorities is not None:
            _attest_child_authorities(capability, child_authorities)
        self._child_authorities = child_authorities
        self._tracked: dict[str, tuple[subprocess.Popen[bytes], TrackedProcessIdentity]] = {}

    def _preflight(self, operation) -> None:
        self._validate_bindings(self._capability.source_root)
        if self._child_authorities is not None:
            _attest_child_authorities(self._capability, self._child_authorities)
        if operation.action == "START":
            executable = Path(operation.argv[0])
            try:
                info = executable.lstat()
                raw = executable.read_bytes()
            except OSError as error:
                raise SourceDrift("approved executable is unavailable") from error
            if (
                executable.is_symlink()
                or not executable.is_file()
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or hashlib.sha256(raw).hexdigest()
                != operation.executable_sha256
            ):
                raise SourceDrift("approved executable identity changed before spawn")

    def start(self, operation_id: str) -> TrackedProcessIdentity:
        operation = self._capability.operations.get(operation_id)
        if operation is None or operation.action != "START":
            raise ValueError("exact approved START operation is required")
        if operation.component in self._tracked:
            raise RuntimeError("component already has a tracked process")
        if len(self._tracked) >= self._capability.max_processes:
            raise RuntimeError("approved process limit reached")
        self._preflight(operation)
        if operation.bind_host is not None and operation.port is not None:
            if self._listener_alive(operation.bind_host, operation.port):
                raise RuntimeError("approved listener is already occupied")
        child_environment = self._child_environment(operation.component)
        process = subprocess.Popen(
            list(operation.argv),
            cwd=str(operation.cwd),
            env=child_environment,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pgid = os.getpgid(process.pid)
        start_ticks = _start_ticks(process.pid)
        environment = SpawnEnvironmentEvidence(
            operation.component, operation.operation_id, process.pid, pgid,
            start_ticks, tuple(sorted(child_environment)),
        )
        identity = TrackedProcessIdentity(
            operation_id=operation.operation_id,
            component=operation.component,
            pid=process.pid,
            process_group=pgid,
            start_ticks=start_ticks,
            environment=environment,
        )
        if pgid != process.pid or identity.start_ticks < 0:
            self._terminate(process)
            raise RuntimeError("spawned process identity is invalid")
        self._tracked[operation.component] = (process, identity)
        return identity

    def stop(self, operation_id: str) -> StopEvidence:
        operation = self._capability.operations.get(operation_id)
        if operation is None or operation.action != "STOP":
            raise ValueError("exact approved STOP operation is required")
        tracked = self._tracked.pop(operation.component, None)
        if tracked is None:
            raise RuntimeError("STOP has no tracked process identity")
        process, identity = tracked
        if process.poll() is None and _start_ticks(identity.pid) != identity.start_ticks:
            raise RuntimeError("tracked process identity changed before STOP")
        self._terminate(process)
        negative = 0
        listener_alive = False
        if operation.bind_host is not None and operation.port is not None:
            for _ in range(3):
                listener_alive = self._listener_alive(
                    operation.bind_host, operation.port
                )
                if listener_alive:
                    break
                negative += 1
        cleanup = (
            process.poll() is not None
            and not _pid_alive(identity.pid)
            and not _process_group_alive(identity.process_group)
            and not listener_alive
            and (operation.port is None or negative == 3)
        )
        return StopEvidence(
            operation_id=operation.operation_id,
            pid=identity.pid,
            process_group=identity.process_group,
            start_ticks=identity.start_ticks,
            listener_negative_probes=negative,
            exit_code=process.returncode,
            cleanup_proven=cleanup,
        )

    def wait_ready(self, operation_id: str) -> ReadinessEvidence:
        operation = self._capability.operations.get(operation_id)
        if (
            operation is None
            or operation.action != "START"
            or operation.bind_host is None
            or operation.port is None
        ):
            raise ValueError("exact approved listener START operation is required")
        tracked = self._tracked.get(operation.component)
        if tracked is None:
            raise RuntimeError("readiness requires a tracked listener process")
        process, identity = tracked
        deadline = self._monotonic() + self._capability.startup_timeout_seconds
        attempts = 0
        while self._monotonic() < deadline:
            attempts += 1
            if process.poll() is not None or _start_ticks(identity.pid) != identity.start_ticks:
                raise RuntimeError("tracked listener exited before readiness")
            inode = self._listener_inode(
                operation.bind_host, operation.port, identity.pid
            )
            if inode >= 0:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                probe_timeout = min(_READINESS_PROBE_TIMEOUT_SECONDS, remaining)
                if any(signal.getitimer(signal.ITIMER_REAL)):
                    raise RuntimeError("readiness probe timer authority is unavailable")
                previous_handler = signal.signal(
                    signal.SIGALRM, _raise_readiness_probe_timeout
                )
                try:
                    try:
                        signal.setitimer(signal.ITIMER_REAL, probe_timeout)
                        with urlopen(  # noqa: S310 - exact approved loopback URL
                            f"http://{operation.bind_host}:{operation.port}/health/ready",
                            timeout=probe_timeout,
                        ) as response:
                            response_status = response.status
                            payload = json.loads(response.read(16 * 1024))
                    finally:
                        try:
                            signal.setitimer(signal.ITIMER_REAL, 0.0)
                        finally:
                            signal.signal(signal.SIGALRM, previous_handler)
                    ready = (
                        response_status == 200
                        and payload.get("data", {}).get("status") == "READY"
                    )
                    if self._monotonic() >= deadline:
                        break
                    if ready:
                        return ReadinessEvidence(
                            operation_id=operation.operation_id,
                            pid=identity.pid,
                            start_ticks=identity.start_ticks,
                            listener_inode=inode,
                            attempts=attempts,
                            status="READY",
                        )
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        raise RuntimeError("approved Job API readiness timeout")

    @staticmethod
    def _listener_inode(host: str, port: int, pid: int) -> int:
        if host != "127.0.0.1":
            return -1
        target = f"0100007F:{port:04X}"
        inodes: set[str] = set()
        try:
            for raw in Path("/proc/net/tcp").read_text(encoding="ascii").splitlines()[1:]:
                fields = raw.split()
                if len(fields) > 9 and fields[1] == target and fields[3] == "0A":
                    inodes.add(fields[9])
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    link = os.readlink(fd)
                except OSError:
                    continue
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    return int(link[8:-1])
        except OSError:
            return -1
        return -1

    def _child_environment(self, component: str) -> dict[str, str]:
        child = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._capability.disposable_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "TRADING_MODE": "paper",
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
            "LIVE_TRADING_ENABLED": "false",
        }
        if self._child_authorities is not None:
            _attest_child_authorities(self._capability, self._child_authorities)
            credentials = (
                self._child_authorities.job_api_credentials
                if component == "JOB_API"
                else self._child_authorities.worker_credentials
            )
            child["CREDENTIALS_DIRECTORY"] = str(credentials)
            child.update(
                {
                    STAGING_SCOPE_ENV: self._child_authorities.staging_scope,
                    STAGING_AUTHORITY_PATH_ENV: str(
                        self._child_authorities.staging_authority
                    ),
                    STAGING_ACTIVATION_PATH_ENV: str(
                        self._child_authorities.staging_activation
                    ),
                    PACKAGE6_APPROVAL_SHA256_ENV: (
                        self._child_authorities.package6_approval_sha256
                    ),
                }
            )
            if component == "WORKER":
                child.update(
                    {
                        "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH": str(
                            self._child_authorities.fixture_authority
                        ),
                    }
                )
        return child

    def run_once(self, operation_id: str) -> EvidenceBundle:
        operation = self._capability.operations.get(operation_id)
        if operation is None or operation.action != "START":
            raise ValueError("exact approved START operation is required")
        self._preflight(operation)
        if operation.bind_host is not None and operation.port is not None:
            if self._listener_alive(operation.bind_host, operation.port):
                raise RuntimeError("approved listener is already occupied")
        child_environment = self._child_environment(operation.component)
        process = subprocess.Popen(
            list(operation.argv),
            cwd=str(operation.cwd),
            env=child_environment,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pgid = os.getpgid(process.pid)
        start_ticks = _start_ticks(process.pid)
        environment = SpawnEnvironmentEvidence(
            operation.component, operation.operation_id, process.pid, pgid,
            start_ticks, tuple(sorted(child_environment)),
        )
        if pgid != process.pid:
            self._terminate(process)
            raise RuntimeError("spawned process group identity is invalid")
        stdout, stderr, timed_out = self._capture(process)
        cleanup_proven = self._terminate(process)
        negative_probes = 0
        listener_alive = False
        if operation.bind_host is not None and operation.port is not None:
            for _ in range(3):
                listener_alive = self._listener_alive(
                    operation.bind_host, operation.port
                )
                if listener_alive:
                    break
                negative_probes += 1
        evidence = ProcessEvidence(
            operation_id=operation.operation_id,
            argv=operation.argv,
            cwd=str(operation.cwd),
            shell=False,
            stdin_closed=True,
            pid=process.pid,
            process_group=pgid,
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stdout_size=len(stdout),
            stdout_truncated=len(stdout) == self._capability.max_output_bytes,
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            stderr_size=len(stderr),
            stderr_truncated=len(stderr) == self._capability.max_output_bytes,
            pid_alive=_pid_alive(process.pid),
            listener_alive=listener_alive,
            start_ticks=start_ticks,
            process_group_alive=_process_group_alive(pgid),
            listener_negative_probes=negative_probes,
            cleanup_proven=(
                cleanup_proven
                and not _process_group_alive(pgid)
                and not listener_alive
                and (
                    operation.port is None
                    or negative_probes == 3
                )
            ),
            environment=environment,
        )
        return self._write_bundle(evidence, stdout, stderr)

    @staticmethod
    def _listener_alive(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            return False

    def _validate_bindings(self, root: Path) -> None:
        for relative, expected in self._capability.source_bindings:
            path = root / relative
            try:
                info = path.lstat()
                raw = path.read_bytes()
            except OSError as error:
                raise SourceDrift("source binding unavailable immediately before spawn") from error
            if (
                path.is_symlink()
                or not path.is_file()
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or hashlib.sha256(raw).hexdigest() != expected
            ):
                raise SourceDrift("source binding drift detected immediately before spawn")

    def _capture(self, process: subprocess.Popen[bytes]) -> tuple[bytes, bytes, bool]:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("bounded child pipes are unavailable")
        selector = selectors.DefaultSelector()
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        deadline = self._monotonic() + self._capability.operation_timeout_seconds
        timed_out = False
        try:
            while selector.get_map():
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    timed_out = True
                    self._signal_group(process, signal.SIGTERM)
                    break
                for key, _mask in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    target = buffers[key.data]
                    capacity = self._capability.max_output_bytes - len(target)
                    target.extend(chunk[: max(0, capacity)])
                if process.poll() is not None and not selector.get_map():
                    break
            if timed_out:
                self._wait_or_kill(process)
            else:
                process.wait(timeout=self._capability.cleanup_timeout_seconds)
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        return bytes(buffers["stdout"]), bytes(buffers["stderr"]), timed_out

    def _signal_group(self, process: subprocess.Popen[bytes], sig: int) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return

    def _wait_or_kill(self, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=self._capability.cleanup_timeout_seconds)
        except subprocess.TimeoutExpired:
            self._signal_group(process, signal.SIGKILL)
            process.wait(timeout=self._capability.cleanup_timeout_seconds)

    def _terminate(self, process: subprocess.Popen[bytes]) -> bool:
        if process.poll() is None:
            self._signal_group(process, signal.SIGTERM)
            self._wait_or_kill(process)
        deadline = self._monotonic() + self._capability.cleanup_timeout_seconds
        if _process_group_alive(process.pid):
            self._signal_group(process, signal.SIGTERM)
        while _process_group_alive(process.pid) and self._monotonic() < deadline:
            time.sleep(0.02)
        if _process_group_alive(process.pid):
            self._signal_group(process, signal.SIGKILL)
            kill_deadline = (
                self._monotonic() + self._capability.cleanup_timeout_seconds
            )
            while (
                _process_group_alive(process.pid)
                and self._monotonic() < kill_deadline
            ):
                time.sleep(0.02)
        return (
            process.poll() is not None
            and not _pid_alive(process.pid)
            and not _process_group_alive(process.pid)
        )

    def _write_bundle(
        self, evidence: ProcessEvidence, stdout: bytes, stderr: bytes
    ) -> EvidenceBundle:
        root = self._capability.evidence_root / f"run-{uuid4().hex}"
        evidence_fd = _open_private_directory(
            self._capability.evidence_root, create=True
        )
        try:
            os.mkdir(root.name, 0o700, dir_fd=evidence_fd)
            root_fd = os.open(
                root.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=evidence_fd,
            )
        finally:
            os.close(evidence_fd)
        files = {
            "approval.json": _canonical_json(
                {
                    "approval_sha256": self._capability.approval_sha256,
                    "source_commit": self._capability.source_commit,
                    "source_tree": self._capability.source_tree,
                    "fixture_sha256": self._capability.fixture_sha256,
                }
            ),
            "process.json": _canonical_json(asdict(evidence)),
            "stdout.bin": stdout,
            "stderr.bin": stderr,
        }
        try:
            for name, raw in files.items():
                _write_exclusive_at(root_fd, name, raw)
            entries = [
                {
                    "path": name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                }
                for name, raw in sorted(files.items())
            ]
            index = {
                "schema_version": 1,
                "verdict": "PENDING_CONTROLLER_RUNTIME_VERIFICATION",
                "entries": entries,
            }
            _write_exclusive_at(root_fd, "index.json", _canonical_json(index))
        finally:
            os.close(root_fd)
        if not evidence.cleanup_proven:
            raise EvidenceIncomplete("process cleanup proof is incomplete")
        verify_evidence_bundle(root)
        return EvidenceBundle(root=root, process=evidence)


_PROCESS_FIELDS = {
    field.name for field in ProcessEvidence.__dataclass_fields__.values()
}


def verify_evidence_bundle(root: Path) -> bool:
    try:
        (root / "runtime.json").lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise EvidenceIncomplete("runtime evidence cannot be inspected") from error
    else:
        from .evidence import verify_runtime_evidence_bundle

        return verify_runtime_evidence_bundle(root)
    from .evidence import _safe_read

    try:
        index = json.loads(_safe_read(root, "index.json"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceIncomplete("evidence index is unavailable") from error
    if (
        not isinstance(index, dict)
        or set(index) != {"schema_version", "verdict", "entries"}
        or index["schema_version"] != 1
        or index["verdict"] != "PENDING_CONTROLLER_RUNTIME_VERIFICATION"
        or not isinstance(index["entries"], list)
    ):
        raise EvidenceIncomplete("evidence index schema is invalid")
    for entry in index["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise EvidenceIncomplete("evidence entry schema is invalid")
        try:
            raw = _safe_read(root, entry["path"])
        except OSError as error:
            raise EvidenceIncomplete("evidence entry is unavailable") from error
        if len(raw) != entry["size_bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise EvidenceIncomplete("evidence entry digest does not match")
    try:
        process = json.loads(_safe_read(root, "process.json"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceIncomplete("process evidence is unavailable") from error
    if not isinstance(process, dict) or set(process) != _PROCESS_FIELDS:
        raise EvidenceIncomplete("process evidence fields are incomplete")
    if (
        process["shell"] is not False
        or process["stdin_closed"] is not True
        or process["cleanup_proven"] is not True
        or process["pid_alive"] is not False
        or process["listener_alive"] is not False
        or process["process_group_alive"] is not False
        or process["start_ticks"] < 0
    ):
        raise EvidenceIncomplete("process cleanup proof is incomplete")
    return True


__all__ = [
    "EvidenceBundle",
    "EvidenceIncomplete",
    "Package6Controller",
    "ProcessEvidence",
    "ReadinessEvidence",
    "RuntimeChildAuthorities",
    "SourceDrift",
    "StopEvidence",
    "TrackedProcessIdentity",
    "verify_evidence_bundle",
    "issue_runtime_child_authorities",
]
