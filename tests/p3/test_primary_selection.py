"""Selection rejects incomplete family disclosure before reading or publishing."""
import hashlib

import pytest

from packages.alpha_lifecycle.contracts.authority import FamilyReview
from packages.alpha_lifecycle.primary_selection import select_primary
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_qualification import synthetic_oos


@pytest.mark.parametrize('fault',['candidate_reports','trial_outcomes'])
def test_selection_rejects_repeated_disclosure_before_storage(fault):
    refs=tuple(ArtifactRefV1(content_sha256=str(i)*64,size_bytes=2,
        media_type='application/json',locator=str(i)*64+'.blob') for i in range(1,8))
    value=dict(schema_version='p3-family-review-v1',input_set_ref=refs[0],
        candidate_report_refs=(refs[1],)*4 if fault=='candidate_reports' else refs[1:5],
        trial_outcome_refs=(refs[5],)*2 if fault=='trial_outcomes' else (refs[5],),
        review_ref=refs[6],complete_disclosure=True)
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    review=FamilyReview.model_validate_json(canonical_json_bytes(value))
    class NoStorage:
        def read_bytes(self,*a,**k):
            raise AssertionError('invalid disclosure reached storage')
        def put_bytes(self,*a,**k):
            raise AssertionError('invalid disclosure published an artifact')
    with pytest.raises(ValueError,match='distinct'):
        select_primary(review,NoStorage())


def test_ranking_preserves_frozen_precision_without_changing_caller():
    from decimal import Decimal,localcontext
    from packages.alpha_lifecycle.primary_selection import ranking_key
    with localcontext() as context:
        context.prec=6
        lower=ranking_key('a0',(Decimal('0.12345678'),)*3,Decimal('0'),Decimal('0.1'))
        higher=ranking_key('a1',(Decimal('0.12345679'),)*3,Decimal('0'),Decimal('0.1'))
        assert higher<lower
        assert context.prec==6


@pytest.mark.parametrize('case',['selected_precision','none_qualified','wrong_order','wrong_version'])
def test_selection_family_and_aggregate_ordering_unit(tmp_path,monkeypatch,case):
    """Isolate selection arithmetic with stubbed graph reads; no authority proof."""
    from decimal import Decimal,localcontext
    from types import SimpleNamespace
    from packages.alpha_lifecycle import primary_selection as selection
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport,PublicationReceipt
    from packages.alpha_lifecycle.contracts.results import QualificationBundle
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.alpha_lifecycle.protocol import AlphaQualificationEvidenceV1
    from packages.data_catalog.artifact_store import LocalArtifactStore
    def ref(index):
        digest=hashlib.sha256(str(index).encode()).hexdigest()
        return ArtifactRefV1(content_sha256=digest,size_bytes=2,media_type='application/json',locator=digest+'.blob')
    reports=tuple(ref(i) for i in range(4))
    value=dict(schema_version='p3-family-review-v1',input_set_ref=ref(20),candidate_report_refs=reports,
        trial_outcome_refs=(ref(21),),review_ref=ref(22),complete_disclosure=True)
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    review=FamilyReview.model_validate_json(canonical_json_bytes(value))
    objects={(review.input_set_ref.content_sha256,InputSet):SimpleNamespace(policy_digest='a'*64)}
    ids=list(FAMILY_IDS)
    if case=='wrong_order':
        ids[0],ids[1]=ids[1],ids[0]
    for i in range(4):
        objects[(ref(i).content_sha256,CampaignClosureReport)]=SimpleNamespace(
            qualification_ref=ref(i+4),publication_ref=ref(i+12))
        objects[(ref(i+4).content_sha256,QualificationBundle)]=SimpleNamespace(
            legacy_evidence_ref=ref(i+8),alpha_verdict='PASS' if i<2 and case!='none_qualified' else 'FAIL',
            criteria=(SimpleNamespace(passed=True),))
        objects[(ref(i+8).content_sha256,AlphaQualificationEvidenceV1)]=SimpleNamespace(
            alpha_id=ids[i],alpha_version='2.0.0' if case=='wrong_version' and i==3 else '1.0.0',
            fold_excess_returns=(Decimal('0.1'),)*3,
            metrics=SimpleNamespace(total_return=Decimal('1.12345679' if i==1 else '1.12345678'),max_drawdown=Decimal('0.1')),
            baseline_result=SimpleNamespace(total_return=Decimal('1.00000001')))
        objects[(ref(i+12).content_sha256,PublicationReceipt)]=SimpleNamespace(registry_event_refs=(ref(i+16),))
    monkeypatch.setattr(selection,'_read',lambda store,reference,model:objects[(reference.content_sha256,model)])
    root=tmp_path/'store'
    root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    with localcontext() as context:
        context.prec=6
        if case in ('wrong_order','wrong_version'):
            with pytest.raises(ValueError,match='family'):
                selection.select_primary(review,store)
            assert not tuple(root.iterdir())
        else:
            result=selection.select_primary(review,store)
            assert result.outcome==('SELECTED' if case=='selected_precision' else 'NONE_QUALIFIED')
            assert result.primary_alpha_id==(FAMILY_IDS[1] if case=='selected_precision' else None)
            assert result.primary_candidate_head_ref==(ref(17) if case=='selected_precision' else None)
            assert len(tuple(root.iterdir()))==1
        assert context.prec==6


def test_distinct_reports_for_one_candidate_cannot_replace_complete_family(synthetic_oos):
    """Real synthetic qualification; publication identities are explicit fixtures."""
    from datetime import UTC,datetime,timedelta
    from uuid import uuid4
    from packages.alpha_lifecycle.baseline_campaign import _read
    from packages.alpha_lifecycle.contracts.execution import EvaluationManifest
    from packages.alpha_lifecycle.operation_input import P3OperationInput,FAMILY_IDS
    from packages.alpha_lifecycle.publication import build_publication_receipt,build_closure_report
    from services.job_store.p3_publication_repository import JobCommitResult
    from services.job_worker.p3_publication_producer import prepare_candidate_oos
    from tests.p3.test_replica_execution import _seal
    store,evaluation,proof=synthetic_oos
    manifest=_read(store,evaluation.manifest_ref,EvaluationManifest)
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-oos-a0-v1',
        operation='OOS',input_set_ref=manifest.input_set_ref,allowed_alpha_ids=(FAMILY_IDS[0],),
        body=dict(evaluation_manifest_ref=evaluation.manifest_ref))
    now=datetime(2026,9,10,tzinfo=UTC)
    proposal=prepare_candidate_oos(_read(store,intent_ref,P3OperationInput),evaluation,proof,
        job_id='synthetic_oos',observed_at=now,expires_at=now+timedelta(hours=1),store=store)
    request=proposal.request
    commit_ref=_seal(store,schema_version='p3-job-commit-result-v1',job_id=request.job_id,
        idempotency_key=request.idempotency_key,semantic_request_digest=request.semantic_request_digest,
        prepublication_ref=request.evidence_ref,ledger_event_ids=[str(uuid4()),str(uuid4())],
        registry_event_refs=request.proposed_event_refs,alpha_outcome='FAIL')
    request_ref=store.put_bytes(canonical_json_bytes(request),media_type='application/json')
    receipt=build_publication_receipt(request_ref,_read(store,commit_ref,JobCommitResult),committed_at=now,store=store)
    reports=[]
    for index in range(1,5):
        closure=build_closure_report(request,request.evidence_ref,receipt,store=store,projection_digest=str(index)*64)
        reports.append(store.put_bytes(canonical_json_bytes(closure),media_type='application/json'))
    review_ref=_seal(store,schema_version='p3-family-review-v1',input_set_ref=manifest.input_set_ref,
        candidate_report_refs=reports,trial_outcome_refs=(request_ref,),review_ref=request_ref,complete_disclosure=True)
    with pytest.raises(ValueError,match='family'):
        select_primary(_read(store,review_ref,FamilyReview),store)
