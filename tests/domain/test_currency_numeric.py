from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import MAX_EMAX, Decimal, localcontext
from enum import Enum

import pytest
from pydantic import TypeAdapter, ValidationError

from packages.domain import (
    DEFAULT_CURRENCY_REGISTRY,
    Currency,
    CurrencyConversion,
    CurrencyRegistry,
    CurrencyType,
    Money,
    OrderQuantity,
    Price,
    convert_money_exact,
    decimal_to_scaled_integer,
)


def test_default_currency_registry_is_versioned_and_covers_required_types() -> None:
    assert DEFAULT_CURRENCY_REGISTRY.version == "currency-v1"
    assert DEFAULT_CURRENCY_REGISTRY.resolve("USD") is Currency.USD
    assert DEFAULT_CURRENCY_REGISTRY.resolve("USDT") is Currency.USDT
    assert DEFAULT_CURRENCY_REGISTRY.resolve("BTC") is Currency.BTC
    assert DEFAULT_CURRENCY_REGISTRY.resolve("ETH") is Currency.ETH
    assert (
        Currency.USD.code,
        Currency.USD.currency_type,
        Currency.USD.precision,
        Currency.USD.registry_version,
    ) == ("USD", CurrencyType.FIAT, 2, "currency-v1")
    assert (Currency.USDT.currency_type, Currency.USDT.precision) == (
        CurrencyType.STABLECOIN,
        6,
    )
    assert (Currency.BTC.currency_type, Currency.BTC.precision) == (
        CurrencyType.CRYPTO,
        8,
    )
    assert (Currency.ETH.currency_type, Currency.ETH.precision) == (
        CurrencyType.CRYPTO,
        18,
    )
    assert not issubclass(Currency, Enum)


@pytest.mark.parametrize("version", ["", " currency-v1", "currency v1", 1, True])
def test_currency_registry_rejects_invalid_version(version: object) -> None:
    with pytest.raises(ValueError, match="version"):
        CurrencyRegistry(version=version, currencies=())  # type: ignore[arg-type]


@pytest.mark.parametrize("code", ["", "usd", " USD", "US-D", 1, True])
def test_currency_identity_rejects_invalid_code(code: object) -> None:
    with pytest.raises(ValueError, match="code"):
        Currency(
            code=code,  # type: ignore[arg-type]
            currency_type=CurrencyType.FIAT,
            precision=2,
            registry_version="currency-v1",
        )


@pytest.mark.parametrize("currency_type", ["fiat", 1, True])
def test_currency_identity_rejects_invalid_type(currency_type: object) -> None:
    with pytest.raises(ValueError, match="currency_type"):
        Currency(
            code="CAD",
            currency_type=currency_type,  # type: ignore[arg-type]
            precision=2,
            registry_version="currency-v1",
        )


@pytest.mark.parametrize("precision", [-1, 19, 1.0, "2", True])
def test_currency_identity_rejects_invalid_precision(precision: object) -> None:
    with pytest.raises(ValueError, match="precision"):
        Currency(
            code="CAD",
            currency_type=CurrencyType.FIAT,
            precision=precision,  # type: ignore[arg-type]
            registry_version="currency-v1",
        )


def test_registry_rejects_duplicates_and_mismatched_identity_versions() -> None:
    cad = Currency("CAD", CurrencyType.FIAT, 2, "currency-v2")
    with pytest.raises(ValueError, match="registry version"):
        CurrencyRegistry("currency-v1", (cad,))

    first = Currency("CAD", CurrencyType.FIAT, 2, "currency-v1")
    second = Currency("CAD", CurrencyType.FIAT, 2, "currency-v1")
    with pytest.raises(ValueError, match="duplicate"):
        CurrencyRegistry("currency-v1", (first, second))


def test_default_registry_is_immutable_and_unknown_codes_fail_closed() -> None:
    with pytest.raises(TypeError):
        DEFAULT_CURRENCY_REGISTRY.by_code["CAD"] = Currency.USD  # type: ignore[index]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        DEFAULT_CURRENCY_REGISTRY.version = "currency-v2"  # type: ignore[misc]
    with pytest.raises(ValueError, match="unknown currency code"):
        DEFAULT_CURRENCY_REGISTRY.resolve("CAD")
    with pytest.raises(ValueError, match="canonical currency code"):
        DEFAULT_CURRENCY_REGISTRY.resolve("usd")


def test_money_rejects_raw_code_and_unregistered_identity() -> None:
    forged_usd = Currency("USD", CurrencyType.FIAT, 2, "currency-v1")

    with pytest.raises(ValueError, match="registered Currency identity"):
        Money(Decimal("1.00"), forged_usd)
    with pytest.raises(ValueError, match="registered Currency identity"):
        Money(Decimal("1.00"), "USD")  # type: ignore[arg-type]


def test_currency_json_is_canonical_code_and_resolves_registered_identity() -> None:
    adapter = TypeAdapter(Money)
    money = Money(Decimal("1.25"), Currency.USD)

    assert adapter.dump_json(money) == b'{"amount":"1.25","currency":"USD"}'
    assert adapter.validate_json(b'{"amount":"1.25","currency":"USD"}') == money
    with pytest.raises(ValidationError, match="unknown currency code"):
        adapter.validate_json(b'{"amount":"1.25","currency":"CAD"}')


def test_money_enforces_currency_scale_but_price_does_not_imply_a_tick_grid() -> None:
    assert Money(Decimal("1.25"), Currency.USD).amount == Decimal("1.25")
    with pytest.raises(ValueError, match="currency precision"):
        Money(Decimal("1.250"), Currency.USD)

    price = Price(Decimal("1.2500000000000000001"), Currency.USD)
    assert price.amount == Decimal("1.2500000000000000001")


@pytest.mark.parametrize("value", [1.0, 1, True, "1"])
def test_order_quantity_rejects_non_decimal_values(value: object) -> None:
    with pytest.raises(ValueError, match="Decimal"):
        OrderQuantity(value=value, precision=2)  # type: ignore[arg-type]


def test_order_quantity_is_unsigned_exact_and_immutable() -> None:
    quantity = OrderQuantity(Decimal("0.250"), precision=3)

    assert quantity.value == Decimal("0.250")
    assert OrderQuantity(Decimal("0"), precision=2).value == Decimal("0.00")
    with pytest.raises(ValueError, match="non-negative"):
        OrderQuantity(Decimal("-0.001"), precision=3)
    with pytest.raises(ValueError, match="precision"):
        OrderQuantity(Decimal("0.001"), precision=2)
    with pytest.raises(FrozenInstanceError):
        quantity.precision = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "rate", [1.0, 1, True, "1", Decimal("NaN"), Decimal("Infinity")]
)
def test_exact_conversion_rejects_non_decimal_or_non_finite_rates(rate: object) -> None:
    with pytest.raises(ValueError, match="rate"):
        convert_money_exact(
            Money(Decimal("1.00"), Currency.USD),
            Currency.USDT,
            rate,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-0.01")])
def test_exact_conversion_requires_a_positive_rate(rate: Decimal) -> None:
    with pytest.raises(ValueError, match="rate must be positive"):
        convert_money_exact(
            Money(Decimal("1.00"), Currency.USD),
            Currency.USDT,
            rate,
        )


def test_exact_conversion_binds_pair_rate_and_exact_target_without_context_rounding(
) -> None:
    source = Money(Decimal("1.23"), Currency.USD)
    with localcontext() as context:
        context.prec = 2
        conversion = convert_money_exact(source, Currency.USDT, Decimal("2.345"))

    assert conversion == CurrencyConversion(
        source=source,
        target_currency=Currency.USDT,
        rate=Decimal("2.345"),
        target=Money(Decimal("2.88435"), Currency.USDT),
    )
    with pytest.raises(ValueError, match="same-currency"):
        convert_money_exact(source, Currency.USD, Decimal("1"))
    with pytest.raises(ValueError, match="target currency"):
        CurrencyConversion(
            source=source,
            target_currency=Currency.USDT,
            rate=Decimal("2"),
            target=Money(Decimal("2.46"), Currency.BTC),
        )
    with pytest.raises(ValueError, match="rate and target"):
        CurrencyConversion(
            source=source,
            target_currency=Currency.USDT,
            rate=Decimal("2"),
            target=Money(Decimal("2.47"), Currency.USDT),
        )


def test_exact_conversion_rejects_target_values_that_require_rounding() -> None:
    with pytest.raises(ValueError, match="currency precision"):
        convert_money_exact(
            Money(Decimal("1.00"), Currency.USD),
            Currency.BTC,
            Decimal("0.123456789"),
        )


def test_exact_conversion_removes_only_excess_trailing_zero_scale() -> None:
    conversion = convert_money_exact(
        Money(Decimal("1.000000"), Currency.USDT),
        Currency.USD,
        Decimal("2.00"),
    )

    assert conversion.target == Money(Decimal("2.00"), Currency.USD)


@pytest.mark.parametrize("value", [1.0, 1, True, "1", Decimal("NaN")])
def test_scaled_integer_rejects_non_decimal_or_non_finite_values(value: object) -> None:
    with pytest.raises(ValueError, match="value"):
        decimal_to_scaled_integer(value, 2)  # type: ignore[arg-type]


@pytest.mark.parametrize("precision", [-1, 19, 1.0, "2", True])
def test_scaled_integer_rejects_unsupported_precision(precision: object) -> None:
    with pytest.raises(ValueError, match="precision"):
        decimal_to_scaled_integer(Decimal("1"), precision)  # type: ignore[arg-type]


def test_scaled_integer_requires_exact_scaling_without_using_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 1
        assert decimal_to_scaled_integer(Decimal("123.4500"), 2) == 12345
        assert decimal_to_scaled_integer(Decimal("-1.23"), 2) == -123
    with pytest.raises(ValueError, match="exactly"):
        decimal_to_scaled_integer(Decimal("1.234"), 2)


def test_scaled_integer_rejects_unsupported_magnitude_without_expansion() -> None:
    with pytest.raises(ValueError, match="magnitude"):
        decimal_to_scaled_integer(Decimal("1E+129"), 0)


def test_exact_conversion_does_not_depend_on_integer_string_limits() -> None:
    amount = Decimal((0, (9,) * 5_000, 0))

    conversion = convert_money_exact(
        Money(amount, Currency.USD),
        Currency.USDT,
        Decimal("1"),
    )

    assert conversion.target.amount == amount


def test_exact_conversion_fails_closed_outside_supported_decimal_range() -> None:
    with pytest.raises(ValueError, match="supported Decimal range"):
        convert_money_exact(
            Money(Decimal(f"1E+{MAX_EMAX}"), Currency.USD),
            Currency.USDT,
            Decimal(f"1E+{MAX_EMAX}"),
        )
