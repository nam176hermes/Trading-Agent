"""Fail-closed, read-only Phase 1 safety evidence for research workers."""

from __future__ import annotations

import os
import stat
import weakref
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from packages.safety_evidence import CanonicalKillSwitchState as KillSwitchState, resolve_kill_switch

from .errors import SafetyBlockedError

APPROVED_DATA_ROOT = Path("/home/thenam176/.hermes/crypto-research")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class SafetyMode(StrEnum):
    PAPER = "PAPER"
    DRYRUN = "DRYRUN"
    LIVE = "LIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SafetySnapshot:
    requested_mode: SafetyMode
    effective_mode: SafetyMode
    live_execution_enabled: bool | None
    live_trading_approved: bool | None
    kill_switch_state: KillSwitchState


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class ValidatedDataRoot:
    _path: Path


_VALIDATED_ROOTS: weakref.WeakSet[ValidatedDataRoot] = weakref.WeakSet()


def _blocked(reason: str, message: str) -> None:
    raise SafetyBlockedError(reason, message)


def _contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return False
    return False


def validate_data_root(path: Path | str | None = None) -> ValidatedDataRoot:
    """Validate the one Phase 1 data root; overrides and aliases are denied."""

    candidate = APPROVED_DATA_ROOT if path is None else Path(path)
    if candidate != APPROVED_DATA_ROOT or not candidate.is_absolute() or ".." in candidate.parts:
        _blocked("SAFETY_DATA_ROOT_NOT_APPROVED", "safety data root is not the exact canonical root")
    if _contains_symlink(candidate):
        _blocked("SAFETY_DATA_ROOT_SYMLINK", "safety data root contains a symlink")
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        _blocked("SAFETY_DATA_ROOT_MISSING", "canonical safety data root does not exist")
    except OSError as exc:
        raise SafetyBlockedError("SAFETY_DATA_ROOT_UNREADABLE", "canonical safety data root cannot be inspected") from exc
    if not stat.S_ISDIR(info.st_mode):
        _blocked("SAFETY_DATA_ROOT_MISSING", "canonical safety data root is not a directory")
    if info.st_uid != os.geteuid():
        _blocked("SAFETY_DATA_ROOT_OWNER_UNSAFE", "canonical safety data root has an unsafe owner")
    if info.st_mode & 0o077:
        _blocked("SAFETY_DATA_ROOT_MODE_UNSAFE", "canonical safety data root permissions are unsafe")
    capability = ValidatedDataRoot()
    object.__setattr__(capability, "_path", candidate)
    _VALIDATED_ROOTS.add(capability)
    return capability


class SafetyProvider:
    """Read exact canonical mode, gate, and kill-switch evidence without writes."""

    def __init__(self, data_root: ValidatedDataRoot, *, source: Mapping[str, str] | None = None) -> None:
        if not isinstance(data_root, ValidatedDataRoot) or data_root not in _VALIDATED_ROOTS:
            raise TypeError("SafetyProvider requires a validated fixed data root")
        self._data_root = data_root._path
        self._source = os.environ if source is None else source
        self._kill_switch = self._data_root / ".kill_switch"

    def snapshot(self) -> SafetySnapshot:
        if "TRADING_KILL_SWITCH_PATH" in self._source:
            configured = Path(self._source["TRADING_KILL_SWITCH_PATH"])
            if configured != self._kill_switch:
                _blocked("SAFETY_KILL_SWITCH_PATH_OVERRIDE", "kill switch path overrides are forbidden")
        requested = self._requested_mode()
        enabled = self._gate("LIVE_EXECUTION_ENABLED")
        approved = self._gate("LIVE_TRADING_APPROVED")
        kill_switch = self._kill_switch_state()
        if requested is SafetyMode.UNKNOWN:
            effective = SafetyMode.UNKNOWN
        elif requested is SafetyMode.LIVE:
            if enabled is None or approved is None or kill_switch is KillSwitchState.UNKNOWN:
                effective = SafetyMode.UNKNOWN
            elif enabled and approved and kill_switch is KillSwitchState.INACTIVE:
                effective = SafetyMode.LIVE
            else:
                effective = SafetyMode.PAPER
        else:
            effective = requested
        return SafetySnapshot(requested, effective, enabled, approved, kill_switch)

    def _safe_file(self, path: Path) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return False
        return (
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077
        )

    def _requested_mode(self) -> SafetyMode:
        target = self._data_root / ".mode"
        if not self._safe_file(target):
            return SafetyMode.UNKNOWN
        try:
            return SafetyMode(target.read_text(encoding="utf-8").strip().upper())
        except (OSError, ValueError):
            return SafetyMode.UNKNOWN

    def _gate(self, key: str) -> bool | None:
        raw = self._source.get(key)
        if raw is None:
            return None
        normalized = raw.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
        return None

    def _kill_switch_state(self) -> KillSwitchState:
        return resolve_kill_switch(self._kill_switch)


def assert_safe(snapshot: SafetySnapshot) -> None:
    """Permit only complete, explicit paper/no-authority evidence."""

    if snapshot.requested_mode is SafetyMode.UNKNOWN:
        _blocked("SAFETY_REQUESTED_MODE_UNKNOWN", "requested mode evidence is missing or unknown")
    if snapshot.requested_mode is not SafetyMode.PAPER:
        _blocked("SAFETY_REQUESTED_MODE_NOT_PAPER", "requested mode is not paper")
    if snapshot.effective_mode is SafetyMode.UNKNOWN:
        _blocked("SAFETY_EFFECTIVE_MODE_UNKNOWN", "effective mode is unknown")
    if snapshot.effective_mode is not SafetyMode.PAPER:
        _blocked("SAFETY_EFFECTIVE_MODE_NOT_PAPER", "effective mode is not paper")
    if snapshot.live_execution_enabled is None:
        _blocked("SAFETY_LIVE_EXECUTION_GATE_UNKNOWN", "execution gate evidence is missing or unknown")
    if snapshot.live_execution_enabled:
        _blocked("SAFETY_LIVE_EXECUTION_GATE_ENABLED", "execution gate is enabled")
    if snapshot.live_trading_approved is None:
        _blocked("SAFETY_LIVE_APPROVAL_GATE_UNKNOWN", "approval gate evidence is missing or unknown")
    if snapshot.live_trading_approved:
        _blocked("SAFETY_LIVE_APPROVAL_GATE_ENABLED", "approval gate is enabled")
    if snapshot.kill_switch_state is KillSwitchState.UNKNOWN:
        _blocked("SAFETY_KILL_SWITCH_UNKNOWN", "kill switch evidence is missing or unknown")
    if snapshot.kill_switch_state is not KillSwitchState.INACTIVE:
        _blocked("SAFETY_KILL_SWITCH_ACTIVE", "kill switch is active")


__all__ = [
    "APPROVED_DATA_ROOT", "KillSwitchState", "SafetyMode", "SafetyProvider",
    "SafetySnapshot", "ValidatedDataRoot", "assert_safe", "validate_data_root",
]
