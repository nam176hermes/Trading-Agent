"""Validated worker attribution and research-only result output."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence


APPROVED_RESEARCH_OUTPUT_ROOT = Path(
    "/home/thenam176/.local/share/trading-agent/research-output"
)
APPROVED_WORKER_SCRATCHPAD_ROOT = Path(
    "/home/thenam176/.local/run/trading-agent/research-home/scratchpad"
)

MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_REPLAY_EVENTS = 10_000

_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_ATTEMPT_ID = re.compile(r"attempt_[0-9a-f]{32}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}")
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SAFE_EVENT_TYPES = frozenset({
    "init", "thinking", "tool_result", "llm_call", "validation",
    "final_decision", "session_end",
})
_SAFE_STATUSES = frozenset({
    "started", "success", "error", "passed", "failed", "completed",
})

_WORKER_ENV_KEYS = frozenset({
    "TRADING_JOB_ID",
    "TRADING_JOB_ATTEMPT_ID",
    "TRADING_ATTEMPT_ID",
    "TRADING_REPORTS_DIR",
    "TRADING_SIGNAL_OUTPUT_DIR",
    "TRADING_RESEARCH_BACKEND_COMMIT",
    "TRADING_RESEARCH_SCRATCHPAD_ROOT",
})
_STRICT_WORKER_INVOCATION: ResearchInvocation | None = None


class ResearchInvocationError(RuntimeError):
    """Raised before research work when worker attribution is unsafe."""


@dataclass(frozen=True, slots=True)
class ResearchInvocation:
    job_id: str | None
    attempt_id: str | None
    research_only: bool
    backend_commit: str
    reports_dir: Path | None
    signal_output_dir: Path | None
    replay_scratchpad_root: Path | None


def bootstrap_strict_worker_invocation(
    source: Mapping[str, str] | None = None,
) -> ResearchInvocation | None:
    """Validate exact worker attribution before importing modules with side effects."""
    global _STRICT_WORKER_INVOCATION
    values = os.environ if source is None else source
    if not any(key in values for key in _WORKER_ENV_KEYS):
        return None
    if "TRADING_JOB_ID" not in values or "TRADING_JOB_ATTEMPT_ID" not in values:
        raise ResearchInvocationError("strict worker attribution requires job ID and attempt ID")
    invocation = resolve_research_invocation(True, values)
    if invocation.job_id is None:
        raise ResearchInvocationError("strict worker attribution requires a job ID")
    _STRICT_WORKER_INVOCATION = invocation
    return invocation


def strict_worker_invocation() -> ResearchInvocation | None:
    """Return the already validated import-time worker context, if present."""
    return _STRICT_WORKER_INVOCATION


def _read_backend_commit() -> str | None:
    """Read the current checkout commit without spawning a process."""
    marker = Path(__file__).resolve().parent / ".git"
    try:
        if marker.is_file():
            marker_value = marker.read_text(encoding="utf-8").strip()
            if not marker_value.startswith("gitdir: "):
                return None
            git_dir = Path(marker_value.removeprefix("gitdir: "))
            if not git_dir.is_absolute():
                git_dir = marker.parent / git_dir
        elif marker.is_dir():
            git_dir = marker
        else:
            return None

        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if _COMMIT.fullmatch(head):
            return head
        if not head.startswith("ref: "):
            return None
        ref = head.removeprefix("ref: ")
        if ref.startswith("/") or ".." in Path(ref).parts:
            return None
        candidates = [git_dir / ref]
        common_marker = git_dir / "commondir"
        if common_marker.is_file():
            common_dir = git_dir / common_marker.read_text(encoding="utf-8").strip()
            candidates.append(common_dir / ref)
        for candidate in candidates:
            try:
                value = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if _COMMIT.fullmatch(value):
                return value
    except (OSError, UnicodeError):
        return None
    return None


def _directory_flags() -> int:
    return (
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory_chain(path: Path, label: str, *, private_leaf: bool) -> int:
    """Open and validate an absolute directory through parent-anchored dirfds."""
    if not path.is_absolute() or ".." in path.parts:
        raise ResearchInvocationError(f"{label} must be an absolute path without traversal")
    descriptor = -1
    try:
        descriptor = os.open(path.anchor, _directory_flags())
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            if part in {"", ".", ".."}:
                raise ResearchInvocationError(f"{label} contains an unsafe component")
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode):
                    raise ResearchInvocationError(f"{label} contains a non-directory component")
                is_leaf = index == len(parts) - 1
                if is_leaf:
                    if info.st_uid != os.geteuid():
                        raise ResearchInvocationError(f"{label} has an unsafe owner")
                    if private_leaf:
                        if stat.S_IMODE(info.st_mode) != 0o700:
                            raise ResearchInvocationError(f"{label} must be mode 0700")
                    elif stat.S_IMODE(info.st_mode) & 0o022:
                        raise ResearchInvocationError(f"{label} is writable by another principal")
                elif info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
                    raise ResearchInvocationError(f"{label} has an unsafe ancestor")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(exc, ResearchInvocationError):
            raise
        raise ResearchInvocationError(f"{label} cannot be safely opened") from exc


def _open_output_directory(path: Path, label: str) -> int:
    approved = APPROVED_RESEARCH_OUTPUT_ROOT
    if path not in {approved / "reports", approved / "signals"}:
        raise ResearchInvocationError(f"{label} is not an exact approved directory")
    root_fd = _open_directory_chain(approved, "approved research output root", private_leaf=True)
    try:
        leaf_fd = os.open(path.name, _directory_flags(), dir_fd=root_fd)
        try:
            info = os.fstat(leaf_fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ResearchInvocationError(f"{label} must be owner-controlled mode 0700")
        except BaseException:
            os.close(leaf_fd)
            raise
        return leaf_fd
    except OSError as exc:
        raise ResearchInvocationError(f"{label} cannot be safely opened") from exc
    finally:
        os.close(root_fd)


def _validate_output_directory(path: Path, label: str) -> Path:
    descriptor = _open_output_directory(path, label)
    os.close(descriptor)
    return path


def resolve_research_invocation(
    research_only: bool,
    source: Mapping[str, str] | None = None,
) -> ResearchInvocation:
    """Resolve immutable lineage before any collector or result write."""
    values = os.environ if source is None else source
    job_id = values.get("TRADING_JOB_ID")
    attempt_id = values.get("TRADING_JOB_ATTEMPT_ID")
    legacy_attempt = values.get("TRADING_ATTEMPT_ID")
    reports_value = values.get("TRADING_REPORTS_DIR")
    signals_value = values.get("TRADING_SIGNAL_OUTPUT_DIR")
    worker_commit = values.get("TRADING_RESEARCH_BACKEND_COMMIT")
    scratchpad_value = values.get("TRADING_RESEARCH_SCRATCHPAD_ROOT")

    if legacy_attempt is not None:
        raise ResearchInvocationError("legacy worker attempt ID environment name is forbidden")
    if (job_id is None) != (attempt_id is None):
        raise ResearchInvocationError("worker job ID and attempt ID must be provided together")
    if job_id is not None and not _JOB_ID.fullmatch(job_id):
        raise ResearchInvocationError("worker job ID is invalid")
    if attempt_id is not None and not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ResearchInvocationError("worker attempt ID is invalid")
    if job_id is not None and not research_only:
        raise ResearchInvocationError("worker attribution requires research-only mode")
    if job_id is None and (worker_commit is not None or scratchpad_value is not None):
        raise ResearchInvocationError("worker-only research attribution environment is forbidden")
    if job_id is not None and (worker_commit is None or not _COMMIT.fullmatch(worker_commit)):
        raise ResearchInvocationError("worker backend commit is missing or invalid")
    if scratchpad_value is not None:
        if Path(scratchpad_value) != APPROVED_WORKER_SCRATCHPAD_ROOT:
            raise ResearchInvocationError("worker replay scratchpad root is not the exact approved path")

    if not research_only:
        return ResearchInvocation(
            None, None, False, _read_backend_commit() or "unknown", None, None, None,
        )

    if (reports_value is None) != (signals_value is None):
        raise ResearchInvocationError("research output directories must be provided together")
    if job_id is not None and reports_value is None:
        raise ResearchInvocationError("worker invocation requires dedicated output directories")

    reports_dir = None
    signal_output_dir = None
    if reports_value is not None and signals_value is not None:
        if Path(reports_value) != APPROVED_RESEARCH_OUTPUT_ROOT / "reports":
            raise ResearchInvocationError("reports directory is not the exact approved directory")
        if Path(signals_value) != APPROVED_RESEARCH_OUTPUT_ROOT / "signals":
            raise ResearchInvocationError("signal output directory is not the exact approved directory")
        reports_dir = _validate_output_directory(Path(reports_value), "reports directory")
        signal_output_dir = _validate_output_directory(Path(signals_value), "signal output directory")
        if reports_dir == signal_output_dir:
            raise ResearchInvocationError("research output directories must be distinct")

    backend_commit = worker_commit if job_id is not None else _read_backend_commit()
    return ResearchInvocation(
        job_id, attempt_id, True, backend_commit or "unknown", reports_dir,
        signal_output_dir,
        APPROVED_WORKER_SCRATCHPAD_ROOT if scratchpad_value is not None else None,
    )


def with_lineage(document: Mapping[str, object], invocation: ResearchInvocation) -> dict:
    """Copy a result and replace any untrusted lineage with worker-owned values."""
    attributed = dict(document)
    attributed.update({
        "job_id": invocation.job_id,
        "attempt_id": invocation.attempt_id,
        "research_only": invocation.research_only,
        "backend_commit": invocation.backend_commit,
    })
    return attributed


def _safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


def _safe_event_metadata(event: Mapping[str, object], size_bytes: int) -> dict[str, object]:
    event_type = event.get("type")
    metadata: dict[str, object] = {
        "type": event_type if event_type in _SAFE_EVENT_TYPES else "unknown",
        "size_bytes": size_bytes,
    }
    if (timestamp := _safe_timestamp(event.get("timestamp"))) is not None:
        metadata["timestamp"] = timestamp
    status = event.get("status")
    if isinstance(status, str) and status in _SAFE_STATUSES:
        metadata["status"] = status
    elif isinstance(event.get("success"), bool):
        metadata["status"] = "success" if event["success"] else "error"
    elif isinstance(event.get("passed"), bool):
        metadata["status"] = "passed" if event["passed"] else "failed"
    return metadata


def load_worker_replay(
    invocation: ResearchInvocation, session_id: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Read one exact worker replay file once through an anchored descriptor."""
    if invocation.job_id is None or invocation.replay_scratchpad_root is None:
        raise ResearchInvocationError("worker replay requires an attested scratchpad root")
    if not _SAFE_SESSION_ID.fullmatch(session_id):
        raise ResearchInvocationError("worker replay session ID is invalid")
    if invocation.replay_scratchpad_root != APPROVED_WORKER_SCRATCHPAD_ROOT:
        raise ResearchInvocationError("worker replay scratchpad root is not approved")

    root_fd = _open_directory_chain(
        invocation.replay_scratchpad_root,
        "worker replay scratchpad root",
        private_leaf=True,
    )
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                f"{session_id}.jsonl",
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
        except OSError as exc:
            raise ResearchInvocationError("worker replay file cannot be safely opened") from exc
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ResearchInvocationError("worker replay file is not regular")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ResearchInvocationError("worker replay file owner or mode is unsafe")
        if info.st_nlink != 1:
            raise ResearchInvocationError("worker replay file has unsafe link count")
        if info.st_size > MAX_RESULT_BYTES:
            raise ResearchInvocationError("worker replay exceeds the 4 MiB limit")
        raw = os.read(descriptor, MAX_RESULT_BYTES)
        if len(raw) != info.st_size:
            raise ResearchInvocationError("worker replay changed during its bounded read")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)

    events: list[dict[str, object]] = []
    sanitized: list[dict[str, object]] = []
    try:
        lines = raw.splitlines()
        if len(lines) > MAX_REPLAY_EVENTS:
            raise ResearchInvocationError("worker replay event count exceeds the limit")
        for line in lines:
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ResearchInvocationError("worker replay event is not an object")
            events.append(event)
            sanitized.append(_safe_event_metadata(event, len(line)))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchInvocationError("worker replay is not valid JSONL") from exc
    if not events:
        raise ResearchInvocationError("worker replay contains no events")
    return events, sanitized


def build_replay_sidecar(
    invocation: ResearchInvocation,
    session_id: str,
    events: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build the allowlisted replay artifact schema without raw event content."""
    if invocation.job_id is None or invocation.attempt_id is None:
        raise ResearchInvocationError("replay sidecar requires worker lineage")
    if len(events) > MAX_REPLAY_EVENTS:
        raise ResearchInvocationError("worker replay event count exceeds the limit")
    safe_events: list[dict[str, object]] = []
    for event in events:
        if set(event) - {"type", "timestamp", "status", "size_bytes"}:
            raise ResearchInvocationError("replay sidecar event contains raw content")
        event_type = event.get("type")
        size_bytes = event.get("size_bytes")
        if event_type not in _SAFE_EVENT_TYPES | {"unknown"}:
            raise ResearchInvocationError("replay sidecar event type is unsafe")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or not 0 <= size_bytes <= MAX_RESULT_BYTES:
            raise ResearchInvocationError("replay sidecar event size is unsafe")
        timestamp = event.get("timestamp")
        if timestamp is not None and _safe_timestamp(timestamp) is None:
            raise ResearchInvocationError("replay sidecar event timestamp is unsafe")
        status = event.get("status")
        if status is not None and status not in _SAFE_STATUSES:
            raise ResearchInvocationError("replay sidecar event status is unsafe")
        safe_events.append(dict(event))
    return {
        "job_id": invocation.job_id,
        "attempt_id": invocation.attempt_id,
        "backend_commit": invocation.backend_commit,
        "session_id": session_id,
        "event_count": len(events),
        "events": safe_events,
    }


def write_json_exclusive(directory: Path, filename: str, document: Mapping[str, object]) -> Path:
    """Durably publish bounded JSON through an exclusive hidden temp file."""
    directory = Path(directory)
    if not _SAFE_FILENAME.fullmatch(filename) or not filename.endswith(".json"):
        raise ResearchInvocationError("result filename is unsafe")
    raw = json.dumps(document, indent=2, default=str).encode("utf-8")
    if len(raw) > MAX_RESULT_BYTES:
        raise ResearchInvocationError("serialized result exceeds the 4 MiB worker limit")
    directory_fd = _open_output_directory(directory, "result directory")
    descriptor = -1
    tempname = f".{filename}.{secrets.token_hex(16)}.tmp"
    temp_created = False
    directory_changed = False
    try:
        descriptor = os.open(
            tempname,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        temp_created = True
        directory_changed = True
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ResearchInvocationError("temporary result file is unsafe")
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("result write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(
            tempname, filename,
            src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.unlink(tempname, dir_fd=directory_fd)
        temp_created = False
        os.fsync(directory_fd)
        directory_changed = False
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if temp_created:
            try:
                os.unlink(tempname, dir_fd=directory_fd)
                directory_changed = True
            except FileNotFoundError:
                pass
        if directory_changed:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(directory_fd)
    return directory / filename


__all__ = [
    "APPROVED_RESEARCH_OUTPUT_ROOT", "APPROVED_WORKER_SCRATCHPAD_ROOT",
    "MAX_REPLAY_EVENTS", "MAX_RESULT_BYTES", "ResearchInvocation",
    "ResearchInvocationError", "build_replay_sidecar", "load_worker_replay",
    "resolve_research_invocation", "with_lineage", "write_json_exclusive",
]
