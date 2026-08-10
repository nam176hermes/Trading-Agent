from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from packages.domain import (
    AccountBalanceSnapshot,
    AccountPortfolioSnapshot,
    AccountPositionSnapshot,
    Currency,
    ExposureSnapshot,
    InstrumentExposureSnapshot,
    InstrumentId,
    Money,
    OrderIntent,
    OrderQuantity,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    PositionMark,
    Price,
    ProductType,
    Quantity,
    RiskDecision,
    RiskOutcome,
    RiskReasonCode,
    RiskStateSnapshot,
    StrategyExposureSnapshot,
    TargetPortfolio,
    TargetPosition,
    TimeInForce,
    VenueExposureSnapshot,
)
from packages.domain.runtime_risk import (
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
from packages.runtime_risk import (
    canonical_model_digest,
    canonical_model_json,
    evaluate_runtime_order_risk,
    project_runtime_order,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "SIM")
SAME_VENUE_INSTRUMENT = InstrumentId(
    "ETH-USD", ProductType.CRYPTO_SPOT, "SIM"
)
OTHER_VENUE_INSTRUMENT = InstrumentId(
    "SOL-USD", ProductType.CRYPTO_SPOT, "ALT"
)
UNRELATED_INSTRUMENT = InstrumentId(
    "ETH-USD", ProductType.CRYPTO_SPOT, "OTHER"
)
MAX_RUNTIME_RISK_DURATION_SECONDS = 86_399_999_999_999


def uid(value: int) -> UUID:
    return UUID(int=value)


def money(amount: str | Decimal, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


def exposure(
    gross: str | Decimal,
    net: str | Decimal,
    *,
    pending: str | Decimal = "0",
) -> ExposureSnapshot:
    return ExposureSnapshot(
        currency=Currency.USD,
        gross=money(gross),
        net=money(net),
        pending=money(pending),
    )


@dataclass(frozen=True)
class EvaluatorCase:
    intent: OrderIntent
    policy_decision: RiskDecision
    observation: RuntimeRiskObservation
    policy: RuntimeRiskPolicy
    decided_at: datetime = NOW

    def evaluate(self, **changes: object) -> RuntimeOrderRiskDecision:
        arguments: dict[str, object] = {
            "decision_id": uid(90),
            "intent": self.intent,
            "policy_decision": self.policy_decision,
            "observation": self.observation,
            "policy": self.policy,
            "decided_at": self.decided_at,
        }
        arguments.update(changes)
        return evaluate_runtime_order_risk(**arguments)  # type: ignore[arg-type]


def evaluator_case(
    *,
    current_quantity: str = "2",
    side: OrderSide = OrderSide.BUY,
    order_quantity: str = "1",
    quantity_precision: int = 3,
    reduce_only: bool = False,
    settlement_currency: Currency = Currency.USD,
    conversion_rate: Decimal | None = None,
) -> EvaluatorCase:
    current = Decimal(current_quantity)
    rate = Decimal(1) if settlement_currency is Currency.USD else conversion_rate
    assert rate is not None
    current_notional = abs(current) * Decimal("100") * rate
    signed_notional = current_notional if current >= 0 else -current_notional
    current_exposure = exposure(current_notional, signed_notional)
    balance = AccountBalanceSnapshot(
        account_id="account-1",
        currency=Currency.USD,
        cash=money("10000"),
        locked_funds=money("0"),
        margin_used=money("0"),
        realized_pnl=money("0"),
        unrealized_pnl=money("0"),
        fees=money("0"),
        funding=money("0"),
        observed_at=NOW,
        schema_version="account-balance-v1",
    )
    positions: tuple[AccountPositionSnapshot, ...] = ()
    partitions: tuple[object, ...] = ()
    if current != 0:
        position = AccountPositionSnapshot(
            account_id="account-1",
            strategy_id="strategy-1",
            instrument=INSTRUMENT,
            settlement_currency=settlement_currency,
            quantity=Quantity(current, 3),
            mark=PositionMark(
                price=Price(Decimal("100"), settlement_currency),
                marked_at=NOW,
                provenance_id="mark-1",
            ),
            average_entry_price=Price(Decimal("100"), settlement_currency),
            realized_pnl=money("0", settlement_currency),
            unrealized_pnl=money("0", settlement_currency),
            fees=money("0", settlement_currency),
            funding=money("0", settlement_currency),
            observed_at=NOW,
            schema_version="account-position-v1",
        )
        positions = (position,)
        partitions = (position,)
    portfolio = AccountPortfolioSnapshot(
        snapshot_id=uid(1),
        account_id="account-1",
        reporting_currency=Currency.USD,
        balances=(balance,),
        positions=positions,
        total_exposure=current_exposure,
        instrument_exposures=(
            ()
            if not partitions
            else (
                InstrumentExposureSnapshot(
                    instrument=INSTRUMENT, exposure=current_exposure
                ),
            )
        ),
        strategy_exposures=(
            ()
            if not partitions
            else (
                StrategyExposureSnapshot(
                    strategy_id="strategy-1", exposure=current_exposure
                ),
            )
        ),
        venue_exposures=(
            ()
            if not partitions
            else (
                VenueExposureSnapshot(venue_id="SIM", exposure=current_exposure),
            )
        ),
        observed_at=NOW,
        schema_version="account-portfolio-v1",
    )
    spec = RuntimeInstrumentRiskSpec(
        instrument=INSTRUMENT,
        venue_id="SIM",
        settlement_currency=settlement_currency,
        price_increment=Price(Decimal("0.01"), settlement_currency),
        quantity_increment=OrderQuantity(Decimal("0.001"), 3),
        min_quantity=OrderQuantity(Decimal("0.001"), 3),
        max_quantity=OrderQuantity(Decimal("100"), 3),
        min_order_notional=money("1"),
        max_order_notional=money("100000"),
        initial_margin_rate=Decimal("0.1"),
    )
    market = RuntimeRiskMarketSnapshot(
        instrument=INSTRUMENT,
        bid=Price(Decimal("99"), settlement_currency),
        ask=Price(Decimal("101"), settlement_currency),
        last=Price(Decimal("100"), settlement_currency),
        observed_at=NOW,
        provenance_id="book-1",
    )
    conversions = ()
    if settlement_currency is not Currency.USD:
        conversions = (
            RuntimeRiskConversionRate(
                source_currency=settlement_currency,
                target_currency=Currency.USD,
                rate=rate,
                observed_at=NOW,
                provenance_id="fx-1",
            ),
        )
    observation = RuntimeRiskObservation(
        observation_id=uid(2),
        state_version=1,
        portfolio=portfolio,
        instrument_specs=(spec,),
        market_snapshots=(market,),
        conversion_rates=conversions,
        venue_health=(
            RuntimeVenueHealthRecord(
                venue_id="SIM",
                health=RuntimeVenueHealth.HEALTHY,
                observed_at=NOW,
            ),
        ),
        engine_ready=True,
        daily_pnl=money("0"),
        current_equity=money("1000"),
        peak_equity=money("1000"),
        command_window_started_at=NOW,
        commands_in_window=0,
        prior_commands=(),
        observed_at=NOW,
        schema_version="runtime-risk-observation-v1",
    )
    target = TargetPortfolio(
        target_id=uid(3),
        positions=(TargetPosition(instrument=INSTRUMENT, target_weight=Decimal("0.5")),),
        source_signal_ids=(uid(4),),
        effective_at=NOW - timedelta(seconds=2),
        schema_version="target-portfolio-v1",
    )
    target_state = RiskStateSnapshot(
        state_id=uid(5),
        portfolio=PortfolioSnapshot(
            snapshot_id=uid(6),
            positions=(),
            observed_at=NOW - timedelta(seconds=2),
            schema_version="portfolio-snapshot-v1",
        ),
        open_order_ids=(),
        kill_switch_engaged=False,
        observed_at=NOW - timedelta(seconds=2),
        schema_version="risk-state-v1",
    )
    policy_decision = RiskDecision(
        decision_id=uid(7),
        original_target=target,
        approved_target=target,
        outcome=RiskOutcome.APPROVED,
        reason_codes=(RiskReasonCode.WITHIN_LIMITS,),
        policy_version="target-policy-v1",
        state_snapshot=target_state,
        decided_at=NOW - timedelta(seconds=1),
        schema_version="risk-decision-v1",
    )
    intent = OrderIntent(
        intent_id=uid(8),
        risk_decision_id=policy_decision.decision_id,
        client_order_id="client-1",
        strategy_id="strategy-1",
        trader_id="trader-1",
        account_id="account-1",
        execution_client_id="execution-1",
        instrument=INSTRUMENT,
        side=side,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        quantity=OrderQuantity(Decimal(order_quantity), quantity_precision),
        limit_price=Price(Decimal("100"), settlement_currency),
        reduce_only=reduce_only,
        requested_at=NOW,
        schema_version="order-intent-v1",
    )
    policy = RuntimeRiskPolicy(
        policy_id=uid(9),
        policy_version="runtime-policy-v1",
        account_id="account-1",
        market_data_max_age_seconds=10,
        portfolio_max_age_seconds=10,
        max_pending_exposure=money("100000"),
        max_gross_exposure=money("100000"),
        max_abs_net_exposure=money("100000"),
        max_strategy_exposure=money("100000"),
        max_venue_exposure=money("100000"),
        min_available_funds=money("0"),
        max_daily_loss=money("1000"),
        max_drawdown=money("1000"),
        command_window_seconds=60,
        max_commands_per_window=10,
        schema_version="runtime-risk-policy-v1",
    )
    return EvaluatorCase(intent, policy_decision, observation, policy)


@pytest.fixture(name="case")
def fixture_case() -> EvaluatorCase:
    return evaluator_case()


def _single_reason_case(reason: RuntimeRiskReasonCode) -> EvaluatorCase:
    case = evaluator_case()
    if reason is RuntimeRiskReasonCode.POLICY_RISK_NOT_APPROVED:
        return EvaluatorCase(
            case.intent.model_copy(update={"risk_decision_id": uid(999)}),
            case.policy_decision,
            case.observation,
            case.policy,
        )
    if reason is RuntimeRiskReasonCode.ENGINE_NOT_READY:
        observation = case.observation.model_copy(update={"engine_ready": False})
    elif reason is RuntimeRiskReasonCode.INSTRUMENT_UNKNOWN:
        observation = case.observation.model_copy(update={"instrument_specs": ()})
    elif reason is RuntimeRiskReasonCode.MARKET_DATA_STALE:
        observation = case.observation.model_copy(update={"market_snapshots": ()})
    elif reason is RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING:
        case = evaluator_case(
            settlement_currency=Currency.USDT,
            conversion_rate=Decimal("1"),
        )
        observation = case.observation.model_copy(update={"conversion_rates": ()})
    elif reason is RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID:
        portfolio = case.observation.portfolio.model_copy(
            update={"instrument_exposures": ()}
        )
        observation = case.observation.model_copy(update={"portfolio": portfolio})
    elif reason is RuntimeRiskReasonCode.PRICE_PRECISION_INVALID:
        intent = case.intent.model_copy(
            update={"limit_price": Price(Decimal("100.001"), Currency.USD)}
        )
        return EvaluatorCase(
            intent, case.policy_decision, case.observation, case.policy
        )
    elif reason is RuntimeRiskReasonCode.QUANTITY_PRECISION_INVALID:
        spec = case.observation.instrument_specs[0].model_copy(
            update={"quantity_increment": OrderQuantity(Decimal("0.003"), 3)}
        )
        observation = case.observation.model_copy(update={"instrument_specs": (spec,)})
    elif reason is RuntimeRiskReasonCode.QUANTITY_OUT_OF_BOUNDS:
        spec = case.observation.instrument_specs[0].model_copy(
            update={"min_quantity": OrderQuantity(Decimal("1.001"), 3)}
        )
        observation = case.observation.model_copy(update={"instrument_specs": (spec,)})
    elif reason is RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT:
        spec = case.observation.instrument_specs[0].model_copy(
            update={"max_order_notional": money("100")}
        )
        observation = case.observation.model_copy(update={"instrument_specs": (spec,)})
    elif reason is RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT:
        policy = case.policy.model_copy(update={"min_available_funds": money("9990")})
        return EvaluatorCase(
            case.intent, case.policy_decision, case.observation, policy
        )
    elif reason is RuntimeRiskReasonCode.PENDING_EXPOSURE_LIMIT:
        policy = case.policy.model_copy(update={"max_pending_exposure": money("100")})
        return EvaluatorCase(
            case.intent, case.policy_decision, case.observation, policy
        )
    elif reason is RuntimeRiskReasonCode.GROSS_EXPOSURE_LIMIT:
        policy = case.policy.model_copy(update={"max_gross_exposure": money("302")})
        return EvaluatorCase(
            case.intent, case.policy_decision, case.observation, policy
        )
    elif reason is RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT:
        policy = case.policy.model_copy(update={"max_abs_net_exposure": money("302")})
        return EvaluatorCase(
            case.intent, case.policy_decision, case.observation, policy
        )
    elif reason is RuntimeRiskReasonCode.STRATEGY_EXPOSURE_LIMIT:
        policy = case.policy.model_copy(update={"max_strategy_exposure": money("302")})
        return EvaluatorCase(
            case.intent, case.policy_decision, case.observation, policy
        )
    elif reason is RuntimeRiskReasonCode.VENUE_EXPOSURE_LIMIT:
        policy = case.policy.model_copy(update={"max_venue_exposure": money("302")})
        return EvaluatorCase(
            case.intent, case.policy_decision, case.observation, policy
        )
    elif reason is RuntimeRiskReasonCode.DAILY_LOSS_LIMIT:
        observation = case.observation.model_copy(update={"daily_pnl": money("-1000.01")})
    elif reason is RuntimeRiskReasonCode.DRAWDOWN_LIMIT:
        observation = case.observation.model_copy(update={"peak_equity": money("2000.01")})
    elif reason is RuntimeRiskReasonCode.REDUCE_ONLY_VIOLATION:
        intent = case.intent.model_copy(update={"reduce_only": True})
        return EvaluatorCase(
            intent, case.policy_decision, case.observation, case.policy
        )
    elif reason is RuntimeRiskReasonCode.COMMAND_RATE_LIMIT:
        observation = case.observation.model_copy(update={"commands_in_window": 10})
    elif reason is RuntimeRiskReasonCode.VENUE_UNHEALTHY:
        venue = case.observation.venue_health[0].model_copy(
            update={"health": RuntimeVenueHealth.DEGRADED}
        )
        observation = case.observation.model_copy(update={"venue_health": (venue,)})
    elif reason is RuntimeRiskReasonCode.DUPLICATE_COMMAND:
        observation = case.observation.model_copy(
            update={
                "prior_commands": (
                    PriorRuntimeCommandIdentity(
                        intent_id=case.intent.intent_id,
                        client_order_id="different-client",
                    ),
                )
            }
        )
    else:  # pragma: no cover - guarded by the closed parameter list below
        raise AssertionError(f"unsupported reason: {reason}")
    return EvaluatorCase(
        case.intent,
        case.policy_decision,
        observation,
        case.policy,
    )


@pytest.mark.parametrize(
    "reason",
    tuple(
        reason
        for reason in RuntimeRiskReasonCode
        if reason is not RuntimeRiskReasonCode.WITHIN_LIMITS
    ),
)
def test_each_rejection_reason_has_an_independent_mutation(
    reason: RuntimeRiskReasonCode,
) -> None:
    case = _single_reason_case(reason)

    decision = case.evaluate()

    assert decision.reason_codes == (reason,)


def test_valid_order_is_approved_with_exact_bindings_and_projections(
    case: EvaluatorCase,
) -> None:
    decision = case.evaluate()
    projection = project_runtime_order(
        case.intent, case.observation, case.policy, decided_at=NOW
    )

    assert decision.outcome is RuntimeRiskOutcome.APPROVED
    assert decision.reason_codes == (RuntimeRiskReasonCode.WITHIN_LIMITS,)
    assert decision.intent_digest == canonical_model_digest(case.intent)
    assert decision.policy_risk_decision_digest == canonical_model_digest(
        case.policy_decision
    )
    assert decision.portfolio_digest == canonical_model_digest(
        case.observation.portfolio
    )
    assert decision.observation_digest == canonical_model_digest(case.observation)
    assert decision.policy_digest == canonical_model_digest(case.policy)
    for name in type(projection).model_fields:
        assert getattr(decision, name) == getattr(projection, name)


def test_complete_rejection_reason_tuple_is_in_fixed_order(case: EvaluatorCase) -> None:
    rejected_target = RiskDecision(
        decision_id=case.policy_decision.decision_id,
        original_target=case.policy_decision.original_target,
        approved_target=None,
        outcome=RiskOutcome.REJECTED,
        reason_codes=(RiskReasonCode.MODEL_NOT_APPROVED,),
        policy_version=case.policy_decision.policy_version,
        state_snapshot=case.policy_decision.state_snapshot,
        decided_at=case.policy_decision.decided_at,
        schema_version=case.policy_decision.schema_version,
    )
    prior = PriorRuntimeCommandIdentity(
        intent_id=case.intent.intent_id,
        client_order_id=case.intent.client_order_id,
    )
    observation = case.observation.model_copy(
        update={
            "engine_ready": False,
            "market_snapshots": (
                case.observation.market_snapshots[0].model_copy(
                    update={"observed_at": NOW - timedelta(seconds=11)}
                ),
            ),
            "daily_pnl": money("-101"),
            "current_equity": money("799"),
            "peak_equity": money("1000"),
            "commands_in_window": 10,
            "venue_health": (
                RuntimeVenueHealthRecord(
                    venue_id="SIM",
                    health=RuntimeVenueHealth.DEGRADED,
                    observed_at=NOW,
                ),
            ),
            "prior_commands": (prior,),
        }
    )
    spec = observation.instrument_specs[0].model_copy(
        update={
            "price_increment": Price(Decimal("3"), Currency.USD),
            "quantity_increment": OrderQuantity(Decimal("2"), 3),
            "min_quantity": OrderQuantity(Decimal("2"), 3),
            "max_quantity": OrderQuantity(Decimal("2"), 3),
            "min_order_notional": money("200"),
            "max_order_notional": money("200"),
        }
    )
    observation = observation.model_copy(update={"instrument_specs": (spec,)})
    policy = case.policy.model_copy(
        update={
            "min_available_funds": money("9991"),
            "max_pending_exposure": money("99"),
            "max_gross_exposure": money("299"),
            "max_abs_net_exposure": money("299"),
            "max_strategy_exposure": money("299"),
            "max_venue_exposure": money("299"),
            "max_daily_loss": money("100"),
            "max_drawdown": money("200"),
        }
    )
    intent = case.intent.model_copy(
        update={"quantity": OrderQuantity(Decimal("3"), 3), "reduce_only": True}
    )

    decision = case.evaluate(
        intent=intent,
        policy_decision=rejected_target,
        observation=observation,
        policy=policy,
    )

    assert decision.reason_codes == (
        RuntimeRiskReasonCode.POLICY_RISK_NOT_APPROVED,
        RuntimeRiskReasonCode.ENGINE_NOT_READY,
        RuntimeRiskReasonCode.MARKET_DATA_STALE,
        RuntimeRiskReasonCode.PRICE_PRECISION_INVALID,
        RuntimeRiskReasonCode.QUANTITY_PRECISION_INVALID,
        RuntimeRiskReasonCode.QUANTITY_OUT_OF_BOUNDS,
        RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,
        RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,
        RuntimeRiskReasonCode.PENDING_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.GROSS_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.STRATEGY_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.VENUE_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.DAILY_LOSS_LIMIT,
        RuntimeRiskReasonCode.DRAWDOWN_LIMIT,
        RuntimeRiskReasonCode.REDUCE_ONLY_VIOLATION,
        RuntimeRiskReasonCode.COMMAND_RATE_LIMIT,
        RuntimeRiskReasonCode.VENUE_UNHEALTHY,
        RuntimeRiskReasonCode.DUPLICATE_COMMAND,
    )
    assert decision.risk_price is not None
    assert decision.projected_available_funds is not None


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("instrument", RuntimeRiskReasonCode.INSTRUMENT_UNKNOWN),
        ("valuation", RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING),
        ("portfolio", RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID),
    ],
)
def test_missing_projection_authority_preserves_independent_later_reasons(
    authority: str,
    expected: RuntimeRiskReasonCode,
) -> None:
    case = (
        evaluator_case(
            settlement_currency=Currency.USDT,
            conversion_rate=Decimal("1"),
        )
        if authority == "valuation"
        else evaluator_case()
    )
    prior = PriorRuntimeCommandIdentity(
        intent_id=case.intent.intent_id,
        client_order_id=case.intent.client_order_id,
    )
    changes: dict[str, object] = {
        "engine_ready": False,
        "commands_in_window": case.policy.max_commands_per_window,
        "venue_health": (
            RuntimeVenueHealthRecord(
                venue_id="SIM",
                health=RuntimeVenueHealth.UNKNOWN,
                observed_at=NOW,
            ),
        ),
        "prior_commands": (prior,),
    }
    if authority == "instrument":
        changes["instrument_specs"] = ()
    elif authority == "valuation":
        changes["conversion_rates"] = ()
    else:
        changes["portfolio"] = case.observation.portfolio.model_copy(
            update={"instrument_exposures": ()}
        )
    observation = case.observation.model_copy(update=changes)

    decision = case.evaluate(observation=observation)

    assert decision.reason_codes == (
        RuntimeRiskReasonCode.ENGINE_NOT_READY,
        expected,
        RuntimeRiskReasonCode.COMMAND_RATE_LIMIT,
        RuntimeRiskReasonCode.VENUE_UNHEALTHY,
        RuntimeRiskReasonCode.DUPLICATE_COMMAND,
    )
    assert decision.risk_price is None
    assert decision.projected_gross is None


def test_wrong_target_reference_and_valid_rejected_target_are_policy_rejections(
    case: EvaluatorCase,
) -> None:
    wrong_reference = case.intent.model_copy(update={"risk_decision_id": uid(999)})
    rejected = RiskDecision(
        decision_id=case.policy_decision.decision_id,
        original_target=case.policy_decision.original_target,
        approved_target=None,
        outcome=RiskOutcome.REJECTED,
        reason_codes=(RiskReasonCode.MODEL_NOT_APPROVED,),
        policy_version=case.policy_decision.policy_version,
        state_snapshot=case.policy_decision.state_snapshot,
        decided_at=case.policy_decision.decided_at,
        schema_version=case.policy_decision.schema_version,
    )

    assert case.evaluate(intent=wrong_reference).reason_codes == (
        RuntimeRiskReasonCode.POLICY_RISK_NOT_APPROVED,
    )
    assert case.evaluate(policy_decision=rejected).reason_codes == (
        RuntimeRiskReasonCode.POLICY_RISK_NOT_APPROVED,
    )


def test_invalid_model_copy_or_construct_inputs_return_no_decision(
    case: EvaluatorCase,
) -> None:
    forged_target = case.policy_decision.model_copy(
        update={"outcome": RiskOutcome.REJECTED}
    )
    constructed_intent = OrderIntent.model_construct()

    with pytest.raises(ValueError, match="canonically"):
        case.evaluate(policy_decision=forged_target)
    with pytest.raises(ValueError, match="canonically"):
        case.evaluate(intent=constructed_intent)


def test_recursive_runtime_observation_is_rejected_without_recursion_error(
    case: EvaluatorCase,
) -> None:
    forged = case.observation.model_copy()
    object.__setattr__(forged, "portfolio", forged)

    with pytest.raises(ValueError, match="canonically represented") as caught:
        case.evaluate(observation=forged)

    assert type(caught.value.__cause__) is ValueError


@pytest.mark.parametrize(
    ("age", "reason"),
    [
        (10, (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        (11, (RuntimeRiskReasonCode.MARKET_DATA_STALE,)),
    ],
)
def test_market_freshness_boundary(
    case: EvaluatorCase,
    age: int,
    reason: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    market = case.observation.market_snapshots[0].model_copy(
        update={"observed_at": NOW - timedelta(seconds=age)}
    )
    observation = case.observation.model_copy(update={"market_snapshots": (market,)})

    assert case.evaluate(observation=observation).reason_codes == reason


@pytest.mark.parametrize("age", [10, 11])
def test_portfolio_and_mark_freshness_boundary(case: EvaluatorCase, age: int) -> None:
    position = case.observation.portfolio.positions[0]
    mark = position.mark
    assert mark is not None
    stale_at = NOW - timedelta(seconds=age)
    position = position.model_copy(
        update={
            "mark": mark.model_copy(update={"marked_at": stale_at}),
            "observed_at": stale_at,
        }
    )
    balances = tuple(
        balance.model_copy(update={"observed_at": stale_at})
        for balance in case.observation.portfolio.balances
    )
    portfolio = case.observation.portfolio.model_copy(
        update={
            "balances": balances,
            "positions": (position,),
            "observed_at": stale_at,
        }
    )
    observation = case.observation.model_copy(update={"portfolio": portfolio})

    expected = (
        (RuntimeRiskReasonCode.WITHIN_LIMITS,)
        if age == 10
        else (RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,)
    )
    assert case.evaluate(observation=observation).reason_codes == expected


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("price", Decimal("100.001"), RuntimeRiskReasonCode.PRICE_PRECISION_INVALID),
        ("quantity", Decimal("1.0001"), RuntimeRiskReasonCode.QUANTITY_PRECISION_INVALID),
    ],
)
def test_one_increment_off_precision_is_rejected(
    case: EvaluatorCase,
    field: str,
    value: Decimal,
    expected: RuntimeRiskReasonCode,
) -> None:
    if field == "price":
        intent = case.intent.model_copy(
            update={"limit_price": Price(value, Currency.USD)}
        )
    else:
        intent = case.intent.model_copy(
            update={"quantity": OrderQuantity(value, 4)}
        )

    assert expected in case.evaluate(intent=intent).reason_codes


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        ("1", "1", (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        ("1.001", "2", (RuntimeRiskReasonCode.QUANTITY_OUT_OF_BOUNDS,)),
        ("0.001", "0.999", (RuntimeRiskReasonCode.QUANTITY_OUT_OF_BOUNDS,)),
    ],
    ids=["equal-minimum-and-maximum", "one-unit-below-minimum", "one-unit-above-maximum"],
)
def test_quantity_bounds_are_inclusive(
    case: EvaluatorCase,
    minimum: str,
    maximum: str,
    expected: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    spec = case.observation.instrument_specs[0].model_copy(
        update={
            "min_quantity": OrderQuantity(Decimal(minimum), 3),
            "max_quantity": OrderQuantity(Decimal(maximum), 3),
        }
    )
    observation = case.observation.model_copy(update={"instrument_specs": (spec,)})

    assert case.evaluate(observation=observation).reason_codes == expected


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        ("101", "101", (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        ("102", "100000", (RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,)),
        ("1", "100", (RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,)),
    ],
    ids=["equal-minimum-and-maximum", "one-dollar-below-minimum", "one-dollar-above-maximum"],
)
def test_order_notional_bounds_are_inclusive(
    case: EvaluatorCase,
    minimum: str,
    maximum: str,
    expected: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    spec = case.observation.instrument_specs[0].model_copy(
        update={
            "min_order_notional": money(minimum),
            "max_order_notional": money(maximum),
        }
    )
    observation = case.observation.model_copy(update={"instrument_specs": (spec,)})

    assert case.evaluate(observation=observation).reason_codes == expected


@pytest.mark.parametrize(
    ("rate", "minimum", "maximum", "expected_notional", "expected_reasons"),
    [
        (
            Decimal("0.5"),
            "50.50",
            "1000",
            "50.50",
            (RuntimeRiskReasonCode.WITHIN_LIMITS,),
        ),
        (
            Decimal("0.5"),
            "50.51",
            "1000",
            "50.50",
            (RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,),
        ),
        (
            Decimal("2"),
            "1",
            "202",
            "202",
            (RuntimeRiskReasonCode.WITHIN_LIMITS,),
        ),
        (
            Decimal("2"),
            "1",
            "201.99",
            "202",
            (RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,),
        ),
    ],
    ids=("minimum-equality", "below-minimum", "maximum-equality", "above-maximum"),
)
def test_cross_currency_notional_bounds_compare_exact_reporting_notional(
    rate: Decimal,
    minimum: str,
    maximum: str,
    expected_notional: str,
    expected_reasons: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    case = evaluator_case(
        settlement_currency=Currency.USDT,
        conversion_rate=rate,
    )
    spec = case.observation.instrument_specs[0].model_copy(
        update={
            "min_order_notional": money(minimum),
            "max_order_notional": money(maximum),
        }
    )
    observation = case.observation.model_copy(update={"instrument_specs": (spec,)})

    decision = case.evaluate(observation=observation)

    assert decision.order_notional == money(expected_notional)
    assert decision.reason_codes == expected_reasons


@pytest.mark.parametrize(
    ("side", "current", "quantity", "approved"),
    [
        (OrderSide.SELL, "2", "1", True),
        (OrderSide.SELL, "2", "2", True),
        (OrderSide.BUY, "2", "1", False),
        (OrderSide.SELL, "2", "3", False),
        (OrderSide.BUY, "-2", "1", True),
        (OrderSide.BUY, "-2", "2", True),
        (OrderSide.SELL, "-2", "1", False),
        (OrderSide.BUY, "-2", "3", False),
    ],
)
def test_reduce_only_closes_without_increasing_crossing_or_reversing(
    side: OrderSide,
    current: str,
    quantity: str,
    approved: bool,
) -> None:
    case = evaluator_case(
        current_quantity=current,
        side=side,
        order_quantity=quantity,
        reduce_only=True,
    )

    reasons = case.evaluate().reason_codes

    assert (RuntimeRiskReasonCode.REDUCE_ONLY_VIOLATION not in reasons) is approved


@pytest.mark.parametrize(
    ("started", "count", "expected"),
    [
        (NOW - timedelta(seconds=60), 9, (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        (NOW - timedelta(seconds=60), 10, (RuntimeRiskReasonCode.COMMAND_RATE_LIMIT,)),
        (NOW - timedelta(seconds=61), 0, (RuntimeRiskReasonCode.COMMAND_RATE_LIMIT,)),
    ],
)
def test_command_window_counts_new_command_and_requires_active_window(
    case: EvaluatorCase,
    started: datetime,
    count: int,
    expected: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    observation = case.observation.model_copy(
        update={"command_window_started_at": started, "commands_in_window": count}
    )

    assert case.evaluate(observation=observation).reason_codes == expected


def test_evaluator_rejects_forged_window_start_after_observation(
    case: EvaluatorCase,
) -> None:
    forged = case.observation.model_copy()
    object.__setattr__(
        forged,
        "command_window_started_at",
        NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="canonically represented"):
        case.evaluate(observation=forged, decided_at=NOW + timedelta(seconds=5))


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (60, (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        (61, (RuntimeRiskReasonCode.COMMAND_RATE_LIMIT,)),
    ],
    ids=("decision-at-window-end", "decision-after-window-end"),
)
def test_command_window_must_also_contain_decision_time(
    case: EvaluatorCase,
    elapsed: int,
    expected: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    policy = case.policy.model_copy(
        update={
            "market_data_max_age_seconds": 120,
            "portfolio_max_age_seconds": 120,
            "command_window_seconds": 60,
        }
    )

    assert case.evaluate(
        policy=policy,
        decided_at=NOW + timedelta(seconds=elapsed),
    ).reason_codes == expected


def test_exact_maximum_duration_policy_evaluates_without_overflow(
    case: EvaluatorCase,
) -> None:
    policy = case.policy.model_copy(
        update={
            "market_data_max_age_seconds": MAX_RUNTIME_RISK_DURATION_SECONDS,
            "portfolio_max_age_seconds": MAX_RUNTIME_RISK_DURATION_SECONDS,
            "command_window_seconds": MAX_RUNTIME_RISK_DURATION_SECONDS,
        }
    )

    assert case.evaluate(policy=policy).reason_codes == (
        RuntimeRiskReasonCode.WITHIN_LIMITS,
    )


@pytest.mark.parametrize(
    "field",
    (
        "market_data_max_age_seconds",
        "portfolio_max_age_seconds",
        "command_window_seconds",
    ),
)
def test_evaluator_rejects_forged_unbounded_duration_without_overflow(
    case: EvaluatorCase,
    field: str,
) -> None:
    forged = case.policy.model_copy()
    object.__setattr__(forged, field, 10**30)

    with pytest.raises(ValueError, match="canonically represented"):
        case.evaluate(policy=forged)


@pytest.mark.parametrize("health", [RuntimeVenueHealth.DEGRADED, RuntimeVenueHealth.UNKNOWN])
def test_nonhealthy_venue_is_rejected(
    case: EvaluatorCase, health: RuntimeVenueHealth
) -> None:
    observation = case.observation.model_copy(
        update={
            "venue_health": (
                RuntimeVenueHealthRecord(
                    venue_id="SIM", health=health, observed_at=NOW
                ),
            )
        }
    )

    assert case.evaluate(observation=observation).reason_codes == (
        RuntimeRiskReasonCode.VENUE_UNHEALTHY,
    )


@pytest.mark.parametrize("duplicate", ["intent", "client"])
def test_either_command_identity_is_a_duplicate(
    case: EvaluatorCase, duplicate: str
) -> None:
    prior = PriorRuntimeCommandIdentity(
        intent_id=case.intent.intent_id if duplicate == "intent" else uid(81),
        client_order_id=(
            case.intent.client_order_id if duplicate == "client" else "different-client"
        ),
    )
    observation = case.observation.model_copy(update={"prior_commands": (prior,)})

    assert case.evaluate(observation=observation).reason_codes == (
        RuntimeRiskReasonCode.DUPLICATE_COMMAND,
    )


def test_exact_limits_are_inclusive_and_one_unit_breaches_reject(case: EvaluatorCase) -> None:
    projection = project_runtime_order(
        case.intent, case.observation, case.policy, decided_at=NOW
    )
    exact_policy = case.policy.model_copy(
        update={
            "max_pending_exposure": projection.projected_pending,
            "max_gross_exposure": projection.projected_gross,
            "max_abs_net_exposure": money(abs(projection.projected_net.amount)),
            "max_strategy_exposure": projection.projected_strategy_gross,
            "max_venue_exposure": projection.projected_venue_gross,
            "min_available_funds": projection.projected_available_funds,
            "max_daily_loss": money("100"),
            "max_drawdown": money("200"),
        }
    )
    observation = case.observation.model_copy(
        update={
            "daily_pnl": money("-100"),
            "current_equity": money("800"),
            "peak_equity": money("1000"),
        }
    )
    assert case.evaluate(policy=exact_policy, observation=observation).outcome is RuntimeRiskOutcome.APPROVED

    breached = exact_policy.model_copy(
        update={
            "max_pending_exposure": money(projection.projected_pending.amount - Decimal("1")),
            "max_gross_exposure": money(projection.projected_gross.amount - Decimal("1")),
            "max_abs_net_exposure": money(abs(projection.projected_net.amount) - Decimal("1")),
            "max_strategy_exposure": money(projection.projected_strategy_gross.amount - Decimal("1")),
            "max_venue_exposure": money(projection.projected_venue_gross.amount - Decimal("1")),
            "min_available_funds": money(projection.projected_available_funds.amount + Decimal("1")),
            "max_daily_loss": money("99"),
            "max_drawdown": money("199"),
        }
    )
    assert case.evaluate(policy=breached, observation=observation).reason_codes == (
        RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,
        RuntimeRiskReasonCode.PENDING_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.GROSS_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.STRATEGY_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.VENUE_EXPOSURE_LIMIT,
        RuntimeRiskReasonCode.DAILY_LOSS_LIMIT,
        RuntimeRiskReasonCode.DRAWDOWN_LIMIT,
    )


def _marked_position(
    *,
    strategy_id: str,
    instrument: InstrumentId,
    quantity: str,
    mark: str,
) -> AccountPositionSnapshot:
    return AccountPositionSnapshot(
        account_id="account-1",
        strategy_id=strategy_id,
        instrument=instrument,
        settlement_currency=Currency.USD,
        quantity=Quantity(Decimal(quantity), 3),
        mark=PositionMark(
            price=Price(Decimal(mark), Currency.USD),
            marked_at=NOW,
            provenance_id=f"mark-{strategy_id}-{instrument.symbol}",
        ),
        average_entry_price=Price(Decimal(mark), Currency.USD),
        realized_pnl=money("0"),
        unrealized_pnl=money("0"),
        fees=money("0"),
        funding=money("0"),
        observed_at=NOW,
        schema_version="account-position-v1",
    )


def _distinct_exposure_case() -> EvaluatorCase:
    case = evaluator_case()
    positions = tuple(
        sorted(
            (
                _marked_position(
                    strategy_id="strategy-1",
                    instrument=INSTRUMENT,
                    quantity="2",
                    mark="100",
                ),
                _marked_position(
                    strategy_id="strategy-1",
                    instrument=OTHER_VENUE_INSTRUMENT,
                    quantity="1",
                    mark="40",
                ),
                _marked_position(
                    strategy_id="strategy-2",
                    instrument=SAME_VENUE_INSTRUMENT,
                    quantity="1",
                    mark="60",
                ),
                _marked_position(
                    strategy_id="strategy-3",
                    instrument=UNRELATED_INSTRUMENT,
                    quantity="-1",
                    mark="30",
                ),
            ),
            key=lambda position: (
                position.strategy_id,
                position.instrument.canonical,
            ),
        )
    )
    instrument_exposures = tuple(
        sorted(
            (
                InstrumentExposureSnapshot(
                    instrument=INSTRUMENT, exposure=exposure("200", "200")
                ),
                InstrumentExposureSnapshot(
                    instrument=SAME_VENUE_INSTRUMENT,
                    exposure=exposure("60", "60"),
                ),
                InstrumentExposureSnapshot(
                    instrument=OTHER_VENUE_INSTRUMENT,
                    exposure=exposure("40", "40"),
                ),
                InstrumentExposureSnapshot(
                    instrument=UNRELATED_INSTRUMENT,
                    exposure=exposure("30", "-30"),
                ),
            ),
            key=lambda item: item.instrument.canonical,
        )
    )
    portfolio = case.observation.portfolio.model_copy(
        update={
            "positions": positions,
            "total_exposure": exposure("330", "270"),
            "instrument_exposures": instrument_exposures,
            "strategy_exposures": (
                StrategyExposureSnapshot(
                    strategy_id="strategy-1", exposure=exposure("240", "240")
                ),
                StrategyExposureSnapshot(
                    strategy_id="strategy-2", exposure=exposure("60", "60")
                ),
                StrategyExposureSnapshot(
                    strategy_id="strategy-3", exposure=exposure("30", "-30")
                ),
            ),
            "venue_exposures": (
                VenueExposureSnapshot(
                    venue_id="ALT", exposure=exposure("40", "40")
                ),
                VenueExposureSnapshot(
                    venue_id="OTHER", exposure=exposure("30", "-30")
                ),
                VenueExposureSnapshot(
                    venue_id="SIM", exposure=exposure("260", "260")
                ),
            ),
        }
    )
    observation = case.observation.model_copy(update={"portfolio": portfolio})
    return EvaluatorCase(
        case.intent, case.policy_decision, observation, case.policy
    )


@pytest.mark.parametrize(
    ("policy_field", "threshold", "reason"),
    [
        (
            "max_pending_exposure",
            "100",
            RuntimeRiskReasonCode.PENDING_EXPOSURE_LIMIT,
        ),
        ("max_gross_exposure", "432", RuntimeRiskReasonCode.GROSS_EXPOSURE_LIMIT),
        ("max_abs_net_exposure", "372", RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT),
        (
            "max_strategy_exposure",
            "342",
            RuntimeRiskReasonCode.STRATEGY_EXPOSURE_LIMIT,
        ),
        (
            "max_venue_exposure",
            "362",
            RuntimeRiskReasonCode.VENUE_EXPOSURE_LIMIT,
        ),
    ],
)
def test_each_exposure_limit_reads_its_distinct_projection(
    policy_field: str,
    threshold: str,
    reason: RuntimeRiskReasonCode,
) -> None:
    case = _distinct_exposure_case()
    projection = project_runtime_order(
        case.intent, case.observation, case.policy, decided_at=NOW
    )
    assert projection.projected_pending == money("101")
    assert projection.projected_gross == money("433")
    assert projection.projected_net == money("373")
    assert projection.projected_strategy_gross == money("343")
    assert projection.projected_venue_gross == money("363")
    policy = case.policy.model_copy(update={policy_field: money(threshold)})

    assert case.evaluate(policy=policy).reason_codes == (reason,)


def test_distinct_exposure_limits_accept_exact_boundaries() -> None:
    case = _distinct_exposure_case()
    policy = case.policy.model_copy(
        update={
            "max_pending_exposure": money("101"),
            "max_gross_exposure": money("433"),
            "max_abs_net_exposure": money("373"),
            "max_strategy_exposure": money("343"),
            "max_venue_exposure": money("363"),
        }
    )

    assert case.evaluate(policy=policy).reason_codes == (
        RuntimeRiskReasonCode.WITHIN_LIMITS,
    )


@pytest.mark.parametrize(
    ("current", "side", "exact_limit", "breach_limit"),
    [
        ("2", OrderSide.BUY, "303", "302"),
        ("-2", OrderSide.SELL, "300", "299"),
    ],
    ids=["positive-net", "negative-net"],
)
def test_absolute_net_limit_accepts_equality_and_rejects_one_unit_breach(
    current: str,
    side: OrderSide,
    exact_limit: str,
    breach_limit: str,
) -> None:
    case = evaluator_case(current_quantity=current, side=side)
    exact = case.policy.model_copy(
        update={"max_abs_net_exposure": money(exact_limit)}
    )
    breached = case.policy.model_copy(
        update={"max_abs_net_exposure": money(breach_limit)}
    )

    assert case.evaluate(policy=exact).reason_codes == (
        RuntimeRiskReasonCode.WITHIN_LIMITS,
    )
    assert case.evaluate(policy=breached).reason_codes == (
        RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT,
    )


def test_missing_reporting_balance_is_balance_margin_rejection(case: EvaluatorCase) -> None:
    portfolio = case.observation.portfolio.model_copy(update={"balances": ()})
    observation = case.observation.model_copy(update={"portfolio": portfolio})

    assert case.evaluate(observation=observation).reason_codes == (
        RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,
    )


def test_different_currency_balance_does_not_supply_reporting_funds(
    case: EvaluatorCase,
) -> None:
    balance = case.observation.portfolio.balances[0]
    usdt_balance = AccountBalanceSnapshot(
        account_id=balance.account_id,
        currency=Currency.USDT,
        cash=money("10000", Currency.USDT),
        locked_funds=money("0", Currency.USDT),
        margin_used=money("0", Currency.USDT),
        realized_pnl=money("0", Currency.USDT),
        unrealized_pnl=money("0", Currency.USDT),
        fees=money("0", Currency.USDT),
        funding=money("0", Currency.USDT),
        observed_at=balance.observed_at,
        schema_version=balance.schema_version,
    )
    portfolio = case.observation.portfolio.model_copy(
        update={"balances": (usdt_balance,)}
    )
    observation = case.observation.model_copy(update={"portfolio": portfolio})

    assert case.evaluate(observation=observation).reason_codes == (
        RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,
    )


def _binding_case(binding: str) -> tuple[EvaluatorCase, tuple[RuntimeRiskReasonCode, ...]]:
    case = evaluator_case()
    if binding == "intent-account":
        intent = case.intent.model_copy(update={"account_id": "account-2"})
        return (
            EvaluatorCase(intent, case.policy_decision, case.observation, case.policy),
            (RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,),
        )
    if binding == "policy-account":
        policy = case.policy.model_copy(update={"account_id": "account-2"})
        return (
            EvaluatorCase(case.intent, case.policy_decision, case.observation, policy),
            (RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,),
        )
    if binding == "intent-instrument":
        intent = case.intent.model_copy(update={"instrument": SAME_VENUE_INSTRUMENT})
        return (
            EvaluatorCase(intent, case.policy_decision, case.observation, case.policy),
            (RuntimeRiskReasonCode.INSTRUMENT_UNKNOWN,),
        )
    if binding == "instrument-spec":
        spec = case.observation.instrument_specs[0].model_copy(
            update={"instrument": SAME_VENUE_INSTRUMENT}
        )
        observation = case.observation.model_copy(update={"instrument_specs": (spec,)})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.INSTRUMENT_UNKNOWN,),
        )
    if binding == "spec-venue":
        spec = case.observation.instrument_specs[0].model_copy(
            update={"venue_id": "ALT"}
        )
        observation = case.observation.model_copy(update={"instrument_specs": (spec,)})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.INSTRUMENT_UNKNOWN,),
        )
    if binding == "market-instrument":
        market = case.observation.market_snapshots[0].model_copy(
            update={"instrument": SAME_VENUE_INSTRUMENT}
        )
        observation = case.observation.model_copy(update={"market_snapshots": (market,)})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.MARKET_DATA_STALE,),
        )
    if binding == "market-currency":
        market = case.observation.market_snapshots[0].model_copy(
            update={
                "bid": Price(Decimal("99"), Currency.USDT),
                "ask": Price(Decimal("101"), Currency.USDT),
                "last": Price(Decimal("100"), Currency.USDT),
            }
        )
        observation = case.observation.model_copy(update={"market_snapshots": (market,)})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.MARKET_DATA_STALE,),
        )
    if binding == "order-price-currency":
        intent = case.intent.model_copy(
            update={"limit_price": Price(Decimal("100"), Currency.USDT)}
        )
        return (
            EvaluatorCase(intent, case.policy_decision, case.observation, case.policy),
            (
                RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING,
                RuntimeRiskReasonCode.PRICE_PRECISION_INVALID,
            ),
        )
    if binding == "policy-currency":
        changes = {
            name: money(getattr(case.policy, name).amount, Currency.USDT)
            for name in (
                "max_pending_exposure",
                "max_gross_exposure",
                "max_abs_net_exposure",
                "max_strategy_exposure",
                "max_venue_exposure",
                "min_available_funds",
                "max_daily_loss",
                "max_drawdown",
            )
        }
        policy = case.policy.model_copy(update=changes)
        return (
            EvaluatorCase(case.intent, case.policy_decision, case.observation, policy),
            (RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING,),
        )
    if binding == "conversion-direction":
        case = evaluator_case(
            settlement_currency=Currency.USDT,
            conversion_rate=Decimal("1"),
        )
        conversion = RuntimeRiskConversionRate(
            source_currency=Currency.USD,
            target_currency=Currency.USDT,
            rate=Decimal("1"),
            observed_at=NOW,
            provenance_id="fx-reversed",
        )
        observation = case.observation.model_copy(
            update={"conversion_rates": (conversion,)}
        )
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING,),
        )
    if binding == "market-absent":
        observation = case.observation.model_copy(update={"market_snapshots": ()})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.MARKET_DATA_STALE,),
        )
    if binding == "venue-absent":
        observation = case.observation.model_copy(update={"venue_health": ()})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.VENUE_UNHEALTHY,),
        )
    if binding == "venue-wrong":
        venue = RuntimeVenueHealthRecord(
            venue_id="ALT",
            health=RuntimeVenueHealth.HEALTHY,
            observed_at=NOW,
        )
        observation = case.observation.model_copy(update={"venue_health": (venue,)})
        return (
            EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
            (RuntimeRiskReasonCode.VENUE_UNHEALTHY,),
        )
    raise AssertionError(binding)


@pytest.mark.parametrize(
    "binding",
    [
        "intent-account",
        "policy-account",
        "intent-instrument",
        "instrument-spec",
        "spec-venue",
        "market-instrument",
        "market-currency",
        "order-price-currency",
        "policy-currency",
        "conversion-direction",
        "market-absent",
        "venue-absent",
        "venue-wrong",
    ],
)
def test_exact_identity_currency_and_missing_authority_bindings(binding: str) -> None:
    case, expected = _binding_case(binding)

    assert case.evaluate().reason_codes == expected


def test_strategy_identity_does_not_replace_another_strategys_position(
    case: EvaluatorCase,
) -> None:
    intent = case.intent.model_copy(update={"strategy_id": "strategy-2"})

    decision = case.evaluate(intent=intent)

    assert decision.reason_codes == (RuntimeRiskReasonCode.WITHIN_LIMITS,)
    assert decision.projected_position_quantity == Quantity(Decimal("1"), 3)
    assert decision.projected_strategy_gross == money("101")
    assert decision.projected_instrument_gross == money("301")


def _freshness_case(
    authority: str,
    age_seconds: int,
) -> tuple[EvaluatorCase, tuple[RuntimeRiskReasonCode, ...]]:
    observed_at = NOW - timedelta(seconds=age_seconds)
    expected_authority = (
        None
        if age_seconds == 10
        else {
            "portfolio": RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,
            "balance": RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,
            "position": RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,
            "mark": RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,
            "market": RuntimeRiskReasonCode.MARKET_DATA_STALE,
            "conversion": RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING,
            "venue": RuntimeRiskReasonCode.VENUE_UNHEALTHY,
        }[authority]
    )
    case = (
        evaluator_case(
            current_quantity="0",
            settlement_currency=Currency.USDT,
            conversion_rate=Decimal("1"),
        )
        if authority == "conversion"
        else evaluator_case(current_quantity="0")
        if authority in ("portfolio", "position")
        else evaluator_case()
    )
    observation = case.observation
    if authority == "market":
        market = observation.market_snapshots[0].model_copy(
            update={"observed_at": observed_at}
        )
        observation = observation.model_copy(update={"market_snapshots": (market,)})
    elif authority == "conversion":
        conversion = observation.conversion_rates[0].model_copy(
            update={"observed_at": observed_at}
        )
        observation = observation.model_copy(
            update={"conversion_rates": (conversion,)}
        )
    elif authority == "venue":
        venue = observation.venue_health[0].model_copy(
            update={"observed_at": observed_at}
        )
        observation = observation.model_copy(update={"venue_health": (venue,)})
    elif authority == "balance":
        balance = observation.portfolio.balances[0].model_copy(
            update={"observed_at": observed_at}
        )
        portfolio = observation.portfolio.model_copy(update={"balances": (balance,)})
        observation = observation.model_copy(update={"portfolio": portfolio})
    elif authority == "mark":
        position = observation.portfolio.positions[0]
        assert position.mark is not None
        mark = position.mark.model_copy(update={"marked_at": observed_at})
        position = position.model_copy(update={"mark": mark})
        portfolio = observation.portfolio.model_copy(
            update={"positions": (position,)}
        )
        observation = observation.model_copy(update={"portfolio": portfolio})
    elif authority == "position":
        zero_position = AccountPositionSnapshot(
            account_id="account-1",
            strategy_id="strategy-1",
            instrument=INSTRUMENT,
            settlement_currency=Currency.USD,
            quantity=Quantity(Decimal("0"), 3),
            mark=None,
            average_entry_price=None,
            realized_pnl=money("0"),
            unrealized_pnl=money("0"),
            fees=money("0"),
            funding=money("0"),
            observed_at=observed_at,
            schema_version="account-position-v1",
        )
        portfolio = observation.portfolio.model_copy(
            update={"positions": (zero_position,)}
        )
        observation = observation.model_copy(update={"portfolio": portfolio})
    elif authority == "portfolio":
        portfolio = observation.portfolio.model_copy(
            update={
                "balances": (),
                "positions": (),
                "observed_at": observed_at,
            }
        )
        observation = observation.model_copy(update={"portfolio": portfolio})
        expected = (RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,)
        if expected_authority is not None:
            expected = (
                expected_authority,
                RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,
            )
        return (
            EvaluatorCase(
                case.intent,
                case.policy_decision,
                observation,
                case.policy,
            ),
            expected,
        )
    else:
        raise AssertionError(authority)
    expected = (
        (RuntimeRiskReasonCode.WITHIN_LIMITS,)
        if expected_authority is None
        else (expected_authority,)
    )
    return (
        EvaluatorCase(case.intent, case.policy_decision, observation, case.policy),
        expected,
    )


@pytest.mark.parametrize(
    "authority",
    ["portfolio", "balance", "position", "mark", "market", "conversion", "venue"],
)
@pytest.mark.parametrize("age_seconds", [10, 11], ids=["exact-boundary", "stale-by-one"])
def test_each_authority_timestamp_has_an_independent_freshness_boundary(
    authority: str,
    age_seconds: int,
) -> None:
    case, expected = _freshness_case(authority, age_seconds)

    assert case.evaluate().reason_codes == expected


@pytest.mark.parametrize(
    ("cash", "expected"),
    [
        ("20.30", (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        ("20.29", (RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,)),
    ],
)
def test_available_cash_has_an_independent_exact_margin_buffer_boundary(
    case: EvaluatorCase,
    cash: str,
    expected: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    balance = case.observation.portfolio.balances[0].model_copy(
        update={"cash": money(cash)}
    )
    portfolio = case.observation.portfolio.model_copy(update={"balances": (balance,)})
    observation = case.observation.model_copy(update={"portfolio": portfolio})
    policy = case.policy.model_copy(update={"min_available_funds": money("10")})

    assert case.evaluate(observation=observation, policy=policy).reason_codes == expected


@pytest.mark.parametrize(
    ("margin_rate", "expected"),
    [
        ("0.2", (RuntimeRiskReasonCode.WITHIN_LIMITS,)),
        ("0.21", (RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT,)),
    ],
)
def test_initial_margin_rate_has_an_independent_available_funds_boundary(
    case: EvaluatorCase,
    margin_rate: str,
    expected: tuple[RuntimeRiskReasonCode, ...],
) -> None:
    balance = case.observation.portfolio.balances[0].model_copy(
        update={"cash": money("100")}
    )
    portfolio = case.observation.portfolio.model_copy(update={"balances": (balance,)})
    spec = case.observation.instrument_specs[0].model_copy(
        update={"initial_margin_rate": Decimal(margin_rate)}
    )
    observation = case.observation.model_copy(
        update={"portfolio": portfolio, "instrument_specs": (spec,)}
    )
    policy = case.policy.model_copy(update={"min_available_funds": money("79.40")})

    assert case.evaluate(observation=observation, policy=policy).reason_codes == expected


def test_identical_inputs_and_hostile_decimal_context_are_canonical(case: EvaluatorCase) -> None:
    expected = case.evaluate()
    expected_json = canonical_model_json(expected)

    with localcontext() as context:
        context.prec = 3
        actual = case.evaluate()

    assert canonical_model_json(actual) == expected_json
    assert actual == case.evaluate()


def test_hostile_decimal_context_preserves_large_exact_loss_boundary(
    case: EvaluatorCase,
) -> None:
    policy = case.policy.model_copy(update={"max_daily_loss": money("123456.78")})
    observation = case.observation.model_copy(update={"daily_pnl": money("-123456.78")})
    expected = case.evaluate(policy=policy, observation=observation)

    with localcontext() as context:
        context.prec = 3
        actual = case.evaluate(policy=policy, observation=observation)

    assert expected.outcome is RuntimeRiskOutcome.APPROVED
    assert actual == expected
