from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    ORDER_STATUS_TRANSITIONS,
    Currency,
    InstrumentId,
    OrderCancelResolution,
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
        "cancel_resolution": None,
        "schema_version": "2.0",
    }
    supplied_fingerprint = changes.pop("event_fingerprint", None)
    values.update(changes)
    event = OrderEvent.create(**values)  # type: ignore[arg-type]
    canonical = {
        name: getattr(event, name) for name in OrderEvent.model_fields
    }
    if supplied_fingerprint is not None:
        canonical["event_fingerprint"] = supplied_fingerprint
    return canonical


def reachable_state(*targets: OrderStatus) -> OrderState:
    state = OrderState(order_id=ORDER_ID)
    for sequence, target in enumerate(targets, start=1):
        reason = (
            "OBSERVED_TERMINAL"
            if target
            in {
                OrderStatus.CANCELED,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
                OrderStatus.DENIED,
            }
            else None
        )
        state = reduce_order(
            state,
            OrderEvent(
                **order_event_values(
                    event_id=uid(200 + sequence),
                    sequence=sequence,
                    target_status=target,
                    occurred_at=NOW + timedelta(seconds=sequence),
                    reason=reason,
                )
            ),
        )
    return state


REACHABLE_STATE_PREFIXES = {
    (OrderStatus.INITIALIZED, False): (),
    (OrderStatus.SUBMITTED, False): (OrderStatus.SUBMITTED,),
    (OrderStatus.ACCEPTED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
    ),
    (OrderStatus.PENDING_UPDATE, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_UPDATE,
    ),
    (OrderStatus.PENDING_CANCEL, True): (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
    ),
    (OrderStatus.TRIGGERED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.TRIGGERED,
    ),
    (OrderStatus.PARTIALLY_FILLED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
    ),
    (OrderStatus.TRIGGERED, True): (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
        OrderStatus.TRIGGERED,
    ),
    (OrderStatus.PARTIALLY_FILLED, True): (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
        OrderStatus.PARTIALLY_FILLED,
    ),
    (OrderStatus.FILLED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.FILLED,
    ),
    (OrderStatus.CANCELED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.CANCELED,
    ),
    (OrderStatus.EXPIRED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.EXPIRED,
    ),
    (OrderStatus.REJECTED, False): (
        OrderStatus.SUBMITTED,
        OrderStatus.REJECTED,
    ),
    (OrderStatus.DENIED, False): (OrderStatus.DENIED,),
}


def reachable_state_key(status: OrderStatus, cancel_pending: bool) -> OrderState:
    return reachable_state(*REACHABLE_STATE_PREFIXES[(status, cancel_pending)])


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
    assert set(ORDER_STATUS_TRANSITIONS) == set(REACHABLE_STATE_PREFIXES)
    assert all(
        isinstance(targets, MappingProxyType)
        for targets in ORDER_STATUS_TRANSITIONS.values()
    )
    with pytest.raises(TypeError):
        ORDER_STATUS_TRANSITIONS[(OrderStatus.INITIALIZED, False)] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        ORDER_STATUS_TRANSITIONS[(OrderStatus.INITIALIZED, False)][
            (OrderStatus.SUBMITTED, None)
        ] = (OrderStatus.SUBMITTED, False)  # type: ignore[index]


def test_reducer_applies_every_declared_transition_from_reachable_history() -> None:
    event_number = 1000
    for source, observations in ORDER_STATUS_TRANSITIONS.items():
        for (target, resolution), expected in observations.items():
            state = reachable_state_key(*source)
            reason = (
                "CANCEL_REJECTED"
                if resolution is not None
                else "VENUE_TERMINATED"
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
                    event_id=uid(event_number),
                    sequence=state.last_sequence + 1,
                    target_status=target,
                    occurred_at=NOW
                    + timedelta(seconds=state.last_sequence + 1),
                    reason=reason,
                    cancel_resolution=resolution,
                )
            )
            event_number += 1

            reduced = reduce_order(state, event)

            assert (reduced.status, reduced.cancel_pending) == expected
            assert reduced.last_sequence == event.sequence
            assert reduced.applied_events[-1] == event
            assert (state.status, state.cancel_pending) == source


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
    state = reachable_state_key(source, False)
    event = OrderEvent(
        **order_event_values(
            event_id=uid(30),
            sequence=state.last_sequence + 1,
            target_status=target,
            reason=(
                "INVALID_JUMP"
                if target
                in {
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                    OrderStatus.REJECTED,
                    OrderStatus.DENIED,
                }
                else None
            ),
        )
    )

    with pytest.raises(OrderReductionError, match="forbidden order transition"):
        reduce_order(state, event)


def test_pending_cancel_resolves_explicit_cancel_and_fill_races() -> None:
    state = reachable_state(
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
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
                    sequence=4,
                    target_status=target,
                    reason=reason,
                )
            ),
        )
        assert result.status is target


def test_pending_cancel_rejects_an_update_and_terminal_states_never_escape() -> None:
    pending_cancel = reachable_state(
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
    )
    update = OrderEvent(
        **order_event_values(
            event_id=uid(50),
            sequence=4,
            target_status=OrderStatus.PENDING_UPDATE,
        )
    )
    with pytest.raises(OrderReductionError, match="pending cancel"):
        reduce_order(pending_cancel, update)

    for terminal, prefix in (
        (OrderStatus.FILLED, (OrderStatus.SUBMITTED, OrderStatus.FILLED)),
        (OrderStatus.CANCELED, (OrderStatus.SUBMITTED, OrderStatus.CANCELED)),
        (OrderStatus.EXPIRED, (OrderStatus.SUBMITTED, OrderStatus.EXPIRED)),
        (OrderStatus.REJECTED, (OrderStatus.SUBMITTED, OrderStatus.REJECTED)),
        (OrderStatus.DENIED, (OrderStatus.DENIED,)),
    ):
        state = reachable_state(*prefix)
        with pytest.raises(OrderReductionError, match="terminal order status"):
            reduce_order(
                state,
                OrderEvent(
                    **order_event_values(
                        event_id=uid(60 + len(terminal.value)),
                        sequence=state.last_sequence + 1,
                        target_status=OrderStatus.SUBMITTED,
                    )
                ),
            )


def test_duplicate_event_is_an_idempotent_noop_and_conflicting_reuse_fails_closed() -> None:
    initial = OrderState(order_id=ORDER_ID)
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
    state = reduce_order(
        OrderState(order_id=ORDER_ID),
        OrderEvent(
            **order_event_values(
                event_id=uid(79),
                sequence=3,
                target_status=OrderStatus.SUBMITTED,
            )
        ),
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
    without_fingerprint = order_event_values()
    without_fingerprint.pop("event_fingerprint")

    assert event.event_fingerprint == same.event_fingerprint
    assert len(event.event_fingerprint) == 64
    assert event.model_dump(mode="json")["event_fingerprint"] == event.event_fingerprint
    with pytest.raises(ValidationError, match="event_fingerprint"):
        OrderEvent(**without_fingerprint)
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
    initial = OrderState(order_id=ORDER_ID)
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


@pytest.mark.parametrize(
    "race_status", [OrderStatus.PARTIALLY_FILLED, OrderStatus.TRIGGERED]
)
def test_pending_cancel_lineage_survives_non_resolving_race_observations(
    race_status: OrderStatus,
) -> None:
    raced = reachable_state(
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
        race_status,
    )
    update = OrderEvent(
        **order_event_values(
            event_id=uid(230),
            sequence=5,
            target_status=OrderStatus.PENDING_UPDATE,
        )
    )

    assert raced.cancel_pending is True
    with pytest.raises(OrderReductionError, match="pending cancel|forbidden"):
        reduce_order(raced, update)

    resolved = reduce_order(
        raced,
        OrderEvent(
            **order_event_values(
                event_id=uid(231),
                sequence=5,
                target_status=race_status,
                reason="CANCEL_REJECTED",
                cancel_resolution=OrderCancelResolution.REJECTED,
            )
        ),
    )
    assert resolved.cancel_pending is False
    assert reduce_order(
        resolved,
        OrderEvent(
            **order_event_values(
                event_id=uid(232),
                sequence=6,
                target_status=OrderStatus.PENDING_UPDATE,
            )
        ),
    ).status is OrderStatus.PENDING_UPDATE


@pytest.mark.parametrize(
    ("race_status", "terminal", "reason"),
    [
        (OrderStatus.TRIGGERED, OrderStatus.FILLED, None),
        (OrderStatus.TRIGGERED, OrderStatus.CANCELED, "CANCEL_CONFIRMED"),
        (OrderStatus.TRIGGERED, OrderStatus.EXPIRED, "ORDER_EXPIRED"),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, None),
        (
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCELED,
            "CANCEL_CONFIRMED",
        ),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.EXPIRED, "ORDER_EXPIRED"),
    ],
)
def test_pending_cancel_terminal_races_are_deterministic(
    race_status: OrderStatus, terminal: OrderStatus, reason: str | None
) -> None:
    raced = reachable_state(
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PENDING_CANCEL,
        race_status,
    )
    event = OrderEvent(
        **order_event_values(
            event_id=uid(235),
            sequence=5,
            target_status=terminal,
            reason=reason,
        )
    )

    first = reduce_order(raced, event)
    second = reduce_order(raced, event)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert (first.status, first.cancel_pending) == (terminal, False)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("order_id", uid(999)),
        ("sequence", 99),
        ("target_status", OrderStatus.FILLED),
        ("occurred_at", NOW + timedelta(days=1)),
        ("reason", "CHANGED_CONTENT"),
        ("cancel_resolution", OrderCancelResolution.REJECTED),
        ("schema_version", "forged-version"),
    ],
)
def test_reducer_revalidates_each_fingerprinted_field_before_duplicate_classification(
    field: str, changed: object
) -> None:
    event = OrderEvent(**order_event_values(event_id=uid(240)))
    submitted = reduce_order(
        OrderState(order_id=ORDER_ID), event
    )
    forged = event.model_copy(update={field: changed})

    with pytest.raises(OrderReductionError, match="invalid order event|event id conflict"):
        reduce_order(submitted, forged)


def test_reducer_revalidates_model_construct_event_before_duplicate_classification() -> None:
    event = OrderEvent(**order_event_values(event_id=uid(241)))
    submitted = reduce_order(
        OrderState(order_id=ORDER_ID), event
    )
    values = {name: getattr(event, name) for name in type(event).model_fields}
    values["target_status"] = OrderStatus.FILLED
    forged = OrderEvent.model_construct(**values)

    with pytest.raises(OrderReductionError, match="invalid order event|event id conflict"):
        reduce_order(submitted, forged)


def test_empty_order_history_is_exactly_the_initialized_zero_sequence_state() -> None:
    with pytest.raises(ValidationError, match="empty history"):
        OrderState(
            order_id=ORDER_ID,
            status=OrderStatus.PENDING_CANCEL,
            last_sequence=9,
            applied_events=(),
            schema_version="2.0",
        )


def test_state_history_rejects_forbidden_jump_and_event_after_terminal() -> None:
    submitted = OrderEvent(**order_event_values(event_id=uid(250)))
    forbidden = OrderEvent(
        **order_event_values(
            event_id=uid(251),
            sequence=2,
            target_status=OrderStatus.TRIGGERED,
        )
    )
    with pytest.raises(ValidationError, match="forbidden order transition"):
        OrderState(
            order_id=ORDER_ID,
            status=OrderStatus.TRIGGERED,
            last_sequence=2,
            applied_events=(submitted, forbidden),
            schema_version="2.0",
        )

    filled = OrderEvent(
        **order_event_values(
            event_id=uid(252), sequence=2, target_status=OrderStatus.FILLED
        )
    )
    after_terminal = OrderEvent(
        **order_event_values(
            event_id=uid(253),
            sequence=3,
            target_status=OrderStatus.PENDING_UPDATE,
        )
    )
    with pytest.raises(ValidationError, match="terminal order status"):
        OrderState(
            order_id=ORDER_ID,
            status=OrderStatus.PENDING_UPDATE,
            last_sequence=3,
            applied_events=(submitted, filled, after_terminal),
            schema_version="2.0",
        )


@pytest.mark.parametrize(
    "forged_change",
    [
        {"status": OrderStatus.ACCEPTED},
        {"last_sequence": 99},
    ],
)
def test_reducer_revalidates_forged_terminal_state_before_reduction(
    forged_change: dict[str, object],
) -> None:
    filled = reachable_state(OrderStatus.SUBMITTED, OrderStatus.FILLED)
    forged = filled.model_copy(update=forged_change)
    update = OrderEvent(
        **order_event_values(
            event_id=uid(260),
            sequence=3,
            target_status=OrderStatus.PENDING_UPDATE,
        )
    )

    with pytest.raises(OrderReductionError, match="invalid order state"):
        reduce_order(forged, update)


def test_sequence_is_the_only_ordering_authority_and_accepts_observed_clock_skew() -> None:
    submitted = reachable_state(OrderStatus.SUBMITTED)
    skewed_ack = OrderEvent(
        **order_event_values(
            event_id=uid(270),
            sequence=2,
            target_status=OrderStatus.ACCEPTED,
            occurred_at=NOW - timedelta(days=1),
        )
    )

    assert reduce_order(submitted, skewed_ack).status is OrderStatus.ACCEPTED


@pytest.mark.parametrize(
    ("observed", "reason"),
    [
        (OrderStatus.PARTIALLY_FILLED, None),
        (OrderStatus.FILLED, None),
        (OrderStatus.CANCELED, "OBSERVED_CANCELED"),
        (OrderStatus.EXPIRED, "OBSERVED_EXPIRED"),
        (OrderStatus.REJECTED, "OBSERVED_REJECTED"),
    ],
)
def test_submitted_state_accepts_observed_ack_races(
    observed: OrderStatus, reason: str | None
) -> None:
    submitted = reachable_state(OrderStatus.SUBMITTED)
    event = OrderEvent(
        **order_event_values(
            event_id=uid(280 + len(observed.value)),
            sequence=2,
            target_status=observed,
            reason=reason,
        )
    )

    assert reduce_order(submitted, event).status is observed


def test_lifecycle_event_rejects_the_accepted_base_wire_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        OrderEvent(**order_event_values(schema_version="1.0"))
