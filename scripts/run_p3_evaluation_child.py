"""Fixed child executable for one deterministic P3 evaluation manifest."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, localcontext
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.baseline_campaign import run_baseline_pack
from packages.alpha_lifecycle.contracts.base import parse_contract
from packages.alpha_lifecycle.evaluation import evaluate
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def main(manifest_ref_path: Path, store_root: Path, output: Path) -> None:
    artifact_root = output.parent / "artifacts"
    artifact_root.mkdir(mode=0o700, exist_ok=False)
    store = ReplicaArtifactStore(store_root, artifact_root)
    ref = ArtifactRefV1.model_validate(json.loads(manifest_ref_path.read_bytes()))
    raw = store.read_bytes(ref)
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        if json.loads(raw).get("schema_version") == "p3-baseline-manifest-v1":
            result = run_baseline_pack(parse_contract("BaselineManifest", raw), store)
        else:
            result = evaluate(parse_contract("EvaluationManifest", raw), store)
    output.write_bytes(canonical_json_bytes(result))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest_ref", type=Path)
    parser.add_argument("store", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.manifest_ref, args.store, args.output)
