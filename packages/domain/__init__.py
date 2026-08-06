"""Public immutable D0.1 primitives and D0.2 canonical contracts."""

from __future__ import annotations

from .clock import FixedUtcClock, SystemUtcClock, require_utc
from .instruments import InstrumentConstraints, InstrumentId, ProductType
from .market_data import (
    MarketCandle,
    MarketContinuity,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    normalize_market_symbol,
)
from .events import EventEnvelope, validate_event_batch
from .orders import FillEvent, OrderEvent, OrderIntent, OrderSide, OrderStatus, OrderType, TimeInForce
from .portfolio import PortfolioSnapshot, PositionSnapshot, TargetPortfolio, TargetPosition
from .primitives import (
    CANONICAL_DECIMAL_POLICY_VERSION,
    DEFAULT_CURRENCY_REGISTRY,
    Currency,
    CurrencyConversion,
    CurrencyRegistry,
    CurrencyType,
    FiniteDecimal,
    Money,
    OrderQuantity,
    Price,
    Quantity,
    convert_money_exact,
    decimal_to_scaled_integer,
)
from .risk import RiskDecision, RiskOutcome, RiskReasonCode, RiskStateSnapshot
from .signals import (
    EvidenceLocator,
    EvidenceLocatorKind,
    EvidenceReference,
    EvidenceSource,
    ResearchPacket,
    SignalDirection,
    SignalProposal,
)

__all__ = [
    "CANONICAL_DECIMAL_POLICY_VERSION",
    "Currency",
    "CurrencyConversion",
    "CurrencyRegistry",
    "CurrencyType",
    "DEFAULT_CURRENCY_REGISTRY",
    "EventEnvelope",
    "EvidenceLocator",
    "EvidenceLocatorKind",
    "EvidenceReference",
    "EvidenceSource",
    "FillEvent",
    "FiniteDecimal",
    "FixedUtcClock",
    "InstrumentConstraints",
    "InstrumentId",
    "Money",
    "MarketCandle",
    "MarketContinuity",
    "MarketDataProvenance",
    "MarketSnapshot",
    "MarketTimeframe",
    "OrderEvent",
    "OrderIntent",
    "OrderQuantity",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PortfolioSnapshot",
    "PositionSnapshot",
    "Price",
    "ProductType",
    "Quantity",
    "ResearchPacket",
    "RiskDecision",
    "RiskOutcome",
    "RiskReasonCode",
    "RiskStateSnapshot",
    "SignalDirection",
    "SignalProposal",
    "SystemUtcClock",
    "TargetPortfolio",
    "TargetPosition",
    "TimeInForce",
    "convert_money_exact",
    "decimal_to_scaled_integer",
    "require_utc",
    "validate_event_batch",
    "normalize_market_symbol",
]
