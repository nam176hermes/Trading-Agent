"""Canonical ingress helpers for deterministic execution reconciliation."""

from __future__ import annotations

from uuid import UUID

from packages.domain.events import EventEnvelope
from packages.domain.orders import (
    FillEvent,
    OrderEvent,
    OrderReductionError,
    OrderState,
    reduce_order,
)
from packages.event_ledger.replay import deserialize_event, serialize_event

from .models import (
    SandboxOrderReconciliation,
    SandboxReconciliationError,
    SandboxReconciliationReason,
    SandboxReconciliationRequest,
    SandboxReconciliationResult,
    SandboxReconciliationStatus,
    SandboxReportPlan,
    SandboxSnapshot,
)


def _canonical_envelope(value: object) -> EventEnvelope[object]:
    if not isinstance(value, EventEnvelope):
        raise SandboxReconciliationError("invalid observed envelope")
    canonical = deserialize_event(serialize_event(value))
    if type(canonical.payload) not in (OrderEvent, FillEvent):
        raise SandboxReconciliationError("invalid observed envelope")
    return canonical


def _canonical_request(value: object) -> SandboxReconciliationRequest:
    if type(value) is not SandboxReconciliationRequest:
        raise SandboxReconciliationError("invalid reconciliation request")
    return SandboxReconciliationRequest(
        snapshot=value.snapshot,
        observed_reports=value.observed_reports,
    )


def _observed_by_event_id(
    reports: tuple[EventEnvelope[object], ...],
) -> dict[UUID, EventEnvelope[object]]:
    observed: dict[UUID, EventEnvelope[object]] = {}
    for report in reports:
        canonical = _canonical_envelope(report)
        prior = observed.get(canonical.event_id)
        if prior is not None and serialize_event(prior) != serialize_event(canonical):
            raise SandboxReconciliationError("conflicting observed event")
        observed[canonical.event_id] = canonical
    return observed


def _known_reports(snapshot: SandboxSnapshot) -> dict[UUID, EventEnvelope[object]]:
    return {report.report_id: report.event for report in snapshot.known_reports}


def _queued_reports(
    snapshot: SandboxSnapshot,
) -> tuple[tuple[SandboxReportPlan, EventEnvelope[object]], ...]:
    known = _known_reports(snapshot)
    try:
        pairs = tuple((plan, known[plan.report_id]) for plan in snapshot.queued_reports)
    except KeyError as exc:
        raise SandboxReconciliationError("queued report is absent from known inventory") from exc
    return tuple(
        pair
        for _, pair in sorted(
            enumerate(pairs), key=lambda item: (item[1][0].deliver_at, item[0])
        )
    )


def _delivered_event_ids(snapshot: SandboxSnapshot) -> frozenset[UUID]:
    queued_ids = {plan.report_id for plan in snapshot.queued_reports}
    return frozenset(
        report.event.event_id
        for report in snapshot.known_reports
        if report.report_id not in queued_ids
    )


def _replay_order_events(
    order_id: UUID,
    events: tuple[EventEnvelope[object], ...],
) -> tuple[OrderState, tuple[SandboxReconciliationReason, ...]]:
    state = OrderState(order_id=order_id)
    try:
        for event in sorted(events, key=lambda item: item.payload.sequence):
            state = reduce_order(state, event.payload)
    except OrderReductionError:
        return state, (SandboxReconciliationReason.OBSERVED_ORDER_REPLAY_FAILED,)
    return state, ()


def _ordered_reasons(
    reasons: list[SandboxReconciliationReason],
) -> tuple[SandboxReconciliationReason, ...]:
    return tuple(reason for reason in SandboxReconciliationReason if reason in reasons)


def _reconcile_canonical_request(
    request: SandboxReconciliationRequest,
) -> SandboxReconciliationResult:
    snapshot = request.snapshot
    known = _known_reports(snapshot)
    queued = _queued_reports(snapshot)
    queued_ids = frozenset(plan.report_id for plan, _ in queued)
    delivered_event_ids = _delivered_event_ids(snapshot)
    observed = _observed_by_event_id(request.observed_reports)
    order_ids = frozenset(order.order_id for order in snapshot.orders)

    unattributed_event_ids: list[UUID] = []
    unattributed_reasons: list[SandboxReconciliationReason] = []
    usable_observed: dict[UUID, EventEnvelope[object]] = {}
    for event_id, event in sorted(observed.items(), key=lambda item: item[0].int):
        if event.payload.order_id not in order_ids:
            unattributed_event_ids.append(event_id)
            unattributed_reasons.append(SandboxReconciliationReason.UNKNOWN_ORDER_REPORT)
        elif event_id not in delivered_event_ids:
            unattributed_event_ids.append(event_id)
            unattributed_reasons.append(
                SandboxReconciliationReason.UNEXPECTED_OBSERVED_REPORT
            )
        else:
            usable_observed[event_id] = event

    orders: list[SandboxOrderReconciliation] = []
    for order in snapshot.orders:
        order_observed = tuple(
            event
            for event in usable_observed.values()
            if event.payload.order_id == order.order_id and type(event.payload) is OrderEvent
        )
        observed_state, observed_reasons = _replay_order_events(
            order.order_id, order_observed
        )
        reasons = list(observed_reasons)
        if not observed_reasons and observed_state != order.observed_state:
            reasons.append(SandboxReconciliationReason.OBSERVED_STATE_MISMATCH)

        pending_events = tuple(
            event
            for _, event in queued
            if event.payload.order_id == order.order_id and type(event.payload) is OrderEvent
        )
        expected_venue_state = observed_state
        try:
            for event in pending_events:
                expected_venue_state = reduce_order(expected_venue_state, event.payload)
        except OrderReductionError:
            reasons.append(SandboxReconciliationReason.PENDING_ORDER_REPLAY_FAILED)
        else:
            if expected_venue_state != order.venue_state:
                reasons.append(SandboxReconciliationReason.VENUE_STATE_MISMATCH)

        observed_report_ids = tuple(
            report_id
            for report_id, event in known.items()
            if report_id not in queued_ids
            and event.payload.order_id == order.order_id
            and event.event_id in usable_observed
        )
        pending_report_ids = tuple(
            plan.report_id
            for plan, event in queued
            if event.payload.order_id == order.order_id
        )
        orders.append(
            SandboxOrderReconciliation(
                order_id=order.order_id,
                observed_state=observed_state,
                expected_venue_state=expected_venue_state,
                observed_report_ids=observed_report_ids,
                pending_report_ids=pending_report_ids,
                reason_codes=_ordered_reasons(reasons),
            )
        )

    pending_report_ids = tuple(plan.report_id for plan, _ in queued)
    has_findings = any(order.reason_codes for order in orders) or bool(
        unattributed_event_ids
    )
    status = (
        SandboxReconciliationStatus.MISMATCH
        if has_findings
        else (
            SandboxReconciliationStatus.DELIVERY_PENDING
            if pending_report_ids
            else SandboxReconciliationStatus.RECONCILED
        )
    )
    return SandboxReconciliationResult(
        status=status,
        snapshot_time=snapshot.current_time,
        orders=tuple(orders),
        pending_report_ids=pending_report_ids,
        unattributed_event_ids=tuple(unattributed_event_ids),
        unattributed_reason_codes=_ordered_reasons(unattributed_reasons),
    )


def reconcile_execution_state(
    request: SandboxReconciliationRequest,
) -> SandboxReconciliationResult:
    """Purely reconcile retained sandbox reports with caller-supplied evidence."""

    canonical_request = _canonical_request(request)
    return _reconcile_canonical_request(canonical_request)
