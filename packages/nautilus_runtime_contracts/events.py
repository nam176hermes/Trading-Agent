"""Closed P1 event vocabulary backed by qualified Nautilus observations."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, TypeAlias
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, model_validator

from packages.domain import FiniteDecimal
from packages.engine_contracts import CanonicalUtcDateTime, Sha256Hex, SourceCommit


P1_EVENT_SCHEMA = "nautilus-p1-event-stream-v1"
Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
]
Sequence = Annotated[StrictInt, Field(gt=0, le=9_223_372_036_854_775_807)]
Count = Annotated[StrictInt, Field(ge=0, le=9_223_372_036_854_775_807)]


class P1EventModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    schema_version: Literal["nautilus-p1-event-stream-v1"]
    sequence: Sequence
    simulation_time: CanonicalUtcDateTime


class P1RunStarted(P1EventModel):
    event_type: Literal["RunStarted"]
    origin: Literal["CONTROL_PLANE"]
    native_type: None
    runtime_family: Literal["cython-v1"]
    engine_version: Literal["1.231.0"]
    upstream_commit: SourceCommit
    closure_digest: Sha256Hex
    config_digest: Sha256Hex
    catalog_digest: Sha256Hex
    data_digest: Sha256Hex


class P1TargetAccepted(P1EventModel):
    event_type: Literal["TargetAccepted"]
    origin: Literal["CONTROL_PLANE"]
    native_type: None
    target_id: Identifier
    target_weight: FiniteDecimal

    @model_validator(mode="after")
    def _long_or_flat(self) -> "P1TargetAccepted":
        if not Decimal(0) <= self.target_weight <= Decimal(1):
            raise ValueError("target_weight must be long or flat")
        return self


class P1TargetQuantityPlanned(P1EventModel):
    event_type: Literal["TargetQuantityPlanned"]
    origin: Literal["CONTROL_PLANE"]
    native_type: None
    target_id: Identifier
    quantity: FiniteDecimal

    @model_validator(mode="after")
    def _non_negative_quantity(self) -> "P1TargetQuantityPlanned":
        if self.quantity < 0:
            raise ValueError("planned quantity must be non-negative")
        return self


class P1OrderSubmitted(P1EventModel):
    event_type: Literal["OrderSubmitted"]
    origin: Literal["CONTROL_PLANE"]
    native_type: Literal["Order"]
    client_order_id: Identifier
    native_order_id: Identifier
    side: Literal["BUY", "SELL"]
    quantity: FiniteDecimal
    order_type: Literal["MARKET"]

    @model_validator(mode="after")
    def _positive_quantity(self) -> "P1OrderSubmitted":
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        return self


class P1Fill(P1EventModel):
    event_type: Literal["Fill"]
    origin: Literal["NAUTILUS_CALLBACK"]
    native_type: Literal["OrderFilled"]
    client_order_id: Identifier
    native_fill_id: Identifier
    side: Literal["BUY", "SELL"]
    quantity: FiniteDecimal
    price: FiniteDecimal
    fee: FiniteDecimal
    fee_currency: Literal["USDT"]

    @model_validator(mode="after")
    def _valid_fill_amounts(self) -> "P1Fill":
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("fill quantity/price must be positive and fee non-negative")
        return self


class P1PositionObserved(P1EventModel):
    event_type: Literal["PositionObserved"]
    origin: Literal["NAUTILUS_CACHE_OBSERVATION"]
    native_type: Literal["Position"]
    quantity: FiniteDecimal
    average_entry_price: FiniteDecimal
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal

    @model_validator(mode="after")
    def _long_or_flat(self) -> "P1PositionObserved":
        if self.quantity < 0 or self.average_entry_price < 0:
            raise ValueError("P1 position must be long or flat")
        return self


class P1AccountObserved(P1EventModel):
    event_type: Literal["AccountObserved"]
    origin: Literal["NAUTILUS_CACHE_OBSERVATION"]
    native_type: Literal["Account"]
    cash_balance: FiniteDecimal
    fees: FiniteDecimal
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal

    @model_validator(mode="after")
    def _valid_account(self) -> "P1AccountObserved":
        if self.cash_balance < 0 or self.fees < 0:
            raise ValueError("cash and fees must be non-negative")
        return self


class P1RunCompleted(P1EventModel):
    event_type: Literal["RunCompleted"]
    origin: Literal["CONTROL_PLANE"]
    native_type: None
    runtime_family: Literal["cython-v1"]
    engine_version: Literal["1.231.0"]
    upstream_commit: SourceCommit
    closure_digest: Sha256Hex
    target_count: Count
    order_count: Count
    fill_count: Count
    final_cash: FiniteDecimal
    final_position: FiniteDecimal
    fees: FiniteDecimal
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal
    semantic_digest: Sha256Hex

    @model_validator(mode="after")
    def _valid_final_account(self) -> "P1RunCompleted":
        if self.final_cash < 0 or self.final_position < 0 or self.fees < 0:
            raise ValueError("final cash, position, and fees must be non-negative")
        return self


P1Event: TypeAlias = Annotated[
    P1RunStarted
    | P1TargetAccepted
    | P1TargetQuantityPlanned
    | P1OrderSubmitted
    | P1Fill
    | P1PositionObserved
    | P1AccountObserved
    | P1RunCompleted,
    Field(discriminator="event_type"),
]
P1_EVENT_ADAPTER = TypeAdapter(P1Event)
P1_EVENT_MODELS = (
    P1RunStarted,
    P1TargetAccepted,
    P1TargetQuantityPlanned,
    P1OrderSubmitted,
    P1Fill,
    P1PositionObserved,
    P1AccountObserved,
    P1RunCompleted,
)


def event_message_id(request_message_id: UUID, event: P1Event) -> UUID:
    """Return the deterministic raw-envelope identity for one event."""

    return uuid5(
        request_message_id,
        f"{P1_EVENT_SCHEMA}:{event.sequence}:{event.event_type}",
    )
