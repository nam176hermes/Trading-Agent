from __future__ import annotations

from decimal import Decimal

import pytest

from packages.alpha_lifecycle.baselines import BaselineId, baseline_weights
from packages.alpha_lifecycle.metrics import CostModelV1, calculate_performance_metrics
from tests.alpha_lifecycle.test_baselines import _rows


COSTS = CostModelV1(
    fee_bps=10,
    spread_bps=5,
    slippage_bps=5,
    funding_bps=0,
    borrow_bps=0,
)


def test_cash_metrics_have_explicit_undefined_ratios() -> None:
    rows = _rows(40)
    metrics = calculate_performance_metrics(
        rows, baseline_weights(BaselineId.CASH, rows), COSTS
    )

    assert metrics.total_return == 0
    assert metrics.sharpe is None
    assert metrics.sortino is None
    assert metrics.calmar is None
    assert metrics.failure_codes == (
        "E_CALMAR_UNDEFINED",
        "E_SHARPE_UNDEFINED",
        "E_SORTINO_UNDEFINED",
    )


def test_buy_and_hold_metrics_are_cost_adjusted_and_repeatable() -> None:
    rows = _rows(400)
    weights = baseline_weights(BaselineId.BUY_AND_HOLD, rows)

    first = calculate_performance_metrics(rows, weights, COSTS)
    second = calculate_performance_metrics(rows, weights, COSTS)

    assert first == second
    assert first.total_return > 0
    assert first.turnover == Decimal(2)
    assert first.trade_count == 2
    assert first.fees == Decimal("0.002000000000")
    assert first.spread == Decimal("0.001000000000")
    assert first.slippage == Decimal("0.001000000000")


def test_funding_and_borrow_are_charged_to_net_returns() -> None:
    rows = _rows(400)
    weights = baseline_weights(BaselineId.BUY_AND_HOLD, rows)
    zero_carry = calculate_performance_metrics(rows, weights, COSTS)
    carry = calculate_performance_metrics(
        rows,
        weights,
        COSTS.model_copy(update={"funding_bps": 365, "borrow_bps": 365}),
    )

    assert carry.total_return < zero_carry.total_return
    assert carry.funding == carry.borrow_cost == Decimal("0.039900000000")


@pytest.mark.parametrize("attack", ("duplicate_time", "mixed_instrument"))
def test_metrics_reject_ambiguous_price_series(attack: str) -> None:
    rows = list(_rows(3))
    if attack == "duplicate_time":
        rows[1] = rows[1].model_copy(update={"closed_at": rows[0].closed_at})
    else:
        rows[1] = rows[1].model_copy(update={"instrument": "ETHUSDT.BINANCE"})

    with pytest.raises(ValueError, match="single instrument|strictly ordered"):
        calculate_performance_metrics(tuple(rows), (Decimal(1),) * 3, COSTS)
