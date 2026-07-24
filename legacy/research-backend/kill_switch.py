"""Canonical fail-closed emergency stop shared by the agent and dashboard."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile

from runtime_paths import kill_switch_file


class KillSwitchState(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class KillSwitchStatus:
    state: KillSwitchState
    reason: str | None = None
    activated_at: str | None = None
    error_code: str | None = None


def resolve_kill_switch_path() -> Path:
    return kill_switch_file()


def read_kill_switch_state() -> KillSwitchStatus:
    target = resolve_kill_switch_path()
    try:
        if not target.exists():
            return KillSwitchStatus(KillSwitchState.INACTIVE)
        content = target.read_text(encoding="utf-8").strip()
        activated_at, separator, reason = content.partition(": ")
        if not separator or not reason.strip():
            return KillSwitchStatus(KillSwitchState.UNKNOWN, error_code="INVALID_STATE")
        datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
        return KillSwitchStatus(KillSwitchState.ACTIVE, reason.strip(), activated_at)
    except (OSError, ValueError):
        return KillSwitchStatus(KillSwitchState.UNKNOWN, error_code="READ_ERROR")


def is_kill_switch_active() -> bool:
    status = read_kill_switch_state()
    if status.state is not KillSwitchState.INACTIVE:
        import logging
        logging.getLogger("kill_switch").warning(
            "[KILL SWITCH] Trading halted: state=%s code=%s",
            status.state.value,
            status.error_code or "ACTIVE",
        )
        return True
    return False


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def activate_kill_switch(reason: str = "Manual override") -> KillSwitchStatus:
    target = resolve_kill_switch_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile("w", dir=target.parent, prefix=f".{target.name}.", delete=False, encoding="utf-8") as handle:
        temporary = Path(handle.name)
        handle.write(f"{datetime.now(timezone.utc).isoformat()}: {reason.strip() or 'Manual override'}\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
    _sync_directory(target.parent)
    return read_kill_switch_state()


def deactivate_kill_switch() -> KillSwitchStatus:
    target = resolve_kill_switch_path()
    if target.exists():
        target.unlink()
        _sync_directory(target.parent)
    return read_kill_switch_state()


if __name__ == "__main__":
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if command == "on":
        status = activate_kill_switch(sys.argv[2] if len(sys.argv) > 2 else "Manual override")
    elif command == "off":
        status = deactivate_kill_switch()
    elif command == "status":
        status = read_kill_switch_state()
    else:
        print(f"Unknown command: {command}")
        raise SystemExit(1)
    print(f"Kill switch: {status.state.value}")
