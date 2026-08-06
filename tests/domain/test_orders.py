from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    ORDER_STATUS_TRANSITIONS,
    Currency,
    InstrumentId,
    OrderEvent,
    OrderIntent,
    OrderQuantity,
    OrderReductionError,
    OrderSide,
    OrderState,
    OrderStatus,
    OrderType,
    Price,
    ProductType,
    Quantity,
    TimeInForce,
    reduce_order,
)


NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")


def uid(number: int) -> UUID:
    return UUID(int=number)


ORDER_ID = uid(10)


def order_intent_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "intent_id": uid(1),
        "risk_decision_id": uid(2),
        "client_order_id": "client-1",
        "venue_order_id": None,
        "strategy_id": "strategy-1",
        "trader_id": "trader-1",
        "account_id": "account-1",
        "execution_client_id": "execution-client-1",
        "order_list_id": None,
        "instrument": INSTRUMENT,
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "time_in_force": TimeInForce.DAY,
        "quantity": OrderQuantity(Decimal("1.25"), precision=2),
        "limit_price": Price(Decimal("100"), Currency.USD),
        "trigger_price": None,
        "trailing_offset": None,
        "gtd_expiry": None,
        "post_only": False,
        "reduce_only": False,
        "requested_at": NOW,
        "schema_version": "1.0",
    }
    values.update(changes)
    return values


def order_event_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "event_id": uid(20),
        "order_id": ORDER_ID,
        "sequence": 1,
        "target_status": OrderStatus.SUBMITTED,
        "occurred_at": NOW,
        "reason": None,
        "schema_version": "1.0",
    }
    values.update(changes)
    return values


EXPECTED_TRANSITIONS = {
    OrderStatus.INITIALIZED: frozenset(
        {OrderStatus.SUBMITTED, OrderStatus.DENIED}
    ),
    OrderStatus.SUBMITTED: frozenset(
        {
            OrderStatus.ACCEPTED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PENDING_UPDATE,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.TRIGGERED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PENDING_UPDATE: frozenset(
        {
            OrderStatus.ACCEPTED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.TRIGGERED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PENDING_CANCEL: frozenset(
        {
            OrderStatus.ACCEPTED,
            OrderStatus.TRIGGERED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.TRIGGERED: frozenset(
        {
            OrderStatus.PENDING_UPDATE,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PENDING_UPDATE,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.DENIED: frozenset(),
}


def test_order_statuses_are_the_complete_stable_uppercase_contract() -> None:
    assert tuple(status.value for status in OrderStatus) == (
        "INITIALIZED",
        "SUBMITTED",
        "ACCEPTED",
        "PENDING_UPDATE",
        "PENDING_CANCEL",
        "TRIGGERED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "EXPIRED",
        "REJECTED",
        "DENIED",
    )


def test_order_types_and_time_in_force_are_the_complete_instruction_contract() -> None:
    assert tuple(order_type.value for order_type in OrderType) == (
        "market",
        "limit",
        "stop_market",
        "stop_limit",
        "market_if_touched",
        "limit_if_touched",
        "trailing_stop",
    )
    assert tuple(time_in_force.value for time_in_force in TimeInForce) == (
        "gtc",
        "ioc",
        "fok",
        "day",
        "gtd",
    )


def test_order_intent_requires_unsigned_order_quantity_and_positive_size() -> None:
    intent = OrderIntent(**order_intent_values())

    assert intent.quantity == OrderQuantity(Decimal("1.25"), 2)
    with pytest.raises(ValidationError, match="OrderQuantity"):
        OrderIntent(
            **order_intent_values(quantity=Quantity(Decimal("1.25"), precision=2))
        )
    with pytest.raises(ValidationError, match="positive"):
        OrderIntent(
            **order_intent_values(quantity=OrderQuantity(Decimal("0"), precision=2))
        )


@pytest.mark.parametrize(
    ("order_type_name", "limit_price", "trigger_price", "trailing_offset"),
    [
        ("MARKET", None, None, None),
        ("LIMIT", Price(Decimal("100"), Currency.USD), None, None),
        ("STOP_MARKET", None, Price(Decimal("99"), Currency.USD), None),
        (
            "STOP_LIMIT",
            Price(Decimal("98"), Currency.USD),
            Price(Decimal("99"), Currency.USD),
            None,
        ),
        (
            "MARKET_IF_TOUCHED",
            None,
            Price(Decimal("101"), Currency.USD),
            None,
        ),
        (
            "LIMIT_IF_TOUCHED",
            Price(Decimal("102"), Currency.USD),
            Price(Decimal("101"), Currency.USD),
            None,
        ),
        (
            "TRAILING_STOP",
            None,
            None,
            Price(Decimal("2"), Currency.USD),
        ),
    ],
)
def test_each_order_type_accepts_only_its_required_price_data(
    order_type_name: str,
    limit_price: Price | None,
    trigger_price: Price | None,
    trailing_offset: Price | None,
) -> None:
    order_type = getattr(OrderType, order_type_name)
    intent = OrderIntent(
        **order_intent_values(
            order_type=order_type,
            limit_price=limit_price,
            trigger_price=trigger_price,
            trailing_offset=trailing_offset,
        )
    )

    assert intent.order_type is order_type


@pytest.mark.parametrize(
    ("order_type_name", "changes", "message"),
    [
        ("MARKET", {"limit_price": Price(Decimal("1"), Currency.USD)}, "limit_price"),
        ("LIMIT", {"limit_price": None}, "limit_price"),
        ("STOP_MARKET", {"limit_price": None, "trigger_price": None}, "trigger_price"),
        (
            "STOP_LIMIT",
            {"limit_price": Price(Decimal("1"), Currency.USD), "trigger_price": None},
            "trigger_price",
        ),
        (
            "MARKET_IF_TOUCHED",
            {"limit_price": None, "trigger_price": None},
            "trigger_price",
        ),
        (
            "LIMIT_IF_TOUCHED",
            {"limit_price": None, "trigger_price": Price(Decimal("1"), Currency.USD)},
            "limit_price",
        ),
        ("TRAILING_STOP", {"limit_price": None, "trailing_offset": None}, "trailing_offset"),
        (
            "TRAILING_STOP",
            {
                "limit_price": None,
                "trigger_price": Price(Decimal("1"), Currency.USD),
                "trailing_offset": Price(Decimal("2"), Currency.USD),
            },
            "trigger_price",
        ),
    ],
)
def test_order_type_rejects_missing_or_forbidden_price_data(
    order_type_name: str, changes: dict[str, object], message: str
) -> None:
    order_type = getattr(OrderType, order_type_name)
    with pytest.raises(ValidationError, match=message):
        OrderIntent(**order_intent_values(order_type=order_type, **changes))


def test_gtd_requires_a_future_utc_expiry_and_other_tif_values_forbid_it() -> None:
    expiry = NOW + timedelta(days=1)
    intent = OrderIntent(
        **order_intent_values(time_in_force=TimeInForce.GTD, gtd_expiry=expiry)
    )

    assert intent.gtd_expiry == expiry
    with pytest.raises(ValidationError, match="gtd_expiry"):
        OrderIntent(**order_intent_values(time_in_force=TimeInForce.GTD))
    with pytest.raises(ValidationError, match="after requested_at"):
        OrderIntent(
            **order_intent_values(time_in_force=TimeInForce.GTD, gtd_expiry=NOW)
        )
    with pytest.raises(ValidationError, match="gtd_expiry"):
        OrderIntent(**order_intent_values(gtd_expiry=expiry))
    with pytest.raises(ValidationError, match="UTC"):
        OrderIntent(
            **order_intent_values(
                time_in_force=TimeInForce.GTD,
                gtd_expiry=datetime(2026, 8, 7, 12),
            )
        )


def test_post_only_is_limited_to_resting_limit_orders_and_reduce_only_is_data() -> None:
    post_only = OrderIntent(**order_intent_values(post_only=True))
    reduce_only = OrderIntent(**order_intent_values(reduce_only=True))

    assert post_only.post_only is True
    assert reduce_only.reduce_only is True
    with pytest.raises(ValidationError, match="post_only"):
        OrderIntent(
            **order_intent_values(
                order_type=OrderType.MARKET,
                limit_price=None,
                post_only=True,
            )
        )
    with pytest.raises(ValidationError, match="post_only"):
        OrderIntent(
            **order_intent_values(time_in_force=TimeInForce.IOC, post_only=True)
        )
    with pytest.raises(ValidationError):
        OrderIntent(**order_intent_values(post_only=1))


@pytest.mark.parametrize(
    "field",
    [
        "client_order_id",
        "venue_order_id",
        "strategy_id",
        "trader_id",
        "account_id",
        "execution_client_id",
        "order_list_id",
    ],
)
@pytest.mark.parametrize("invalid", ["", " has-space", "bad/value", "x" * 65])
def test_order_identity_values_are_safe_bounded_canonical_identifiers(
    field: str, invalid: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        OrderIntent(**order_intent_values(**{field: invalid}))


def test_required_order_identities_cannot_be_omitted_but_venue_and_list_are_optional() -> None:
    values = order_intent_values()
    for field in (
        "client_order_id",
        "strategy_id",
        "trader_id",
        "account_id",
        "execution_client_id",
    ):
        missing = dict(values)
        del missing[field]
        with pytest.raises(ValidationError, match=field):
            OrderIntent(**missing)

    intent = OrderIntent(**values)
    assert intent.venue_order_id is None
    assert intent.order_list_id is None


def test_order_intent_is_strict_frozen_utc_only_and_deterministically_serialized() -> None:
    intent = OrderIntent(**order_intent_values())

    assert intent.model_dump_json() == OrderIntent(**order_intent_values()).model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        intent.client_order_id = "client-2"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="UTC"):
        OrderIntent(**order_intent_values(requested_at=datetime(2026, 8, 6, 12)))
    with pytest.raises(ValidationError, match="UTC"):
        OrderIntent(
            **order_intent_values(
                requested_at=datetime(
                    2026, 8, 6, 8, tzinfo=timezone(timedelta(hours=-4))
                )
            )
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        OrderIntent(**order_intent_values(provider_payload={}))


def test_order_instruction_enums_and_flags_are_strict() -> None:
    with pytest.raises(ValidationError):
        OrderIntent(**order_intent_values(order_type="limit"))
    with pytest.raises(ValidationError):
        OrderIntent(**order_intent_values(time_in_force="day"))
    with pytest.raises(ValidationError, match="limit_price"):
        OrderIntent(**order_intent_values(limit_price=100.0))


@pytest.mark.parametrize(
    "order_type",
    [OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LIMIT_IF_TOUCHED],
)
@pytest.mark.parametrize(
    "time_in_force", [TimeInForce.GTC, TimeInForce.DAY, TimeInForce.GTD]
)
def test_post_only_accepts_limit_instructions_that_can_rest(
    order_type: OrderType, time_in_force: TimeInForce
) -> None:
    changes: dict[str, object] = {
        "order_type": order_type,
        "time_in_force": time_in_force,
        "post_only": True,
    }
    if order_type in {OrderType.STOP_LIMIT, OrderType.LIMIT_IF_TOUCHED}:
        changes["trigger_price"] = Price(Decimal("99"), Currency.USD)
    if time_in_force is TimeInForce.GTD:
        changes["gtd_expiry"] = NOW + timedelta(days=1)

    assert OrderIntent(**order_intent_values(**changes)).post_only is True


def test_transition_table_is_complete_centralized_and_immutable() -> None:
    assert dict(ORDER_STATUS_TRANSITIONS) == EXPECTED_TRANSITIONS
    assert set(ORDER_STATUS_TRANSITIONS) == set(OrderStatus)
    assert all(
        isinstance(targets, frozenset)
        for targets in ORDER_STATUS_TRANSITIONS.values()
    )
    with pytest.raises(TypeError):
        ORDER_STATUS_TRANSITIONS[OrderStatus.INITIALIZED] = frozenset()  # type: ignore[index]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in EXPECTED_TRANSITIONS.items()
        for target in targets
    ],
)
def test_reducer_applies_every_declared_valid_transition(
    source: OrderStatus, target: OrderStatus
) -> None:
    state = OrderState(
        order_id=ORDER_ID,
        status=source,
        last_sequence=7,
        applied_events=(),
        schema_version="1.0",
    )
    reason = (
        "VENUE_TERMINATED"
        if target
        in {
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
            OrderStatus.DENIED,
        }
        else None
    )
    event = OrderEvent(
        **order_event_values(
            event_id=uid(1000 + len(target.value) + len(source.value)),
            sequence=8,
            target_status=target,
            occurred_at=NOW + timedelta(seconds=8),
            reason=reason,
        )
    )

    reduced = reduce_order(state, event)

    assert reduced.status is target
    assert reduced.last_sequence == 8
    assert reduced.applied_events == (event,)
    assert state.status is source
    assert state.last_sequence == 7
    assert state.applied_events == ()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (OrderStatus.INITIALIZED, OrderStatus.ACCEPTED),
        (OrderStatus.INITIALIZED, OrderStatus.FILLED),
        (OrderStatus.SUBMITTED, OrderStatus.TRIGGERED),
        (OrderStatus.ACCEPTED, OrderStatus.SUBMITTED),
        (OrderStatus.PENDING_UPDATE, OrderStatus.SUBMITTED),
        (OrderStatus.TRIGGERED, OrderStatus.ACCEPTED),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.ACCEPTED),
    ],
)
def test_reducer_rejects_forbidden_jumps(
    source: OrderStatus, target: OrderStatus
) -> None:
    state = OrderState(
        order_id=ORDER_ID,
        status=source,
        last_sequence=1,
        applied_events=(),
        schema_version="1.0",
    )
    event = OrderEvent(
        **order_event_values(
            event_id=uid(30),
            sequence=2,
            target_status=target,
            reason="INVALID_JUMP" if target is OrderStatus.FILLED else None,
        )
    )

    with pytest.raises(OrderReductionError, match="forbidden order transition"):
        reduce_order(state, event)


def test_pending_cancel_resolves_explicit_cancel_and_fill_races() -> None:
    state = OrderState(
        order_id=ORDER_ID,
        status=OrderStatus.PENDING_CANCEL,
        last_sequence=4,
        applied_events=(),
        schema_version="1.0",
    )

    for number, target, reason in (
        (40, OrderStatus.CANCELED, "CANCEL_CONFIRMED"),
        (41, OrderStatus.PARTIALLY_FILLED, None),
        (42, OrderStatus.FILLED, None),
    ):
        result = reduce_order(
            state,
            OrderEvent(
                **order_event_values(
                    event_id=uid(number),
                    sequence=5,
                    target_status=target,
                    reason=reason,
                )
            ),
        )
        assert result.status is target


def test_pending_cancel_rejects_an_update_and_terminal_states_never_escape() -> None:
    pending_cancel = OrderState(
        order_id=ORDER_ID,
        status=OrderStatus.PENDING_CANCEL,
        last_sequence=1,
        applied_events=(),
        schema_version="1.0",
    )
    update = OrderEvent(
        **order_event_values(
            event_id=uid(50),
            sequence=2,
            target_status=OrderStatus.PENDING_UPDATE,
        )
    )
    with pytest.raises(OrderReductionError, match="forbidden order transition"):
        reduce_order(pending_cancel, update)

    for terminal in (
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.DENIED,
    ):
        state = pending_cancel.model_copy(update={"status": terminal})
        with pytest.raises(OrderReductionError, match="terminal order status"):
            reduce_order(
                state,
                OrderEvent(
                    **order_event_values(
                        event_id=uid(60 + len(terminal.value)),
                        sequence=2,
                        target_status=OrderStatus.SUBMITTED,
                    )
                ),
            )


def test_duplicate_event_is_an_idempotent_noop_and_conflicting_reuse_fails_closed() -> None:
    initial = OrderState(
        order_id=ORDER_ID,
        status=OrderStatus.INITIALIZED,
        last_sequence=0,
        applied_events=(),
        schema_version="1.0",
    )
    event = OrderEvent(**order_event_values(event_id=uid(70)))
    submitted = reduce_order(initial, event)

    assert reduce_order(submitted, event) is submitted

    conflict = OrderEvent(
        **order_event_values(
            event_id=event.event_id,
            sequence=2,
            target_status=OrderStatus.ACCEPTED,
        )
    )
    with pytest.raises(OrderReductionError, match="event id conflict"):
        reduce_order(submitted, conflict)


def test_reducer_rejects_order_mismatch_and_non_increasing_sequence() -> None:
    state = OrderState(
        order_id=ORDER_ID,
        status=OrderStatus.SUBMITTED,
        last_sequence=3,
        applied_events=(),
        schema_version="1.0",
    )
    mismatch = OrderEvent(
        **order_event_values(
            event_id=uid(80),
            order_id=uid(81),
            sequence=4,
            target_status=OrderStatus.ACCEPTED,
        )
    )
    with pytest.raises(OrderReductionError, match="order id mismatch"):
        reduce_order(state, mismatch)

    for sequence in (2, 3):
        event = OrderEvent(
            **order_event_values(
                event_id=uid(82 + sequence),
                sequence=sequence,
                target_status=OrderStatus.ACCEPTED,
            )
        )
        with pytest.raises(OrderReductionError, match="non-increasing sequence"):
            reduce_order(state, event)


def test_order_event_is_strict_frozen_utc_and_has_a_verified_canonical_fingerprint() -> None:
    event = OrderEvent(**order_event_values())
    same = OrderEvent(**order_event_values())

    assert event.event_fingerprint == same.event_fingerprint
    assert len(event.event_fingerprint) == 64
    assert event.model_dump(mode="json")["event_fingerprint"] == event.event_fingerprint
    with pytest.raises(ValidationError, match="frozen"):
        event.sequence = 2  # type: ignore[misc]
    with pytest.raises(ValidationError, match="event_fingerprint"):
        OrderEvent(
            **order_event_values(
                sequence=2, event_fingerprint=event.event_fingerprint
            )
        )
    with pytest.raises(ValidationError, match="UTC"):
        OrderEvent(**order_event_values(occurred_at=datetime(2026, 8, 6, 12)))
    with pytest.raises(ValidationError):
        OrderEvent(**order_event_values(sequence=True))
    with pytest.raises(ValidationError, match="greater than 0"):
        OrderEvent(**order_event_values(sequence=0))


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.DENIED,
    ],
)
def test_terminal_outcome_events_require_a_canonical_reason(
    status: OrderStatus,
) -> None:
    with pytest.raises(ValidationError, match="reason"):
        OrderEvent(**order_event_values(target_status=status))
    with pytest.raises(ValidationError, match="reason"):
        OrderEvent(
            **order_event_values(target_status=status, reason="not canonical")
        )

    event = OrderEvent(
        **order_event_values(target_status=status, reason="VENUE_TERMINATED")
    )
    assert event.reason == "VENUE_TERMINATED"


def test_reduction_and_round_trip_serialization_are_deterministic() -> None:
    initial = OrderState(
        order_id=ORDER_ID,
        status=OrderStatus.INITIALIZED,
        last_sequence=0,
        applied_events=(),
        schema_version="1.0",
    )
    events = (
        OrderEvent(**order_event_values(event_id=uid(90))),
        OrderEvent(
            **order_event_values(
                event_id=uid(91),
                sequence=2,
                target_status=OrderStatus.ACCEPTED,
                occurred_at=NOW + timedelta(seconds=1),
            )
        ),
        OrderEvent(
            **order_event_values(
                event_id=uid(92),
                sequence=3,
                target_status=OrderStatus.PARTIALLY_FILLED,
                occurred_at=NOW + timedelta(seconds=2),
            )
        ),
    )

    first = initial
    second = initial
    for event in events:
        first = reduce_order(first, event)
        second = reduce_order(second, event)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert OrderState.model_validate_json(first.model_dump_json()) == first
    assert OrderEvent.model_validate_json(events[0].model_dump_json()) == events[0]
    assert initial.status is OrderStatus.INITIALIZED
    assert initial.applied_events == ()
