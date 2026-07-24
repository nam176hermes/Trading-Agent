"""Strict order-intent, order-lifecycle, and fill payload contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .clock import require_utc
from .instruments import InstrumentId
from .primitives import Money, Price, Quantity


NonEmptyText = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    CREATED = "created"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"


class _OrderBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    instrument: InstrumentId
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: Quantity
    limit_price: Price | None = None
    schema_version: NonEmptyText

    @field_validator("quantity")
    @classmethod
    def _positive_quantity(cls, value: Quantity) -> Quantity:
        if value.value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @model_validator(mode="after")
    def _price_matches_type(self) -> "_OrderBase":
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("market orders reject limit_price")
        return self


class OrderIntent(_OrderBase):
    intent_id: UUID
    risk_decision_id: UUID
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class OrderEvent(_OrderBase):
    order_id: UUID
    intent_id: UUID
    status: OrderStatus
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class FillEvent(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    fill_id: UUID
    order_id: UUID
    instrument: InstrumentId
    side: OrderSide
    quantity: Quantity
    price: Price
    fees: Money
    filled_at: datetime
    schema_version: NonEmptyText

    @field_validator("quantity")
    @classmethod
    def _positive_quantity(cls, value: Quantity) -> Quantity:
        if value.value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @field_validator("fees")
    @classmethod
    def _non_negative_fees(cls, value: Money) -> Money:
        if value.amount < 0:
            raise ValueError("fees must be non-negative")
        return value

    @field_validator("filled_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)
