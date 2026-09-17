"""Seal pre-materialized P2 V3 partitions into P3 dataset evidence."""

from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.acquisition import daily_arrow_table, validate_acquisition_receipt
from packages.alpha_lifecycle.data_view import seal_research_dataset, validate_dates
from packages.alpha_lifecycle.pit_evidence import P3ResearchRevisionInventory, materialize_daily_revision
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArrowSchemaV1, ArtifactRefV1, DatasetPartitionManifestV3, PITQueryV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.pre_p3_provenance import canonical_source_identity


def main(input_path: Path, store_root: Path, *, acquisition_refs: bool = False) -> None:
    payload = json.loads(input_path.read_bytes())
    store = LocalArtifactStore(store_root)
    query = PITQueryV1.model_validate_json(canonical_json_bytes(payload["query"]))
    if acquisition_refs:
        references = payload["acquisition_refs"]
        if not isinstance(references, list) or not 2800 <= len(references) <= 100000:
            raise ValueError("research acquisition input requires 2800 through 100000 references")
        acquired = tuple(validate_acquisition_receipt(ArtifactRefV1.model_validate(ref), store) for ref in references)
        keys = tuple((receipt.day, receipt.fetched_at) for receipt in acquired)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("acquisitions must be unique and ordered by day and fetch time")
        validate_dates("RESEARCH", tuple(sorted({receipt.day for receipt in acquired})))
        ingested_at = datetime.now(UTC)
        if ingested_at > query.cutoff:
            raise ValueError("query cutoff must include actual materialization time")
        entries = []
        previous = {}
        for receipt in acquired:
            entry = materialize_daily_revision(receipt.artifact_ref, store,
                ingested_at=ingested_at, previous=previous.get(receipt.day))
            entries.append(entry)
            previous[receipt.day] = entry
        schema, _ = daily_arrow_table(acquired[0].artifact_ref, store)
        evidence = seal_research_dataset(tuple(entry.partition for entry in entries), query, schema, store)
        body = dict(schema_version="p3-research-revision-inventory-v1",
            source=canonical_source_identity(ROOT), entries=tuple(entries),
            policy_digest=hashlib.sha256(canonical_json_bytes(json.loads(
                (ROOT / "docs/implementation/p3/specs/p3-policy-set-v21.json").read_bytes()
            ))).hexdigest())
        body["digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        inventory = P3ResearchRevisionInventory.model_validate(body)
        ref = store.put_bytes(canonical_json_bytes(inventory), media_type="application/json")
        print(json.dumps(dict(dataset=evidence.model_dump(mode="json"), revision_inventory_ref=ref.model_dump(mode="json"))))
        return
    evidence = seal_research_dataset(
        tuple(DatasetPartitionManifestV3.model_validate_json(canonical_json_bytes(item)) for item in payload["partitions"]),
        query,
        ArrowSchemaV1.model_validate_json(canonical_json_bytes(payload["schema"])),
        store,
    )
    print(evidence.model_dump_json())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("store", type=Path)
    parser.add_argument("--acquisition-refs", action="store_true", help="materialize retained research acquisition receipts through P2")
    args = parser.parse_args()
    main(args.input, args.store, acquisition_refs=args.acquisition_refs)
