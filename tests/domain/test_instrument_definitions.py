from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
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


def _forged_definition(**changes: object) -> object:
    valid = _definition()
    forged = object.__new__(domain.InstrumentDefinition)
    for field in fields(domain.InstrumentDefinition):
        object.__setattr__(
            forged,
            field.name,
            changes.get(field.name, getattr(valid, field.name)),
        )
    return forged


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


@pytest.mark.parametrize(
    "field",
    ["size_increment", "minimum_quantity", "maximum_quantity"],
)
def test_constraints_reject_signed_quantity_in_forged_definition(field: str) -> None:
    forged = _forged_definition(
        **{field: domain.Quantity(Decimal("0.010"), precision=3)}
    )

    with pytest.raises(ValueError, match=rf"{field} must be an OrderQuantity"):
        domain.InstrumentConstraints(definition=forged)


def test_constraints_fully_revalidate_forged_definition_currency_binding() -> None:
    forged = _forged_definition(settlement_currency=Currency.USDT)

    with pytest.raises(ValueError, match="settlement currency must equal quote currency"):
        domain.InstrumentConstraints(definition=forged)


def test_constraints_bind_a_fresh_exact_canonical_definition() -> None:
    supplied = _definition()

    constraints = domain.InstrumentConstraints(definition=supplied)

    assert constraints.definition == supplied
    assert constraints.definition is not supplied
    assert type(constraints.definition) is domain.InstrumentDefinition


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


@given(
    step_coefficient=st.integers(min_value=2, max_value=1_000_000).filter(
        lambda value: value % 10 != 0
    ),
    multiplier=st.integers(min_value=1, max_value=10_000),
    precision=st.integers(min_value=0, max_value=18),
)
@settings(max_examples=100, deadline=None, database=None)
def test_tick_grid_matches_exact_integer_multiples_across_precisions(
    step_coefficient: int, multiplier: int, precision: int
) -> None:
    tick_amount = Decimal(step_coefficient).scaleb(-precision)
    maximum_notional = Decimal(step_coefficient * 10_000).scaleb(-precision)
    constraints = _constraints(
        instrument_id=InstrumentId(
            "BTC-ETH", ProductType.CRYPTO_SPOT, "ALPACA"
        ),
        quote_currency=Currency.ETH,
        settlement_currency=Currency.ETH,
        tick_size=Price(tick_amount, Currency.ETH),
        size_increment=OrderQuantity(Decimal("1"), 0),
        minimum_quantity=OrderQuantity(Decimal("1"), 0),
        maximum_quantity=OrderQuantity(Decimal("10000"), 0),
        minimum_notional=Money(tick_amount, Currency.ETH),
        maximum_notional=Money(maximum_notional, Currency.ETH),
    )
    on_grid = Decimal(step_coefficient * multiplier).scaleb(-precision)
    off_grid = Decimal((step_coefficient * multiplier) + 1).scaleb(-precision)

    assert constraints.validate_price(Price(on_grid, Currency.ETH)).amount == on_grid
    with pytest.raises(ValueError, match="tick grid"):
        constraints.validate_price(Price(off_grid, Currency.ETH))


@given(
    step_coefficient=st.integers(min_value=2, max_value=1_000_000),
    multiplier=st.integers(min_value=1, max_value=10_000),
    precision=st.integers(min_value=0, max_value=18),
)
@settings(max_examples=100, deadline=None, database=None)
def test_size_grid_matches_exact_integer_multiples_across_precisions(
    step_coefficient: int, multiplier: int, precision: int
) -> None:
    increment_value = Decimal(step_coefficient).scaleb(-precision)
    maximum_value = Decimal(step_coefficient * 10_000).scaleb(-precision)
    increment = OrderQuantity(increment_value, precision)
    constraints = _constraints(
        instrument_id=InstrumentId(
            "BTC-ETH", ProductType.CRYPTO_SPOT, "ALPACA"
        ),
        quote_currency=Currency.ETH,
        settlement_currency=Currency.ETH,
        tick_size=Price(Decimal("1"), Currency.ETH),
        size_increment=increment,
        minimum_quantity=increment,
        maximum_quantity=OrderQuantity(maximum_value, precision),
        minimum_notional=Money(increment_value, Currency.ETH),
        maximum_notional=Money(maximum_value, Currency.ETH),
    )
    on_grid = OrderQuantity(
        Decimal(step_coefficient * multiplier).scaleb(-precision),
        precision,
    )
    off_grid = OrderQuantity(
        Decimal((step_coefficient * multiplier) + 1).scaleb(-precision),
        precision,
    )

    assert constraints.validate_quantity(on_grid) == on_grid
    with pytest.raises(ValueError, match="size increment grid"):
        constraints.validate_quantity(off_grid)


@given(
    price_coefficient=st.integers(min_value=2, max_value=1_000_000),
    quantity_coefficient=st.integers(min_value=1, max_value=1_000_000),
    multiplier_coefficient=st.integers(min_value=1, max_value=100),
    price_precision=st.integers(min_value=0, max_value=6),
    quantity_precision=st.integers(min_value=0, max_value=6),
)
@settings(max_examples=75, deadline=None, database=None)
def test_multiplier_notional_product_has_exact_inclusive_boundaries(
    price_coefficient: int,
    quantity_coefficient: int,
    multiplier_coefficient: int,
    price_precision: int,
    quantity_precision: int,
) -> None:
    price_amount = Decimal(price_coefficient).scaleb(-price_precision)
    quantity_value = Decimal(quantity_coefficient).scaleb(-quantity_precision)
    exact_notional = Decimal(
        price_coefficient * quantity_coefficient * multiplier_coefficient
    ).scaleb(-(price_precision + quantity_precision))
    quantity = OrderQuantity(quantity_value, quantity_precision)
    constraints = _constraints(
        instrument_id=InstrumentId(
            "BTC-ETH", ProductType.CRYPTO_SPOT, "ALPACA"
        ),
        quote_currency=Currency.ETH,
        settlement_currency=Currency.ETH,
        tick_size=Price(Decimal(1).scaleb(-price_precision), Currency.ETH),
        size_increment=OrderQuantity(
            Decimal(1).scaleb(-quantity_precision), quantity_precision
        ),
        minimum_quantity=quantity,
        maximum_quantity=quantity,
        minimum_notional=Money(exact_notional, Currency.ETH),
        maximum_notional=Money(exact_notional, Currency.ETH),
        multiplier=Decimal(multiplier_coefficient),
    )
    price = Price(price_amount, Currency.ETH)

    with localcontext() as context:
        context.prec = 1
        assert constraints.validate_order(price=price, quantity=quantity) == (
            price,
            quantity,
        )

    above_boundary = Price(
        Decimal(price_coefficient + 1).scaleb(-price_precision), Currency.ETH
    )
    with pytest.raises(ValueError, match="maximum notional"):
        constraints.validate_order(price=above_boundary, quantity=quantity)
