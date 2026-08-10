"""Pure conservative pricing and exact runtime order-risk projections."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from packages.domain.clock import require_utc
from packages.domain.orders import OrderIntent, OrderSide
from packages.domain.portfolio import (
    AccountPositionSnapshot,
    ExposureSnapshot,
)
from packages.domain.primitives import Currency, Money, Price, Quantity
from packages.domain.runtime_risk import (
    RuntimeInstrumentRiskSpec,
    RuntimeRiskMarketSnapshot,
    RuntimeRiskObservation,
    RuntimeRiskPolicy,
)

from .canonical import canonical_model_digest


class ProjectionError(ValueError):
    """Raised when an exact runtime-risk projection cannot be derived."""


class RuntimeRiskProjection(BaseModel):
    """Complete exact products used by the runtime-risk evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    risk_price: Price
    order_notional: Money
    projected_position_quantity: Quantity
    projected_pending: Money
    projected_gross: Money
    projected_net: Money
    projected_strategy_gross: Money
    projected_venue_gross: Money
    projected_instrument_gross: Money
    projected_margin_used: Money
    projected_available_funds: Money


_T = TypeVar("_T")


def _fraction(value: Decimal) -> Fraction:
    sign, digits, exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = (coefficient * 10) + digit
    if sign:
        coefficient = -coefficient
    if exponent >= 0:
        return Fraction(coefficient * (10**exponent), 1)
    return Fraction(coefficient, 10 ** (-exponent))


def _decimal(value: Fraction, *, field: str) -> Decimal:
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ProjectionError(f"{field} cannot be represented exactly as a Decimal")
    scale = max(twos, fives)
    coefficient = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = 1 if coefficient < 0 else 0
    digits = tuple(int(character) for character in str(abs(coefficient))) or (0,)
    return Decimal((sign, digits, -scale))


def _sum(*values: Decimal, field: str) -> Decimal:
    return _decimal(
        sum((_fraction(value) for value in values), Fraction(0)),
        field=field,
    )


def _product(*values: Decimal, field: str) -> Decimal:
    result = Fraction(1)
    for value in values:
        result *= _fraction(value)
    return _decimal(result, field=field)


def _money(amount: Decimal, currency: Currency, *, field: str) -> Money:
    sign, raw_digits, raw_exponent = amount.as_tuple()
    digits = raw_digits
    exponent = int(raw_exponent)
    minimum_exponent = -currency.precision
    while exponent < minimum_exponent and len(digits) > 1 and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    normalized = Decimal((sign, digits, exponent))
    try:
        return Money(normalized, currency)
    except ValueError as exc:
        raise ProjectionError(f"{field} is not exact at reporting currency precision") from exc


def _quantity(amount: Decimal, precision: int) -> Quantity:
    try:
        return Quantity(amount, precision)
    except ValueError as exc:
        raise ProjectionError("projected position quantity is not exact") from exc


def _validated_digest(value: BaseModel, *, field: str) -> None:
    try:
        canonical_model_digest(value)
    except (TypeError, ValueError) as exc:
        raise ProjectionError(f"{field} is invalid") from exc


def _one_by_key(
    values: tuple[_T, ...],
    key: object,
    *,
    key_of: Callable[[_T], object],
    field: str,
) -> _T:
    matches = tuple(value for value in values if key_of(value) == key)
    if len(matches) != 1:
        raise ProjectionError(f"exactly one {field} is required")
    return matches[0]


def _risk_price(
    intent: OrderIntent,
    market: RuntimeRiskMarketSnapshot,
    spec: RuntimeInstrumentRiskSpec,
) -> Price:
    executable = market.ask if intent.side is OrderSide.BUY else market.bid
    candidates = [executable]
    if intent.limit_price is not None:
        candidates.append(intent.limit_price)
    if intent.trigger_price is not None:
        candidates.append(intent.trigger_price)
    if any(price.currency is not spec.settlement_currency for price in candidates):
        raise ProjectionError("order and market prices must use settlement currency")
    return max(candidates, key=lambda price: price.amount)


def _conversion_rate(
    source: Currency,
    target: Currency,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    *,
    decided_at: datetime,
) -> Decimal:
    if source is target:
        return Decimal(1)
    try:
        conversion = _one_by_key(
            observation.conversion_rates,
            (source.code, target.code),
            key_of=lambda item: (
                item.source_currency.code,
                item.target_currency.code,
            ),
            field="conversion rate",
        )
    except ProjectionError as exc:
        raise ProjectionError("exact source/target conversion rate is required") from exc
    if conversion.source_currency is not source or conversion.target_currency is not target:
        raise ProjectionError("exact source/target conversion rate is required")
    maximum_age = timedelta(seconds=policy.market_data_max_age_seconds)
    if conversion.observed_at > decided_at or decided_at - conversion.observed_at > maximum_age:
        raise ProjectionError("conversion rate is stale")
    return conversion.rate


def _reporting_notional(
    quantity: Decimal,
    price: Price,
    rate: Decimal,
    *,
    field: str,
) -> Decimal:
    return _product(quantity, price.amount, rate, field=field)


def _add_partition_contribution(
    amounts: dict[object, tuple[Decimal, Decimal]],
    key: object,
    signed_notional: Decimal,
    *,
    field: str,
) -> None:
    gross, net = amounts.get(key, (Decimal(0), Decimal(0)))
    amounts[key] = (
        _sum(gross, signed_notional.copy_abs(), field=f"{field} gross"),
        _sum(net, signed_notional, field=f"{field} net"),
    )


def _position_contributions(
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    *,
    decided_at: datetime,
) -> tuple[
    dict[str, tuple[Decimal, Decimal]],
    dict[str, tuple[Decimal, Decimal]],
    dict[str, tuple[Decimal, Decimal]],
]:
    reporting = observation.portfolio.reporting_currency
    instruments: dict[str, tuple[Decimal, Decimal]] = {}
    strategies: dict[str, tuple[Decimal, Decimal]] = {}
    venues: dict[str, tuple[Decimal, Decimal]] = {}
    for position in observation.portfolio.positions:
        if position.quantity.value.is_zero():
            continue
        if position.mark is None:
            raise ProjectionError("non-zero position requires a marked contribution")
        rate = _conversion_rate(
            position.settlement_currency,
            reporting,
            observation,
            policy,
            decided_at=decided_at,
        )
        signed = _reporting_notional(
            position.quantity.value,
            position.mark.price,
            rate,
            field="current marked position notional",
        )
        _add_partition_contribution(
            instruments,
            position.instrument.canonical,
            signed,
            field="instrument exposure",
        )
        _add_partition_contribution(
            strategies,
            position.strategy_id,
            signed,
            field="strategy exposure",
        )
        _add_partition_contribution(
            venues,
            position.instrument.venue,
            signed,
            field="venue exposure",
        )
    return instruments, strategies, venues


def _assert_exposure(
    actual: ExposureSnapshot,
    expected: tuple[Decimal, Decimal],
    reporting: Currency,
    *,
    field: str,
) -> None:
    if actual.currency is not reporting:
        raise ProjectionError(f"{field} partition currency is inconsistent")
    if actual.gross.amount != expected[0] or actual.net.amount != expected[1]:
        raise ProjectionError(f"{field} partition is inconsistent")


def _verify_partitions(
    observation: RuntimeRiskObservation,
    instrument_amounts: dict[str, tuple[Decimal, Decimal]],
    strategy_amounts: dict[str, tuple[Decimal, Decimal]],
    venue_amounts: dict[str, tuple[Decimal, Decimal]],
) -> None:
    portfolio = observation.portfolio
    reporting = portfolio.reporting_currency
    total_gross = _sum(
        *(gross for gross, _ in instrument_amounts.values()),
        field="recomputed total gross",
    )
    total_net = _sum(
        *(net for _, net in instrument_amounts.values()),
        field="recomputed total net",
    )
    _assert_exposure(
        portfolio.total_exposure,
        (total_gross, total_net),
        reporting,
        field="total exposure",
    )

    collections = (
        (
            {
                item.instrument.canonical: item.exposure
                for item in portfolio.instrument_exposures
            },
            instrument_amounts,
            "instrument",
        ),
        (
            {item.strategy_id: item.exposure for item in portfolio.strategy_exposures},
            strategy_amounts,
            "strategy",
        ),
        (
            {item.venue_id: item.exposure for item in portfolio.venue_exposures},
            venue_amounts,
            "venue",
        ),
    )
    for actual, expected, field in collections:
        missing = set(expected).difference(actual)
        if missing:
            raise ProjectionError(f"{field} partition is missing for non-zero position")
        for key, value in actual.items():
            _assert_exposure(
                value,
                expected.get(key, (Decimal(0), Decimal(0))),
                reporting,
                field=field,
            )
        pending = _sum(
            *(value.pending.amount for value in actual.values()),
            field=f"{field} pending exposure",
        )
        if pending != portfolio.total_exposure.pending.amount:
            raise ProjectionError(
                f"{field} pending partitions are inconsistent with total pending"
            )


def _matching_position(
    intent: OrderIntent,
    observation: RuntimeRiskObservation,
    spec: RuntimeInstrumentRiskSpec,
) -> AccountPositionSnapshot | None:
    matches = tuple(
        position
        for position in observation.portfolio.positions
        if position.strategy_id == intent.strategy_id
        and position.instrument.canonical == intent.instrument.canonical
    )
    if len(matches) > 1:
        raise ProjectionError("matching position key is not unique")
    if not matches:
        return None
    position = matches[0]
    if position.instrument != intent.instrument:
        raise ProjectionError("position instrument does not exactly match order instrument")
    if position.settlement_currency is not spec.settlement_currency:
        raise ProjectionError("position currency does not match instrument settlement currency")
    return position


def _partition_gross(
    amounts: dict[str, tuple[Decimal, Decimal]],
    key: str,
) -> Decimal:
    return amounts.get(key, (Decimal(0), Decimal(0)))[0]


def _replace_gross(
    current_gross: Decimal,
    current_position_notional: Decimal,
    projected_position_notional: Decimal,
    *,
    field: str,
) -> Decimal:
    return _sum(
        current_gross,
        current_position_notional.copy_abs().copy_negate(),
        projected_position_notional.copy_abs(),
        field=field,
    )


def project_runtime_order(
    intent: OrderIntent,
    observation: RuntimeRiskObservation,
    policy: RuntimeRiskPolicy,
    *,
    decided_at: datetime,
) -> RuntimeRiskProjection:
    """Derive conservative price and exact replacement portfolio projections."""

    for value, expected_type, field in (
        (intent, OrderIntent, "intent"),
        (observation, RuntimeRiskObservation, "observation"),
        (policy, RuntimeRiskPolicy, "policy"),
    ):
        if type(value) is not expected_type:
            raise ProjectionError(f"{field} is invalid")
        _validated_digest(value, field=field)
    try:
        require_utc(decided_at)
    except ValueError as exc:
        raise ProjectionError("decided_at must be UTC") from exc

    portfolio = observation.portfolio
    reporting = portfolio.reporting_currency
    if not (
        intent.account_id
        == policy.account_id
        == portfolio.account_id
    ):
        raise ProjectionError("order, policy, and portfolio accounts must match")
    policy_money = (
        policy.max_pending_exposure,
        policy.max_gross_exposure,
        policy.max_abs_net_exposure,
        policy.max_strategy_exposure,
        policy.max_venue_exposure,
        policy.min_available_funds,
        policy.max_daily_loss,
        policy.max_drawdown,
    )
    if any(value.currency is not reporting for value in policy_money):
        raise ProjectionError("policy currency must match portfolio reporting currency")

    spec = _one_by_key(
        observation.instrument_specs,
        intent.instrument.canonical,
        key_of=lambda item: item.instrument.canonical,
        field="instrument risk spec",
    )
    market = _one_by_key(
        observation.market_snapshots,
        intent.instrument.canonical,
        key_of=lambda item: item.instrument.canonical,
        field="market snapshot",
    )
    if spec.instrument != intent.instrument or market.instrument != intent.instrument:
        raise ProjectionError("instrument authority does not exactly match order instrument")
    if spec.venue_id != intent.instrument.venue:
        raise ProjectionError("instrument venue authority does not match order venue")
    venue = _one_by_key(
        observation.venue_health,
        spec.venue_id,
        key_of=lambda item: item.venue_id,
        field="venue health record",
    )
    if venue.venue_id != spec.venue_id:
        raise ProjectionError("venue authority does not exactly match order venue")
    if any(
        price.currency is not spec.settlement_currency
        for price in (market.bid, market.ask, market.last)
    ):
        raise ProjectionError("market currency does not match instrument settlement currency")

    reporting_balance = _one_by_key(
        portfolio.balances,
        reporting.code,
        key_of=lambda item: item.currency.code,
        field="reporting balance",
    )
    if reporting_balance.currency is not reporting:
        raise ProjectionError("reporting balance currency is inconsistent")

    rate = _conversion_rate(
        spec.settlement_currency,
        reporting,
        observation,
        policy,
        decided_at=decided_at,
    )
    risk_price = _risk_price(intent, market, spec)
    order_notional_amount = _reporting_notional(
        intent.quantity.value,
        risk_price,
        rate,
        field="order notional",
    ).copy_abs()

    instrument_amounts, strategy_amounts, venue_amounts = _position_contributions(
        observation,
        policy,
        decided_at=decided_at,
    )
    _verify_partitions(
        observation,
        instrument_amounts,
        strategy_amounts,
        venue_amounts,
    )

    current_position = _matching_position(intent, observation, spec)
    current_quantity = (
        Decimal(0) if current_position is None else current_position.quantity.value
    )
    signed_order_quantity = (
        intent.quantity.value
        if intent.side is OrderSide.BUY
        else intent.quantity.value.copy_negate()
    )
    projected_quantity_amount = _sum(
        current_quantity,
        signed_order_quantity,
        field="projected position quantity",
    )
    projected_quantity_precision = intent.quantity.precision
    if current_position is not None:
        projected_quantity_precision = max(
            projected_quantity_precision,
            current_position.quantity.precision,
        )
    projected_quantity = _quantity(
        projected_quantity_amount,
        projected_quantity_precision,
    )

    current_position_notional = Decimal(0)
    if current_position is not None and not current_position.quantity.value.is_zero():
        if current_position.mark is None:
            raise ProjectionError("non-zero matching position requires a mark")
        current_position_notional = _reporting_notional(
            current_position.quantity.value,
            current_position.mark.price,
            rate,
            field="matching current position notional",
        )
    projected_position_notional = _reporting_notional(
        projected_quantity_amount,
        risk_price,
        rate,
        field="projected position notional",
    )

    projected_pending_amount = _sum(
        portfolio.total_exposure.pending.amount,
        order_notional_amount,
        field="projected pending exposure",
    )
    projected_gross_amount = _replace_gross(
        portfolio.total_exposure.gross.amount,
        current_position_notional,
        projected_position_notional,
        field="projected gross exposure",
    )
    projected_net_amount = _sum(
        portfolio.total_exposure.net.amount,
        current_position_notional.copy_negate(),
        projected_position_notional,
        field="projected net exposure",
    )
    projected_strategy_gross_amount = _replace_gross(
        _partition_gross(strategy_amounts, intent.strategy_id),
        current_position_notional,
        projected_position_notional,
        field="projected strategy gross exposure",
    )
    projected_venue_gross_amount = _replace_gross(
        _partition_gross(venue_amounts, spec.venue_id),
        current_position_notional,
        projected_position_notional,
        field="projected venue gross exposure",
    )
    projected_instrument_gross_amount = _replace_gross(
        _partition_gross(instrument_amounts, intent.instrument.canonical),
        current_position_notional,
        projected_position_notional,
        field="projected instrument gross exposure",
    )

    risk_increase = _sum(
        projected_position_notional.copy_abs(),
        current_position_notional.copy_abs().copy_negate(),
        field="risk-increasing notional",
    )
    if risk_increase < 0:
        risk_increase = Decimal(0)
    margin_increase = _product(
        risk_increase,
        spec.initial_margin_rate,
        field="margin increase",
    )
    projected_margin_amount = _sum(
        reporting_balance.margin_used.amount,
        margin_increase,
        field="projected margin used",
    )
    projected_available_amount = _sum(
        reporting_balance.cash.amount,
        reporting_balance.locked_funds.amount.copy_negate(),
        projected_margin_amount.copy_negate(),
        field="projected available funds",
    )

    return RuntimeRiskProjection(
        risk_price=risk_price,
        order_notional=_money(order_notional_amount, reporting, field="order notional"),
        projected_position_quantity=projected_quantity,
        projected_pending=_money(
            projected_pending_amount,
            reporting,
            field="projected pending exposure",
        ),
        projected_gross=_money(
            projected_gross_amount,
            reporting,
            field="projected gross exposure",
        ),
        projected_net=_money(
            projected_net_amount,
            reporting,
            field="projected net exposure",
        ),
        projected_strategy_gross=_money(
            projected_strategy_gross_amount,
            reporting,
            field="projected strategy gross exposure",
        ),
        projected_venue_gross=_money(
            projected_venue_gross_amount,
            reporting,
            field="projected venue gross exposure",
        ),
        projected_instrument_gross=_money(
            projected_instrument_gross_amount,
            reporting,
            field="projected instrument gross exposure",
        ),
        projected_margin_used=_money(
            projected_margin_amount,
            reporting,
            field="projected margin used",
        ),
        projected_available_funds=_money(
            projected_available_amount,
            reporting,
            field="projected available funds",
        ),
    )
