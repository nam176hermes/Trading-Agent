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
from packages.runtime_risk import canonical_model_digest


class SandboxExecutionError(RuntimeError):
    """A fail-closed deterministic execution sandbox error."""


class SandboxLostResponse(SandboxExecutionError):
    """The closed scenario applied a command but intentionally hid its response."""


class SandboxReconciliationError(SandboxExecutionError):
    """A fail-closed execution-evidence reconciliation boundary error."""


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


class SandboxReconciliationStatus(str, Enum):
    """Closed outcome of one deterministic execution-evidence comparison."""

    RECONCILED = "RECONCILED"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    MISMATCH = "MISMATCH"


class SandboxReconciliationReason(str, Enum):
    """Closed, descriptive evidence reasons with no repair authority."""

    UNKNOWN_ORDER_REPORT = "UNKNOWN_ORDER_REPORT"
    OBSERVED_ORDER_REPLAY_FAILED = "OBSERVED_ORDER_REPLAY_FAILED"
    OBSERVED_STATE_MISMATCH = "OBSERVED_STATE_MISMATCH"
    PENDING_ORDER_REPLAY_FAILED = "PENDING_ORDER_REPLAY_FAILED"
    VENUE_STATE_MISMATCH = "VENUE_STATE_MISMATCH"
    FILL_EVIDENCE_MISMATCH = "FILL_EVIDENCE_MISMATCH"
    UNEXPECTED_OBSERVED_REPORT = "UNEXPECTED_OBSERVED_REPORT"


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


class SandboxKnownReport(SandboxModel):
    report_id: UUID
    event: EventEnvelope[object]

    @model_validator(mode="after")
    def _canonical_event(self) -> "SandboxKnownReport":
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
    known_reports: tuple[SandboxKnownReport, ...] = ()
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
        known_reports = tuple(
            _canonical_model(report, SandboxKnownReport, "known_reports")
            for report in self.known_reports
        )
        queued_reports = tuple(
            _canonical_model(report, SandboxReportPlan, "queued_reports")
            for report in self.queued_reports
        )
        order_ids = tuple(order.order_id for order in orders)
        client_order_ids = tuple(order.client_order_id for order in orders)
        known_report_ids = tuple(report.report_id for report in known_reports)
        report_ids = tuple(report.report_id for report in queued_reports)
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("orders contain duplicate order_id")
        if len(client_order_ids) != len(set(client_order_ids)):
            raise ValueError("orders contain duplicate client_order_id")
        if len(known_report_ids) != len(set(known_report_ids)):
            raise ValueError("known_reports contain duplicate report_id")
        if len(report_ids) != len(set(report_ids)):
            raise ValueError("queued_reports contain duplicate report_id")
        if any(report_id not in known_report_ids for report_id in report_ids):
            raise ValueError("queued report_id must occur exactly once in known_reports")
        if any(report.event.payload.order_id not in order_ids for report in known_reports):
            raise ValueError("known report order_id must be present in snapshot orders")
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "known_reports", known_reports)
        object.__setattr__(self, "queued_reports", queued_reports)
        return self


def _canonical_reason_codes(
    values: tuple[SandboxReconciliationReason, ...], field_name: str
) -> tuple[SandboxReconciliationReason, ...]:
    """Require unique reason codes in their closed public enum order."""

    indices = tuple(list(SandboxReconciliationReason).index(value) for value in values)
    if len(indices) != len(set(indices)):
        raise ValueError(f"{field_name} must not contain duplicate reason codes")
    if indices != tuple(sorted(indices)):
        raise ValueError(f"{field_name} must follow enum order")
    return values


class SandboxReconciliationRequest(SandboxModel):
    """Immutable sandbox state plus independently observed execution evidence."""

    snapshot: SandboxSnapshot
    observed_reports: tuple[EventEnvelope[object], ...]

    @model_validator(mode="after")
    def _canonical_values(self) -> "SandboxReconciliationRequest":
        snapshot = _canonical_model(self.snapshot, SandboxSnapshot, "snapshot")
        reports: list[EventEnvelope[object]] = []
        report_bytes_by_id: dict[UUID, str] = {}
        for report in self.observed_reports:
            try:
                canonical = _canonical_report_event(report)
                canonical_bytes = serialize_event(canonical)
            except (ReplayError, TypeError, ValueError) as exc:
                raise ValueError("observed_reports must be canonical execution envelopes") from exc
            previous = report_bytes_by_id.get(canonical.event_id)
            if previous is not None and previous != canonical_bytes:
                raise ValueError("conflicting observed event")
            report_bytes_by_id[canonical.event_id] = canonical_bytes
            reports.append(canonical)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "observed_reports", tuple(reports))
        return self


class SandboxOrderReconciliation(SandboxModel):
    """Canonical reconciliation fact for one sandbox order."""

    order_id: UUID
    observed_state: OrderState
    expected_venue_state: OrderState
    observed_report_ids: tuple[UUID, ...]
    pending_report_ids: tuple[UUID, ...]
    reason_codes: tuple[SandboxReconciliationReason, ...] = ()

    @model_validator(mode="after")
    def _canonical_values(self) -> "SandboxOrderReconciliation":
        observed_state = _canonical_model(self.observed_state, OrderState, "observed_state")
        expected_venue_state = _canonical_model(
            self.expected_venue_state, OrderState, "expected_venue_state"
        )
        if (
            observed_state.order_id != self.order_id
            or expected_venue_state.order_id != self.order_id
        ):
            raise ValueError("order states must match reconciliation order_id")
        if len(self.observed_report_ids) != len(set(self.observed_report_ids)):
            raise ValueError("observed_report_ids must occur exactly once")
        if len(self.pending_report_ids) != len(set(self.pending_report_ids)):
            raise ValueError("pending_report_ids must occur exactly once")
        if set(self.observed_report_ids) & set(self.pending_report_ids):
            raise ValueError("report ids cannot be both observed and pending")
        object.__setattr__(self, "observed_state", observed_state)
        object.__setattr__(self, "expected_venue_state", expected_venue_state)
        object.__setattr__(
            self,
            "reason_codes",
            _canonical_reason_codes(self.reason_codes, "reason_codes"),
        )
        return self


class SandboxReconciliationResult(SandboxModel):
    """Immutable, digestible evidence outcome; it grants no execution authority."""

    status: SandboxReconciliationStatus
    snapshot_time: datetime
    orders: tuple[SandboxOrderReconciliation, ...]
    pending_report_ids: tuple[UUID, ...]
    unattributed_event_ids: tuple[UUID, ...]
    unattributed_reason_codes: tuple[SandboxReconciliationReason, ...]

    @field_validator("snapshot_time")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _canonical_values(self) -> "SandboxReconciliationResult":
        orders = tuple(
            _canonical_model(order, SandboxOrderReconciliation, "orders")
            for order in self.orders
        )
        sorted_orders = tuple(sorted(orders, key=lambda order: order.order_id.int))
        if len({order.order_id for order in sorted_orders}) != len(sorted_orders):
            raise ValueError("orders must contain unique order_id values")
        if len(self.pending_report_ids) != len(set(self.pending_report_ids)):
            raise ValueError("pending_report_ids must occur exactly once")
        if len(self.unattributed_event_ids) != len(set(self.unattributed_event_ids)):
            raise ValueError("unattributed_event_ids must occur exactly once")
        if self.unattributed_event_ids != tuple(
            sorted(self.unattributed_event_ids, key=lambda event_id: event_id.int)
        ):
            raise ValueError("unattributed_event_ids must be UUID ordered")
        unattributed_reasons = _canonical_reason_codes(
            self.unattributed_reason_codes, "unattributed_reason_codes"
        )
        has_findings = (
            any(order.reason_codes for order in sorted_orders)
            or bool(self.unattributed_event_ids)
            or bool(unattributed_reasons)
        )
        expected_status = (
            SandboxReconciliationStatus.MISMATCH
            if has_findings
            else (
                SandboxReconciliationStatus.DELIVERY_PENDING
                if self.pending_report_ids
                else SandboxReconciliationStatus.RECONCILED
            )
        )
        if self.status is not expected_status:
            raise ValueError("status does not match reconciliation findings and pending content")
        object.__setattr__(self, "orders", sorted_orders)
        object.__setattr__(self, "unattributed_reason_codes", unattributed_reasons)
        return self

    @property
    def digest(self) -> str:
        return canonical_model_digest(self)


__all__ = [
    "SandboxExecutionError",
    "SandboxLostResponse",
    "SandboxReconciliationError",
    "SandboxConnectionState",
    "SandboxCommandKind",
    "SandboxResponseDisposition",
    "SandboxReconciliationStatus",
    "SandboxReconciliationReason",
    "SandboxReportPlan",
    "SandboxKnownReport",
    "SandboxCommandPlan",
    "SandboxScenario",
    "SandboxSubmitRequest",
    "SandboxModifyRequest",
    "SandboxCancelRequest",
    "SandboxCommandResult",
    "SandboxOrderSnapshot",
    "SandboxSnapshot",
    "SandboxReconciliationRequest",
    "SandboxOrderReconciliation",
    "SandboxReconciliationResult",
]
