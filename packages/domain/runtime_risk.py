"""Strict immutable contracts for deterministic runtime order-risk decisions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt

from .clock import require_utc
from .instruments import InstrumentId
from .orders import DomainModel
from .portfolio import AccountPortfolioSnapshot
from .primitives import (
    DEFAULT_CURRENCY_REGISTRY,
    Currency,
    FiniteDecimal,
    Money,
    OrderQuantity,
    Price,
    Quantity,
)


CanonicalRiskIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_MAX_RUNTIME_RISK_DURATION_SECONDS = 86_399_999_999_999
_RuntimeRiskDurationSeconds = Annotated[
    StrictInt,
    Field(gt=0, le=_MAX_RUNTIME_RISK_DURATION_SECONDS),
]


class RuntimeRiskOutcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RuntimeVenueHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class RuntimeRiskReasonCode(str, Enum):
    POLICY_RISK_NOT_APPROVED = "POLICY_RISK_NOT_APPROVED"
    ENGINE_NOT_READY = "ENGINE_NOT_READY"
    INSTRUMENT_UNKNOWN = "INSTRUMENT_UNKNOWN"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    VALUATION_AUTHORITY_MISSING = "VALUATION_AUTHORITY_MISSING"
    PORTFOLIO_STATE_INVALID = "PORTFOLIO_STATE_INVALID"
    PRICE_PRECISION_INVALID = "PRICE_PRECISION_INVALID"
    QUANTITY_PRECISION_INVALID = "QUANTITY_PRECISION_INVALID"
    QUANTITY_OUT_OF_BOUNDS = "QUANTITY_OUT_OF_BOUNDS"
    ORDER_NOTIONAL_LIMIT = "ORDER_NOTIONAL_LIMIT"
    BALANCE_MARGIN_LIMIT = "BALANCE_MARGIN_LIMIT"
    PENDING_EXPOSURE_LIMIT = "PENDING_EXPOSURE_LIMIT"
    GROSS_EXPOSURE_LIMIT = "GROSS_EXPOSURE_LIMIT"
    NET_EXPOSURE_LIMIT = "NET_EXPOSURE_LIMIT"
    STRATEGY_EXPOSURE_LIMIT = "STRATEGY_EXPOSURE_LIMIT"
    VENUE_EXPOSURE_LIMIT = "VENUE_EXPOSURE_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    REDUCE_ONLY_VIOLATION = "REDUCE_ONLY_VIOLATION"
    COMMAND_RATE_LIMIT = "COMMAND_RATE_LIMIT"
    VENUE_UNHEALTHY = "VENUE_UNHEALTHY"
    DUPLICATE_COMMAND = "DUPLICATE_COMMAND"
    WITHIN_LIMITS = "WITHIN_LIMITS"


_REASON_ORDER = {reason: index for index, reason in enumerate(RuntimeRiskReasonCode)}
_PROJECTION_FIELDS = (
    "risk_price",
    "order_notional",
    "projected_position_quantity",
    "projected_pending",
    "projected_gross",
    "projected_net",
    "projected_strategy_gross",
    "projected_venue_gross",
    "projected_instrument_gross",
    "projected_margin_used",
    "projected_available_funds",
)


class RuntimeRiskModel(DomainModel):
    """Runtime contract base which cannot retain unvalidated copy updates."""

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> Self:
        values = {name: getattr(self, name) for name in type(self).model_fields}
        if update:
            values.update(update)
        if deep:
            currency_memo = {
                id(currency): currency
                for currency in DEFAULT_CURRENCY_REGISTRY.currencies
            }
            values = deepcopy(values, currency_memo)
        return type(self).model_validate(values)


def _require_positive(value: Decimal, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_non_negative(value: Decimal, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_money_currency(currency: Currency, values: tuple[Money, ...], label: str) -> None:
    if any(value.currency is not currency for value in values):
        raise ValueError(f"{label} money currency must match {label} currency")


def _is_complete(model: BaseModel) -> bool:
    return all(name in model.__dict__ for name in type(model).model_fields)


class RuntimeInstrumentRiskSpec(RuntimeRiskModel):
    instrument: InstrumentId
    venue_id: CanonicalRiskIdentifier
    settlement_currency: Currency
    price_increment: Price
    quantity_increment: OrderQuantity
    min_quantity: OrderQuantity
    max_quantity: OrderQuantity
    min_order_notional: Money
    max_order_notional: Money
    initial_margin_rate: FiniteDecimal

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        if self.price_increment.currency is not self.settlement_currency:
            raise ValueError("price_increment currency must match settlement currency")
        _require_positive(self.price_increment.amount, "price_increment")
        for name, quantity in (
            ("quantity_increment", self.quantity_increment),
            ("min_quantity", self.min_quantity),
            ("max_quantity", self.max_quantity),
        ):
            _require_positive(quantity.value, name)
        if self.min_quantity.precision != self.quantity_increment.precision:
            raise ValueError("min_quantity precision must match quantity_increment")
        if self.max_quantity.precision != self.quantity_increment.precision:
            raise ValueError("max_quantity precision must match quantity_increment")
        if self.max_quantity.value < self.min_quantity.value:
            raise ValueError("max_quantity must not be below min_quantity")
        if self.min_order_notional.currency is not self.max_order_notional.currency:
            raise ValueError("order notional bounds currency must match")
        _require_positive(self.min_order_notional.amount, "min_order_notional")
        _require_positive(self.max_order_notional.amount, "max_order_notional")
        if self.max_order_notional.amount < self.min_order_notional.amount:
            raise ValueError("max_order_notional must not be below min_order_notional")
        if not Decimal(0) <= self.initial_margin_rate <= Decimal(1):
            raise ValueError("initial_margin_rate must be between zero and one")


class RuntimeRiskMarketSnapshot(RuntimeRiskModel):
    instrument: InstrumentId
    bid: Price
    ask: Price
    last: Price
    observed_at: datetime
    provenance_id: CanonicalRiskIdentifier

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.observed_at)
        if self.bid.currency is not self.ask.currency or self.bid.currency is not self.last.currency:
            raise ValueError("bid, ask, and last currency must match")
        if self.bid.amount > self.ask.amount:
            raise ValueError("bid must not exceed ask")


class RuntimeRiskConversionRate(RuntimeRiskModel):
    source_currency: Currency
    target_currency: Currency
    rate: FiniteDecimal
    observed_at: datetime
    provenance_id: CanonicalRiskIdentifier

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.observed_at)
        if self.source_currency is self.target_currency:
            raise ValueError("source_currency and target_currency must differ")
        _require_positive(self.rate, "rate")


class RuntimeVenueHealthRecord(RuntimeRiskModel):
    venue_id: CanonicalRiskIdentifier
    health: RuntimeVenueHealth
    observed_at: datetime

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.observed_at)


class PriorRuntimeCommandIdentity(RuntimeRiskModel):
    intent_id: UUID
    client_order_id: CanonicalRiskIdentifier


class RuntimeRiskPolicy(RuntimeRiskModel):
    policy_id: UUID
    policy_version: CanonicalRiskIdentifier
    account_id: CanonicalRiskIdentifier
    market_data_max_age_seconds: _RuntimeRiskDurationSeconds
    portfolio_max_age_seconds: _RuntimeRiskDurationSeconds
    max_pending_exposure: Money
    max_gross_exposure: Money
    max_abs_net_exposure: Money
    max_strategy_exposure: Money
    max_venue_exposure: Money
    min_available_funds: Money
    max_daily_loss: Money
    max_drawdown: Money
    command_window_seconds: _RuntimeRiskDurationSeconds
    max_commands_per_window: StrictInt
    schema_version: Literal["runtime-risk-policy-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        if self.market_data_max_age_seconds <= 0:
            raise ValueError("market_data_max_age_seconds must be positive")
        if self.portfolio_max_age_seconds <= 0:
            raise ValueError("portfolio_max_age_seconds must be positive")
        if self.command_window_seconds <= 0:
            raise ValueError("command_window_seconds must be positive")
        if self.max_commands_per_window < 0:
            raise ValueError("max_commands_per_window must be non-negative")
        money_values = (
            self.max_pending_exposure,
            self.max_gross_exposure,
            self.max_abs_net_exposure,
            self.max_strategy_exposure,
            self.max_venue_exposure,
            self.min_available_funds,
            self.max_daily_loss,
            self.max_drawdown,
        )
        policy_currency = self.max_pending_exposure.currency
        _require_money_currency(policy_currency, money_values, "policy")
        for name, value in zip(
            (
                "max_pending_exposure", "max_gross_exposure", "max_abs_net_exposure",
                "max_strategy_exposure", "max_venue_exposure", "min_available_funds",
                "max_daily_loss", "max_drawdown",
            ),
            money_values,
            strict=True,
        ):
            _require_non_negative(value.amount, name)


class RuntimeRiskObservation(RuntimeRiskModel):
    observation_id: UUID
    state_version: StrictInt
    portfolio: AccountPortfolioSnapshot
    instrument_specs: tuple[RuntimeInstrumentRiskSpec, ...]
    market_snapshots: tuple[RuntimeRiskMarketSnapshot, ...]
    conversion_rates: tuple[RuntimeRiskConversionRate, ...]
    venue_health: tuple[RuntimeVenueHealthRecord, ...]
    engine_ready: bool
    daily_pnl: Money
    current_equity: Money
    peak_equity: Money
    command_window_started_at: datetime
    commands_in_window: StrictInt
    prior_commands: tuple[PriorRuntimeCommandIdentity, ...]
    observed_at: datetime
    schema_version: Literal["runtime-risk-observation-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.command_window_started_at)
        require_utc(self.observed_at)
        if self.command_window_started_at > self.observed_at:
            raise ValueError("command window must start no later than observation")
        if self.state_version < 0:
            raise ValueError("state_version must be non-negative")
        if self.commands_in_window < 0:
            raise ValueError("commands_in_window must be non-negative")
        _require_money_currency(
            self.portfolio.reporting_currency,
            (self.daily_pnl, self.current_equity, self.peak_equity),
            "reporting",
        )
        if any(
            spec.min_order_notional.currency is not self.portfolio.reporting_currency
            or spec.max_order_notional.currency is not self.portfolio.reporting_currency
            for spec in self.instrument_specs
        ):
            raise ValueError("instrument notional bounds must use reporting currency")
        if self.current_equity.amount < 0:
            raise ValueError("current_equity must be non-negative")
        if self.peak_equity.amount < 0:
            raise ValueError("peak_equity must be non-negative")
        observed_children = (
            ("portfolio", self.portfolio.observed_at),
            *(("market", item.observed_at) for item in self.market_snapshots),
            *(("conversion", item.observed_at) for item in self.conversion_rates),
            *(("venue", item.observed_at) for item in self.venue_health),
        )
        for name, child_observed_at in observed_children:
            if child_observed_at > self.observed_at:
                raise ValueError(f"{name} timestamp must not be after observation")
        intent_ids = tuple(item.intent_id for item in self.prior_commands)
        client_order_ids = tuple(item.client_order_id for item in self.prior_commands)
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("prior_commands contain duplicate intent_id")
        if len(client_order_ids) != len(set(client_order_ids)):
            raise ValueError("prior_commands contain duplicate client_order_id")
        ordered_collections = (
            ("instrument_specs", tuple((item.instrument.canonical, item.venue_id) for item in self.instrument_specs)),
            ("market_snapshots", tuple(item.instrument.canonical for item in self.market_snapshots)),
            ("conversion_rates", tuple((item.source_currency.code, item.target_currency.code) for item in self.conversion_rates)),
            ("venue_health", tuple(item.venue_id for item in self.venue_health)),
            ("prior_commands", tuple((str(item.intent_id), item.client_order_id) for item in self.prior_commands)),
        )
        for name, keys in ordered_collections:
            if len(keys) != len(set(keys)):
                raise ValueError(f"{name} contain duplicates")
            if keys != tuple(sorted(keys)):
                raise ValueError(f"{name} must be canonically ordered")


class RuntimeOrderRiskDecision(RuntimeRiskModel):
    decision_id: UUID
    intent_id: UUID
    risk_decision_id: UUID
    intent_digest: Sha256
    policy_risk_decision_digest: Sha256
    portfolio_snapshot_id: UUID
    portfolio_digest: Sha256
    observation_id: UUID
    observation_version: StrictInt
    observation_digest: Sha256
    policy_id: UUID
    policy_version: CanonicalRiskIdentifier
    policy_digest: Sha256
    risk_price: Price | None
    order_notional: Money | None
    projected_position_quantity: Quantity | None
    projected_pending: Money | None
    projected_gross: Money | None
    projected_net: Money | None
    projected_strategy_gross: Money | None
    projected_venue_gross: Money | None
    projected_instrument_gross: Money | None
    projected_margin_used: Money | None
    projected_available_funds: Money | None
    outcome: RuntimeRiskOutcome
    reason_codes: tuple[RuntimeRiskReasonCode, ...]
    decided_at: datetime
    schema_version: Literal["runtime-order-risk-decision-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        require_utc(self.decided_at)
        if self.observation_version < 0:
            raise ValueError("observation_version must be non-negative")
        if not self.reason_codes:
            raise ValueError("reason_codes must not be empty")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes contain duplicates")
        if tuple(sorted(self.reason_codes, key=_REASON_ORDER.__getitem__)) != self.reason_codes:
            raise ValueError("reason_codes must follow canonical risk-check order")
        has_within_limits = RuntimeRiskReasonCode.WITHIN_LIMITS in self.reason_codes
        projection_values = tuple(getattr(self, name) for name in _PROJECTION_FIELDS)
        if self.outcome is RuntimeRiskOutcome.APPROVED:
            if self.reason_codes != (RuntimeRiskReasonCode.WITHIN_LIMITS,):
                raise ValueError("approved outcome requires only WITHIN_LIMITS")
            if any(value is None for value in projection_values):
                raise ValueError("approved outcome requires every projection")
        else:
            if has_within_limits:
                raise ValueError("rejected outcome cannot use WITHIN_LIMITS")


class DurableOrderApprovalRef(RuntimeRiskModel):
    decision_outcome: Literal[RuntimeRiskOutcome.APPROVED]
    event_id: UUID
    stream_id: UUID
    sequence: StrictInt
    event_digest: Sha256
    decision_id: UUID
    decision_digest: Sha256
    intent_id: UUID
    intent_digest: Sha256
    risk_decision_id: UUID
    policy_risk_decision_digest: Sha256
    portfolio_snapshot_id: UUID
    portfolio_digest: Sha256
    observation_id: UUID
    observation_version: StrictInt
    observation_digest: Sha256
    policy_id: UUID
    policy_version: CanonicalRiskIdentifier
    policy_digest: Sha256
    schema_version: Literal["durable-order-approval-v1"]

    def model_post_init(self, __context: Any) -> None:
        if not _is_complete(self):
            return
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.observation_version < 0:
            raise ValueError("observation_version must be non-negative")
