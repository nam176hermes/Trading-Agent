"""Retained arithmetic is real; SQL commit identities here are synthetic fixtures."""
from datetime import UTC,datetime,timedelta
import hashlib

import pytest

from packages.alpha_lifecycle import primary_selection
from packages.alpha_lifecycle.baseline_campaign import _read
from packages.alpha_lifecycle.contracts.execution import EvaluationManifest,InputSet
from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence,CampaignClosureReport,PublicationReceipt
from packages.alpha_lifecycle.contracts.results import QualificationBundle
from packages.alpha_lifecycle.lifecycle import plan_transition,read_registry_event
from packages.alpha_lifecycle.operation_input import FAMILY_IDS,P3OperationInput
from packages.alpha_lifecycle.protocol import AlphaQualificationResultV1
from packages.alpha_lifecycle.publication import build_publication_receipt,build_closure_report
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus,QualificationDecision
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult
from services.job_worker.p3_publication_producer import prepare_candidate_oos,build_publication_proposal
from tests.p3.test_publication import _changed
from tests.p3.test_qualification import synthetic_oos
from tests.p3.test_replica_execution import _seal


def _candidate_closure(inputs,*,forged=False):
    store,evaluation,proof=inputs
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    manifest=_read(store,evaluation.manifest_ref,EvaluationManifest)
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-oos-a0-v1',
        operation='OOS',input_set_ref=manifest.input_set_ref,allowed_alpha_ids=(FAMILY_IDS[0],),
        body=dict(evaluation_manifest_ref=evaluation.manifest_ref))
    now=datetime(2026,9,10,tzinfo=UTC)
    proposal=prepare_candidate_oos(_read(store,intent_ref,P3OperationInput),evaluation,proof,
        job_id='synthetic_closed_oos',observed_at=now,expires_at=now+timedelta(hours=1),store=store)
    if forged:
        evidence=_read(store,proposal.request.evidence_ref,PrePublicationEvidence)
        bundle=_read(store,evidence.qualification_bundle_ref,QualificationBundle)
        legacy=_read(store,bundle.legacy_result_ref,AlphaQualificationResultV1)
        criteria=tuple(item.model_copy(update={'passed':True}) for item in bundle.criteria)
        legacy=AlphaQualificationResultV1.model_validate(legacy.model_copy(update=dict(
            criteria=criteria,qualified=True,failure_codes=(),result_sha256=None)))
        bundle=_changed(bundle,criteria=[item.model_dump(mode='json') for item in criteria],
            alpha_verdict='PASS',legacy_result_ref=retain(legacy).model_dump(mode='json'))
        bundle_ref=retain(bundle)
        evidence=_changed(evidence,qualification_bundle_ref=bundle_ref.model_dump(mode='json'))
        head=read_registry_event(store,manifest.candidate_head_ref)
        record=head.record.model_copy(update=dict(lifecycle_status=AlphaLifecycleStatus.RESEARCHED,
            qualification_decision=QualificationDecision.PASS,qualification_reason='C01-C16 passed',
            metrics_sha256=hashlib.sha256(canonical_json_bytes(evaluation.base.aggregate_metrics)).hexdigest(),
            robustness_sha256=bundle_ref.content_sha256))
        researched=plan_transition(record,head,evidence)
        passed=plan_transition(record.model_copy(update={'lifecycle_status':AlphaLifecycleStatus.OOS_PASS}),researched,evidence)
        proposal=build_publication_proposal(events=(researched,passed),expected_heads=proposal.request.expected_heads,
            evidence=evidence,epoch_id=_read(store,manifest.input_set_ref,InputSet).epoch_id,
            job_id=proposal.request.job_id,observed_at=now,expires_at=now+timedelta(hours=1),store=store)
    request=proposal.request
    commit_ref=_seal(store,schema_version='p3-job-commit-result-v1',job_id=request.job_id,
        idempotency_key=request.idempotency_key,semantic_request_digest=request.semantic_request_digest,
        prepublication_ref=request.evidence_ref,ledger_event_ids=[str(item.event_id) for item in proposal.entries],
        registry_event_refs=request.proposed_event_refs,alpha_outcome='PASS' if forged else 'FAIL')
    receipt=build_publication_receipt(retain(request),_read(store,commit_ref,JobCommitResult),committed_at=now,store=store)
    closure=build_closure_report(request,request.evidence_ref,receipt,store=store)
    return store,retain(closure),manifest.input_set_ref,request.proposed_event_refs[-1]


@pytest.mark.parametrize('forged',[False,True])
def test_candidate_closure_recomputes_even_a_fully_rehashed_pass(synthetic_oos,monkeypatch,forged):
    store,closure_ref,input_ref,head_ref=_candidate_closure(synthetic_oos,forged=forged)
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('closure validation wrote artifacts')))
    arguments=dict(input_set_ref=input_ref,alpha_id=FAMILY_IDS[0],store=store)
    if forged:
        with pytest.raises(ValueError):
            primary_selection.validate_candidate_closure(closure_ref,**arguments)
    else:
        qualification,evidence,terminal=primary_selection.validate_candidate_closure(closure_ref,**arguments)
        assert qualification.alpha_verdict=='FAIL'
        assert evidence.alpha_id==FAMILY_IDS[0]
        assert terminal==head_ref


@pytest.mark.parametrize('fault',['media','input_set','commit_outcome','event_ids','event_order','request_media','commit_media'])
def test_candidate_closure_rejects_scope_and_commit_drift(synthetic_oos,monkeypatch,fault):
    store,closure_ref,input_ref,head_ref=_candidate_closure(synthetic_oos)
    if fault=='media':
        closure_ref=closure_ref.model_copy(update={'media_type':'text/plain'})
    elif fault=='input_set':
        input_ref=head_ref
    else:
        def retain(value):
            return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
        closure=_read(store,closure_ref,CampaignClosureReport)
        receipt=_read(store,closure.publication_ref,PublicationReceipt)
        commit=_read(store,receipt.commit_result_ref,JobCommitResult)
        if fault in ('request_media','commit_media'):
            field='request_ref' if fault=='request_media' else 'commit_result_ref'
            reference=getattr(receipt,field).model_copy(update={'media_type':'text/plain'})
            receipt=_changed(receipt,**{field:reference.model_dump(mode='json')})
        elif fault=='event_ids':
            from uuid import UUID
            ids=[str(UUID(int=10)),str(UUID(int=11))]
            commit=_changed(commit,ledger_event_ids=ids)
            receipt=_changed(receipt,ledger_event_ids=ids)
        elif fault=='event_order':
            ids=[str(item) for item in reversed(commit.ledger_event_ids)]
            commit=_changed(commit,ledger_event_ids=ids)
            receipt=_changed(receipt,ledger_event_ids=ids)
        else:
            commit=_changed(commit,alpha_outcome='PASS')
        if fault not in ('request_media','commit_media'):
            receipt=_changed(receipt,commit_result_ref=retain(commit).model_dump(mode='json'))
        closure_ref=retain(_changed(closure,publication_ref=retain(receipt).model_dump(mode='json')))
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('invalid closure wrote artifacts')))
    with pytest.raises(ValueError):
        primary_selection.validate_candidate_closure(closure_ref,input_set_ref=input_ref,alpha_id=FAMILY_IDS[0],store=store)


@pytest.mark.parametrize('index',[0,1])
@pytest.mark.parametrize('corrupt',[False,True])
def test_candidate_closure_reads_each_actual_registry_event(synthetic_oos,monkeypatch,index,corrupt):
    store,closure_ref,input_ref,_=_candidate_closure(synthetic_oos)
    closure=_read(store,closure_ref,CampaignClosureReport)
    receipt=_read(store,closure.publication_ref,PublicationReceipt)
    missing=receipt.registry_event_refs[index]
    original=store.read_bytes
    def read(ref):
        if ref.content_sha256==missing.content_sha256:
            if corrupt:
                return b'{}'
            raise FileNotFoundError('missing proposed registry event')
        return original(ref)
    monkeypatch.setattr(store,'read_bytes',read)
    with pytest.raises(ValueError if corrupt else FileNotFoundError):
        primary_selection.validate_candidate_closure(closure_ref,input_set_ref=input_ref,
            alpha_id=FAMILY_IDS[0],store=store)


@pytest.mark.parametrize('owner',['baseline_campaign','publication','evaluation'])
def test_typed_research_read_rejects_nonjson_before_storage(owner):
    from importlib import import_module
    read=import_module('packages.alpha_lifecycle.'+owner)._read
    from packages.data_contracts import ArtifactRefV1
    class NoRead:
        def read_bytes(self,*a,**k):
            raise AssertionError('typed JSON reader reached storage for non-JSON ref')
    ref=ArtifactRefV1(content_sha256='a'*64,size_bytes=2,media_type='text/plain',locator='a'*64+'.blob')
    with pytest.raises(ValueError):
        read(NoRead(),ref,InputSet)
