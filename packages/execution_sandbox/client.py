"""Pure deterministic lifecycle and delivery client for sandbox scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from packages.domain.clock import require_utc
from packages.domain.events import EventEnvelope
from packages.domain.orders import (
    TERMINAL_ORDER_STATUSES,
    OrderEvent,
    OrderIntent,
    OrderState,
    OrderStatus,
    reduce_order,
)
from packages.event_ledger import EventLedgerRepository, OutboxIntent, deserialize_event, serialize_event
from packages.event_ledger.models import AppendOutcome
from packages.runtime_risk import (
    GlobalHaltAuthorityError,
    GlobalSafetyAuthorityVerifier,
    SubmitPermitConsumptionError,
    consume_submit_permit,
)

from .models import (
    SandboxCancelRequest,
    SandboxCommandKind,
    SandboxCommandPlan,
    SandboxCommandResult,
    SandboxConnectionState,
    SandboxExecutionError,
    SandboxLostResponse,
    SandboxModifyRequest,
    SandboxOrderSnapshot,
    SandboxReportPlan,
    SandboxResponseDisposition,
    SandboxScenario,
    SandboxSnapshot,
    SandboxSubmitRequest,
)


_OUTBOX_TOPIC = "execution-sandbox.report"


@dataclass(frozen=True, slots=True)
class _QueuedReport:
    plan: SandboxReportPlan
    canonical_event: str
    insertion_ordinal: int


@dataclass(frozen=True, slots=True)
class _RetainedReport:
    report_id: UUID
    canonical_event: str


def _canonical_model(value: object, expected: type[Any], field_name: str) -> Any:
    """Rebuild a strict public model without trusting constructed instances."""

    if type(value) is not expected:
        raise SandboxExecutionError(f"invalid {field_name}")
    try:
        return expected(**{name: getattr(value, name) for name in expected.model_fields})
    except (AttributeError, TypeError, ValueError) as exc:
        raise SandboxExecutionError(f"invalid {field_name}") from exc


def _canonical_envelope(canonical_event: str) -> EventEnvelope[object]:
    try:
        return deserialize_event(canonical_event)
    except (TypeError, ValueError) as exc:
        raise SandboxExecutionError("invalid queued report") from exc


class SandboxExecutionClient:
    """An injected-clock, scripted execution lifecycle with no external effects."""

    def __init__(
        self,
        *,
        repository: EventLedgerRepository,
        safety_verifier: GlobalSafetyAuthorityVerifier,
        scenario: SandboxScenario,
        initial_time: datetime,
    ) -> None:
        if repository is None or safety_verifier is None:
            raise SandboxExecutionError("sandbox dependencies are required")
        self._repository = repository
        self._safety_verifier = safety_verifier
        self._scenario = _canonical_model(scenario, SandboxScenario, "scenario")
        try:
            current_time = require_utc(initial_time)
        except (TypeError, ValueError) as exc:
            raise SandboxExecutionError("initial_time must be UTC") from exc
        self._snapshot = SandboxSnapshot(
            connection_state=SandboxConnectionState.CONNECTED,
            current_time=current_time,
        )
        self._queued_reports: tuple[_QueuedReport, ...] = ()
        self._retained_reports: tuple[_RetainedReport, ...] = ()
        self._executed_command_ids: tuple[UUID, ...] = ()

    def snapshot(self) -> SandboxSnapshot:
        """Return a freshly canonicalized immutable inspection snapshot."""

        return _canonical_model(self._snapshot, SandboxSnapshot, "snapshot")

    def submit(self, request: SandboxSubmitRequest) -> SandboxCommandResult:
        request = self._canonical_submit_request(request)
        plan = self._require_plan(request.command_id, SandboxCommandKind.SUBMIT, request.order_id)
        self._require_connected_and_unused_client_order_id(
            request.order_intent.client_order_id, request.order_id
        )
        self._validate_all_planned_reports_before_effect(plan, request)
        try:
            authority = consume_submit_permit(
                repository=self._repository,
                permit=request.permit,
                current_observation=request.current_observation,
                current_policy=request.current_policy,
                current_safety=request.current_safety,
                safety_verifier=self._safety_verifier,
                consumed_event_id=request.consumed_event_id,
                consumed_at=request.submitted_at,
            )
        except (GlobalHaltAuthorityError, SubmitPermitConsumptionError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise SandboxExecutionError("submit authority consumption failed") from exc
        initial_order = SandboxOrderSnapshot(
            order_id=request.order_id,
            client_order_id=request.order_intent.client_order_id,
            order_intent=request.order_intent,
            venue_state=OrderState(order_id=request.order_id),
            observed_state=OrderState(order_id=request.order_id),
        )
        next_order, next_queue, retained = self._apply_venue_reports_and_enqueue(plan, initial_order)
        self._replace_state(
            orders=self._snapshot.orders + (next_order,),
            queued=next_queue,
            retained=retained,
            executed=self._executed_command_ids + (plan.command_id,),
        )
        if plan.response_disposition is SandboxResponseDisposition.LOST_RESPONSE:
            raise SandboxLostResponse("sandbox response was intentionally lost")
        return SandboxCommandResult(
            command_id=request.command_id,
            response=plan.response_disposition,
            consumed_authority=authority,
        )

    def modify(self, request: SandboxModifyRequest) -> SandboxCommandResult:
        request = _canonical_model(request, SandboxModifyRequest, "modify request")
        self._require_connected()
        existing = self._order_for(request.order_id)
        self._require_nonterminal(existing)
        if request.replacement_order_intent.intent_id != request.order_id:
            raise SandboxExecutionError("replacement order identity is invalid")
        if request.replacement_order_intent.client_order_id != existing.client_order_id:
            raise SandboxExecutionError("replacement client order identity is invalid")
        plan = self._require_plan(request.command_id, SandboxCommandKind.MODIFY, request.order_id)
        next_order, next_queue, retained = self._apply_venue_reports_and_enqueue(
            plan,
            existing,
            replacement_order_intent=request.replacement_order_intent,
        )
        self._replace_state(
            orders=self._replace_order(next_order),
            queued=next_queue,
            retained=retained,
            executed=self._executed_command_ids + (plan.command_id,),
        )
        return self._result_or_lost(plan)

    def cancel(self, request: SandboxCancelRequest) -> SandboxCommandResult:
        request = _canonical_model(request, SandboxCancelRequest, "cancel request")
        self._require_connected()
        existing = self._order_for(request.order_id)
        self._require_nonterminal(existing)
        plan = self._require_plan(request.command_id, SandboxCommandKind.CANCEL, request.order_id)
        next_order, next_queue, retained = self._apply_venue_reports_and_enqueue(plan, existing)
        self._replace_state(
            orders=self._replace_order(next_order),
            queued=next_queue,
            retained=retained,
            executed=self._executed_command_ids + (plan.command_id,),
        )
        return self._result_or_lost(plan)

    def disconnect(self, *, command_id: UUID, at: datetime) -> SandboxCommandResult:
        return self._connection_transition(command_id, at, SandboxCommandKind.DISCONNECT)

    def reconnect(self, *, command_id: UUID, at: datetime) -> SandboxCommandResult:
        return self._connection_transition(command_id, at, SandboxCommandKind.RECONNECT)

    def advance_time(self, *, to: datetime) -> SandboxSnapshot:
        try:
            target = require_utc(to)
        except (TypeError, ValueError) as exc:
            raise SandboxExecutionError("clock time must be UTC") from exc
        if target < self._snapshot.current_time:
            raise SandboxExecutionError("clock cannot move backwards")
        self._replace_state(current_time=target)
        return self.snapshot()

    def drain_reports(self) -> tuple[EventEnvelope[object], ...]:
        self._require_connected()
        due = tuple(
            sorted(
                (item for item in self._queued_reports if item.plan.deliver_at <= self._snapshot.current_time),
                key=lambda item: (item.plan.deliver_at, item.insertion_ordinal),
            )
        )
        if not due:
            return ()

        next_orders = self._snapshot.orders
        delivered: list[EventEnvelope[object]] = []
        for item in due:
            event = _canonical_envelope(item.canonical_event)
            order = self._order_from(next_orders, event.payload.order_id)
            self._append_and_read_back(event)
            if type(event.payload) is OrderEvent:
                order = SandboxOrderSnapshot(
                    order_id=order.order_id,
                    client_order_id=order.client_order_id,
                    order_intent=order.order_intent,
                    venue_state=order.venue_state,
                    observed_state=self._reduce(order.observed_state, event.payload),
                )
                next_orders = self._replace_order_in(next_orders, order)
            delivered.append(event)

        delivered_ordinals = {item.insertion_ordinal for item in due}
        self._replace_state(
            orders=next_orders,
            queued=tuple(item for item in self._queued_reports if item.insertion_ordinal not in delivered_ordinals),
        )
        return tuple(delivered)

    def _connection_transition(
        self, command_id: UUID, at: datetime, kind: SandboxCommandKind
    ) -> SandboxCommandResult:
        if type(command_id) is not UUID:
            raise SandboxExecutionError("command_id must be a UUID")
        try:
            require_utc(at)
        except (TypeError, ValueError) as exc:
            raise SandboxExecutionError("connection time must be UTC") from exc
        wanted = SandboxConnectionState.CONNECTED if kind is SandboxCommandKind.DISCONNECT else SandboxConnectionState.DISCONNECTED
        if self._snapshot.connection_state is not wanted:
            raise SandboxExecutionError("invalid sandbox connection transition")
        plan = self._require_plan(command_id, kind, None)
        next_state = SandboxConnectionState.DISCONNECTED if kind is SandboxCommandKind.DISCONNECT else SandboxConnectionState.CONNECTED
        self._replace_state(
            connection_state=next_state,
            executed=self._executed_command_ids + (plan.command_id,),
        )
        return self._result_or_lost(plan)

    def _canonical_submit_request(self, request: SandboxSubmitRequest) -> SandboxSubmitRequest:
        return _canonical_model(request, SandboxSubmitRequest, "submit request")

    def _require_plan(
        self, command_id: UUID, kind: SandboxCommandKind, order_id: UUID | None
    ) -> SandboxCommandPlan:
        if type(command_id) is not UUID:
            raise SandboxExecutionError("command_id must be a UUID")
        plan = next((item for item in self._scenario.command_plans if item.command_id == command_id), None)
        if plan is None or plan.kind is not kind:
            raise SandboxExecutionError("command is not permitted by scenario")
        if order_id is not None and plan.order_id != order_id:
            raise SandboxExecutionError("command order_id does not match scenario")
        if plan.command_id in self._executed_command_ids:
            raise SandboxExecutionError("command has already executed")
        return _canonical_model(plan, SandboxCommandPlan, "command plan")

    def _validate_all_planned_reports_before_effect(
        self, plan: SandboxCommandPlan, request: SandboxSubmitRequest
    ) -> None:
        reports, _ = self._planned_reports(plan, request.order_id)
        state = OrderState(order_id=request.order_id)
        for _, canonical_event in reports:
            event = _canonical_envelope(canonical_event)
            if type(event.payload) is OrderEvent:
                state = self._reduce(state, event.payload)

    def _apply_venue_reports_and_enqueue(
        self,
        plan: SandboxCommandPlan,
        order: SandboxOrderSnapshot,
        *,
        replacement_order_intent: OrderIntent | None = None,
    ) -> tuple[SandboxOrderSnapshot, tuple[_QueuedReport, ...], tuple[_RetainedReport, ...]]:
        planned_reports, retained = self._planned_reports(plan, order.order_id)
        next_order = order
        queued = self._queued_reports
        next_ordinal = max((item.insertion_ordinal for item in queued), default=-1) + 1
        replacement_ready = False
        for report, canonical_event in planned_reports:
            event = _canonical_envelope(canonical_event)
            if type(event.payload) is OrderEvent:
                previous_status = next_order.venue_state.status
                next_order = SandboxOrderSnapshot(
                    order_id=next_order.order_id,
                    client_order_id=next_order.client_order_id,
                    order_intent=next_order.order_intent,
                    venue_state=self._reduce(next_order.venue_state, event.payload),
                    observed_state=next_order.observed_state,
                )
                if (
                    replacement_order_intent is not None
                    and previous_status is OrderStatus.ACCEPTED
                    and event.payload.target_status is OrderStatus.PENDING_UPDATE
                ):
                    replacement_ready = True
                elif (
                    replacement_ready
                    and event.payload.target_status is OrderStatus.ACCEPTED
                    and next_order.venue_state.status is OrderStatus.ACCEPTED
                ):
                    next_order = SandboxOrderSnapshot(
                        order_id=next_order.order_id,
                        client_order_id=next_order.client_order_id,
                        order_intent=replacement_order_intent,
                        venue_state=next_order.venue_state,
                        observed_state=next_order.observed_state,
                    )
                    replacement_ready = False
            queued = queued + (_QueuedReport(report, canonical_event, next_ordinal),)
            next_ordinal += 1
        return next_order, queued, retained

    def _planned_reports(
        self, plan: SandboxCommandPlan, order_id: UUID
    ) -> tuple[tuple[tuple[SandboxReportPlan, str], ...], tuple[_RetainedReport, ...]]:
        retained = self._retained_reports
        planned: list[tuple[SandboxReportPlan, str]] = []
        for report_id in plan.report_ids:
            report = self._report_plan_for(report_id)
            if report.event is not None:
                try:
                    canonical_event = serialize_event(report.event)
                except (TypeError, ValueError) as exc:
                    raise SandboxExecutionError("invalid scenario report") from exc
                retained = retained + (
                    _RetainedReport(report_id=report.report_id, canonical_event=canonical_event),
                )
            else:
                canonical_event = next(
                    (
                        item.canonical_event
                        for item in retained
                        if item.report_id == report.duplicate_of_report_id
                    ),
                    None,
                )
                if canonical_event is None:
                    raise SandboxExecutionError("duplicate report original is unavailable")
            event = _canonical_envelope(canonical_event)
            if event.payload.order_id != order_id:
                raise SandboxExecutionError("report order_id is unknown")
            planned.append((report, canonical_event))
        return tuple(planned), retained

    def _report_plan_for(self, report_id: UUID) -> SandboxReportPlan:
        report = next((item for item in self._scenario.report_plans if item.report_id == report_id), None)
        if report is None:
            raise SandboxExecutionError("scenario report is unavailable")
        return _canonical_model(report, SandboxReportPlan, "report plan")

    def _append_and_read_back(self, event: EventEnvelope[object]) -> None:
        try:
            canonical_event = serialize_event(event)
            outbox = OutboxIntent(
                event_id=event.event_id,
                topic=_OUTBOX_TOPIC,
                payload_json=canonical_event,
            )
            outcome = self._repository.append(event, outbox)
            if type(outcome) is not AppendOutcome or outcome.event_id != event.event_id or type(outcome.inserted) is not bool:
                raise SandboxExecutionError("ledger append outcome is invalid")
            read_back = tuple(
                candidate
                for candidate in self._repository.load_events()
                if candidate.event_id == event.event_id
            )
            if len(read_back) != 1 or serialize_event(read_back[0]) != canonical_event:
                raise SandboxExecutionError("ledger read-back is not exact")
        except SandboxExecutionError:
            raise
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            raise SandboxExecutionError("ledger delivery failed") from exc

    def _replace_state(
        self,
        *,
        connection_state: SandboxConnectionState | None = None,
        current_time: datetime | None = None,
        orders: tuple[SandboxOrderSnapshot, ...] | None = None,
        queued: tuple[_QueuedReport, ...] | None = None,
        retained: tuple[_RetainedReport, ...] | None = None,
        executed: tuple[UUID, ...] | None = None,
    ) -> None:
        next_queue = self._queued_reports if queued is None else queued
        self._snapshot = SandboxSnapshot(
            connection_state=self._snapshot.connection_state if connection_state is None else connection_state,
            current_time=self._snapshot.current_time if current_time is None else current_time,
            orders=self._snapshot.orders if orders is None else orders,
            queued_reports=tuple(item.plan for item in next_queue),
        )
        self._queued_reports = next_queue
        self._retained_reports = self._retained_reports if retained is None else retained
        self._executed_command_ids = self._executed_command_ids if executed is None else executed

    def _order_for(self, order_id: UUID) -> SandboxOrderSnapshot:
        if type(order_id) is not UUID:
            raise SandboxExecutionError("order_id must be a UUID")
        return self._order_from(self._snapshot.orders, order_id)

    def _require_connected_and_unused_client_order_id(
        self, client_order_id: str, order_id: UUID
    ) -> None:
        self._require_connected()
        if any(order.order_id == order_id for order in self._snapshot.orders):
            raise SandboxExecutionError("order_id already exists")
        if any(order.client_order_id == client_order_id for order in self._snapshot.orders):
            raise SandboxExecutionError("client_order_id already exists")

    @staticmethod
    def _require_nonterminal(order: SandboxOrderSnapshot) -> None:
        if order.venue_state.status in TERMINAL_ORDER_STATUSES:
            raise SandboxExecutionError("order is terminal")

    @staticmethod
    def _order_from(orders: tuple[SandboxOrderSnapshot, ...], order_id: UUID) -> SandboxOrderSnapshot:
        order = next((item for item in orders if item.order_id == order_id), None)
        if order is None:
            raise SandboxExecutionError("order_id is unknown")
        return order

    def _replace_order(self, replacement: SandboxOrderSnapshot) -> tuple[SandboxOrderSnapshot, ...]:
        return self._replace_order_in(self._snapshot.orders, replacement)

    @staticmethod
    def _replace_order_in(
        orders: tuple[SandboxOrderSnapshot, ...], replacement: SandboxOrderSnapshot
    ) -> tuple[SandboxOrderSnapshot, ...]:
        return tuple(replacement if item.order_id == replacement.order_id else item for item in orders)

    @staticmethod
    def _reduce(state: OrderState, event: OrderEvent) -> OrderState:
        try:
            return reduce_order(state, event)
        except (TypeError, ValueError) as exc:
            raise SandboxExecutionError("invalid order lifecycle transition") from exc

    def _require_connected(self) -> None:
        if self._snapshot.connection_state is not SandboxConnectionState.CONNECTED:
            raise SandboxExecutionError("sandbox is disconnected")

    @staticmethod
    def _result_or_lost(plan: SandboxCommandPlan) -> SandboxCommandResult:
        if plan.response_disposition is SandboxResponseDisposition.LOST_RESPONSE:
            raise SandboxLostResponse("scripted sandbox response was lost")
        return SandboxCommandResult(command_id=plan.command_id, response=plan.response_disposition)


__all__ = ["SandboxExecutionClient"]
