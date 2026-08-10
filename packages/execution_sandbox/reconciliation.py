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
    return frozenset(_delivered_event_bytes(snapshot))


def _delivered_event_bytes(snapshot: SandboxSnapshot) -> dict[UUID, str]:
    queued_ids = {plan.report_id for plan in snapshot.queued_reports}
    delivered: dict[UUID, str] = {}
    for report in snapshot.known_reports:
        if report.report_id in queued_ids:
            continue
        canonical_bytes = serialize_event(report.event)
        previous = delivered.get(report.event.event_id)
        if previous is not None and previous != canonical_bytes:
            raise SandboxReconciliationError("conflicting delivered known event")
        delivered[report.event.event_id] = canonical_bytes
    return delivered


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


def _validate_fill_evidence(
    *,
    known: dict[UUID, EventEnvelope[object]],
    queued_ids: frozenset[UUID],
    observed: dict[UUID, EventEnvelope[object]],
) -> dict[UUID, tuple[SandboxReconciliationReason, ...]]:
    reasons: dict[UUID, list[SandboxReconciliationReason]] = {}
    delivered_fills = tuple(
        expected
        for report_id, expected in known.items()
        if report_id not in queued_ids and type(expected.payload) is FillEvent
    )
    delivered_fill_bytes = {
        (expected.event_id, serialize_event(expected)) for expected in delivered_fills
    }

    for actual in observed.values():
        if type(actual.payload) is not FillEvent:
            continue
        if (actual.event_id, serialize_event(actual)) not in delivered_fill_bytes:
            reasons.setdefault(actual.payload.order_id, []).append(
                SandboxReconciliationReason.FILL_EVIDENCE_MISMATCH
            )

    for expected in delivered_fills:
        actual = observed.get(expected.event_id)
        exact_identity = (
            actual is not None
            and type(actual.payload) is FillEvent
            and actual.payload.execution_id == expected.payload.execution_id
            and actual.payload.report_sequence == expected.payload.report_sequence
            and actual.payload.order_id == expected.payload.order_id
        )
        if not exact_identity or serialize_event(actual) != serialize_event(expected):
            reasons.setdefault(expected.payload.order_id, []).append(
                SandboxReconciliationReason.FILL_EVIDENCE_MISMATCH
            )

    return {
        order_id: _ordered_reasons(values)
        for order_id, values in sorted(reasons.items(), key=lambda item: item[0].int)
    }


def _reconcile_canonical_request(
    request: SandboxReconciliationRequest,
) -> SandboxReconciliationResult:
    snapshot = request.snapshot
    known = _known_reports(snapshot)
    queued = _queued_reports(snapshot)
    queued_ids = frozenset(plan.report_id for plan, _ in queued)
    delivered_event_bytes = _delivered_event_bytes(snapshot)
    delivered_event_ids = frozenset(delivered_event_bytes)
    observed = _observed_by_event_id(request.observed_reports)
    order_ids = frozenset(order.order_id for order in snapshot.orders)
    fill_reasons = _validate_fill_evidence(
        known=known,
        queued_ids=queued_ids,
        observed=observed,
    )

    unattributed_event_ids: list[UUID] = []
    unattributed_reasons: list[SandboxReconciliationReason] = []
    order_evidence_reasons: dict[UUID, list[SandboxReconciliationReason]] = {}
    usable_observed: dict[UUID, EventEnvelope[object]] = {}
    for event_id, event in sorted(observed.items(), key=lambda item: item[0].int):
        if event.payload.order_id not in order_ids:
            unattributed_event_ids.append(event_id)
            unattributed_reasons.append(SandboxReconciliationReason.UNKNOWN_ORDER_REPORT)
            unattributed_reasons.extend(fill_reasons.get(event.payload.order_id, ()))
        elif event_id not in delivered_event_ids:
            unattributed_event_ids.append(event_id)
            unattributed_reasons.append(
                SandboxReconciliationReason.UNEXPECTED_OBSERVED_REPORT
            )
        elif (
            type(event.payload) is OrderEvent
            and serialize_event(event) != delivered_event_bytes[event_id]
        ):
            order_evidence_reasons.setdefault(event.payload.order_id, []).append(
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
        reasons.extend(order_evidence_reasons.get(order.order_id, ()))
        reasons.extend(fill_reasons.get(order.order_id, ()))
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
