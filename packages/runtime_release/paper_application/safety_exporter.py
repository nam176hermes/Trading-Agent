"""Export only canonical safety evidence into a short-lived worker snapshot."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from packages.safety_evidence import (
    CANONICAL_SAFETY_SOURCE_ROOT,
    CanonicalKillSwitchState,
    safety_source_fingerprint,
)

CANONICAL_SOURCE_ROOT = CANONICAL_SAFETY_SOURCE_ROOT
_RUNTIME_ROOT = Path(f"/run/user/{os.geteuid()}/trading-agent")
MOUNTED_SOURCE_ROOT = _RUNTIME_ROOT / "safety-sources"
DEFAULT_SNAPSHOT_PATH = Path(
    _RUNTIME_ROOT / "safety-state.json"
)
EXPORT_INTERVAL_SECONDS = 2
SNAPSHOT_TTL_SECONDS = 6
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_MAX_SOURCE_BYTES = 4096


class _SafetyMode(StrEnum):
    PAPER = "PAPER"
    UNKNOWN = "UNKNOWN"


source_fingerprint = safety_source_fingerprint


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("safety-state clock must be timezone-aware")
    normalized = value.astimezone(UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _gate(source: Mapping[str, str], key: str) -> bool | None:
    raw = source.get(key)
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _read_private_source(root_fd: int, name: str) -> bytes | None:
    descriptor = -1
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        return None
    except OSError:
        return b""
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            return b""
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(1024, _MAX_SOURCE_BYTES + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > _MAX_SOURCE_BYTES:
                return b""
        return b"".join(chunks)
    except OSError:
        return b""
    finally:
        os.close(descriptor)


def _mode(raw: bytes | None) -> _SafetyMode:
    if raw is None or raw == b"":
        return _SafetyMode.UNKNOWN
    try:
        return _SafetyMode(raw.decode("utf-8").strip().upper())
    except (UnicodeError, ValueError):
        return _SafetyMode.UNKNOWN


def _kill_switch(raw: bytes | None) -> CanonicalKillSwitchState:
    if raw is None:
        return CanonicalKillSwitchState.INACTIVE
    if raw == b"":
        return CanonicalKillSwitchState.UNKNOWN
    try:
        lines = raw.decode("utf-8").strip().splitlines()
        if len(lines) != 1:
            return CanonicalKillSwitchState.UNKNOWN
        activated_at, separator, reason = lines[0].partition(": ")
        if not separator or not reason.strip():
            return CanonicalKillSwitchState.UNKNOWN
        datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
        return CanonicalKillSwitchState.ACTIVE
    except (UnicodeError, ValueError):
        return CanonicalKillSwitchState.UNKNOWN


def _effective_mode(
    requested: _SafetyMode,
    execution_enabled: bool | None,
    trading_approved: bool | None,
    kill_switch: CanonicalKillSwitchState,
) -> _SafetyMode:
    if (
        requested is _SafetyMode.PAPER
        and execution_enabled is False
        and trading_approved is False
        and kill_switch is CanonicalKillSwitchState.INACTIVE
    ):
        return _SafetyMode.PAPER
    return _SafetyMode.UNKNOWN


class SafetyStateExporter:
    """Read two named files and two named gates, then atomically publish JSON."""

    def __init__(
        self,
        *,
        canonical_source_root: Path,
        mounted_source_root: Path,
        output_path: Path,
        exporter_commit: str,
        gate_source: Mapping[str, str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(exporter_commit, str) or _COMMIT.fullmatch(exporter_commit) is None:
            raise ValueError("safety exporter commit is invalid")
        self.canonical_source_root = Path(canonical_source_root)
        self.mounted_source_root = Path(mounted_source_root)
        self.output_path = Path(output_path)
        self.exporter_commit = exporter_commit
        self.gate_source = gate_source
        self.clock = clock or (lambda: datetime.now(UTC))

    def snapshot(self) -> dict[str, object]:
        root_fd = os.open(
            self.mounted_source_root,
            _READ_FLAGS | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            root_info = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.geteuid()
                or stat.S_IMODE(root_info.st_mode) != 0o700
            ):
                raise OSError("mounted safety source root is unsafe")
            requested = _mode(_read_private_source(root_fd, ".mode"))
            kill_switch = _kill_switch(
                _read_private_source(root_fd, ".kill_switch")
            )
        finally:
            os.close(root_fd)
        execution_enabled = _gate(self.gate_source, "LIVE_EXECUTION_ENABLED")
        trading_approved = _gate(self.gate_source, "LIVE_TRADING_APPROVED")
        generated = self.clock()
        expires = generated + timedelta(seconds=SNAPSHOT_TTL_SECONDS)
        return {
            "schema_version": 1,
            "exporter_commit": self.exporter_commit,
            "generated_at": _timestamp(generated),
            "expires_at": _timestamp(expires),
            "requested_mode": requested.value,
            "effective_mode": _effective_mode(
                requested, execution_enabled, trading_approved, kill_switch,
            ).value,
            "live_execution_enabled": execution_enabled,
            "live_trading_approved": trading_approved,
            "kill_switch_state": kill_switch.value,
            "source_fingerprint": source_fingerprint(
                self.canonical_source_root
            ),
        }

    def export_once(self) -> None:
        payload = json.dumps(
            self.snapshot(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii") + b"\n"
        temporary_name = f".{self.output_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        descriptor = -1
        parent_fd = -1
        try:
            parent_fd = os.open(
                self.output_path.parent,
                _READ_FLAGS | getattr(os, "O_DIRECTORY", 0),
            )
            parent_info = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != os.geteuid()
                or parent_info.st_mode & 0o077
            ):
                raise OSError("safety snapshot directory is unsafe")
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("safety snapshot write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary_name,
                self.output_path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                if parent_fd >= 0:
                    os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            if parent_fd >= 0:
                os.close(parent_fd)


__all__ = [
    "CANONICAL_SOURCE_ROOT",
    "DEFAULT_SNAPSHOT_PATH",
    "EXPORT_INTERVAL_SECONDS",
    "MOUNTED_SOURCE_ROOT",
    "SNAPSHOT_TTL_SECONDS",
    "SafetyStateExporter",
    "source_fingerprint",
]
