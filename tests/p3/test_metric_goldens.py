from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.alpha_lifecycle.baselines import DailyCloseV1
from packages.alpha_lifecycle.execution_trace import build_trace_math, suppress_terminal_signal
from packages.alpha_lifecycle.metrics import CostModelV1, calculate_aggregate_performance_metrics


def _rows(prices: tuple[str, ...]) -> tuple[DailyCloseV1, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return tuple(
        DailyCloseV1(
            instrument="BTCUSDT.BINANCE",
            closed_at=start + timedelta(days=index),
            close=Decimal(price),
        )
        for index, price in enumerate(prices)
    )


@pytest.mark.parametrize(
    ("prices", "weights", "samples", "equity"),
    (
        (("100", "110"), (1, 1), ("-0.01", "0.089"), "1.07811"),
        (("100", "100"), (1, 1), ("-0.01", "-0.01"), "0.9801"),
        (("100", "110", "99"), (1, 0, 0), ("-0.01", "0.09", "0"), "1.0791"),
        (("100", "110"), (0, 0), ("0", "0"), "1"),
    ),
)
def test_trace_math_matches_independent_pack_goldens(
    prices: tuple[str, ...],
    weights: tuple[int, ...],
    samples: tuple[str, ...],
    equity: str,
) -> None:
    costs = CostModelV1(fee_bps=100, spread_bps=0, slippage_bps=0, funding_bps=0, borrow_bps=0)
    trace = build_trace_math(_rows(prices), tuple(map(Decimal, weights)), costs)
    assert tuple(item.net_return for item in trace) == tuple(map(Decimal, samples))
    assert trace[-1].equity == Decimal(equity)


def test_terminal_signal_is_suppressed_before_trace() -> None:
    assert suppress_terminal_signal((Decimal(0), Decimal(0), Decimal(1))) == (
        Decimal(0), Decimal(0), Decimal(0)
    )


def test_aggregate_metrics_compound_complete_fold_samples_without_cross_fold_return() -> None:
    costs = CostModelV1(fee_bps=100, spread_bps=0, slippage_bps=0, funding_bps=0, borrow_bps=0)
    rows = _rows(("100", "110"))
    rows = (rows[0], rows[1].model_copy(update={"closed_at": rows[0].closed_at + timedelta(days=365)}))
    result = calculate_aggregate_performance_metrics(
        ((rows, (Decimal(1), Decimal(1))), (rows, (Decimal(1), Decimal(1)))), costs
    )
    assert result.total_return == Decimal("0.162321172100")
    assert result.trade_count == 4
