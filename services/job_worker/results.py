"""Bounded, race-resistant validators for attributable result artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Mapping

from control_api.normalization import parse_datetime
from packages.job_contracts import ReplayPayload

MAX_RESULT_CANDIDATES = 64
MAX_RESULT_ENTRIES = 10_000
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_REPLAY_EVENTS = 10_000
MAX_REPORT_ASSETS = 1_000

_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_ATTEMPT_ID = re.compile(r"attempt_[0-9a-f]{32}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SEMANTIC_INPUT_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_ASSET_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_SAFE_EVENT_TYPES = frozenset({
    "init", "thinking", "tool_result", "llm_call", "validation",
    "final_decision", "session_end", "unknown",
})
_SAFE_STATUSES = frozenset({
    "started", "success", "error", "passed", "failed", "completed",
})
_REPLAY_FIELDS = frozenset({
    "job_id", "attempt_id", "backend_commit", "session_id", "event_count", "events",
})
_REPLAY_EVENT_FIELDS = frozenset({"type", "timestamp", "status", "size_bytes"})


class ResultValidationError(RuntimeError):
    reason_code = "RESULT_VALIDATION_FAILED"

    def __init__(self, message: str, *, reconciliation_required: bool = False) -> None:
        super().__init__(message)
        self.reconciliation_required = reconciliation_required


@dataclass(frozen=True, slots=True)
class ValidatedResult:
    artifact_type: str
    relative_ref: str
    sha256: str
    size_bytes: int
    media_type: str
    truncated: bool
    validator_id: str
    validation_metadata: dict[str, object]


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _close_descriptors(*descriptors: int) -> None:
    first_error: OSError | None = None
    for descriptor in dict.fromkeys(fd for fd in descriptors if fd >= 0):
        try:
            os.close(descriptor)
        except OSError as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


def _cleanup_temp(directory_fd: int, filename: str, descriptor: int) -> None:
    first_error: OSError | None = None
    if descriptor >= 0:
        try:
            os.close(descriptor)
        except OSError as exc:
            first_error = exc
    try:
        os.unlink(filename, dir_fd=directory_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        first_error = first_error or exc
    if first_error is not None:
        raise first_error


def _open_directory_chain(path: Path, *, create: bool = False) -> int:
    """Open every path component relative to its verified parent dirfd."""
    absolute = path.absolute()
    descriptor = os.open(absolute.anchor, _directory_flags())
    try:
        for part in absolute.parts[1:]:
            if part in {"", ".", ".."}:
                raise OSError("unsafe directory component")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                os.fsync(descriptor)
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            try:
                info = os.fstat(child)
            except BaseException:
                os.close(child)
                raise
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise OSError("path component is not a directory")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class ResultValidator:
    """Validate fixed roots and seal accepted bytes into worker-owned storage."""

    def __init__(self, reports_root: Path, replay_root: Path, artifact_root: Path | None = None) -> None:
        self._reports_root = Path(reports_root)
        self._replay_root = Path(replay_root)
        self._artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else self._reports_root.parent / ".worker-artifacts"
        )
        self._results_root = self._artifact_root / "results"

    def validate(
        self, validator_id: str, job: object, *, exit_code: int,
        attempt_started_at: datetime, backend_commit: str,
        semantic_input_fingerprint: str | None = None,
        progress: Callable[[], None] | None = None,
    ) -> ValidatedResult:
        if exit_code != 0:
            raise ResultValidationError("child exit code was not zero")
        if attempt_started_at.tzinfo is None:
            raise ResultValidationError("attempt start is not timezone-aware")
        self._expected_lineage(job, backend_commit)
        started = attempt_started_at.astimezone(UTC)
        if validator_id == "legacy-report-v1":
            if (
                not isinstance(semantic_input_fingerprint, str)
                or _SEMANTIC_INPUT_FINGERPRINT.fullmatch(semantic_input_fingerprint) is None
            ):
                raise ResultValidationError(
                    "spawn-bound semantic input fingerprint is invalid"
                )
            raw, extra = self._validate_report(
                job, backend_commit, semantic_input_fingerprint, started,
                progress or (lambda: None),
            )
        elif validator_id == "legacy-replay-v1":
            raw, extra = self._validate_replay(
                job, backend_commit, started, progress or (lambda: None),
            )
        else:
            raise ResultValidationError("result validator is not allowlisted")
        return self._seal(job, raw, validator_id, extra)

    def _candidates(
        self, root: Path, prefix: str, progress: Callable[[], None],
    ) -> Iterator[tuple[str, bytes, Mapping[str, object], float]]:
        try:
            root_fd = _open_directory_chain(root)
        except OSError:
            return
        examined = 0
        try:
            with os.scandir(root_fd) as entries:
                for entry in entries:
                    examined += 1
                    progress()
                    if examined > MAX_RESULT_ENTRIES:
                        raise ResultValidationError("too many directory entries were examined")
                    name = entry.name
                    if not name.startswith(prefix) or not name.endswith(".json"):
                        continue
                    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                    try:
                        fd = os.open(name, flags, dir_fd=root_fd)
                    except OSError:
                        continue
                    try:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
                            continue
                        chunks: list[bytes] = []
                        remaining = MAX_RESULT_BYTES + 1
                        while remaining:
                            progress()
                            chunk = os.read(fd, min(64 * 1024, remaining))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            remaining -= len(chunk)
                        raw = b"".join(chunks)
                        if len(raw) > MAX_RESULT_BYTES:
                            continue
                        value = json.loads(raw)
                        if isinstance(value, Mapping):
                            yield name, raw, value, info.st_mtime
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        continue
                    finally:
                        os.close(fd)
        finally:
            os.close(root_fd)

    @staticmethod
    def _expected_lineage(job: object, backend_commit: str) -> None:
        job_id = getattr(job, "job_id", None)
        attempt_id = getattr(job, "attempt_id", None)
        if (
            not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None
            or not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None
            or not isinstance(backend_commit, str) or _COMMIT.fullmatch(backend_commit) is None
        ):
            raise ResultValidationError("expected result attribution is unsafe")

    @staticmethod
    def _attributed(
        value: Mapping[str, object], job: object, backend_commit: str,
    ) -> bool:
        return (
            value.get("job_id") == getattr(job, "job_id", None)
            and value.get("attempt_id") == getattr(job, "attempt_id", None)
            and value.get("backend_commit") == backend_commit
        )

    def _validate_report(
        self, job: object, backend_commit: str,
        semantic_input_fingerprint: str, started: datetime,
        progress: Callable[[], None],
    ) -> tuple[bytes, dict[str, object]]:
        valid: bytes | None = None
        count = 0
        for _, raw, value, modified_at in self._candidates(self._reports_root, "report_", progress):
            try:
                if not self._attributed(value, job, backend_commit) or value.get("research_only") is not True:
                    continue
                if datetime.fromtimestamp(modified_at, UTC) <= started:
                    continue
                count += 1
                if count > MAX_RESULT_CANDIDATES:
                    raise ResultValidationError("too many fresh attributable result candidates")
                if value.get("semantic_input_fingerprint") != semantic_input_fingerprint:
                    raise ResultValidationError(
                        "report semantic input fingerprint does not match spawn-bound authority"
                    )
                as_of = parse_datetime(value.get("as_of") or value.get("timestamp"))
                assets = value.get("assets")
                if as_of <= started or not self._valid_report_assets(assets):
                    continue
                if valid is not None:
                    raise ResultValidationError("multiple attributable result artifacts were produced", reconciliation_required=True)
                valid = raw
            except (ValueError, TypeError):
                continue
        if valid is None:
            raise ResultValidationError("one fresh attributable report is required")
        return valid, {
            "job_id": str(getattr(job, "job_id")),
            "attempt_id": str(getattr(job, "attempt_id")),
            "backend_commit": backend_commit,
            "research_only": True,
            "semantic_input_fingerprint": semantic_input_fingerprint,
        }

    @staticmethod
    def _valid_report_assets(assets: object) -> bool:
        if not isinstance(assets, list) or not assets or len(assets) > MAX_REPORT_ASSETS:
            return False
        for asset in assets:
            if not isinstance(asset, Mapping):
                return False
            symbol = asset.get("symbol")
            suggestion = asset.get("suggestion")
            if (
                not isinstance(symbol, str) or _ASSET_SYMBOL.fullmatch(symbol) is None
                or not isinstance(suggestion, str) or not suggestion or len(suggestion) > 64
            ):
                return False
            for price_key in ("current_price", "price"):
                price = asset.get(price_key)
                if price is not None and (
                    not isinstance(price, (int, float)) or isinstance(price, bool)
                ):
                    return False
        return True

    @staticmethod
    def _valid_replay_sidecar(
        value: Mapping[str, object], session_id: str,
    ) -> tuple[bool, int]:
        if set(value) != _REPLAY_FIELDS or value.get("session_id") != session_id:
            return False, 0
        events = value.get("events")
        event_count = value.get("event_count")
        if (
            not isinstance(events, list) or not events or len(events) > MAX_REPLAY_EVENTS
            or not isinstance(event_count, int) or isinstance(event_count, bool)
            or event_count != len(events)
        ):
            return False, 0
        for event in events:
            if not isinstance(event, Mapping) or not set(event) <= _REPLAY_EVENT_FIELDS:
                return False, 0
            if set(event) < {"type", "size_bytes"}:
                return False, 0
            if event.get("type") not in _SAFE_EVENT_TYPES:
                return False, 0
            size_bytes = event.get("size_bytes")
            if (
                not isinstance(size_bytes, int) or isinstance(size_bytes, bool)
                or not 0 <= size_bytes <= MAX_RESULT_BYTES
            ):
                return False, 0
            timestamp = event.get("timestamp")
            if timestamp is not None:
                try:
                    parsed = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else None
                except ValueError:
                    return False, 0
                if parsed is None or parsed.tzinfo is None or len(timestamp) > 64:
                    return False, 0
            status = event.get("status")
            if status is not None and status not in _SAFE_STATUSES:
                return False, 0
        return True, event_count

    def _validate_replay(
        self, job: object, backend_commit: str, started: datetime,
        progress: Callable[[], None],
    ) -> tuple[bytes, dict[str, object]]:
        payload = getattr(job, "payload", None)
        if not isinstance(payload, ReplayPayload):
            raise ResultValidationError("replay payload is invalid")
        valid: bytes | None = None
        count = 0
        for _, raw, value, modified_at in self._candidates(self._replay_root, "replay_", progress):
            if datetime.fromtimestamp(modified_at, UTC) <= started:
                continue
            if self._attributed(value, job, backend_commit) and value.get("session_id") == payload.session_id:
                count += 1
                if count > MAX_RESULT_CANDIDATES:
                    raise ResultValidationError("too many fresh attributable result candidates")
                schema_valid, event_count = self._valid_replay_sidecar(value, payload.session_id)
                if not schema_valid:
                    continue
                if valid is not None:
                    raise ResultValidationError("multiple replay artifacts were produced", reconciliation_required=True)
                valid = raw
        if valid is None:
            raise ResultValidationError("one fresh replay for the exact session is required")
        return valid, {
            "job_id": str(getattr(job, "job_id")),
            "attempt_id": str(getattr(job, "attempt_id")),
            "backend_commit": backend_commit,
            "session_id": payload.session_id,
            "event_count": event_count,
        }

    def _seal(self, job: object, raw: bytes, validator_id: str, extra: dict[str, object]) -> ValidatedResult:
        digest = hashlib.sha256(raw).hexdigest()
        components = (str(getattr(job, "job_id")), str(getattr(job, "attempt_id")))
        if any(not value or value in {".", ".."} or "/" in value for value in components):
            raise ResultValidationError("result attribution is unsafe")
        root_fd = -1
        try:
            root_fd = _open_directory_chain(self._results_root, create=True)
            root_info = os.fstat(root_fd)
            if root_info.st_uid != os.geteuid():
                raise OSError("sealed root owner is unsafe")
            os.fchmod(root_fd, 0o700)
            current = root_fd
            try:
                for part in components:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                    os.fsync(current)
                    child = os.open(part, _directory_flags(), dir_fd=current)
                    try:
                        child_info = os.fstat(child)
                        if child_info.st_uid != os.geteuid():
                            raise OSError("sealed directory owner is unsafe")
                        os.fchmod(child, 0o700)
                    except BaseException:
                        os.close(child)
                        raise
                    if current != root_fd:
                        os.close(current)
                    current = child
                filename = f"{digest}.json"
                tempname = f".{digest}.{secrets.token_hex(12)}.tmp"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                fd = -1
                try:
                    existing = os.open(
                        filename,
                        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current,
                    )
                except FileExistsError:
                    raise
                except FileNotFoundError:
                    existing = -1
                if existing >= 0:
                    try:
                        info = os.fstat(existing)
                        chunks: list[bytes] = []
                        remaining = MAX_RESULT_BYTES + 1
                        while remaining:
                            chunk = os.read(existing, min(64 * 1024, remaining))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            remaining -= len(chunk)
                        existing_raw = b"".join(chunks)
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_uid != os.geteuid()
                            or hashlib.sha256(existing_raw).hexdigest() != digest
                            or existing_raw != raw
                        ):
                            raise OSError("sealed artifact collision")
                        os.fchmod(existing, 0o600)
                    finally:
                        os.close(existing)
                else:
                    try:
                        fd = os.open(tempname, flags, 0o600, dir_fd=current)
                        os.fchmod(fd, 0o600)
                        view = memoryview(raw)
                        while view:
                            written = os.write(fd, view)
                            if written <= 0:
                                raise OSError("sealed artifact write made no progress")
                            view = view[written:]
                        os.fsync(fd)
                        os.close(fd)
                        fd = -1
                        os.rename(tempname, filename, src_dir_fd=current, dst_dir_fd=current)
                        os.fsync(current)
                    finally:
                        _cleanup_temp(current, tempname, fd)
            finally:
                descriptors = (current if current != root_fd else -1, root_fd)
                root_fd = -1
                _close_descriptors(*descriptors)
        except (OSError, ValueError) as exc:
            if root_fd >= 0:
                try:
                    os.close(root_fd)
                except OSError:
                    pass
            raise ResultValidationError("validated result could not be sealed") from exc
        relative = "/".join(("results", *components, f"{digest}.json"))
        return ValidatedResult("result", relative, digest, len(raw), "application/json", False, validator_id, {**extra, "validator_id": validator_id})


__all__ = [
    "MAX_REPLAY_EVENTS", "MAX_REPORT_ASSETS", "MAX_RESULT_BYTES", "MAX_RESULT_CANDIDATES",
    "MAX_RESULT_ENTRIES", "ResultValidationError", "ResultValidator",
    "ValidatedResult",
]
