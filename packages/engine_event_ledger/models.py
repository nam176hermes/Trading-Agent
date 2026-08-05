"""Strict, engine-neutral records for validated engine event ingestion."""

from __future__ import annotations

import hashlib
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.engine_contracts import EngineEventEnvelope, EventFamily, canonical_json


Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveSequence = Annotated[int, Field(gt=0, le=9_223_372_036_854_775_807)]
NonNegativeCount = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)]
EventType = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$"),
]
FIRST_ENGINE_EVENT_SEQUENCE: Final = 2


class EngineEventLedgerModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class StoredEngineEvent(EngineEventLedgerModel):
    """Canonical engine event bytes and their stable identity digest."""

    message_id: UUID
    engine_run_id: UUID
    stream_sequence: PositiveSequence
    event_type: EventType
    event_family: EventFamily
    canonical_json: Annotated[str, Field(min_length=2)]
    digest: Sha256Hex
    batch_sha256: Sha256Hex

    @classmethod
    def from_envelope(
        cls,
        event: EngineEventEnvelope,
        *,
        batch_sha256: str,
    ) -> "StoredEngineEvent":
        serialized = canonical_json(event)
        return cls(
            message_id=event.message_id,
            engine_run_id=event.engine_run_id,
            stream_sequence=event.stream_sequence,
            event_type=event.payload.event_type,
            event_family=event.payload.family,
            canonical_json=serialized,
            digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            batch_sha256=batch_sha256,
        )


class EngineEventTypeCount(EngineEventLedgerModel):
    event_type: EventType
    count: NonNegativeCount


class EngineRunProjection(EngineEventLedgerModel):
    engine_run_id: UUID
    event_count: NonNegativeCount
    event_type_counts: tuple[EngineEventTypeCount, ...]
    last_sequence: PositiveSequence
    last_digest: Sha256Hex


class EngineEventBatchReceipt(EngineEventLedgerModel):
    batch_sha256: Sha256Hex
    ingestion_digest: Sha256Hex
    job_id: Annotated[str, Field(min_length=1, max_length=128)]
    attempt_id: Annotated[str, Field(min_length=1, max_length=128)]
    engine_run_id: UUID
    event_count: Annotated[int, Field(gt=0, le=4_096)]
    first_sequence: PositiveSequence
    last_sequence: PositiveSequence
    last_digest: Sha256Hex


class EngineJobResultBinding(EngineEventLedgerModel):
    job_id: Annotated[str, Field(pattern=r"^job_[0-9a-f]{32}$")]
    attempt_id: Annotated[str, Field(pattern=r"^attempt_[0-9a-f]{32}$")]
    batch_sha256: Sha256Hex


class EngineEventLedgerState(EngineEventLedgerModel):
    """Authoritative fake persistence retained across process restarts."""

    events: tuple[StoredEngineEvent, ...]
    receipts: tuple[EngineEventBatchReceipt, ...]
    job_results: tuple[EngineJobResultBinding, ...] = ()
