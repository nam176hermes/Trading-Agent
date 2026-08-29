"""Stable path-free failures for the sealed P1 runtime."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar


ERROR_FAMILIES = {
    "INPUT_INVALID",
    "PROFILE_UNSUPPORTED",
    "ENGINE_SETUP_FAILED",
    "ENGINE_EXECUTION_FAILED",
    "EVENT_PROJECTION_FAILED",
    "FINAL_STATE_MISMATCH",
    "OUTPUT_FAILED",
}
_T = TypeVar("_T")


class RuntimeFailure(RuntimeError):
    """A classified runtime failure whose detail remains in ``__cause__``."""

    @property
    def family(self) -> str:
        return str(self.args[0])


def _failure(family: str) -> RuntimeFailure:
    if type(family) is not str or family not in ERROR_FAMILIES:
        raise ValueError("unknown runtime failure family")
    return RuntimeFailure(family)


def guarded(family: str, action: Callable[..., _T], *args: object, **kwargs: object) -> _T:
    try:
        return action(*args, **kwargs)
    except Exception as cause:
        raise _failure(family) from cause


def classified(family: str, cause: Exception) -> RuntimeFailure:
    raise _failure(family) from cause


def diagnostic_line(
    family: str,
    *,
    engine_version: str | None = None,
    closure_digest: str | None = None,
) -> bytes:
    if type(family) is not str or family not in ERROR_FAMILIES:
        raise ValueError("unknown runtime failure family")
    line = b"P1_RUNTIME:" + family.encode("ascii")
    if engine_version is None and closure_digest is None:
        return line + b"\n"
    if (
        engine_version != "1.231.0"
        or type(closure_digest) is not str
        or len(closure_digest) != 64
        or any(character not in "0123456789abcdef" for character in closure_digest)
    ):
        raise ValueError("invalid runtime diagnostic lineage")
    return line + b":" + engine_version.encode("ascii") + b":" + closure_digest.encode("ascii") + b"\n"


def emit_diagnostic(
    family: str,
    *,
    engine_version: str | None = None,
    closure_digest: str | None = None,
) -> None:
    os.write(
        2,
        diagnostic_line(
            family,
            engine_version=engine_version,
            closure_digest=closure_digest,
        ),
    )


__all__ = [
    "ERROR_FAMILIES",
    "RuntimeFailure",
    "classified",
    "diagnostic_line",
    "emit_diagnostic",
    "guarded",
]
