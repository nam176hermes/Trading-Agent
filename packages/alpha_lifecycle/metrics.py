"""Deterministic Decimal performance metrics for frozen P3 baselines and alphas."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .baselines import DailyCloseV1
from .protocol import PerformanceMetricsV1


_Q = Decimal("0.000000000001")


class CostModelV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    fee_bps: int = Field(ge=0, le=100_000)
    spread_bps: int = Field(ge=0, le=100_000)
    slippage_bps: int = Field(ge=0, le=100_000)
    funding_bps: int = Field(ge=0, le=100_000)
    borrow_bps: int = Field(ge=0, le=100_000)


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_EVEN)


def _rolling_worst(returns: tuple[Decimal, ...], width: int) -> Decimal:
    if not returns:
        return Decimal(0)
    if len(returns) < width:
        width = len(returns)
    return min(
        _compound(returns[index : index + width])
        for index in range(len(returns) - width + 1)
    )


def _compound(returns: tuple[Decimal, ...]) -> Decimal:
    value = Decimal(1)
    for item in returns:
        value *= Decimal(1) + item
    return value - 1


def _holding_days(weights: tuple[Decimal, ...]) -> Decimal | None:
    runs: list[int] = []
    current = 0
    for weight in weights:
        if weight == 1:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return None if not runs else _q(Decimal(sum(runs)) / Decimal(len(runs)))


def calculate_performance_metrics(
    rows: tuple[DailyCloseV1, ...],
    weights: tuple[Decimal, ...],
    costs: CostModelV1,
) -> PerformanceMetricsV1:
    """Apply prior-close weights to next-close returns with explicit round-trip costs."""

    values = tuple(DailyCloseV1.model_validate(row) for row in rows)
    model = CostModelV1.model_validate(costs)
    if len(values) < 2 or len(weights) != len(values):
        raise ValueError("metrics require matching daily closes and weights")
    if any(weight not in {Decimal(0), Decimal(1)} for weight in weights):
        raise ValueError("P3 foundation metrics accept only long/flat weights")
    if len({row.instrument for row in values}) != 1:
        raise ValueError("metric closes must contain a single instrument")
    if any(
        current.closed_at <= previous.closed_at
        for previous, current in zip(values, values[1:])
    ):
        raise ValueError("metric closes must be strictly ordered")

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        fee_rate = Decimal(model.fee_bps) / Decimal(10_000)
        spread_rate = Decimal(model.spread_bps) / Decimal(10_000)
        slippage_rate = Decimal(model.slippage_bps) / Decimal(10_000)
        transaction_rate = fee_rate + spread_rate + slippage_rate
        funding_rate = Decimal(model.funding_bps) / Decimal(3_650_000)
        borrow_rate = Decimal(model.borrow_bps) / Decimal(3_650_000)
        carry_rate = funding_rate + borrow_rate
        turnover = abs(weights[0]) + sum(
            abs(current - previous)
            for previous, current in zip(weights, weights[1:])
        ) + abs(weights[-1])
        trade_count = int(weights[0] != 0) + sum(
            current != previous
            for previous, current in zip(weights, weights[1:])
        ) + int(weights[-1] != 0)
        equity = Decimal(1) - abs(weights[0]) * transaction_rate
        curve = [equity]
        daily: list[Decimal] = [-abs(weights[0]) * transaction_rate]
        for index in range(1, len(values)):
            market_return = values[index].close / values[index - 1].close - 1
            change = abs(weights[index] - weights[index - 1])
            period_return = (
                weights[index - 1] * market_return
                - change * transaction_rate
                - weights[index - 1] * carry_rate
            )
            equity *= Decimal(1) + period_return
            curve.append(equity)
            daily.append(period_return)
        exit_cost = abs(weights[-1]) * transaction_rate
        equity *= Decimal(1) - exit_cost
        curve[-1] = equity
        daily[-1] = (Decimal(1) + daily[-1]) * (Decimal(1) - exit_cost) - 1

        total_return = equity - 1
        years = Decimal(
            (values[-1].closed_at - values[0].closed_at).total_seconds()
        ) / Decimal("31557600")
        cagr = (
            Decimal(-1)
            if total_return <= -1
            else ((Decimal(1) + total_return).ln() / years).exp() - 1
        )
        mean = sum(daily, Decimal(0)) / Decimal(len(daily))
        sample_variance = sum((item - mean) ** 2 for item in daily) / Decimal(
            len(daily) - 1
        )
        annualizer = Decimal(365).sqrt()
        volatility = sample_variance.sqrt() * annualizer
        downside = (
            sum(min(item, Decimal(0)) ** 2 for item in daily) / Decimal(len(daily))
        ).sqrt() * annualizer
        annual_return = mean * Decimal(365)
        failures: list[str] = []
        sharpe = None if volatility == 0 else annual_return / volatility
        if sharpe is None:
            failures.append("E_SHARPE_UNDEFINED")
        sortino = None if downside == 0 else annual_return / downside
        if sortino is None:
            failures.append("E_SORTINO_UNDEFINED")
        peak = curve[0]
        max_drawdown = Decimal(0)
        for item in curve:
            peak = max(peak, item)
            max_drawdown = max(max_drawdown, Decimal(1) - item / peak)
        calmar = None if max_drawdown == 0 else cagr / max_drawdown
        if calmar is None:
            failures.append("E_CALMAR_UNDEFINED")
        ordered = sorted(daily)
        tail_count = max(1, ceil(len(ordered) * 0.05))
        expected_shortfall = sum(ordered[:tail_count], Decimal(0)) / Decimal(tail_count)
        exposure_days = sum(weights[:-1], Decimal(0))
        funding = exposure_days * funding_rate
        borrow = exposure_days * borrow_rate

    return PerformanceMetricsV1(
        total_return=_q(total_return),
        cagr=_q(cagr),
        volatility=_q(volatility),
        max_drawdown=_q(max_drawdown),
        downside_risk=_q(downside),
        sharpe=None if sharpe is None else _q(sharpe),
        sortino=None if sortino is None else _q(sortino),
        calmar=None if calmar is None else _q(calmar),
        trade_count=trade_count,
        turnover=_q(turnover),
        average_holding_days=_holding_days(weights),
        fees=_q(turnover * fee_rate),
        spread=_q(turnover * spread_rate),
        slippage=_q(turnover * slippage_rate),
        funding=_q(funding),
        borrow_cost=_q(borrow),
        worst_day=_q(min(daily)),
        worst_week=_q(_rolling_worst(tuple(daily), 7)),
        worst_month=_q(_rolling_worst(tuple(daily), 30)),
        expected_shortfall_5=_q(expected_shortfall),
        failure_codes=tuple(sorted(failures)),
    )


__all__ = ["CostModelV1", "calculate_performance_metrics"]
