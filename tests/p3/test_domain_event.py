from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import hashlib

from packages.domain.alpha_events import AlphaRegistryTransitionRecordedV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.domain.events import EventEnvelope
from packages.event_ledger.replay import deserialize_event, serialize_event


def test_alpha_transition_payload_uses_closed_canonical_event_dispatch() -> None:
    registry_event_text = canonical_json_bytes({
        "predecessor_sha256": None,
        "record": {"alpha_id": "a0.donchian-20-10-close-confirm", "version": "1.0.0"},
        "schema_version": "alpha-registry-event-v1",
        "sequence": 1,
    }).decode()
    payload = AlphaRegistryTransitionRecordedV1(
        epoch_id="p3-btc-d1-e1", alpha_id="a0.donchian-20-10-close-confirm",
        alpha_version="1.0.0", registry_sequence=1, predecessor_sha256=None,
        registry_event_sha256=hashlib.sha256(registry_event_text.encode()).hexdigest(),
        registry_event_text=registry_event_text, evidence_sha256="b" * 64,
    )
    now = datetime(2026, 9, 5, tzinfo=UTC)
    event = EventEnvelope[AlphaRegistryTransitionRecordedV1](
        event_id=UUID("00000000-0000-5000-8000-000000000001"),
        event_type="AlphaRegistryTransitionRecordedV1", schema_version="1.0.0",
        source="p3-alpha-lifecycle", stream_id=UUID("00000000-0000-5000-8000-000000000002"),
        sequence=1, observed_at=now, ingested_at=now, produced_at=now,
        effective_at=now, expires_at=now + timedelta(days=1),
        correlation_id=UUID(int=3), causation_id=UUID(int=4), trace_id=UUID(int=5),
        payload=payload,
    )
    encoded = serialize_event(event)
    assert deserialize_event(encoded).payload == payload
