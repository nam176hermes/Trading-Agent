"""Canonical, read-only safety sentinel resolution shared by all services."""

from __future__ import annotations

import os
import hashlib
import json
import stat
from datetime import datetime
from enum import StrEnum
from pathlib import Path


CANONICAL_SAFETY_SOURCE_ROOT = Path("/home/thenam176/.hermes/crypto-research")


def safety_source_fingerprint(canonical_source_root: Path) -> str:
    """Identify the exact allowlisted safety names without reading the root."""

    root = Path(canonical_source_root)
    record = {
        "gate_sources": ["LIVE_EXECUTION_ENABLED", "LIVE_TRADING_APPROVED"],
        "kill_switch_path": os.fspath(root / ".kill_switch"),
        "mode_path": os.fspath(root / ".mode"),
    }
    encoded = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


class CanonicalKillSwitchState(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


def resolve_kill_switch(path: Path) -> CanonicalKillSwitchState:
    """Resolve the exact canonical sentinel without following unsafe objects.

    Absence is the established inactive state. Any present object must be a
    private, owner-controlled regular file containing one timestamp/reason line.
    """

    try:
        info = path.lstat()
    except FileNotFoundError:
        return CanonicalKillSwitchState.INACTIVE
    except OSError:
        return CanonicalKillSwitchState.UNKNOWN
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        return CanonicalKillSwitchState.UNKNOWN
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) != 1:
            return CanonicalKillSwitchState.UNKNOWN
        activated_at, separator, reason = lines[0].partition(": ")
        if not separator or not reason.strip():
            return CanonicalKillSwitchState.UNKNOWN
        datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
        return CanonicalKillSwitchState.ACTIVE
    except (OSError, UnicodeError, ValueError):
        return CanonicalKillSwitchState.UNKNOWN


__all__ = [
    "CANONICAL_SAFETY_SOURCE_ROOT", "CanonicalKillSwitchState",
    "resolve_kill_switch", "safety_source_fingerprint",
]
