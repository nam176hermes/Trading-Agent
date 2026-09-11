"""Adapt verified P3 artifacts into the unchanged C01-C16 protocol."""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, ReadbackStore, _read
from packages.alpha_lifecycle.contracts.data import PITProof
from packages.alpha_lifecycle.contracts.execution import EvaluationManifest, InputSet, EnvironmentIdentity
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from packages.alpha_lifecycle.contracts.results import (
    BaselinePack, BaselineSelection, EvaluationResult,
    QualificationBundle, ReplayProof, ScenarioResult,
)
from packages.alpha_lifecycle.protocol import (
    AlphaQualificationEvidenceV1, evaluate_alpha_qualification,
)
from packages.engine_contracts.serialization import canonical_json_bytes


class QualificationPipelineError(ValueError):
    """Required pipeline evidence is missing or inconsistent."""


def _seal(store: ArtifactStore, value):
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def qualify(
    evaluation: EvaluationResult,
    proof: ReplayProof,
    reader: ArtifactStore,
) -> QualificationBundle:
    with localcontext(prec=50, rounding=ROUND_HALF_EVEN):
        evaluation = EvaluationResult.model_validate(evaluation)
        proof = ReplayProof.model_validate(proof)
        # Validation reads retained bytes; rejected inputs must not publish artifacts.
        evaluation_ref = ReadbackStore(reader,reader).put_bytes(canonical_json_bytes(evaluation),media_type="application/json")
        manifest = _read(reader,evaluation.manifest_ref,EvaluationManifest)
        input_set = _read(reader,manifest.input_set_ref,InputSet)
        environment = _read(reader,input_set.environment_ref,EnvironmentIdentity)
        from packages.alpha_lifecycle.replay import validate_replay_proof
        validate_replay_proof(proof,manifest_ref=evaluation.manifest_ref,result_ref=evaluation_ref,
            source=input_set.source,environment_ref=input_set.environment_ref,
            sandbox_policy_digest=environment.sandbox_policy_digest,reader=reader)
        from packages.alpha_lifecycle.publication import validate_evaluation_registration
        validate_evaluation_registration(manifest,store=ReadbackStore(reader,reader))
        from packages.alpha_lifecycle.evaluation import evaluate
        if evaluate(manifest,ReadbackStore(reader,reader)) != evaluation:
            raise QualificationPipelineError("candidate result differs from frozen parent recomputation")
        spec = _read(reader, manifest.candidate_spec_ref, CandidateSpec)
        selection = _read(reader, manifest.baseline_selection_ref, BaselineSelection)
        pit = _read(reader, input_set.pit_proof_ref, PITProof)
        from packages.alpha_lifecycle.pit_suite import validate_pit_suite_receipt
        validate_pit_suite_receipt(pit.no_future_suite_ref,source=input_set.source,store=reader)
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


def prepare_oos_evidence(intent, evaluation, proof, *, store: ArtifactStore):
    """Reconstruct the fixed OOS decision for publication and downstream readback."""
    from packages.alpha_lifecycle.contracts.lifecycle import ExpectedHead, PrePublicationEvidence
    from packages.alpha_lifecycle.baseline_campaign import _read
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest,InputSet
    from packages.alpha_lifecycle.contracts.results import EvaluationResult,ReplayProof,ReplayReceipt
    from packages.alpha_lifecycle.lifecycle import plan_transition,read_registry_event
    from packages.alpha_lifecycle.operation_input import P3OperationInput,CandidateOOSInput
    from packages.alpha_lifecycle.protocol import AlphaQualificationResultV1
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus,QualificationDecision
    intent=P3OperationInput.model_validate(intent)
    evaluation=EvaluationResult.model_validate(evaluation)
    proof=ReplayProof.model_validate(proof)
    if not isinstance(intent.body,CandidateOOSInput) or intent.body.evaluation_manifest_ref!=evaluation.manifest_ref:
        raise ValueError('OOS result does not belong to the exact operation intent')
    manifest=_read(store,evaluation.manifest_ref,EvaluationManifest)
    inputs=_read(store,manifest.input_set_ref,InputSet)
    head=read_registry_event(store,manifest.candidate_head_ref)
    if (manifest.input_set_ref!=intent.input_set_ref or intent.allowed_alpha_ids!=(head.record.alpha_id,)
        or any(_read(store,ref, ReplayReceipt).logical_trial_id!=intent.workflow_operation for ref in proof.receipt_refs)):
        raise ValueError('OOS input, candidate or replay operation differs')
    bundle=qualify(evaluation,proof,store)
    result=_read(store,bundle.legacy_result_ref,AlphaQualificationResultV1)
    bundle_ref=store.put_bytes(canonical_json_bytes(bundle),media_type='application/json')
    record_ref=store.put_bytes(canonical_json_bytes(head.record),media_type='application/json')
    value=dict(schema_version='p3-pre-publication-evidence-v1',stage='RESEARCH_DECISION',
        input_set_ref=intent.input_set_ref,baseline_selection_ref=manifest.baseline_selection_ref,
        qualification_bundle_ref=bundle_ref,exit_result_ref=None,candidate_record_refs=(record_ref,))
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    evidence=PrePublicationEvidence.model_validate_json(canonical_json_bytes(value))
    passed=bundle.alpha_verdict=='PASS'
    metrics=hashlib.sha256(canonical_json_bytes(evaluation.base.aggregate_metrics)).hexdigest() if passed else None
    record=head.record.model_copy(update=dict(lifecycle_status=AlphaLifecycleStatus.RESEARCHED,
        qualification_decision=QualificationDecision.PASS if passed else QualificationDecision.FAIL,
        qualification_reason='C01-C16 passed' if passed else ','.join(result.failure_codes),
        metrics_sha256=metrics,
        robustness_sha256=bundle_ref.content_sha256 if passed else None))
    researched=plan_transition(record,head,evidence)
    terminal=plan_transition(record.model_copy(update=dict(lifecycle_status=AlphaLifecycleStatus.OOS_PASS if passed else AlphaLifecycleStatus.REJECTED)),researched,evidence)
    expected=ExpectedHead(alpha_id=head.record.alpha_id,version=head.record.version,
        sequence=head.sequence,event_digest=head.event_sha256)
    return (researched,terminal),(expected,),evidence,inputs.epoch_id


__all__ = ["QualificationPipelineError", "evaluate_alpha_qualification", "qualify"]
