"""Deterministic, fail-closed evaluation for mandatory WS-04 research gates."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from packages.engine_contracts import canonical_json_bytes
from packages.engine_contracts.serialization import Sha256Hex

from .models import ResearchGateEvidenceV1, ResearchValidationModel


GateName = Literal[
    "lookahead",
    "recursive-indicator-stability",
    "walk-forward",
    "fee-slippage-sensitivity",
    "benchmark-comparison",
    "provenance-verification",
]
_GATE_ORDER: tuple[GateName, ...] = (
    "lookahead",
    "recursive-indicator-stability",
    "walk-forward",
    "fee-slippage-sensitivity",
    "benchmark-comparison",
    "provenance-verification",
)


class ResearchGateResultV1(ResearchValidationModel):
    name: GateName
    passed: bool
    failure_codes: tuple[str, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def _canonical_status(self) -> "ResearchGateResultV1":
        if self.failure_codes != tuple(sorted(set(self.failure_codes))):
            raise ValueError("failure codes must be sorted and unique")
        if self.passed != (not self.failure_codes):
            raise ValueError("gate pass status must match failure codes")
        return self


class ResearchGateReportV1(ResearchValidationModel):
    schema_version: Literal["research-gate-report-v1"] = "research-gate-report-v1"
    evidence_sha256: Sha256Hex
    results: tuple[ResearchGateResultV1, ...] = Field(min_length=6, max_length=6)
    report_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _complete_digest(self) -> "ResearchGateReportV1":
        if tuple(item.name for item in self.results) != _GATE_ORDER:
            raise ValueError("research gate results must be complete and ordered")
        digest = hashlib.sha256(
            canonical_json_bytes(
                {"schema_version": self.schema_version, "results": self.results}
                | {"evidence_sha256": self.evidence_sha256}
            )
        ).hexdigest()
        if self.report_sha256 is not None and self.report_sha256 != digest:
            raise ValueError("research report digest does not match results")
        object.__setattr__(self, "report_sha256", digest)
        return self

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.results)


def _result(name: GateName, failures: set[str]) -> ResearchGateResultV1:
    return ResearchGateResultV1(
        name=name, passed=not failures, failure_codes=tuple(sorted(failures))
    )


def _input_artifacts_sha256(evidence: ResearchGateEvidenceV1) -> str:
    provenance = evidence.provenance
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "engine_configuration": provenance.engine_configuration_sha256,
                "instrument_catalog": provenance.instrument_catalog_sha256,
                "strategy_configuration": provenance.strategy_configuration_sha256,
                "market_data": provenance.market_data_sha256,
            }
        )
    ).hexdigest()


def _lookahead(evidence: ResearchGateEvidenceV1) -> ResearchGateResultV1:
    failures: set[str] = set()
    if not evidence.point_in_time:
        failures.add("E_LOOKAHEAD_EMPTY")
    for item in evidence.point_in_time:
        if not item.feature_event_at <= item.known_at <= item.decision_at:
            failures.add("E_LOOKAHEAD_TIME")
        if item.known_at < evidence.provenance.dataset.known_at:
            failures.add("E_LOOKAHEAD_MANIFEST_AVAILABILITY")
        if item.source_data_sha256 != evidence.provenance.canonical_rows_sha256:
            failures.add("E_LOOKAHEAD_SOURCE")
        if item.input_artifacts_sha256 != evidence.provenance.backtest_input_artifacts_sha256:
            failures.add("E_LOOKAHEAD_INPUT_DRIFT")
    return _result("lookahead", failures)


def _recursive_indicator_stability(
    evidence: ResearchGateEvidenceV1,
) -> ResearchGateResultV1:
    failures: set[str] = set()
    if not evidence.recursive_replays:
        failures.add("E_RECURSIVE_EMPTY")
    if any(
        item.prefix_state_sha256 != item.replay_state_sha256
        for item in evidence.recursive_replays
    ):
        failures.add("E_RECURSIVE_STATE_DRIFT")
    if any(
        item.input_artifacts_sha256 != evidence.provenance.backtest_input_artifacts_sha256
        for item in evidence.recursive_replays
    ):
        failures.add("E_RECURSIVE_INPUT_DRIFT")
    return _result("recursive-indicator-stability", failures)


def _walk_forward(evidence: ResearchGateEvidenceV1) -> ResearchGateResultV1:
    failures: set[str] = set()
    folds = evidence.walk_forward_folds
    if len(folds) < 2:
        failures.add("E_WALK_FORWARD_FOLD_COUNT")
    previous_oos_end = None
    for fold in folds:
        if fold.input_artifacts_sha256 != evidence.provenance.backtest_input_artifacts_sha256:
            failures.add("E_WALK_FORWARD_INPUT_DRIFT")
        if not (
            fold.train_start_at <= fold.train_end_at < fold.validation_start_at
            <= fold.validation_end_at < fold.out_of_sample_start_at
            <= fold.out_of_sample_end_at
        ):
            failures.add("E_WALK_FORWARD_WINDOW")
        if previous_oos_end is not None and fold.out_of_sample_start_at <= previous_oos_end:
            failures.add("E_WALK_FORWARD_OOS_OVERLAP")
        previous_oos_end = fold.out_of_sample_end_at
    if sum((fold.out_of_sample_return for fold in folds), Decimal("0")) < evidence.minimum_walk_forward_return:
        failures.add("E_WALK_FORWARD_RETURN")
    return _result("walk-forward", failures)


def _fee_slippage_sensitivity(evidence: ResearchGateEvidenceV1) -> ResearchGateResultV1:
    failures: set[str] = set()
    scenarios = {item.name: item for item in evidence.cost_scenarios}
    required = {"baseline", "fee-stress", "slippage-stress", "combined-stress"}
    if set(scenarios) != required:
        failures.add("E_COST_SCENARIO_SET")
        return _result("fee-slippage-sensitivity", failures)
    baseline = scenarios["baseline"]
    fee_stress = scenarios["fee-stress"]
    slippage_stress = scenarios["slippage-stress"]
    combined_stress = scenarios["combined-stress"]
    if fee_stress.fee_bps <= baseline.fee_bps:
        failures.add("E_COST_NO_FEE_STRESS")
    if slippage_stress.slippage_bps <= baseline.slippage_bps:
        failures.add("E_COST_NO_SLIPPAGE_STRESS")
    if (
        combined_stress.fee_bps <= baseline.fee_bps
        or combined_stress.slippage_bps <= baseline.slippage_bps
    ):
        failures.add("E_COST_NO_COMBINED_STRESS")
    for name in required - {"baseline"}:
        scenario = scenarios[name]
        if scenario.input_artifacts_sha256 != evidence.provenance.backtest_input_artifacts_sha256:
            failures.add("E_COST_INPUT_DRIFT")
        if scenario.fee_bps < baseline.fee_bps or scenario.slippage_bps < baseline.slippage_bps:
            failures.add("E_COST_NON_MONOTONIC")
        if scenario.net_return < evidence.minimum_stressed_return:
            failures.add("E_COST_STRESSED_RETURN")
    return _result("fee-slippage-sensitivity", failures)


def _benchmark_comparison(evidence: ResearchGateEvidenceV1) -> ResearchGateResultV1:
    failures: set[str] = set()
    comparisons = {item.comparator: item for item in evidence.comparisons}
    if set(comparisons) != {"reference", "legacy", "nautilus"}:
        failures.add("E_BENCHMARK_COMPARATOR_SET")
        return _result("benchmark-comparison", failures)
    expected_inputs = evidence.provenance.backtest_input_artifacts_sha256
    if any(item.input_artifacts_sha256 != expected_inputs for item in comparisons.values()):
        failures.add("E_BENCHMARK_INPUT_DRIFT")
    reference = comparisons["reference"]
    for name in ("legacy", "nautilus"):
        item = comparisons[name]
        if item.disposition == "match" and item.result_sha256 != reference.result_sha256:
            failures.add("E_BENCHMARK_FALSE_MATCH")
    if comparisons["nautilus"].result_sha256 != evidence.provenance.backtest_result_sha256:
        failures.add("E_BENCHMARK_NAUTILUS_RESULT_DRIFT")
    if comparisons["nautilus"].event_sha256 != evidence.provenance.backtest_event_sha256:
        failures.add("E_BENCHMARK_NAUTILUS_EVENT_DRIFT")
    return _result("benchmark-comparison", failures)


def _provenance_verification(evidence: ResearchGateEvidenceV1) -> ResearchGateResultV1:
    failures: set[str] = set()
    provenance = evidence.provenance
    if provenance.dataset_content_sha256 != provenance.dataset.content_digest:
        failures.add("E_PROVENANCE_DATASET_CONTENT")
    if provenance.canonical_rows_sha256 != provenance.dataset.canonical_rows_sha256:
        failures.add("E_PROVENANCE_CANONICAL_ROWS")
    if provenance.backtest_input_artifacts_sha256 != _input_artifacts_sha256(evidence):
        failures.add("E_PROVENANCE_INPUT_DIGEST")
    return _result("provenance-verification", failures)


def evaluate_research_gates(evidence: ResearchGateEvidenceV1) -> ResearchGateReportV1:
    """Evaluate all 04D gates from immutable evidence and never infer authority."""

    if type(evidence) is not ResearchGateEvidenceV1:
        raise TypeError("ResearchGateEvidenceV1 is required")
    return ResearchGateReportV1(
        evidence_sha256=hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
        results=(
            _lookahead(evidence),
            _recursive_indicator_stability(evidence),
            _walk_forward(evidence),
            _fee_slippage_sensitivity(evidence),
            _benchmark_comparison(evidence),
            _provenance_verification(evidence),
        )
    )
