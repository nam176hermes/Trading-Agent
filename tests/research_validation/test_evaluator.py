from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from packages.research_validation import (
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    evaluate_research_gates,
)
from tests.research_validation.test_models import NOW, _digest, evidence


def test_research_gate_evaluator_is_available() -> None:
    assert evaluate_research_gates is not None


def test_complete_evidence_passes_with_stable_digest() -> None:
    first = evaluate_research_gates(evidence())
    second = evaluate_research_gates(evidence())

    assert first.passed is True
    assert first.report_sha256 == second.report_sha256
    assert first.evidence_sha256 == second.evidence_sha256
    assert all(result.passed for result in first.results)


def test_lookahead_and_recursive_drift_block_independently() -> None:
    lookahead = evidence().point_in_time[0].model_copy(
        update={"known_at": NOW + timedelta(minutes=3), "decision_at": NOW + timedelta(minutes=2)}
    )
    report = evaluate_research_gates(evidence(point_in_time=(lookahead,)))
    assert next(item for item in report.results if item.name == "lookahead").failure_codes == (
        "E_LOOKAHEAD_TIME",
    )

    unstable = RecursiveIndicatorReplay(
        indicator_name="ema-20",
        seed_sha256=_digest("4"),
        prefix_state_sha256=_digest("5"),
        replay_state_sha256=_digest("6"),
        sample_count=20,
    )
    report = evaluate_research_gates(evidence(recursive_replays=(unstable,)))
    assert next(
        item for item in report.results if item.name == "recursive-indicator-stability"
    ).failure_codes == ("E_RECURSIVE_STATE_DRIFT",)


def test_walk_forward_and_cost_fail_closed() -> None:
    overlapping = evidence().walk_forward_folds[1].model_copy(
        update={"out_of_sample_start_at": NOW + timedelta(minutes=5)}
    )
    report = evaluate_research_gates(
        evidence(walk_forward_folds=(evidence().walk_forward_folds[0], overlapping))
    )
    assert "E_WALK_FORWARD_OOS_OVERLAP" in next(
        item for item in report.results if item.name == "walk-forward"
    ).failure_codes

    scenarios = tuple(
        CostScenario(
            name=item.name,
            fee_bps=5 if item.name == "baseline" else 0 if item.name == "fee-stress" else item.fee_bps,
            slippage_bps=item.slippage_bps,
            net_return=Decimal("-0.02") if item.name == "combined-stress" else item.net_return,
        )
        for item in evidence().cost_scenarios
    )
    report = evaluate_research_gates(evidence(cost_scenarios=scenarios))
    codes = next(
        item for item in report.results if item.name == "fee-slippage-sensitivity"
    ).failure_codes
    assert "E_COST_NON_MONOTONIC" in codes
    assert "E_COST_STRESSED_RETURN" in codes


def test_benchmark_and_provenance_drift_block() -> None:
    records = tuple(
        item.model_copy(update={"input_artifacts_sha256": _digest("0")})
        if item.comparator == "legacy"
        else item
        for item in evidence().comparisons
    )
    report = evaluate_research_gates(evidence(comparisons=records))
    assert next(
        item for item in report.results if item.name == "benchmark-comparison"
    ).failure_codes == ("E_BENCHMARK_INPUT_DRIFT",)

    provenance = evidence().provenance.model_copy(update={"market_data_sha256": _digest("0")})
    report = evaluate_research_gates(evidence(provenance=provenance))
    assert "E_PROVENANCE_MARKET_DATA" in next(
        item for item in report.results if item.name == "provenance-verification"
    ).failure_codes
