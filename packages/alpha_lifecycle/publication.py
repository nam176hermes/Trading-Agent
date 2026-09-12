"""Post-commit P3 receipt and closure artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, ReadbackStore, _read
from packages.alpha_lifecycle.contracts.lifecycle import (
    CampaignClosureReport,
    PrePublicationEvidence,
    PublicationReceipt,
    PublicationRequest,
    RegistrationProof,
)
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult, P3PublicationRepository


def _seal(store: ArtifactStore, value: object) -> ArtifactRefV1:
    return store.put_bytes(canonical_json_bytes(value), media_type="application/json")


def _digest(payload: Mapping[str, object]) -> str:
    def json_value(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, Mapping):
            return {key: json_value(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [json_value(item) for item in value]
        return value

    return hashlib.sha256(canonical_json_bytes(json_value(payload))).hexdigest()


def build_publication_receipt(
    request_ref: ArtifactRefV1,
    commit: JobCommitResult,
    *,
    committed_at: datetime,
    store: ArtifactStore,
) -> PublicationReceipt:
    request = _read(store, request_ref, PublicationRequest)
    commit = JobCommitResult.model_validate(commit).bound_to(request)
    payload = {
        "schema_version": "p3-publication-receipt-v1",
        "request_ref": request_ref,
        "ledger_event_ids": commit.ledger_event_ids,
        "registry_event_refs": commit.registry_event_refs,
        "commit_result_ref": _seal(store, commit),
        "committed_at": committed_at,
        "outcome": "COMMITTED",
    }
    payload["digest"] = _digest(payload)
    return PublicationReceipt.model_validate(payload)


def recover_publication_receipt(
    job_id: str, *, repository: P3PublicationRepository, store: ArtifactStore
) -> PublicationReceipt:
    """Reproduce a receipt from committed SQL custody, never from caller time."""
    request, commit, committed_at = repository.read_publication(job_id)
    request_ref = _seal(store, request)
    receipt = build_publication_receipt(request_ref, commit, committed_at=committed_at, store=store)
    if _read(store, receipt.commit_result_ref, JobCommitResult) != commit:
        raise ValueError("publication commit retention mismatch")
    return _read(store, _seal(store, receipt), PublicationReceipt)


def validate_registration_records(evidence: PrePublicationEvidence, store: ArtifactStore):
    """Validate the same immutable family before and after SQL publication."""
    from packages.alpha_lifecycle.contracts.policy import _CANDIDATE_DIGESTS
    from packages.alpha_lifecycle.registry import AlphaRecordV1, AlphaLifecycleStatus
    from packages.alpha_lifecycle.baseline_campaign import validate_baseline_selection, validate_research_inputs
    evidence = PrePublicationEvidence.model_validate(evidence)
    records = tuple(_read(store,ref,AlphaRecordV1) for ref in evidence.candidate_record_refs)
    if evidence.stage != 'REGISTER' or tuple(r.alpha_id for r in records) != tuple(sorted(_CANDIDATE_DIGESTS)):
        raise ValueError('registration requires the complete frozen family')
    selection = validate_baseline_selection(evidence.baseline_selection_ref,evidence.input_set_ref,store)
    inputs,folds,dataset,threshold = validate_research_inputs(evidence.input_set_ref,store)
    for record in records:
        if (record.version != '1.0.0' or record.lifecycle_status != AlphaLifecycleStatus.IDEA
            or record.parameter_set_sha256 != _CANDIDATE_DIGESTS[record.alpha_id]
            or record.source_sha != inputs.source.commit_sha
            or record.dataset_snapshot_sha256 != dataset.snapshot_ref.content_sha256
            or record.cost_model_sha256 != selection.selected_result.cost_model_sha256
            or record.baseline_id != selection.selected_id
            or record.baseline_version != selection.selected_result.baseline_version
            or inputs.epoch_id not in record.lineage or record.universe != ('BTCUSDT.BINANCE',)
            or record.training_start_at.date() != threshold.training_range.start
            or record.training_end_at.date() != threshold.training_range.end
            or record.validation_end_at.date() >= folds.folds[0].decision_start
            or record.oos_start_at.date() != folds.folds[0].return_end_range.start
            or record.oos_end_at.date() != folds.folds[-1].return_end_range.end
            or record.qualification_decision.value != 'NOT_EVALUATED'
            or record.metrics_sha256 is not None or record.robustness_sha256 is not None):
            raise ValueError('registration differs from its preregistered family')
    return records


def build_registration_proof(receipt: PublicationReceipt, *, store: ArtifactStore) -> RegistrationProof:
    """Derive historical family registration from an already committed receipt."""
    from packages.alpha_lifecycle.contracts.policy import _CANDIDATE_DIGESTS
    from packages.alpha_lifecycle.lifecycle import plan_transition, read_registry_event
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus
    receipt = PublicationReceipt.model_validate(receipt)
    request = _read(store,receipt.request_ref,PublicationRequest)
    evidence = _read(store,request.evidence_ref,PrePublicationEvidence)
    build_closure_report(request,request.evidence_ref,receipt,store=store)
    if (request.stage != 'REGISTER' or len(request.proposed_event_refs) != 8
        or tuple(h.alpha_id for h in request.expected_heads) != tuple(sorted(_CANDIDATE_DIGESTS))
        or any(h.sequence != 0 or h.event_digest is not None or h.version != '1.0.0' for h in request.expected_heads)):
        raise ValueError('registration proof requires the complete frozen family')
    for index,record in enumerate(validate_registration_records(evidence,store)):
        idea,candidate = (read_registry_event(store,ref) for ref in request.proposed_event_refs[2*index:2*index+2])
        if (idea != plan_transition(record,None,evidence)
            or candidate != plan_transition(record.model_copy(update={'lifecycle_status':AlphaLifecycleStatus.CANDIDATE}),idea,evidence)):
            raise ValueError('registration proof differs from its preregistered family')
    payload = dict(schema_version='p3-registration-proof-v1',input_set_ref=evidence.input_set_ref,
        baseline_selection_ref=evidence.baseline_selection_ref,
        candidate_head_refs=request.proposed_event_refs[1::2],publication_ref=_seal(store,receipt))
    payload['digest'] = _digest(payload)
    return RegistrationProof.model_validate(payload)


def validate_evaluation_registration(manifest, *, store: ArtifactStore) -> RegistrationProof:
    """Close historical registration before OOS; live SQL custody is checked by the worker."""
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
    from packages.alpha_lifecycle.contracts.policy import CandidateSpec, _CANDIDATE_DIGESTS
    from packages.alpha_lifecycle.lifecycle import read_registry_event
    from packages.alpha_lifecycle.registry import AlphaLifecycleStatus

    manifest = EvaluationManifest.model_validate(manifest)
    proof = _read(store, manifest.registration_proof_ref, RegistrationProof)
    receipt = _read(store, proof.publication_ref, PublicationReceipt)
    expected = build_registration_proof(receipt, store=ReadbackStore(store, store))
    spec = _read(store, manifest.candidate_spec_ref, CandidateSpec)
    index = tuple(sorted(_CANDIDATE_DIGESTS)).index(spec.alpha_id)
    head = read_registry_event(store, manifest.candidate_head_ref)
    if (proof != expected or proof.input_set_ref != manifest.input_set_ref
        or proof.baseline_selection_ref != manifest.baseline_selection_ref
        or manifest.candidate_head_ref != proof.candidate_head_refs[index]
        or head.sequence != 2 or head.record.lifecycle_status != AlphaLifecycleStatus.CANDIDATE
        or head.record.alpha_id != spec.alpha_id or head.record.version != spec.version
        or head.record.parameter_set_sha256 != spec.digest):
        raise ValueError('evaluation differs from retained family registration')
    return proof


def build_closure_report(
    request: PublicationRequest,
    prepublication_ref: ArtifactRefV1,
    receipt: PublicationReceipt,
    *,
    store: ArtifactStore,
    projection_digest: str | None = None,
) -> CampaignClosureReport:
    request = PublicationRequest.model_validate(request)
    receipt = PublicationReceipt.model_validate(receipt)
    committed_request = _read(store, receipt.request_ref, PublicationRequest)
    commit = _read(store, receipt.commit_result_ref, JobCommitResult).bound_to(request)
    evidence = _read(store, prepublication_ref, PrePublicationEvidence)
    if (
        committed_request != request
        or prepublication_ref != request.evidence_ref
        or evidence.stage != request.stage
        or receipt.ledger_event_ids != commit.ledger_event_ids
        or receipt.registry_event_refs != commit.registry_event_refs
    ):
        raise ValueError("publication closure artifacts do not match the committed request")
    payload = {
        "schema_version": "p3-campaign-closure-report-v1",
        "stage": request.stage,
        "prepublication_ref": prepublication_ref,
        "publication_ref": _seal(store, receipt),
        "qualification_ref": evidence.qualification_bundle_ref,
        "exit_result_ref": evidence.exit_result_ref,
        "projection_digest": projection_digest,
        "projection_status": "READY" if projection_digest is not None else "PENDING",
    }
    payload["digest"] = _digest(payload)
    return CampaignClosureReport.model_validate(payload)


__all__ = ["build_closure_report", "build_publication_receipt", "build_registration_proof", "recover_publication_receipt", "validate_registration_records", "validate_evaluation_registration"]
