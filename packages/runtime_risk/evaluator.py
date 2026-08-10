"""Pure fixed-order evaluation of runtime order-risk policy."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

from pydantic import BaseModel

from packages.domain.clock import require_utc
from packages.domain.orders import OrderIntent, OrderSide
from packages.domain.portfolio import AccountPortfolioSnapshot
from packages.domain.risk import RiskDecision, RiskOutcome
from packages.domain.runtime_risk import (
    RuntimeInstrumentRiskSpec,
    RuntimeOrderRiskDecision,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskObservation,
    RuntimeRiskOutcome,
    RuntimeRiskPolicy,
    RuntimeRiskReasonCode,
    RuntimeVenueHealth,
)

from .canonical import canonical_model_digest
from .projections import ProjectionError, RuntimeRiskProjection, project_runtime_order


_POLICY_MONEY_FIELDS = (
    "max_pending_exposure",
    "max_gross_exposure",
    "max_abs_net_exposure",
    "max_strategy_exposure",
    "max_venue_exposure",
    "min_available_funds",
    "max_daily_loss",
    "max_drawdown",
)


def _validated_digest(value: object, expected_type: type[BaseModel], field: str) -> str:
    if type(value) is not expected_type:
        raise ValueError(f"{field} cannot be canonically represented")
    try:
        return canonical_model_digest(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} cannot be canonically represented") from exc


def _fraction(value: Decimal) -> Fraction:
    sign, digits, raw_exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = (coefficient * 10) + digit
    if sign:
        coefficient = -coefficient
    exponent = int(raw_exponent)
    if exponent >= 0:
        return Fraction(coefficient * (10**exponent), 1)
    return Fraction(coefficient, 10 ** (-exponent))


def _multiple(value: Decimal, increment: Decimal) -> bool:
    return (_fraction(value) / _fraction(increment)).denominator == 1


def _age_is_valid(observed_at: datetime, decided_at: datetime, seconds: int) -> bool:
    return observed_at <= decided_at and decided_at - observed_at <= timedelta(
        seconds=seconds
    )


def _instrument_authority(
    intent: OrderIntent, observation: RuntimeRiskObservation
) -> RuntimeInstrumentRiskSpec | None:
    matches = tuple(
        item
        for item in observation.instrument_specs
        if item.instrument.canonical == intent.instrument.canonical
    )
    if len(matches) != 1:
        return None
    spec = matches[0]
    if spec.instrument != intent.instrument or spec.venue_id != intent.instrument.venue:
        return None
    return spec


def _market_authority(
    intent: OrderIntent,
    spec: RuntimeInstrumentRiskSpec | None,
    observation: RuntimeRiskObservation,
) -> RuntimeRiskMarketSnapshot | None:
    if spec is None:
        return None
    matches = tuple(
        item
        for item in observation.market_snapshots
        if item.instrument.canonical == intent.instrument.canonical
    )
    if len(matches) != 1:
        return None
    market = matches[0]
    if market.instrument != intent.instrument:
        return None
    if any(
        price.currency is not spec.settlement_currency
        for price in (market.bid, market.ask, market.last)
    ):
        return None
    return market


def _conversion_rate(
    source_currency: object,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
) -> Decimal | None:
    reporting = observation.portfolio.reporting_currency
    if source_currency is reporting:
        return Decimal(1)
    matches = tuple(
        item
        for item in observation.conversion_rates
        if item.source_currency is source_currency
        and item.target_currency is reporting
    )
    if len(matches) != 1:
        return None
    conversion = matches[0]
    if not _age_is_valid(
        conversion.observed_at,
        decided_at,
        policy.market_data_max_age_seconds,
    ) or conversion.observed_at > observation.observed_at:
        return None
    return conversion.rate


def _valuation_authority_valid(
    spec: RuntimeInstrumentRiskSpec | None,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
) -> bool:
    if spec is None:
        return False
    reporting = observation.portfolio.reporting_currency
    if any(
        getattr(policy, name).currency is not reporting
        for name in _POLICY_MONEY_FIELDS
    ):
        return False
    currencies = {spec.settlement_currency}
    currencies.update(
        position.settlement_currency
        for position in observation.portfolio.positions
        if not position.quantity.value.is_zero()
    )
    return all(
        _conversion_rate(currency, observation, policy, decided_at) is not None
        for currency in currencies
    )


def _position_amounts(
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
) -> tuple[
    dict[str, tuple[Fraction, Fraction]],
    dict[str, tuple[Fraction, Fraction]],
    dict[str, tuple[Fraction, Fraction]],
] | None:
    instruments: dict[str, tuple[Fraction, Fraction]] = {}
    strategies: dict[str, tuple[Fraction, Fraction]] = {}
    venues: dict[str, tuple[Fraction, Fraction]] = {}
    for position in observation.portfolio.positions:
        if position.quantity.value.is_zero():
            continue
        if position.mark is None:
            return None
        rate = _conversion_rate(
            position.settlement_currency, observation, policy, decided_at
        )
        if rate is None:
            return None
        signed = (
            _fraction(position.quantity.value)
            * _fraction(position.mark.price.amount)
            * _fraction(rate)
        )
        for mapping, key in (
            (instruments, position.instrument.canonical),
            (strategies, position.strategy_id),
            (venues, position.instrument.venue),
        ):
            gross, net = mapping.get(key, (Fraction(0), Fraction(0)))
            mapping[key] = (gross + abs(signed), net + signed)
    return instruments, strategies, venues


def _partitions_are_consistent(
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
) -> bool:
    amounts = _position_amounts(observation, policy, decided_at)
    if amounts is None:
        return False
    instrument_amounts, strategy_amounts, venue_amounts = amounts
    portfolio = observation.portfolio
    reporting = portfolio.reporting_currency
    if portfolio.total_exposure.currency is not reporting:
        return False
    if portfolio.observed_at > observation.observed_at:
        return False
    if _fraction(portfolio.total_exposure.gross.amount) != sum(
        (gross for gross, _ in instrument_amounts.values()), Fraction(0)
    ):
        return False
    if _fraction(portfolio.total_exposure.net.amount) != sum(
        (net for _, net in instrument_amounts.values()), Fraction(0)
    ):
        return False
    collections = (
        (
            {
                item.instrument.canonical: item.exposure
                for item in portfolio.instrument_exposures
            },
            instrument_amounts,
        ),
        (
            {item.strategy_id: item.exposure for item in portfolio.strategy_exposures},
            strategy_amounts,
        ),
        (
            {item.venue_id: item.exposure for item in portfolio.venue_exposures},
            venue_amounts,
        ),
    )
    total_pending = _fraction(portfolio.total_exposure.pending.amount)
    for actual, expected in collections:
        if set(expected).difference(actual):
            return False
        for key, exposure in actual.items():
            expected_gross, expected_net = expected.get(
                key, (Fraction(0), Fraction(0))
            )
            if exposure.currency is not reporting:
                return False
            if _fraction(exposure.gross.amount) != expected_gross:
                return False
            if _fraction(exposure.net.amount) != expected_net:
                return False
        if sum(
            (_fraction(exposure.pending.amount) for exposure in actual.values()),
            Fraction(0),
        ) != total_pending:
            return False
    return True


def _portfolio_authority_valid(
    intent: OrderIntent,
    spec: RuntimeInstrumentRiskSpec | None,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
    valuation_valid: bool,
) -> bool:
    portfolio = observation.portfolio
    if not (
        intent.account_id == policy.account_id == portfolio.account_id
        and valuation_valid
    ):
        return False
    maximum_age = policy.portfolio_max_age_seconds
    timestamps = [portfolio.observed_at]
    timestamps.extend(balance.observed_at for balance in portfolio.balances)
    timestamps.extend(position.observed_at for position in portfolio.positions)
    timestamps.extend(
        position.mark.marked_at
        for position in portfolio.positions
        if position.mark is not None
    )
    if any(not _age_is_valid(value, decided_at, maximum_age) for value in timestamps):
        return False
    if spec is not None:
        matching = tuple(
            position
            for position in portfolio.positions
            if position.strategy_id == intent.strategy_id
            and position.instrument.canonical == intent.instrument.canonical
        )
        if len(matching) > 1:
            return False
        if matching and (
            matching[0].instrument != intent.instrument
            or matching[0].settlement_currency is not spec.settlement_currency
        ):
            return False
    return _partitions_are_consistent(observation, policy, decided_at)


def _venue_is_healthy(
    intent: OrderIntent,
    spec: RuntimeInstrumentRiskSpec | None,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
) -> bool:
    venue_id = intent.instrument.venue if spec is None else spec.venue_id
    matches = tuple(
        item for item in observation.venue_health if item.venue_id == venue_id
    )
    return (
        len(matches) == 1
        and matches[0].observed_at <= observation.observed_at
        and _age_is_valid(
            matches[0].observed_at,
            decided_at,
            policy.market_data_max_age_seconds,
        )
        and matches[0].health is RuntimeVenueHealth.HEALTHY
    )


def _reduce_only_is_valid(
    intent: OrderIntent,
    observation: RuntimeRiskObservation,
    portfolio_valid: bool,
) -> bool:
    if not intent.reduce_only:
        return True
    if not portfolio_valid:
        return True
    matches = tuple(
        position
        for position in observation.portfolio.positions
        if position.strategy_id == intent.strategy_id
        and position.instrument.canonical == intent.instrument.canonical
    )
    if len(matches) != 1:
        return False
    current = _fraction(matches[0].quantity.value)
    signed_order = _fraction(intent.quantity.value)
    if intent.side is OrderSide.SELL:
        signed_order = -signed_order
    projected = current + signed_order
    if current > 0:
        return Fraction(0) <= projected < current
    if current < 0:
        return current < projected <= Fraction(0)
    return False


def evaluate_runtime_order_risk(
    *,
    decision_id: UUID,
    intent: OrderIntent,
    policy_decision: RiskDecision,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    decided_at: datetime,
) -> RuntimeOrderRiskDecision:
    """Evaluate one order against explicit immutable runtime-risk authority."""

    if type(decision_id) is not UUID:
        raise ValueError("decision_id must be a UUID")
    require_utc(decided_at)
    intent_digest = _validated_digest(intent, OrderIntent, "intent")
    policy_decision_digest = _validated_digest(
        policy_decision, RiskDecision, "policy_decision"
    )
    observation_digest = _validated_digest(
        observation, RuntimeRiskObservation, "observation"
    )
    policy_digest = _validated_digest(policy, RuntimeRiskPolicy, "policy")
    portfolio_digest = _validated_digest(
        observation.portfolio,
        AccountPortfolioSnapshot,
        "portfolio",
    )
    if not (
        policy_decision.decided_at <= intent.requested_at <= decided_at
        and observation.observed_at <= decided_at
    ):
        raise ValueError("runtime-risk inputs are not in valid UTC time order")

    spec = _instrument_authority(intent, observation)
    market = _market_authority(intent, spec, observation)
    market_fresh = market is not None and _age_is_valid(
        market.observed_at,
        decided_at,
        policy.market_data_max_age_seconds,
    ) and market.observed_at <= observation.observed_at
    valuation_valid = _valuation_authority_valid(
        spec, observation, policy, decided_at
    )
    portfolio_valid = _portfolio_authority_valid(
        intent,
        spec,
        observation,
        policy,
        decided_at,
        valuation_valid,
    )
    venue_healthy = _venue_is_healthy(
        intent, spec, observation, policy, decided_at
    )
    reporting = observation.portfolio.reporting_currency
    reporting_balances = tuple(
        balance
        for balance in observation.portfolio.balances
        if balance.currency is reporting
    )
    balance_valid = len(reporting_balances) == 1

    projection: RuntimeRiskProjection | None = None
    projection_failed = False
    if (
        spec is not None
        and market is not None
        and valuation_valid
        and portfolio_valid
        and balance_valid
        and tuple(
            item for item in observation.venue_health if item.venue_id == spec.venue_id
        )
    ):
        try:
            projection = project_runtime_order(
                intent, observation, policy, decided_at=decided_at
            )
        except ProjectionError:
            projection_failed = True

    policy_approved = (
        intent.risk_decision_id == policy_decision.decision_id
        and policy_decision.outcome in (RiskOutcome.APPROVED, RiskOutcome.MODIFIED)
        and policy_decision.approved_target is not None
    )
    order_prices = tuple(
        price
        for price in (
            intent.limit_price,
            intent.trigger_price,
            intent.trailing_offset,
        )
        if price is not None
    )
    price_precision_valid = spec is None or all(
        price.currency is spec.settlement_currency
        and _multiple(price.amount, spec.price_increment.amount)
        for price in order_prices
    )
    quantity_precision_valid = spec is None or _multiple(
        intent.quantity.value, spec.quantity_increment.value
    )
    quantity_bounds_valid = spec is None or (
        spec.min_quantity.value
        <= intent.quantity.value
        <= spec.max_quantity.value
    )
    notional_bounds_valid = True
    if spec is not None and projection is not None:
        settlement_notional = (
            _fraction(intent.quantity.value) * _fraction(projection.risk_price.amount)
        )
        notional_bounds_valid = (
            _fraction(spec.min_order_notional.amount)
            <= settlement_notional
            <= _fraction(spec.max_order_notional.amount)
        )
    balance_margin_failed = not balance_valid or (
        projection is not None
        and (
            projection.projected_available_funds.currency is not reporting
            or projection.projected_available_funds.amount
            < policy.min_available_funds.amount
        )
    )
    daily_loss_valid = _fraction(observation.daily_pnl.amount) >= -_fraction(
        policy.max_daily_loss.amount
    )
    drawdown = max(
        _fraction(observation.peak_equity.amount)
        - _fraction(observation.current_equity.amount),
        Fraction(0),
    )
    drawdown_valid = drawdown <= _fraction(policy.max_drawdown.amount)
    command_window_valid = _age_is_valid(
        observation.command_window_started_at,
        decided_at,
        policy.command_window_seconds,
    )
    command_rate_valid = (
        command_window_valid
        and observation.commands_in_window + 1 <= policy.max_commands_per_window
    )
    duplicate = any(
        prior.intent_id == intent.intent_id
        or prior.client_order_id == intent.client_order_id
        for prior in observation.prior_commands
    )

    checks = (
        (RuntimeRiskReasonCode.POLICY_RISK_NOT_APPROVED, not policy_approved),
        (RuntimeRiskReasonCode.ENGINE_NOT_READY, not observation.engine_ready),
        (RuntimeRiskReasonCode.INSTRUMENT_UNKNOWN, spec is None),
        (
            RuntimeRiskReasonCode.MARKET_DATA_STALE,
            spec is not None and not market_fresh,
        ),
        (
            RuntimeRiskReasonCode.VALUATION_AUTHORITY_MISSING,
            spec is not None and (not valuation_valid or projection_failed),
        ),
        (
            RuntimeRiskReasonCode.PORTFOLIO_STATE_INVALID,
            spec is not None and valuation_valid and not portfolio_valid,
        ),
        (
            RuntimeRiskReasonCode.PRICE_PRECISION_INVALID,
            spec is not None and not price_precision_valid,
        ),
        (
            RuntimeRiskReasonCode.QUANTITY_PRECISION_INVALID,
            spec is not None and not quantity_precision_valid,
        ),
        (
            RuntimeRiskReasonCode.QUANTITY_OUT_OF_BOUNDS,
            spec is not None and not quantity_bounds_valid,
        ),
        (
            RuntimeRiskReasonCode.ORDER_NOTIONAL_LIMIT,
            projection is not None and not notional_bounds_valid,
        ),
        (RuntimeRiskReasonCode.BALANCE_MARGIN_LIMIT, balance_margin_failed),
        (
            RuntimeRiskReasonCode.PENDING_EXPOSURE_LIMIT,
            projection is not None
            and projection.projected_pending.amount
            > policy.max_pending_exposure.amount,
        ),
        (
            RuntimeRiskReasonCode.GROSS_EXPOSURE_LIMIT,
            projection is not None
            and projection.projected_gross.amount > policy.max_gross_exposure.amount,
        ),
        (
            RuntimeRiskReasonCode.NET_EXPOSURE_LIMIT,
            projection is not None
            and abs(_fraction(projection.projected_net.amount))
            > _fraction(policy.max_abs_net_exposure.amount),
        ),
        (
            RuntimeRiskReasonCode.STRATEGY_EXPOSURE_LIMIT,
            projection is not None
            and projection.projected_strategy_gross.amount
            > policy.max_strategy_exposure.amount,
        ),
        (
            RuntimeRiskReasonCode.VENUE_EXPOSURE_LIMIT,
            projection is not None
            and projection.projected_venue_gross.amount
            > policy.max_venue_exposure.amount,
        ),
        (RuntimeRiskReasonCode.DAILY_LOSS_LIMIT, not daily_loss_valid),
        (RuntimeRiskReasonCode.DRAWDOWN_LIMIT, not drawdown_valid),
        (
            RuntimeRiskReasonCode.REDUCE_ONLY_VIOLATION,
            not _reduce_only_is_valid(intent, observation, portfolio_valid),
        ),
        (RuntimeRiskReasonCode.COMMAND_RATE_LIMIT, not command_rate_valid),
        (RuntimeRiskReasonCode.VENUE_UNHEALTHY, not venue_healthy),
        (RuntimeRiskReasonCode.DUPLICATE_COMMAND, duplicate),
    )
    reasons = tuple(reason for reason, failed in checks if failed)
    outcome = RuntimeRiskOutcome.REJECTED
    if not reasons:
        reasons = (RuntimeRiskReasonCode.WITHIN_LIMITS,)
        outcome = RuntimeRiskOutcome.APPROVED
    projection_values = (
        {}
        if projection is None
        else {
            name: getattr(projection, name)
            for name in type(projection).model_fields
        }
    )
    return RuntimeOrderRiskDecision(
        decision_id=decision_id,
        intent_id=intent.intent_id,
        risk_decision_id=intent.risk_decision_id,
        intent_digest=intent_digest,
        policy_risk_decision_digest=policy_decision_digest,
        portfolio_snapshot_id=observation.portfolio.snapshot_id,
        portfolio_digest=portfolio_digest,
        observation_id=observation.observation_id,
        observation_version=observation.state_version,
        observation_digest=observation_digest,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_digest=policy_digest,
        risk_price=projection_values.get("risk_price"),
        order_notional=projection_values.get("order_notional"),
        projected_position_quantity=projection_values.get(
            "projected_position_quantity"
        ),
        projected_pending=projection_values.get("projected_pending"),
        projected_gross=projection_values.get("projected_gross"),
        projected_net=projection_values.get("projected_net"),
        projected_strategy_gross=projection_values.get("projected_strategy_gross"),
        projected_venue_gross=projection_values.get("projected_venue_gross"),
        projected_instrument_gross=projection_values.get(
            "projected_instrument_gross"
        ),
        projected_margin_used=projection_values.get("projected_margin_used"),
        projected_available_funds=projection_values.get(
            "projected_available_funds"
        ),
        outcome=outcome,
        reason_codes=reasons,
        decided_at=decided_at,
        schema_version="runtime-order-risk-decision-v1",
    )
