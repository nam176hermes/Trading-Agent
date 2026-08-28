"""Bounded path-free diagnostics for the sealed P1 runtime."""

import os


_CODES = {
    "E_BOOTSTRAP",
    "E_RUNTIME_NOT_READY",
}


def diagnostic_line(code: str) -> bytes:
    if type(code) is not str or code not in _CODES:
        raise ValueError("unknown runtime diagnostic")
    return b"P1_RUNTIME:" + code.encode("ascii") + b"\n"


def emit_diagnostic(code: str) -> None:
    os.write(2, diagnostic_line(code))
