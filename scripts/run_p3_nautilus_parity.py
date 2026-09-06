#!/usr/bin/env python3
"""Enter the sealed Nautilus 1.231.0 runtime through its P1-owned guard."""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.job_worker.p3_native_runner import PINNED_ENGINE_VERSION


def main() -> None:
    profile = P1_REAL_BACKTEST_POLICY
    if profile.engine_version != PINNED_ENGINE_VERSION:
        raise SystemExit("E_NATIVE_VERSION: P1 runtime does not match P3 pin")
    argv = (
        profile.entrypoint,
        *profile.argv_prefix,
        "/inputs/request.json",
        "/inputs/request.sha256",
    )
    os.execve(profile.entrypoint, argv, {})
    raise SystemExit("E_NATIVE_EXEC: native guard returned")  # pragma: no cover


if __name__ == "__main__":
    main()
