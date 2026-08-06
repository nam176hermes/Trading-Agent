from __future__ import annotations

import json
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
)
from packages.event_ledger import ReplayError, serialize_event


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
