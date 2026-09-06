"""Frozen training-only P3 regime calculations."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Literal

from packages.alpha_lifecycle.baselines import DailyCloseV1


Regime = Literal["BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL"]
_REQUIRED = frozenset(("BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL"))


class RegimeError(ValueError):
    """Frozen regime evidence cannot be constructed."""


def nearest_rank_threshold(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise RegimeError("training volatility sample is empty")
    ordered = tuple(sorted(values))
    return ordered[(3 * len(ordered) + 3) // 4 - 1]


def _volatility(closes: tuple[Decimal, ...], index: int) -> Decimal:
    returns = tuple(
        closes[i] / closes[i - 1] - 1 for i in range(index - 19, index + 1)
    )
    mean = sum(returns, Decimal(0)) / Decimal(20)
    return (sum((value - mean) ** 2 for value in returns) / Decimal(20)).sqrt()


def training_threshold(rows: tuple[DailyCloseV1, ...]) -> Decimal:
    closes = tuple(row.close for row in rows)
    if len(closes) < 64:
        raise RegimeError("training requires 63 prior closes")
    with localcontext() as context:
        context.prec = 50
        return nearest_rank_threshold(
            tuple(_volatility(closes, index) for index in range(63, len(closes)))
        )


def assign_regime(volatility: Decimal, trend: Decimal, threshold: Decimal) -> Regime:
    if volatility > threshold:
        return "HIGH_VOL"
    return "BULL_LOW_VOL" if trend > 0 else "BEAR_LOW_VOL"


def assign_regimes(
    rows: tuple[DailyCloseV1, ...], threshold: Decimal
) -> tuple[Regime, ...]:
    closes = tuple(row.close for row in rows)
    if len(closes) < 64:
        raise RegimeError("regime assignment requires 63 prior closes")
    with localcontext() as context:
        context.prec = 50
        labels = tuple(
            assign_regime(
                _volatility(closes, index), closes[index] / closes[index - 63] - 1, threshold
            )
            for index in range(63, len(closes))
        )
    require_regime_coverage(labels)
    return labels


def require_regime_coverage(labels: tuple[Regime, ...] | tuple[str, ...]) -> None:
    if set(labels) != _REQUIRED:
        raise RegimeError("E_REGIME_COVERAGE: all three frozen regimes are required")


def compound_by_regime(
    returns: tuple[Decimal, ...], labels: tuple[Regime, ...]
) -> dict[Regime, Decimal]:
    if len(returns) != len(labels):
        raise RegimeError("return and regime samples must align")
    require_regime_coverage(labels)
    result = {label: Decimal(1) for label in _REQUIRED}
    for value, label in zip(returns, labels, strict=True):
        result[label] *= 1 + value
    return {label: value - 1 for label, value in result.items()}


__all__ = [
    "Regime", "RegimeError", "assign_regime", "assign_regimes",
    "compound_by_regime", "nearest_rank_threshold", "require_regime_coverage",
    "training_threshold",
]
