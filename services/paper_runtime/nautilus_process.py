"""Exact Bubblewrap process transport for the interactive P1 paper child."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import weakref

from engines.nautilus.runtime_v1.control_channel import (
    MAX_FRAME_BYTES,
    frame_payload,
    paper_child_identity,
)
from packages.engine_contracts import (
    EngineCommandEnvelope,
    RunBacktest,
    canonical_json_bytes,
)
from services.job_worker.p1_engine_spawn import (
    P1EngineClosureAttestation,
    P1_PAPER_SOURCE_SHA256,
    P1PaperLaunchAuthority,
    claim_p1_paper_launch,
    is_issued_p1_paper_launch,
)


def _read(path: Path, maximum: int = 4096) -> bytes:
    with path.open("rb", buffering=0) as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        raise ValueError("paper child process authority is unavailable")
    return raw


def _children(pid: int) -> tuple[int, ...]:
    with Path(f"/proc/{pid}/task/{pid}/children").open("rb", buffering=0) as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("paper child process authority is unavailable")
    return tuple(int(value) for value in raw.split())


def _descendants(pid: int) -> tuple[int, ...]:
    pending = [pid]
    observed: list[int] = []
    while pending:
        current = pending.pop()
        if current in observed:
            raise ValueError("paper child process tree is cyclic")
        observed.append(current)
        pending.extend(_children(current))
    return tuple(observed)


def _process_facts(pid: int) -> tuple[int, int, tuple[int, int]]:
    stat_raw = _read(Path(f"/proc/{pid}/stat"))
    end = stat_raw.rfind(b")")
    fields = stat_raw[end + 2 :].split() if end > 0 else []
    status = _read(Path(f"/proc/{pid}/status"), 64 * 1024)
    namespace = [line for line in status.splitlines() if line.startswith(b"NSpid:")]
    executable = Path(f"/proc/{pid}/exe").stat()
    if len(fields) < 20 or len(namespace) != 1:
        raise ValueError("paper child process authority is unavailable")
    namespace_ids = namespace[0].split()[1:]
    if not namespace_ids:
        raise ValueError("paper child process authority is unavailable")
    return (
        int(namespace_ids[-1]),
        int(fields[19]),
        (executable.st_dev, executable.st_ino),
    )


def _process_cmdline_sha256(pid: int) -> str:
    raw = _read(Path(f"/proc/{pid}/cmdline"), 16 * 1024)
    values = tuple(item.decode("utf-8") for item in raw.rstrip(b"\0").split(b"\0"))
    return hashlib.sha256(canonical_json_bytes(values)).hexdigest()


def _process_executable_sha256(pid: int) -> str:
    digest = hashlib.sha256()
    with Path(f"/proc/{pid}/exe").open("rb", buffering=0) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    eq=False,
    repr=False,
    weakref_slot=True,
)
class NautilusPaperProcess:
    """One attested running process and its bounded framed transport."""

    child_identity_sha256: str
    closure_digest: str
    session_id: str
    owner_id: str
    host_pid: int
    namespace_pid: int
    process_start_ticks: int
    executable_identity: tuple[int, int]
    executable_sha256: str
    child_argv_sha256: str
    argv_sha256: str
    paper_source_sha256: str
    _process: subprocess.Popen[bytes]
    _closure: P1EngineClosureAttestation
    _request: EngineCommandEnvelope

    def matches_authority(
        self,
        closure: P1EngineClosureAttestation,
        request: EngineCommandEnvelope,
    ) -> bool:
        try:
            current = _process_facts(self.host_pid)
            child_argv_sha256 = _process_cmdline_sha256(self.host_pid)
        except (
            FileNotFoundError,
            ProcessLookupError,
            UnicodeDecodeError,
            ValueError,
        ):
            return False
        return (
            self in _ISSUED
            and self._process.poll() is None
            and self.closure_digest == closure.closure_sha256
            and self.session_id == str(request.engine_run_id)
            and self.owner_id == str(request.causation_id)
            and current[0] == self.namespace_pid
            and current[1] == self.process_start_ticks
            and current[2] == self.executable_identity
            and child_argv_sha256 == self.child_argv_sha256
            and type(self._process.args) is tuple
            and hashlib.sha256(canonical_json_bytes(self._process.args)).hexdigest()
            == self.argv_sha256
            and self.child_identity_sha256
            == paper_child_identity(
                closure_digest=self.closure_digest,
                owner_id=self.owner_id,
                paper_source_sha256=self.paper_source_sha256,
                process_id=self.namespace_pid,
                process_start_ticks=self.process_start_ticks,
                session_id=self.session_id,
            )
        )

    def exchange(self, raw: bytes) -> bytes:
        process = self._process
        if (
            not self.matches_authority(self._closure, self._request)
            or process.stdin is None
            or process.stdout is None
        ):
            raise RuntimeError("paper child is not running")
        process.stdin.write(frame_payload(raw))
        process.stdin.flush()
        response = bytearray()
        for _ in range(64):
            header = process.stdout.read(4)
            if len(header) != 4:
                raise RuntimeError("paper child response is truncated")
            size = int.from_bytes(header, "big")
            if not 0 < size <= MAX_FRAME_BYTES:
                raise RuntimeError("paper child response is oversized")
            payload = process.stdout.read(size)
            if len(payload) != size:
                raise RuntimeError("paper child response is truncated")
            response.extend(header)
            response.extend(payload)
            document = json.loads(payload)
            if type(document) is dict and document.get("frame_type") == "CHECKPOINT":
                return bytes(response)
        raise RuntimeError("paper child response exceeds maximum frames")

    def is_running(self) -> bool:
        return self.matches_authority(self._closure, self._request)

    def close_input(self) -> int:
        if self._process.stdin is not None and not self._process.stdin.closed:
            self._process.stdin.close()
        return self._process.wait(timeout=60)

    def abort(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait(timeout=10)


_ISSUED: weakref.WeakSet[NautilusPaperProcess] = weakref.WeakSet()


def _bind_process(
    process: subprocess.Popen[bytes],
    closure: P1EngineClosureAttestation,
    request: EngineCommandEnvelope,
    argv_sha256: str,
    paper_source_sha256: str,
    python_executable_sha256: str,
    child_argv: tuple[str, ...],
) -> NautilusPaperProcess:
    deadline = time.monotonic() + 5
    matches: list[tuple[int, tuple[int, int, tuple[int, int]]]] = []
    while not matches and process.poll() is None and time.monotonic() < deadline:
        for pid in _descendants(process.pid):
            try:
                facts = _process_facts(pid)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                continue
            try:
                executable_sha256 = _process_executable_sha256(pid)
                cmdline_sha256 = _process_cmdline_sha256(pid)
            except (FileNotFoundError, ProcessLookupError, UnicodeDecodeError, ValueError):
                continue
            if (
                executable_sha256 == python_executable_sha256
                and cmdline_sha256
                == hashlib.sha256(canonical_json_bytes(child_argv)).hexdigest()
            ):
                matches.append((pid, facts))
        if not matches:
            time.sleep(0.005)
    if len(matches) != 1:
        raise ValueError("exact P1 paper child process is unavailable")
    host_pid, (namespace_pid, start_ticks, executable) = matches[0]
    identity = paper_child_identity(
        closure_digest=closure.closure_sha256,
        owner_id=str(request.causation_id),
        paper_source_sha256=paper_source_sha256,
        process_id=namespace_pid,
        process_start_ticks=start_ticks,
        session_id=str(request.engine_run_id),
    )
    value = object.__new__(NautilusPaperProcess)
    for name, item in (
        ("child_identity_sha256", identity),
        ("closure_digest", closure.closure_sha256),
        ("session_id", str(request.engine_run_id)),
        ("owner_id", str(request.causation_id)),
        ("host_pid", host_pid),
        ("namespace_pid", namespace_pid),
        ("process_start_ticks", start_ticks),
        ("executable_identity", executable),
        ("executable_sha256", python_executable_sha256),
        (
            "child_argv_sha256",
            hashlib.sha256(canonical_json_bytes(child_argv)).hexdigest(),
        ),
        ("argv_sha256", argv_sha256),
        ("paper_source_sha256", paper_source_sha256),
        ("_process", process),
        ("_closure", closure),
        ("_request", request),
    ):
        object.__setattr__(value, name, item)
    _ISSUED.add(value)
    return value


def launch_nautilus_paper_process(
    authority: P1PaperLaunchAuthority,
    closure: P1EngineClosureAttestation,
    request: EngineCommandEnvelope,
) -> NautilusPaperProcess:
    """Launch and attest the sole child from one exact prepared authority."""

    if (
        not is_issued_p1_paper_launch(authority)
        or type(closure) is not P1EngineClosureAttestation
        or type(request) is not EngineCommandEnvelope
        or type(request.payload) is not RunBacktest
    ):
        raise TypeError("exact P1 paper launch authority is required")
    request_sha256 = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
    if (
        authority.closure_sha256 != closure.closure_sha256
        or authority.request_sha256 != request_sha256
        or authority.paper_source_sha256 != P1_PAPER_SOURCE_SHA256
        or authority.argv_sha256
        != hashlib.sha256(canonical_json_bytes(authority.built.argv)).hexdigest()
    ):
        raise ValueError("P1 paper launch authority changed")
    built = claim_p1_paper_launch(authority)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            built.argv,
            cwd=built.cwd,
            env=dict(built.environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=built.pass_fds,
        )
    finally:
        for descriptor in built.close_after_spawn_fds:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if (
        process is None
        or process.poll() is not None
        or process.stdin is None
        or process.stdout is None
        or process.stderr is None
    ):
        raise RuntimeError("P1 paper process did not start")
    try:
        return _bind_process(
            process,
            closure,
            request,
            authority.argv_sha256,
            authority.paper_source_sha256,
            authority.python_executable_sha256,
            tuple(authority.built.argv[-6:]),
        )
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        raise


def is_attested_nautilus_paper_process(value: object) -> bool:
    return type(value) is NautilusPaperProcess and value in _ISSUED


__all__ = [
    "NautilusPaperProcess",
    "launch_nautilus_paper_process",
    "is_attested_nautilus_paper_process",
]
