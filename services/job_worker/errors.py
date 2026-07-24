"""Sanitized, reason-coded failures raised before worker process creation."""

from __future__ import annotations


class WorkerBlockedError(RuntimeError):
    """A fail-closed worker precondition was not satisfied."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CommandRegistryError(WorkerBlockedError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(reason_code, message)


class SafetyBlockedError(WorkerBlockedError):
    pass
