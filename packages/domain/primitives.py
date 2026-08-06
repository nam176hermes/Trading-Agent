"""Immutable fixed-precision monetary domain primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import gcd
import re
from types import MappingProxyType
from typing import Annotated, ClassVar

from pydantic import BeforeValidator, PlainSerializer, ValidationInfo, WithJsonSchema
from pydantic_core import core_schema


CANONICAL_DECIMAL_POLICY_VERSION = "decimal-v1"
_CANONICAL_DECIMAL_PATTERN = (
    r"^(?:0|-?[1-9]\d*|-?(?:0|[1-9]\d*)\.\d*[1-9])$"
)
_MAX_QUANTITY_COEFFICIENT_DIGITS = 128
_CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_REGISTRY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_FINANCIAL_PRECISION = 18


def _serialize_canonical_decimal(value: Decimal) -> str:
    """Return the decimal-v1 plain, scale-independent numeric spelling."""

    if value.is_zero():
        return "0"
    plain = format(value, "f")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    return plain


def _validate_canonical_decimal(value: object, info: ValidationInfo) -> Decimal:
    """Accept Decimal objects in Python and decimal-v1 strings in JSON."""

    if info.mode == "json":
        if not isinstance(value, str) or re.fullmatch(_CANONICAL_DECIMAL_PATTERN, value) is None:
            raise ValueError("value must be a canonical Decimal string")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("value must be a valid Decimal string") from exc
        if _serialize_canonical_decimal(parsed) != value:
            raise ValueError("value must be a canonical Decimal string")
        value = parsed
    if not isinstance(value, Decimal):
        raise ValueError("value must be a Decimal instance")
    if not value.is_finite():
        raise ValueError("value must be finite")
    return value


FiniteDecimal = Annotated[
    Decimal,
    BeforeValidator(_validate_canonical_decimal, json_schema_input_type=str),
    PlainSerializer(_serialize_canonical_decimal, return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _CANONICAL_DECIMAL_PATTERN,
            "x-canonical-decimal-policy": CANONICAL_DECIMAL_POLICY_VERSION,
        },
        mode="validation",
    ),
]


class CurrencyType(str, Enum):
    """Broad currency classifications carried by registry identities."""

    FIAT = "fiat"
    STABLECOIN = "stablecoin"
    CRYPTO = "crypto"


def _require_registry_version(value: object) -> str:
    if not isinstance(value, str) or _REGISTRY_VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError("registry version must be a non-empty canonical identifier")
    return value


def _require_currency_code(value: object) -> str:
    if not isinstance(value, str) or _CURRENCY_CODE_PATTERN.fullmatch(value) is None:
        raise ValueError("code must be a canonical uppercase currency code")
    return value


def _require_precision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("precision must be an integer from 0 through 18")
    if not 0 <= value <= _MAX_FINANCIAL_PRECISION:
        raise ValueError("precision must be an integer from 0 through 18")
    return value


@dataclass(frozen=True, slots=True)
class Currency:
    """One immutable identity issued by a versioned currency registry."""

    code: str
    currency_type: CurrencyType
    precision: int
    registry_version: str

    USD: ClassVar[Currency]
    USDT: ClassVar[Currency]
    BTC: ClassVar[Currency]
    ETH: ClassVar[Currency]

    def __post_init__(self) -> None:
        _require_currency_code(self.code)
        if not isinstance(self.currency_type, CurrencyType):
            raise ValueError("currency_type must be a CurrencyType value")
        _require_precision(self.precision)
        _require_registry_version(self.registry_version)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: object, _handler: object
    ) -> core_schema.CoreSchema:
        return core_schema.json_or_python_schema(
            json_schema=core_schema.chain_schema(
                [
                    core_schema.str_schema(strict=True),
                    core_schema.no_info_plain_validator_function(
                        DEFAULT_CURRENCY_REGISTRY.resolve
                    ),
                ]
            ),
            python_schema=core_schema.no_info_plain_validator_function(
                _require_currency
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda currency: currency.code,
                return_schema=core_schema.str_schema(),
                when_used="json",
            ),
            ref=cls.__name__,
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _schema: core_schema.CoreSchema, _handler: object
    ) -> dict[str, object]:
        return {
            "description": cls.__doc__,
            "enum": list(DEFAULT_CURRENCY_REGISTRY.by_code),
            "title": cls.__name__,
            "type": "string",
            "x-currency-registry-version": DEFAULT_CURRENCY_REGISTRY.version,
        }


@dataclass(frozen=True, slots=True)
class CurrencyRegistry:
    """An immutable, versioned set of canonical currency identities."""

    version: str
    currencies: tuple[Currency, ...]
    _by_code: Mapping[str, Currency] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_registry_version(self.version)
        if not isinstance(self.currencies, tuple):
            raise ValueError("currencies must be an immutable tuple")
        by_code: dict[str, Currency] = {}
        for currency in self.currencies:
            if not isinstance(currency, Currency):
                raise ValueError("currencies must contain Currency identities")
            if currency.registry_version != self.version:
                raise ValueError(
                    "currency registry version does not match registry version"
                )
            if currency.code in by_code:
                raise ValueError(f"duplicate currency code: {currency.code}")
            by_code[currency.code] = currency
        object.__setattr__(self, "_by_code", MappingProxyType(by_code))

    @property
    def by_code(self) -> Mapping[str, Currency]:
        return self._by_code

    def resolve(self, code: object) -> Currency:
        try:
            canonical_code = _require_currency_code(code)
        except ValueError as exc:
            raise ValueError("currency code must be a canonical currency code") from exc
        try:
            return self._by_code[canonical_code]
        except KeyError as exc:
            raise ValueError(f"unknown currency code: {canonical_code}") from exc

    def is_registered(self, currency: object) -> bool:
        return (
            isinstance(currency, Currency)
            and self._by_code.get(currency.code) is currency
        )


DEFAULT_CURRENCY_REGISTRY = CurrencyRegistry(
    version="currency-v1",
    currencies=(
        Currency("USD", CurrencyType.FIAT, 2, "currency-v1"),
        Currency("USDT", CurrencyType.STABLECOIN, 6, "currency-v1"),
        Currency("BTC", CurrencyType.CRYPTO, 8, "currency-v1"),
        Currency("ETH", CurrencyType.CRYPTO, 18, "currency-v1"),
    ),
)
Currency.USD = DEFAULT_CURRENCY_REGISTRY.resolve("USD")
Currency.USDT = DEFAULT_CURRENCY_REGISTRY.resolve("USDT")
Currency.BTC = DEFAULT_CURRENCY_REGISTRY.resolve("BTC")
Currency.ETH = DEFAULT_CURRENCY_REGISTRY.resolve("ETH")


def _require_finite_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be a Decimal instance")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _require_currency(value: object) -> Currency:
    if not DEFAULT_CURRENCY_REGISTRY.is_registered(value):
        raise ValueError("currency must be a registered Currency identity")
    return value


def _decimal_coefficient_and_exponent(value: Decimal) -> tuple[int, int]:
    """Return an unsigned coefficient and exponent without Decimal context math."""

    _, digits, raw_exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = (coefficient * 10) + digit
    return coefficient, int(raw_exponent)


def decimal_to_scaled_integer(value: Decimal, precision: int) -> int:
    """Return the exact integer for ``value`` at ``precision`` decimal places."""

    exact_value = _require_finite_decimal(value, "value")
    exact_precision = _require_precision(precision)
    sign, digits, raw_exponent = exact_value.as_tuple()
    if exact_value.is_zero():
        return 0

    exponent = int(raw_exponent)
    scale_delta = exponent + exact_precision
    if scale_delta >= 0:
        scaled_digit_count = len(digits) + scale_delta
        if scaled_digit_count > _MAX_QUANTITY_COEFFICIENT_DIGITS:
            raise ValueError("scaled integer exceeds maximum supported magnitude")
        retained_digits = digits
    else:
        removed_digit_count = -scale_delta
        if removed_digit_count > len(digits) or any(
            digit != 0 for digit in digits[-removed_digit_count:]
        ):
            raise ValueError("value cannot be represented exactly at precision")
        retained_digits = digits[:-removed_digit_count]
        if len(retained_digits) > _MAX_QUANTITY_COEFFICIENT_DIGITS:
            raise ValueError("scaled integer exceeds maximum supported magnitude")

    scaled = 0
    for digit in retained_digits:
        scaled = (scaled * 10) + digit
    if scale_delta > 0:
        scaled *= 10**scale_delta
    return -scaled if sign and scaled else scaled


def _multiply_decimals_exact(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite Decimals with tuple arithmetic, independent of context."""

    left_sign, _, _ = left.as_tuple()
    right_sign, _, _ = right.as_tuple()
    left_coefficient, left_exponent = _decimal_coefficient_and_exponent(left)
    right_coefficient, right_exponent = _decimal_coefficient_and_exponent(right)
    coefficient = left_coefficient * right_coefficient
    digits = _integer_digits(coefficient)
    try:
        return Decimal(
            (
                left_sign ^ right_sign,
                digits,
                left_exponent + right_exponent,
            )
        )
    except InvalidOperation as exc:
        raise ValueError("exact result is outside the supported Decimal range") from exc


def _integer_digits(value: int) -> tuple[int, ...]:
    if value == 0:
        return (0,)
    reversed_digits: list[int] = []
    while value:
        value, digit = divmod(value, 10)
        reversed_digits.append(digit)
    return tuple(reversed(reversed_digits))


def _reduce_fractional_trailing_zeros_to_precision(
    value: Decimal, precision: int
) -> Decimal:
    if value.is_zero():
        return Decimal((0, (0,), -precision))
    sign, raw_digits, raw_exponent = value.as_tuple()
    digits = raw_digits
    exponent = int(raw_exponent)
    minimum_exponent = -precision
    while exponent < minimum_exponent and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    return Decimal((sign, digits, exponent))


def _is_exact_decimal_multiple(value: Decimal, increment: Decimal) -> bool:
    """Return whether value is an exact multiple without division or rounding."""

    if increment <= 0:
        raise ValueError("increment must be positive")
    if value.is_zero():
        return True

    value_coefficient, value_exponent = _decimal_coefficient_and_exponent(value)
    increment_coefficient, increment_exponent = _decimal_coefficient_and_exponent(
        increment
    )
    exponent_delta = value_exponent - increment_exponent

    if exponent_delta < 0:
        if value_coefficient % increment_coefficient:
            return False
        quotient = value_coefficient // increment_coefficient
        required_trailing_zeros = -exponent_delta
        while required_trailing_zeros and quotient % 10 == 0:
            quotient //= 10
            required_trailing_zeros -= 1
        return required_trailing_zeros == 0

    denominator = increment_coefficient // gcd(
        value_coefficient, increment_coefficient
    )
    for factor in (2, 5):
        available = exponent_delta
        while available and denominator % factor == 0:
            denominator //= factor
            available -= 1
    return denominator == 1


@dataclass(frozen=True, slots=True)
class Money:
    """An exact signed monetary amount."""

    amount: FiniteDecimal
    currency: Currency

    def __post_init__(self) -> None:
        amount = _require_finite_decimal(self.amount, "amount")
        currency = _require_currency(self.currency)
        if max(0, -int(amount.as_tuple().exponent)) > currency.precision:
            raise ValueError("amount scale exceeds currency precision")


@dataclass(frozen=True, slots=True)
class Price:
    """An exact strictly positive monetary price."""

    amount: FiniteDecimal
    currency: Currency

    def __post_init__(self) -> None:
        amount = _require_finite_decimal(self.amount, "amount")
        _require_currency(self.currency)
        if amount <= Decimal(0):
            raise ValueError("price amount must be positive")


@dataclass(frozen=True, slots=True)
class Quantity:
    """An exact signed quantity with declared fractional precision."""

    value: FiniteDecimal
    precision: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_quantity(self.value, self.precision),
        )


def _normalize_quantity(value: object, precision: object) -> Decimal:
    exact_value = _require_finite_decimal(value, "value")
    exact_precision = _require_precision(precision)
    target_exponent = -exact_precision
    if exact_value.is_zero():
        return Decimal((0, (0,), target_exponent))

    sign, raw_digits, raw_exponent = exact_value.as_tuple()
    digits = raw_digits
    exponent = int(raw_exponent)
    trailing_zero_count = 0
    for index in range(len(digits) - 1, 0, -1):
        if digits[index] != 0:
            break
        trailing_zero_count += 1
    if trailing_zero_count:
        digits = digits[:-trailing_zero_count]
        exponent += trailing_zero_count

    fractional_digits = max(0, -exponent)
    if fractional_digits > exact_precision:
        raise ValueError("value has more fractional digits than precision")

    expanded_digit_count = len(digits) + (exponent - target_exponent)
    if expanded_digit_count > _MAX_QUANTITY_COEFFICIENT_DIGITS:
        raise ValueError("value exceeds maximum quantity magnitude")

    canonical_digits = digits + ((0,) * (exponent - target_exponent))
    return Decimal((sign, canonical_digits, target_exponent))


@dataclass(frozen=True, slots=True)
class OrderQuantity:
    """An exact non-negative order quantity with declared precision."""

    value: FiniteDecimal
    precision: int

    def __post_init__(self) -> None:
        value = _require_finite_decimal(self.value, "value")
        if value < 0:
            raise ValueError("order quantity must be non-negative")
        object.__setattr__(self, "value", _normalize_quantity(value, self.precision))


@dataclass(frozen=True, slots=True)
class CurrencyConversion:
    """An explicit exact conversion from source money to target money."""

    source: Money
    target_currency: Currency
    rate: FiniteDecimal
    target: Money

    def __post_init__(self) -> None:
        if not isinstance(self.source, Money):
            raise ValueError("source must be Money")
        target_currency = _require_currency(self.target_currency)
        rate = _require_finite_decimal(self.rate, "rate")
        if rate <= 0:
            raise ValueError("rate must be positive")
        if self.source.currency is target_currency:
            raise ValueError("same-currency conversion is not allowed")
        if not isinstance(self.target, Money):
            raise ValueError("target must be Money")
        if self.target.currency is not target_currency:
            raise ValueError("target currency does not match conversion target currency")
        expected = _multiply_decimals_exact(self.source.amount, rate)
        if self.target.amount != expected:
            raise ValueError("conversion rate and target amount do not match exactly")


def convert_money_exact(
    source: Money, target_currency: Currency, rate: Decimal
) -> CurrencyConversion:
    """Build an exact conversion, rejecting any target-precision rounding need."""

    if not isinstance(source, Money):
        raise ValueError("source must be Money")
    registered_target = _require_currency(target_currency)
    exact_rate = _require_finite_decimal(rate, "rate")
    if exact_rate <= 0:
        raise ValueError("rate must be positive")
    if source.currency is registered_target:
        raise ValueError("same-currency conversion is not allowed")
    exact_target = _multiply_decimals_exact(source.amount, exact_rate)
    target = Money(
        _reduce_fractional_trailing_zeros_to_precision(
            exact_target, registered_target.precision
        ),
        registered_target,
    )
    return CurrencyConversion(source, registered_target, exact_rate, target)
