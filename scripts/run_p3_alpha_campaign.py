#!/usr/bin/env python3
"""Run exactly three P3 replicas through the reviewed Bubblewrap executor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.baseline_campaign import execute_baseline_manifest
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-ref", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--environment-ref", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--sandbox-policy-digest", required=True)
    parser.add_argument("--logical-trial-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    store = LocalArtifactStore(args.store)
    executor = BubblewrapExecutor(
        store=store, store_root=args.store, release_root=args.release, python=args.python,
        source=SourceIdentity.model_validate_json(args.source.read_bytes()),
        environment_ref=ArtifactRefV1.model_validate_json(args.environment_ref.read_bytes()),
        sandbox_policy_digest=args.sandbox_policy_digest,
    )
    result = execute_baseline_manifest(
        ArtifactRefV1.model_validate_json(args.manifest_ref.read_bytes()), executor,
        logical_trial_id=args.logical_trial_id, output_root=args.output,
    )
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")


if __name__ == "__main__":
    main()
