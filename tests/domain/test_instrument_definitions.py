from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest
from pydantic import TypeAdapter

import packages.domain as domain
from packages.domain import (
    Currency,
    CurrencyType,
    InstrumentId,
    Money,
    OrderQuantity,
    Price,
    ProductType,
)


NOW = datetime(2026, 8, 5, 12, 30, tzinfo=UTC)


def _provenance(**changes: object) -> object:
    values: dict[str, object] = {
        "source_id": " alpaca-catalog ",
        "source_revision": " revision-42 ",
        "observed_at": NOW,
    }
    values.update(changes)
    return domain.InstrumentProvenance(**values)


def _definition(**changes: object) -> object:
    values: dict[str, object] = {
        "instrument_id": InstrumentId(
            "BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA"
        ),
        "raw_symbol": " btc/usd ",
        "asset_class": domain.AssetClass.CRYPTO,
        "base_currency": Currency.BTC,
        "quote_currency": Currency.USD,
        "settlement_currency": Currency.USD,
        "tick_size": Price(Decimal("0.05"), Currency.USD),
        "size_increment": OrderQuantity(Decimal("0.005"), precision=3),
        "minimum_quantity": OrderQuantity(Decimal("0.010"), precision=3),
        "maximum_quantity": OrderQuantity(Decimal("100.000"), precision=3),
        "minimum_notional": Money(Decimal("10.00"), Currency.USD),
        "maximum_notional": Money(Decimal("1000000.00"), Currency.USD),
        "multiplier": Decimal("1"),
        "margin": None,
        "session_calendar": " crypto-24x7 ",
        "provenance": _provenance(),
    }
    values.update(changes)
    return domain.InstrumentDefinition(**values)


def _constraints(**changes: object) -> object:
    return domain.InstrumentConstraints(definition=_definition(**changes))


def test_crypto_definition_is_canonical_immutable_and_complete() -> None:
    definition = _definition()

    assert definition.instrument_id.canonical == "crypto_spot:ALPACA:BTC-USD"
    assert definition.raw_symbol == " btc/usd "
    assert definition.asset_class is domain.AssetClass.CRYPTO
    assert definition.base_currency is Currency.BTC
    assert definition.quote_currency is Currency.USD
    assert definition.settlement_currency is Currency.USD
    assert definition.session_calendar == "CRYPTO-24X7"
    assert definition.provenance.source_id == "ALPACA-CATALOG"
    assert definition.provenance.source_revision == "REVISION-42"
    with pytest.raises(FrozenInstanceError):
        definition.raw_symbol = "BTCUSD"  # type: ignore[misc]


def test_equity_definition_has_no_base_currency() -> None:
    definition = _definition(
        instrument_id=InstrumentId("aapl", ProductType.EQUITY, "nasdaq"),
        raw_symbol="AAPL",
        asset_class=domain.AssetClass.EQUITY,
        base_currency=None,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("1"), precision=0),
        minimum_quantity=OrderQuantity(Decimal("1"), precision=0),
        maximum_quantity=OrderQuantity(Decimal("10000"), precision=0),
        minimum_notional=Money(Decimal("1.00"), Currency.USD),
        maximum_notional=Money(Decimal("1000000.00"), Currency.USD),
        session_calendar="XNYS",
    )

    assert definition.instrument_id.canonical == "equity:NASDAQ:AAPL"
    assert definition.base_currency is None


def test_raw_symbol_preserves_provider_spelling_without_affecting_identity() -> None:
    first = _definition(raw_symbol=" btc/usd ")
    second = _definition(raw_symbol="BTCUSD")

    assert first.raw_symbol != second.raw_symbol
    assert first.instrument_id == second.instrument_id
    assert first.instrument_id.canonical == second.instrument_id.canonical


@pytest.mark.parametrize(
    "raw_symbol",
    ["", "   ", "A" * 129, "BTC\nUSD", "BTC\tUSD", "BTC\x00USD", "BTC USD"],
)
def test_raw_symbol_rejects_empty_oversized_or_non_printable_ascii(
    raw_symbol: str,
) -> None:
    with pytest.raises(ValueError, match="raw_symbol"):
        _definition(raw_symbol=raw_symbol)


@pytest.mark.parametrize(
    ("product_type", "asset_class"),
    [
        (ProductType.CRYPTO_SPOT, "crypto"),
        (ProductType.CRYPTO_SPOT, "equity-class"),
        (ProductType.EQUITY, "crypto-class"),
    ],
)
def test_asset_class_must_be_typed_and_agree_with_product_type(
    product_type: ProductType, asset_class: object
) -> None:
    if asset_class == "equity-class":
        asset_class = domain.AssetClass.EQUITY
    elif asset_class == "crypto-class":
        asset_class = domain.AssetClass.CRYPTO
    with pytest.raises(ValueError, match="asset_class"):
        _definition(
            instrument_id=InstrumentId("BTC", product_type, "ALPACA"),
            asset_class=asset_class,
        )


def test_crypto_currency_rules_require_registered_distinct_base_and_quote() -> None:
    forged_btc = Currency("BTC", CurrencyType.CRYPTO, 8, "currency-v1")

    for changes in (
        {"base_currency": None},
        {"base_currency": Currency.USD},
        {"base_currency": forged_btc},
        {"quote_currency": "USD"},
        {"settlement_currency": Currency.USDT},
    ):
        with pytest.raises(ValueError, match="currency|settlement"):
            _definition(**changes)


def test_equity_rejects_a_currency_base() -> None:
    with pytest.raises(ValueError, match="base_currency"):
        _definition(
            instrument_id=InstrumentId("AAPL", ProductType.EQUITY, "NASDAQ"),
            asset_class=domain.AssetClass.EQUITY,
            base_currency=Currency.BTC,
        )


def test_provenance_is_normalized_immutable_and_utc_only() -> None:
    provenance = _provenance()

    assert provenance.observed_at is NOW
    with pytest.raises(FrozenInstanceError):
        provenance.source_revision = "NEW"  # type: ignore[misc]
    for observed_at in (
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(-timedelta(hours=4))),
        "2026-08-05T12:30:00Z",
    ):
        with pytest.raises(ValueError, match="UTC"):
            _provenance(observed_at=observed_at)


@pytest.mark.parametrize(
    ("initial", "maintenance"),
    [
        (Decimal("0.5"), Decimal("0.25")),
        (Decimal("1"), Decimal("1")),
    ],
)
def test_margin_requirements_accept_bounded_exact_rates(
    initial: Decimal, maintenance: Decimal
) -> None:
    margin = domain.MarginRequirements(initial, maintenance)

    assert margin.initial_margin_rate == initial
    assert margin.maintenance_margin_rate == maintenance
    assert _definition(margin=margin).margin is margin


@pytest.mark.parametrize(
    ("initial", "maintenance"),
    [
        (0.5, Decimal("0.25")),
        (Decimal("0.5"), 0.25),
        (Decimal("NaN"), Decimal("0.25")),
        (Decimal("0"), Decimal("0")),
        (Decimal("1.01"), Decimal("0.25")),
        (Decimal("0.25"), Decimal("0.5")),
        (Decimal("0.1234567890123456789"), Decimal("0.1")),
    ],
)
def test_margin_requirements_reject_invalid_bounds(
    initial: object, maintenance: object
) -> None:
    with pytest.raises(ValueError, match="margin"):
        domain.MarginRequirements(initial, maintenance)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_increment", OrderQuantity(Decimal("0.000"), precision=3)),
        ("minimum_quantity", OrderQuantity(Decimal("0.000"), precision=3)),
        ("maximum_quantity", OrderQuantity(Decimal("0.000"), precision=3)),
        ("minimum_notional", Money(Decimal("0.00"), Currency.USD)),
        ("maximum_notional", Money(Decimal("0.00"), Currency.USD)),
        ("multiplier", Decimal("0")),
    ],
)
def test_definition_rejects_non_positive_grids_limits_and_multiplier(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match="positive"):
        _definition(**{field: value})


def test_definition_rejects_non_monotonic_or_off_grid_limits() -> None:
    invalid_changes = (
        {
            "minimum_quantity": OrderQuantity(Decimal("2.000"), 3),
            "maximum_quantity": OrderQuantity(Decimal("1.000"), 3),
        },
        {"minimum_quantity": OrderQuantity(Decimal("0.011"), 3)},
        {"maximum_quantity": OrderQuantity(Decimal("100.001"), 3)},
        {
            "minimum_notional": Money(Decimal("20.00"), Currency.USD),
            "maximum_notional": Money(Decimal("10.00"), Currency.USD),
        },
        {
            "tick_size": Price(Decimal("0.03"), Currency.USD),
            "size_increment": OrderQuantity(Decimal("0.02"), 2),
            "minimum_quantity": OrderQuantity(Decimal("0.02"), 2),
            "maximum_quantity": OrderQuantity(Decimal("100.00"), 2),
            "minimum_notional": Money(Decimal("10.01"), Currency.USD),
        },
    )

    for changes in invalid_changes:
        with pytest.raises(ValueError, match="grid|maximum|minimum"):
            _definition(**changes)


def test_definition_requires_instrument_precision_and_quote_currency() -> None:
    with pytest.raises(ValueError, match="precision"):
        _definition(
            minimum_quantity=OrderQuantity(Decimal("0.0100"), precision=4)
        )
    with pytest.raises(ValueError, match="quote currency"):
        _definition(tick_size=Price(Decimal("0.05"), Currency.USDT))
    with pytest.raises(ValueError, match="quote currency"):
        _definition(minimum_notional=Money(Decimal("10"), Currency.USDT))


@pytest.mark.parametrize("multiplier", [1, 1.0, "1", True, Decimal("Infinity")])
def test_definition_rejects_non_decimal_or_non_finite_multiplier(
    multiplier: object,
) -> None:
    with pytest.raises(ValueError, match="multiplier"):
        _definition(multiplier=multiplier)


def test_definition_revalidates_forged_nested_values() -> None:
    forged_instrument = object.__new__(InstrumentId)
    object.__setattr__(forged_instrument, "symbol", "BTC/USD")
    object.__setattr__(forged_instrument, "product_type", ProductType.CRYPTO_SPOT)
    object.__setattr__(forged_instrument, "venue", "ALPACA")

    forged_tick = object.__new__(Price)
    object.__setattr__(forged_tick, "amount", Decimal("-0.05"))
    object.__setattr__(forged_tick, "currency", Currency.USD)

    with pytest.raises(ValueError, match="symbol"):
        _definition(instrument_id=forged_instrument)
    with pytest.raises(ValueError, match="positive"):
        _definition(tick_size=forged_tick)


def test_constraints_bind_definition_and_require_unsigned_order_quantity() -> None:
    constraints = _constraints()
    price = Price(Decimal("1000.00"), Currency.USD)
    quantity = OrderQuantity(Decimal("0.010"), precision=3)

    assert constraints.definition is not None
    assert constraints.validate_order(price=price, quantity=quantity) == (
        price,
        quantity,
    )
    with pytest.raises(ValueError, match="OrderQuantity"):
        constraints.validate_quantity(domain.Quantity(Decimal("0.010"), 3))


def test_constraints_round_trip_canonical_json() -> None:
    adapter = TypeAdapter(domain.InstrumentConstraints)
    constraints = _constraints()

    assert adapter.validate_json(adapter.dump_json(constraints)) == constraints


def test_constraints_reject_runtime_price_and_quantity_grid_violations() -> None:
    constraints = _constraints()

    with pytest.raises(ValueError, match="tick grid"):
        constraints.validate_price(Price(Decimal("1000.01"), Currency.USD))
    with pytest.raises(ValueError, match="size increment grid"):
        constraints.validate_quantity(OrderQuantity(Decimal("0.011"), 3))
    with pytest.raises(ValueError, match="minimum quantity"):
        constraints.validate_quantity(OrderQuantity(Decimal("0.005"), 3))
    with pytest.raises(ValueError, match="positive"):
        constraints.validate_quantity(OrderQuantity(Decimal("0.000"), 3))


def test_constraints_enforce_inclusive_quantity_and_notional_maxima() -> None:
    constraints = _constraints(
        maximum_quantity=OrderQuantity(Decimal("1.000"), 3),
        maximum_notional=Money(Decimal("1000.00"), Currency.USD),
    )
    boundary_price = Price(Decimal("1000.00"), Currency.USD)
    boundary_quantity = OrderQuantity(Decimal("1.000"), 3)

    assert constraints.validate_order(
        price=boundary_price, quantity=boundary_quantity
    ) == (boundary_price, boundary_quantity)
    with pytest.raises(ValueError, match="maximum quantity"):
        constraints.validate_quantity(OrderQuantity(Decimal("1.005"), 3))
    with pytest.raises(ValueError, match="maximum notional"):
        constraints.validate_order(
            price=Price(Decimal("1000.05"), Currency.USD),
            quantity=boundary_quantity,
        )


def test_notional_includes_multiplier_and_uses_exact_hostile_context_arithmetic() -> None:
    constraints = _constraints(
        tick_size=Price(Decimal("0.000000001"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.000000001"), 9),
        minimum_quantity=OrderQuantity(Decimal("0.000000001"), 9),
        maximum_quantity=OrderQuantity(Decimal("10.000000000"), 9),
        minimum_notional=Money(Decimal("1.00"), Currency.USD),
        maximum_notional=Money(Decimal("100.00"), Currency.USD),
        multiplier=Decimal("100000000"),
    )
    price = Price(Decimal("0.100000000"), Currency.USD)
    quantity = OrderQuantity(Decimal("0.000000001"), 9)

    with localcontext() as context:
        context.prec = 1
        with pytest.raises(ValueError, match="minimum notional"):
            constraints.validate_order(price=price, quantity=quantity)


def test_notional_rejects_inexact_quote_precision_without_rounding() -> None:
    constraints = _constraints(
        tick_size=Price(Decimal("0.001"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.001"), 3),
        minimum_quantity=OrderQuantity(Decimal("0.001"), 3),
        maximum_quantity=OrderQuantity(Decimal("100.000"), 3),
        minimum_notional=Money(Decimal("0.01"), Currency.USD),
        maximum_notional=Money(Decimal("1000.00"), Currency.USD),
    )

    with pytest.raises(ValueError, match="represented exactly"):
        constraints.validate_order(
            price=Price(Decimal("10.001"), Currency.USD),
            quantity=OrderQuantity(Decimal("0.001"), 3),
        )


def test_notional_rejects_out_of_range_exact_product() -> None:
    constraints = _constraints(
        tick_size=Price(Decimal("1"), Currency.USD),
        size_increment=OrderQuantity(Decimal("1"), 0),
        minimum_quantity=OrderQuantity(Decimal("1"), 0),
        maximum_quantity=OrderQuantity(Decimal("1E+125"), 0),
        minimum_notional=Money(Decimal("1"), Currency.USD),
        maximum_notional=Money(Decimal("1E+125"), Currency.USD),
    )

    with pytest.raises(ValueError, match="supported Decimal range"):
        constraints.validate_order(
            price=Price(Decimal("1E+125"), Currency.USD),
            quantity=OrderQuantity(Decimal("10"), 0),
        )
