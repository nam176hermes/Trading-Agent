"""Deterministic implementations of the four frozen P3 candidate families."""

from __future__ import annotations

from decimal import Decimal, localcontext

from packages.alpha_lifecycle.contracts.data import DailyBar, Fold
from packages.alpha_lifecycle.contracts.policy import CandidateParameters, CandidateSpec


class CandidateError(ValueError):
    """Candidate inputs do not identify one frozen logical path."""


def _next_weight(
    parameters: CandidateParameters,
    bars: tuple[DailyBar, ...],
    index: int,
    weight: int,
) -> int:
    close = Decimal(bars[index].close)
    family = parameters.family
    if family == "DONCHIAN":
        if index < parameters.entry:
            return weight
        upper = max(Decimal(item.high) for item in bars[index - parameters.entry:index])
        lower = min(Decimal(item.low) for item in bars[index - parameters.exit:index])
        if weight == 0 and close > upper:
            return 1
        if weight == 1 and close < lower:
            return 0
        return weight
    if family == "DUAL_SMA":
        if index + 1 < parameters.slow:
            return weight
        fast = sum((Decimal(item.close) for item in bars[index - parameters.fast + 1:index + 1]), Decimal(0)) / Decimal(parameters.fast)
        slow = sum((Decimal(item.close) for item in bars[index - parameters.slow + 1:index + 1]), Decimal(0)) / Decimal(parameters.slow)
        return int(fast > slow)
    if family == "ZSCORE":
        if index + 1 < parameters.window:
            return weight
        values = tuple(Decimal(item.close) for item in bars[index - parameters.window + 1:index + 1])
        mean = sum(values, Decimal(0)) / Decimal(parameters.window)
        deviation = (sum((item - mean) ** 2 for item in values) / Decimal(parameters.window)).sqrt()
        if deviation == 0:
            return 0
        z_score = (close - mean) / deviation
        if weight == 0 and z_score <= Decimal(parameters.entry_z):
            return 1
        if weight == 1 and z_score >= Decimal(parameters.exit_z):
            return 0
        return weight
    if index <= parameters.horizons[-1]:
        return weight
    positive = sum(
        close / Decimal(bars[index - horizon].close) - 1 > 0
        for horizon in parameters.horizons
    )
    return int(positive >= parameters.positive_votes)


def run_candidate(
    spec: CandidateSpec,
    bars: tuple[DailyBar, ...],
    fold: Fold,
    *,
    perturbation_id: str | None = None,
) -> tuple[int, ...]:
    spec = CandidateSpec.model_validate(spec)
    values = tuple(DailyBar.model_validate(item) for item in bars)
    if not values or tuple(item.date for item in values) != tuple(sorted({item.date for item in values})):
        raise CandidateError("candidate bars must be unique and ordered")
    if perturbation_id is None:
        parameters = spec.parameters
    else:
        try:
            parameters = next(
                item.parameters for item in spec.perturbations
                if item.perturbation_id == perturbation_id
            )
        except StopIteration as error:
            raise CandidateError("runtime perturbation is outside the frozen candidate") from error
    selected = [
        index for index, bar in enumerate(values)
        if fold.decision_start <= bar.date <= fold.return_end_range.end
    ]
    if not selected or values[selected[0]].date != fold.decision_start:
        raise CandidateError("bars do not contain the fold decision start")
    weight = 0
    output: list[int] = []
    with localcontext() as context:
        context.prec = 50
        for index in selected:
            weight = _next_weight(parameters, values, index, weight)
            output.append(weight)
    if len(output) != fold.return_count + 1:
        raise CandidateError("candidate output does not match fold return count")
    output[-1] = output[-2]
    return tuple(output)


__all__ = ["CandidateError", "run_candidate"]
