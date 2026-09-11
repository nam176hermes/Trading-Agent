"""Immutable shared inputs and private content-addressed replica outputs."""

import hashlib
import json
from pathlib import Path
import re

from packages.data_catalog.artifact_store import (
    ArtifactIntegrityError,
    LocalArtifactStore,
)
from packages.data_contracts import ArtifactRefV1
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes


class ReplicaArtifactStore:
    def __init__(self, input_root: Path, output_root: Path) -> None:
        if input_root == output_root:
            raise ValueError("replica input and output stores must differ")
        self._inputs = LocalArtifactStore(input_root)
        self._outputs = LocalArtifactStore(output_root)

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        try:
            return self._outputs.read_bytes(ref)
        except ArtifactIntegrityError as error:
            if not isinstance(error.__cause__, FileNotFoundError):
                raise
        return self._inputs.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        return self._outputs.put_bytes(value, media_type=media_type)


def retain_replica_outputs(
    output_root: Path, destination: 'ArtifactStore', result_ref: ArtifactRefV1
) -> str:
    """Verify child CAS files before retaining them; this grants no job authority."""
    source = LocalArtifactStore(output_root)
    values = []
    total = result_ref.size_bytes
    for path in output_root.iterdir():
        if re.fullmatch(r"[0-9a-f]{64}\.blob", path.name) is None:
            raise ArtifactIntegrityError("unexpected replica artifact name")
        size = path.stat(follow_symlinks=False).st_size
        total += size + len(path.name)
        if total > 268_435_456:
            raise ArtifactIntegrityError("replica output exceeds the frozen byte bound")
        ref = ArtifactRefV1(
            content_sha256=path.stem,
            size_bytes=size,
            media_type="application/json",
            locator=path.name,
        )
        raw = source.read_bytes(ref)
        try:
            if canonical_json_bytes(json.loads(raw)) != raw:
                raise ValueError("replica artifact is not canonical JSON")
        except (ValueError, TypeError) as error:
            raise ArtifactIntegrityError(
                "replica artifact is not canonical JSON"
            ) from error
        values.append((ref, raw))
    inventory = [result_ref]
    for ref, raw in sorted(values, key=lambda item: item[0].locator):
        if destination.put_bytes(raw, media_type=ref.media_type) != ref:
            raise ArtifactIntegrityError("retained replica artifact identity differs")
        inventory.append(ref)
    raw_inventory = canonical_json_bytes(sorted(inventory, key=lambda ref: ref.locator))
    destination.put_bytes(raw_inventory, media_type="application/json")
    return hashlib.sha256(raw_inventory).hexdigest()
