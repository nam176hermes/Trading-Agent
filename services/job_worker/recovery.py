"""Process identity inspection used by conservative lease recovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """The complete identity needed to distinguish a live child from PID reuse."""

    pid: int
    process_group: int
    start_ticks: int
    command_fingerprint: str

    def __post_init__(self) -> None:
        if self.pid <= 0 or self.process_group <= 0 or self.start_ticks < 0:
            raise ValueError("process identity values are invalid")
        if len(self.command_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.command_fingerprint
        ):
            raise ValueError("command fingerprint must be lowercase SHA-256")


class ProcessInspector(Protocol):
    """Return the current identity for a PID, or ``None`` only if it is absent."""

    def inspect(self, pid: int) -> ProcessIdentity | None: ...


class ProcProcessInspector:
    """Read Linux procfs without signaling or otherwise changing the process."""

    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self._proc_root = proc_root

    def inspect(self, pid: int) -> ProcessIdentity | None:
        process_root = self._proc_root / str(pid)
        try:
            stat = (process_root / "stat").read_text(encoding="utf-8")
            command = (process_root / "cmdline").read_bytes()
        except FileNotFoundError:
            return None
        end_of_name = stat.rfind(")")
        if end_of_name < 0:
            raise RuntimeError("process stat identity is malformed")
        # Fields after the command name start at field 3 (state). pgrp is field
        # 5 and process start time is field 22 in proc_pid_stat(5).
        tail = stat[end_of_name + 2 :].split()
        if len(tail) < 20:
            raise RuntimeError("process stat identity is incomplete")
        return ProcessIdentity(
            pid=pid,
            process_group=int(tail[2]),
            start_ticks=int(tail[19]),
            command_fingerprint=hashlib.sha256(command).hexdigest(),
        )
