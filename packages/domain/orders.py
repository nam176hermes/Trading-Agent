"""Strict order instructions and a pure deterministic lifecycle reducer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Mapping
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from .clock import require_utc
from .instruments import InstrumentId
from .primitives import Money, OrderQuantity, Price, Quantity


NonEmptyText = Annotated[str, Field(min_length=1)]
CanonicalIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
]
CanonicalReason = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$"),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


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
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    MARKET_IF_TOUCHED = "market_if_touched"
    LIMIT_IF_TOUCHED = "limit_if_touched"
    TRAILING_STOP = "trailing_stop"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    DAY = "day"
    GTD = "gtd"


class OrderStatus(str, Enum):
    INITIALIZED = "INITIALIZED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PENDING_UPDATE = "PENDING_UPDATE"
    PENDING_CANCEL = "PENDING_CANCEL"
    TRIGGERED = "TRIGGERED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    DENIED = "DENIED"


TERMINAL_ORDER_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.DENIED,
    }
)


ORDER_STATUS_TRANSITIONS: Mapping[OrderStatus, frozenset[OrderStatus]] = (
    MappingProxyType(
        {
            OrderStatus.INITIALIZED: frozenset(
                {OrderStatus.SUBMITTED, OrderStatus.DENIED}
            ),
            OrderStatus.SUBMITTED: frozenset(
                {
                    OrderStatus.ACCEPTED,
                    OrderStatus.PENDING_CANCEL,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                    OrderStatus.REJECTED,
                }
            ),
            OrderStatus.ACCEPTED: frozenset(
                {
                    OrderStatus.PENDING_UPDATE,
                    OrderStatus.PENDING_CANCEL,
                    OrderStatus.TRIGGERED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                }
            ),
            OrderStatus.PENDING_UPDATE: frozenset(
                {
                    OrderStatus.ACCEPTED,
                    OrderStatus.PENDING_CANCEL,
                    OrderStatus.TRIGGERED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                }
            ),
            OrderStatus.PENDING_CANCEL: frozenset(
                {
                    OrderStatus.ACCEPTED,
                    OrderStatus.TRIGGERED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                }
            ),
            OrderStatus.TRIGGERED: frozenset(
                {
                    OrderStatus.PENDING_UPDATE,
                    OrderStatus.PENDING_CANCEL,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                }
            ),
            OrderStatus.PARTIALLY_FILLED: frozenset(
                {
                    OrderStatus.PENDING_UPDATE,
                    OrderStatus.PENDING_CANCEL,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELED,
                    OrderStatus.EXPIRED,
                }
            ),
            OrderStatus.FILLED: frozenset(),
            OrderStatus.CANCELED: frozenset(),
            OrderStatus.EXPIRED: frozenset(),
            OrderStatus.REJECTED: frozenset(),
            OrderStatus.DENIED: frozenset(),
        }
    )
)


_LIMIT_PRICE_TYPES = frozenset(
    {OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LIMIT_IF_TOUCHED}
)
_TRIGGER_PRICE_TYPES = frozenset(
    {
        OrderType.STOP_MARKET,
        OrderType.STOP_LIMIT,
        OrderType.MARKET_IF_TOUCHED,
        OrderType.LIMIT_IF_TOUCHED,
    }
)
_POST_ONLY_TIME_IN_FORCE = frozenset(
    {TimeInForce.GTC, TimeInForce.DAY, TimeInForce.GTD}
)
_REASON_REQUIRED_STATUSES = frozenset(
    {
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.DENIED,
    }
)


class OrderIntent(DomainModel):
    """A typed immutable order instruction; identity fields confer no authority."""

    intent_id: UUID
    risk_decision_id: UUID
    client_order_id: CanonicalIdentifier
    venue_order_id: CanonicalIdentifier | None = None
    strategy_id: CanonicalIdentifier
    trader_id: CanonicalIdentifier
    account_id: CanonicalIdentifier
    execution_client_id: CanonicalIdentifier
    order_list_id: CanonicalIdentifier | None = None
    instrument: InstrumentId
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    quantity: OrderQuantity
    limit_price: Price | None = None
    trigger_price: Price | None = None
    trailing_offset: Price | None = None
    gtd_expiry: datetime | None = None
    post_only: bool = False
    reduce_only: bool = False
    requested_at: datetime
    schema_version: NonEmptyText

    @field_validator("quantity")
    @classmethod
    def _positive_quantity(cls, value: OrderQuantity) -> OrderQuantity:
        if value.value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @field_validator("requested_at", "gtd_expiry")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _validate_instruction(self) -> "OrderIntent":
        requires_limit = self.order_type in _LIMIT_PRICE_TYPES
        if requires_limit and self.limit_price is None:
            raise ValueError(f"{self.order_type.value} orders require limit_price")
        if not requires_limit and self.limit_price is not None:
            raise ValueError(f"{self.order_type.value} orders forbid limit_price")

        requires_trigger = self.order_type in _TRIGGER_PRICE_TYPES
        if requires_trigger and self.trigger_price is None:
            raise ValueError(f"{self.order_type.value} orders require trigger_price")
        if not requires_trigger and self.trigger_price is not None:
            raise ValueError(f"{self.order_type.value} orders forbid trigger_price")

        requires_trailing = self.order_type is OrderType.TRAILING_STOP
        if requires_trailing and self.trailing_offset is None:
            raise ValueError("trailing_stop orders require trailing_offset")
        if not requires_trailing and self.trailing_offset is not None:
            raise ValueError(f"{self.order_type.value} orders forbid trailing_offset")

        if self.time_in_force is TimeInForce.GTD:
            if self.gtd_expiry is None:
                raise ValueError("GTD orders require gtd_expiry")
            if self.gtd_expiry <= self.requested_at:
                raise ValueError("gtd_expiry must be after requested_at")
        elif self.gtd_expiry is not None:
            raise ValueError("gtd_expiry is valid only for GTD orders")

        if self.post_only and (
            self.order_type not in _LIMIT_PRICE_TYPES
            or self.time_in_force not in _POST_ONLY_TIME_IN_FORCE
        ):
            raise ValueError("post_only requires a resting limit instruction")
        return self


def _event_fingerprint(event: "OrderEvent") -> str:
    occurred_at = event.occurred_at.astimezone(UTC).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    material = json.dumps(
        {
            "event_id": str(event.event_id),
            "occurred_at": occurred_at,
            "order_id": str(event.order_id),
            "reason": event.reason,
            "schema_version": event.schema_version,
            "sequence": event.sequence,
            "target_status": event.target_status.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class OrderEvent(DomainModel):
    """One immutable order lifecycle observation with canonical content identity."""

    event_id: UUID
    order_id: UUID
    sequence: Annotated[StrictInt, Field(gt=0)]
    target_status: OrderStatus
    occurred_at: datetime
    reason: CanonicalReason | None = None
    schema_version: NonEmptyText
    event_fingerprint: Sha256Hex = ""

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_and_set_fingerprint(self) -> "OrderEvent":
        if self.target_status in _REASON_REQUIRED_STATUSES and self.reason is None:
            raise ValueError(f"reason is required for {self.target_status.value}")
        expected = _event_fingerprint(self)
        if self.event_fingerprint and self.event_fingerprint != expected:
            raise ValueError("event_fingerprint does not match canonical event fields")
        object.__setattr__(self, "event_fingerprint", expected)
        return self


class OrderState(DomainModel):
    """Frozen reducer state containing enough history to detect event-id reuse."""

    order_id: UUID
    status: OrderStatus = OrderStatus.INITIALIZED
    last_sequence: Annotated[StrictInt, Field(ge=0)] = 0
    applied_events: tuple[OrderEvent, ...] = ()
    schema_version: NonEmptyText

    @model_validator(mode="after")
    def _validate_history(self) -> "OrderState":
        if not self.applied_events:
            return self
        event_ids: set[UUID] = set()
        previous_sequence = 0
        for event in self.applied_events:
            if event.order_id != self.order_id:
                raise ValueError("applied event order_id does not match state order_id")
            if event.event_id in event_ids:
                raise ValueError("applied_events contain a duplicate event_id")
            if event.sequence <= previous_sequence:
                raise ValueError("applied_events contain non-increasing sequence")
            event_ids.add(event.event_id)
            previous_sequence = event.sequence
        if previous_sequence != self.last_sequence:
            raise ValueError("last_sequence does not match applied_events")
        if self.applied_events[-1].target_status is not self.status:
            raise ValueError("status does not match the last applied event")
        return self


class OrderReductionError(ValueError):
    """A fail-closed order lifecycle reduction error."""


def reduce_order(state: OrderState, event: OrderEvent) -> OrderState:
    """Purely apply one validated lifecycle event to a frozen order state."""

    if event.order_id != state.order_id:
        raise OrderReductionError(
            f"order id mismatch: state {state.order_id}, event {event.order_id}"
        )

    duplicate = next(
        (
            applied
            for applied in state.applied_events
            if applied.event_id == event.event_id
        ),
        None,
    )
    if duplicate is not None:
        if duplicate.event_fingerprint == event.event_fingerprint:
            return state
        raise OrderReductionError(f"event id conflict: {event.event_id}")

    if event.sequence <= state.last_sequence:
        raise OrderReductionError(
            "non-increasing sequence: "
            f"event {event.sequence}, state {state.last_sequence}"
        )
    if state.status in TERMINAL_ORDER_STATUSES:
        raise OrderReductionError(f"terminal order status cannot change: {state.status.value}")
    if event.target_status not in ORDER_STATUS_TRANSITIONS[state.status]:
        raise OrderReductionError(
            "forbidden order transition: "
            f"{state.status.value} -> {event.target_status.value}"
        )

    return OrderState(
        order_id=state.order_id,
        status=event.target_status,
        last_sequence=event.sequence,
        applied_events=state.applied_events + (event,),
        schema_version=state.schema_version,
    )


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
    def _utc_fill(cls, value: datetime) -> datetime:
        return require_utc(value)
