"""Run the frozen B0-B4 baseline campaign over one sealed input set."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Protocol, TypeVar

from pydantic import BaseModel

from packages.alpha_lifecycle.baselines import BaselineId, BaselineResultV1, baseline_weights_with_reset
from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence, FoldManifest
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
from packages.alpha_lifecycle.contracts.results import (
    BaselineEntry, BaselinePack, BaselineSelection, FoldResult, RegimeThreshold, ScenarioResult,
)
from packages.alpha_lifecycle.data_view import to_daily_close
from packages.alpha_lifecycle.execution_trace import build_research_trace, suppress_terminal_signal
from packages.alpha_lifecycle.metrics import calculate_aggregate_performance_metrics
from packages.alpha_lifecycle.regimes import assign_regimes
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class ArtifactStore(Protocol):
    def read_bytes(self, ref: ArtifactRefV1) -> bytes: ...
    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1: ...


Model = TypeVar("Model", bound=BaseModel)


def _read(store: ArtifactStore, ref: ArtifactRefV1, model: type[Model]) -> Model:
    return model.model_validate_json(store.read_bytes(ref))


def _seal(store: ArtifactStore, value: BaseModel) -> ArtifactRefV1:
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def run_baseline_pack(
    manifest: BaselineManifest, reader: ArtifactStore
) -> BaselinePack:
    manifest = BaselineManifest.model_validate(manifest)
    input_set = _read(reader, manifest.input_set_ref, InputSet)
    fold_manifest = _read(reader, input_set.fold_manifest_ref, FoldManifest)
    dataset = _read(reader, fold_manifest.dataset_evidence_ref, DatasetEvidence)
    threshold = Decimal(_read(reader, input_set.regime_threshold_ref, RegimeThreshold).threshold)
    bars = tuple(_read(reader, ref, DailyBar) for ref in dataset.row_refs)
    by_day = {bar.date: (bar, ref) for bar, ref in zip(bars, dataset.row_refs, strict=True)}

    entries = []
    for baseline_id in BaselineId:
        fold_results = []
        aggregate_inputs = []
        for fold in fold_manifest.folds:
            context = tuple(
                pair for day, pair in by_day.items()
                if fold.context_start <= day <= fold.return_end_range.end
            )
            context = tuple(sorted(context, key=lambda pair: pair[0].date))
            context_rows = tuple(to_daily_close(pair[0]) for pair in context)
            score_start = to_daily_close(by_day[fold.decision_start][0]).closed_at
            weights = baseline_weights_with_reset(
                baseline_id, context_rows, score_start=score_start
            )
            weights = suppress_terminal_signal(weights)
            scored_rows = context_rows[-len(weights):]
            all_labels = assign_regimes(context_rows, threshold)
            labels = all_labels[-len(weights):]
            fold_ref = _seal(reader, fold)
            trace = build_research_trace(
                scored_rows, weights, input_set.cost_model,
                fold_ref=fold_ref, subject_id=baseline_id.value,
                decision_row_refs=fold.decision_row_refs,
                return_row_refs=fold.return_row_refs, regimes=labels,
            )
            trace_ref = _seal(reader, trace)
            fold_payload = {"fold_id": fold.fold_id, "trace_ref": trace_ref, "metrics": trace.metrics}
            fold_payload["digest"] = _digest(fold_payload)
            fold_results.append(FoldResult.model_validate(fold_payload))
            aggregate_inputs.append((scored_rows, weights))
        aggregate = calculate_aggregate_performance_metrics(tuple(aggregate_inputs), input_set.cost_model)
        scenario_payload = {
            "schema_version": "p3-scenario-result-v1", "scenario": "BASE",
            "perturbation_id": None, "fold_results": tuple(fold_results),
            "aggregate_metrics": aggregate,
        }
        scenario_payload["digest"] = _digest(scenario_payload)
        scenario = ScenarioResult.model_validate(scenario_payload)
        entries.append(BaselineEntry(
            baseline_id=baseline_id, baseline_version="1.0.0",
            scenario_ref=_seal(reader, scenario), aggregate_metrics=aggregate,
        ))
    payload = {
        "schema_version": "p3-baseline-pack-v1",
        "input_set_ref": manifest.input_set_ref,
        "baseline_results": tuple(entries),
    }
    payload["digest"] = _digest(payload)
    return BaselinePack.model_validate(payload)


def select_baseline(
    pack: BaselinePack,
    *,
    replay_proof_ref: ArtifactRefV1,
    selection_policy_digest: str,
    snapshot_digest: str,
    cost_model_digest: str,
    store: ArtifactStore,
) -> BaselineSelection:
    winner = max(
        pack.baseline_results,
        key=lambda item: (item.aggregate_metrics.total_return, -list(BaselineId).index(item.baseline_id)),
    )
    metrics_digest = hashlib.sha256(canonical_json_bytes(winner.aggregate_metrics)).hexdigest()
    result = BaselineResultV1(
        baseline_id=winner.baseline_id, baseline_version=winner.baseline_version,
        dataset_snapshot_sha256=snapshot_digest, cost_model_sha256=cost_model_digest,
        metrics_sha256=metrics_digest, total_return=winner.aggregate_metrics.total_return,
    )
    payload = {
        "schema_version": "p3-baseline-selection-v1", "pack_ref": _seal(store, pack),
        "selected_id": winner.baseline_id.value, "selected_result": result,
        "selection_policy_digest": selection_policy_digest,
        "baseline_replay_proof_ref": replay_proof_ref,
    }
    payload["digest"] = _digest(payload)
    return BaselineSelection.model_validate(payload)


__all__ = ["ArtifactStore", "run_baseline_pack", "select_baseline"]
