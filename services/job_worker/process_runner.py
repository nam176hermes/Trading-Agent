"""Hardened subprocess lifecycle with one bounded nonblocking event loop."""

from __future__ import annotations

import hashlib
import io
import ctypes
import errno
import fcntl
import os
import re
import platform
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Callable, Protocol, Sequence, cast

from .artifacts import MAX_STREAM_BYTES, ArtifactMetadata, ArtifactWriter
from .command_registry import (
    PAPER_COMMAND_ARGV_PREFIX,
    CommandLineage,
    PreparedSpawn,
    consume_prepared_spawn,
)
from .environment import (
    APPROVED_SCRATCH_HOME,
    ResearchEnvironmentSettings,
    build_child_environment,
)
from .engine_spawn import (
    EngineBuiltSpawn,
    EngineSpawnLineage,
    PreparedEngineSpawn,
    consume_prepared_engine_spawn,
)
from .recovery import ProcProcessInspector, ProcessIdentity, ProcessInspector
from .safety_state import SafetyEvidence, validate_current_safety_evidence


_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_ATTEMPT_ID = re.compile(r"attempt_[0-9a-f]{32}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SOURCE_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_PIDFD_SYSCALLS = {
    "x86_64": (434, 424),
    "amd64": (434, 424),
    "aarch64": (434, 424),
    "arm64": (434, 424),
}


def _syscall_numbers() -> tuple[int, int]:
    numbers = _PIDFD_SYSCALLS.get(platform.machine().lower())
    if platform.system() != "Linux" or numbers is None:
        raise RuntimeError("pidfd syscalls are unsupported on this platform")
    return numbers


def _linux_pidfd_open(pid: int, flags: int = 0) -> int:
    open_number, _ = _syscall_numbers()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    result = int(libc.syscall(ctypes.c_long(open_number), ctypes.c_int(pid), ctypes.c_uint(flags)))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    try:
        descriptor_flags = fcntl.fcntl(result, fcntl.F_GETFD)
        if not descriptor_flags & fcntl.FD_CLOEXEC:
            fcntl.fcntl(result, fcntl.F_SETFD, descriptor_flags | fcntl.FD_CLOEXEC)
    except BaseException:
        os.close(result)
        raise
    return result


def _linux_pidfd_send_signal(pidfd: int, sig: int, info: object | None, flags: int) -> None:
    if info is not None:
        raise ValueError("pidfd siginfo must be None")
    _, send_number = _syscall_numbers()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    result = int(libc.syscall(
        ctypes.c_long(send_number), ctypes.c_int(pidfd), ctypes.c_int(sig),
        ctypes.c_void_p(), ctypes.c_uint(flags),
    ))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _default_pidfd_api() -> tuple[Callable[[int, int], int], Callable[[int, int, object | None, int], None]]:
    opener = getattr(os, "pidfd_open", None) or _linux_pidfd_open
    sender = getattr(signal, "pidfd_send_signal", None) or _linux_pidfd_send_signal
    descriptor = -1
    try:
        descriptor = opener(os.getpid(), 0)
        sender(descriptor, 0, None, 0)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise RuntimeError("Linux pidfd APIs are unavailable") from exc
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return opener, sender


class HeartbeatDecision(StrEnum):
    CONTINUE = "CONTINUE"
    CANCEL = "CANCEL"
    SAFETY_DRIFT = "SAFETY_DRIFT"
    STALE_LEASE = "STALE_LEASE"


_SAFETY_REASON_CODE = re.compile(r"SAFETY_[A-Z0-9_]{1,120}")


@dataclass(frozen=True, slots=True)
class HeartbeatInstruction:
    """A process-control decision carrying a sanitized safety reason."""

    decision: HeartbeatDecision
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.decision is HeartbeatDecision.SAFETY_DRIFT:
            if (
                not isinstance(self.reason_code, str)
                or _SAFETY_REASON_CODE.fullmatch(self.reason_code) is None
            ):
                raise ValueError("safety heartbeat reason is invalid")
        elif self.reason_code is not None:
            raise ValueError("non-safety heartbeat cannot carry a safety reason")

    @classmethod
    def safety_drift(cls, reason_code: object) -> HeartbeatInstruction:
        sanitized = (
            reason_code
            if isinstance(reason_code, str)
            and _SAFETY_REASON_CODE.fullmatch(reason_code) is not None
            else "SAFETY_STATE_INVALID"
        )
        return cls(HeartbeatDecision.SAFETY_DRIFT, sanitized)


@dataclass(frozen=True, slots=True)
class ProcessLineage:
    command: dict[str, str]
    safety_initial: dict[str, object]
    safety_final: dict[str, object]

    def as_metadata(self) -> dict[str, object]:
        return {
            "command": dict(self.command),
            "safety": {
                "initial": dict(self.safety_initial),
                "final": dict(self.safety_final),
            },
        }

    def with_final_safety(self, evidence: SafetyEvidence) -> "ProcessLineage":
        return ProcessLineage(
            dict(self.command),
            dict(self.safety_initial),
            _safety_metadata(evidence),
        )


def _safety_metadata(evidence: object) -> dict[str, object]:
    if not isinstance(evidence, SafetyEvidence):
        raise TypeError("worker safety preflight returned invalid evidence")
    return {
        "snapshot_sha256": evidence.snapshot_sha256,
        "generated_at": evidence.generated_at.isoformat(),
        "expires_at": evidence.expires_at.isoformat(),
        "requested_mode": evidence.requested_mode.value,
        "effective_mode": evidence.effective_mode.value,
        "live_execution_enabled": evidence.live_execution_enabled,
        "live_trading_approved": evidence.live_trading_approved,
        "kill_switch_state": evidence.kill_switch_state.value,
    }


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    exit_code: int | None
    termination_reason: str | None
    identity: ProcessIdentity
    stdout: ArtifactMetadata
    stderr: ArtifactMetadata
    capability_fingerprint: str
    result_validator_id: str
    backend_revision: str
    lineage: ProcessLineage
    safety_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class SessionMember:
    pid: int
    process_group: int
    session_id: int
    start_ticks: int


@dataclass(slots=True)
class _MemberHandle:
    member: SessionMember
    pidfd: int
    term_at: float | None = None
    killed: bool = False


@dataclass(slots=True)
class _SessionCleanupState:
    handles: dict[tuple[int, int], _MemberHandle]
    deadline: float | None = None
    empty_proofs: int = 0
    uncertain: bool = False
    complete: bool = False


@dataclass(slots=True)
class _StreamState:
    stream: BinaryIO
    retained: bytearray
    digest: "_Digest"
    observed: int = 0
    eof: bool = False


class _Digest(Protocol):
    def update(self, value: bytes, /) -> None: ...
    def hexdigest(self) -> str: ...


class _Process(Protocol):
    pid: int
    stdout: BinaryIO | None
    stderr: BinaryIO | None

    def send_signal(self, sig: int) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


class _SelectorKey(Protocol):
    fd: int
    data: str


class _Selector(Protocol):
    def register(self, fileobj: int, events: int, data: str) -> _SelectorKey: ...
    def unregister(self, fileobj: int) -> _SelectorKey: ...
    def select(self, timeout: float | None = None) -> Sequence[tuple[_SelectorKey, int]]: ...
    def close(self) -> None: ...


def _leader_exited_wnowait(pid: int) -> bool:
    """Observe leader exit while deliberately retaining its zombie/PID anchor."""

    flags = os.WEXITED | os.WNOHANG | getattr(os, "WNOWAIT", 0)
    if not getattr(os, "WNOWAIT", 0):
        raise RuntimeError("WNOWAIT is required for safe process-group ownership")
    return os.waitid(os.P_PID, pid, flags) is not None


def _read_proc_member(pid: int, *, allow_zombie: bool) -> SessionMember | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(f"/proc/{pid}/stat", flags)
        raw = os.read(descriptor, 4096)
    except FileNotFoundError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    close_paren = raw.rfind(b")")
    fields = raw[close_paren + 2 :].split() if close_paren >= 0 else []
    if len(fields) < 20:
        raise OSError("proc stat shape is unsafe")
    if fields[0] == b"Z" and not allow_zombie:
        return None
    return SessionMember(pid, int(fields[2]), int(fields[3]), int(fields[19]))


def _read_member_proc(pid: int) -> SessionMember | None:
    return _read_proc_member(pid, allow_zombie=False)


def _read_leader_proc(pid: int) -> SessionMember | None:
    """Read retained leader identity, including its WNOWAIT zombie anchor."""

    return _read_proc_member(pid, allow_zombie=True)


def _session_members_proc(identity: ProcessIdentity) -> tuple[SessionMember, ...]:
    """Snapshot all live members contained by the anchored process session."""

    members: list[SessionMember] = []
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            member = _read_member_proc(int(entry.name))
            if member is not None and member.session_id == identity.pid:
                members.append(member)
    return tuple(sorted(members, key=lambda member: (member.process_group, member.pid)))


class ProcessRunner:
    def __init__(
        self,
        artifacts: ArtifactWriter,
        *,
        popen: Callable[..., _Process] = cast(
            Callable[..., _Process], subprocess.Popen
        ),
        inspector: ProcessInspector | None = None,
        leader_exited: Callable[[int], bool] = _leader_exited_wnowait,
        session_members: Callable[[ProcessIdentity], tuple[SessionMember, ...]] = _session_members_proc,
        member_inspector: Callable[[int], SessionMember | None] = _read_member_proc,
        leader_inspector: Callable[[int], SessionMember | None] = _read_leader_proc,
        pidfd_open: Callable[[int, int], int] | None = None,
        pidfd_send_signal: Callable[[int, int, object | None, int], None] | None = None,
        selector_factory: Callable[[], _Selector] = cast(
            Callable[[], _Selector], selectors.DefaultSelector
        ),
        monotonic: Callable[[], float] = time.monotonic,
        safety_clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.2,
        terminate_grace_seconds: float = 5.0,
    ) -> None:
        self._artifacts = artifacts
        self._popen = popen
        self._inspector = inspector or ProcProcessInspector()
        if (pidfd_open is None) != (pidfd_send_signal is None):
            raise ValueError("pidfd open and send functions must be injected together")
        if pidfd_open is None:
            resolved_pidfd_open, resolved_pidfd_send = _default_pidfd_api()
        else:
            resolved_pidfd_open = pidfd_open
            resolved_pidfd_send = cast(
                Callable[[int, int, object | None, int], None],
                pidfd_send_signal,
            )
        self._leader_exited = leader_exited
        self._session_members = session_members
        self._member_inspector = member_inspector
        self._leader_inspector = leader_inspector
        self._pidfd_open = resolved_pidfd_open
        self._pidfd_send_signal = resolved_pidfd_send
        self._selector_factory = selector_factory
        self._monotonic = monotonic
        self._safety_clock = safety_clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._terminate_grace_seconds = terminate_grace_seconds

    def run(
        self,
        prepare_spawn: Callable[[], PreparedSpawn | PreparedEngineSpawn],
        environment: ResearchEnvironmentSettings,
        timeout_seconds: int | None,
        heartbeat: Callable[
            [ProcessIdentity], HeartbeatDecision | HeartbeatInstruction
        ],
        *,
        job_id: str,
        attempt_id: str,
        preflight: Callable[[], SafetyEvidence],
    ) -> ProcessOutcome:
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout must be a positive integer")
        if not callable(prepare_spawn):
            raise TypeError("spawn preparation must be a last-moment callable")
        if not callable(preflight):
            raise TypeError("current safety preflight is required")

        if _JOB_ID.fullmatch(job_id) is None or _ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise ValueError("worker attribution IDs are invalid")
        initial_safety = validate_current_safety_evidence(
            preflight(), self._safety_clock()
        )
        initial_safety_metadata = _safety_metadata(initial_safety)
        prepared = prepare_spawn()
        engine_authority = type(prepared) is PreparedEngineSpawn
        # Legacy root and credential policy validation remains in its original
        # position. Engine authority has no ambient/legacy environment path.
        child_environment = (
            None if engine_authority else build_child_environment(environment)
        )
        reserve_fd = os.open(
            "/dev/null", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        close_after_spawn_fds: tuple[int, ...] = ()
        try:
            if engine_authority:
                engine_built = consume_prepared_engine_spawn(
                    cast(PreparedEngineSpawn, prepared)
                )
                # The provider has transferred ownership. Claim every
                # descriptor before any validation branch can raise.
                close_after_spawn_fds = engine_built.close_after_spawn_fds
                if (
                    type(engine_built) is not EngineBuiltSpawn
                    or not isinstance(engine_built.argv, tuple)
                    or not engine_built.argv
                    or any(
                        not isinstance(argument, str) or not argument or "\x00" in argument
                        for argument in engine_built.argv
                    )
                    or engine_built.cwd != Path("/")
                    or dict(engine_built.environment) != {}
                    or not isinstance(engine_built.pass_fds, tuple)
                    or not engine_built.pass_fds
                    or len(set(engine_built.pass_fds)) != len(engine_built.pass_fds)
                    or any(
                        isinstance(descriptor, bool)
                        or not isinstance(descriptor, int)
                        or descriptor < 0
                        for descriptor in engine_built.pass_fds
                    )
                    or engine_built.close_after_spawn_fds != engine_built.pass_fds
                    or not isinstance(engine_built.lineage, EngineSpawnLineage)
                ):
                    raise ValueError("attested engine spawn shape is unsafe")
                if (
                    timeout_seconds is not None
                    and timeout_seconds != engine_built.timeout_seconds
                ):
                    raise ValueError("timeout must equal the attested command timeout")
                timeout_seconds = engine_built.timeout_seconds
                if _SOURCE_REVISION.fullmatch(engine_built.source_revision) is None:
                    raise ValueError("attested engine source revision is invalid")
                argv = engine_built.argv
                cwd = engine_built.cwd
                child_environment = engine_built.environment
                pass_fds = engine_built.pass_fds
                capability_fingerprint = engine_built.capability_fingerprint
                result_validator_id = engine_built.result_validator_id
                source_revision = engine_built.source_revision
                command_lineage = engine_built.lineage.as_metadata()
            else:
                built = consume_prepared_spawn(cast(PreparedSpawn, prepared))
                expected_argv = (str(built.executable), *PAPER_COMMAND_ARGV_PREFIX)
                if (
                    built.shell
                    or not isinstance(built.argv, tuple)
                    or built.argv != expected_argv
                    or not isinstance(built.lineage, CommandLineage)
                ):
                    raise ValueError("attested command shape is unsafe")
                if timeout_seconds is None or timeout_seconds != built.timeout_seconds:
                    raise ValueError("timeout must equal the attested command timeout")
                if _COMMIT.fullmatch(built.backend_revision) is None:
                    raise ValueError("attested backend revision is invalid")
                assert child_environment is not None
                fixture_path = child_environment.get(
                    "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH"
                )
                fixture_approval = child_environment.get(
                    "TRADING_PACKAGE6_APPROVAL_SHA256"
                )
                if fixture_path is not None or fixture_approval is not None:
                    from packages.runtime_release.paper_backend.provider_free_fixture import (
                        load_provider_free_fixture,
                    )

                    load_provider_free_fixture(
                        Path(fixture_path or ""),
                        expected_backend_commit=built.backend_revision,
                        expected_package6_approval_sha256=fixture_approval or "",
                    )
                child_environment.update({
                    "TRADING_JOB_ID": job_id,
                    "TRADING_JOB_ATTEMPT_ID": attempt_id,
                    "TRADING_RESEARCH_BACKEND_COMMIT": built.backend_revision,
                    "TRADING_RESEARCH_SCRATCHPAD_ROOT": str(
                        Path(
                            child_environment.get(
                                "HOME",
                                str(APPROVED_SCRATCH_HOME),
                            )
                        )
                        / "scratchpad"
                    ),
                })
                argv = built.argv
                cwd = built.cwd
                pass_fds = ()
                capability_fingerprint = built.capability_fingerprint
                result_validator_id = built.result_validator_id
                source_revision = built.backend_revision
                command_lineage = built.lineage.as_metadata()
            assert timeout_seconds is not None
            # Current safety is the final authority read. Immutable/semantic
            # attestation and all local command validation have already
            # completed, so no potentially slow operation can age this proof
            # before process creation.
            final_safety = validate_current_safety_evidence(
                preflight(), self._safety_clock()
            )
            final_safety_metadata = _safety_metadata(final_safety)
            popen_options: dict[str, object] = {
                "cwd": str(cwd),
                "env": child_environment,
                "shell": False,
                "start_new_session": True,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if pass_fds:
                popen_options["pass_fds"] = pass_fds
            try:
                process = self._popen(list(argv), **popen_options)
            finally:
                for descriptor in close_after_spawn_fds:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                close_after_spawn_fds = ()
        except BaseException:
            for descriptor in close_after_spawn_fds:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            os.close(reserve_fd)
            raise
        original_streams = (getattr(process, "stdout", None), getattr(process, "stderr", None))

        identity: ProcessIdentity | None = None
        selector: _Selector | None = None
        states: dict[str, _StreamState] = {}
        cleanup_errors: list[BaseException] = []
        session_cleanup = _SessionCleanupState({})
        reaped = False
        suppress_child_signal = False
        try:
            identity = self._inspector.inspect(process.pid)
            if identity is None or identity.pid != process.pid or identity.process_group != process.pid:
                reaped = self._cleanup_child_handle(process, cleanup_errors)
                suppress_child_signal = True
                identity = None
                raise RuntimeError("spawned process identity could not be proven")

            leader_pidfd = -1
            try:
                os.close(reserve_fd)
                reserve_fd = -1
                leader_pidfd = self._pidfd_open(identity.pid, 0)
                leader_member = self._leader_inspector(identity.pid)
                if (
                    leader_member is None
                    or leader_member.pid != identity.pid
                    or leader_member.process_group != identity.process_group
                    or leader_member.session_id != identity.pid
                    or leader_member.start_ticks != identity.start_ticks
                ):
                    raise OSError("leader identity changed during pidfd acquisition")
                session_cleanup.handles[(leader_member.pid, leader_member.start_ticks)] = (
                    _MemberHandle(leader_member, leader_pidfd)
                )
                leader_pidfd = -1
            except BaseException as exc:
                if leader_pidfd >= 0:
                    self._best_effort(
                        lambda fd=leader_pidfd: self._pidfd_send_signal(
                            fd, signal.SIGKILL, None, 0,
                        ),
                        cleanup_errors,
                    )
                    self._best_effort(lambda fd=leader_pidfd: os.close(fd), cleanup_errors)
                cleanup_errors.append(exc)
                reaped = self._wait_bounded(process, cleanup_errors) is not None
                suppress_child_signal = True
                identity = None
                raise RuntimeError(
                    "PROCESS_GROUP_CLEANUP_UNPROVEN: "
                    "leader pidfd identity could not be proven"
                ) from exc

            selector = self._selector_factory()
            active_selector = selector
            for kind, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                if stream is None:
                    raise RuntimeError("child pipe is missing")
                fd = stream.fileno()
                os.set_blocking(fd, False)
                states[kind] = _StreamState(stream, bytearray(), hashlib.sha256())
                active_selector.register(fd, selectors.EVENT_READ, kind)

            started = self._monotonic()
            leader_exited = False
            reason: str | None = None
            safety_reason_code: str | None = None
            cleanup_proven = True
            cleanup_active = False
            pipe_deadline: float | None = None

            while True:
                now = self._monotonic()
                try:
                    events = active_selector.select(self._poll_interval)
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    events = ()
                    cleanup_proven = False
                    reason = reason or "PROCESS_GROUP_CLEANUP_UNPROVEN"
                for key, _ in events:
                    state = states[key.data]
                    try:
                        chunk = os.read(key.fd, 64 * 1024)
                    except BlockingIOError:
                        continue
                    except BaseException as exc:
                        cleanup_errors.append(exc)
                        chunk = b""
                    if chunk:
                        state.digest.update(chunk)
                        state.observed += len(chunk)
                        if len(state.retained) < MAX_STREAM_BYTES:
                            state.retained.extend(chunk[: MAX_STREAM_BYTES - len(state.retained)])
                    else:
                        state.eof = True
                        self._best_effort(
                            lambda fd=key.fd: active_selector.unregister(fd),
                            cleanup_errors,
                        )
                        self._best_effort(state.stream.close, cleanup_errors)

                if not leader_exited:
                    try:
                        leader_exited = bool(self._leader_exited(identity.pid))
                    except BaseException as exc:
                        cleanup_errors.append(exc)
                        cleanup_proven = False
                        reason = reason or "PROCESS_GROUP_CLEANUP_UNPROVEN"
                        session_cleanup.uncertain = True

                try:
                    instruction = heartbeat(identity)
                    if isinstance(instruction, HeartbeatInstruction):
                        decision = instruction.decision
                    else:
                        decision = HeartbeatDecision(instruction)
                except BaseException:
                    raise
                if decision is not HeartbeatDecision.CONTINUE and reason is None:
                    reason = self._decision_reason(decision)
                    if (
                        decision is HeartbeatDecision.SAFETY_DRIFT
                        and isinstance(instruction, HeartbeatInstruction)
                    ):
                        safety_reason_code = instruction.reason_code
                if reason is None and now - started >= timeout_seconds:
                    reason = "TIMEOUT"

                pipes_done = all(state.eof for state in states.values())
                cleanup_active = cleanup_active or leader_exited or reason is not None
                if cleanup_active:
                    self._session_cleanup_step(identity, session_cleanup, now, cleanup_errors)
                    if session_cleanup.uncertain:
                        cleanup_proven = False
                        reason = reason or "PROCESS_GROUP_CLEANUP_UNPROVEN"

                if leader_exited and session_cleanup.complete and pipes_done:
                    break
                if pipes_done:
                    pipe_deadline = None
                if leader_exited and session_cleanup.complete and not pipes_done and pipe_deadline is None:
                    pipe_deadline = now + self._terminate_grace_seconds
                if pipe_deadline is not None and now >= pipe_deadline:
                    if not pipes_done:
                        reason = reason or "OUTPUT_DRAIN_TRUNCATED"
                    if not session_cleanup.complete:
                        cleanup_proven = False
                        reason = "PROCESS_GROUP_CLEANUP_UNPROVEN"
                    break
                if session_cleanup.deadline is not None and now >= session_cleanup.deadline:
                    self._force_kill_handles(session_cleanup, cleanup_errors)
                    if not session_cleanup.complete:
                        cleanup_proven = False
                        reason = "PROCESS_GROUP_CLEANUP_UNPROVEN"
                    elif not pipes_done:
                        reason = reason or "OUTPUT_DRAIN_TRUNCATED"
                    break

            if not cleanup_proven:
                reason = "PROCESS_GROUP_CLEANUP_UNPROVEN"
            self._close_member_handles(session_cleanup, cleanup_errors)
            self._close_selector_and_streams(selector, states, original_streams, cleanup_errors)
            selector = None
            exit_code = self._wait_bounded(process, cleanup_errors)
            reaped = exit_code is not None
            if cleanup_errors or not reaped:
                reason = "PROCESS_GROUP_CLEANUP_UNPROVEN"
            captured = {
                kind: self._persist_state(job_id, attempt_id, kind, state)
                for kind, state in states.items()
            }
            return ProcessOutcome(
                exit_code, reason, identity, captured["stdout"], captured["stderr"],
                capability_fingerprint, result_validator_id,
                source_revision,
                ProcessLineage(
                    command_lineage,
                    initial_safety_metadata,
                    final_safety_metadata,
                ),
                safety_reason_code,
            )
        except BaseException:
            if identity is not None:
                self._cleanup_session_bounded(identity, session_cleanup, cleanup_errors)
            elif not suppress_child_signal:
                self._best_effort(lambda: process.send_signal(signal.SIGKILL), cleanup_errors)
            raise
        finally:
            if reserve_fd >= 0:
                self._best_effort(lambda fd=reserve_fd: os.close(fd), cleanup_errors)
            if identity is not None and not session_cleanup.complete:
                self._cleanup_session_bounded(identity, session_cleanup, cleanup_errors)
            self._close_member_handles(session_cleanup, cleanup_errors)
            self._close_selector_and_streams(selector, states, original_streams, cleanup_errors)
            if not reaped:
                self._wait_bounded(process, cleanup_errors)

    @staticmethod
    def _decision_reason(decision: HeartbeatDecision) -> str:
        return {
            HeartbeatDecision.CANCEL: "CANCELLED",
            HeartbeatDecision.SAFETY_DRIFT: "SAFETY_DRIFT",
            HeartbeatDecision.STALE_LEASE: "STALE_LEASE",
        }[decision]

    def _persist_state(self, job_id: str, attempt_id: str, kind: str, state: _StreamState) -> ArtifactMetadata:
        stored = self._artifacts.capture_stream(job_id, attempt_id, kind, io.BytesIO(bytes(state.retained)))
        return ArtifactMetadata(
            stored.artifact_type, stored.relative_ref, state.digest.hexdigest(),
            state.observed, stored.media_type, state.observed > len(state.retained), stored.validator_id,
        )

    def _session_cleanup_step(
        self, identity: ProcessIdentity, state: _SessionCleanupState,
        now: float, errors: list[BaseException],
    ) -> None:
        if state.deadline is None:
            state.deadline = now + 4 * self._terminate_grace_seconds
        try:
            members = tuple(self._session_members(identity))
            if any(
                not isinstance(member, SessionMember)
                or member.pid <= 0 or member.process_group <= 0
                or member.session_id != identity.pid or member.start_ticks <= 0
                for member in members
            ):
                raise OSError("session membership snapshot is unsafe")
            scan_proven = True
        except BaseException as exc:
            errors.append(exc)
            state.uncertain = True
            state.empty_proofs = 0
            members = ()
            scan_proven = False

        current = {(member.pid, member.start_ticks): member for member in members}
        for key in tuple(state.handles):
            if key not in current:
                if not scan_proven:
                    continue
                try:
                    observed = self._member_inspector(key[0])
                    if (
                        observed is not None and observed.start_ticks == key[1]
                        and observed.session_id == identity.pid
                    ):
                        state.uncertain = True
                        errors.append(OSError("live session member was omitted from snapshot"))
                        current[key] = observed
                        continue
                    if (
                        observed is not None and observed.start_ticks == key[1]
                        and observed.session_id != identity.pid
                    ):
                        state.uncertain = True
                        errors.append(OSError("session member escaped the anchored session"))
                        self._pidfd_send_signal(
                            state.handles[key].pidfd, signal.SIGKILL, None, 0,
                        )
                except BaseException as exc:
                    errors.append(exc)
                    state.uncertain = True
                self._close_member_handle(state, key, errors)
        state.empty_proofs = state.empty_proofs + 1 if scan_proven and not current else 0
        state.complete = state.empty_proofs >= 2

        for key, member in current.items():
            if key in state.handles:
                continue
            pidfd = -1
            try:
                pidfd = self._pidfd_open(member.pid, 0)
                verified = self._member_inspector(member.pid)
                if verified != member:
                    raise OSError("session member identity changed during pidfd acquisition")
                state.handles[key] = _MemberHandle(member, pidfd, now)
                pidfd = -1
                self._pidfd_send_signal(state.handles[key].pidfd, signal.SIGTERM, None, 0)
            except BaseException as exc:
                errors.append(exc)
                state.uncertain = True
            finally:
                if pidfd >= 0:
                    try:
                        os.close(pidfd)
                    except OSError as exc:
                        errors.append(exc)
                        state.uncertain = True

        for key, handle in tuple(state.handles.items()):
            if handle.term_at is None:
                try:
                    self._pidfd_send_signal(handle.pidfd, signal.SIGTERM, None, 0)
                except BaseException as exc:
                    errors.append(exc)
                    state.uncertain = True
                handle.term_at = now
            if (scan_proven and key not in current) or handle.killed:
                continue
            if now - handle.term_at >= self._terminate_grace_seconds:
                try:
                    self._pidfd_send_signal(handle.pidfd, signal.SIGKILL, None, 0)
                    handle.killed = True
                except BaseException as exc:
                    errors.append(exc)
                    state.uncertain = True

    def _cleanup_session_bounded(
        self, identity: ProcessIdentity, state: _SessionCleanupState,
        errors: list[BaseException],
    ) -> None:
        while not state.complete:
            now = self._monotonic()
            self._session_cleanup_step(identity, state, now, errors)
            if state.complete:
                break
            if state.deadline is not None and now >= state.deadline:
                self._force_kill_handles(state, errors)
                state.uncertain = True
                break
            self._sleep(self._poll_interval)

    def _close_member_handle(
        self, state: _SessionCleanupState, key: tuple[int, int],
        errors: list[BaseException],
    ) -> None:
        handle = state.handles.pop(key, None)
        if handle is None:
            return
        try:
            os.close(handle.pidfd)
        except OSError as exc:
            errors.append(exc)
            state.uncertain = True

    def _force_kill_handles(
        self, state: _SessionCleanupState, errors: list[BaseException],
    ) -> None:
        for handle in state.handles.values():
            if handle.killed:
                continue
            try:
                self._pidfd_send_signal(handle.pidfd, signal.SIGKILL, None, 0)
                handle.killed = True
            except BaseException as exc:
                errors.append(exc)
                state.uncertain = True

    def _close_member_handles(
        self, state: _SessionCleanupState, errors: list[BaseException],
    ) -> None:
        for key in tuple(state.handles):
            self._close_member_handle(state, key, errors)

    def _wait_bounded(
        self, process: _Process, errors: list[BaseException]
    ) -> int | None:
        try:
            return process.wait(timeout=max(self._terminate_grace_seconds, self._poll_interval))
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            errors.append(exc)
            return None
        except BaseException as exc:
            errors.append(exc)
            return None

    def _cleanup_child_handle(
        self, process: _Process, errors: list[BaseException]
    ) -> bool:
        self._best_effort(lambda: process.send_signal(signal.SIGKILL), errors)
        return self._wait_bounded(process, errors) is not None

    @staticmethod
    def _best_effort(operation: Callable[[], object], errors: list[BaseException]) -> None:
        try:
            operation()
        except BaseException as exc:
            errors.append(exc)

    def _close_selector_and_streams(
        self,
        selector: _Selector | None,
        states: dict[str, _StreamState],
        original_streams: tuple[BinaryIO | None, ...],
        errors: list[BaseException],
    ) -> None:
        for state in states.values():
            if not state.eof:
                self._best_effort(state.stream.close, errors)
                state.eof = True
        for stream in original_streams:
            if stream is not None and not getattr(stream, "closed", False):
                self._best_effort(stream.close, errors)
        if selector is not None:
            self._best_effort(selector.close, errors)


__all__ = [
    "HeartbeatDecision", "HeartbeatInstruction", "ProcessOutcome",
    "ProcessRunner", "SessionMember",
]
