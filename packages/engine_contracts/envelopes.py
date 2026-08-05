"""Strict authority envelopes shared by engine commands and events."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

from .commands import EngineCommand
from .events import EngineEvent
from .serialization import (
    CanonicalUtcDateTime,
    ProducerIdentity,
    Sha256Hex,
    SourceCommit,
    payload_digest,
)
from .versions import SchemaVersion


class _EnvelopeModel(BaseModel):
    """Authority metadata required on every engine protocol message."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    message_id: UUID
    correlation_id: UUID
    causation_id: UUID
    engine_run_id: UUID
    stream_sequence: Annotated[StrictInt, Field(gt=0)]
    event_time: CanonicalUtcDateTime
    initialization_time: CanonicalUtcDateTime
    schema_version: SchemaVersion
    producer_identity: ProducerIdentity
    source_commit: SourceCommit
    config_digest: Sha256Hex
    payload_digest: Sha256Hex

    @model_validator(mode="after")
    def _validate_authority_metadata(self) -> "_EnvelopeModel":
        if self.initialization_time > self.event_time:
            raise ValueError("initialization_time must not be after event_time")
        payload = getattr(self, "payload", None)
        if payload is None or payload_digest(payload) != self.payload_digest:
            raise ValueError("payload_digest does not match the canonical payload")
        return self


class EngineCommandEnvelope(_EnvelopeModel):
    """A validated command plus complete execution authority metadata."""

    payload: Annotated[EngineCommand, Field(discriminator="command_type")]


class EngineEventEnvelope(_EnvelopeModel):
    """A classified event plus complete execution authority metadata."""

    payload: EngineEvent


EngineEnvelope: TypeAlias = EngineCommandEnvelope | EngineEventEnvelope


def validate_envelope_batch(
    envelopes: Iterable[EngineEnvelope],
) -> tuple[EngineEnvelope, ...]:
    """Reject duplicate identities and non-increasing per-run stream sequences."""

    batch = tuple(envelopes)
    message_ids: set[UUID] = set()
    last_sequence: dict[UUID, int] = {}
    for envelope in batch:
        if envelope.message_id in message_ids:
            raise ValueError(f"duplicate message_id: {envelope.message_id}")
        message_ids.add(envelope.message_id)
        previous = last_sequence.get(envelope.engine_run_id)
        if previous is not None and envelope.stream_sequence <= previous:
            raise ValueError(
                "stream_sequence must increase for engine_run_id "
                f"{envelope.engine_run_id}: {envelope.stream_sequence} after {previous}"
            )
        last_sequence[envelope.engine_run_id] = envelope.stream_sequence
    return batch
