"""Frozen one-primary selection across all four disclosed P3 candidates."""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from statistics import median

from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, ReadbackStore, _read
from packages.alpha_lifecycle.contracts.authority import FamilyReview, PrimarySelection
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport, PublicationReceipt
from packages.alpha_lifecycle.contracts.results import QualificationBundle
from packages.alpha_lifecycle.protocol import AlphaQualificationEvidenceV1
from packages.alpha_lifecycle.operation_input import FAMILY_IDS
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


def ranking_key(
    alpha_id: str,
    fold_excess: tuple[Decimal, ...],
    aggregate_excess: Decimal,
    max_drawdown: Decimal,
) -> tuple[Decimal, Decimal, Decimal, str]:
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        return (-median(fold_excess), -aggregate_excess, max_drawdown, alpha_id)


def validate_candidate_closure(closure_ref: ArtifactRefV1, *, input_set_ref: ArtifactRefV1,
    alpha_id: str, store: ArtifactStore):
    """Recompute retained arithmetic and publication bindings; SQL authority is separate."""
    from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
    from packages.alpha_lifecycle.contracts.results import EvaluationResult,ReplayProof
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.publication import build_closure_report
    from packages.alpha_lifecycle.qualification import prepare_oos_evidence
    from packages.alpha_lifecycle.pit_evidence import _reference, _ReadBudget
    from packages.alpha_lifecycle.lifecycle import publication_event_ids, read_registry_event
    from services.job_store.p3_publication_repository import JobCommitResult

    _reference(closure_ref,65536)
    _reference(input_set_ref,65536)
    budget=_ReadBudget(store)
    reader=ReadbackStore(budget,budget)
    closure=_read(reader,closure_ref,CampaignClosureReport)
    for ref in (closure.publication_ref,closure.prepublication_ref):
        _reference(ref,65536)
    receipt=_read(reader,closure.publication_ref,PublicationReceipt)
    for ref in (receipt.request_ref,receipt.commit_result_ref,*receipt.registry_event_refs):
        _reference(ref,65536)
    request=_read(reader,receipt.request_ref,PublicationRequest)
    _reference(request.evidence_ref,65536)
    for ref in request.proposed_event_refs:
        _reference(ref,65536)
    if (closure.stage!='RESEARCH_DECISION' or closure.qualification_ref is None
        or closure!=build_closure_report(request,closure.prepublication_ref,receipt,
            store=reader,projection_digest=closure.projection_digest)):
        raise ValueError('candidate closure differs from its retained publication')
    _reference(closure.qualification_ref,65536)
    qualification=_read(reader,closure.qualification_ref,QualificationBundle)
    for ref in (qualification.replay_proof_ref,qualification.pit_ref,
        qualification.legacy_evidence_ref,qualification.legacy_result_ref):
        _reference(ref,65536)
    _reference(qualification.evaluation_ref,67108864)
    legacy=_read(reader,qualification.legacy_evidence_ref,AlphaQualificationEvidenceV1)
    if (legacy.alpha_id,legacy.alpha_version)!=(alpha_id,'1.0.0'):
        raise ValueError('candidate closure differs from the frozen family')
    evaluation=_read(reader,qualification.evaluation_ref,EvaluationResult)
    proof=_read(reader,qualification.replay_proof_ref,ReplayProof)
    value=dict(schema_version='p3-operation-input-v1',
        workflow_operation=f'p3-oos-a{FAMILY_IDS.index(alpha_id)}-v1',operation='OOS',
        input_set_ref=input_set_ref,allowed_alpha_ids=(alpha_id,),
        body=dict(evaluation_manifest_ref=evaluation.manifest_ref))
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    intent=P3OperationInput.model_validate_json(canonical_json_bytes(value))
    events,heads,evidence,epoch_id=prepare_oos_evidence(intent,evaluation,proof,store=reader)
    for event in events:
        _reference(event.artifact,65536)
        if read_registry_event(reader,event.artifact)!=event:
            raise ValueError('retained candidate registry event differs')
    evidence_ref=reader.put_bytes(canonical_json_bytes(evidence),media_type='application/json')
    semantic=dict(stage=evidence.stage,evidence_ref=evidence_ref,
        expected_heads=heads,proposed_event_refs=tuple(event.artifact for event in events))
    digest=hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    commit=_read(reader,receipt.commit_result_ref,JobCommitResult)
    commit.bound_to(request,ledger_event_ids=tuple(publication_event_ids(epoch_id,event)[1] for event in events))
    if (request.stage!=evidence.stage or request.evidence_ref!=evidence_ref
        or request.expected_heads!=heads or request.proposed_event_refs!=tuple(event.artifact for event in events)
        or request.semantic_request_digest!=digest or request.idempotency_key!='publication.'+digest
        or evidence.qualification_bundle_ref!=closure.qualification_ref
        or commit.alpha_outcome!=qualification.alpha_verdict):
        raise ValueError('candidate decision differs from complete frozen recomputation')
    return qualification,legacy,events[-1].artifact


def select_primary(review: FamilyReview, reader: ArtifactStore) -> PrimarySelection:
    review = FamilyReview.model_validate(review)
    if (len({ref.content_sha256 for ref in review.candidate_report_refs})!=4
        or len({ref.content_sha256 for ref in review.trial_outcome_refs})!=len(review.trial_outcome_refs)):
        raise ValueError("primary selection requires distinct reports and trial disclosures")
    eligible = []
    family = []
    for alpha_id,closure_ref in zip(FAMILY_IDS,review.candidate_report_refs,strict=True):
        qualification,evidence,head_ref=validate_candidate_closure(closure_ref,
            input_set_ref=review.input_set_ref,alpha_id=alpha_id,store=reader)
        family.append((evidence.alpha_id,evidence.alpha_version))
        if qualification.alpha_verdict == "PASS" and all(item.passed for item in qualification.criteria):
            with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
                aggregate_excess=evidence.metrics.total_return-evidence.baseline_result.total_return
            eligible.append((
                ranking_key(
                    evidence.alpha_id,
                    evidence.fold_excess_returns,
                    aggregate_excess,
                    evidence.metrics.max_drawdown,
                ),
                evidence,
                head_ref,
            ))
    if tuple(family)!=tuple((alpha_id,'1.0.0') for alpha_id in FAMILY_IDS):
        raise ValueError('primary selection requires the complete ordered frozen family')
    input_set = _read(reader, review.input_set_ref, InputSet)
    review_ref = reader.put_bytes(canonical_json_bytes(review), media_type="application/json")
    if eligible:
        _, evidence, head_ref = min(eligible, key=lambda item: item[0])
        payload = {
            "schema_version":"p3-primary-selection-v1","family_review_ref":review_ref,
            "selection_policy_digest":input_set.policy_digest,
            "primary_alpha_id":evidence.alpha_id,"primary_version":evidence.alpha_version,
            "primary_candidate_head_ref":head_ref,"outcome":"SELECTED",
        }
    else:
        payload = {
            "schema_version":"p3-primary-selection-v1","family_review_ref":review_ref,
            "selection_policy_digest":input_set.policy_digest,
            "primary_alpha_id":None,"primary_version":None,
            "primary_candidate_head_ref":None,"outcome":"NONE_QUALIFIED",
        }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PrimarySelection.model_validate(payload)


__all__ = ["ranking_key", "select_primary"]
