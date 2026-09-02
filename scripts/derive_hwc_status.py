#!/usr/bin/env python3
"""Print, check, or atomically write the derived HWC source status."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.hwc_status import derive_hwc_source_status


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--check", type=Path)
    output.add_argument("--write", type=Path)
    args = parser.parse_args(arguments)
    expected = canonical_json_bytes(derive_hwc_source_status(args.root)) + b"\n"
    if args.check is not None:
        if (
            args.check.is_symlink()
            or not args.check.is_file()
            or args.check.read_bytes() != expected
        ):
            print("canonical HWC source status is stale", file=sys.stderr)
            return 1
        return 0
    if args.write is not None:
        destination = Path(os.path.abspath(args.write))
        if not destination.parent.is_dir() or (
            destination.exists()
            and (destination.is_symlink() or not destination.is_file())
        ):
            print("canonical HWC source status destination is invalid", file=sys.stderr)
            return 2
        mode = stat.S_IMODE(destination.lstat().st_mode) if destination.exists() else 0o644
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(expected)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, destination)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            print(f"canonical HWC source status write failed: {exc}", file=sys.stderr)
            return 2
        return 0
    sys.stdout.buffer.write(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
