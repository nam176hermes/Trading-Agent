"""Strict order instructions and a pure deterministic lifecycle reducer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, Iterable, Literal, Mapping, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from .clock import require_utc
from .instruments import InstrumentConstraints, InstrumentDefinition, InstrumentId
from .primitives import Money, OrderQuantity, Price


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


class OrderCancelResolution(str, Enum):
    """Explicit resolution of an outstanding cancellation request."""

    REJECTED = "REJECTED"


TERMINAL_ORDER_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
        OrderStatus.REJECTED,
        OrderStatus.DENIED,
    }
)


OrderStateKey = tuple[OrderStatus, bool]
OrderObservationKey = tuple[OrderStatus, OrderCancelResolution | None]


def _immutable_transitions(
    values: Mapping[OrderObservationKey, OrderStateKey],
) -> Mapping[OrderObservationKey, OrderStateKey]:
    return MappingProxyType(dict(values))


# Sequence orders observations, not causal venue messages. A venue ACK may be
# absent or observed after a fill/terminal report, so SUBMITTED retains those
# explicit observed-ACK race edges. Cancellation lineage is orthogonal to the
# visible status and is therefore part of every table key and result.
ORDER_STATUS_TRANSITIONS: Mapping[
    OrderStateKey, Mapping[OrderObservationKey, OrderStateKey]
] = MappingProxyType(
    {
        (OrderStatus.INITIALIZED, False): _immutable_transitions(
            {
                (OrderStatus.SUBMITTED, None): (OrderStatus.SUBMITTED, False),
                (OrderStatus.DENIED, None): (OrderStatus.DENIED, False),
            }
        ),
        (OrderStatus.SUBMITTED, False): _immutable_transitions(
            {
                (OrderStatus.ACCEPTED, None): (OrderStatus.ACCEPTED, False),
                (OrderStatus.PENDING_CANCEL, None): (
                    OrderStatus.PENDING_CANCEL,
                    True,
                ),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    False,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
                (OrderStatus.REJECTED, None): (OrderStatus.REJECTED, False),
            }
        ),
        (OrderStatus.ACCEPTED, False): _immutable_transitions(
            {
                (OrderStatus.PENDING_UPDATE, None): (
                    OrderStatus.PENDING_UPDATE,
                    False,
                ),
                (OrderStatus.PENDING_CANCEL, None): (
                    OrderStatus.PENDING_CANCEL,
                    True,
                ),
                (OrderStatus.TRIGGERED, None): (OrderStatus.TRIGGERED, False),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    False,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.PENDING_UPDATE, False): _immutable_transitions(
            {
                (OrderStatus.ACCEPTED, None): (OrderStatus.ACCEPTED, False),
                (OrderStatus.PENDING_CANCEL, None): (
                    OrderStatus.PENDING_CANCEL,
                    True,
                ),
                (OrderStatus.TRIGGERED, None): (OrderStatus.TRIGGERED, False),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    False,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.TRIGGERED, False): _immutable_transitions(
            {
                (OrderStatus.PENDING_UPDATE, None): (
                    OrderStatus.PENDING_UPDATE,
                    False,
                ),
                (OrderStatus.PENDING_CANCEL, None): (
                    OrderStatus.PENDING_CANCEL,
                    True,
                ),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    False,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.PARTIALLY_FILLED, False): _immutable_transitions(
            {
                (OrderStatus.PENDING_UPDATE, None): (
                    OrderStatus.PENDING_UPDATE,
                    False,
                ),
                (OrderStatus.PENDING_CANCEL, None): (
                    OrderStatus.PENDING_CANCEL,
                    True,
                ),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    False,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.PENDING_CANCEL, True): _immutable_transitions(
            {
                (OrderStatus.ACCEPTED, OrderCancelResolution.REJECTED): (
                    OrderStatus.ACCEPTED,
                    False,
                ),
                (OrderStatus.TRIGGERED, None): (OrderStatus.TRIGGERED, True),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    True,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.TRIGGERED, True): _immutable_transitions(
            {
                (OrderStatus.TRIGGERED, OrderCancelResolution.REJECTED): (
                    OrderStatus.TRIGGERED,
                    False,
                ),
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    True,
                ),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.PARTIALLY_FILLED, True): _immutable_transitions(
            {
                (OrderStatus.PARTIALLY_FILLED, None): (
                    OrderStatus.PARTIALLY_FILLED,
                    True,
                ),
                (
                    OrderStatus.PARTIALLY_FILLED,
                    OrderCancelResolution.REJECTED,
                ): (OrderStatus.PARTIALLY_FILLED, False),
                (OrderStatus.FILLED, None): (OrderStatus.FILLED, False),
                (OrderStatus.CANCELED, None): (OrderStatus.CANCELED, False),
                (OrderStatus.EXPIRED, None): (OrderStatus.EXPIRED, False),
            }
        ),
        (OrderStatus.FILLED, False): _immutable_transitions({}),
        (OrderStatus.CANCELED, False): _immutable_transitions({}),
        (OrderStatus.EXPIRED, False): _immutable_transitions({}),
        (OrderStatus.REJECTED, False): _immutable_transitions({}),
        (OrderStatus.DENIED, False): _immutable_transitions({}),
    }
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


class _OrderEventMaterial(DomainModel):
    event_id: UUID
    order_id: UUID
    sequence: Annotated[StrictInt, Field(gt=0)]
    target_status: OrderStatus
    occurred_at: datetime
    reason: CanonicalReason | None = None
    cancel_resolution: OrderCancelResolution | None = None
    schema_version: Literal["2.0"]

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_reason(self) -> "_OrderEventMaterial":
        if self.target_status in _REASON_REQUIRED_STATUSES and self.reason is None:
            raise ValueError(f"reason is required for {self.target_status.value}")
        if self.cancel_resolution is not None and self.reason is None:
            raise ValueError("reason is required for cancel_resolution")
        return self


def _event_fingerprint(event: _OrderEventMaterial) -> str:
    occurred_at = event.occurred_at.astimezone(UTC).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    material = json.dumps(
        {
            "event_id": str(event.event_id),
            "cancel_resolution": (
                None
                if event.cancel_resolution is None
                else event.cancel_resolution.value
            ),
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


class OrderEvent(_OrderEventMaterial):
    """A versioned lifecycle observation ordered solely by sequence.

    Producers must carry the required canonical fingerprint. ``create`` is the
    local construction path which validates material first and derives it.
    ``occurred_at`` records an observation and may move backward under clock
    skew; it is never an ordering authority.
    """

    event_fingerprint: Sha256Hex

    @model_validator(mode="after")
    def _validate_and_set_fingerprint(self) -> "OrderEvent":
        expected = _event_fingerprint(self)
        if self.event_fingerprint != expected:
            raise ValueError("event_fingerprint does not match canonical event fields")
        return self

    @classmethod
    def create(
        cls,
        *,
        event_id: UUID,
        order_id: UUID,
        sequence: int,
        target_status: OrderStatus,
        occurred_at: datetime,
        reason: str | None = None,
        cancel_resolution: OrderCancelResolution | None = None,
        schema_version: Literal["2.0"] = "2.0",
    ) -> "OrderEvent":
        """Validate event material and attach its required canonical digest."""

        material = _OrderEventMaterial(
            event_id=event_id,
            order_id=order_id,
            sequence=sequence,
            target_status=target_status,
            occurred_at=occurred_at,
            reason=reason,
            cancel_resolution=cancel_resolution,
            schema_version=schema_version,
        )
        values = {
            name: getattr(material, name) for name in material.__class__.model_fields
        }
        return cls(**values, event_fingerprint=_event_fingerprint(material))


class OrderReductionError(ValueError):
    """A fail-closed order lifecycle reduction error."""


def _next_state_key(
    current: OrderStateKey, event: OrderEvent
) -> OrderStateKey:
    if current[0] in TERMINAL_ORDER_STATUSES:
        raise OrderReductionError(
            f"terminal order status cannot change: {current[0].value}"
        )
    observation = (event.target_status, event.cancel_resolution)
    next_key = ORDER_STATUS_TRANSITIONS.get(current, {}).get(observation)
    if next_key is None:
        if current[1] and event.target_status is OrderStatus.PENDING_UPDATE:
            raise OrderReductionError("pending cancel forbids order update")
        raise OrderReductionError(
            "forbidden order transition: "
            f"{current[0].value}/{current[1]} -> "
            f"{event.target_status.value}/{event.cancel_resolution}"
        )
    return next_key


def _canonical_event(value: OrderEvent) -> OrderEvent:
    raw = {name: getattr(value, name) for name in OrderEvent.model_fields}
    return OrderEvent.model_validate(raw)


class OrderState(DomainModel):
    """Frozen state whose complete history is replayed on every ingress."""

    order_id: UUID
    status: OrderStatus = OrderStatus.INITIALIZED
    cancel_pending: bool = False
    last_sequence: Annotated[StrictInt, Field(ge=0)] = 0
    applied_events: tuple[OrderEvent, ...] = ()
    schema_version: Literal["2.0"] = "2.0"

    @model_validator(mode="after")
    def _validate_history(self) -> "OrderState":
        if not self.applied_events:
            if (
                self.status is not OrderStatus.INITIALIZED
                or self.cancel_pending
                or self.last_sequence != 0
            ):
                raise ValueError(
                    "empty history requires INITIALIZED status, no pending cancel, "
                    "and sequence zero"
                )
            return self
        event_ids: set[UUID] = set()
        previous_sequence = 0
        current: OrderStateKey = (OrderStatus.INITIALIZED, False)
        canonical_events: list[OrderEvent] = []
        for event in self.applied_events:
            try:
                canonical = _canonical_event(event)
            except (AttributeError, TypeError, ValidationError) as exc:
                raise ValueError("applied_events contain invalid order event") from exc
            if canonical.order_id != self.order_id:
                raise ValueError("applied event order_id does not match state order_id")
            if canonical.event_id in event_ids:
                raise ValueError("applied_events contain a duplicate event_id")
            if canonical.sequence <= previous_sequence:
                raise ValueError("applied_events contain non-increasing sequence")
            try:
                current = _next_state_key(current, canonical)
            except OrderReductionError as exc:
                raise ValueError(str(exc)) from exc
            event_ids.add(canonical.event_id)
            previous_sequence = canonical.sequence
            canonical_events.append(canonical)
        if previous_sequence != self.last_sequence:
            raise ValueError("last_sequence does not match applied_events")
        if current != (self.status, self.cancel_pending):
            raise ValueError("status or cancel_pending does not match applied_events")
        object.__setattr__(self, "applied_events", tuple(canonical_events))
        return self


def _canonical_state(value: OrderState) -> OrderState:
    raw = {name: getattr(value, name) for name in OrderState.model_fields}
    return OrderState.model_validate(raw)


def reduce_order(state: OrderState, event: OrderEvent) -> OrderState:
    """Defensively validate and purely apply one sequence-ordered observation."""

    try:
        canonical_state = _canonical_state(state)
    except (AttributeError, TypeError, ValidationError) as exc:
        raise OrderReductionError("invalid order state") from exc
    try:
        canonical_event = _canonical_event(event)
    except (AttributeError, TypeError, ValidationError) as exc:
        raise OrderReductionError("invalid order event") from exc

    if canonical_event.order_id != canonical_state.order_id:
        raise OrderReductionError(
            "order id mismatch: "
            f"state {canonical_state.order_id}, event {canonical_event.order_id}"
        )

    duplicate = next(
        (
            applied
            for applied in canonical_state.applied_events
            if applied.event_id == canonical_event.event_id
        ),
        None,
    )
    if duplicate is not None:
        if duplicate.event_fingerprint == canonical_event.event_fingerprint:
            return state
        raise OrderReductionError(f"event id conflict: {canonical_event.event_id}")

    if canonical_event.sequence <= canonical_state.last_sequence:
        raise OrderReductionError(
            "non-increasing sequence: "
            f"event {canonical_event.sequence}, state {canonical_state.last_sequence}"
        )
    next_status, next_cancel_pending = _next_state_key(
        (canonical_state.status, canonical_state.cancel_pending), canonical_event
    )

    return OrderState(
        order_id=canonical_state.order_id,
        status=next_status,
        cancel_pending=next_cancel_pending,
        last_sequence=canonical_event.sequence,
        applied_events=canonical_state.applied_events + (canonical_event,),
        schema_version=canonical_state.schema_version,
    )


class LiquiditySide(str, Enum):
    """Closed venue liquidity classification for one execution report."""

    MAKER = "maker"
    TAKER = "taker"


class ReconciliationSource(str, Enum):
    """Closed provenance source for an execution report reconciliation."""

    VENUE = "venue"
    DROP_COPY = "drop_copy"
    CLEARING = "clearing"


class FillReportStatus(str, Enum):
    """Explicit execution-report lifecycle or correction classification."""

    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    DUPLICATE = "DUPLICATE"
    CORRECTION = "CORRECTION"
    BUST = "BUST"


_REFERENCE_FIELD_BY_FILL_STATUS: Mapping[FillReportStatus, str | None] = (
    MappingProxyType(
        {
            FillReportStatus.PARTIALLY_FILLED: None,
            FillReportStatus.FILLED: None,
            FillReportStatus.DUPLICATE: "duplicate_of_execution_id",
            FillReportStatus.CORRECTION: "correction_of_execution_id",
            FillReportStatus.BUST: "bust_of_execution_id",
        }
    )
)


def _canonical_instrument_definition(value: object) -> InstrumentDefinition:
    """Rebuild an instrument definition so forged dataclass instances fail closed."""

    if not isinstance(value, InstrumentDefinition):
        raise ValueError("instrument_definition must be an InstrumentDefinition")
    try:
        return InstrumentDefinition(
            instrument_id=value.instrument_id,
            raw_symbol=value.raw_symbol,
            asset_class=value.asset_class,
            base_currency=value.base_currency,
            quote_currency=value.quote_currency,
            settlement_currency=value.settlement_currency,
            tick_size=value.tick_size,
            size_increment=value.size_increment,
            minimum_quantity=value.minimum_quantity,
            maximum_quantity=value.maximum_quantity,
            minimum_notional=value.minimum_notional,
            maximum_notional=value.maximum_notional,
            multiplier=value.multiplier,
            margin=value.margin,
            session_calendar=value.session_calendar,
            provenance=value.provenance,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("instrument_definition is not canonical") from exc


def _canonical_order_quantity(value: object, field_name: str) -> OrderQuantity:
    """Rebuild an order quantity so Pydantic instance shortcuts cannot bypass it."""

    if not isinstance(value, OrderQuantity):
        raise ValueError(f"{field_name} must be an OrderQuantity")
    try:
        return OrderQuantity(value.value, value.precision)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not canonical") from exc


def _canonical_price(value: object, field_name: str) -> Price:
    if not isinstance(value, Price):
        raise ValueError(f"{field_name} must be a Price")
    try:
        return Price(value.amount, value.currency)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not canonical") from exc


def _canonical_money(value: object, field_name: str) -> Money:
    if not isinstance(value, Money):
        raise ValueError(f"{field_name} must be Money")
    try:
        return Money(value.amount, value.currency)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not canonical") from exc


class FillEvent(DomainModel):
    """Immutable version-2 execution report, independent of any fill reducer.

    ``execution_id`` is the canonical execution identity.  Correction, bust,
    and duplicate reports carry a typed reference to one prior execution; the
    contract deliberately rejects the pre-WS-03 ``FillEvent`` wire shape.
    """

    execution_id: UUID
    order_id: UUID
    report_sequence: Annotated[StrictInt, Field(gt=0)]
    venue_trade_id: CanonicalIdentifier
    instrument_definition: InstrumentDefinition
    side: OrderSide
    liquidity_side: LiquiditySide
    status: FillReportStatus
    quantity: OrderQuantity
    cumulative_fill_quantity: OrderQuantity
    leaves_quantity: OrderQuantity
    order_quantity: OrderQuantity
    last_fill_price: Price
    average_fill_price: Price
    commission: Money
    reconciliation_source: ReconciliationSource
    duplicate_of_execution_id: UUID | None = None
    correction_of_execution_id: UUID | None = None
    bust_of_execution_id: UUID | None = None
    filled_at: datetime
    schema_version: Literal["2.0"]

    @field_validator("instrument_definition")
    @classmethod
    def _canonical_definition(cls, value: InstrumentDefinition) -> InstrumentDefinition:
        return _canonical_instrument_definition(value)

    @field_validator(
        "quantity",
        "cumulative_fill_quantity",
        "leaves_quantity",
        "order_quantity",
    )
    @classmethod
    def _canonical_quantities(cls, value: OrderQuantity, info: Any) -> OrderQuantity:
        return _canonical_order_quantity(value, info.field_name)

    @field_validator("last_fill_price", "average_fill_price")
    @classmethod
    def _canonical_prices(cls, value: Price, info: Any) -> Price:
        return _canonical_price(value, info.field_name)

    @field_validator("commission")
    @classmethod
    def _canonical_commission(cls, value: Money) -> Money:
        return _canonical_money(value, "commission")

    @field_validator("filled_at")
    @classmethod
    def _utc_fill(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_execution_report(self) -> "FillEvent":
        constraints = InstrumentConstraints(self.instrument_definition)
        definition = constraints.definition
        quantity_fields = (
            ("quantity", self.quantity, True),
            ("cumulative_fill_quantity", self.cumulative_fill_quantity, True),
            ("leaves_quantity", self.leaves_quantity, False),
            ("order_quantity", self.order_quantity, True),
        )
        increment = definition.size_increment
        increment_scaled = _quantity_scaled(increment, increment.precision)
        for name, quantity, require_positive in quantity_fields:
            if quantity.precision != increment.precision:
                raise ValueError(f"{name} must use instrument quantity precision")
            scaled = _quantity_scaled(quantity, increment.precision)
            if scaled % increment_scaled:
                raise ValueError(f"{name} must be on the instrument size increment grid")
            if require_positive and quantity.value <= 0:
                raise ValueError(f"{name} must be positive")
        try:
            constraints.validate_quantity(self.order_quantity)
        except ValueError as exc:
            raise ValueError(f"order_quantity is invalid for instrument: {exc}") from exc

        quantity_scaled = _quantity_scaled(self.quantity, increment.precision)
        cumulative_scaled = _quantity_scaled(
            self.cumulative_fill_quantity, increment.precision
        )
        leaves_scaled = _quantity_scaled(self.leaves_quantity, increment.precision)
        order_scaled = _quantity_scaled(self.order_quantity, increment.precision)
        if cumulative_scaled < quantity_scaled:
            raise ValueError("cumulative_fill_quantity must be at least quantity")
        if cumulative_scaled + leaves_scaled != order_scaled:
            raise ValueError("order_quantity must equal cumulative_fill_quantity plus leaves_quantity")
        if self.status is FillReportStatus.FILLED and leaves_scaled != 0:
            raise ValueError("terminal FILLED reports must have zero leaves_quantity")

        for name, price in (
            ("last_fill_price", self.last_fill_price),
            ("average_fill_price", self.average_fill_price),
        ):
            try:
                constraints.validate_price(price)
            except ValueError as exc:
                raise ValueError(f"{name} is invalid for instrument: {exc}") from exc

        if self.commission.amount < 0:
            raise ValueError("commission must be non-negative")

        references = {
            "duplicate_of_execution_id": self.duplicate_of_execution_id,
            "correction_of_execution_id": self.correction_of_execution_id,
            "bust_of_execution_id": self.bust_of_execution_id,
        }
        expected_reference = _REFERENCE_FIELD_BY_FILL_STATUS[self.status]
        supplied_references = tuple(
            name for name, value in references.items() if value is not None
        )
        if expected_reference is None:
            if supplied_references:
                raise ValueError("normal execution reports must not carry an identity reference")
        elif supplied_references != (expected_reference,):
            raise ValueError(
                f"{self.status.value} reports require only {expected_reference}"
            )
        if any(value == self.execution_id for value in references.values() if value is not None):
            raise ValueError("execution report references must not self-reference")
        return self

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Revalidate forged ``model_copy`` and ``model_construct`` instances."""

        if isinstance(obj, cls):
            try:
                obj = {name: getattr(obj, name) for name in cls.model_fields}
            except AttributeError as exc:
                raise ValidationError.from_exception_data(
                    cls.__name__,
                    [
                        {
                            "type": "missing",
                            "loc": ("execution_report",),
                            "input": obj,
                        }
                    ],
                ) from exc
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    def _canonical_public_instance(self) -> "FillEvent":
        return type(self).model_validate(self)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Serialize only a fresh, fully revalidated execution report."""

        canonical = self._canonical_public_instance()
        return BaseModel.model_dump(canonical, *args, **kwargs)

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        """Serialize only a fresh, fully revalidated execution report."""

        canonical = self._canonical_public_instance()
        return BaseModel.model_dump_json(canonical, *args, **kwargs)


def _quantity_scaled(quantity: OrderQuantity, precision: int) -> int:
    """Return a quantity's exact scaled integer with no Decimal arithmetic."""

    from .primitives import decimal_to_scaled_integer

    return decimal_to_scaled_integer(quantity.value, precision)


def validate_fill_report_batch(reports: Iterable[FillEvent]) -> tuple[FillEvent, ...]:
    """Fail closed for duplicate executions and per-order report regressions.

    This is deliberately a pure collection validator, not a fill reducer: it
    neither derives balances nor stores state.  Callers define report order by
    their supplied immutable sequence.
    """

    canonical_reports: list[FillEvent] = []
    execution_ids: set[UUID] = set()
    last_sequence_by_order: dict[UUID, int] = {}
    for report in reports:
        try:
            canonical = FillEvent.model_validate(report)
        except (AttributeError, TypeError, ValidationError) as exc:
            raise ValueError("invalid execution report") from exc
        if canonical.execution_id in execution_ids:
            raise ValueError(f"duplicate execution_id: {canonical.execution_id}")
        previous_sequence = last_sequence_by_order.get(canonical.order_id)
        if (
            previous_sequence is not None
            and canonical.report_sequence <= previous_sequence
        ):
            raise ValueError(
                "non-increasing report_sequence for order_id "
                f"{canonical.order_id}: {canonical.report_sequence} after "
                f"{previous_sequence}"
            )
        execution_ids.add(canonical.execution_id)
        last_sequence_by_order[canonical.order_id] = canonical.report_sequence
        canonical_reports.append(canonical)
    return tuple(canonical_reports)
