"""Reconstructible P3 trace using the frozen V1 accounting convention."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from packages.alpha_lifecycle.baselines import DailyCloseV1
from packages.alpha_lifecycle.contracts.results import PerformanceTrace, TraceSample
from packages.alpha_lifecycle.metrics import CostModelV1, calculate_performance_metrics
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class TraceMathSample:
    market_return: Decimal
    gross_return: Decimal
    turnover: Decimal
    transition_cost: Decimal
    carry_cost: Decimal
    exit_cost: Decimal
    net_return: Decimal
    equity: Decimal


def _validated(
    rows: tuple[DailyCloseV1, ...], weights: tuple[Decimal, ...]
) -> tuple[DailyCloseV1, ...]:
    values = tuple(DailyCloseV1.model_validate(item) for item in rows)
    if len(values) < 2 or len(values) != len(weights):
        raise ValueError("trace requires matching closes and weights")
    if any(weight not in {Decimal(0), Decimal(1)} for weight in weights):
        raise ValueError("trace weights must be long or flat")
    return values


def suppress_terminal_signal(weights: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if len(weights) < 2:
        raise ValueError("terminal suppression requires at least two weights")
    return (*weights[:-1], weights[-2])


def build_trace_math(
    rows: tuple[DailyCloseV1, ...],
    weights: tuple[Decimal, ...],
    costs: CostModelV1,
) -> tuple[TraceMathSample, ...]:
    values = _validated(rows, weights)
    costs = CostModelV1.model_validate(costs)
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        transaction_rate = Decimal(
            costs.fee_bps + costs.spread_bps + costs.slippage_bps
        ) / Decimal(10_000)
        carry_rate = Decimal(costs.funding_bps + costs.borrow_bps) / Decimal(3_650_000)
        entry_cost = abs(weights[0]) * transaction_rate
        equity = Decimal(1) - entry_cost
        result = [TraceMathSample(
            Decimal(0), Decimal(0), abs(weights[0]), entry_cost,
            Decimal(0), Decimal(0), -entry_cost, equity,
        )]
        for index in range(1, len(values)):
            market = values[index].close / values[index - 1].close - 1
            gross = weights[index - 1] * market
            turnover = abs(weights[index] - weights[index - 1])
            transition = turnover * transaction_rate
            carry = weights[index - 1] * carry_rate
            net = gross - transition - carry
            exit_cost = abs(weights[-1]) * transaction_rate if index == len(values) - 1 else Decimal(0)
            if exit_cost:
                net = (Decimal(1) + net) * (Decimal(1) - exit_cost) - 1
            equity *= Decimal(1) + net
            result.append(TraceMathSample(
                market, gross, turnover, transition, carry, exit_cost, net, equity
            ))
    return tuple(result)


def _text(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


def build_research_trace(
    rows: tuple[DailyCloseV1, ...],
    weights: tuple[Decimal, ...],
    costs: CostModelV1,
    *,
    fold_ref: ArtifactRefV1,
    subject_id: str,
    decision_row_refs: tuple[ArtifactRefV1, ...],
    return_row_refs: tuple[ArtifactRefV1, ...],
    regimes: tuple[str, ...],
) -> PerformanceTrace:
    values = _validated(rows, weights)
    if not (
        len(decision_row_refs) == len(values) - 1
        and len(return_row_refs) == len(values) - 1
        and len(regimes) == len(values)
    ):
        raise ValueError("trace evidence arrays do not align")
    math = build_trace_math(values, weights, costs)
    samples = []
    for index, item in enumerate(math):
        payload = {
            "sample_index": index,
            "kind": "ENTRY" if index == 0 else "RETURN",
            "return_end_at": values[index].model_dump(mode="json")["closed_at"],
            "applied_weight": int(weights[max(0, index - 1)]),
            "next_weight": int(weights[index]),
            "market_return": _text(item.market_return),
            "gross_return": _text(item.gross_return),
            "turnover": _text(item.turnover),
            "transition_cost": _text(item.transition_cost),
            "carry_cost": _text(item.carry_cost),
            "exit_cost": _text(item.exit_cost),
            "net_return": _text(item.net_return),
            "equity": _text(item.equity),
            "regime": regimes[index],
            "decision_row_ref": decision_row_refs[max(0, index - 1)],
            "return_row_ref": None if index == 0 else return_row_refs[index - 1],
        }
        payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        samples.append(TraceSample.model_validate(payload))
    metrics = calculate_performance_metrics(values, weights, costs)
    payload = {
        "schema_version": "p3-performance-trace-v1",
        "fold_ref": fold_ref,
        "subject_id": subject_id,
        "cost_model_digest": hashlib.sha256(canonical_json_bytes(costs)).hexdigest(),
        "raw_signal_digest": hashlib.sha256(canonical_json_bytes(tuple(map(int, weights)))).hexdigest(),
        "effective_weights_digest": hashlib.sha256(canonical_json_bytes(tuple(map(int, weights)))).hexdigest(),
        "samples": tuple(samples),
        "metrics": metrics,
        "round_trip_count": metrics.trade_count,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PerformanceTrace.model_validate(payload)


__all__ = ["TraceMathSample", "build_research_trace", "build_trace_math", "suppress_terminal_signal"]
