"""Selection rejects incomplete family disclosure before reading or publishing."""
import hashlib

import pytest

from packages.alpha_lifecycle.contracts.authority import FamilyReview
from packages.alpha_lifecycle.primary_selection import select_primary
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


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


def test_selection_recomputes_candidate_closure_before_ranking(monkeypatch):
    from packages.alpha_lifecycle import primary_selection as selection
    refs=tuple(ArtifactRefV1(content_sha256=str(i)*64,size_bytes=2,
        media_type='application/json',locator=str(i)*64+'.blob') for i in range(1,8))
    value=dict(schema_version='p3-family-review-v1',input_set_ref=refs[0],candidate_report_refs=refs[1:5],
        trial_outcome_refs=(refs[5],),review_ref=refs[6],complete_disclosure=True)
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    def reject(*a,**k):
        raise ValueError('candidate recomputation rejected')
    monkeypatch.setattr(selection,'validate_candidate_closure',reject)
    class NoUnvalidatedRead:
        def read_bytes(self,*a,**k):
            raise AssertionError('selection read an unvalidated candidate')
    with pytest.raises(ValueError,match='candidate recomputation rejected'):
        select_primary(FamilyReview.model_validate_json(canonical_json_bytes(value)),NoUnvalidatedRead())


@pytest.mark.parametrize('case',['selected_precision','none_qualified','wrong_order','wrong_version','missing_trials'])
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
    def candidate(reference,**kwargs):
        i=reports.index(reference)
        return (objects[(ref(i+4).content_sha256,QualificationBundle)],
            objects[(ref(i+8).content_sha256,AlphaQualificationEvidenceV1)],ref(i+16))
    monkeypatch.setattr(selection,'validate_candidate_closure',candidate)
    if case!='missing_trials':
        monkeypatch.setattr(selection,'_validate_disclosure',lambda *args:None)
    root=tmp_path/'store'
    root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    with localcontext() as context:
        context.prec=6
        if case in ('wrong_order','wrong_version','missing_trials'):
            with pytest.raises(ValueError,match='trial' if case=='missing_trials' else 'family'):
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
        prepublication_ref=request.evidence_ref,ledger_event_ids=[str(item.event_id) for item in proposal.entries],
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


@pytest.fixture(scope='module')
def disclosed_family(tmp_path_factory,synthetic_oos):
    """Real retained contracts; candidate qualification and SQL authority are assumed unit inputs."""
    from types import SimpleNamespace
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.alpha_lifecycle.replay import run_replays
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.alpha_lifecycle.trials import deterministic_trial_keys
    from packages.alpha_lifecycle.primary_selection import family_disclosure_digest
    from tests.p3.test_replay import Executor,_ref
    from tests.p3.test_replica_execution import _seal
    tmp_path=tmp_path_factory.mktemp('disclosed-family')
    root=tmp_path/'store'; root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    from tests.p3.test_publication import _changed
    environment_ref=_seal(store,schema_version='p3-environment-identity-v1',python_version='3.11',
        root_lock_digest='a'*64,native_manifest_digest='b'*64,sandbox_policy_digest='c'*64,
        platform='linux-x86_64',decimal_precision=50)
    class UnitExecutor(Executor):
        def execute(self,*args,**kwargs):
            return _changed(super().execute(*args,**kwargs),environment_ref=environment_ref,sandbox_policy_digest='c'*64)
    qualifications=[]; outcomes=[]
    for index,alpha_id in enumerate(FAMILY_IDS):
        executor=UnitExecutor()
        executor.result=canonical_json_bytes(_changed(synthetic_oos[1],manifest_ref=_ref(str(index+1)*64)))
        executor.result_ref=_ref(hashlib.sha256(executor.result).hexdigest(),len(executor.result))
        executor.values={executor.result_ref.content_sha256:executor.result}
        proof=run_replays(_ref(str(index+1)*64),executor,
            logical_trial_id=f'p3-oos-a{index}-v1',output_root=tmp_path/f'alpha{index}')
        for raw in executor.values.values():
            store.put_bytes(raw,media_type='application/json')
        proof_ref=store.put_bytes(canonical_json_bytes(proof),media_type='application/json')
        qualifications.append(SimpleNamespace(evaluation_ref=executor.result_ref,replay_proof_ref=proof_ref))
        for key in deterministic_trial_keys('synthetic.epoch',alpha_id):
            outcomes.append(_seal(store,schema_version='p3-trial-outcome-v1',trial_key=key,status='COMPLETED',
                result_ref=executor.result_ref,execution_receipt_refs=proof.receipt_refs))
    first=json_read(store,proof.receipt_refs[0])
    from packages.alpha_lifecycle.contracts.results import ReplayReceipt
    receipt=ReplayReceipt.model_validate_json(canonical_json_bytes(first))
    inputs=SimpleNamespace(source=receipt.source,environment_ref=receipt.environment_ref,epoch_id='synthetic.epoch')
    placeholder=store.put_bytes(b'{}',media_type='application/json')
    review_ref=_seal(store,schema_version='p3-family-review-v1',input_set_ref=placeholder,
        candidate_report_refs=tuple(_ref(str(index+1)*64) for index in range(4)),
        trial_outcome_refs=outcomes,review_ref=placeholder,complete_disclosure=True)
    review=FamilyReview.model_validate_json(store.read_bytes(review_ref))
    approval_ref=_seal(store,schema_version='p3-review-approval-v1',source=inputs.source,
        subject_digests=(family_disclosure_digest(review),),operator_identity='synthetic.operator',
        reviewer_identity='synthetic.reviewer',review_execution_id='synthetic.review',verdict='APPROVED',
        issued_at='2026-09-10T00:00:00Z',expires_at='2026-09-11T00:00:00Z',evidence_ref=placeholder,
        authority=dict(broker=False,live=False,network=False,production=False))
    from tests.p3.test_publication import _changed
    review=_changed(review,review_ref=approval_ref.model_dump(mode='json'))
    return store,inputs,qualifications,review


def json_read(store,ref):
    import json
    return json.loads(store.read_bytes(ref))


@pytest.mark.parametrize('fault',[None,'missing','outside_key','wrong_result','wrong_receipts','unfinished',
    'cancelled_result','omitted_completion','wrong_review_subject','same_reviewer','rejected_review',
    'wrong_source','invalid_interval','missing_review_evidence','historical_failure','historical_cancellation',
    'receipt_missing_result','receipt_wrong_policy','receipt_reverse_time','receipt_wrong_order',
    'receipt_wrong_manifest','detached_failed_result'])
def test_family_disclosure_binds_all_trials_and_noncyclic_review(disclosed_family,fault):
    from packages.alpha_lifecycle import primary_selection as selection
    from packages.alpha_lifecycle.contracts.results import TrialOutcome
    from packages.alpha_lifecycle.contracts.authority import ReviewApproval
    from tests.p3.test_publication import _changed
    store,inputs,bundles,review=disclosed_family
    def retain(value):
        return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    refs=list(review.trial_outcome_refs)
    outcome=TrialOutcome.model_validate_json(store.read_bytes(refs[0]))
    approval=ReviewApproval.model_validate_json(store.read_bytes(review.review_ref))
    if fault=='missing': refs.pop()
    elif fault=='outside_key': outcome=_changed(outcome,trial_key='outside.family')
    elif fault=='wrong_result': outcome=_changed(outcome,result_ref=review.input_set_ref.model_dump(mode='json'))
    elif fault=='wrong_receipts': outcome=_changed(outcome,execution_receipt_refs=list(reversed(outcome.execution_receipt_refs)))
    elif fault=='unfinished': outcome=_changed(outcome,status='STARTED')
    elif fault=='cancelled_result': outcome=_changed(outcome,status='CANCELLED')
    elif fault=='omitted_completion': outcome=_changed(outcome,status='PIPELINE_FAILED')
    elif fault in ('historical_failure','historical_cancellation'):
        refs.append(retain(_changed(outcome,status='PIPELINE_FAILED' if fault=='historical_failure' else 'CANCELLED',
            result_ref=None,execution_receipt_refs=[])))
    if fault and (fault.startswith('receipt_') or fault=='detached_failed_result'):
        from packages.alpha_lifecycle.contracts.results import ReplayReceipt,EvaluationResult
        from tests.p3.test_replay import _ref
        receipt=ReplayReceipt.model_validate_json(store.read_bytes(outcome.execution_receipt_refs[0]))
        partial_result=None
        if fault=='receipt_missing_result': receipt=_changed(receipt,result_ref=_ref('9'*64))
        elif fault=='receipt_wrong_policy': receipt=_changed(receipt,sandbox_policy_digest='9'*64)
        elif fault=='receipt_reverse_time': receipt=_changed(receipt,completed_at='2026-09-04T00:00:00Z')
        elif fault=='receipt_wrong_order':
            receipt=ReplayReceipt.model_validate_json(store.read_bytes(outcome.execution_receipt_refs[2]))
        elif fault=='receipt_wrong_manifest':
            evaluation=EvaluationResult.model_validate_json(store.read_bytes(receipt.result_ref))
            receipt=_changed(receipt,result_ref=retain(_changed(evaluation,manifest_ref=_ref('9'*64))))
        else: partial_result=review.input_set_ref
        refs.append(retain(_changed(outcome,status='PIPELINE_FAILED',result_ref=partial_result,
            execution_receipt_refs=(retain(receipt),))))
    if fault!='missing': refs[0]=retain(outcome)
    review=_changed(review,trial_outcome_refs=refs)
    approval=_changed(approval,subject_digests=(selection.family_disclosure_digest(review),))
    if fault=='wrong_review_subject': approval=_changed(approval,subject_digests=('a'*64,))
    elif fault=='same_reviewer': approval=_changed(approval,reviewer_identity=approval.operator_identity)
    elif fault=='rejected_review': approval=_changed(approval,verdict='REJECTED')
    elif fault=='wrong_source': approval=_changed(approval,source={**approval.source.model_dump(),'commit_sha':'e'*40})
    elif fault=='invalid_interval': approval=_changed(approval,expires_at='2026-09-09T00:00:00Z')
    elif fault=='missing_review_evidence':
        from tests.p3.test_replay import _ref
        approval=_changed(approval,evidence_ref=_ref('9'*64))
    review=_changed(review,review_ref=retain(approval))
    before={p.name:p.read_bytes() for p in store._root.iterdir()}
    if fault in (None,'historical_failure','historical_cancellation'):
        selection._validate_disclosure(review,bundles,inputs,store)
    else:
        with pytest.raises((ValueError,OSError)):
            selection._validate_disclosure(review,bundles,inputs,store)
    assert before=={p.name:p.read_bytes() for p in store._root.iterdir()}


@pytest.mark.parametrize('fault',['expired','future','short_interval','wrong_input_set','wrong_operator',None])
def test_selection_staging_requires_current_inner_review(disclosed_family,fault):
    from datetime import UTC,datetime,timedelta
    from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload,AuthorityHeld
    from packages.alpha_lifecycle.contracts.authority import ReviewApproval,RunAuthorization
    from packages.alpha_lifecycle.operation_input import P3OperationInput,FAMILY_IDS
    from tests.p3.test_baseline_operation import _cli_authorization
    from tests.p3.test_replica_execution import _seal
    from tests.p3.test_publication import _changed
    store,inputs,_,review=disclosed_family
    def retain(value): return store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    now=datetime.now(UTC)
    inner=ReviewApproval.model_validate_json(store.read_bytes(review.review_ref))
    def utc(value): return value.isoformat(timespec='microseconds').replace('+00:00','Z')
    inner=_changed(inner,operator_identity='synthetic-operator',issued_at=utc(now-timedelta(minutes=2)),
        expires_at=utc(now+timedelta(hours=1)))
    if fault=='expired': inner=_changed(inner,expires_at=utc(now-timedelta(minutes=1)))
    elif fault=='future': inner=_changed(inner,issued_at=utc(now+timedelta(minutes=1)))
    elif fault=='short_interval': inner=_changed(inner,expires_at=utc(now+timedelta(minutes=1)))
    elif fault=='wrong_operator': inner=_changed(inner,operator_identity='another.operator')
    review=_changed(review,review_ref=retain(inner))
    operation_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-select-primary-v1',
        operation='OOS',input_set_ref=retain({'different':True}) if fault=='wrong_input_set' else review.input_set_ref,
        allowed_alpha_ids=FAMILY_IDS,body=dict(family_review_ref=retain(review)))
    authorization_ref=_cli_authorization(store,operation_ref,inputs.source)
    operation=P3OperationInput.model_validate_json(store.read_bytes(operation_ref))
    authorization=RunAuthorization.model_validate_json(store.read_bytes(authorization_ref))
    if fault is None:
        payload=stage_alpha_campaign_payload(store,authorization,inputs.source,operation.workflow_operation,
            canonical_json_bytes(operation),operation_input=operation)
        assert payload.manifest_ref==operation_ref
    else:
        with pytest.raises(AuthorityHeld,match='REVIEW'):
            stage_alpha_campaign_payload(store,authorization,inputs.source,operation.workflow_operation,
                canonical_json_bytes(operation),operation_input=operation)
