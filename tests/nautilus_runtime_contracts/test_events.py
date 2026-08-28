from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from packages.nautilus_runtime_contracts.events import (
    P1AccountObserved,
    P1Fill,
    P1OrderSubmitted,
    P1PositionObserved,
    P1RunCompleted,
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
    P1_EVENT_ADAPTER,
    event_message_id,
)
from packages.nautilus_runtime_contracts.semantic import semantic_digest
from packages.nautilus_runtime_contracts.state_machine import validate_event_stream


NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
DIGEST = "a" * 64


def stream() -> tuple:
    events = (
        P1RunStarted(schema_version="nautilus-p1-event-stream-v1", event_type="RunStarted", origin="CONTROL_PLANE", native_type=None, sequence=2, simulation_time=NOW, runtime_family="cython-v1", engine_version="1.231.0", upstream_commit="b" * 40, closure_digest=DIGEST, config_digest=DIGEST, catalog_digest=DIGEST, data_digest=DIGEST),
        P1TargetAccepted(schema_version="nautilus-p1-event-stream-v1", event_type="TargetAccepted", origin="CONTROL_PLANE", native_type=None, sequence=3, simulation_time=NOW, target_id="target-1", source_signal_ids=("signal-1",), target_weight=Decimal("1")),
        P1TargetQuantityPlanned(schema_version="nautilus-p1-event-stream-v1", event_type="TargetQuantityPlanned", origin="CONTROL_PLANE", native_type=None, sequence=4, simulation_time=NOW, target_id="target-1", quantity=Decimal("1")),
        P1OrderSubmitted(schema_version="nautilus-p1-event-stream-v1", event_type="OrderSubmitted", origin="CONTROL_PLANE", native_type="Order", sequence=5, simulation_time=NOW, client_order_id="order-1", native_order_id="native-random-1", target_id="target-1", source_signal_ids=("signal-1",), side="BUY", quantity=Decimal("1"), order_type="MARKET"),
        P1Fill(schema_version="nautilus-p1-event-stream-v1", event_type="Fill", origin="NAUTILUS_CALLBACK", native_type="OrderFilled", sequence=6, simulation_time=NOW, client_order_id="order-1", native_fill_id="fill-random-1", side="BUY", quantity=Decimal("1"), price=Decimal("100"), fee=Decimal("0.1"), fee_currency="USDT"),
        P1PositionObserved(schema_version="nautilus-p1-event-stream-v1", event_type="PositionObserved", origin="NAUTILUS_CACHE_OBSERVATION", native_type="Position", sequence=7, simulation_time=NOW, quantity=Decimal("1"), average_entry_price=Decimal("100"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("1")),
        P1AccountObserved(schema_version="nautilus-p1-event-stream-v1", event_type="AccountObserved", origin="NAUTILUS_CACHE_OBSERVATION", native_type="Account", sequence=8, simulation_time=NOW, cash_balance=Decimal("999899.9"), fees=Decimal("0.1"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("1")),
        P1RunCompleted(schema_version="nautilus-p1-event-stream-v1", event_type="RunCompleted", origin="CONTROL_PLANE", native_type=None, sequence=9, simulation_time=NOW, runtime_family="cython-v1", engine_version="1.231.0", upstream_commit="b" * 40, closure_digest=DIGEST, target_count=1, order_count=1, fill_count=1, final_cash=Decimal("999899.9"), final_position=Decimal("1"), fees=Decimal("0.1"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("1"), semantic_digest="0" * 64),
    )
    return events[:-1] + (
        events[-1].model_copy(update={"semantic_digest": semantic_digest(events)}),
    )


def test_valid_event_stream_and_deterministic_message_ids() -> None:
    assert validate_event_stream(stream()) == stream()
    assert event_message_id(RUN_ID, stream()[0]) == event_message_id(RUN_ID, stream()[0])
    assert event_message_id(RUN_ID, stream()[0]) != event_message_id(RUN_ID, stream()[1])


@pytest.mark.parametrize(
    "events",
    (
        stream()[1:],
        stream()[:5] + (stream()[4],) + stream()[5:],
        stream() + (stream()[-1],),
        stream()[:3] + (stream()[4],) + stream()[5:],
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


def test_state_machine_binds_lineage_chronology_and_native_identities() -> None:
    events = stream()
    regress = events[:5] + (
        events[5].model_copy(update={"simulation_time": NOW.replace(hour=11)}),
    ) + events[6:]
    lineage = events[:-1] + (
        events[-1].model_copy(update={"closure_digest": "c" * 64}),
    )
    duplicate_fill = events[:5] + (
        events[4].model_copy(update={"sequence": 6}),
    ) + tuple(event.model_copy(update={"sequence": event.sequence + 1}) for event in events[5:])
    for mutation in (regress, lineage, duplicate_fill):
        with pytest.raises(ValueError):
            validate_event_stream(mutation)


def _resequence_and_digest(events: tuple) -> tuple:
    resequenced = tuple(
        event.model_copy(update={"sequence": sequence})
        for sequence, event in enumerate(events, start=2)
    )
    return resequenced[:-1] + (
        resequenced[-1].model_copy(
            update={"semantic_digest": semantic_digest(resequenced)}
        ),
    )


def test_state_machine_rejects_unfinished_late_and_contradictory_native_state() -> None:
    events = stream()
    unfinished = _resequence_and_digest(
        events[:4]
        + events[5:-1]
        + (events[-1].model_copy(update={"fill_count": 0}),)
    )
    second_order = events[3].model_copy(
        update={"client_order_id": "order-2", "native_order_id": "native-random-2"}
    )
    second_fill = events[4].model_copy(
        update={"client_order_id": "order-2", "native_fill_id": "fill-random-2"}
    )
    late_native = _resequence_and_digest(
        events[:-1]
        + (second_order, second_fill)
        + (events[-1].model_copy(update={"order_count": 2, "fill_count": 2}),)
    )
    contradictory = list(events)
    contradictory[-3] = contradictory[-3].model_copy(
        update={"realized_pnl": Decimal("1")}
    )
    contradictory_stream = _resequence_and_digest(tuple(contradictory))
    for mutation in (unfinished, late_native, contradictory_stream):
        with pytest.raises(ValueError):
            validate_event_stream(mutation)


def test_event_schema_rejects_unknown_wrong_origin_float_and_nonfinite() -> None:
    valid = stream()[4].model_dump(mode="json")
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
            P1_EVENT_ADAPTER.validate_json(json.dumps(mutation))


def test_event_union_is_closed_and_discriminated() -> None:
    schema = TypeAdapter(type(stream()[0])).json_schema()
    assert schema["additionalProperties"] is False
    missing = stream()[0].model_dump(mode="json")
    missing.pop("origin")
    with pytest.raises(ValidationError):
        P1_EVENT_ADAPTER.validate_json(json.dumps(missing))
    with pytest.raises(ValidationError):
        P1_EVENT_ADAPTER.validate_python({"event_type": "FabricatedCallback"})
