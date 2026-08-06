"""Canonical immutable instrument identities, definitions, and constraints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re

from .clock import require_utc
from .primitives import (
    Currency,
    FiniteDecimal,
    Money,
    OrderQuantity,
    Price,
    decimal_to_scaled_integer,
    _is_exact_decimal_multiple,
    _multiply_decimals_exact,
    _require_currency,
    _require_finite_decimal,
)


_ASCII_WHITESPACE = " \t\n\r\f\v"
_SAFE_COMPONENT = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")
_MAX_COMPONENT_LENGTH = 32
_MAX_RAW_SYMBOL_LENGTH = 128
_MAX_FINANCIAL_PRECISION = 18


class ProductType(str, Enum):
    """Product classes supported by the canonical domain model."""

    CRYPTO_SPOT = "crypto_spot"
    EQUITY = "equity"


class AssetClass(str, Enum):
    """Asset classes with an exact mapping to supported product types."""

    CRYPTO = "crypto"
    EQUITY = "equity"


def _canonical_component(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip(_ASCII_WHITESPACE).upper()
    if not normalized or len(normalized) > _MAX_COMPONENT_LENGTH:
        raise ValueError(f"{field_name} must contain 1 through 32 safe characters")
    if _SAFE_COMPONENT.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} contains unsafe characters")
    return normalized


def _raw_symbol(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("raw_symbol must be a string")
    if not value or not value.strip(" ") or len(value) > _MAX_RAW_SYMBOL_LENGTH:
        raise ValueError("raw_symbol must contain 1 through 128 printable ASCII characters")
    if any(not 32 <= ord(character) <= 126 for character in value):
        raise ValueError("raw_symbol must contain only printable ASCII characters")
    return value


def _fractional_precision(value: Decimal) -> int:
    """Return the minimal exact fractional precision without context arithmetic."""

    _, raw_digits, raw_exponent = value.as_tuple()
    exponent = int(raw_exponent)
    if exponent >= 0 or value.is_zero():
        return 0
    removable = min(-exponent, len(raw_digits))
    trailing_zeros = 0
    for digit in reversed(raw_digits):
        if trailing_zeros == removable or digit:
            break
        trailing_zeros += 1
    return max(0, -exponent - trailing_zeros)


def _bounded_positive_decimal(value: object, field_name: str) -> Decimal:
    exact = _require_finite_decimal(value, field_name)
    if exact <= 0:
        raise ValueError(f"{field_name} must be positive")
    try:
        decimal_to_scaled_integer(exact, _MAX_FINANCIAL_PRECISION)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} exceeds supported precision or magnitude"
        ) from exc
    return exact


def _exact_product(*factors: Decimal) -> Decimal:
    result = Decimal(1)
    try:
        for factor in factors:
            result = _multiply_decimals_exact(result, factor)
    except ValueError as exc:
        raise ValueError("notional is outside the supported Decimal range") from exc
    return result


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A normalized identity whose canonical form includes type and venue."""

    symbol: str
    product_type: ProductType
    venue: str

    def __post_init__(self) -> None:
        if not isinstance(self.product_type, ProductType):
            raise ValueError("product_type must be a ProductType enum value")
        object.__setattr__(self, "symbol", _canonical_component(self.symbol, "symbol"))
        object.__setattr__(self, "venue", _canonical_component(self.venue, "venue"))

    @property
    def canonical(self) -> str:
        """Stable, delimiter-safe canonical identity string."""
        return f"{self.product_type.value}:{self.venue}:{self.symbol}"


@dataclass(frozen=True, slots=True)
class InstrumentProvenance:
    """Immutable catalog-source evidence with no provider authority."""

    source_id: str
    source_revision: str
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_id", _canonical_component(self.source_id, "source_id")
        )
        object.__setattr__(
            self,
            "source_revision",
            _canonical_component(self.source_revision, "source_revision"),
        )
        require_utc(self.observed_at)


@dataclass(frozen=True, slots=True)
class MarginRequirements:
    """Optional exact initial and maintenance margin rates."""

    initial_margin_rate: FiniteDecimal
    maintenance_margin_rate: FiniteDecimal

    def __post_init__(self) -> None:
        try:
            initial = _bounded_positive_decimal(
                self.initial_margin_rate, "initial margin rate"
            )
            maintenance = _bounded_positive_decimal(
                self.maintenance_margin_rate, "maintenance margin rate"
            )
        except ValueError as exc:
            raise ValueError(f"invalid margin requirements: {exc}") from exc
        if initial > 1 or maintenance > 1:
            raise ValueError("margin rates must not exceed one")
        if maintenance > initial:
            raise ValueError("maintenance margin rate must not exceed initial margin rate")


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    """Sole immutable source of one instrument's catalog trading constraints."""

    instrument_id: InstrumentId
    raw_symbol: str
    asset_class: AssetClass
    base_currency: Currency | None
    quote_currency: Currency
    settlement_currency: Currency
    tick_size: Price
    size_increment: OrderQuantity
    minimum_quantity: OrderQuantity
    maximum_quantity: OrderQuantity
    minimum_notional: Money
    maximum_notional: Money
    multiplier: FiniteDecimal
    margin: MarginRequirements | None
    session_calendar: str
    provenance: InstrumentProvenance

    def __post_init__(self) -> None:
        self._validate_identity_and_catalog()
        self._validate_currencies()
        self._validate_price_and_quantity_limits()
        self._validate_notional_limits()

    def _validate_identity_and_catalog(self) -> None:
        if not isinstance(self.instrument_id, InstrumentId):
            raise ValueError("instrument_id must be an InstrumentId")
        canonical_id = InstrumentId(
            self.instrument_id.symbol,
            self.instrument_id.product_type,
            self.instrument_id.venue,
        )
        if canonical_id != self.instrument_id:
            raise ValueError("instrument_id must be a canonical InstrumentId")
        object.__setattr__(self, "raw_symbol", _raw_symbol(self.raw_symbol))
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError("asset_class must be an AssetClass enum value")
        expected_asset_class = {
            ProductType.CRYPTO_SPOT: AssetClass.CRYPTO,
            ProductType.EQUITY: AssetClass.EQUITY,
        }[self.instrument_id.product_type]
        if self.asset_class is not expected_asset_class:
            raise ValueError("asset_class does not agree with product_type")
        object.__setattr__(
            self,
            "session_calendar",
            _canonical_component(self.session_calendar, "session_calendar"),
        )
        if not isinstance(self.provenance, InstrumentProvenance):
            raise ValueError("provenance must be InstrumentProvenance")
        canonical_provenance = InstrumentProvenance(
            self.provenance.source_id,
            self.provenance.source_revision,
            self.provenance.observed_at,
        )
        if canonical_provenance != self.provenance:
            raise ValueError("provenance must be canonical")
        if self.margin is not None:
            if not isinstance(self.margin, MarginRequirements):
                raise ValueError("margin must be MarginRequirements or None")
            MarginRequirements(
                self.margin.initial_margin_rate,
                self.margin.maintenance_margin_rate,
            )

    def _validate_currencies(self) -> None:
        quote = _require_currency(self.quote_currency)
        settlement = _require_currency(self.settlement_currency)
        if settlement is not quote:
            raise ValueError("settlement currency must equal quote currency")
        if self.instrument_id.product_type is ProductType.CRYPTO_SPOT:
            base = _require_currency(self.base_currency)
            if base is quote:
                raise ValueError("base currency must be distinct from quote currency")
        elif self.base_currency is not None:
            raise ValueError("equity base_currency must be None")

    def _validate_price_and_quantity_limits(self) -> None:
        if not isinstance(self.tick_size, Price):
            raise ValueError("tick_size must be a Price")
        Price(self.tick_size.amount, self.tick_size.currency)
        if self.tick_size.currency is not self.quote_currency:
            raise ValueError("tick_size must use quote currency")
        tick_precision = _fractional_precision(self.tick_size.amount)
        if tick_precision > _MAX_FINANCIAL_PRECISION:
            raise ValueError("tick_size exceeds supported precision")
        try:
            decimal_to_scaled_integer(self.tick_size.amount, tick_precision)
        except ValueError as exc:
            raise ValueError("tick_size exceeds supported precision or magnitude") from exc

        for name, value in (
            ("size_increment", self.size_increment),
            ("minimum_quantity", self.minimum_quantity),
            ("maximum_quantity", self.maximum_quantity),
        ):
            if not isinstance(value, OrderQuantity):
                raise ValueError(f"{name} must be an OrderQuantity")
            OrderQuantity(value.value, value.precision)
            if value.value <= 0:
                raise ValueError(f"{name} must be positive")
        quantity_precision = self.size_increment.precision
        if self.minimum_quantity.precision != quantity_precision:
            raise ValueError("minimum_quantity must use instrument quantity precision")
        if self.maximum_quantity.precision != quantity_precision:
            raise ValueError("maximum_quantity must use instrument quantity precision")
        if not _is_exact_decimal_multiple(
            self.minimum_quantity.value, self.size_increment.value
        ):
            raise ValueError("minimum_quantity must be on the size increment grid")
        if not _is_exact_decimal_multiple(
            self.maximum_quantity.value, self.size_increment.value
        ):
            raise ValueError("maximum_quantity must be on the size increment grid")
        if self.maximum_quantity.value < self.minimum_quantity.value:
            raise ValueError("maximum_quantity must not be below minimum_quantity")

    def _validate_notional_limits(self) -> None:
        multiplier = _bounded_positive_decimal(self.multiplier, "multiplier")
        for name, value in (
            ("minimum_notional", self.minimum_notional),
            ("maximum_notional", self.maximum_notional),
        ):
            if not isinstance(value, Money):
                raise ValueError(f"{name} must be Money")
            Money(value.amount, value.currency)
            if value.currency is not self.quote_currency:
                raise ValueError(f"{name} must use quote currency")
            if value.amount <= 0:
                raise ValueError(f"{name} must be positive")
            try:
                decimal_to_scaled_integer(value.amount, self.quote_currency.precision)
            except ValueError as exc:
                raise ValueError(
                    f"{name} exceeds supported precision or magnitude"
                ) from exc
        if self.maximum_notional.amount < self.minimum_notional.amount:
            raise ValueError("maximum_notional must not be below minimum_notional")
        notional_increment = _exact_product(
            self.tick_size.amount,
            self.size_increment.value,
            multiplier,
        )
        if not _is_exact_decimal_multiple(
            self.minimum_notional.amount, notional_increment
        ):
            raise ValueError("minimum_notional must be on the notional grid")
        if not _is_exact_decimal_multiple(
            self.maximum_notional.amount, notional_increment
        ):
            raise ValueError("maximum_notional must be on the notional grid")


@dataclass(frozen=True, slots=True)
class InstrumentConstraints:
    """Exact order validation bound to one canonical instrument definition."""

    definition: InstrumentDefinition

    def __post_init__(self) -> None:
        if not isinstance(self.definition, InstrumentDefinition):
            raise ValueError("definition must be an InstrumentDefinition")
        supplied = self.definition
        try:
            canonical = InstrumentDefinition(
                instrument_id=supplied.instrument_id,
                raw_symbol=supplied.raw_symbol,
                asset_class=supplied.asset_class,
                base_currency=supplied.base_currency,
                quote_currency=supplied.quote_currency,
                settlement_currency=supplied.settlement_currency,
                tick_size=supplied.tick_size,
                size_increment=supplied.size_increment,
                minimum_quantity=supplied.minimum_quantity,
                maximum_quantity=supplied.maximum_quantity,
                minimum_notional=supplied.minimum_notional,
                maximum_notional=supplied.maximum_notional,
                multiplier=supplied.multiplier,
                margin=supplied.margin,
                session_calendar=supplied.session_calendar,
                provenance=supplied.provenance,
            )
        except AttributeError as exc:
            raise ValueError(
                "definition must contain every InstrumentDefinition field"
            ) from exc
        object.__setattr__(self, "definition", canonical)

    def validate_price(self, price: Price) -> Price:
        """Return a valid price or reject currency, precision, and tick-grid errors."""

        if not isinstance(price, Price):
            raise ValueError("price must be a Price")
        Price(price.amount, price.currency)
        if price.currency is not self.definition.quote_currency:
            raise ValueError("price currency does not match quote currency")
        precision = _fractional_precision(self.definition.tick_size.amount)
        try:
            decimal_to_scaled_integer(price.amount, precision)
        except ValueError as exc:
            raise ValueError("price exceeds instrument precision or magnitude") from exc
        if not _is_exact_decimal_multiple(
            price.amount, self.definition.tick_size.amount
        ):
            raise ValueError("price is not on the tick grid")
        return price

    def validate_quantity(self, quantity: OrderQuantity) -> OrderQuantity:
        """Return a valid unsigned quantity or reject precision, grid, and bounds."""

        if not isinstance(quantity, OrderQuantity):
            raise ValueError("quantity must be an OrderQuantity")
        OrderQuantity(quantity.value, quantity.precision)
        increment = self.definition.size_increment
        if quantity.precision != increment.precision:
            raise ValueError("quantity does not use instrument precision")
        if quantity.value <= 0:
            raise ValueError("quantity must be positive")
        if not _is_exact_decimal_multiple(quantity.value, increment.value):
            raise ValueError("quantity is not on the size increment grid")
        if quantity.value < self.definition.minimum_quantity.value:
            raise ValueError("quantity is below minimum quantity")
        if quantity.value > self.definition.maximum_quantity.value:
            raise ValueError("quantity exceeds maximum quantity")
        return quantity

    def validate_order(
        self, *, price: Price, quantity: OrderQuantity
    ) -> tuple[Price, OrderQuantity]:
        """Validate exact grids and inclusive bounds without Decimal rounding."""

        valid_price = self.validate_price(price)
        valid_quantity = self.validate_quantity(quantity)
        notional = _exact_product(
            valid_price.amount,
            valid_quantity.value,
            self.definition.multiplier,
        )
        try:
            decimal_to_scaled_integer(
                notional, self.definition.quote_currency.precision
            )
        except ValueError as exc:
            raise ValueError(
                "notional cannot be represented exactly in quote currency or is "
                "outside the supported Decimal range"
            ) from exc
        if notional < self.definition.minimum_notional.amount:
            raise ValueError("order is below minimum notional")
        if notional > self.definition.maximum_notional.amount:
            raise ValueError("order exceeds maximum notional")
        return valid_price, valid_quantity
