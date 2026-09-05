#!/usr/bin/env python3
"""Build a P3 promotion receipt from exact protected-main Foundation identity."""

from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.p3_provenance import PhaseExitReceipt, PromotionReceipt
from packages.pre_p3_provenance import canonical_source_identity


def build_promotion(environment: dict[str, str]) -> PromotionReceipt:
    promoted = SourceIdentity.model_validate(canonical_source_identity(ROOT))
    if (
        environment.get("GITHUB_REPOSITORY") != "nam176hermes/Trading-Agent"
        or environment.get("GITHUB_WORKFLOW") != "Foundation"
        or environment.get("GITHUB_WORKFLOW_REF")
        != "nam176hermes/Trading-Agent/.github/workflows/foundation.yml@refs/heads/main"
        or environment.get("GITHUB_EVENT_NAME") != "push"
        or environment.get("GITHUB_REF") != "refs/heads/main"
        or environment.get("GITHUB_SHA") != promoted.commit_sha
        or environment.get("GITHUB_WORKFLOW_SHA") != promoted.commit_sha
    ):
        raise RuntimeError("protected Foundation identity is required")
    run_id = environment.get("GITHUB_RUN_ID", "")
    attempt = environment.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isdigit() or run_id.startswith("0") or not attempt.isdigit() or attempt.startswith("0"):
        raise RuntimeError("protected Foundation run identity is invalid")
    phase = PhaseExitReceipt.model_validate_json(
        (ROOT / "docs/implementation/p3/receipts/p3-phase-exit-v1.json").read_bytes()
    )
    if phase.source.closure_sha256 != promoted.closure_sha256:
        raise RuntimeError("phase-exit semantic closure is stale")
    payload = {
        "schema_version": "p3-promotion-v1", "qualified_source": phase.source,
        "promoted_source": promoted, "phase_exit_receipt_digest": phase.digest,
        "foundation_run_id": int(run_id), "foundation_attempt": int(attempt),
        "workflow_ref": "foundation.yml", "status": "PASS",
        "authority": {"broker": False, "live": False, "network": False, "production": False},
    }
    payload["digest"] = sha256(canonical_json_bytes(payload)).hexdigest()
    return PromotionReceipt.model_validate(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, canonical_json_bytes(build_promotion(dict(os.environ))) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    main()
