#!/usr/bin/env python3
"""Emit one redacted restore-proof failure code from a private capture."""

from __future__ import annotations

from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.restore_proof_failure_codes import (  # noqa: E402
    NO_RESTORE_PROOF_CODE,
    extract_restore_proof_failure_code,
)


def _input_path(arguments: list[str]) -> str | None:
    if len(arguments) != 2 or arguments[0] != "--input":
        return None
    return arguments[1]


def main(arguments: list[str] | None = None) -> int:
    input_path = _input_path(sys.argv[1:] if arguments is None else arguments)
    if input_path is None:
        failure_code = NO_RESTORE_PROOF_CODE
    else:
        try:
            with open(
                input_path,
                "r",
                encoding="utf-8",
                errors="strict",
                newline="",
            ) as private_capture:
                failure_code = extract_restore_proof_failure_code(private_capture)
        except (OSError, UnicodeError):
            failure_code = NO_RESTORE_PROOF_CODE
    sys.stdout.write(f"{failure_code}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
