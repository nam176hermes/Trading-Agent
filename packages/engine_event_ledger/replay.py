"""Deterministic projection reduction for stored engine events."""

from __future__ import annotations

from collections import Counter
from uuid import UUID
import hashlib

from packages.engine_contracts import EngineEventEnvelope, canonical_json

from .errors import EngineEventConflictError, EngineEventSequenceBlockedError
from .models import (
    FIRST_ENGINE_EVENT_SEQUENCE,
    EngineEventTypeCount,
    EngineRunProjection,
    StoredEngineEvent,
)


def project_engine_run(
    events: tuple[StoredEngineEvent, ...],
    *,
    batch_sha256: str | None = None,
    semantic_digest: str | None = None,
    request_message_id: UUID | None = None,
) -> EngineRunProjection:
    if not events:
        raise ValueError("engine run projection requires at least one event")
    ordered = tuple(sorted(events, key=lambda event: event.stream_sequence))
    run_id = ordered[0].engine_run_id
    expected_sequence = FIRST_ENGINE_EVENT_SEQUENCE
    message_ids = set()
    for event in ordered:
        try:
            envelope = EngineEventEnvelope.model_validate_json(event.canonical_json)
        except ValueError as exc:
            raise EngineEventConflictError(
                f"stored canonical content is invalid for message_id {event.message_id}"
            ) from exc
        if (
            event.engine_run_id != run_id
            or event.message_id in message_ids
            or envelope.message_id != event.message_id
            or envelope.engine_run_id != event.engine_run_id
            or envelope.stream_sequence != event.stream_sequence
            or envelope.payload.event_type != event.event_type
            or envelope.payload.family is not event.event_family
            or canonical_json(envelope) != event.canonical_json
            or hashlib.sha256(event.canonical_json.encode("utf-8")).hexdigest()
            != event.digest
        ):
            raise EngineEventConflictError(
                f"stored event identity is inconsistent for message_id {event.message_id}"
            )
        if event.stream_sequence != expected_sequence:
            raise EngineEventSequenceBlockedError(
                engine_run_id=run_id,
                expected_sequence=expected_sequence,
                actual_sequence=event.stream_sequence,
            )
        message_ids.add(event.message_id)
        expected_sequence += 1
    counts = Counter(event.event_type for event in ordered)
    return EngineRunProjection(
        engine_run_id=ordered[0].engine_run_id,
        event_count=len(ordered),
        event_type_counts=tuple(
            EngineEventTypeCount(event_type=event_type, count=counts[event_type])
            for event_type in sorted(counts)
        ),
        last_sequence=ordered[-1].stream_sequence,
        last_digest=ordered[-1].digest,
        batch_sha256=batch_sha256,
        semantic_digest=semantic_digest,
        request_message_id=request_message_id,
    )
