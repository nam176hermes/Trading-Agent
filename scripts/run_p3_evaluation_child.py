"""Fixed child executable for one deterministic P3 evaluation manifest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
from packages.alpha_lifecycle.evaluation import evaluate
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def main(manifest_ref_path: Path, store_root: Path, output: Path) -> None:
    store = LocalArtifactStore(store_root)
    ref = ArtifactRefV1.model_validate(json.loads(manifest_ref_path.read_bytes()))
    manifest = EvaluationManifest.model_validate_json(store.read_bytes(ref))
    result = evaluate(manifest, store)
    output.write_bytes(canonical_json_bytes(result))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest_ref", type=Path)
    parser.add_argument("store", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.manifest_ref, args.store, args.output)
