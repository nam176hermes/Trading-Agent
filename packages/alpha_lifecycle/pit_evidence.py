"""Construct P3 PIT proof only after dataset and fold evidence exist."""

from __future__ import annotations

import hashlib

from packages.alpha_lifecycle.contracts.data import DatasetEvidence, FoldManifest, PITProof
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def build_pit_proof(
    dataset: DatasetEvidence,
    fold_manifest: FoldManifest,
    revision_proof_ref: ArtifactRefV1,
    no_future_suite_ref: ArtifactRefV1,
    store: LocalArtifactStore,
) -> PITProof:
    dataset_ref = store.put_bytes(canonical_json_bytes(dataset), media_type="application/json")
    fold_ref = store.put_bytes(canonical_json_bytes(fold_manifest), media_type="application/json")
    payload = {
        "schema_version": "p3-p-i-t-proof-v1",
        "dataset_ref": dataset_ref,
        "fold_manifest_ref": fold_ref,
        "vintage_class": dataset.vintage_class,
        "historical_vintage_verified": dataset.vintage_class == "HISTORICAL_VINTAGE_VERIFIED",
        "revision_proof_ref": revision_proof_ref,
        "no_future_suite_ref": no_future_suite_ref,
        "limitations": dataset.limitations,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PITProof.model_validate(payload)


__all__ = ["build_pit_proof"]
