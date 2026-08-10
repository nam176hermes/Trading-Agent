"""Strict, in-memory scenario contracts for deterministic execution tests."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from packages.domain.clock import require_utc
from packages.domain.events import EventEnvelope
from packages.domain.orders import OrderEvent, OrderIntent, OrderState
from packages.domain.orders import FillEvent
from packages.domain.runtime_halt import (
    ConsumedSubmitAuthority,
    GlobalSafetyObservation,
    PreparedSubmitPermit,
)
from packages.domain.runtime_risk import RuntimeRiskObservation, RuntimeRiskPolicy
from packages.event_ledger.replay import ReplayError, deserialize_event, serialize_event


class SandboxExecutionError(RuntimeError):
    """A fail-closed deterministic execution sandbox error."""


class SandboxLostResponse(SandboxExecutionError):
    """The closed scenario applied a command but intentionally hid its response."""


class SandboxModel(BaseModel):
    """Every public sandbox value is strict, immutable, and freshly revalidated."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class SandboxConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class SandboxCommandKind(str, Enum):
    SUBMIT = "SUBMIT"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    DISCONNECT = "DISCONNECT"
    RECONNECT = "RECONNECT"


class SandboxResponseDisposition(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    LOST_RESPONSE = "LOST_RESPONSE"


ModelT = TypeVar("ModelT", bound=BaseModel)


def _canonical_model(value: object, expected: type[ModelT], field_name: str) -> ModelT:
    """Rebuild a nested Pydantic value to reject constructed/copy-forged input."""

    if type(value) is not expected:
        raise ValueError(f"{field_name} must be a concrete {expected.__name__}")
    try:
        values = {name: getattr(value, name) for name in expected.model_fields}
        return expected.model_validate(values)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not canonical") from exc


def _canonical_report_event(event: object) -> EventEnvelope[object]:
    """Round-trip one permitted report through the existing canonical ledger codec."""

    if not isinstance(event, EventEnvelope):
        raise ValueError("event must be an EventEnvelope")
    try:
        canonical = deserialize_event(serialize_event(event))
    except (ReplayError, TypeError, ValueError) as exc:
        raise ValueError("event is not a canonical execution report envelope") from exc
    if type(canonical.payload) not in (OrderEvent, FillEvent):
        raise ValueError("event payload must be a concrete OrderEvent or FillEvent")
    return canonical


class SandboxReportPlan(SandboxModel):
    report_id: UUID
    deliver_at: datetime
    event: EventEnvelope[object] | None = None
    duplicate_of_report_id: UUID | None = None

    @field_validator("deliver_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_original_or_duplicate(self) -> "SandboxReportPlan":
        original = self.event is not None
        duplicate = self.duplicate_of_report_id is not None
        if original == duplicate:
            raise ValueError("report plan requires exactly one original event or duplicate reference")
        if original:
            object.__setattr__(self, "event", _canonical_report_event(self.event))
        return self


class SandboxCommandPlan(SandboxModel):
    command_id: UUID
    kind: SandboxCommandKind
    response_disposition: SandboxResponseDisposition
    order_id: UUID
    report_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def _unique_report_ids(self) -> "SandboxCommandPlan":
        if len(self.report_ids) != len(set(self.report_ids)):
            raise ValueError("command report_ids must occur exactly once")
        connection_command = self.kind in (
            SandboxCommandKind.DISCONNECT,
            SandboxCommandKind.RECONNECT,
        )
        if connection_command and self.report_ids:
            raise ValueError("connection command report_ids must be empty")
        if not connection_command and not self.report_ids:
            raise ValueError("lifecycle command report_ids must not be empty")
        return self


class SandboxScenario(SandboxModel):
    command_plans: tuple[SandboxCommandPlan, ...]
    report_plans: tuple[SandboxReportPlan, ...]

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "SandboxScenario":
        commands = tuple(
            _canonical_model(plan, SandboxCommandPlan, "command_plans")
            for plan in self.command_plans
        )
        reports = tuple(
            _canonical_model(plan, SandboxReportPlan, "report_plans")
            for plan in self.report_plans
        )
        command_ids: set[UUID] = set()
        for command in commands:
            if command.command_id in command_ids:
                raise ValueError("duplicate command_id")
            command_ids.add(command.command_id)

        reports_by_id: dict[UUID, SandboxReportPlan] = {}
        originals_by_id: dict[UUID, SandboxReportPlan] = {}
        report_order_ids: dict[UUID, UUID] = {}
        for report in reports:
            if report.report_id in reports_by_id:
                raise ValueError("duplicate report_id")
            reports_by_id[report.report_id] = report
            if report.event is not None:
                payload = report.event.payload
                if type(payload) not in (OrderEvent, FillEvent):
                    raise ValueError("report event payload must be concrete")
                originals_by_id[report.report_id] = report
                report_order_ids[report.report_id] = payload.order_id
                continue
            original = originals_by_id.get(report.duplicate_of_report_id)
            if original is None:
                raise ValueError("duplicate report must reference one prior original report")
            payload = original.event.payload if original.event is not None else None
            if type(payload) not in (OrderEvent, FillEvent):
                raise ValueError("duplicate report original payload is invalid")
            report_order_ids[report.report_id] = payload.order_id

        assigned_report_ids: set[UUID] = set()
        for command in commands:
            for report_id in command.report_ids:
                if report_id not in reports_by_id:
                    raise ValueError("command report_id does not exist")
                if report_id in assigned_report_ids:
                    raise ValueError("command report_id must occur exactly once")
                assigned_report_ids.add(report_id)
                if report_order_ids[report_id] != command.order_id:
                    raise ValueError("report order_id must match command order_id")
        if assigned_report_ids != set(reports_by_id):
            raise ValueError("each report_id must occur exactly once in command plans")
        object.__setattr__(self, "command_plans", commands)
        object.__setattr__(self, "report_plans", reports)
        return self


class SandboxSubmitRequest(SandboxModel):
    command_id: UUID
    order_id: UUID
    order_intent: OrderIntent
    permit: PreparedSubmitPermit
    current_observation: RuntimeRiskObservation
    current_policy: RuntimeRiskPolicy
    current_safety: GlobalSafetyObservation
    consumed_event_id: UUID
    submitted_at: datetime

    @field_validator("submitted_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_nested_values(self) -> "SandboxSubmitRequest":
        object.__setattr__(self, "order_intent", _canonical_model(self.order_intent, OrderIntent, "order_intent"))
        object.__setattr__(self, "permit", _canonical_model(self.permit, PreparedSubmitPermit, "permit"))
        object.__setattr__(self, "current_observation", _canonical_model(self.current_observation, RuntimeRiskObservation, "current_observation"))
        object.__setattr__(self, "current_policy", _canonical_model(self.current_policy, RuntimeRiskPolicy, "current_policy"))
        object.__setattr__(self, "current_safety", _canonical_model(self.current_safety, GlobalSafetyObservation, "current_safety"))
        if self.order_id != self.order_intent.intent_id:
            raise ValueError("submit order_id must equal order_intent.intent_id")
        return self


class SandboxModifyRequest(SandboxModel):
    command_id: UUID
    order_id: UUID
    replacement_order_intent: OrderIntent
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_nested_values(self) -> "SandboxModifyRequest":
        object.__setattr__(
            self,
            "replacement_order_intent",
            _canonical_model(
                self.replacement_order_intent,
                OrderIntent,
                "replacement_order_intent",
            ),
        )
        return self


class SandboxCancelRequest(SandboxModel):
    command_id: UUID
    order_id: UUID
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class SandboxCommandResult(SandboxModel):
    command_id: UUID
    response: SandboxResponseDisposition
    consumed_authority: ConsumedSubmitAuthority | None = None

    @model_validator(mode="after")
    def _canonical_consumed_authority(self) -> "SandboxCommandResult":
        if self.consumed_authority is not None:
            object.__setattr__(
                self,
                "consumed_authority",
                _canonical_model(
                    self.consumed_authority,
                    ConsumedSubmitAuthority,
                    "consumed_authority",
                ),
            )
        return self


class SandboxOrderSnapshot(SandboxModel):
    order_id: UUID
    client_order_id: str
    order_intent: OrderIntent
    venue_state: OrderState
    observed_state: OrderState

    @model_validator(mode="after")
    def _canonical_order_state(self) -> "SandboxOrderSnapshot":
        intent = _canonical_model(self.order_intent, OrderIntent, "order_intent")
        venue_state = _canonical_model(self.venue_state, OrderState, "venue_state")
        observed_state = _canonical_model(self.observed_state, OrderState, "observed_state")
        if self.order_id != intent.intent_id:
            raise ValueError("order_id must match order_intent.intent_id")
        if self.client_order_id != intent.client_order_id:
            raise ValueError("client_order_id must match order_intent")
        if self.order_id != venue_state.order_id or self.order_id != observed_state.order_id:
            raise ValueError("order state order_id must match snapshot order_id")
        object.__setattr__(self, "order_intent", intent)
        object.__setattr__(self, "venue_state", venue_state)
        object.__setattr__(self, "observed_state", observed_state)
        return self


class SandboxSnapshot(SandboxModel):
    connection_state: SandboxConnectionState
    current_time: datetime
    orders: tuple[SandboxOrderSnapshot, ...] = ()
    queued_reports: tuple[SandboxReportPlan, ...] = ()

    @field_validator("current_time")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_snapshot_values(self) -> "SandboxSnapshot":
        orders = tuple(
            _canonical_model(order, SandboxOrderSnapshot, "orders")
            for order in self.orders
        )
        queued_reports = tuple(
            _canonical_model(report, SandboxReportPlan, "queued_reports")
            for report in self.queued_reports
        )
        order_ids = tuple(order.order_id for order in orders)
        client_order_ids = tuple(order.client_order_id for order in orders)
        report_ids = tuple(report.report_id for report in queued_reports)
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("orders contain duplicate order_id")
        if len(client_order_ids) != len(set(client_order_ids)):
            raise ValueError("orders contain duplicate client_order_id")
        if len(report_ids) != len(set(report_ids)):
            raise ValueError("queued_reports contain duplicate report_id")
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "queued_reports", queued_reports)
        return self


__all__ = [
    "SandboxExecutionError",
    "SandboxLostResponse",
    "SandboxConnectionState",
    "SandboxCommandKind",
    "SandboxResponseDisposition",
    "SandboxReportPlan",
    "SandboxCommandPlan",
    "SandboxScenario",
    "SandboxSubmitRequest",
    "SandboxModifyRequest",
    "SandboxCancelRequest",
    "SandboxCommandResult",
    "SandboxOrderSnapshot",
    "SandboxSnapshot",
]
