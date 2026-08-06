"""Pure canonical-domain adapter representations for the Nautilus process edge.

The DTOs here intentionally describe only a stable, internal wire vocabulary.
They do not import the isolated CPython 3.12 engine wheel and must remain free
of runtime authority, I/O, clocks, provider state, and process execution.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from packages.domain import (
    AssetClass,
    Currency,
    CurrencyType,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    MarginRequirements,
    Money,
    OrderCancelResolution,
    OrderEvent,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    OrderType,
    Price,
    ProductType,
    ReconciliationSource,
    TimeInForce,
    require_utc,
)
from packages.domain.primitives import DEFAULT_CURRENCY_REGISTRY


_ADAPTER_VERSION = "nautilus-adapter-v1"
_SCHEMA_VERSION = "2.0"
AdapterT = TypeVar("AdapterT", bound="_AdapterModel")


class NautilusMappingError(ValueError):
    """The one fail-closed error exposed by this mapping boundary."""


class _AdapterModel(BaseModel):
    """Frozen strict DTO base; ingress is always rebuilt by mapping functions."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class NautilusCurrencyTypeV1(str, Enum):
    FIAT = "FIAT"
    STABLECOIN = "STABLECOIN"
    CRYPTO = "CRYPTO"


class NautilusProductTypeV1(str, Enum):
    CRYPTO_SPOT = "CRYPTO_SPOT"
    EQUITY = "EQUITY"


class NautilusAssetClassV1(str, Enum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"


class NautilusOrderSideV1(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class NautilusOrderTypeV1(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    MARKET_IF_TOUCHED = "MARKET_IF_TOUCHED"
    LIMIT_IF_TOUCHED = "LIMIT_IF_TOUCHED"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class NautilusTimeInForceV1(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    DAY = "DAY"
    GTD = "GTD"


class NautilusOrderStatusV1(str, Enum):
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


class NautilusCancelResolutionV1(str, Enum):
    REJECTED = "REJECTED"


class NautilusLiquiditySideV1(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class NautilusFillReportStatusV1(str, Enum):
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    DUPLICATE = "DUPLICATE"
    CORRECTION = "CORRECTION"
    BUST = "BUST"


class NautilusReconciliationSourceV1(str, Enum):
    VENUE = "VENUE"
    DROP_COPY = "DROP_COPY"
    CLEARING = "CLEARING"


def _finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    return value


class NautilusCurrencyV1(_AdapterModel):
    code: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z][A-Z0-9]{0,15}$")
    currency_type: NautilusCurrencyTypeV1
    precision: StrictInt = Field(ge=0, le=18)
    registry_version: str = Field(min_length=1, max_length=64)


class NautilusQuantityV1(_AdapterModel):
    value: Decimal
    precision: StrictInt = Field(ge=0, le=18)

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class NautilusPriceV1(_AdapterModel):
    amount: Decimal
    currency: NautilusCurrencyV1

    @field_validator("amount")
    @classmethod
    def _finite_amount(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class NautilusMoneyV1(_AdapterModel):
    amount: Decimal
    currency: NautilusCurrencyV1

    @field_validator("amount")
    @classmethod
    def _finite_amount(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class NautilusInstrumentIdV1(_AdapterModel):
    symbol: str = Field(min_length=1, max_length=32)
    product_type: NautilusProductTypeV1
    venue: str = Field(min_length=1, max_length=32)


class NautilusMarginRequirementsV1(_AdapterModel):
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal

    @field_validator("initial_margin_rate", "maintenance_margin_rate")
    @classmethod
    def _finite_rate(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class NautilusInstrumentProvenanceV1(_AdapterModel):
    source_id: str = Field(min_length=1, max_length=32)
    source_revision: str = Field(min_length=1, max_length=32)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class NautilusInstrumentDefinitionV1(_AdapterModel):
    instrument_id: NautilusInstrumentIdV1
    raw_symbol: str = Field(min_length=1, max_length=128)
    asset_class: NautilusAssetClassV1
    base_currency: NautilusCurrencyV1 | None
    quote_currency: NautilusCurrencyV1
    settlement_currency: NautilusCurrencyV1
    tick_size: NautilusPriceV1
    size_increment: NautilusQuantityV1
    minimum_quantity: NautilusQuantityV1
    maximum_quantity: NautilusQuantityV1
    minimum_notional: NautilusMoneyV1
    maximum_notional: NautilusMoneyV1
    multiplier: Decimal
    margin: NautilusMarginRequirementsV1 | None
    session_calendar: str = Field(min_length=1, max_length=32)
    provenance: NautilusInstrumentProvenanceV1

    @field_validator("multiplier")
    @classmethod
    def _finite_multiplier(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class NautilusOrderIntentV1(_AdapterModel):
    adapter_version: Literal["nautilus-adapter-v1"]
    schema_version: Literal["2.0"]
    intent_id: UUID
    risk_decision_id: UUID
    client_order_id: str = Field(min_length=1, max_length=64)
    venue_order_id: str | None = Field(default=None, min_length=1, max_length=64)
    strategy_id: str = Field(min_length=1, max_length=64)
    trader_id: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=64)
    execution_client_id: str = Field(min_length=1, max_length=64)
    order_list_id: str | None = Field(default=None, min_length=1, max_length=64)
    instrument: NautilusInstrumentIdV1
    side: NautilusOrderSideV1
    order_type: NautilusOrderTypeV1
    time_in_force: NautilusTimeInForceV1
    quantity: NautilusQuantityV1
    limit_price: NautilusPriceV1 | None = None
    trigger_price: NautilusPriceV1 | None = None
    trailing_offset: NautilusPriceV1 | None = None
    gtd_expiry: datetime | None = None
    post_only: bool
    reduce_only: bool
    requested_at: datetime

    @field_validator("requested_at", "gtd_expiry")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class NautilusOrderEventV1(_AdapterModel):
    adapter_version: Literal["nautilus-adapter-v1"]
    schema_version: Literal["2.0"]
    event_id: UUID
    order_id: UUID
    sequence: StrictInt = Field(gt=0)
    target_status: NautilusOrderStatusV1
    occurred_at: datetime
    reason: str | None = Field(default=None, min_length=1, max_length=64)
    cancel_resolution: NautilusCancelResolutionV1 | None = None
    event_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class NautilusFillEventV1(_AdapterModel):
    adapter_version: Literal["nautilus-adapter-v1"]
    schema_version: Literal["2.0"]
    execution_id: UUID
    order_id: UUID
    report_sequence: StrictInt = Field(gt=0)
    venue_trade_id: str = Field(min_length=1, max_length=64)
    instrument_definition: NautilusInstrumentDefinitionV1
    side: NautilusOrderSideV1
    liquidity_side: NautilusLiquiditySideV1
    status: NautilusFillReportStatusV1
    quantity: NautilusQuantityV1
    cumulative_fill_quantity: NautilusQuantityV1
    leaves_quantity: NautilusQuantityV1
    order_quantity: NautilusQuantityV1
    last_fill_price: NautilusPriceV1
    average_fill_price: NautilusPriceV1
    commission: NautilusMoneyV1
    reconciliation_source: NautilusReconciliationSourceV1
    duplicate_of_execution_id: UUID | None = None
    correction_of_execution_id: UUID | None = None
    bust_of_execution_id: UUID | None = None
    filled_at: datetime

    @field_validator("filled_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


_ORDER_SIDE_TO_ADAPTER = {
    OrderSide.BUY: NautilusOrderSideV1.BUY,
    OrderSide.SELL: NautilusOrderSideV1.SELL,
}
_ORDER_SIDE_FROM_ADAPTER = {
    NautilusOrderSideV1.BUY: OrderSide.BUY,
    NautilusOrderSideV1.SELL: OrderSide.SELL,
}
_ORDER_TYPE_TO_ADAPTER = {
    OrderType.MARKET: NautilusOrderTypeV1.MARKET,
    OrderType.LIMIT: NautilusOrderTypeV1.LIMIT,
    OrderType.STOP_MARKET: NautilusOrderTypeV1.STOP_MARKET,
    OrderType.STOP_LIMIT: NautilusOrderTypeV1.STOP_LIMIT,
    OrderType.MARKET_IF_TOUCHED: NautilusOrderTypeV1.MARKET_IF_TOUCHED,
    OrderType.LIMIT_IF_TOUCHED: NautilusOrderTypeV1.LIMIT_IF_TOUCHED,
    OrderType.TRAILING_STOP: NautilusOrderTypeV1.TRAILING_STOP_MARKET,
}
_ORDER_TYPE_FROM_ADAPTER = {
    NautilusOrderTypeV1.MARKET: OrderType.MARKET,
    NautilusOrderTypeV1.LIMIT: OrderType.LIMIT,
    NautilusOrderTypeV1.STOP_MARKET: OrderType.STOP_MARKET,
    NautilusOrderTypeV1.STOP_LIMIT: OrderType.STOP_LIMIT,
    NautilusOrderTypeV1.MARKET_IF_TOUCHED: OrderType.MARKET_IF_TOUCHED,
    NautilusOrderTypeV1.LIMIT_IF_TOUCHED: OrderType.LIMIT_IF_TOUCHED,
    NautilusOrderTypeV1.TRAILING_STOP_MARKET: OrderType.TRAILING_STOP,
}
_TIF_TO_ADAPTER = {
    TimeInForce.GTC: NautilusTimeInForceV1.GTC,
    TimeInForce.IOC: NautilusTimeInForceV1.IOC,
    TimeInForce.FOK: NautilusTimeInForceV1.FOK,
    TimeInForce.DAY: NautilusTimeInForceV1.DAY,
    TimeInForce.GTD: NautilusTimeInForceV1.GTD,
}
_TIF_FROM_ADAPTER = {
    NautilusTimeInForceV1.GTC: TimeInForce.GTC,
    NautilusTimeInForceV1.IOC: TimeInForce.IOC,
    NautilusTimeInForceV1.FOK: TimeInForce.FOK,
    NautilusTimeInForceV1.DAY: TimeInForce.DAY,
    NautilusTimeInForceV1.GTD: TimeInForce.GTD,
}
_ORDER_STATUS_TO_ADAPTER = {
    OrderStatus.INITIALIZED: NautilusOrderStatusV1.INITIALIZED,
    OrderStatus.SUBMITTED: NautilusOrderStatusV1.SUBMITTED,
    OrderStatus.ACCEPTED: NautilusOrderStatusV1.ACCEPTED,
    OrderStatus.PENDING_UPDATE: NautilusOrderStatusV1.PENDING_UPDATE,
    OrderStatus.PENDING_CANCEL: NautilusOrderStatusV1.PENDING_CANCEL,
    OrderStatus.TRIGGERED: NautilusOrderStatusV1.TRIGGERED,
    OrderStatus.PARTIALLY_FILLED: NautilusOrderStatusV1.PARTIALLY_FILLED,
    OrderStatus.FILLED: NautilusOrderStatusV1.FILLED,
    OrderStatus.CANCELED: NautilusOrderStatusV1.CANCELED,
    OrderStatus.EXPIRED: NautilusOrderStatusV1.EXPIRED,
    OrderStatus.REJECTED: NautilusOrderStatusV1.REJECTED,
    OrderStatus.DENIED: NautilusOrderStatusV1.DENIED,
}
_ORDER_STATUS_FROM_ADAPTER = {
    NautilusOrderStatusV1.INITIALIZED: OrderStatus.INITIALIZED,
    NautilusOrderStatusV1.SUBMITTED: OrderStatus.SUBMITTED,
    NautilusOrderStatusV1.ACCEPTED: OrderStatus.ACCEPTED,
    NautilusOrderStatusV1.PENDING_UPDATE: OrderStatus.PENDING_UPDATE,
    NautilusOrderStatusV1.PENDING_CANCEL: OrderStatus.PENDING_CANCEL,
    NautilusOrderStatusV1.TRIGGERED: OrderStatus.TRIGGERED,
    NautilusOrderStatusV1.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    NautilusOrderStatusV1.FILLED: OrderStatus.FILLED,
    NautilusOrderStatusV1.CANCELED: OrderStatus.CANCELED,
    NautilusOrderStatusV1.EXPIRED: OrderStatus.EXPIRED,
    NautilusOrderStatusV1.REJECTED: OrderStatus.REJECTED,
    NautilusOrderStatusV1.DENIED: OrderStatus.DENIED,
}
_CANCEL_TO_ADAPTER = {
    OrderCancelResolution.REJECTED: NautilusCancelResolutionV1.REJECTED,
}
_CANCEL_FROM_ADAPTER = {
    NautilusCancelResolutionV1.REJECTED: OrderCancelResolution.REJECTED,
}
_FILL_STATUS_TO_ADAPTER = {
    FillReportStatus.PARTIALLY_FILLED: NautilusFillReportStatusV1.PARTIALLY_FILLED,
    FillReportStatus.FILLED: NautilusFillReportStatusV1.FILLED,
    FillReportStatus.DUPLICATE: NautilusFillReportStatusV1.DUPLICATE,
    FillReportStatus.CORRECTION: NautilusFillReportStatusV1.CORRECTION,
    FillReportStatus.BUST: NautilusFillReportStatusV1.BUST,
}
_FILL_STATUS_FROM_ADAPTER = {
    NautilusFillReportStatusV1.PARTIALLY_FILLED: FillReportStatus.PARTIALLY_FILLED,
    NautilusFillReportStatusV1.FILLED: FillReportStatus.FILLED,
    NautilusFillReportStatusV1.DUPLICATE: FillReportStatus.DUPLICATE,
    NautilusFillReportStatusV1.CORRECTION: FillReportStatus.CORRECTION,
    NautilusFillReportStatusV1.BUST: FillReportStatus.BUST,
}
_LIQUIDITY_TO_ADAPTER = {
    LiquiditySide.MAKER: NautilusLiquiditySideV1.MAKER,
    LiquiditySide.TAKER: NautilusLiquiditySideV1.TAKER,
}
_LIQUIDITY_FROM_ADAPTER = {
    NautilusLiquiditySideV1.MAKER: LiquiditySide.MAKER,
    NautilusLiquiditySideV1.TAKER: LiquiditySide.TAKER,
}
_RECONCILIATION_TO_ADAPTER = {
    ReconciliationSource.VENUE: NautilusReconciliationSourceV1.VENUE,
    ReconciliationSource.DROP_COPY: NautilusReconciliationSourceV1.DROP_COPY,
    ReconciliationSource.CLEARING: NautilusReconciliationSourceV1.CLEARING,
}
_RECONCILIATION_FROM_ADAPTER = {
    NautilusReconciliationSourceV1.VENUE: ReconciliationSource.VENUE,
    NautilusReconciliationSourceV1.DROP_COPY: ReconciliationSource.DROP_COPY,
    NautilusReconciliationSourceV1.CLEARING: ReconciliationSource.CLEARING,
}
_PRODUCT_TO_ADAPTER = {
    ProductType.CRYPTO_SPOT: NautilusProductTypeV1.CRYPTO_SPOT,
    ProductType.EQUITY: NautilusProductTypeV1.EQUITY,
}
_PRODUCT_FROM_ADAPTER = {
    NautilusProductTypeV1.CRYPTO_SPOT: ProductType.CRYPTO_SPOT,
    NautilusProductTypeV1.EQUITY: ProductType.EQUITY,
}
_ASSET_TO_ADAPTER = {
    AssetClass.CRYPTO: NautilusAssetClassV1.CRYPTO,
    AssetClass.EQUITY: NautilusAssetClassV1.EQUITY,
}
_ASSET_FROM_ADAPTER = {
    NautilusAssetClassV1.CRYPTO: AssetClass.CRYPTO,
    NautilusAssetClassV1.EQUITY: AssetClass.EQUITY,
}
_CURRENCY_TYPE_TO_ADAPTER = {
    CurrencyType.FIAT: NautilusCurrencyTypeV1.FIAT,
    CurrencyType.STABLECOIN: NautilusCurrencyTypeV1.STABLECOIN,
    CurrencyType.CRYPTO: NautilusCurrencyTypeV1.CRYPTO,
}
_CURRENCY_TYPE_FROM_ADAPTER = {
    NautilusCurrencyTypeV1.FIAT: CurrencyType.FIAT,
    NautilusCurrencyTypeV1.STABLECOIN: CurrencyType.STABLECOIN,
    NautilusCurrencyTypeV1.CRYPTO: CurrencyType.CRYPTO,
}


def _rebuild_adapter(value: object, expected: type[AdapterT]) -> AdapterT:
    if not isinstance(value, expected):
        raise ValueError(f"adapter value must be {expected.__name__}")
    supplied = getattr(value, "__dict__", None)
    if not isinstance(supplied, dict):
        raise ValueError("adapter value has no complete model fields")
    unexpected = set(supplied).difference(expected.model_fields)
    if unexpected:
        raise ValueError("adapter value contains an unsupported Nautilus concept")
    extra = getattr(value, "__pydantic_extra__", None)
    if extra:
        raise ValueError("adapter value contains an unsupported Nautilus concept")
    raw = {name: getattr(value, name) for name in expected.model_fields}
    return expected.model_validate(raw)


def _fresh_currency(value: object) -> Currency:
    if not isinstance(value, Currency) or not DEFAULT_CURRENCY_REGISTRY.is_registered(value):
        raise ValueError("currency must be a registered canonical identity")
    return DEFAULT_CURRENCY_REGISTRY.resolve(value.code)


def _fresh_quantity(value: object) -> OrderQuantity:
    if not isinstance(value, OrderQuantity):
        raise ValueError("quantity must be an OrderQuantity")
    return OrderQuantity(value.value, value.precision)


def _fresh_price(value: object) -> Price:
    if not isinstance(value, Price):
        raise ValueError("price must be a Price")
    return Price(value.amount, _fresh_currency(value.currency))


def _fresh_money(value: object) -> Money:
    if not isinstance(value, Money):
        raise ValueError("money must be Money")
    return Money(value.amount, _fresh_currency(value.currency))


def _fresh_instrument_id(value: object) -> InstrumentId:
    if not isinstance(value, InstrumentId):
        raise ValueError("instrument must be an InstrumentId")
    return InstrumentId(value.symbol, value.product_type, value.venue)


def _fresh_margin(value: object) -> MarginRequirements | None:
    if value is None:
        return None
    if not isinstance(value, MarginRequirements):
        raise ValueError("margin must be MarginRequirements or None")
    return MarginRequirements(value.initial_margin_rate, value.maintenance_margin_rate)


def _fresh_provenance(value: object) -> InstrumentProvenance:
    if not isinstance(value, InstrumentProvenance):
        raise ValueError("provenance must be InstrumentProvenance")
    return InstrumentProvenance(value.source_id, value.source_revision, value.observed_at)


def _fresh_definition(value: object) -> InstrumentDefinition:
    if not isinstance(value, InstrumentDefinition):
        raise ValueError("instrument_definition must be InstrumentDefinition")
    return InstrumentDefinition(
        instrument_id=_fresh_instrument_id(value.instrument_id),
        raw_symbol=value.raw_symbol,
        asset_class=value.asset_class,
        base_currency=None if value.base_currency is None else _fresh_currency(value.base_currency),
        quote_currency=_fresh_currency(value.quote_currency),
        settlement_currency=_fresh_currency(value.settlement_currency),
        tick_size=_fresh_price(value.tick_size),
        size_increment=_fresh_quantity(value.size_increment),
        minimum_quantity=_fresh_quantity(value.minimum_quantity),
        maximum_quantity=_fresh_quantity(value.maximum_quantity),
        minimum_notional=_fresh_money(value.minimum_notional),
        maximum_notional=_fresh_money(value.maximum_notional),
        multiplier=value.multiplier,
        margin=_fresh_margin(value.margin),
        session_calendar=value.session_calendar,
        provenance=_fresh_provenance(value.provenance),
    )


def _fresh_intent(value: object) -> OrderIntent:
    if not isinstance(value, OrderIntent):
        raise ValueError("value must be OrderIntent")
    return OrderIntent(
        intent_id=value.intent_id,
        risk_decision_id=value.risk_decision_id,
        client_order_id=value.client_order_id,
        venue_order_id=value.venue_order_id,
        strategy_id=value.strategy_id,
        trader_id=value.trader_id,
        account_id=value.account_id,
        execution_client_id=value.execution_client_id,
        order_list_id=value.order_list_id,
        instrument=_fresh_instrument_id(value.instrument),
        side=value.side,
        order_type=value.order_type,
        time_in_force=value.time_in_force,
        quantity=_fresh_quantity(value.quantity),
        limit_price=None if value.limit_price is None else _fresh_price(value.limit_price),
        trigger_price=None if value.trigger_price is None else _fresh_price(value.trigger_price),
        trailing_offset=None
        if value.trailing_offset is None
        else _fresh_price(value.trailing_offset),
        gtd_expiry=value.gtd_expiry,
        post_only=value.post_only,
        reduce_only=value.reduce_only,
        requested_at=value.requested_at,
        schema_version=value.schema_version,
    )


def _fresh_event(value: object) -> OrderEvent:
    if not isinstance(value, OrderEvent):
        raise ValueError("value must be OrderEvent")
    return OrderEvent(
        event_id=value.event_id,
        order_id=value.order_id,
        sequence=value.sequence,
        target_status=value.target_status,
        occurred_at=value.occurred_at,
        reason=value.reason,
        cancel_resolution=value.cancel_resolution,
        schema_version=value.schema_version,
        event_fingerprint=value.event_fingerprint,
    )


def _fresh_fill(value: object) -> FillEvent:
    if not isinstance(value, FillEvent):
        raise ValueError("value must be FillEvent")
    return FillEvent(
        execution_id=value.execution_id,
        order_id=value.order_id,
        report_sequence=value.report_sequence,
        venue_trade_id=value.venue_trade_id,
        instrument_definition=_fresh_definition(value.instrument_definition),
        side=value.side,
        liquidity_side=value.liquidity_side,
        status=value.status,
        quantity=_fresh_quantity(value.quantity),
        cumulative_fill_quantity=_fresh_quantity(value.cumulative_fill_quantity),
        leaves_quantity=_fresh_quantity(value.leaves_quantity),
        order_quantity=_fresh_quantity(value.order_quantity),
        last_fill_price=_fresh_price(value.last_fill_price),
        average_fill_price=_fresh_price(value.average_fill_price),
        commission=_fresh_money(value.commission),
        reconciliation_source=value.reconciliation_source,
        duplicate_of_execution_id=value.duplicate_of_execution_id,
        correction_of_execution_id=value.correction_of_execution_id,
        bust_of_execution_id=value.bust_of_execution_id,
        filled_at=value.filled_at,
        schema_version=value.schema_version,
    )


def _adapter_currency(value: Currency) -> NautilusCurrencyV1:
    canonical = _fresh_currency(value)
    return NautilusCurrencyV1(
        code=canonical.code,
        currency_type=_CURRENCY_TYPE_TO_ADAPTER[canonical.currency_type],
        precision=canonical.precision,
        registry_version=canonical.registry_version,
    )


def _canonical_currency(value: object) -> Currency:
    adapter = _rebuild_adapter(value, NautilusCurrencyV1)
    currency = DEFAULT_CURRENCY_REGISTRY.resolve(adapter.code)
    if (
        adapter.currency_type is not _CURRENCY_TYPE_TO_ADAPTER[currency.currency_type]
        or adapter.precision != currency.precision
        or adapter.registry_version != currency.registry_version
    ):
        raise ValueError("adapter currency does not match the canonical registry")
    return currency


def _adapter_quantity(value: OrderQuantity) -> NautilusQuantityV1:
    canonical = _fresh_quantity(value)
    return NautilusQuantityV1(value=canonical.value, precision=canonical.precision)


def _canonical_quantity(value: object) -> OrderQuantity:
    adapter = _rebuild_adapter(value, NautilusQuantityV1)
    return OrderQuantity(adapter.value, adapter.precision)


def _adapter_price(value: Price) -> NautilusPriceV1:
    canonical = _fresh_price(value)
    return NautilusPriceV1(amount=canonical.amount, currency=_adapter_currency(canonical.currency))


def _canonical_price(value: object) -> Price:
    adapter = _rebuild_adapter(value, NautilusPriceV1)
    return Price(adapter.amount, _canonical_currency(adapter.currency))


def _adapter_money(value: Money) -> NautilusMoneyV1:
    canonical = _fresh_money(value)
    return NautilusMoneyV1(amount=canonical.amount, currency=_adapter_currency(canonical.currency))


def _canonical_money(value: object) -> Money:
    adapter = _rebuild_adapter(value, NautilusMoneyV1)
    return Money(adapter.amount, _canonical_currency(adapter.currency))


def _adapter_instrument_id(value: InstrumentId) -> NautilusInstrumentIdV1:
    canonical = _fresh_instrument_id(value)
    return NautilusInstrumentIdV1(
        symbol=canonical.symbol,
        product_type=_PRODUCT_TO_ADAPTER[canonical.product_type],
        venue=canonical.venue,
    )


def _canonical_instrument_id(value: object) -> InstrumentId:
    adapter = _rebuild_adapter(value, NautilusInstrumentIdV1)
    return InstrumentId(adapter.symbol, _PRODUCT_FROM_ADAPTER[adapter.product_type], adapter.venue)


def _adapter_definition(value: InstrumentDefinition) -> NautilusInstrumentDefinitionV1:
    canonical = _fresh_definition(value)
    margin = canonical.margin
    return NautilusInstrumentDefinitionV1(
        instrument_id=_adapter_instrument_id(canonical.instrument_id),
        raw_symbol=canonical.raw_symbol,
        asset_class=_ASSET_TO_ADAPTER[canonical.asset_class],
        base_currency=None
        if canonical.base_currency is None
        else _adapter_currency(canonical.base_currency),
        quote_currency=_adapter_currency(canonical.quote_currency),
        settlement_currency=_adapter_currency(canonical.settlement_currency),
        tick_size=_adapter_price(canonical.tick_size),
        size_increment=_adapter_quantity(canonical.size_increment),
        minimum_quantity=_adapter_quantity(canonical.minimum_quantity),
        maximum_quantity=_adapter_quantity(canonical.maximum_quantity),
        minimum_notional=_adapter_money(canonical.minimum_notional),
        maximum_notional=_adapter_money(canonical.maximum_notional),
        multiplier=canonical.multiplier,
        margin=None
        if margin is None
        else NautilusMarginRequirementsV1(
            initial_margin_rate=margin.initial_margin_rate,
            maintenance_margin_rate=margin.maintenance_margin_rate,
        ),
        session_calendar=canonical.session_calendar,
        provenance=NautilusInstrumentProvenanceV1(
            source_id=canonical.provenance.source_id,
            source_revision=canonical.provenance.source_revision,
            observed_at=canonical.provenance.observed_at,
        ),
    )


def _canonical_definition(value: object) -> InstrumentDefinition:
    adapter = _rebuild_adapter(value, NautilusInstrumentDefinitionV1)
    margin = adapter.margin
    provenance = _rebuild_adapter(adapter.provenance, NautilusInstrumentProvenanceV1)
    return InstrumentDefinition(
        instrument_id=_canonical_instrument_id(adapter.instrument_id),
        raw_symbol=adapter.raw_symbol,
        asset_class=_ASSET_FROM_ADAPTER[adapter.asset_class],
        base_currency=None
        if adapter.base_currency is None
        else _canonical_currency(adapter.base_currency),
        quote_currency=_canonical_currency(adapter.quote_currency),
        settlement_currency=_canonical_currency(adapter.settlement_currency),
        tick_size=_canonical_price(adapter.tick_size),
        size_increment=_canonical_quantity(adapter.size_increment),
        minimum_quantity=_canonical_quantity(adapter.minimum_quantity),
        maximum_quantity=_canonical_quantity(adapter.maximum_quantity),
        minimum_notional=_canonical_money(adapter.minimum_notional),
        maximum_notional=_canonical_money(adapter.maximum_notional),
        multiplier=adapter.multiplier,
        margin=None
        if margin is None
        else MarginRequirements(
            _rebuild_adapter(margin, NautilusMarginRequirementsV1).initial_margin_rate,
            _rebuild_adapter(margin, NautilusMarginRequirementsV1).maintenance_margin_rate,
        ),
        session_calendar=adapter.session_calendar,
        provenance=InstrumentProvenance(
            provenance.source_id, provenance.source_revision, provenance.observed_at
        ),
    )


def canonical_to_nautilus_order_intent(value: OrderIntent) -> NautilusOrderIntentV1:
    """Map one freshly revalidated canonical instruction into adapter DTO v1."""

    try:
        canonical = _fresh_intent(value)
        return NautilusOrderIntentV1(
            adapter_version=_ADAPTER_VERSION,
            schema_version=_SCHEMA_VERSION,
            intent_id=canonical.intent_id,
            risk_decision_id=canonical.risk_decision_id,
            client_order_id=canonical.client_order_id,
            venue_order_id=canonical.venue_order_id,
            strategy_id=canonical.strategy_id,
            trader_id=canonical.trader_id,
            account_id=canonical.account_id,
            execution_client_id=canonical.execution_client_id,
            order_list_id=canonical.order_list_id,
            instrument=_adapter_instrument_id(canonical.instrument),
            side=_ORDER_SIDE_TO_ADAPTER[canonical.side],
            order_type=_ORDER_TYPE_TO_ADAPTER[canonical.order_type],
            time_in_force=_TIF_TO_ADAPTER[canonical.time_in_force],
            quantity=_adapter_quantity(canonical.quantity),
            limit_price=None
            if canonical.limit_price is None
            else _adapter_price(canonical.limit_price),
            trigger_price=None
            if canonical.trigger_price is None
            else _adapter_price(canonical.trigger_price),
            trailing_offset=None
            if canonical.trailing_offset is None
            else _adapter_price(canonical.trailing_offset),
            gtd_expiry=canonical.gtd_expiry,
            post_only=canonical.post_only,
            reduce_only=canonical.reduce_only,
            requested_at=canonical.requested_at,
        )
    except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise NautilusMappingError("invalid canonical order intent") from exc


def nautilus_to_canonical_order_intent(value: NautilusOrderIntentV1) -> OrderIntent:
    """Map one complete adapter DTO v1 into a fresh canonical instruction."""

    try:
        adapter = _rebuild_adapter(value, NautilusOrderIntentV1)
        return OrderIntent(
            intent_id=adapter.intent_id,
            risk_decision_id=adapter.risk_decision_id,
            client_order_id=adapter.client_order_id,
            venue_order_id=adapter.venue_order_id,
            strategy_id=adapter.strategy_id,
            trader_id=adapter.trader_id,
            account_id=adapter.account_id,
            execution_client_id=adapter.execution_client_id,
            order_list_id=adapter.order_list_id,
            instrument=_canonical_instrument_id(adapter.instrument),
            side=_ORDER_SIDE_FROM_ADAPTER[adapter.side],
            order_type=_ORDER_TYPE_FROM_ADAPTER[adapter.order_type],
            time_in_force=_TIF_FROM_ADAPTER[adapter.time_in_force],
            quantity=_canonical_quantity(adapter.quantity),
            limit_price=None
            if adapter.limit_price is None
            else _canonical_price(adapter.limit_price),
            trigger_price=None
            if adapter.trigger_price is None
            else _canonical_price(adapter.trigger_price),
            trailing_offset=None
            if adapter.trailing_offset is None
            else _canonical_price(adapter.trailing_offset),
            gtd_expiry=adapter.gtd_expiry,
            post_only=adapter.post_only,
            reduce_only=adapter.reduce_only,
            requested_at=adapter.requested_at,
            schema_version=adapter.schema_version,
        )
    except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise NautilusMappingError("invalid Nautilus order intent adapter") from exc


def canonical_to_nautilus_order_event(value: OrderEvent) -> NautilusOrderEventV1:
    """Map one freshly revalidated lifecycle observation into adapter DTO v1."""

    try:
        canonical = _fresh_event(value)
        return NautilusOrderEventV1(
            adapter_version=_ADAPTER_VERSION,
            schema_version=_SCHEMA_VERSION,
            event_id=canonical.event_id,
            order_id=canonical.order_id,
            sequence=canonical.sequence,
            target_status=_ORDER_STATUS_TO_ADAPTER[canonical.target_status],
            occurred_at=canonical.occurred_at,
            reason=canonical.reason,
            cancel_resolution=None
            if canonical.cancel_resolution is None
            else _CANCEL_TO_ADAPTER[canonical.cancel_resolution],
            event_fingerprint=canonical.event_fingerprint,
        )
    except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise NautilusMappingError("invalid canonical order event") from exc


def nautilus_to_canonical_order_event(value: NautilusOrderEventV1) -> OrderEvent:
    """Map one complete adapter DTO v1 into a fresh lifecycle observation."""

    try:
        adapter = _rebuild_adapter(value, NautilusOrderEventV1)
        return OrderEvent(
            event_id=adapter.event_id,
            order_id=adapter.order_id,
            sequence=adapter.sequence,
            target_status=_ORDER_STATUS_FROM_ADAPTER[adapter.target_status],
            occurred_at=adapter.occurred_at,
            reason=adapter.reason,
            cancel_resolution=None
            if adapter.cancel_resolution is None
            else _CANCEL_FROM_ADAPTER[adapter.cancel_resolution],
            schema_version=adapter.schema_version,
            event_fingerprint=adapter.event_fingerprint,
        )
    except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise NautilusMappingError("invalid Nautilus order event adapter") from exc


def canonical_to_nautilus_fill_event(value: FillEvent) -> NautilusFillEventV1:
    """Map one freshly revalidated v2 execution report into adapter DTO v1."""

    try:
        canonical = _fresh_fill(value)
        return NautilusFillEventV1(
            adapter_version=_ADAPTER_VERSION,
            schema_version=_SCHEMA_VERSION,
            execution_id=canonical.execution_id,
            order_id=canonical.order_id,
            report_sequence=canonical.report_sequence,
            venue_trade_id=canonical.venue_trade_id,
            instrument_definition=_adapter_definition(canonical.instrument_definition),
            side=_ORDER_SIDE_TO_ADAPTER[canonical.side],
            liquidity_side=_LIQUIDITY_TO_ADAPTER[canonical.liquidity_side],
            status=_FILL_STATUS_TO_ADAPTER[canonical.status],
            quantity=_adapter_quantity(canonical.quantity),
            cumulative_fill_quantity=_adapter_quantity(canonical.cumulative_fill_quantity),
            leaves_quantity=_adapter_quantity(canonical.leaves_quantity),
            order_quantity=_adapter_quantity(canonical.order_quantity),
            last_fill_price=_adapter_price(canonical.last_fill_price),
            average_fill_price=_adapter_price(canonical.average_fill_price),
            commission=_adapter_money(canonical.commission),
            reconciliation_source=_RECONCILIATION_TO_ADAPTER[
                canonical.reconciliation_source
            ],
            duplicate_of_execution_id=canonical.duplicate_of_execution_id,
            correction_of_execution_id=canonical.correction_of_execution_id,
            bust_of_execution_id=canonical.bust_of_execution_id,
            filled_at=canonical.filled_at,
        )
    except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise NautilusMappingError("invalid canonical fill event") from exc


def nautilus_to_canonical_fill_event(value: NautilusFillEventV1) -> FillEvent:
    """Map one complete adapter DTO v1 into a fresh v2 execution report."""

    try:
        adapter = _rebuild_adapter(value, NautilusFillEventV1)
        return FillEvent(
            execution_id=adapter.execution_id,
            order_id=adapter.order_id,
            report_sequence=adapter.report_sequence,
            venue_trade_id=adapter.venue_trade_id,
            instrument_definition=_canonical_definition(adapter.instrument_definition),
            side=_ORDER_SIDE_FROM_ADAPTER[adapter.side],
            liquidity_side=_LIQUIDITY_FROM_ADAPTER[adapter.liquidity_side],
            status=_FILL_STATUS_FROM_ADAPTER[adapter.status],
            quantity=_canonical_quantity(adapter.quantity),
            cumulative_fill_quantity=_canonical_quantity(adapter.cumulative_fill_quantity),
            leaves_quantity=_canonical_quantity(adapter.leaves_quantity),
            order_quantity=_canonical_quantity(adapter.order_quantity),
            last_fill_price=_canonical_price(adapter.last_fill_price),
            average_fill_price=_canonical_price(adapter.average_fill_price),
            commission=_canonical_money(adapter.commission),
            reconciliation_source=_RECONCILIATION_FROM_ADAPTER[
                adapter.reconciliation_source
            ],
            duplicate_of_execution_id=adapter.duplicate_of_execution_id,
            correction_of_execution_id=adapter.correction_of_execution_id,
            bust_of_execution_id=adapter.bust_of_execution_id,
            filled_at=adapter.filled_at,
            schema_version=adapter.schema_version,
        )
    except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
        raise NautilusMappingError("invalid Nautilus fill event adapter") from exc
