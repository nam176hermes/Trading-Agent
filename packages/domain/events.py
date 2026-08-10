"""Immutable, replay-safe canonical event envelope."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Iterable, Self, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from .clock import require_utc
from .orders import FillEvent, OrderEvent, OrderIntent, validate_fill_report_batch
from .portfolio import TargetPortfolio
from .portfolio_events import (
    PortfolioConversionEntry,
    PortfolioFillEntry,
    PortfolioFundingEntry,
    PortfolioMarkEntry,
    PortfolioOpeningEntry,
    PortfolioReconciliationEntry,
    PortfolioValuationRateEntry,
)
from .risk import RiskDecision
from .runtime_halt import (
    GlobalHaltTransition,
    SubmitPermitConsumed,
    SubmitPermitPrepared,
)
from .runtime_risk import RuntimeOrderRiskDecision
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
    PortfolioOpeningEntry: "PortfolioOpeningEntry",
    PortfolioFillEntry: "PortfolioFillEntry",
    PortfolioMarkEntry: "PortfolioMarkEntry",
    PortfolioFundingEntry: "PortfolioFundingEntry",
    PortfolioConversionEntry: "PortfolioConversionEntry",
    PortfolioValuationRateEntry: "PortfolioValuationRateEntry",
    PortfolioReconciliationEntry: "PortfolioReconciliationEntry",
    RuntimeOrderRiskDecision: "RuntimeOrderRiskDecision",
    GlobalHaltTransition: "GlobalHaltTransition",
    SubmitPermitPrepared: "SubmitPermitPrepared",
    SubmitPermitConsumed: "SubmitPermitConsumed",
}


def _registered_event_type(payload_type: object) -> str:
    if not isinstance(payload_type, type) or payload_type not in EVENT_TYPE_BY_PAYLOAD:
        raise ValueError("payload type is not registered for an event envelope")
    return EVENT_TYPE_BY_PAYLOAD[payload_type]


def _registered_payload_type(event_type: object) -> type[object]:
    if not isinstance(event_type, str):
        raise ValueError("event_type must be a registered string")
    for payload_type, registered_type in EVENT_TYPE_BY_PAYLOAD.items():
        if registered_type == event_type:
            return payload_type
    raise ValueError(f"event_type is not registered: {event_type!r}")


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
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Rebuild model-copy and model-construct envelopes before validation."""

        if isinstance(obj, EventEnvelope):
            try:
                obj = {name: getattr(obj, name) for name in EventEnvelope.model_fields}
            except AttributeError as exc:
                raise ValidationError.from_exception_data(
                    cls.__name__,
                    [{"type": "missing", "loc": ("event_envelope",), "input": obj}],
                ) from exc
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    def _canonical_public_instance(self) -> "EventEnvelope[object]":
        return _canonical_event_envelope(self)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Serialize only a complete, registered, freshly validated envelope."""

        canonical = self._canonical_public_instance()
        return BaseModel.model_dump(canonical, *args, **kwargs)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        """Serialize only a complete, registered, freshly validated envelope."""

        canonical = self._canonical_public_instance()
        return BaseModel.model_dump_json(canonical, *args, **kwargs)

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


def _concrete_payload_type(event: EventEnvelope[object]) -> type[object]:
    generic_args = event.__class__.__pydantic_generic_metadata__.get("args", ())
    declared = generic_args[0] if generic_args else object
    if declared in EVENT_TYPE_BY_PAYLOAD:
        return declared
    return _registered_payload_type(event.event_type)


def _canonical_event_envelope(event: object) -> EventEnvelope[object]:
    """Rebuild against the registered concrete payload model before egress."""

    if not isinstance(event, EventEnvelope):
        raise ValueError("event must be an EventEnvelope")
    try:
        payload_type = _concrete_payload_type(event)
        fields = {
            name: getattr(event, name)
            for name in EventEnvelope.model_fields
        }
        return EventEnvelope[payload_type].model_validate(fields)
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("invalid event envelope") from exc


def validate_execution_report_events(
    events: Iterable[EventEnvelope[object]],
) -> tuple[EventEnvelope[object], ...]:
    """Validate execution-report identity and ordering without reducing fills."""

    batch = tuple(events)
    reports: list[FillEvent] = []
    seen_event_ids: set[UUID] = set()
    for event in batch:
        if not isinstance(event, EventEnvelope):
            continue
        if event.event_type != "FillEvent" and type(event.payload) is not FillEvent:
            continue
        canonical = _canonical_event_envelope(event)
        if canonical.event_id in seen_event_ids:
            # The ledger resolves same-ID duplicate/conflict semantics after
            # this pure report-collection validation.  Do not turn an exact
            # delivery retry into a false duplicate execution identity.
            continue
        seen_event_ids.add(canonical.event_id)
        if type(canonical.payload) is not FillEvent:
            raise ValueError("FillEvent envelope must carry a concrete FillEvent")
        reports.append(canonical.payload)
    validate_fill_report_batch(reports)
    return batch


def validate_event_batch(events: Iterable[EventEnvelope[object]]) -> tuple[EventEnvelope[object], ...]:
    """Validate deterministic input order for duplicate IDs and per-stream sequence."""

    batch = tuple(_canonical_event_envelope(event) for event in events)
    validate_execution_report_events(batch)
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
