"""Fail-closed validation for a complete P1 event stream."""

from __future__ import annotations

from decimal import Decimal

from .events import (
    P1AccountObserved,
    P1Event,
    P1Fill,
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

    accepted_targets: dict[str, tuple[str, ...]] = {}
    planned_targets: set[str] = set()
    submitted_orders: set[str] = set()
    submitted_targets: set[str] = set()
    native_order_ids: set[str] = set()
    native_fill_ids: set[str] = set()
    fill_count = 0
    position_count = 0
    account_count = 0
    previous_simulation_time = events[0].simulation_time
    submitted_order_facts: dict[str, tuple[str, Decimal, str]] = {}
    filled_quantities: dict[str, Decimal] = {}
    last_position = None
    last_account = None
    for expected, event in enumerate(events, start=2):
        if event.sequence != expected:
            raise ValueError("P1 event stream sequence is not contiguous")
        if event.simulation_time < previous_simulation_time:
            raise ValueError("simulation_time must not regress")
        previous_simulation_time = event.simulation_time
        if isinstance(event, P1TargetAccepted):
            if event.target_id in accepted_targets:
                raise ValueError("duplicate target acceptance")
            accepted_targets[event.target_id] = event.source_signal_ids
        elif isinstance(event, P1TargetQuantityPlanned):
            if event.target_id not in accepted_targets or event.target_id in planned_targets:
                raise ValueError("target quantity was not planned after acceptance")
            planned_targets.add(event.target_id)
        elif isinstance(event, P1OrderSubmitted):
            if (
                not planned_targets
                or event.client_order_id in submitted_orders
                or event.native_order_id in native_order_ids
                or event.target_id not in planned_targets
                or event.target_id in submitted_targets
                or accepted_targets[event.target_id] != event.source_signal_ids
            ):
                raise ValueError("order submission is not bound to a target plan")
            submitted_orders.add(event.client_order_id)
            native_order_ids.add(event.native_order_id)
            submitted_targets.add(event.target_id)
            submitted_order_facts[event.client_order_id] = (
                event.side,
                event.quantity,
                event.native_order_id,
            )
        elif isinstance(event, P1Fill):
            if (
                event.client_order_id not in submitted_orders
                or event.native_fill_id in native_fill_ids
            ):
                raise ValueError("fill occurred before order submission")
            native_fill_ids.add(event.native_fill_id)
            side, submitted_quantity, _ = submitted_order_facts[event.client_order_id]
            prior_quantity = filled_quantities.get(event.client_order_id, Decimal(0))
            total_quantity = prior_quantity + event.quantity
            if event.side != side or total_quantity > submitted_quantity:
                raise ValueError("fill facts do not match the submitted order")
            filled_quantities[event.client_order_id] = total_quantity
            fill_count += 1
        elif isinstance(event, P1PositionObserved):
            position_count += 1
            last_position = event
        elif isinstance(event, P1AccountObserved):
            account_count += 1
            last_account = event

    if set(accepted_targets) != planned_targets or not accepted_targets:
        raise ValueError("every accepted target requires exactly one quantity plan")
    if position_count == 0 or account_count == 0:
        raise ValueError("completion requires position and account observations")
    if not (
        isinstance(events[-3], P1PositionObserved)
        and isinstance(events[-2], P1AccountObserved)
    ):
        raise ValueError("final observations must immediately precede completion")
    if any(
        filled_quantities.get(order_id, Decimal(0)) != facts[1]
        for order_id, facts in submitted_order_facts.items()
    ):
        raise ValueError("completion cannot retain an unfinished order")
    completion = events[-1]
    if not isinstance(completion, P1RunCompleted):
        raise ValueError("P1 event stream completion is missing")
    start = events[0]
    if not isinstance(start, P1RunStarted) or (
        completion.upstream_commit != start.upstream_commit
        or completion.closure_digest != start.closure_digest
    ):
        raise ValueError("completion lineage does not match run start")
    if (
        completion.target_count != len(accepted_targets)
        or completion.order_count != len(submitted_orders)
        or completion.fill_count != fill_count
    ):
        raise ValueError("completion counters do not match the event stream")
    if (
        last_position is None
        or last_account is None
        or completion.final_position != last_position.quantity
        or completion.final_cash != last_account.cash_balance
        or completion.fees != last_account.fees
        or completion.realized_pnl != last_account.realized_pnl
        or completion.unrealized_pnl != last_account.unrealized_pnl
        or last_position.realized_pnl != last_account.realized_pnl
        or last_position.unrealized_pnl != last_account.unrealized_pnl
    ):
        raise ValueError("completion summary does not match final observations")
    from .semantic import semantic_digest

    if completion.semantic_digest != semantic_digest(events):
        raise ValueError("completion semantic digest does not match the stream")
    return events
