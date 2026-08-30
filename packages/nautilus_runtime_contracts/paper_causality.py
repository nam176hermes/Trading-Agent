from __future__ import annotations

from decimal import Decimal

from .events import (
    P1AccountObserved,
    P1Event,
    P1Fill,
    P1OrderSubmitted,
    P1PositionObserved,
    P1RunCompleted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
)


def stop_event_allowed(events: tuple[P1Event, ...], event: P1Event) -> bool:
    exit_targets = {
        item.target_id
        for item in events
        if isinstance(item, P1TargetAccepted) and item.target_weight == 0
    }
    exit_orders = {
        item.client_order_id: item.quantity
        for item in events
        if isinstance(item, P1OrderSubmitted)
        and item.target_id in exit_targets
        and item.side == "SELL"
    }
    open_quantity = sum(
        (
            item.quantity if item.side == "BUY" else -item.quantity
            for item in events
            if isinstance(item, P1Fill)
        ),
        Decimal(0),
    )
    exit_fills = {
        order_id: sum(
            (
                item.quantity
                for item in events
                if isinstance(item, P1Fill) and item.client_order_id == order_id
            ),
            Decimal(0),
        )
        for order_id in exit_orders
    }
    reserved = sum(
        (quantity - exit_fills[order_id] for order_id, quantity in exit_orders.items()),
        Decimal(0),
    )
    if isinstance(event, P1TargetAccepted):
        return event.target_weight == 0
    if isinstance(event, P1TargetQuantityPlanned):
        return event.target_id in exit_targets
    if isinstance(event, P1OrderSubmitted):
        return (
            event.target_id in exit_targets
            and event.side == "SELL"
            and reserved + event.quantity <= open_quantity
        )
    if isinstance(event, P1Fill):
        return (
            event.client_order_id in exit_orders
            and event.side == "SELL"
            and event.quantity <= open_quantity
            and exit_fills[event.client_order_id] + event.quantity
            <= exit_orders[event.client_order_id]
        )
    if isinstance(event, P1PositionObserved):
        return open_quantity == event.quantity == 0
    if isinstance(event, P1AccountObserved):
        return bool(events) and isinstance(events[-1], P1PositionObserved) and events[-1].quantity == 0
    return isinstance(event, P1RunCompleted) and open_quantity == 0 and bool(events) and isinstance(events[-1], P1AccountObserved)


__all__ = ["stop_event_allowed"]
