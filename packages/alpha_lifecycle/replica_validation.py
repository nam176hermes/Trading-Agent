"""Validate result-owned artifacts before a parent issues a replay receipt."""

from typing import TypeVar
from decimal import Decimal

from pydantic import BaseModel

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, validate_research_inputs
from packages.alpha_lifecycle.contracts.base import parse_contract
from packages.alpha_lifecycle.contracts.data import (
    DailyBar,
    Fold,
)
from packages.alpha_lifecycle.contracts.execution import (
    BaselineManifest,
    EvaluationManifest,
)
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.alpha_lifecycle.contracts.results import (
    BaselinePack,
    BaselineSelection,
    EvaluationResult,
    PerformanceTrace,
    ScenarioResult,
)
from packages.alpha_lifecycle.robustness import build_robustness
from packages.alpha_lifecycle.regimes import assign_regimes
from packages.alpha_lifecycle.data_view import to_daily_close
from packages.alpha_lifecycle.execution_trace import build_research_trace
from packages.alpha_lifecycle.metrics import calculate_aggregate_performance_metrics
from packages.alpha_lifecycle.trials import deterministic_trial_keys
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
import hashlib


Model = TypeVar("Model", bound=BaseModel)


def _read(store: ArtifactStore, ref: ArtifactRefV1, model: type[Model]) -> Model:
    raw = store.read_bytes(ref)
    value = parse_contract(model.__name__, raw)
    if not isinstance(value, model) or canonical_json_bytes(value) != raw:
        raise ValueError("replica artifact contract or canonical bytes differ")
    return value


class _ReadbackStore:
    def __init__(self, reader: ArtifactStore, outputs: ArtifactStore) -> None:
        self._reader, self._outputs = reader, outputs

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        return self._reader.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        digest = hashlib.sha256(value).hexdigest()
        ref = ArtifactRefV1(
            content_sha256=digest,
            size_bytes=len(value),
            media_type=media_type,
            locator=f"{digest}.blob",
        )
        if self._outputs.read_bytes(ref) != value:
            raise ValueError("replica recomputation differs from child outputs")
        return ref


def validate_replica_result(
    result: BaselinePack | EvaluationResult,
    manifest: BaselineManifest | EvaluationManifest,
    inputs: ArtifactStore,
    outputs: ArtifactStore,
    combined: ArtifactStore,
) -> None:
    input_set, folds, dataset, regime = validate_research_inputs(manifest.input_set_ref,inputs)
    bars = tuple(_read(inputs, ref, DailyBar) for ref in dataset.row_refs)
    threshold = Decimal(regime.threshold)
    labels_by_fold = {
        fold.fold_id: assign_regimes(
            tuple(
                to_daily_close(bar)
                for bar in bars
                if fold.context_start <= bar.date <= fold.return_end_range.end
            ),
            threshold,
        )[-(fold.return_count + 1) :]
        for fold in folds.folds
    }

    def scenario(
        value: ScenarioResult, subject: str, kind: str, perturbation: str | None
    ) -> None:
        if (value.scenario, value.perturbation_id) != (kind, perturbation):
            raise ValueError("replica scenario identity differs")
        if tuple(item.fold_id for item in value.fold_results) != tuple(
            fold.fold_id for fold in folds.folds
        ):
            raise ValueError("replica fold inventory differs")
        costs = input_set.cost_model
        if kind == "DOUBLE_COST":
            costs = costs.model_copy(
                update={
                    "fee_bps": costs.fee_bps * 2,
                    "spread_bps": costs.spread_bps * 2,
                    "slippage_bps": costs.slippage_bps * 2,
                }
            )
        cost_digest = hashlib.sha256(canonical_json_bytes(costs)).hexdigest()
        aggregate_inputs = []
        for item, fold in zip(value.fold_results, folds.folds, strict=True):
            trace = _read(outputs, item.trace_ref, PerformanceTrace)
            if (
                _read(outputs, trace.fold_ref, Fold) != fold
                or trace.subject_id != subject
                or trace.cost_model_digest != cost_digest
                or trace.metrics != item.metrics
                or trace.round_trip_count != item.metrics.trade_count
                or len(trace.samples) != fold.return_count + 1
            ):
                raise ValueError("replica trace binding differs")
            for index, sample in enumerate(trace.samples):
                if (
                    sample.sample_index != index
                    or sample.kind != ("ENTRY" if index == 0 else "RETURN")
                    or sample.decision_row_ref
                    != fold.decision_row_refs[max(0, index - 1)]
                    or sample.return_row_ref
                    != (None if index == 0 else fold.return_row_refs[index - 1])
                ):
                    raise ValueError("replica sample binding differs")
            rows = tuple(
                to_daily_close(_read(inputs, ref, DailyBar))
                for ref in (
                    fold.decision_row_refs[0],
                    *fold.return_row_refs,
                )
            )
            weights = tuple(Decimal(sample.next_weight) for sample in trace.samples)
            expected = build_research_trace(
                rows,
                weights,
                costs,
                fold_ref=trace.fold_ref,
                subject_id=subject,
                decision_row_refs=fold.decision_row_refs,
                return_row_refs=fold.return_row_refs,
                regimes=labels_by_fold[fold.fold_id],
            )
            if trace != expected:
                raise ValueError(
                    "replica trace arithmetic differs from input rows and weights"
                )
            aggregate_inputs.append((rows, weights))
        if value.aggregate_metrics != calculate_aggregate_performance_metrics(
            tuple(aggregate_inputs), costs
        ):
            raise ValueError("replica aggregate metrics differ from fold traces")

    if isinstance(result, BaselinePack) and isinstance(manifest, BaselineManifest):
        for entry in result.baseline_results:
            value = _read(outputs, entry.scenario_ref, ScenarioResult)
            if (
                entry.baseline_version != "1.0.0"
                or value.aggregate_metrics != entry.aggregate_metrics
            ):
                raise ValueError("replica baseline metrics or version differ")
            scenario(value, entry.baseline_id.value, "BASE", None)
    elif isinstance(result, EvaluationResult) and isinstance(
        manifest, EvaluationManifest
    ):
        spec = _read(inputs, manifest.candidate_spec_ref, CandidateSpec)
        if result.deterministic_trial_keys != deterministic_trial_keys(
            input_set.epoch_id, spec.alpha_id
        ):
            raise ValueError("replica trial inventory differs")
        for value, kind in (
            (result.base, "BASE"),
            (result.double_cost, "DOUBLE_COST"),
            (result.delayed, "DELAYED"),
        ):
            scenario(value, spec.alpha_id, kind, None)
        for value, perturbation in zip(
            result.perturbations, spec.perturbations, strict=True
        ):
            scenario(
                value,
                f"{spec.alpha_id}.{perturbation.perturbation_id}",
                "PERTURBATION",
                perturbation.perturbation_id,
            )
        selection = _read(inputs, manifest.baseline_selection_ref, BaselineSelection)
        robustness = build_robustness(
            result.base,
            (*result.perturbations, result.double_cost, result.delayed),
            selection,
            _ReadbackStore(combined, outputs),
        )
        if (
            result.regimes != robustness.regimes
            or result.capacity != robustness.capacity
        ):
            raise ValueError("replica robustness evidence differs from its traces")
    else:
        raise ValueError("replica result does not match its manifest type")
