"""Deterministic read projection from canonical alpha registry ledger events."""

from __future__ import annotations

import json
from typing import Protocol

from packages.alpha_lifecycle.registry import AlphaRecordV1, AlphaRegistryEventV1
from packages.data_contracts import ArtifactRefV1
from packages.domain.alpha_events import AlphaRegistryTransitionRecordedV1
from packages.domain.events import EventEnvelope
from packages.engine_contracts.serialization import canonical_json_bytes


class ArtifactReader(Protocol):
    def read_bytes(self, ref: ArtifactRefV1) -> bytes: ...


def rebuild_projection(
    events: tuple[EventEnvelope[object], ...], reader: ArtifactReader
) -> bytes:
    heads: dict[tuple[str, str], dict[str, object]] = {}
    expected: dict[tuple[str, str], tuple[int, str | None]] = {}
    for envelope in events:
        value = EventEnvelope[AlphaRegistryTransitionRecordedV1].model_validate(envelope)
        payload = value.payload
        raw = payload.registry_event_text.encode("utf-8")
        ref = ArtifactRefV1(
            content_sha256=payload.registry_event_sha256,
            size_bytes=len(raw),
            media_type="application/json",
            locator=f"{payload.registry_event_sha256}.blob",
        )
        if reader.read_bytes(ref) != raw:
            raise ValueError("ledger registry artifact read-back is invalid")
        document = json.loads(raw)
        record = AlphaRecordV1.model_validate_json(canonical_json_bytes(document["record"]))
        registry_event = AlphaRegistryEventV1(
            sequence=document["sequence"],
            predecessor_sha256=document["predecessor_sha256"],
            record=record,
            event_sha256=payload.registry_event_sha256,
            artifact=ref,
        )
        key = (record.alpha_id, record.version)
        sequence, digest = expected.get(key, (0, None))
        if registry_event.sequence != sequence + 1 or registry_event.predecessor_sha256 != digest:
            raise ValueError("registry ledger history is not contiguous")
        expected[key] = (registry_event.sequence, registry_event.event_sha256)
        heads[key] = {
            "alpha_id": record.alpha_id,
            "alpha_version": record.version,
            "lifecycle_status": record.lifecycle_status.value,
            "qualification_decision": record.qualification_decision.value,
            "qualification_reason": record.qualification_reason,
            "registry_event_sha256": registry_event.event_sha256,
            "registry_sequence": registry_event.sequence,
            "publication_status": "COMMITTED_REPORT_PENDING",
        }
    return canonical_json_bytes({
        "schema_version": "p3-alpha-projection-v1",
        "heads": [heads[key] for key in sorted(heads)],
    })


__all__ = ["ArtifactReader", "rebuild_projection"]
