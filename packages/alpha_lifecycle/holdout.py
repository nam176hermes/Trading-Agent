"""Fail-closed validation for one authorized historical holdout access."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Literal

from packages.alpha_lifecycle.baseline_campaign import validate_research_inputs
from packages.alpha_lifecycle.replica_store import ArtifactStore
from packages.alpha_lifecycle.replay import SandboxExecutor
from packages.alpha_lifecycle.contracts.execution import HoldoutManifest
from packages.alpha_lifecycle.contracts.authority import (
    CustodyRecord,
    HoldoutRequest,
    PrimarySelection,
    RunAuthorization,
)
from packages.alpha_lifecycle.contracts.base import DigestModel, SourceIdentity
from packages.alpha_lifecycle.operation_input import HoldoutInput, P3OperationInput
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class HoldoutOperationResult(DigestModel):
    """Internal parent output; references do not grant holdout access."""

    schema_version: Literal['p3-holdout-operation-result-v1']
    holdout_request_ref: ArtifactRefV1
    holdout_manifest_ref: ArtifactRefV1
    holdout_evaluation_ref: ArtifactRefV1
    holdout_replay_ref: ArtifactRefV1
    executable_ref: ArtifactRefV1
    baseline_executable_ref: ArtifactRefV1


def execute_holdout_manifest(
    request_ref: ArtifactRefV1, manifest_ref: ArtifactRefV1, instrument_spec_ref: ArtifactRefV1,
    executor: SandboxExecutor, *, output_root: Path,
) -> HoldoutOperationResult:
    """Calculate an already released view; the caller owns consumption and custody.

    Request expiry is checked at durable consumption, not against completion time.
    This helper neither authenticates that consumption nor makes data readable.
    """
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest,InstrumentSpec,EnvironmentIdentity
    from packages.alpha_lifecycle.contracts.results import HoldoutEvaluationResult,ReplayReceipt
    from packages.alpha_lifecycle.evaluation import evaluate_holdout
    from packages.alpha_lifecycle.executable_reference import run_executable_reference,run_selected_baseline_reference
    from packages.alpha_lifecycle.pit_evidence import _reference
    from packages.alpha_lifecycle.replica_store import ReadbackStore,_read
    from packages.alpha_lifecycle.replay import run_replays,validate_replay_proof
    for ref in (request_ref,manifest_ref,instrument_spec_ref):
        _reference(ref,65536)
    request=_read(executor,request_ref,HoldoutRequest)
    manifest=_read(executor,manifest_ref,HoldoutManifest)
    if (request.logical_trial_id!='p3-holdout-primary-v1' or request.source!=manifest.source
        or request.primary_selection_ref!=manifest.primary_selection_ref
        or request.policy_digest!=manifest.policy_digest):
        raise ValueError('holdout request differs from its calculation manifest')
    spec=_read(executor,instrument_spec_ref,InstrumentSpec)
    _reference(manifest.environment_ref,65536)
    environment=_read(executor,manifest.environment_ref,EnvironmentIdentity)
    proof=run_replays(manifest_ref,executor,logical_trial_id=request.logical_trial_id,output_root=output_root)
    receipt=_read(executor,proof.receipt_refs[0],ReplayReceipt)
    evaluation=_read(executor,receipt.result_ref,HoldoutEvaluationResult)
    validate_replay_proof(proof,manifest_ref=manifest_ref,result_ref=receipt.result_ref,
        source=manifest.source,environment_ref=manifest.environment_ref,
        sandbox_policy_digest=environment.sandbox_policy_digest,reader=executor)
    if evaluation!=evaluate_holdout(manifest,spec,ReadbackStore(executor,executor)):
        raise ValueError('holdout evaluation differs from retained replay artifacts')
    primary=run_executable_reference(manifest,spec,executor)
    baseline=run_selected_baseline_reference(manifest,spec,executor)
    def seal(value):
        return executor.put_bytes(canonical_json_bytes(value),media_type='application/json')
    payload={
        'schema_version':'p3-holdout-operation-result-v1','holdout_request_ref':request_ref,
        'holdout_manifest_ref':manifest_ref,'holdout_evaluation_ref':receipt.result_ref,
        'holdout_replay_ref':seal(proof),'executable_ref':seal(primary),
        'baseline_executable_ref':seal(baseline),
    }
    payload['digest']=hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return HoldoutOperationResult.model_validate(payload)


def derive_holdout_request(
    intent: P3OperationInput, authorization: RunAuthorization, *, expected_source: SourceIdentity,
) -> HoldoutRequest:
    """Pure post-review projection; authenticating custody and consuming access are separate."""
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    intent=P3OperationInput.model_validate(intent)
    authorization=RunAuthorization.model_validate(authorization)
    source=SourceIdentity.model_validate(expected_source)
    if not isinstance(intent.body,HoldoutInput):
        raise ValueError('holdout request requires the fixed holdout operation input')
    build_alpha_campaign_payload(authorization,source,'p3-holdout-primary-v1',operation_input=intent)
    body=intent.body
    encoded=authorization.model_dump(mode='json')
    payload={
        'schema_version':'p3-holdout-request-v1','source':source,
        'primary_selection_ref':body.primary_selection_ref,'policy_digest':body.policy_digest,
        'holdout_input_set_ref':body.holdout_input_set_ref,'custody_record_ref':body.custody_record_ref,
        'review_ref':authorization.review_ref,'issued_at':encoded['issued_at'],
        'expires_at':encoded['expires_at'],'logical_trial_id':intent.workflow_operation,
        'authority':authorization.authority,
    }
    payload['digest']=hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return HoldoutRequest.model_validate(payload)


def validate_holdout_access(
    request: HoldoutRequest,
    selection: PrimarySelection,
    custody: CustodyRecord,
    *,
    expected_source: SourceIdentity,
    now: datetime,
) -> None:
    request = HoldoutRequest.model_validate(request)
    selection = PrimarySelection.model_validate(selection)
    custody = CustodyRecord.model_validate(custody)
    for reference,value in ((request.primary_selection_ref,selection),(request.custody_record_ref,custody)):
        raw=canonical_json_bytes(value)
        if (reference.media_type!='application/json' or reference.size_bytes!=len(raw)
            or reference.content_sha256!=hashlib.sha256(raw).hexdigest()):
            raise ValueError('holdout request binding differs from primary or custody')
    if selection.outcome != "SELECTED":
        raise ValueError("holdout requires exactly one selected primary")
    if request.source != expected_source or not request.issued_at <= now < request.expires_at:
        raise ValueError("holdout source or request validity is stale")
    if request.policy_digest != selection.selection_policy_digest:
        raise ValueError("holdout policy differs from primary selection")
    if custody.research_identity == custody.custodian_identity:
        raise ValueError("holdout custody separation is invalid")


def validate_holdout_operation_input(intent: P3OperationInput, authorization: RunAuthorization, *,
    expected_source: SourceIdentity, store: ArtifactStore, now: datetime,
) -> tuple[HoldoutRequest, HoldoutManifest]:
    """Reconstruct retained research and holdout metadata, never release authority.

    The protected consumer must authenticate reviewer/custodian principals,
    commitment, qualification and durable consumption before mounting plaintext.
    """
    from datetime import date
    from packages.alpha_lifecycle.authority import validate_operation_review
    from packages.alpha_lifecycle.baseline_campaign import validate_baseline_selection
    from packages.alpha_lifecycle.contracts.authority import FamilyReview,IntegrationReceipt
    from packages.alpha_lifecycle.contracts.data import FoldManifest,PITProof
    from packages.alpha_lifecycle.contracts.execution import InputSet,HoldoutManifest,InstrumentSpec,EnvironmentIdentity,EvaluationManifest
    from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof,PublicationReceipt,CampaignClosureReport
    from packages.alpha_lifecycle.contracts.policy import CandidateSpec
    from packages.alpha_lifecycle.contracts.results import QualificationBundle,EvaluationResult
    from packages.alpha_lifecycle.executable_reference import _dataset,ResearchInstrument
    from packages.alpha_lifecycle.folds import build_holdout_fold_manifest
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.alpha_lifecycle.pit_evidence import _ReadBudget,_reference
    from packages.alpha_lifecycle.primary_selection import select_primary,validate_family_review_approval
    from packages.alpha_lifecycle.publication import build_registration_proof
    from packages.alpha_lifecycle.replica_store import ReadbackStore,_read

    intent=P3OperationInput.model_validate(intent)
    authorization=RunAuthorization.model_validate(authorization)
    request=derive_holdout_request(intent,authorization,expected_source=expected_source)
    body=intent.body
    assert isinstance(body,HoldoutInput)
    for name in type(body).model_fields:
        if name.endswith('_ref'):
            _reference(getattr(body,name),2097152 if name in
                {'holdout_dataset_ref','context_dataset_ref','buffer_ref'} else 65536)
    _reference(intent.input_set_ref,65536)
    unreadable=set()
    class MetadataReader(_ReadBudget):
        def read_bytes(self,ref):
            ref=ArtifactRefV1.model_validate(ref)
            if ref.content_sha256 in unreadable or ref.size_bytes>67108864:
                raise ValueError('holdout metadata cannot read unreleased artifacts or oversized objects')
            return super().read_bytes(ref)
    budget=MetadataReader(store)
    reader=ReadbackStore(budget,budget)
    outer=validate_operation_review(authorization,expected_source,intent,reader,now=now)
    holdout=_dataset(reader,body.holdout_dataset_ref,'HOLDOUT',date(2025,9,1),date(2026,8,31))
    unreadable.update(ref.content_sha256 for ref in (*holdout.row_refs,holdout.snapshot_ref))
    buffer=_dataset(reader,body.buffer_ref,'BUFFER',date(2026,9,1),date(2026,9,1))
    unreadable.update(ref.content_sha256 for ref in (*buffer.row_refs,buffer.snapshot_ref))
    custody=_read(reader,body.custody_record_ref,CustodyRecord)
    unreadable.update((custody.ciphertext_ref.content_sha256,custody.custodian_attestation_ref.content_sha256))
    primary=_read(reader,body.primary_selection_ref,PrimarySelection)
    validate_holdout_access(request,primary,custody,expected_source=expected_source,now=now)
    inputs=_read(reader,intent.input_set_ref,InputSet)
    h_inputs=_read(reader,body.holdout_input_set_ref,InputSet)
    context=_dataset(reader,body.context_dataset_ref,'RESEARCH',date(2018,1,1),date(2025,8,31))
    if (inputs.source!=expected_source or h_inputs.source!=expected_source
        or inputs.dataset_evidence_ref!=body.context_dataset_ref
        or h_inputs.dataset_evidence_ref!=body.holdout_dataset_ref
        or inputs.policy_digest!=body.policy_digest or inputs.environment_ref!=body.environment_ref
        or any(getattr(inputs,key)!=getattr(h_inputs,key) for key in
            ('epoch_id','family_digest','policy_digest','environment_ref','cost_model','regime_threshold_ref','integration_receipt_ref'))):
        raise ValueError('holdout operation differs from its research and holdout InputSets')
    roles=(*context.row_refs,*holdout.row_refs,*buffer.row_refs,
        context.snapshot_ref,holdout.snapshot_ref,buffer.snapshot_ref,
        custody.ciphertext_ref,custody.custodian_attestation_ref)
    if len({ref.content_sha256 for ref in roles})!=len(roles):
        raise ValueError('holdout data and custody roles overlap')
    for ref in (primary.family_review_ref,inputs.environment_ref,inputs.regime_threshold_ref,
        inputs.integration_receipt_ref,h_inputs.pit_proof_ref):
        _reference(ref,65536)
    integration=_read(reader,inputs.integration_receipt_ref,IntegrationReceipt)
    if integration.source!=expected_source:
        raise ValueError('holdout integration receipt differs from source')
    _reference(outer.evidence_ref,67108864)
    reader.read_bytes(outer.evidence_ref)
    _reference(h_inputs.fold_manifest_ref,2097152)
    folds=_read(reader,h_inputs.fold_manifest_ref,FoldManifest)
    pit=_read(reader,h_inputs.pit_proof_ref,PITProof)
    if (folds!=build_holdout_fold_manifest(context,holdout,policy_digest=body.policy_digest)
        or pit.dataset_ref!=h_inputs.dataset_evidence_ref or pit.fold_manifest_ref!=h_inputs.fold_manifest_ref
        or pit.vintage_class!=holdout.vintage_class or pit.limitations!=holdout.limitations
        or pit.historical_vintage_verified or holdout.vintage_class!='RETROSPECTIVE_CURRENT_ARCHIVE'):
        raise ValueError('holdout InputSet fold or PIT metadata differs')
    for ref in (pit.revision_proof_ref,pit.no_future_suite_ref):
        _reference(ref,65536)
    family=_read(reader,primary.family_review_ref,FamilyReview)
    if family.input_set_ref!=intent.input_set_ref or select_primary(family,reader)!=primary:
        raise ValueError('holdout primary differs from complete retained family selection')
    inner=validate_family_review_approval(family,expected_source,reader)
    if (inner.operator_identity!=outer.operator_identity
        or not inner.issued_at<=authorization.issued_at<=now<authorization.expires_at<=inner.expires_at):
        raise ValueError('holdout family review is not current for the operation')
    registration=_read(reader,body.registration_proof_ref,RegistrationProof)
    for ref in (registration.publication_ref,registration.baseline_selection_ref):
        _reference(ref,65536)
    if (registration.input_set_ref!=intent.input_set_ref or registration!=build_registration_proof(
        _read(reader,registration.publication_ref,PublicationReceipt),store=reader)):
        raise ValueError('holdout registration differs from retained family publication')
    candidate=_read(reader,body.candidate_spec_ref,CandidateSpec)
    if (primary.primary_alpha_id!=candidate.alpha_id or primary.primary_version!=candidate.version
        or intent.allowed_alpha_ids!=(candidate.alpha_id,)):
        raise ValueError('holdout candidate differs from its selected primary')
    index=FAMILY_IDS.index(candidate.alpha_id)
    closure=_read(reader,family.candidate_report_refs[index],CampaignClosureReport)
    if closure.qualification_ref is None:
        raise ValueError('holdout primary has no retained qualification')
    qualification=_read(reader,closure.qualification_ref,QualificationBundle)
    evaluation=_read(reader,qualification.evaluation_ref,EvaluationResult)
    selected=_read(reader,evaluation.manifest_ref,EvaluationManifest)
    if (selected.registration_proof_ref!=body.registration_proof_ref
        or selected.candidate_spec_ref!=body.candidate_spec_ref
        or selected.candidate_head_ref!=registration.candidate_head_refs[index]):
        raise ValueError('holdout registration or candidate differs from selected OOS')
    _,_,_,threshold=validate_research_inputs(intent.input_set_ref,reader)
    if (threshold.training_range.start!=date(2018,1,1) or threshold.training_range.end!=date(2021,8,31)
        or threshold.sample_count!=(date(2021,8,31)-date(2018,1,1)).days+1-63 or Decimal(threshold.threshold)<0):
        raise ValueError('holdout threshold differs from frozen training inputs')
    baseline=validate_baseline_selection(registration.baseline_selection_ref,intent.input_set_ref,reader)
    environment=_read(reader,body.environment_ref,EnvironmentIdentity)
    spec=_read(reader,body.instrument_spec_ref,InstrumentSpec)
    _reference(spec.security_master_ref,65536)
    instrument=_read(reader,spec.security_master_ref,ResearchInstrument)
    if (environment.python_version!='3.11' or instrument.source!=expected_source
        or instrument.policy_digest!=body.policy_digest
        or any(getattr(spec,key)!=getattr(instrument,key) for key in
            ('instrument','price_increment','size_increment','quote_quantum','minimum_notional'))):
        raise ValueError('holdout instrument or environment differs from source-bound assumptions')
    value=dict(schema_version='p3-holdout-manifest-v1',source=expected_source,
        primary_selection_ref=body.primary_selection_ref,candidate_spec_ref=body.candidate_spec_ref,
        research_registration_ref=body.registration_proof_ref,holdout_dataset_ref=body.holdout_dataset_ref,
        context_dataset_ref=body.context_dataset_ref,buffer_ref=body.buffer_ref,policy_digest=body.policy_digest,
        selected_baseline=baseline.selected_id,environment_ref=body.environment_ref)
    manifest=HoldoutManifest.model_validate({**value,'digest':hashlib.sha256(canonical_json_bytes(value)).hexdigest()})
    return request,manifest


__all__ = ["derive_holdout_request", "validate_holdout_access", "validate_holdout_operation_input"]
