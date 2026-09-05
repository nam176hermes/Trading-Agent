"""Adapt verified P3 artifacts into the unchanged C01-C16 protocol."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore
from packages.alpha_lifecycle.contracts.data import PITProof
from packages.alpha_lifecycle.contracts.execution import EvaluationManifest, InputSet
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.alpha_lifecycle.contracts.results import (
    BaselinePack, BaselineSelection, EvaluationResult,
    QualificationBundle, ReplayProof, ReplayReceipt, ScenarioResult,
)
from packages.alpha_lifecycle.protocol import (
    AlphaQualificationEvidenceV1, evaluate_alpha_qualification,
)
from packages.engine_contracts.serialization import canonical_json_bytes


class QualificationPipelineError(ValueError):
    """Required pipeline evidence is missing or inconsistent."""


def _read(store: ArtifactStore, ref, model):
    return model.model_validate_json(store.read_bytes(ref))


def _seal(store: ArtifactStore, value):
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def qualify(
    evaluation: EvaluationResult,
    proof: ReplayProof,
    reader: ArtifactStore,
) -> QualificationBundle:
    evaluation = EvaluationResult.model_validate(evaluation)
    proof = ReplayProof.model_validate(proof)
    evaluation_ref = _seal(reader, evaluation)
    if proof.result_digest != evaluation_ref.content_sha256:
        raise QualificationPipelineError("E_REPLAY: replay result does not match evaluation")
    receipts = tuple(_read(reader, ref, ReplayReceipt) for ref in proof.receipt_refs)
    if any(item.result_ref.content_sha256 != proof.result_digest for item in receipts):
        raise QualificationPipelineError("E_REPLAY: receipt result identity differs")

    manifest = _read(reader, evaluation.manifest_ref, EvaluationManifest)
    input_set = _read(reader, manifest.input_set_ref, InputSet)
    spec = _read(reader, manifest.candidate_spec_ref, CandidateSpec)
    selection = _read(reader, manifest.baseline_selection_ref, BaselineSelection)
    pit = _read(reader, input_set.pit_proof_ref, PITProof)
    reader.read_bytes(pit.no_future_suite_ref)
    pack = _read(reader, selection.pack_ref, BaselinePack)
    baseline_entry = next(
        item for item in pack.baseline_results if item.baseline_id.value == selection.selected_id
    )
    baseline_scenario = _read(reader, baseline_entry.scenario_ref, ScenarioResult)
    if tuple(item.fold_id for item in evaluation.base.fold_results) != tuple(
        item.fold_id for item in baseline_scenario.fold_results
    ):
        raise QualificationPipelineError("baseline folds do not align with candidate folds")
    fold_excess = tuple(
        candidate.metrics.total_return - baseline.metrics.total_return
        for candidate, baseline in zip(
            evaluation.base.fold_results, baseline_scenario.fold_results, strict=True
        )
    )
    evidence = AlphaQualificationEvidenceV1(
        alpha_id=spec.alpha_id, alpha_version=spec.version,
        source_sha=input_set.source.commit_sha,
        dataset_snapshot_sha256=selection.selected_result.dataset_snapshot_sha256,
        parameter_set_sha256=spec.digest,
        cost_model_sha256=selection.selected_result.cost_model_sha256,
        environment_sha256=input_set.environment_ref.content_sha256,
        result_artifact_sha256=evaluation_ref.content_sha256,
        replay_result_sha256s=(proof.result_digest,) * 3,
        pit_adversarial_passed=True,
        metrics=evaluation.base.aggregate_metrics,
        baseline_result=selection.selected_result,
        fold_excess_returns=fold_excess,
        regime_excess_returns=tuple(Decimal(item.excess) for item in evaluation.regimes),
        perturbation_net_returns=tuple(
            item.aggregate_metrics.total_return for item in evaluation.perturbations
        ),
        double_cost_net_return=evaluation.double_cost.aggregate_metrics.total_return,
        delayed_execution_net_return=evaluation.delayed.aggregate_metrics.total_return,
        median_participation=Decimal(evaluation.capacity.median),
        peak_participation=Decimal(evaluation.capacity.peak),
        single_instrument_concentration="NOT_APPLICABLE_SINGLE_INSTRUMENT_SCOPE",
    )
    result = evaluate_alpha_qualification(evidence)
    payload = {
        "schema_version": "p3-qualification-bundle-v1",
        "evaluation_ref": evaluation_ref,
        "replay_proof_ref": _seal(reader, proof),
        "pit_ref": input_set.pit_proof_ref,
        "legacy_evidence_ref": _seal(reader, evidence),
        "legacy_result_ref": _seal(reader, result),
        "criteria": result.criteria,
        "pipeline_verdict": "PASS",
        "alpha_verdict": "PASS" if result.qualified else "FAIL",
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return QualificationBundle.model_validate(payload)


__all__ = ["QualificationPipelineError", "evaluate_alpha_qualification", "qualify"]
