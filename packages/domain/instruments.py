"""Canonical immutable instrument identities."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, DecimalException, Inexact, MAX_EMAX, MIN_EMIN
from enum import Enum
import re

from .primitives import Money, Price, Quantity, _is_exact_decimal_multiple


_ASCII_WHITESPACE = " \t\n\r\f\v"
_SAFE_COMPONENT = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")
_MAX_COMPONENT_LENGTH = 32


class ProductType(str, Enum):
    """Product classes supported by the canonical domain model."""

    CRYPTO_SPOT = "crypto_spot"
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
class InstrumentConstraints:
    """Exact venue trading grid and minimums for one instrument."""

    instrument: InstrumentId
    tick_size: Price
    lot_size: Quantity
    minimum_quantity: Quantity
    minimum_notional: Money

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise ValueError("instrument must be an InstrumentId")
        if not isinstance(self.tick_size, Price):
            raise ValueError("tick_size must be a Price")
        if not isinstance(self.lot_size, Quantity):
            raise ValueError("lot_size must be a Quantity")
        if not isinstance(self.minimum_quantity, Quantity):
            raise ValueError("minimum_quantity must be a Quantity")
        if not isinstance(self.minimum_notional, Money):
            raise ValueError("minimum_notional must be Money")
        if self.lot_size.value <= 0:
            raise ValueError("lot size must be positive")
        if self.minimum_quantity.value <= 0:
            raise ValueError("minimum quantity must be positive")
        if self.minimum_notional.amount <= 0:
            raise ValueError("minimum notional must be positive")
        if self.minimum_notional.currency is not self.tick_size.currency:
            raise ValueError("tick size and minimum notional currency must match")
        if self.minimum_quantity.precision > self.lot_size.precision:
            raise ValueError("minimum quantity precision exceeds lot size precision")
        if not _is_exact_decimal_multiple(
            self.minimum_quantity.value, self.lot_size.value
        ):
            raise ValueError("minimum quantity must be on the lot grid")

    def validate_price(self, price: Price) -> Price:
        """Return a valid price or reject currency and tick-grid violations."""

        if not isinstance(price, Price):
            raise ValueError("price must be a Price")
        if price.currency is not self.tick_size.currency:
            raise ValueError("price currency does not match tick size currency")
        if not _is_exact_decimal_multiple(price.amount, self.tick_size.amount):
            raise ValueError("price is not on the tick grid")
        return price

    def validate_quantity(self, quantity: Quantity) -> Quantity:
        """Return a valid quantity or reject precision, grid, and minimum violations."""

        if not isinstance(quantity, Quantity):
            raise ValueError("quantity must be a Quantity")
        if quantity.precision > self.lot_size.precision:
            raise ValueError("quantity precision exceeds lot size precision")
        if quantity.value <= 0:
            raise ValueError("quantity must be positive")
        if not _is_exact_decimal_multiple(quantity.value, self.lot_size.value):
            raise ValueError("quantity is not on the lot grid")
        if quantity.value < self.minimum_quantity.value:
            raise ValueError("quantity is below minimum quantity")
        return quantity

    def validate_order(
        self, *, price: Price, quantity: Quantity
    ) -> tuple[Price, Quantity]:
        """Validate grid and minimum notional without ambient-context rounding."""

        valid_price = self.validate_price(price)
        valid_quantity = self.validate_quantity(quantity)
        price_digits = len(valid_price.amount.as_tuple().digits)
        quantity_digits = len(valid_quantity.value.as_tuple().digits)
        exact_context = Context(
            prec=max(1, price_digits + quantity_digits),
            Emax=MAX_EMAX,
            Emin=MIN_EMIN,
        )
        try:
            notional = exact_context.multiply(
                valid_price.amount, valid_quantity.value
            )
        except DecimalException as exc:
            raise ValueError("notional is outside the supported Decimal range") from exc
        if not notional.is_finite() or exact_context.flags[Inexact]:
            raise ValueError("notional is outside the supported Decimal range")
        if notional < self.minimum_notional.amount:
            raise ValueError("order is below minimum notional")
        return valid_price, valid_quantity
