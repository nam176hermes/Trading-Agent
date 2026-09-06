from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.p3_status import derive_p3_status


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--check",type=Path)
    args=parser.parse_args(); raw=canonical_json_bytes(derive_p3_status(ROOT))+b"\n"
    if args.check is not None:
        raise SystemExit(0 if args.check.read_bytes()==raw else 1)
    sys.stdout.buffer.write(raw)
