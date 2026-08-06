from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    AssetClass,
    Currency,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    Money,
    OrderQuantity,
    OrderSide,
    Price,
    ProductType,
    ReconciliationSource,
    LiquiditySide,
    EventEnvelope,
    validate_event_batch,
    validate_fill_report_batch,
)
from packages.event_ledger import ReplayError, reduce_events, replay, serialize_event
from packages.domain.events import (
    _canonical_event_envelope,
    _registered_event_type,
    _registered_payload_type,
    validate_execution_report_events,
)
from packages.domain.orders import (
    _canonical_instrument_definition,
    _canonical_money,
    _canonical_order_quantity,
    _canonical_price,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


def definition() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA"),
        raw_symbol="BTCUSD",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.01"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.01"), 2),
        maximum_quantity=OrderQuantity(Decimal("100"), 2),
        minimum_notional=Money(Decimal("1"), Currency.USD),
        maximum_notional=Money(Decimal("100000"), Currency.USD),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar="24X7",
        provenance=InstrumentProvenance(
            source_id="catalog", source_revision="r1", observed_at=NOW
        ),
    )


def report(**changes: object) -> FillEvent:
    values: dict[str, object] = {
        "execution_id": uid(1),
        "order_id": uid(2),
        "report_sequence": 1,
        "venue_trade_id": "trade-1",
        "instrument_definition": definition(),
        "side": OrderSide.BUY,
        "liquidity_side": LiquiditySide.MAKER,
        "status": FillReportStatus.PARTIALLY_FILLED,
        "quantity": OrderQuantity(Decimal("1"), 2),
        "cumulative_fill_quantity": OrderQuantity(Decimal("1"), 2),
        "leaves_quantity": OrderQuantity(Decimal("1.5"), 2),
        "order_quantity": OrderQuantity(Decimal("2.5"), 2),
        "last_fill_price": Price(Decimal("100"), Currency.USD),
        "average_fill_price": Price(Decimal("100"), Currency.USD),
        "commission": Money(Decimal("0.01"), Currency.USD),
        "reconciliation_source": ReconciliationSource.VENUE,
        "duplicate_of_execution_id": None,
        "correction_of_execution_id": None,
        "bust_of_execution_id": None,
        "filled_at": NOW,
        "schema_version": "2.0",
    }
    values.update(changes)
    return FillEvent(**values)


def test_execution_report_has_canonical_v2_round_trip_and_required_execution_data() -> None:
    original = report()

    serialized = json.loads(original.model_dump_json())
    restored = FillEvent.model_validate_json(json.dumps(serialized))

    assert restored == original
    assert serialized["schema_version"] == "2.0"
    assert serialized["quantity"]["value"] == "1"
    assert serialized["cumulative_fill_quantity"]["value"] == "1"
    assert serialized["commission"] == {"amount": "0.01", "currency": "USD"}
    assert serialized["instrument_definition"]["quote_currency"] == "USD"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("quantity", OrderQuantity(Decimal("0"), 2), "quantity must be positive"),
        (
            "cumulative_fill_quantity",
            OrderQuantity(Decimal("0.5"), 2),
            "cumulative_fill_quantity",
        ),
        ("leaves_quantity", OrderQuantity(Decimal("1.505"), 3), "precision"),
        ("last_fill_price", Price(Decimal("100"), Currency.USDT), "quote currency"),
        ("average_fill_price", Price(Decimal("100"), Currency.USDT), "quote currency"),
        ("commission", Money(Decimal("-0.01"), Currency.USD), "commission"),
        ("report_sequence", 0, "report_sequence"),
        ("venue_trade_id", " trade", "venue_trade_id"),
        ("filled_at", NOW.astimezone(timezone(timedelta(hours=-4))), "UTC"),
    ],
)
def test_execution_report_rejects_invalid_values(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        report(**{field: value})


@pytest.mark.parametrize(
    ("status", "changes"),
    [
        (FillReportStatus.FILLED, {"leaves_quantity": OrderQuantity(Decimal("0.01"), 2)}),
        (FillReportStatus.DUPLICATE, {}),
        (FillReportStatus.CORRECTION, {}),
        (FillReportStatus.BUST, {}),
        (
            FillReportStatus.PARTIALLY_FILLED,
            {"duplicate_of_execution_id": uid(3)},
        ),
        (
            FillReportStatus.DUPLICATE,
            {
                "duplicate_of_execution_id": uid(3),
                "correction_of_execution_id": uid(4),
            },
        ),
        (
            FillReportStatus.CORRECTION,
            {"correction_of_execution_id": uid(1)},
        ),
        (FillReportStatus.BUST, {"bust_of_execution_id": uid(1)}),
    ],
)
def test_execution_report_requires_coherent_status_leaves_and_identity_references(
    status: FillReportStatus, changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        report(status=status, **changes)


@pytest.mark.parametrize(
    ("status", "reference_field"),
    [
        (FillReportStatus.DUPLICATE, "duplicate_of_execution_id"),
        (FillReportStatus.CORRECTION, "correction_of_execution_id"),
        (FillReportStatus.BUST, "bust_of_execution_id"),
    ],
)
def test_execution_report_accepts_exactly_one_matching_identity_reference(
    status: FillReportStatus, reference_field: str
) -> None:
    assert report(status=status, **{reference_field: uid(3)}).status is status


def test_execution_report_rejects_incoherent_cumulative_total_and_precision() -> None:
    with pytest.raises(ValidationError, match="order_quantity"):
        report(order_quantity=OrderQuantity(Decimal("2.49"), 2))
    with pytest.raises(ValidationError, match="precision"):
        report(quantity=OrderQuantity(Decimal("1"), 1))


def test_execution_report_json_rejects_float_legacy_shape_unknown_enums_and_non_utc_timestamp() -> None:
    payload = json.loads(report().model_dump_json())
    payload["quantity"]["value"] = 1.0
    with pytest.raises(ValidationError):
        FillEvent.model_validate_json(json.dumps(payload))

    payload = json.loads(report().model_dump_json())
    payload["liquidity_side"] = "unknown"
    with pytest.raises(ValidationError):
        FillEvent.model_validate_json(json.dumps(payload))

    payload = json.loads(report().model_dump_json())
    payload["filled_at"] = "2026-07-20T08:00:00-04:00"
    with pytest.raises(ValidationError, match="UTC"):
        FillEvent.model_validate_json(json.dumps(payload))

    legacy = {
        "fill_id": str(uid(1)),
        "order_id": str(uid(2)),
        "instrument": {"symbol": "BTC-USD", "product_type": "crypto_spot", "venue": "ALPACA"},
        "side": "buy",
        "quantity": {"value": "1", "precision": 0},
        "price": {"amount": "100", "currency": "USD"},
        "fees": {"amount": "0", "currency": "USD"},
        "filled_at": "2026-07-20T12:00:00Z",
        "schema_version": "1.0",
    }
    with pytest.raises(ValidationError):
        FillEvent.model_validate_json(json.dumps(legacy))


def test_execution_report_revalidates_forged_models_at_model_and_serialization_ingress() -> None:
    original = report()
    forged_copy = original.model_copy(
        update={"commission": Money(Decimal("-0.01"), Currency.USD)}
    )
    forged_values = {
        name: getattr(original, name)
        for name in FillEvent.model_fields
    }
    forged_values["leaves_quantity"] = OrderQuantity(Decimal("9"), 2)
    forged_construct = FillEvent.model_construct(**forged_values)

    for forged in (forged_copy, forged_construct):
        with pytest.raises(ValidationError):
            FillEvent.model_validate(forged)
        with pytest.raises(ValueError):
            forged.model_dump()
        with pytest.raises(ValueError):
            forged.model_dump_json()


def test_execution_report_revalidates_forged_payloads_at_envelope_and_ledger_ingress() -> None:
    original = report()
    forged = original.model_copy(
        update={"commission": Money(Decimal("-0.01"), Currency.USD)}
    )
    envelope_values = {
        "event_id": uid(10),
        "event_type": "FillEvent",
        "schema_version": "1.0",
        "source": "domain-test",
        "stream_id": uid(11),
        "sequence": 1,
        "observed_at": NOW,
        "ingested_at": NOW + timedelta(seconds=1),
        "produced_at": NOW + timedelta(seconds=2),
        "effective_at": NOW + timedelta(seconds=2),
        "expires_at": NOW + timedelta(minutes=5),
        "correlation_id": uid(12),
        "causation_id": uid(13),
        "trace_id": uid(14),
    }

    with pytest.raises(ValidationError):
        EventEnvelope[FillEvent](**envelope_values, payload=forged)

    valid = EventEnvelope[FillEvent](**envelope_values, payload=original)
    with pytest.raises(ReplayError):
        serialize_event(valid.model_copy(update={"payload": forged}))


def test_typed_envelope_revalidates_forged_execution_reports_at_every_public_ingress() -> None:
    valid = fill_envelope(report(), event_id=uid(20), stream_id=uid(21), sequence=1)
    forged_payload = valid.payload.model_copy(
        update={"commission": Money(Decimal("-0.01"), Currency.USD)}
    )
    forged_copy = valid.model_copy(update={"payload": forged_payload})
    forged_values = {
        name: getattr(valid, name)
        for name in EventEnvelope[FillEvent].model_fields
    }
    forged_values["payload"] = forged_payload
    forged_construct = EventEnvelope[FillEvent].model_construct(**forged_values)

    for forged in (forged_copy, forged_construct):
        with pytest.raises(ValidationError):
            EventEnvelope[FillEvent].model_validate(forged)
        with pytest.raises(ValueError):
            forged.model_dump()
        with pytest.raises(ValueError):
            forged.model_dump_json()
        with pytest.raises(ReplayError):
            serialize_event(forged)
        with pytest.raises(ReplayError):
            replay((forged,))


def test_execution_report_batch_rejects_duplicate_identity_and_non_increasing_per_order_sequence() -> None:
    first = report()
    second = report(
        execution_id=uid(30),
        report_sequence=2,
        quantity=OrderQuantity(Decimal("1"), 2),
        cumulative_fill_quantity=OrderQuantity(Decimal("2"), 2),
        leaves_quantity=OrderQuantity(Decimal("0.5"), 2),
    )

    assert validate_fill_report_batch((first, second)) == (first, second)
    with pytest.raises(ValueError, match="duplicate execution_id"):
        validate_fill_report_batch((first, report(order_id=uid(99))))
    with pytest.raises(ValueError, match="duplicate report_sequence"):
        validate_fill_report_batch((first, report(execution_id=uid(31))))
    assert validate_fill_report_batch((second, first)) == (first, second)


def test_event_batch_and_replay_reject_per_order_execution_report_sequence_across_streams() -> None:
    first = fill_envelope(report(), event_id=uid(40), stream_id=uid(41), sequence=1)
    duplicate_sequence = fill_envelope(
        report(execution_id=uid(42)), event_id=uid(43), stream_id=uid(44), sequence=1
    )

    for events in ((first, duplicate_sequence), (duplicate_sequence, first)):
        with pytest.raises(ValueError, match="duplicate report_sequence"):
            validate_event_batch(events)
        with pytest.raises(ReplayError, match="execution report"):
            replay(events)

    duplicate_execution = fill_envelope(
        report(order_id=uid(45)), event_id=uid(46), stream_id=uid(47), sequence=1
    )
    with pytest.raises(ValueError, match="duplicate execution_id"):
        validate_event_batch((first, duplicate_execution))
    with pytest.raises(ReplayError, match="execution report"):
        replay((first, duplicate_execution))


def test_cross_order_execution_reports_retain_deterministic_ledger_replay() -> None:
    first = fill_envelope(report(), event_id=uid(50), stream_id=uid(51), sequence=1)
    other_order = fill_envelope(
        report(execution_id=uid(52), order_id=uid(53)),
        event_id=uid(54),
        stream_id=uid(55),
        sequence=1,
    )

    assert validate_event_batch((first, other_order)) == (first, other_order)
    assert replay((first, other_order)) == replay((other_order, first))


def test_same_order_execution_reports_have_intrinsic_sequence_chronology_across_streams() -> None:
    first = report()
    second = report(
        execution_id=uid(60),
        report_sequence=2,
        quantity=OrderQuantity(Decimal("1"), 2),
        cumulative_fill_quantity=OrderQuantity(Decimal("2"), 2),
        leaves_quantity=OrderQuantity(Decimal("0.5"), 2),
    )
    first_envelope = fill_envelope(
        first, event_id=uid(61), stream_id=uid(62), sequence=1
    )
    second_envelope = fill_envelope(
        second, event_id=uid(63), stream_id=uid(64), sequence=1
    )

    assert validate_fill_report_batch((first, second)) == (first, second)
    assert validate_fill_report_batch((second, first)) == (first, second)
    assert reduce_events((first_envelope, second_envelope)) == reduce_events(
        (second_envelope, first_envelope)
    )
    assert replay((first_envelope, second_envelope)) == replay(
        (second_envelope, first_envelope)
    )


def test_execution_report_canonical_nested_helpers_fail_closed_for_wrong_and_forged_values() -> None:
    helpers = (
        (_canonical_instrument_definition, "instrument_definition", object()),
        (_canonical_order_quantity, "quantity", object()),
        (_canonical_price, "last_fill_price", object()),
        (_canonical_money, "commission", object()),
    )
    for helper, field_name, invalid in helpers:
        with pytest.raises(ValueError):
            if helper is _canonical_instrument_definition:
                helper(invalid)
            else:
                helper(invalid, field_name)

    forged_values = (
        (_canonical_instrument_definition, object.__new__(InstrumentDefinition), None),
        (_canonical_order_quantity, object.__new__(OrderQuantity), "quantity"),
        (_canonical_price, object.__new__(Price), "last_fill_price"),
        (_canonical_money, object.__new__(Money), "commission"),
    )
    for helper, forged, field_name in forged_values:
        with pytest.raises(ValueError):
            if field_name is None:
                helper(forged)
            else:
                helper(forged, field_name)


def test_execution_report_rejects_grid_minimum_and_terminal_leaves_integrity_branches() -> None:
    grid_definition = replace(
        definition(),
        size_increment=OrderQuantity(Decimal("0.02"), 2),
        minimum_quantity=OrderQuantity(Decimal("0.02"), 2),
    )
    with pytest.raises(ValidationError, match="size increment grid"):
        report(
            instrument_definition=grid_definition,
            leaves_quantity=OrderQuantity(Decimal("1.51"), 2),
        )
    minimum_definition = replace(
        definition(), minimum_quantity=OrderQuantity(Decimal("0.02"), 2)
    )
    with pytest.raises(ValidationError, match="order_quantity is invalid"):
        report(
            instrument_definition=minimum_definition,
            quantity=OrderQuantity(Decimal("0.01"), 2),
            cumulative_fill_quantity=OrderQuantity(Decimal("0.01"), 2),
            leaves_quantity=OrderQuantity(Decimal("0"), 2),
            order_quantity=OrderQuantity(Decimal("0.01"), 2),
        )
    with pytest.raises(ValidationError, match="terminal FILLED"):
        report(status=FillReportStatus.FILLED)


def test_execution_report_and_envelope_runtime_integrity_helpers_reject_malformed_instances() -> None:
    with pytest.raises(ValidationError):
        FillEvent.model_validate(FillEvent.model_construct())
    with pytest.raises(ValidationError):
        EventEnvelope[FillEvent].model_validate(EventEnvelope[FillEvent].model_construct())
    with pytest.raises(ValueError):
        _canonical_event_envelope(object())
    with pytest.raises(ValueError):
        _registered_event_type(object())
    with pytest.raises(ValueError):
        _registered_payload_type(object())
    with pytest.raises(ValueError):
        _registered_payload_type("Unregistered")
    runtime_marker = object()
    assert validate_execution_report_events((runtime_marker,)) == (runtime_marker,)
    schema = EventEnvelope[FillEvent].model_json_schema()
    assert schema["properties"]["event_type"] == {"const": "FillEvent", "type": "string"}


def fill_envelope(
    payload: FillEvent, *, event_id: UUID, stream_id: UUID, sequence: int
) -> EventEnvelope[FillEvent]:
    return EventEnvelope[FillEvent](
        event_id=event_id,
        event_type="FillEvent",
        schema_version="1.0",
        source="domain-test",
        stream_id=stream_id,
        sequence=sequence,
        observed_at=NOW,
        ingested_at=NOW + timedelta(seconds=1),
        produced_at=NOW + timedelta(seconds=2),
        effective_at=NOW + timedelta(seconds=2),
        expires_at=NOW + timedelta(minutes=5),
        correlation_id=uid(56),
        causation_id=uid(57),
        trace_id=uid(58),
        payload=payload,
    )
