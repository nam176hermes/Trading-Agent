"""Immutable fixed-precision monetary domain primitives."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import gcd
import re
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer, ValidationInfo, WithJsonSchema


CANONICAL_DECIMAL_POLICY_VERSION = "decimal-v1"
_CANONICAL_DECIMAL_PATTERN = (
    r"^(?:0|-?[1-9]\d*|-?(?:0|[1-9]\d*)\.\d*[1-9])$"
)
_MAX_QUANTITY_COEFFICIENT_DIGITS = 128


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


class Currency(str, Enum):
    """Currencies approved for canonical spot and equity assets."""

    USD = "USD"
    USDT = "USDT"


def _require_finite_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be a Decimal instance")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _require_currency(value: object) -> Currency:
    if not isinstance(value, Currency):
        raise ValueError("currency must be a Currency enum value")
    return value


def _decimal_coefficient_and_exponent(value: Decimal) -> tuple[int, int]:
    """Return an unsigned coefficient and exponent without Decimal context math."""

    _, digits, raw_exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = (coefficient * 10) + digit
    return coefficient, int(raw_exponent)


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
        _require_finite_decimal(self.amount, "amount")
        _require_currency(self.currency)


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
        value = _require_finite_decimal(self.value, "value")
        if isinstance(self.precision, bool) or not isinstance(self.precision, int):
            raise ValueError("precision must be an integer from 0 through 18")
        if not 0 <= self.precision <= 18:
            raise ValueError("precision must be an integer from 0 through 18")
        target_exponent = -self.precision
        if value.is_zero():
            object.__setattr__(
                self,
                "value",
                Decimal((0, (0,), target_exponent)),
            )
            return

        sign, raw_digits, raw_exponent = value.as_tuple()
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
        if fractional_digits > self.precision:
            raise ValueError("value has more fractional digits than precision")

        expanded_digit_count = len(digits) + (exponent - target_exponent)
        if expanded_digit_count > _MAX_QUANTITY_COEFFICIENT_DIGITS:
            raise ValueError("value exceeds maximum quantity magnitude")

        canonical_digits = digits + ((0,) * (exponent - target_exponent))
        object.__setattr__(
            self,
            "value",
            Decimal((sign, canonical_digits, target_exponent)),
        )
