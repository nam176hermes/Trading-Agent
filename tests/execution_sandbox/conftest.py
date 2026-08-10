from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    AssetClass,
    Currency,
    EventEnvelope,
    ExposureSnapshot,
    FillEvent,
    FillReportStatus,
    InstrumentDefinition,
    InstrumentExposureSnapshot,
    InstrumentId,
    InstrumentProvenance,
    LiquiditySide,
    Money,
    OrderEvent,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    PositionMark,
    Price,
    ProductType,
    Quantity,
    ReconciliationSource,
    RiskDecision,
    RiskOutcome,
    RiskReasonCode,
    RiskStateSnapshot,
    RuntimeInstrumentRiskSpec,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskObservation,
    RuntimeRiskPolicy,
    RuntimeVenueHealth,
    RuntimeVenueHealthRecord,
    StrategyExposureSnapshot,
    TargetPortfolio,
    TargetPosition,
    TimeInForce,
    VenueExposureSnapshot,
)
from packages.domain.runtime_halt import GlobalHaltStatus, GlobalSafetyObservation, PreparedSubmitPermit
from packages.event_ledger import InMemoryEventLedger
from packages.runtime_risk import (
    evaluate_runtime_order_risk,
    prepare_submit_permit,
    record_global_halt_observation,
    record_runtime_risk_decision,
)
from packages.safety_evidence import CanonicalKillSwitchState


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
HALT_STREAM_ID = UUID(int=900)


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(amount: str) -> Money:
    return Money(Decimal(amount), Currency.USD)


class ExactSafetyVerifier:
    def verify(self, *, observation: GlobalSafetyObservation) -> GlobalSafetyObservation:
        return observation


@dataclass(frozen=True)
class PreparedCase:
    intent: OrderIntent
    observation: RuntimeRiskObservation
    policy: RuntimeRiskPolicy
    safety: GlobalSafetyObservation
    permit: PreparedSubmitPermit


@pytest.fixture(name="now")
def fixture_now() -> datetime:
    return NOW


@pytest.fixture(name="fixture_uid")
def fixture_uid() -> type[uid]:
    return uid


@pytest.fixture(name="instrument")
def fixture_instrument() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id=InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "SIM"),
        raw_symbol="BTC-USD",
        asset_class=AssetClass.CRYPTO,
        base_currency=Currency.BTC,
        quote_currency=Currency.USD,
        settlement_currency=Currency.USD,
        tick_size=Price(Decimal("0.01"), Currency.USD),
        size_increment=OrderQuantity(Decimal("0.001"), 3),
        minimum_quantity=OrderQuantity(Decimal("0.001"), 3),
        maximum_quantity=OrderQuantity(Decimal("100"), 3),
        minimum_notional=money("0.01"),
        maximum_notional=money("1000000"),
        multiplier=Decimal("1"),
        margin=None,
        session_calendar="24X7",
        provenance=InstrumentProvenance("catalog", "v1", NOW),
    )


@pytest.fixture(name="order_intent")
def fixture_order_intent() -> OrderIntent:
    return OrderIntent(
        intent_id=uid(1),
        risk_decision_id=uid(2),
        client_order_id="sandbox-client-1",
        strategy_id="strategy-1",
        trader_id="trader-1",
        account_id="account-1",
        execution_client_id="execution-1",
        instrument=InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "SIM"),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        quantity=OrderQuantity(Decimal("1"), 3),
        limit_price=Price(Decimal("100"), Currency.USD),
        requested_at=NOW,
        schema_version="order-intent-v1",
    )


@pytest.fixture(name="submitted_envelope")
def fixture_submitted_envelope() -> EventEnvelope[OrderEvent]:
    payload = OrderEvent.create(
        event_id=uid(3),
        order_id=uid(1),
        sequence=1,
        target_status=OrderStatus.SUBMITTED,
        occurred_at=NOW,
    )
    return EventEnvelope[OrderEvent](
        event_id=uid(4),
        event_type="OrderEvent",
        schema_version="sandbox-order-event-v1",
        source="execution-sandbox",
        stream_id=uid(5),
        sequence=1,
        observed_at=NOW,
        ingested_at=NOW,
        produced_at=NOW,
        effective_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        correlation_id=uid(1),
        causation_id=uid(2),
        trace_id=uid(6),
        payload=payload,
    )


@pytest.fixture(name="fill_envelope")
def fixture_fill_envelope(instrument: InstrumentDefinition) -> EventEnvelope[FillEvent]:
    payload = FillEvent(
        execution_id=uid(7),
        order_id=uid(1),
        report_sequence=1,
        venue_trade_id="trade-1",
        instrument_definition=instrument,
        side=OrderSide.BUY,
        liquidity_side=LiquiditySide.TAKER,
        status=FillReportStatus.FILLED,
        quantity=OrderQuantity(Decimal("1"), 3),
        cumulative_fill_quantity=OrderQuantity(Decimal("1"), 3),
        leaves_quantity=OrderQuantity(Decimal("0"), 3),
        order_quantity=OrderQuantity(Decimal("1"), 3),
        last_fill_price=Price(Decimal("100"), Currency.USD),
        average_fill_price=Price(Decimal("100"), Currency.USD),
        commission=money("0"),
        reconciliation_source=ReconciliationSource.VENUE,
        filled_at=NOW,
        schema_version="2.0",
    )
    return EventEnvelope[FillEvent](
        event_id=uid(8),
        event_type="FillEvent",
        schema_version="sandbox-fill-event-v1",
        source="execution-sandbox",
        stream_id=uid(5),
        sequence=2,
        observed_at=NOW,
        ingested_at=NOW,
        produced_at=NOW,
        effective_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        correlation_id=uid(1),
        causation_id=uid(3),
        trace_id=uid(6),
        payload=payload,
    )


def _authority_inputs(intent: OrderIntent) -> tuple[RuntimeRiskObservation, RuntimeRiskPolicy, RiskDecision]:
    exposure = ExposureSnapshot(currency=Currency.USD, gross=money("200"), net=money("200"), pending=money("0"))
    balance = AccountBalanceSnapshot(
        account_id="account-1", currency=Currency.USD, cash=money("10000"),
        locked_funds=money("0"), margin_used=money("0"), realized_pnl=money("0"),
        unrealized_pnl=money("0"), fees=money("0"), funding=money("0"),
        observed_at=NOW, schema_version="account-balance-v1",
    )
    position = AccountPositionSnapshot(
        account_id="account-1", strategy_id="strategy-1",
        instrument=intent.instrument, settlement_currency=Currency.USD,
        quantity=Quantity(Decimal("2"), 3),
        mark=PositionMark(price=Price(Decimal("100"), Currency.USD), marked_at=NOW, provenance_id="mark-1"),
        average_entry_price=Price(Decimal("100"), Currency.USD),
        realized_pnl=money("0"), unrealized_pnl=money("0"), fees=money("0"), funding=money("0"),
        observed_at=NOW, schema_version="account-position-v1",
    )
    portfolio = AccountPortfolioSnapshot(
        snapshot_id=uid(9), account_id="account-1", reporting_currency=Currency.USD,
        balances=(balance,), positions=(position,), total_exposure=exposure,
        instrument_exposures=(InstrumentExposureSnapshot(instrument=intent.instrument, exposure=exposure),),
        strategy_exposures=(StrategyExposureSnapshot(strategy_id="strategy-1", exposure=exposure),),
        venue_exposures=(VenueExposureSnapshot(venue_id="SIM", exposure=exposure),),
        observed_at=NOW, schema_version="account-portfolio-v1",
    )
    observation = RuntimeRiskObservation(
        observation_id=uid(10), state_version=1, portfolio=portfolio,
        instrument_specs=(RuntimeInstrumentRiskSpec(
            instrument=intent.instrument, venue_id="SIM", settlement_currency=Currency.USD,
            price_increment=Price(Decimal("0.01"), Currency.USD),
            quantity_increment=OrderQuantity(Decimal("0.001"), 3),
            min_quantity=OrderQuantity(Decimal("0.001"), 3),
            max_quantity=OrderQuantity(Decimal("100"), 3), min_order_notional=money("1"),
            max_order_notional=money("100000"), initial_margin_rate=Decimal("0.1"),
        ),),
        market_snapshots=(RuntimeRiskMarketSnapshot(
            instrument=intent.instrument, bid=Price(Decimal("99"), Currency.USD),
            ask=Price(Decimal("101"), Currency.USD), last=Price(Decimal("100"), Currency.USD),
            observed_at=NOW, provenance_id="book-1",
        ),),
        conversion_rates=(), venue_health=(RuntimeVenueHealthRecord(
            venue_id="SIM", health=RuntimeVenueHealth.HEALTHY, observed_at=NOW,
        ),),
        engine_ready=True, daily_pnl=money("0"), current_equity=money("1000"),
        peak_equity=money("1000"), command_window_started_at=NOW, commands_in_window=0,
        prior_commands=(), observed_at=NOW, schema_version="runtime-risk-observation-v1",
    )
    target = TargetPortfolio(
        target_id=uid(11), positions=(TargetPosition(instrument=intent.instrument, target_weight=Decimal("0.5")),),
        source_signal_ids=(uid(12),), effective_at=NOW - timedelta(seconds=2), schema_version="target-portfolio-v1",
    )
    policy_decision = RiskDecision(
        decision_id=intent.risk_decision_id, original_target=target, approved_target=target,
        outcome=RiskOutcome.APPROVED, reason_codes=(RiskReasonCode.WITHIN_LIMITS,),
        policy_version="target-policy-v1", state_snapshot=RiskStateSnapshot(
            state_id=uid(13), portfolio=PortfolioSnapshot(snapshot_id=uid(14), positions=(),
            observed_at=NOW - timedelta(seconds=2), schema_version="portfolio-snapshot-v1"),
            open_order_ids=(), kill_switch_engaged=False, observed_at=NOW - timedelta(seconds=2),
            schema_version="risk-state-v1",
        ), decided_at=NOW - timedelta(seconds=1), schema_version="risk-decision-v1",
    )
    policy = RuntimeRiskPolicy(
        policy_id=uid(15), policy_version="runtime-policy-v1", account_id="account-1",
        market_data_max_age_seconds=10, portfolio_max_age_seconds=10,
        max_pending_exposure=money("100000"), max_gross_exposure=money("100000"),
        max_abs_net_exposure=money("100000"), max_strategy_exposure=money("100000"),
        max_venue_exposure=money("100000"), min_available_funds=money("0"),
        max_daily_loss=money("1000"), max_drawdown=money("1000"), command_window_seconds=60,
        max_commands_per_window=10, schema_version="runtime-risk-policy-v1",
    )
    return observation, policy, policy_decision


@pytest.fixture(name="prepared_case")
def fixture_prepared_case(order_intent: OrderIntent) -> PreparedCase:
    observation, policy, policy_decision = _authority_inputs(order_intent)
    decision = evaluate_runtime_order_risk(
        decision_id=uid(16), intent=order_intent, policy_decision=policy_decision,
        observation=observation, policy=policy, decided_at=NOW,
    )
    runtime_event = EventEnvelope(
        event_id=uid(17), event_type="RuntimeOrderRiskDecision", schema_version="runtime-order-risk-event-v1",
        source="runtime-risk", stream_id=uid(18), sequence=1, observed_at=NOW, ingested_at=NOW,
        produced_at=NOW, effective_at=NOW, expires_at=NOW + timedelta(minutes=5),
        correlation_id=order_intent.intent_id, causation_id=order_intent.risk_decision_id, trace_id=uid(19), payload=decision,
    )
    ledger = InMemoryEventLedger()
    reference = record_runtime_risk_decision(repository=ledger, event=runtime_event)
    assert reference is not None
    safety = GlobalSafetyObservation(
        source_fingerprint="a" * 64, kill_switch_state=CanonicalKillSwitchState.INACTIVE,
        observed_at=NOW, schema_version="global-safety-observation-v1",
    )
    state = record_global_halt_observation(
        repository=ledger, stream_id=HALT_STREAM_ID, observation=observation, policy=policy,
        safety=safety, safety_verifier=ExactSafetyVerifier(), transition_id=uid(20), event_id=uid(21), decided_at=NOW,
    )
    assert state.status is GlobalHaltStatus.ACTIVE
    permit = prepare_submit_permit(
        repository=ledger, halt_stream_id=HALT_STREAM_ID, approval_reference=reference, intent=order_intent,
        policy_decision=policy_decision, approval_observation=observation, approval_policy=policy,
        current_observation=observation, current_policy=policy, current_safety=safety,
        safety_verifier=ExactSafetyVerifier(), permit_id=uid(22), event_id=uid(23), prepared_at=NOW + timedelta(seconds=1),
    )
    return PreparedCase(order_intent, observation, policy, safety, permit)
