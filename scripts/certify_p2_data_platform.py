#!/usr/bin/env python3
"""Run the deterministic P2 certification three times without external I/O."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_certification import certify_p2_data_platform


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="trading-agent-p2-", dir="/tmp") as directory:
        store = LocalArtifactStore(Path(directory))
        receipts = tuple(certify_p2_data_platform(store) for _ in range(3))
    if receipts[1:] != receipts[:-1]:
        raise RuntimeError("P2 certification is not repeatable three of three")
    receipt = receipts[0]
    payload = {**receipt.payload(), "receipt_sha256": receipt.receipt_sha256, "repetitions": 3}
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
