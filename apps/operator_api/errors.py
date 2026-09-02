"""Typed, non-sensitive Operator API errors."""

from __future__ import annotations


class OperatorApiError(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


__all__ = ["OperatorApiError"]
