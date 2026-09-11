"""Run the frozen B0-B4 baseline campaign over one sealed input set."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from packages.alpha_lifecycle.baselines import BaselineId, BaselineResultV1, baseline_weights_with_reset
from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence, FoldManifest, PITProof
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet, EnvironmentIdentity
from packages.alpha_lifecycle.contracts.results import (
    BaselineEntry, BaselinePack, BaselineSelection, FoldResult, RegimeThreshold, ScenarioResult,
    ReplayReceipt, ReplayProof,
)
from packages.alpha_lifecycle.data_view import to_daily_close
from packages.alpha_lifecycle.execution_trace import build_research_trace, suppress_terminal_signal
from packages.alpha_lifecycle.metrics import calculate_aggregate_performance_metrics
from packages.alpha_lifecycle.regimes import assign_regimes
from packages.alpha_lifecycle.replay import SandboxExecutor, run_replays, validate_replay_proof
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class ArtifactStore(Protocol):
    def read_bytes(self, ref: ArtifactRefV1, /) -> bytes: ...
    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1: ...


class ReadbackStore:
    """Recompute with existing builders while requiring already retained bytes."""

    def __init__(self, reader: ArtifactStore, outputs: ArtifactStore) -> None:
        self._reader, self._outputs = reader, outputs

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        return self._reader.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        digest = hashlib.sha256(value).hexdigest()
        ref = ArtifactRefV1(content_sha256=digest, size_bytes=len(value),
            media_type=media_type, locator=f"{digest}.blob")
        if self._outputs.read_bytes(ref) != value:
            raise ValueError("recomputation differs from retained artifacts")
        return ref


Model = TypeVar("Model", bound=BaseModel)


def _read(store: ArtifactStore, ref: ArtifactRefV1, model: type[Model]) -> Model:
    raw = store.read_bytes(ref)
    value = model.model_validate_json(raw)
    if canonical_json_bytes(value) != raw:
        raise ValueError('research input artifact is not canonical')
    return value


def _seal(store: ArtifactStore, value: BaseModel) -> ArtifactRefV1:
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()


def validate_research_inputs(input_set_ref: ArtifactRefV1, reader: ArtifactStore):
    inputs = _read(reader,input_set_ref,InputSet)
    folds = _read(reader,inputs.fold_manifest_ref,FoldManifest)
    dataset = _read(reader,inputs.dataset_evidence_ref,DatasetEvidence)
    threshold = _read(reader,inputs.regime_threshold_ref,RegimeThreshold)
    pit = _read(reader,inputs.pit_proof_ref,PITProof)
    if (
        dataset.segment != 'RESEARCH' or folds.mode != 'OOS'
        or folds.dataset_evidence_ref != inputs.dataset_evidence_ref
        or folds.static_policy_digest != inputs.policy_digest
        or any(fold.snapshot_ref != dataset.snapshot_ref for fold in folds.folds)
        or threshold.policy_digest != inputs.policy_digest
        or threshold.training_dataset_ref != inputs.dataset_evidence_ref
        or not dataset.date_range.start <= threshold.training_range.start <= threshold.training_range.end <= dataset.date_range.end
        or threshold.training_range.end >= folds.folds[0].decision_start
        or pit.dataset_ref != inputs.dataset_evidence_ref
        or pit.fold_manifest_ref != inputs.fold_manifest_ref
        or pit.vintage_class != dataset.vintage_class or pit.limitations != dataset.limitations
    ):
        raise ValueError('research input graph binding differs')
    reader.read_bytes(pit.revision_proof_ref)
    reader.read_bytes(pit.no_future_suite_ref)
    return inputs,folds,dataset,threshold


def run_baseline_pack(
    manifest: BaselineManifest, reader: ArtifactStore
) -> BaselinePack:
    manifest = BaselineManifest.model_validate(manifest)
    input_set, fold_manifest, dataset, regime = validate_research_inputs(manifest.input_set_ref,reader)
    threshold = Decimal(regime.threshold)
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


def execute_baseline_manifest(
    manifest_ref: ArtifactRefV1, executor: SandboxExecutor, *,
    logical_trial_id: str, output_root: Path,
) -> BaselineSelection:
    """Select B0-B4 only after parent-observed replay of the baseline manifest."""
    manifest = _read(executor,manifest_ref,BaselineManifest)
    inputs, _, dataset, _ = validate_research_inputs(manifest.input_set_ref,executor)
    proof = run_replays(manifest_ref,executor,logical_trial_id=logical_trial_id,output_root=output_root)
    receipt = _read(executor,proof.receipt_refs[0],ReplayReceipt)
    pack = _read(executor,receipt.result_ref,BaselinePack)
    if pack.input_set_ref != manifest.input_set_ref:
        raise ValueError('baseline replay result belongs to another InputSet')
    return select_baseline(pack,replay_proof_ref=_seal(executor,proof),
        selection_policy_digest=inputs.policy_digest,
        snapshot_digest=dataset.snapshot_ref.content_sha256,
        cost_model_digest=hashlib.sha256(canonical_json_bytes(inputs.cost_model)).hexdigest(),store=executor)


def baseline_manifest_ref(input_set_ref: ArtifactRefV1) -> ArtifactRefV1:
    """Reconstruct the fixed baseline manifest identity without creating artifacts."""
    value = dict(schema_version='p3-baseline-manifest-v1',input_set_ref=input_set_ref,
        required_baselines=tuple(item.value for item in BaselineId))
    value['digest'] = _digest(value)
    raw = canonical_json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    return ArtifactRefV1(content_sha256=digest,size_bytes=len(raw),media_type='application/json',locator=f'{digest}.blob')


def validate_baseline_selection(selection_ref: ArtifactRefV1, input_set_ref: ArtifactRefV1,
    reader: ArtifactStore) -> BaselineSelection:
    inputs,_,dataset,_ = validate_research_inputs(input_set_ref,reader)
    selection = _read(reader,selection_ref,BaselineSelection)
    pack = _read(reader,selection.pack_ref,BaselinePack)
    proof = _read(reader,selection.baseline_replay_proof_ref,ReplayProof)
    environment = _read(reader,inputs.environment_ref,EnvironmentIdentity)
    manifest_ref = baseline_manifest_ref(input_set_ref)
    _read(reader,manifest_ref,BaselineManifest)
    validate_replay_proof(proof,manifest_ref=manifest_ref,result_ref=selection.pack_ref,
        source=inputs.source,environment_ref=inputs.environment_ref,
        sandbox_policy_digest=environment.sandbox_policy_digest,reader=reader)
    expected = select_baseline(pack,replay_proof_ref=selection.baseline_replay_proof_ref,
        selection_policy_digest=inputs.policy_digest,snapshot_digest=dataset.snapshot_ref.content_sha256,
        cost_model_digest=hashlib.sha256(canonical_json_bytes(inputs.cost_model)).hexdigest(),store=ReadbackStore(reader,reader))
    if pack.input_set_ref != input_set_ref or selection != expected:
        raise ValueError('baseline selection differs from its frozen input or result')
    return selection


__all__ = ["baseline_manifest_ref", "ArtifactStore", "ReadbackStore", "execute_baseline_manifest", "run_baseline_pack", "select_baseline", "validate_baseline_selection", "validate_research_inputs"]
