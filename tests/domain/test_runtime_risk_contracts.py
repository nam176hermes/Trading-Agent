from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    Currency,
    ExposureSnapshot,
    InstrumentId,
    InstrumentExposureSnapshot,
    Money,
    OrderQuantity,
    PositionMark,
    Price,
    ProductType,
    Quantity,
    RuntimeRiskPolicy as PublicRuntimeRiskPolicy,
    StrategyExposureSnapshot,
    VenueExposureSnapshot,
)
from packages.domain.runtime_risk import (
    DurableOrderApprovalRef,
    PriorRuntimeCommandIdentity,
    RuntimeInstrumentRiskSpec,
    RuntimeOrderRiskDecision,
    RuntimeRiskConversionRate,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskObservation,
    RuntimeRiskOutcome,
    RuntimeRiskPolicy,
    RuntimeRiskReasonCode,
    RuntimeVenueHealth,
    RuntimeVenueHealthRecord,
)
from packages.runtime_risk import canonical_model_digest, canonical_model_json


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "SIM")
DIGEST = "a" * 64

EXPECTED_REASON_ORDER = (
    "POLICY_RISK_NOT_APPROVED",
    "ENGINE_NOT_READY",
    "INSTRUMENT_UNKNOWN",
    "MARKET_DATA_STALE",
    "VALUATION_AUTHORITY_MISSING",
    "PORTFOLIO_STATE_INVALID",
    "PRICE_PRECISION_INVALID",
    "QUANTITY_PRECISION_INVALID",
    "QUANTITY_OUT_OF_BOUNDS",
    "ORDER_NOTIONAL_LIMIT",
    "BALANCE_MARGIN_LIMIT",
    "PENDING_EXPOSURE_LIMIT",
    "GROSS_EXPOSURE_LIMIT",
    "NET_EXPOSURE_LIMIT",
    "STRATEGY_EXPOSURE_LIMIT",
    "VENUE_EXPOSURE_LIMIT",
    "DAILY_LOSS_LIMIT",
    "DRAWDOWN_LIMIT",
    "REDUCE_ONLY_VIOLATION",
    "COMMAND_RATE_LIMIT",
    "VENUE_UNHEALTHY",
    "DUPLICATE_COMMAND",
    "WITHIN_LIMITS",
)


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(value: str) -> Money:
    return Money(Decimal(value), Currency.USD)


def portfolio() -> AccountPortfolioSnapshot:
    balance = AccountBalanceSnapshot(
        account_id="account-1", currency=Currency.USD, cash=money("1000"),
        locked_funds=money("0"), margin_used=money("0"), realized_pnl=money("0"),
        unrealized_pnl=money("0"), fees=money("0"), funding=money("0"),
        observed_at=NOW, schema_version="account-balance-v1",
    )
    position = AccountPositionSnapshot(
        account_id="account-1", strategy_id="strategy-1", instrument=INSTRUMENT,
        settlement_currency=Currency.USD, quantity=Quantity(Decimal("1"), 0),
        mark=PositionMark(price=Price(Decimal("100"), Currency.USD), marked_at=NOW, provenance_id="sim-mark"),
        average_entry_price=Price(Decimal("100"), Currency.USD), realized_pnl=money("0"),
        unrealized_pnl=money("0"), fees=money("0"), funding=money("0"),
        observed_at=NOW, schema_version="account-position-v1",
    )
    exposure = ExposureSnapshot(currency=Currency.USD, gross=money("100"), net=money("100"), pending=money("0"))
    return AccountPortfolioSnapshot(
        snapshot_id=uid(1), account_id="account-1", reporting_currency=Currency.USD,
        balances=(balance,), positions=(position,), total_exposure=exposure,
        instrument_exposures=(InstrumentExposureSnapshot(instrument=INSTRUMENT, exposure=exposure),),
        strategy_exposures=(StrategyExposureSnapshot(strategy_id="strategy-1", exposure=exposure),),
        venue_exposures=(VenueExposureSnapshot(venue_id="SIM", exposure=exposure),),
        observed_at=NOW, schema_version="account-portfolio-v1",
    )


def instrument_spec(**changes: object) -> RuntimeInstrumentRiskSpec:
    values: dict[str, object] = {
        "instrument": INSTRUMENT, "venue_id": "SIM", "settlement_currency": Currency.USD,
        "price_increment": Price(Decimal("0.01"), Currency.USD),
        "quantity_increment": OrderQuantity(Decimal("0.001"), 3),
        "min_quantity": OrderQuantity(Decimal("0.001"), 3),
        "max_quantity": OrderQuantity(Decimal("10"), 3),
        "min_order_notional": money("1"), "max_order_notional": money("100000"),
        "initial_margin_rate": Decimal("0.1"),
    }
    values.update(changes)
    return RuntimeInstrumentRiskSpec(**values)


def policy(**changes: object) -> RuntimeRiskPolicy:
    values: dict[str, object] = {
        "policy_id": uid(2), "policy_version": "policy-v1", "account_id": "account-1",
        "market_data_max_age_seconds": 10, "portfolio_max_age_seconds": 10,
        "max_pending_exposure": money("100"), "max_gross_exposure": money("1000"),
        "max_abs_net_exposure": money("1000"), "max_strategy_exposure": money("500"),
        "max_venue_exposure": money("1000"), "min_available_funds": money("0"),
        "max_daily_loss": money("100"), "max_drawdown": money("200"),
        "command_window_seconds": 60, "max_commands_per_window": 5,
        "schema_version": "runtime-risk-policy-v1",
    }
    values.update(changes)
    return RuntimeRiskPolicy(**values)


def observation(**changes: object) -> RuntimeRiskObservation:
    values: dict[str, object] = {
        "observation_id": uid(3), "state_version": 1, "portfolio": portfolio(),
        "instrument_specs": (instrument_spec(),),
        "market_snapshots": (RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("99"), Currency.USD), ask=Price(Decimal("101"), Currency.USD), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-book"),),
        "conversion_rates": (RuntimeRiskConversionRate(source_currency=Currency.BTC, target_currency=Currency.USD, rate=Decimal("100"), observed_at=NOW, provenance_id="sim-fx"),),
        "venue_health": (RuntimeVenueHealthRecord(venue_id="SIM", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW),),
        "engine_ready": True, "daily_pnl": money("0"), "current_equity": money("1000"),
        "peak_equity": money("1000"), "command_window_started_at": NOW,
        "commands_in_window": 0, "prior_commands": (), "observed_at": NOW,
        "schema_version": "runtime-risk-observation-v1",
    }
    values.update(changes)
    return RuntimeRiskObservation(**values)


def approved_decision(**changes: object) -> RuntimeOrderRiskDecision:
    values: dict[str, object] = {
        "decision_id": uid(4), "intent_id": uid(5), "risk_decision_id": uid(6),
        "intent_digest": DIGEST, "policy_risk_decision_digest": DIGEST,
        "portfolio_snapshot_id": uid(1), "portfolio_digest": DIGEST,
        "observation_id": uid(3), "observation_version": 1, "observation_digest": DIGEST,
        "policy_id": uid(2), "policy_version": "policy-v1", "policy_digest": DIGEST,
        "risk_price": Price(Decimal("100"), Currency.USD), "order_notional": money("100"),
        "projected_position_quantity": Quantity(Decimal("2"), 0), "projected_pending": money("0"),
        "projected_gross": money("200"), "projected_net": money("200"),
        "projected_strategy_gross": money("200"), "projected_venue_gross": money("200"),
        "projected_instrument_gross": money("200"), "projected_margin_used": money("20"),
        "projected_available_funds": money("980"), "outcome": RuntimeRiskOutcome.APPROVED,
        "reason_codes": (RuntimeRiskReasonCode.WITHIN_LIMITS,), "decided_at": NOW,
        "schema_version": "runtime-order-risk-decision-v1",
    }
    values.update(changes)
    return RuntimeOrderRiskDecision(**values)


def approval_ref(**changes: object) -> DurableOrderApprovalRef:
    values: dict[str, object] = {
        "decision_outcome": RuntimeRiskOutcome.APPROVED, "event_id": uid(7), "stream_id": uid(8),
        "sequence": 1, "event_digest": DIGEST, "decision_id": uid(4), "decision_digest": DIGEST,
        "intent_id": uid(5), "intent_digest": DIGEST, "risk_decision_id": uid(6),
        "policy_risk_decision_digest": DIGEST, "portfolio_snapshot_id": uid(1),
        "portfolio_digest": DIGEST, "observation_id": uid(3), "observation_version": 1,
        "observation_digest": DIGEST, "policy_id": uid(2), "policy_version": "policy-v1",
        "policy_digest": DIGEST, "schema_version": "durable-order-approval-v1",
    }
    values.update(changes)
    return DurableOrderApprovalRef(**values)


def test_runtime_risk_reason_codes_are_the_complete_stable_contract() -> None:
    assert tuple(code.value for code in RuntimeRiskReasonCode) == EXPECTED_REASON_ORDER


def test_runtime_contracts_are_strict_frozen_and_export_existing_domain_types() -> None:
    spec = instrument_spec()
    assert spec.instrument == INSTRUMENT
    assert PublicRuntimeRiskPolicy is RuntimeRiskPolicy
    with pytest.raises(ValidationError):
        instrument_spec(venue_id=1)
    with pytest.raises(ValidationError):
        spec.venue_id = "OTHER"  # type: ignore[misc]


def test_instrument_spec_requires_positive_increments_ordered_bounds_and_settlement_currency() -> None:
    with pytest.raises(ValidationError, match="price_increment"):
        instrument_spec(price_increment={"amount": Decimal("0"), "currency": Currency.USD})
    with pytest.raises(ValidationError, match="quantity_increment"):
        instrument_spec(quantity_increment=OrderQuantity(Decimal("0"), 3))
    with pytest.raises(ValidationError, match="max_quantity"):
        instrument_spec(max_quantity=OrderQuantity(Decimal("0.0001"), 4))
    with pytest.raises(ValidationError, match="notional"):
        instrument_spec(min_order_notional=money("100001"))
    with pytest.raises(ValidationError, match="settlement"):
        instrument_spec(max_order_notional=Money(Decimal("100000"), Currency.USDT))
    with pytest.raises(ValidationError, match="initial_margin_rate"):
        instrument_spec(initial_margin_rate=Decimal("1.01"))


def test_market_and_conversion_contracts_require_ordered_currency_aligned_positive_quotes() -> None:
    with pytest.raises(ValidationError, match="bid"):
        RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("102"), Currency.USD), ask=Price(Decimal("101"), Currency.USD), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-book")
    with pytest.raises(ValidationError, match="currency"):
        RuntimeRiskMarketSnapshot(instrument=INSTRUMENT, bid=Price(Decimal("99"), Currency.USD), ask=Price(Decimal("101"), Currency.USDT), last=Price(Decimal("100"), Currency.USD), observed_at=NOW, provenance_id="sim-book")
    with pytest.raises(ValidationError, match="source_currency"):
        RuntimeRiskConversionRate(source_currency=Currency.USD, target_currency=Currency.USD, rate=Decimal("1"), observed_at=NOW, provenance_id="sim-fx")
    with pytest.raises(ValidationError, match="rate"):
        RuntimeRiskConversionRate(source_currency=Currency.BTC, target_currency=Currency.USD, rate=Decimal("0"), observed_at=NOW, provenance_id="sim-fx")


def test_policy_and_observation_require_nonnegative_counts_matching_currencies_and_unique_prior_commands() -> None:
    with pytest.raises(ValidationError, match="max_commands_per_window"):
        policy(max_commands_per_window=-1)
    with pytest.raises(ValidationError, match="market_data_max_age_seconds"):
        policy(market_data_max_age_seconds=0)
    with pytest.raises(ValidationError, match="policy money"):
        policy(max_daily_loss=Money(Decimal("100"), Currency.USDT))
    prior = PriorRuntimeCommandIdentity(intent_id=uid(9), client_order_id="client-1")
    with pytest.raises(ValidationError, match="prior_commands"):
        observation(prior_commands=(prior, prior))
    with pytest.raises(ValidationError, match="commands_in_window"):
        observation(commands_in_window=-1)
    with pytest.raises(ValidationError, match="reporting currency"):
        observation(daily_pnl=Money(Decimal("0"), Currency.USDT))


def test_observation_requires_canonical_order_for_each_identity_collection() -> None:
    second_spec = instrument_spec(venue_id="SIM-2")
    with pytest.raises(ValidationError, match="instrument_specs must be canonically ordered"):
        observation(instrument_specs=(second_spec, instrument_spec()))
    second_venue = RuntimeVenueHealthRecord(venue_id="SIM-2", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW)
    with pytest.raises(ValidationError, match="venue_health must be canonically ordered"):
        observation(venue_health=(second_venue, RuntimeVenueHealthRecord(venue_id="SIM", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW)))


def test_runtime_policy_limits_and_margin_rate_include_their_boundaries() -> None:
    assert instrument_spec(initial_margin_rate=Decimal("0")).initial_margin_rate == Decimal("0")
    assert instrument_spec(initial_margin_rate=Decimal("1")).initial_margin_rate == Decimal("1")
    assert policy(min_available_funds=money("0")).min_available_funds.amount == Decimal("0")


def test_runtime_order_decision_enforces_outcome_projection_and_reason_semantics() -> None:
    approved = approved_decision()
    rejected = approved_decision(
        outcome=RuntimeRiskOutcome.REJECTED,
        reason_codes=(RuntimeRiskReasonCode.ENGINE_NOT_READY,),
        risk_price=None, order_notional=None, projected_position_quantity=None,
        projected_pending=None, projected_gross=None, projected_net=None,
        projected_strategy_gross=None, projected_venue_gross=None,
        projected_instrument_gross=None, projected_margin_used=None,
        projected_available_funds=None,
    )
    assert rejected.outcome is RuntimeRiskOutcome.REJECTED
    with pytest.raises(ValueError):
        approved.model_copy(update={"reason_codes": (RuntimeRiskReasonCode.ENGINE_NOT_READY,)})
    with pytest.raises(ValueError):
        rejected.model_copy(update={"reason_codes": (RuntimeRiskReasonCode.WITHIN_LIMITS,)})
    with pytest.raises(ValueError):
        approved.model_copy(update={"risk_price": None})
    with pytest.raises(ValidationError, match="canonical risk-check order"):
        approved_decision(outcome=RuntimeRiskOutcome.REJECTED, reason_codes=(RuntimeRiskReasonCode.VENUE_UNHEALTHY, RuntimeRiskReasonCode.ENGINE_NOT_READY))


def test_approval_reference_is_approval_only_and_model_copy_revalidates() -> None:
    approved_ref = approval_ref()
    with pytest.raises(ValueError):
        approved_ref.model_copy(update={"decision_outcome": RuntimeRiskOutcome.REJECTED})


def test_canonical_identity_is_sorted_deterministic_and_rejects_forged_models() -> None:
    value = approval_ref()
    canonical = canonical_model_json(value)
    assert canonical == canonical_model_json(value)
    assert canonical.index('"decision_id"') < canonical.index('"event_digest"')
    assert canonical_model_digest(value) == canonical_model_digest(value)
    with pytest.raises(ValueError, match="Pydantic model"):
        canonical_model_json("not-a-model")  # type: ignore[arg-type]
    incomplete = DurableOrderApprovalRef.model_construct(decision_outcome=RuntimeRiskOutcome.APPROVED)
    with pytest.raises(ValueError, match="canonically represented"):
        canonical_model_digest(incomplete)
