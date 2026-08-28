"""Fail-closed validation for a complete P1 event stream."""

from __future__ import annotations

from .events import (
    P1AccountObserved,
    P1Event,
    P1Fill,
    P1OrderAccepted,
    P1OrderSubmitted,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
)


def validate_event_stream(events: tuple[P1Event, ...]) -> tuple[P1Event, ...]:
    if len(events) < 5:
        raise ValueError("P1 event stream is incomplete")
    if not isinstance(events[0], P1RunStarted) or not isinstance(events[-1], P1RunCompleted):
        raise ValueError("P1 event stream must start and complete exactly once")
    if sum(isinstance(event, P1RunStarted) for event in events) != 1 or sum(
        isinstance(event, P1RunCompleted) for event in events
    ) != 1:
        raise ValueError("P1 event stream lifecycle cardinality is invalid")

    accepted_targets: set[str] = set()
    planned_targets: set[str] = set()
    submitted_orders: set[str] = set()
    accepted_orders: set[str] = set()
    fill_count = 0
    position_count = 0
    account_count = 0
    for expected, event in enumerate(events, start=1):
        if event.sequence != expected:
            raise ValueError("P1 event stream sequence is not contiguous")
        if isinstance(event, P1TargetAccepted):
            if event.target_id in accepted_targets:
                raise ValueError("duplicate target acceptance")
            accepted_targets.add(event.target_id)
        elif isinstance(event, P1TargetQuantityPlanned):
            if event.target_id not in accepted_targets or event.target_id in planned_targets:
                raise ValueError("target quantity was not planned after acceptance")
            planned_targets.add(event.target_id)
        elif isinstance(event, P1OrderSubmitted):
            if not planned_targets or event.client_order_id in submitted_orders:
                raise ValueError("order submission is not bound to a target plan")
            submitted_orders.add(event.client_order_id)
        elif isinstance(event, P1OrderAccepted):
            if event.client_order_id not in submitted_orders or event.client_order_id in accepted_orders:
                raise ValueError("order acceptance has no unique submitted order")
            accepted_orders.add(event.client_order_id)
        elif isinstance(event, P1Fill):
            if event.client_order_id not in submitted_orders:
                raise ValueError("fill occurred before order submission")
            fill_count += 1
        elif isinstance(event, P1PositionObserved):
            position_count += 1
        elif isinstance(event, P1AccountObserved):
            account_count += 1

    if accepted_targets != planned_targets or not accepted_targets:
        raise ValueError("every accepted target requires exactly one quantity plan")
    if position_count == 0 or account_count == 0:
        raise ValueError("completion requires position and account observations")
    completion = events[-1]
    if not isinstance(completion, P1RunCompleted):
        raise ValueError("P1 event stream completion is missing")
    if (
        completion.target_count != len(accepted_targets)
        or completion.order_count != len(submitted_orders)
        or completion.fill_count != fill_count
    ):
        raise ValueError("completion counters do not match the event stream")
    return events
