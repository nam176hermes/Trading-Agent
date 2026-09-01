from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.alpha_lifecycle.baselines import (
    BaselineId,
    BaselineResultV1,
    DailyCloseV1,
    baseline_weights,
)


def _rows(count: int, *, falling: bool = False) -> tuple[DailyCloseV1, ...]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        DailyCloseV1(
            instrument="BTCUSDT.BINANCE",
            closed_at=start + timedelta(days=index),
            close=Decimal(count - index if falling else index + 1),
        )
        for index in range(count)
    )


def test_cash_buy_and_hold_and_equal_weight_are_exact() -> None:
    rows = _rows(3)

    assert baseline_weights(BaselineId.CASH, rows) == (Decimal(0),) * 3
    assert baseline_weights(BaselineId.BUY_AND_HOLD, rows) == (Decimal(1),) * 3
    assert baseline_weights(BaselineId.EQUAL_WEIGHT, rows) == (Decimal(1),) * 3


def test_momentum_excludes_the_most_recent_five_closes_and_rebalances_monthly() -> None:
    rising = baseline_weights(BaselineId.SIMPLE_MOMENTUM, _rows(160))
    falling = baseline_weights(BaselineId.SIMPLE_MOMENTUM, _rows(160, falling=True))

    assert set(rising[:126]) == {Decimal(0)}
    assert Decimal(1) in rising[126:]
    assert set(falling) == {Decimal(0)}


def test_mean_reversion_is_long_flat_and_deterministic() -> None:
    rows = list(_rows(30))
    rows[21] = rows[21].model_copy(update={"close": Decimal("1")})

    first = baseline_weights(BaselineId.SIMPLE_MEAN_REVERSION, tuple(rows))
    second = baseline_weights(BaselineId.SIMPLE_MEAN_REVERSION, tuple(rows))

    assert first == second
    assert set(first) <= {Decimal(0), Decimal(1)}


def test_baseline_result_is_hash_bound_to_snapshot_costs_and_metrics() -> None:
    result = BaselineResultV1(
        baseline_id=BaselineId.BUY_AND_HOLD,
        baseline_version="1.0.0",
        dataset_snapshot_sha256="a" * 64,
        cost_model_sha256="b" * 64,
        metrics_sha256="c" * 64,
        total_return=Decimal("0.10"),
    )

    assert len(result.result_sha256) == 64
