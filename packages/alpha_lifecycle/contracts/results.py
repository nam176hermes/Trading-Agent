"""P3 deterministic evaluation, replay, and phase-exit result contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.alpha_lifecycle.baselines import BaselineId, BaselineResultV1
from packages.alpha_lifecycle.protocol import PerformanceMetricsV1, QualificationCriterionV1
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime

from .base import DecimalText, DigestModel, SemVer, Sha256, SourceIdentity, StrictModel, Text, Token
from .data import DateRange, Day


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


class TraceSample(DigestModel):
    sample_index: Annotated[int, Field(ge=0)]
    kind: Literal["ENTRY", "RETURN"]
    return_end_at: CanonicalUtcDateTime
    applied_weight: Literal[0, 1]
    next_weight: Literal[0, 1]
    market_return: DecimalText
    gross_return: DecimalText
    turnover: DecimalText
    transition_cost: DecimalText
    carry_cost: DecimalText
    exit_cost: DecimalText
    net_return: DecimalText
    equity: DecimalText
    regime: Literal["BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL"]
    decision_row_ref: ArtifactRefV1 | None
    return_row_ref: ArtifactRefV1 | None


class FoldResult(DigestModel):
    fold_id: Literal["F1", "F2", "F3", "H1"]
    trace_ref: ArtifactRefV1
    metrics: PerformanceMetricsV1


class ScenarioResult(DigestModel):
    schema_version: Literal["p3-scenario-result-v1"]
    scenario: Literal["BASE", "DOUBLE_COST", "DELAYED", "PERTURBATION"]
    perturbation_id: Token | None
    fold_results: Annotated[tuple[FoldResult, ...], Field(min_length=1, max_length=3)]
    aggregate_metrics: PerformanceMetricsV1


class BaselineEntry(StrictModel):
    baseline_id: BaselineId
    baseline_version: SemVer
    scenario_ref: ArtifactRefV1
    aggregate_metrics: PerformanceMetricsV1


class BaselinePack(DigestModel):
    schema_version: Literal["p3-baseline-pack-v1"]
    input_set_ref: ArtifactRefV1
    baseline_results: Annotated[tuple[BaselineEntry, ...], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def _baselines(self) -> "BaselinePack":
        if tuple(item.baseline_id for item in self.baseline_results) != tuple(BaselineId):
            raise ValueError("baseline pack must contain exact B0 through B4")
        return self


class BaselineSelection(DigestModel):
    schema_version: Literal["p3-baseline-selection-v1"]
    pack_ref: ArtifactRefV1
    selected_id: Text
    selected_result: BaselineResultV1
    selection_policy_digest: Sha256
    baseline_replay_proof_ref: ArtifactRefV1


class PerformanceTrace(DigestModel):
    schema_version: Literal["p3-performance-trace-v1"]
    fold_ref: ArtifactRefV1
    subject_id: Text
    cost_model_digest: Sha256
    raw_signal_digest: Sha256
    effective_weights_digest: Sha256
    samples: Annotated[tuple[TraceSample, ...], BeforeValidator(_tuple), Field(min_length=2, max_length=5000)]
    metrics: PerformanceMetricsV1
    round_trip_count: Annotated[int, Field(ge=0)]


class RegimeThreshold(DigestModel):
    schema_version: Literal["p3-regime-threshold-v1"]
    policy_digest: Sha256
    training_dataset_ref: ArtifactRefV1
    training_range: DateRange
    sample_count: Annotated[int, Field(ge=0)]
    ordered_volatility_digest: Sha256
    threshold: DecimalText


class RegimeResult(StrictModel):
    schema_version: Literal["p3-regime-result-v1"]
    regime_id: Literal["BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL"]
    sample_indices: Annotated[tuple[int, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=5000)]
    candidate_return: DecimalText
    baseline_return: DecimalText
    excess: DecimalText


class RegimeEvidence(DigestModel):
    threshold_ref: ArtifactRefV1
    assignment_trace_ref: ArtifactRefV1
    regime_ids: Annotated[tuple[str, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    candidate_returns: Annotated[tuple[DecimalText, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    baseline_returns: Annotated[tuple[DecimalText, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    excess_returns: Annotated[tuple[DecimalText, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    sample_counts: Annotated[tuple[int, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]


class CapacityEvidence(DigestModel):
    schema_version: Literal["p3-capacity-evidence-v1"]
    policy_digest: Sha256
    event_participations: Annotated[tuple[DecimalText, ...], BeforeValidator(_tuple), Field(max_length=5000)]
    event_keys: Annotated[tuple[Token, ...], BeforeValidator(_tuple), Field(max_length=5000)]
    median: DecimalText
    peak: DecimalText
    no_trades: bool

    @model_validator(mode="after")
    def _events(self) -> "CapacityEvidence":
        if len(self.event_participations) != len(self.event_keys):
            raise ValueError("capacity event arrays must have equal lengths")
        return self


class RobustnessEvidence(DigestModel):
    schema_version: Literal["p3-robustness-evidence-v1"]
    regimes: Annotated[tuple[RegimeResult, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    perturbation_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=4, max_length=4)]
    double_cost_ref: ArtifactRefV1
    delayed_ref: ArtifactRefV1
    capacity: CapacityEvidence


class EvaluationResult(DigestModel):
    schema_version: Literal["p3-evaluation-result-v1"]
    manifest_ref: ArtifactRefV1
    base: ScenarioResult
    double_cost: ScenarioResult
    delayed: ScenarioResult
    perturbations: Annotated[tuple[ScenarioResult, ...], Field(min_length=4, max_length=4)]
    regimes: Annotated[tuple[RegimeResult, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    capacity: CapacityEvidence
    deterministic_trial_keys: Annotated[tuple[Token, ...], BeforeValidator(_tuple), Field(min_length=7, max_length=7)]


class ReplayReceipt(DigestModel):
    schema_version: Literal["p3-replay-receipt-v1"]
    logical_trial_id: Token
    replicate: Literal["R1", "R2", "R3"]
    manifest_digest: Sha256
    result_ref: ArtifactRefV1
    source: SourceIdentity
    environment_ref: ArtifactRefV1
    sandbox_policy_digest: Sha256
    started_at: CanonicalUtcDateTime
    completed_at: CanonicalUtcDateTime
    process_exit: Literal[0]
    network_denied: Literal[True]
    output_inventory_digest: Sha256


class ReplayProof(DigestModel):
    schema_version: Literal["p3-replay-proof-v1"]
    manifest_digest: Sha256
    result_digest: Sha256
    receipt_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]


class QualificationBundle(DigestModel):
    schema_version: Literal["p3-qualification-bundle-v1"]
    evaluation_ref: ArtifactRefV1
    replay_proof_ref: ArtifactRefV1
    pit_ref: ArtifactRefV1
    legacy_evidence_ref: ArtifactRefV1
    legacy_result_ref: ArtifactRefV1
    criteria: Annotated[tuple[QualificationCriterionV1, ...], BeforeValidator(_tuple), Field(min_length=16, max_length=16)]
    pipeline_verdict: Literal["PASS"]
    alpha_verdict: Literal["PASS", "FAIL"]


class HoldoutEvaluationResult(DigestModel):
    schema_version: Literal["p3-holdout-evaluation-result-v1"]
    manifest_ref: ArtifactRefV1
    primary_base: ScenarioResult
    primary_double_cost: ScenarioResult
    primary_delayed: ScenarioResult
    baseline_base: ScenarioResult
    capacity: CapacityEvidence


class NativeTraceRow(DigestModel):
    schema_version: Literal["p3-native-trace-row-v1"]
    sequence: Annotated[int, Field(ge=0)]
    event_time_ns: Annotated[int, Field(ge=0)]
    init_time_ns: Annotated[int, Field(ge=0)]
    kind: Literal["SIGNAL", "ORDER", "QUOTE", "FILL", "MARK", "LIQUIDATION"]
    side: Literal["BUY", "SELL", "NONE"]
    price: DecimalText | None
    quantity: DecimalText | None
    fee_quote: DecimalText | None
    cash_after: DecimalText
    position_after: DecimalText
    source_day: Day
    source_artifact_ref: ArtifactRefV1


class ExecutableResult(DigestModel):
    schema_version: Literal["p3-executable-result-v1"]
    manifest_ref: ArtifactRefV1
    parity_policy_digest: Sha256
    instrument_spec_ref: ArtifactRefV1
    transition_trace_ref: ArtifactRefV1
    fill_trace_ref: ArtifactRefV1
    ending_cash: DecimalText
    ending_position: DecimalText
    net_return: DecimalText
    max_drawdown: DecimalText


class ParityResult(DigestModel):
    schema_version: Literal["p3-parity-result-v1"]
    reference_ref: ArtifactRefV1
    native_result_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    native_receipt_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=3, max_length=3)]
    comparison_ref: ArtifactRefV1
    exact_fields_pass: bool
    numeric_fields_pass: bool
    verdict: Literal["PASS", "FAIL", "DEFERRED"]


class ExitCheck(StrictModel):
    check_id: Literal["RETURN", "EXCESS", "DOUBLE_COST", "DELAY", "MAX_DRAWDOWN", "CAPACITY", "NATIVE_RETURN", "NATIVE_EXCESS", "NATIVE_DRAWDOWN", "PARITY", "REPLAY", "PRIMARY", "IDENTITY"]
    passed: bool
    code: Annotated[str, Field(pattern=r"^E_[A-Z0-9_]+$", max_length=96)]


class ExitResult(DigestModel):
    schema_version: Literal["p3-exit-result-v1"]
    primary_selection_ref: ArtifactRefV1
    holdout_request_ref: ArtifactRefV1
    holdout_evaluation_ref: ArtifactRefV1
    holdout_replay_ref: ArtifactRefV1
    executable_ref: ArtifactRefV1
    baseline_executable_ref: ArtifactRefV1
    parity_ref: ArtifactRefV1
    checks: Annotated[tuple[ExitCheck, ...], BeforeValidator(_tuple), Field(min_length=13, max_length=13)]
    verdict: Literal["PASS", "FAIL", "HELD"]
    limitations: Annotated[tuple[Token, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=16)]


class TrialOutcome(DigestModel):
    schema_version: Literal["p3-trial-outcome-v1"]
    trial_key: Token
    status: Literal["PREREGISTERED", "STARTED", "COMPLETED", "PIPELINE_FAILED", "CANCELLED"]
    result_ref: ArtifactRefV1 | None
    execution_receipt_refs: Annotated[tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(max_length=3)]


__all__ = ["BaselineEntry", "BaselinePack", "BaselineSelection", "CapacityEvidence", "EvaluationResult", "ExecutableResult", "ExitCheck", "ExitResult", "FoldResult", "HoldoutEvaluationResult", "NativeTraceRow", "ParityResult", "PerformanceTrace", "QualificationBundle", "RegimeEvidence", "RegimeResult", "RegimeThreshold", "ReplayProof", "ReplayReceipt", "RobustnessEvidence", "ScenarioResult", "TraceSample", "TrialOutcome"]
