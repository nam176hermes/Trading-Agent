from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.alpha_lifecycle.baselines import (
    BaselineId,
    DailyCloseV1,
    baseline_weights,
    baseline_weights_with_reset,
)


def test_momentum_context_does_not_inherit_a_training_position() -> None:
    start = datetime(2024, 10, 1, 23, 59, 59, 999999, tzinfo=UTC)
    rows = tuple(
        DailyCloseV1(
            instrument="BTCUSDT.BINANCE",
            closed_at=start + timedelta(days=index),
            close=Decimal(100 + index),
        )
        for index in range(220)
    )
    score_start = datetime(2025, 3, 15, 23, 59, 59, 999999, tzinfo=UTC)
    first = next(index for index, row in enumerate(rows) if row.closed_at == score_start)

    legacy = baseline_weights(BaselineId.SIMPLE_MOMENTUM, rows)
    reset = baseline_weights_with_reset(
        BaselineId.SIMPLE_MOMENTUM, rows, score_start=score_start
    )

    assert legacy[first] == 1
    assert reset[0] == 0
    april = next(index for index, row in enumerate(rows[first:]) if row.closed_at.month == 4)
    assert reset[april] == 1
