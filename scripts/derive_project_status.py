#!/usr/bin/env python3
"""Print or verify the canonical status derived from immutable receipts."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.project_status import derive_project_status


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(arguments)
    expected = canonical_json_bytes(derive_project_status(args.root)) + b"\n"
    if args.check is not None:
        if not args.check.is_file() or args.check.read_bytes() != expected:
            print("canonical project status is stale", file=sys.stderr)
            return 1
        return 0
    sys.stdout.buffer.write(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
