from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from packages.domain import Currency, Money, Price, Quantity


def test_money_accepts_signed_decimal_amount_and_is_immutable() -> None:
    money = Money(amount=Decimal("-12.34"), currency=Currency.USD)

    assert money.amount == Decimal("-12.34")
    assert money.currency is Currency.USD
    with pytest.raises(FrozenInstanceError):
        money.amount = Decimal("1")  # type: ignore[misc]


def test_price_accepts_positive_decimal_and_is_immutable() -> None:
    price = Price(amount=Decimal("0.000100"), currency=Currency.USDT)

    assert price.amount == Decimal("0.000100")
    with pytest.raises(FrozenInstanceError):
        price.currency = Currency.USD  # type: ignore[misc]


@pytest.mark.parametrize("amount", [1.0, 1, True, "1"])
@pytest.mark.parametrize("value_type", [Money, Price])
def test_money_and_price_reject_non_decimal_amounts(
    value_type: type[Money] | type[Price], amount: object
) -> None:
    with pytest.raises(ValueError, match="Decimal"):
        value_type(amount=amount, currency=Currency.USD)  # type: ignore[arg-type]


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
@pytest.mark.parametrize("value_type", [Money, Price])
def test_money_and_price_reject_non_finite_amounts(
    value_type: type[Money] | type[Price], amount: Decimal
) -> None:
    with pytest.raises(ValueError, match="finite"):
        value_type(amount=amount, currency=Currency.USD)


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.01")])
def test_price_rejects_non_positive_amounts(amount: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        Price(amount=amount, currency=Currency.USD)


def test_money_and_price_validate_currency_enum() -> None:
    with pytest.raises(ValueError, match="Currency"):
        Money(amount=Decimal("1"), currency="USD")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Currency"):
        Price(amount=Decimal("1"), currency="USD")  # type: ignore[arg-type]


def test_quantity_accepts_signed_decimal_value_without_changing_precision() -> None:
    quantity = Quantity(value=Decimal("-1.2300"), precision=4)

    assert quantity.value == Decimal("-1.2300")
    assert quantity.precision == 4
    with pytest.raises(FrozenInstanceError):
        quantity.precision = 3  # type: ignore[misc]


@pytest.mark.parametrize("value", [1.0, 1, True, "1"])
def test_quantity_rejects_non_decimal_values(value: object) -> None:
    with pytest.raises(ValueError, match="Decimal"):
        Quantity(value=value, precision=2)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_quantity_rejects_non_finite_values(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        Quantity(value=value, precision=2)


@pytest.mark.parametrize("precision", [-1, 19, True, 1.0, "2"])
def test_quantity_validates_precision_bounds_and_type(precision: object) -> None:
    with pytest.raises(ValueError, match="precision"):
        Quantity(value=Decimal("1"), precision=precision)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "precision"),
    [(Decimal("1.234"), 2), (Decimal("-0.001"), 2)],
)
def test_quantity_rejects_fractional_digit_overflow(value: Decimal, precision: int) -> None:
    with pytest.raises(ValueError, match="precision"):
        Quantity(value=value, precision=precision)


def test_quantity_accepts_value_at_declared_precision() -> None:
    assert Quantity(value=Decimal("1.230"), precision=3).value == Decimal("1.230")


def test_quantity_precision_accepts_global_boundary_and_rejects_overflow() -> None:
    assert Quantity(value=Decimal("0.000000000000000001"), precision=18).value == Decimal(
        "0.000000000000000001"
    )
    with pytest.raises(ValueError, match="precision"):
        Quantity(value=Decimal("0.0000000000000000001"), precision=18)


def test_quantity_rejects_huge_positive_exponent_without_expansion() -> None:
    with pytest.raises(ValueError, match="maximum quantity magnitude"):
        Quantity(value=Decimal(f"1E+{10**7}"), precision=0)


def test_quantity_magnitude_cap_has_exact_boundary() -> None:
    accepted = Decimal("9" * 128)
    assert Quantity(value=accepted, precision=0).value == accepted
    with pytest.raises(ValueError, match="maximum quantity magnitude"):
        Quantity(value=Decimal("9" * 129), precision=0)
