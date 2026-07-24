from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, MAX_EMAX, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter

from packages.domain import (
    Currency,
    InstrumentConstraints,
    InstrumentId,
    Money,
    Price,
    ProductType,
    Quantity,
)


def test_instrument_normalizes_ascii_whitespace_and_case() -> None:
    instrument = InstrumentId(
        symbol=" \tbtc-usdt\n",
        product_type=ProductType.CRYPTO_SPOT,
        venue=" alpaca ",
    )

    assert instrument.symbol == "BTC-USDT"
    assert instrument.venue == "ALPACA"
    assert instrument.canonical == "crypto_spot:ALPACA:BTC-USDT"
    with pytest.raises(FrozenInstanceError):
        instrument.symbol = "ETH-USDT"  # type: ignore[misc]


@pytest.mark.parametrize("symbol", ["", "BTC USDT", "BTC/USDT", "BTC:USDT", "BTC\u00a0USDT", "A" * 33])
def test_instrument_rejects_unsafe_or_oversized_symbols(symbol: str) -> None:
    with pytest.raises(ValueError, match="symbol"):
        InstrumentId(symbol=symbol, product_type=ProductType.CRYPTO_SPOT, venue="BINANCE")


@pytest.mark.parametrize("venue", ["", "ALPACA!", "ALPACA:US", "ALPACA\u00a0", "V" * 33])
def test_instrument_rejects_unsafe_or_oversized_venues(venue: str) -> None:
    with pytest.raises(ValueError, match="venue"):
        InstrumentId(symbol="AAPL", product_type=ProductType.EQUITY, venue=venue)


def test_instrument_validates_product_type_enum() -> None:
    with pytest.raises(ValueError, match="ProductType"):
        InstrumentId(symbol="AAPL", product_type="equity", venue="ALPACA")  # type: ignore[arg-type]


def test_instrument_canonical_form_is_collision_resistant() -> None:
    crypto = InstrumentId("BTC", ProductType.CRYPTO_SPOT, "ALPACA")
    equity = InstrumentId("BTC", ProductType.EQUITY, "ALPACA")
    alternate_venue = InstrumentId("BTC", ProductType.CRYPTO_SPOT, "COINBASE")

    assert len({crypto.canonical, equity.canonical, alternate_venue.canonical}) == 3


def _constraints(**changes: object) -> InstrumentConstraints:
    values: dict[str, object] = {
        "instrument": InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA"),
        "tick_size": Price(Decimal("0.05"), Currency.USD),
        "lot_size": Quantity(Decimal("0.005"), precision=3),
        "minimum_quantity": Quantity(Decimal("0.01"), precision=3),
        "minimum_notional": Money(Decimal("10"), Currency.USD),
    }
    values.update(changes)
    return InstrumentConstraints(**values)  # type: ignore[arg-type]


def test_constraints_accept_exact_quantity_and_notional_boundaries() -> None:
    constraints = _constraints()
    price = Price(Decimal("1000"), Currency.USD)
    quantity = Quantity(Decimal("0.01"), precision=3)

    assert constraints.validate_order(price=price, quantity=quantity) == (price, quantity)
    with pytest.raises(FrozenInstanceError):
        constraints.tick_size = Price(Decimal("0.01"), Currency.USD)  # type: ignore[misc]


def test_constraints_round_trip_canonical_json() -> None:
    adapter = TypeAdapter(InstrumentConstraints)
    constraints = _constraints()

    assert adapter.validate_json(adapter.dump_json(constraints)) == constraints


@pytest.mark.parametrize("amount", [Decimal("1000.01"), Decimal("999.99")])
def test_constraints_reject_price_off_tick_grid(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="tick grid"):
        _constraints().validate_price(Price(amount, Currency.USD))


def test_constraints_reject_quantity_off_lot_grid() -> None:
    with pytest.raises(ValueError, match="lot grid"):
        _constraints().validate_quantity(Quantity(Decimal("0.011"), precision=3))


def test_constraints_reject_quantity_below_minimum() -> None:
    with pytest.raises(ValueError, match="minimum quantity"):
        _constraints().validate_quantity(Quantity(Decimal("0.005"), precision=3))


def test_constraints_reject_notional_below_minimum() -> None:
    with pytest.raises(ValueError, match="minimum notional"):
        _constraints().validate_order(
            price=Price(Decimal("999.95"), Currency.USD),
            quantity=Quantity(Decimal("0.01"), precision=3),
        )


def test_constraints_notional_check_ignores_ambient_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 2
        with pytest.raises(ValueError, match="minimum notional"):
            _constraints().validate_order(
                price=Price(Decimal("999.95"), Currency.USD),
                quantity=Quantity(Decimal("0.01"), precision=3),
            )


def test_constraints_fail_closed_when_notional_exceeds_decimal_range() -> None:
    constraints = _constraints(
        tick_size=Price(Decimal("1"), Currency.USD),
        lot_size=Quantity(Decimal("1"), precision=0),
        minimum_quantity=Quantity(Decimal("1"), precision=0),
        minimum_notional=Money(Decimal("1"), Currency.USD),
    )

    with pytest.raises(ValueError, match="supported Decimal range"):
        constraints.validate_order(
            price=Price(Decimal(f"1E+{MAX_EMAX}"), Currency.USD),
            quantity=Quantity(Decimal("10"), precision=0),
        )


def test_constraints_accept_grid_values_with_exponent_notation() -> None:
    price = Price(Decimal("1E+3"), Currency.USD)
    quantity = Quantity(Decimal("1E-2"), precision=3)

    assert _constraints().validate_order(price=price, quantity=quantity) == (
        price,
        quantity,
    )


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.005")])
def test_constraints_reject_zero_or_negative_quantity(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        _constraints().validate_quantity(Quantity(amount, precision=3))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lot_size", Quantity(Decimal("0"), precision=3)),
        ("lot_size", Quantity(Decimal("-0.005"), precision=3)),
        ("minimum_quantity", Quantity(Decimal("0"), precision=3)),
        ("minimum_quantity", Quantity(Decimal("-0.01"), precision=3)),
        ("minimum_notional", Money(Decimal("0"), Currency.USD)),
        ("minimum_notional", Money(Decimal("-10"), Currency.USD)),
    ],
)
def test_constraints_reject_non_positive_limits(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="positive"):
        _constraints(**{field: value})


def test_constraints_reject_minimum_quantity_off_lot_grid() -> None:
    with pytest.raises(ValueError, match="minimum quantity.*lot grid"):
        _constraints(minimum_quantity=Quantity(Decimal("0.011"), precision=3))


def test_constraints_reject_quantity_precision_overflow() -> None:
    with pytest.raises(ValueError, match="precision"):
        _constraints().validate_quantity(Quantity(Decimal("0.01"), precision=4))
    with pytest.raises(ValueError, match="precision"):
        _constraints(minimum_quantity=Quantity(Decimal("0.01"), precision=4))


@pytest.mark.parametrize("currency", [Currency.USDT])
def test_constraints_reject_price_currency_mismatch(currency: Currency) -> None:
    with pytest.raises(ValueError, match="currency"):
        _constraints().validate_price(Price(Decimal("1000"), currency))


def test_constraints_require_tick_and_notional_currency_match() -> None:
    with pytest.raises(ValueError, match="currency"):
        _constraints(minimum_notional=Money(Decimal("10"), Currency.USDT))


@given(
    step_coefficient=st.integers(min_value=2, max_value=1_000_000),
    multiplier=st.integers(min_value=1, max_value=10_000),
    precision=st.integers(min_value=0, max_value=18),
)
@settings(max_examples=100, deadline=None, database=None)
def test_tick_grid_matches_exact_integer_multiples(
    step_coefficient: int, multiplier: int, precision: int
) -> None:
    tick = Decimal(step_coefficient).scaleb(-precision)
    constraints = _constraints(tick_size=Price(tick, Currency.USD))
    on_grid = Decimal(step_coefficient * multiplier).scaleb(-precision)
    off_grid = Decimal((step_coefficient * multiplier) + 1).scaleb(-precision)

    assert constraints.validate_price(Price(on_grid, Currency.USD)).amount == on_grid
    with pytest.raises(ValueError, match="tick grid"):
        constraints.validate_price(Price(off_grid, Currency.USD))


@given(
    step_coefficient=st.integers(min_value=2, max_value=1_000_000),
    multiplier=st.integers(min_value=1, max_value=10_000),
    precision=st.integers(min_value=0, max_value=18),
)
@settings(max_examples=100, deadline=None, database=None)
def test_lot_grid_matches_exact_integer_multiples(
    step_coefficient: int, multiplier: int, precision: int
) -> None:
    lot_value = Decimal(step_coefficient).scaleb(-precision)
    lot_size = Quantity(lot_value, precision=precision)
    constraints = _constraints(lot_size=lot_size, minimum_quantity=lot_size)
    on_grid = Quantity(
        Decimal(step_coefficient * multiplier).scaleb(-precision),
        precision=precision,
    )
    off_grid = Quantity(
        Decimal((step_coefficient * multiplier) + 1).scaleb(-precision),
        precision=precision,
    )

    assert constraints.validate_quantity(on_grid) == on_grid
    with pytest.raises(ValueError, match="lot grid"):
        constraints.validate_quantity(off_grid)


@given(
    price_coefficient=st.integers(min_value=1, max_value=1_000_000),
    quantity_coefficient=st.integers(min_value=1, max_value=1_000_000),
    price_precision=st.integers(min_value=0, max_value=9),
    quantity_precision=st.integers(min_value=0, max_value=9),
)
@settings(max_examples=75, deadline=None, database=None)
def test_minimum_notional_uses_exact_decimal_product(
    price_coefficient: int,
    quantity_coefficient: int,
    price_precision: int,
    quantity_precision: int,
) -> None:
    price = Price(Decimal(price_coefficient).scaleb(-price_precision), Currency.USD)
    quantity = Quantity(
        Decimal(quantity_coefficient).scaleb(-quantity_precision),
        precision=quantity_precision,
    )
    tick = Price(Decimal(1).scaleb(-price_precision), Currency.USD)
    lot = Quantity(Decimal(1).scaleb(-quantity_precision), quantity_precision)
    product_coefficient = price_coefficient * quantity_coefficient
    product_precision = price_precision + quantity_precision
    exact_notional = Decimal(product_coefficient).scaleb(-product_precision)

    exact = _constraints(
        tick_size=tick,
        lot_size=lot,
        minimum_quantity=lot,
        minimum_notional=Money(exact_notional, Currency.USD),
    )
    assert exact.validate_order(price=price, quantity=quantity) == (price, quantity)

    above_exact = _constraints(
        tick_size=tick,
        lot_size=lot,
        minimum_quantity=lot,
        minimum_notional=Money(
            Decimal(product_coefficient + 1).scaleb(-product_precision),
            Currency.USD,
        ),
    )
    with pytest.raises(ValueError, match="minimum notional"):
        above_exact.validate_order(price=price, quantity=quantity)
