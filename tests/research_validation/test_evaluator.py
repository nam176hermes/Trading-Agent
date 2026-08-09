from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal

import pytest

from packages.engine_contracts import canonical_json_bytes
from packages.research_validation import (
    CostScenario,
    PointInTimeObservation,
    RecursiveIndicatorReplay,
    evaluate_research_gates,
)
from tests.research_validation.test_models import NOW, _digest, evidence
from tests.research_validation.test_models import _campaign_evidence, _scenario_comparison
import packages.research_validation as research


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
        "E_LOOKAHEAD_MANIFEST_AVAILABILITY",
        "E_LOOKAHEAD_TIME",
    )

    pre_manifest = evidence().point_in_time[0].model_copy(
        update={"known_at": NOW + timedelta(minutes=3), "decision_at": NOW + timedelta(minutes=4)}
    )
    report = evaluate_research_gates(evidence(point_in_time=(pre_manifest,)))
    assert next(item for item in report.results if item.name == "lookahead").failure_codes == (
        "E_LOOKAHEAD_MANIFEST_AVAILABILITY",
    )

    unstable = RecursiveIndicatorReplay(
        indicator_name="ema-20",
        input_artifacts_sha256=evidence().provenance.backtest_input_artifacts_sha256,
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

    leaked_train = evidence().walk_forward_folds[1].model_copy(
        update={"train_start_at": NOW + timedelta(minutes=5)}
    )
    report = evaluate_research_gates(
        evidence(walk_forward_folds=(evidence().walk_forward_folds[0], leaked_train))
    )
    assert "E_WALK_FORWARD_FOLD_OVERLAP" in next(
        item for item in report.results if item.name == "walk-forward"
    ).failure_codes

    scenarios = tuple(
        CostScenario(
            name=item.name,
            input_artifacts_sha256=item.input_artifacts_sha256,
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
    assert "E_COST_NO_FEE_STRESS" in codes
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
    assert "E_PROVENANCE_INPUT_DIGEST" in next(
        item for item in report.results if item.name == "provenance-verification"
    ).failure_codes

    drifted_event = tuple(
        item.model_copy(update={"event_sha256": _digest("0")})
        if item.comparator == "nautilus"
        else item
        for item in evidence().comparisons
    )
    report = evaluate_research_gates(evidence(comparisons=drifted_event))
    assert next(
        item for item in report.results if item.name == "benchmark-comparison"
    ).failure_codes == (
        "E_BENCHMARK_FALSE_MATCH_EVENT",
        "E_BENCHMARK_NAUTILUS_EVENT_DRIFT",
    )

    legacy_event_drift = tuple(
        item.model_copy(update={"event_sha256": _digest("0")})
        if item.comparator == "legacy"
        else item
        for item in evidence().comparisons
    )
    report = evaluate_research_gates(evidence(comparisons=legacy_event_drift))
    assert next(
        item for item in report.results if item.name == "benchmark-comparison"
    ).failure_codes == ("E_BENCHMARK_FALSE_MATCH_EVENT",)


def _campaign():
    return _campaign_evidence(
        tuple(
            _scenario_comparison(scenario_id, index)
            for index, scenario_id in enumerate(research.PHASE4_SCENARIO_IDS)
        )
    )


def test_campaign_evaluator_derives_all_six_gate_results_without_pass_flags() -> None:
    first = research.evaluate_research_campaign(_campaign())
    second = research.evaluate_research_campaign(_campaign())

    assert first.passed is True
    assert first.report_sha256 == second.report_sha256
    assert tuple(item.name for item in first.results) == (
        "lookahead",
        "recursive-indicator-stability",
        "walk-forward",
        "fee-slippage-sensitivity",
        "benchmark-comparison",
        "provenance-verification",
    )
    assert first.passed is True
    campaign = _campaign()
    assert campaign.candidate_closure_sha256 != (
        campaign.paper_result.candidate_closure_sha256
    )
    assert campaign.candidate_manifest_sha256 != (
        campaign.paper_result.candidate_manifest_sha256
    )


@pytest.mark.parametrize(
    "field", ("candidate_closure_sha256", "candidate_manifest_sha256")
)
def test_campaign_evaluator_rejects_forged_paper_candidate_self_digest(
    field: str,
) -> None:
    baseline = _campaign()
    forged_paper = baseline.paper_result.model_copy(update={field: _digest("0")})
    forged_record_sha256 = hashlib.sha256(
        canonical_json_bytes(forged_paper) + b"\n"
    ).hexdigest()
    forged = baseline.model_copy(
        update={
            "paper_record_sha256": forged_record_sha256,
            "paper_result": forged_paper,
        }
    )

    result = next(
        item
        for item in research.evaluate_research_campaign(forged).results
        if item.name == "provenance-verification"
    )

    assert result.passed is False
    assert "E_PROVENANCE_PAPER_RESULT" in result.failure_codes


@pytest.mark.parametrize(
    ("field", "failure_code"),
    (
        ("scenario_campaign_sha256", "E_PROVENANCE_PAPER_BINDING"),
        ("strategy_source_sha256", "E_PROVENANCE_PAPER_BINDING"),
        ("parity_record_sha256", "E_PROVENANCE_PAPER_BINDING"),
        ("engine_configuration_sha256", "E_PROVENANCE_PAPER_SCENARIO"),
        ("instrument_catalog_sha256", "E_PROVENANCE_PAPER_SCENARIO"),
        ("strategy_configuration_sha256", "E_PROVENANCE_PAPER_SCENARIO"),
    ),
)
def test_campaign_evaluator_rejects_paper_common_authority_drift(
    field: str,
    failure_code: str,
) -> None:
    baseline = _campaign()
    fields = baseline.paper_result.model_dump(
        exclude={"compatible", "result_sha256", "schema_version"}
    )
    fields[field] = _digest("0")
    drifted_paper = research.PaperCompatibilityResultV1.create(**fields)
    drifted_record_sha256 = hashlib.sha256(
        canonical_json_bytes(drifted_paper) + b"\n"
    ).hexdigest()
    drifted = baseline.model_copy(
        update={
            "paper_record_sha256": drifted_record_sha256,
            "paper_result": drifted_paper,
        }
    )

    result = next(
        item
        for item in research.evaluate_research_campaign(drifted).results
        if item.name == "provenance-verification"
    )

    assert result.passed is False
    assert failure_code in result.failure_codes


def test_campaign_evaluator_rejects_exact_paper_record_digest_drift() -> None:
    baseline = _campaign()
    drifted = baseline.model_copy(update={"paper_record_sha256": _digest("0")})

    result = next(
        item
        for item in research.evaluate_research_campaign(drifted).results
        if item.name == "provenance-verification"
    )

    assert result.failure_codes == ("E_PROVENANCE_PAPER_RECORD",)


def test_campaign_evaluator_blocks_reference_nautilus_drift_and_legacy_selection() -> None:
    baseline = _campaign()
    drifted = baseline.comparisons[0].model_copy(
        update={"nautilus_result_sha256": _digest("0")}
    )
    report = research.evaluate_research_campaign(
        _campaign_evidence((drifted, *baseline.comparisons[1:]))
    )
    assert next(
        item for item in report.results if item.name == "benchmark-comparison"
    ).failure_codes == ("E_CAMPAIGN_RESULT_PARITY",)

    selected = baseline.comparisons[0].model_copy(
        update={"legacy_selected": True}
    )
    report = research.evaluate_research_campaign(
        _campaign_evidence((selected, *baseline.comparisons[1:]))
    )
    assert "E_CAMPAIGN_LEGACY_AUTHORITY" in next(
        item for item in report.results if item.name == "benchmark-comparison"
    ).failure_codes


def test_campaign_evaluator_binds_each_derived_record_to_sealed_inputs() -> None:
    baseline = _campaign()

    point_in_time = (
        baseline.point_in_time[0].model_copy(
            update={"input_artifacts_sha256": _digest("0")}
        ),
        *baseline.point_in_time[1:],
    )
    report = research.evaluate_research_campaign(
        _campaign_evidence(baseline.comparisons, point_in_time=point_in_time)
    )
    assert next(
        item for item in report.results if item.name == "lookahead"
    ).failure_codes == ("E_LOOKAHEAD_INPUT_DRIFT",)

    recursive_replays = (
        baseline.recursive_replays[0].model_copy(
            update={"input_artifacts_sha256": _digest("0")}
        ),
        *baseline.recursive_replays[1:],
    )
    report = research.evaluate_research_campaign(
        _campaign_evidence(
            baseline.comparisons,
            recursive_replays=recursive_replays,
        )
    )
    assert next(
        item
        for item in report.results
        if item.name == "recursive-indicator-stability"
    ).failure_codes == ("E_RECURSIVE_INPUT_DRIFT",)

    folds = (
        baseline.walk_forward_folds[0].model_copy(
            update={"input_artifacts_sha256": _digest("0")}
        ),
        baseline.walk_forward_folds[1],
    )
    report = research.evaluate_research_campaign(
        _campaign_evidence(baseline.comparisons, walk_forward_folds=folds)
    )
    assert next(
        item for item in report.results if item.name == "walk-forward"
    ).failure_codes == ("E_WALK_FORWARD_INPUT_DRIFT",)

    costs = tuple(
        item.model_copy(update={"input_artifacts_sha256": _digest("0")})
        if item.name == "combined-stress"
        else item
        for item in baseline.cost_scenarios
    )
    report = research.evaluate_research_campaign(
        _campaign_evidence(baseline.comparisons, cost_scenarios=costs)
    )
    assert next(
        item for item in report.results if item.name == "fee-slippage-sensitivity"
    ).failure_codes == ("E_COST_INPUT_DRIFT",)
