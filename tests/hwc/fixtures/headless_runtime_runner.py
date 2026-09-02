#!/usr/bin/env python3
"""Long-lived JSONL process for the provider-free HWC paper fixture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes
from tests.fixtures.paper_runtime import DeterministicPaperRuntime


def _emit(payload: object) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(payload) + b"\n")
    sys.stdout.buffer.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    runtime = DeterministicPaperRuntime(arguments.root.resolve())
    _emit(
        {
            "schema_version": "hwc-headless-runtime-ready-v1",
            "pid": os.getpid(),
            "session_id": str(runtime.port.session_id),
        }
    )
    for raw in sys.stdin.buffer:
        try:
            request = json.loads(raw)
            if request == {"command": "shutdown"}:
                return 0
            _emit(json.loads(runtime.port.exchange(canonical_json_bytes(request))))
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            _emit({"schema_version": "hwc-headless-runtime-error-v1", "error": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
