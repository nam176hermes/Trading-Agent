"""Retained synthetic OOS graph; declared H1 bytes stay absent and unreadable."""
from datetime import UTC,date,datetime,timedelta
import hashlib
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle import holdout,primary_selection
from packages.alpha_lifecycle.contracts.authority import CustodyRecord,FamilyReview,PrimarySelection,RunAuthorization,IntegrationReceipt
from packages.alpha_lifecycle.contracts.data import DatasetEvidence,FoldManifest,PITProof
from packages.alpha_lifecycle.contracts.execution import BaselineManifest,EvaluationManifest,InputSet
from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport,PublicationReceipt,RegistrationProof
from packages.alpha_lifecycle.contracts.results import EvaluationResult,QualificationBundle,RegimeThreshold
from packages.alpha_lifecycle.folds import build_holdout_fold_manifest
from packages.alpha_lifecycle.operation_input import FAMILY_IDS,P3OperationInput
from packages.alpha_lifecycle.replica_store import _read
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_baseline_operation import _cli_authorization
from tests.p3.test_reference_input import _changed
from tests.p3.test_replica_execution import _seal
from tests.p3.test_select_operation import retained_family  # noqa: F401


@pytest.fixture(scope='module')
def synthetic_oos(tmp_path_factory):
    from tests.p3 import research_fixtures as fixtures
    original=fixtures.baseline_inputs
    def inputs_with_receipt(root,**kwargs):
        store,ref=original(root,**kwargs)
        manifest=_read(store,ref,BaselineManifest)
        inputs=_read(store,manifest.input_set_ref,InputSet)
        proof=store.put_bytes(b'{"synthetic_metadata_only":true}',media_type='application/json')
        receipt=_seal(store,schema_version='p3-integration-qualified-v1',source=inputs.source,
            sql_proof_ref=proof,native_fixture_proof_ref=proof,cleanup_proof_ref=proof,
            workflow_run_id=1,workflow_attempt=1,status='PASS',
            authority=dict(broker=False,live=False,network=False,production=False))
        inputs=store.put_bytes(canonical_json_bytes(_changed(inputs,integration_receipt_ref=receipt)),media_type='application/json')
        ref=store.put_bytes(canonical_json_bytes(_changed(manifest,input_set_ref=inputs)),media_type='application/json')
        return store,ref
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixtures,'baseline_inputs',inputs_with_receipt)
        return fixtures.build_synthetic_oos(tmp_path_factory)


@pytest.fixture(scope='module')
def holdout_graph(retained_family):
    store,inputs,family_ref=retained_family
    family=_read(store,family_ref,FamilyReview)
    closure=_read(store,family.candidate_report_refs[0],CampaignClosureReport)
    qualification=_read(store,closure.qualification_ref,QualificationBundle)
    evaluation=_read(store,qualification.evaluation_ref,EvaluationResult)
    selected=_read(store,evaluation.manifest_ref,EvaluationManifest)
    registration=_read(store,selected.registration_proof_ref,RegistrationProof)
    publication=_read(store,closure.publication_ref,PublicationReceipt)
    context=_read(store,inputs.dataset_evidence_ref,DatasetEvidence)
    def closed(label):
        digest=hashlib.sha256(('synthetic.unreleased.'+label).encode()).hexdigest()
        return ArtifactRefV1(content_sha256=digest,size_bytes=1,media_type='application/json',locator=digest+'.blob')
    def dataset(segment,start,count):
        refs=tuple(closed(str(start+timedelta(days=i))) for i in range(count))
        value=_changed(context,segment=segment,date_range=dict(start=str(start),end=str(start+timedelta(days=count-1))),
            usable_rows=count,row_refs=refs,snapshot_ref=closed(segment+'.snapshot'),
            ordered_rows_digest=hashlib.sha256(canonical_json_bytes([ref.content_sha256 for ref in refs])).hexdigest())
        ref=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
        return ref,_read(store,ref,DatasetEvidence)
    h_ref,h_data=dataset('HOLDOUT',date(2025,9,1),365)
    buffer_ref,buffer=dataset('BUFFER',date(2026,9,1),1)
    folds=build_holdout_fold_manifest(context,h_data,policy_digest=inputs.policy_digest)
    folds_ref=store.put_bytes(canonical_json_bytes(folds),media_type='application/json')
    pit_ref=_seal(store,schema_version='p3-p-i-t-proof-v1',dataset_ref=h_ref,fold_manifest_ref=folds_ref,
        vintage_class=h_data.vintage_class,historical_vintage_verified=False,
        revision_proof_ref=closed('holdout.producer'),no_future_suite_ref=closed('holdout.no-future'),limitations=h_data.limitations)
    h_inputs_ref=store.put_bytes(canonical_json_bytes(_changed(inputs,dataset_evidence_ref=h_ref,
        fold_manifest_ref=folds_ref,pit_proof_ref=pit_ref)),media_type='application/json')
    custody_ref=_seal(store,schema_version='p3-custody-record-v1',holdout_commitment='a'*64,
        ciphertext_ref=closed('ciphertext'),plaintext_bundle_digest='b'*64,custodian_identity='synthetic.custodian',
        research_identity='synthetic.research',custodian_attestation_ref=closed('attestation'),
        access_policy_digest='c'*64,classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND')
    # This selected value is a declared dependency stub only. The real family fails;
    # the separate non-stubbed test below must reject it and never read H1.
    primary_ref=_seal(store,schema_version='p3-primary-selection-v1',family_review_ref=family_ref,
        selection_policy_digest=inputs.policy_digest,primary_alpha_id=FAMILY_IDS[0],primary_version='1.0.0',
        primary_candidate_head_ref=publication.registry_event_refs[-1],outcome='SELECTED')
    assumption=_seal(store,schema_version='p3-research-instrument-v1',source=inputs.source,policy_digest=inputs.policy_digest,
        instrument='BTCUSDT.BINANCE',price_increment='0.01',size_increment='0.00001',quote_quantum='0.01',minimum_notional='10',
        classification='APPROVED_RESEARCH_ASSUMPTION_NOT_CURRENT_OR_HISTORICAL_VENUE_FILTER')
    spec_ref=_seal(store,schema_version='p3-instrument-spec-v1',instrument='BTCUSDT.BINANCE',security_master_ref=assumption,
        price_increment='0.01',size_increment='0.00001',quote_quantum='0.01',minimum_notional='10',
        base_currency='BTC',quote_currency='USDT',historical_rule_claim='SOURCE_BOUND_SIMULATION_NOT_HISTORICAL_EXCHANGE_RULES')
    body=dict(primary_selection_ref=primary_ref,candidate_spec_ref=selected.candidate_spec_ref,
        registration_proof_ref=selected.registration_proof_ref,custody_record_ref=custody_ref,
        holdout_input_set_ref=h_inputs_ref,holdout_dataset_ref=h_ref,context_dataset_ref=inputs.dataset_evidence_ref,
        buffer_ref=buffer_ref,environment_ref=inputs.environment_ref,instrument_spec_ref=spec_ref,policy_digest=inputs.policy_digest)
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-holdout-primary-v1',
        operation='HOLDOUT',input_set_ref=family.input_set_ref,allowed_alpha_ids=(FAMILY_IDS[0],),body=body)
    primary=_read(store,primary_ref,PrimarySelection)
    custody=_read(store,custody_ref,CustodyRecord)
    forbidden={ref.content_sha256 for ref in (*h_data.row_refs,*buffer.row_refs,h_data.snapshot_ref,buffer.snapshot_ref,
        custody.ciphertext_ref,custody.custodian_attestation_ref)}
    return SimpleNamespace(store=store,inputs=inputs,intent_ref=intent_ref,primary=primary,forbidden=forbidden,
        h_data=h_data,buffer=buffer,context=context,registration=registration,closed=closed)


def _request(graph,*,body=None):
    intent=_read(graph.store,graph.intent_ref,P3OperationInput)
    if body is not None:
        intent=P3OperationInput.model_validate_json(canonical_json_bytes(_changed(intent,body=body)))
    ref=graph.store.put_bytes(canonical_json_bytes(intent),media_type='application/json')
    auth=_read(graph.store,_cli_authorization(graph.store,ref,graph.inputs.source),RunAuthorization)
    return intent,auth


def _reader(graph,reads):
    def read(ref):
        assert ref.content_sha256 not in graph.forbidden,'pre-mount validation read unreleased H1 material'
        reads.append(ref)
        return graph.store.read_bytes(ref)
    def write(*args,**kwargs):
        raise AssertionError('metadata reconstruction wrote a new research artifact')
    return SimpleNamespace(read_bytes=read,put_bytes=write)


@pytest.mark.parametrize('fault',[None,'epoch','family','cost','regime','integration','h_source','h_environment',
    'h_dataset','pit_dataset','pit_fold','pit_vintage','fold_rows','row_overlap','snapshot_overlap',
    'row_snapshot_overlap','ciphertext_attestation_overlap','ciphertext_row_overlap','attestation_snapshot_overlap',
    'primary_none','primary_head','primary_alias','custody_alias','registration','candidate','instrument','training','negative_threshold'])
def test_holdout_metadata_closes_graph_without_reading_h1(holdout_graph,monkeypatch,fault):
    graph=holdout_graph
    intent,authorization=_request(graph)
    body=intent.body.model_dump(mode='json')
    def changed(ref,model,**values):
        return graph.store.put_bytes(canonical_json_bytes(_changed(_read(graph.store,ref,model),**values)),media_type='application/json')
    h_inputs=_read(graph.store,intent.body.holdout_input_set_ref,InputSet)
    if fault in {'epoch','family','cost','regime','integration','h_source','h_environment','h_dataset'}:
        key,value={
            'epoch':('epoch_id','changed.epoch'),'family':('family_digest','9'*64),
            'cost':('cost_model',h_inputs.cost_model.model_copy(update={'fee_bps':11})),
            'regime':('regime_threshold_ref',graph.closed('other-regime')),
            'integration':('integration_receipt_ref',graph.closed('other-integration')),
            'h_source':('source',h_inputs.source.model_copy(update={'commit_sha':'9'*40})),
            'h_environment':('environment_ref',graph.closed('other-environment')),
            'h_dataset':('dataset_evidence_ref',intent.body.buffer_ref),
        }[fault]
        body['holdout_input_set_ref']=changed(intent.body.holdout_input_set_ref,InputSet,**{key:value})
    elif fault in {'pit_dataset','pit_fold','pit_vintage'}:
        values={'pit_dataset':dict(dataset_ref=intent.body.context_dataset_ref),
            'pit_fold':dict(fold_manifest_ref=graph.closed('other-fold')),
            'pit_vintage':dict(vintage_class='HISTORICAL_VINTAGE_VERIFIED',historical_vintage_verified=True)}[fault]
        ref=changed(h_inputs.pit_proof_ref,PITProof,**values)
        body['holdout_input_set_ref']=changed(intent.body.holdout_input_set_ref,InputSet,pit_proof_ref=ref)
    elif fault=='fold_rows':
        folds=_read(graph.store,h_inputs.fold_manifest_ref,FoldManifest)
        fold=_changed(folds.folds[0],decision_row_refs=graph.h_data.row_refs)
        ref=changed(h_inputs.fold_manifest_ref,FoldManifest,folds=(fold,))
        body['holdout_input_set_ref']=changed(intent.body.holdout_input_set_ref,InputSet,fold_manifest_ref=ref)
    elif fault=='row_overlap':
        refs=(graph.context.row_refs[0],*graph.h_data.row_refs[1:])
        body['holdout_dataset_ref']=changed(intent.body.holdout_dataset_ref,DatasetEvidence,row_refs=refs,
            ordered_rows_digest=hashlib.sha256(canonical_json_bytes([r.content_sha256 for r in refs])).hexdigest())
        body['holdout_input_set_ref']=changed(intent.body.holdout_input_set_ref,InputSet,dataset_evidence_ref=body['holdout_dataset_ref'])
    elif fault=='snapshot_overlap':
        body['buffer_ref']=changed(intent.body.buffer_ref,DatasetEvidence,snapshot_ref=graph.h_data.snapshot_ref)
    elif fault=='row_snapshot_overlap':
        body['buffer_ref']=changed(intent.body.buffer_ref,DatasetEvidence,snapshot_ref=graph.context.row_refs[0])
    elif fault in {'ciphertext_attestation_overlap','ciphertext_row_overlap','attestation_snapshot_overlap'}:
        custody=_read(graph.store,intent.body.custody_record_ref,CustodyRecord)
        values={'ciphertext_attestation_overlap':dict(ciphertext_ref=custody.custodian_attestation_ref),
            'ciphertext_row_overlap':dict(ciphertext_ref=graph.h_data.row_refs[0]),
            'attestation_snapshot_overlap':dict(custodian_attestation_ref=graph.context.snapshot_ref)}[fault]
        body['custody_record_ref']=changed(intent.body.custody_record_ref,CustodyRecord,**values)
    elif fault=='primary_none':
        body['primary_selection_ref']=changed(intent.body.primary_selection_ref,PrimarySelection,
            outcome='NONE_QUALIFIED',primary_alpha_id=None,primary_version=None,primary_candidate_head_ref=None)
    elif fault=='primary_head':
        body['primary_selection_ref']=changed(intent.body.primary_selection_ref,PrimarySelection,primary_candidate_head_ref=graph.closed('other-head'))
    elif fault=='primary_alias': body['primary_selection_ref']=graph.h_data.row_refs[0]
    elif fault=='custody_alias': body['custody_record_ref']=graph.buffer.row_refs[0]
    elif fault=='registration':
        body['registration_proof_ref']=changed(intent.body.registration_proof_ref,RegistrationProof,
            candidate_head_refs=tuple(reversed(graph.registration.candidate_head_refs)))
    elif fault=='candidate':
        import json
        from scripts.generate_p3_specs import _candidate_specs,POLICY_SOURCE
        body['candidate_spec_ref']=graph.store.put_bytes(canonical_json_bytes(
            _candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))[1]),media_type='application/json')
    elif fault=='instrument':
        from packages.alpha_lifecycle.contracts.execution import InstrumentSpec
        body['instrument_spec_ref']=changed(intent.body.instrument_spec_ref,InstrumentSpec,minimum_notional='11')
    intent,authorization=_request(graph,body=body)
    calls=[]
    def dependency(review,reader):
        calls.append(review)
        return graph.primary
    monkeypatch.setattr(primary_selection,'select_primary',dependency)
    # The reusable OOS seed has a shortened training range. This is a declared
    # owner-result stub, not a rewritten retained threshold or qualified family.
    original=holdout.validate_research_inputs
    def training_dependency(ref,reader):
        inputs,folds,dataset,threshold=original(ref,reader)
        if fault!='training':
            threshold=RegimeThreshold.model_validate(_changed(threshold,
                training_range=dict(start='2018-01-01',end='2021-08-31'),sample_count=1276,
                threshold='-0.02' if fault=='negative_threshold' else threshold.threshold))
        return inputs,folds,dataset,threshold
    monkeypatch.setattr(holdout,'validate_research_inputs',training_dependency)
    reads=[]
    def check():
        return holdout.validate_holdout_operation_input(intent,authorization,expected_source=graph.inputs.source,
            store=_reader(graph,reads),now=datetime.now(UTC))
    if fault:
        match='roles overlap' if fault.endswith('_overlap') else 'frozen training' if fault in {'training','negative_threshold'} else None
        with pytest.raises(ValueError,match=match): check()
    else:
        request,manifest=check()
        assert len(calls)==1 and request.primary_selection_ref==manifest.primary_selection_ref==intent.body.primary_selection_ref
        assert manifest.research_registration_ref==intent.body.registration_proof_ref
        assert manifest.context_dataset_ref==graph.inputs.dataset_evidence_ref
        assert manifest.policy_digest==graph.inputs.policy_digest
        assert request.holdout_input_set_ref==intent.body.holdout_input_set_ref
    assert reads


@pytest.mark.parametrize('fault',['untyped','source'])
def test_holdout_rejects_unqualified_integration_before_family(holdout_graph,monkeypatch,fault):
    graph=holdout_graph
    intent,authorization=_request(graph)
    receipt=_read(graph.store,graph.inputs.integration_receipt_ref,IntegrationReceipt)
    raw=b'{}' if fault=='untyped' else canonical_json_bytes(_changed(receipt,
        source=receipt.source.model_copy(update={'commit_sha':'9'*40})))
    bad_ref=graph.store.put_bytes(raw,media_type='application/json')
    body=intent.body.model_dump(mode='json')
    for name in ('input_set_ref','holdout_input_set_ref'):
        ref=intent.input_set_ref if name=='input_set_ref' else intent.body.holdout_input_set_ref
        updated=_changed(_read(graph.store,ref,InputSet),integration_receipt_ref=bad_ref)
        ref=graph.store.put_bytes(canonical_json_bytes(updated),media_type='application/json')
        if name=='input_set_ref': research_ref=ref
        else: body[name]=ref
    intent=P3OperationInput.model_validate_json(canonical_json_bytes(_changed(intent,input_set_ref=research_ref,body=body)))
    ref=graph.store.put_bytes(canonical_json_bytes(intent),media_type='application/json')
    authorization=_read(graph.store,_cli_authorization(graph.store,ref,graph.inputs.source),RunAuthorization)
    original=_reader(graph,[]).read_bytes
    def guarded(ref):
        assert ref!=graph.primary.family_review_ref,'unqualified receipt reached retained family traversal'
        return original(ref)
    with pytest.raises(ValueError):
        holdout.validate_holdout_operation_input(intent,authorization,expected_source=graph.inputs.source,
            store=SimpleNamespace(read_bytes=guarded),now=datetime.now(UTC))


def test_real_failed_family_cannot_obtain_holdout_projection(holdout_graph):
    intent,authorization=_request(holdout_graph)
    with pytest.raises(ValueError,match='family selection'):
        holdout.validate_holdout_operation_input(intent,authorization,expected_source=holdout_graph.inputs.source,
            store=_reader(holdout_graph,[]),now=datetime.now(UTC))
