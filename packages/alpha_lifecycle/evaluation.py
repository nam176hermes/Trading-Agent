"""Deterministic calculation boundary for one preregistered P3 candidate."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.candidates import run_candidate
from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence, FoldManifest, PITProof
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity, EvaluationManifest, InputSet
from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.alpha_lifecycle.contracts.results import (
    BaselineSelection, EvaluationResult, FoldResult, RegimeThreshold, ScenarioResult,
)
from packages.alpha_lifecycle.data_view import to_daily_close
from packages.alpha_lifecycle.execution_trace import build_research_trace
from packages.alpha_lifecycle.metrics import CostModelV1, calculate_aggregate_performance_metrics
from packages.alpha_lifecycle.regimes import assign_regimes
from packages.alpha_lifecycle.registry import AlphaRegistryEventV1
from packages.alpha_lifecycle.robustness import build_robustness, delayed_weights
from packages.alpha_lifecycle.trials import deterministic_trial_keys
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class EvaluationError(ValueError):
    """Evaluation inputs are not one preregistered, source-bound computation."""


def _read(store: ArtifactStore, ref: ArtifactRefV1, model):
    return model.model_validate_json(store.read_bytes(ref))


def _seal(store: ArtifactStore, value):
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _head(store: ArtifactStore, ref: ArtifactRefV1) -> AlphaRegistryEventV1:
    payload = json.loads(store.read_bytes(ref))
    return AlphaRegistryEventV1.model_validate_json(canonical_json_bytes({
        **payload, "event_sha256": ref.content_sha256, "artifact": ref,
    }))


def _scenario(
    *,
    scenario: str,
    perturbation_id: str | None,
    spec: CandidateSpec,
    bars: tuple[DailyBar, ...],
    folds: FoldManifest,
    threshold: Decimal,
    costs: CostModelV1,
    delayed: bool,
    store: ArtifactStore,
) -> ScenarioResult:
    by_day = {bar.date: bar for bar in bars}
    fold_results = []
    aggregate_inputs = []
    for fold in folds.folds:
        weights_int = run_candidate(spec, bars, fold, perturbation_id=perturbation_id)
        if delayed:
            weights_int = delayed_weights(weights_int)
        weights = tuple(Decimal(value) for value in weights_int)
        scored_bars = tuple(
            bar for bar in bars
            if fold.decision_start <= bar.date <= fold.return_end_range.end
        )
        rows = tuple(to_daily_close(bar) for bar in scored_bars)
        context_bars = tuple(
            bar for bar in bars
            if fold.context_start <= bar.date <= fold.return_end_range.end
        )
        labels = assign_regimes(tuple(to_daily_close(bar) for bar in context_bars), threshold)[-len(rows):]
        trace = build_research_trace(
            rows, weights, costs, fold_ref=_seal(store, fold),
            subject_id=spec.alpha_id if perturbation_id is None else f"{spec.alpha_id}.{perturbation_id}",
            decision_row_refs=fold.decision_row_refs,
            return_row_refs=fold.return_row_refs, regimes=labels,
        )
        trace_ref = _seal(store, trace)
        result_payload = {"fold_id": fold.fold_id, "trace_ref": trace_ref, "metrics": trace.metrics}
        result_payload["digest"] = _digest(result_payload)
        fold_results.append(FoldResult.model_validate(result_payload))
        aggregate_inputs.append((rows, weights))
    payload = {
        "schema_version": "p3-scenario-result-v1", "scenario": scenario,
        "perturbation_id": perturbation_id, "fold_results": tuple(fold_results),
        "aggregate_metrics": calculate_aggregate_performance_metrics(tuple(aggregate_inputs), costs),
    }
    payload["digest"] = _digest(payload)
    return ScenarioResult.model_validate(payload)


def evaluate(manifest: EvaluationManifest, reader: ArtifactStore) -> EvaluationResult:
    manifest = EvaluationManifest.model_validate(manifest)
    input_set = _read(reader, manifest.input_set_ref, InputSet)
    _read(reader, input_set.environment_ref, EnvironmentIdentity)
    _read(reader, input_set.pit_proof_ref, PITProof)
    spec = _read(reader, manifest.candidate_spec_ref, CandidateSpec)
    selection = _read(reader, manifest.baseline_selection_ref, BaselineSelection)
    registration = _read(reader, manifest.registration_proof_ref, RegistrationProof)
    if (
        registration.input_set_ref != manifest.input_set_ref
        or registration.baseline_selection_ref != manifest.baseline_selection_ref
        or manifest.candidate_head_ref not in registration.candidate_head_refs
    ):
        raise EvaluationError("registration does not bind the evaluation manifest")
    head = _head(reader, manifest.candidate_head_ref)
    if (
        head.record.alpha_id != spec.alpha_id
        or head.record.version != spec.version
        or head.record.parameter_set_sha256 != spec.digest
        or head.record.source_sha != input_set.source.commit_sha
        or head.record.baseline_id != selection.selected_id
    ):
        raise EvaluationError("candidate head identity differs from the sealed input")
    folds = _read(reader, input_set.fold_manifest_ref, FoldManifest)
    dataset = _read(reader, folds.dataset_evidence_ref, DatasetEvidence)
    if head.record.dataset_snapshot_sha256 != dataset.snapshot_ref.content_sha256:
        raise EvaluationError("candidate head dataset identity differs")
    bars = tuple(_read(reader, ref, DailyBar) for ref in dataset.row_refs)
    threshold = Decimal(_read(reader, input_set.regime_threshold_ref, RegimeThreshold).threshold)
    base = _scenario(
        scenario="BASE", perturbation_id=None, spec=spec, bars=bars, folds=folds,
        threshold=threshold, costs=input_set.cost_model, delayed=False, store=reader,
    )
    perturbations = tuple(
        _scenario(
            scenario="PERTURBATION", perturbation_id=item.perturbation_id,
            spec=spec, bars=bars, folds=folds, threshold=threshold,
            costs=input_set.cost_model, delayed=False, store=reader,
        )
        for item in spec.perturbations
    )
    double_costs = input_set.cost_model.model_copy(update={
        "fee_bps": input_set.cost_model.fee_bps * 2,
        "spread_bps": input_set.cost_model.spread_bps * 2,
        "slippage_bps": input_set.cost_model.slippage_bps * 2,
    })
    double = _scenario(
        scenario="DOUBLE_COST", perturbation_id=None, spec=spec, bars=bars, folds=folds,
        threshold=threshold, costs=double_costs, delayed=False, store=reader,
    )
    delayed = _scenario(
        scenario="DELAYED", perturbation_id=None, spec=spec, bars=bars, folds=folds,
        threshold=threshold, costs=input_set.cost_model, delayed=True, store=reader,
    )
    robustness = build_robustness(
        base, (*perturbations, double, delayed), selection, reader
    )
    payload = {
        "schema_version": "p3-evaluation-result-v1", "manifest_ref": _seal(reader, manifest),
        "base": base, "double_cost": double, "delayed": delayed,
        "perturbations": perturbations, "regimes": robustness.regimes,
        "capacity": robustness.capacity,
        "deterministic_trial_keys": deterministic_trial_keys(input_set.epoch_id, spec.alpha_id),
    }
    payload["digest"] = _digest(payload)
    return EvaluationResult.model_validate(payload)


__all__ = ["EvaluationError", "evaluate"]
