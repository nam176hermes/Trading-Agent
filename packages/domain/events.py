"""Immutable, replay-safe canonical event envelope."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Iterable, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .clock import require_utc
from .orders import FillEvent, OrderEvent, OrderIntent
from .portfolio import TargetPortfolio
from .risk import RiskDecision
from .signals import SignalProposal


NonEmptyText = Annotated[str, Field(min_length=1)]
PayloadT = TypeVar("PayloadT")

EVENT_TYPE_BY_PAYLOAD: dict[type[object], str] = {
    SignalProposal: "SignalProposal",
    TargetPortfolio: "TargetPortfolio",
    RiskDecision: "RiskDecision",
    OrderIntent: "OrderIntent",
    OrderEvent: "OrderEvent",
    FillEvent: "FillEvent",
}


def _registered_event_type(payload_type: object) -> str:
    if not isinstance(payload_type, type) or payload_type not in EVENT_TYPE_BY_PAYLOAD:
        raise ValueError("payload type is not registered for an event envelope")
    return EVENT_TYPE_BY_PAYLOAD[payload_type]


class EventEnvelope(BaseModel, Generic[PayloadT]):
    """Authority metadata plus one strictly typed immutable domain payload."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    event_id: UUID
    event_type: NonEmptyText
    schema_version: NonEmptyText
    source: NonEmptyText
    stream_id: UUID
    sequence: Annotated[StrictInt, Field(gt=0)]
    observed_at: datetime
    ingested_at: datetime
    produced_at: datetime
    effective_at: datetime
    expires_at: datetime
    correlation_id: UUID
    causation_id: UUID
    trace_id: UUID
    payload: PayloadT

    @field_validator("observed_at", "ingested_at", "produced_at", "effective_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _valid_timeline(self) -> "EventEnvelope[PayloadT]":
        if self.observed_at > self.ingested_at:
            raise ValueError("observed_at must not be after ingested_at")
        if self.ingested_at > self.produced_at:
            raise ValueError("ingested_at must not be after produced_at")
        if self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        generic_args = self.__class__.__pydantic_generic_metadata__.get("args", ())
        payload_type: object = generic_args[0] if generic_args and generic_args[0] is not object else type(self.payload)
        expected_event_type = _registered_event_type(payload_type)
        if self.event_type != expected_event_type:
            raise ValueError(
                f"event_type must be {expected_event_type} for {expected_event_type} payload"
            )
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: Any
    ) -> dict[str, Any]:
        schema = handler(core_schema)
        generic_args = cls.__pydantic_generic_metadata__.get("args", ())
        if generic_args and generic_args[0] in EVENT_TYPE_BY_PAYLOAD:
            expected_event_type = EVENT_TYPE_BY_PAYLOAD[generic_args[0]]
            schema["properties"]["event_type"] = {
                "const": expected_event_type,
                "type": "string",
            }
        return schema


def validate_event_batch(events: Iterable[EventEnvelope[object]]) -> tuple[EventEnvelope[object], ...]:
    """Validate deterministic input order for duplicate IDs and per-stream sequence."""

    batch = tuple(events)
    event_ids: set[UUID] = set()
    last_sequence: dict[UUID, int] = {}
    for event in batch:
        if event.event_id in event_ids:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        event_ids.add(event.event_id)
        previous = last_sequence.get(event.stream_id)
        if previous is not None and event.sequence <= previous:
            raise ValueError(
                f"non-increasing sequence for stream_id {event.stream_id}: "
                f"{event.sequence} after {previous}"
            )
        last_sequence[event.stream_id] = event.sequence
    return batch
