"""Seal pre-materialized P2 V3 partitions into P3 dataset evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.data_view import seal_research_dataset
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArrowSchemaV1, DatasetPartitionManifestV3, PITQueryV1


def main(input_path: Path, store_root: Path) -> None:
    payload = json.loads(input_path.read_bytes())
    evidence = seal_research_dataset(
        tuple(DatasetPartitionManifestV3.model_validate(item) for item in payload["partitions"]),
        PITQueryV1.model_validate(payload["query"]),
        ArrowSchemaV1.model_validate(payload["schema"]),
        LocalArtifactStore(store_root),
    )
    print(evidence.model_dump_json())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("store", type=Path)
    args = parser.parse_args()
    main(args.input, args.store)
