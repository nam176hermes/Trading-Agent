from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1Fill,
    P1OrderAccepted,
    P1OrderSubmitted,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
    P1_EVENT_ADAPTER,
    event_message_id,
)
from packages.nautilus_runtime_contracts.state_machine import validate_event_stream


NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
DIGEST = "a" * 64


def stream() -> tuple:
    return (
        P1RunStarted(sequence=1, simulation_time=NOW, runtime_family="cython-v1", engine_version="1.231.0", upstream_commit="b" * 40, closure_digest=DIGEST, config_digest=DIGEST, catalog_digest=DIGEST, data_digest=DIGEST),
        P1TargetAccepted(sequence=2, simulation_time=NOW, target_id="target-1", target_weight=Decimal("1")),
        P1TargetQuantityPlanned(sequence=3, simulation_time=NOW, target_id="target-1", quantity=Decimal("1")),
        P1OrderSubmitted(sequence=4, simulation_time=NOW, client_order_id="order-1", native_order_id="native-random-1", side="BUY", quantity=Decimal("1"), order_type="MARKET"),
        P1OrderAccepted(sequence=5, simulation_time=NOW, client_order_id="order-1", native_order_id="native-random-1"),
        P1Fill(sequence=6, simulation_time=NOW, client_order_id="order-1", native_fill_id="fill-random-1", side="BUY", quantity=Decimal("1"), price=Decimal("100"), fee=Decimal("0.1"), fee_currency="USDT"),
        P1PositionObserved(sequence=7, simulation_time=NOW, quantity=Decimal("1"), average_entry_price=Decimal("100"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("1")),
        P1AccountObserved(sequence=8, simulation_time=NOW, cash_balance=Decimal("999899.9"), fees=Decimal("0.1"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("1")),
        P1RunCompleted(sequence=9, simulation_time=NOW, runtime_family="cython-v1", engine_version="1.231.0", upstream_commit="b" * 40, closure_digest=DIGEST, target_count=1, order_count=1, fill_count=1, final_cash=Decimal("999899.9"), final_position=Decimal("1"), fees=Decimal("0.1"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("1")),
    )


def test_valid_event_stream_and_deterministic_message_ids() -> None:
    assert validate_event_stream(stream()) == stream()
    assert event_message_id(RUN_ID, stream()[0]) == event_message_id(RUN_ID, stream()[0])
    assert event_message_id(RUN_ID, stream()[0]) != event_message_id(RUN_ID, stream()[1])


@pytest.mark.parametrize(
    "events",
    (
        stream()[1:],
        stream()[:4] + stream()[5:],
        stream()[:6] + (stream()[5],) + stream()[6:],
        stream() + (stream()[-1],),
        stream()[:3] + (stream()[5],) + stream()[6:],
        stream()[:2] + stream()[3:],
    ),
)
def test_state_machine_rejects_illegal_streams(events: tuple) -> None:
    with pytest.raises(ValueError):
        validate_event_stream(events)


def test_state_machine_rejects_incompatible_completion_counters() -> None:
    events = stream()
    changed = events[:-1] + (events[-1].model_copy(update={"fill_count": 2}),)
    with pytest.raises(ValueError, match="counters"):
        validate_event_stream(changed)


def test_event_schema_rejects_unknown_wrong_origin_float_and_nonfinite() -> None:
    valid = stream()[5].model_dump(mode="json")
    for mutation in (
        {**valid, "unknown": True},
        {**valid, "origin": "CONTROL_PLANE"},
        {**valid, "price": 100.0},
        {**valid, "price": "NaN"},
        {**valid, "quantity": "-1"},
        {**valid, "fee": "-0.1"},
        {**valid, "client_order_id": "x" * 129},
    ):
        with pytest.raises(ValidationError):
            P1_EVENT_ADAPTER.validate_python(mutation)


def test_event_union_is_closed_and_discriminated() -> None:
    schema = TypeAdapter(type(stream()[0])).json_schema()
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError):
        P1_EVENT_ADAPTER.validate_python({"event_type": "FabricatedCallback"})
