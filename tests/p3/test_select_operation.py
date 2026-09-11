"""Four real synthetic candidate executions; no canonical SQL or protected authority."""
from datetime import UTC,datetime,timedelta
import hashlib
import json
from pathlib import Path
import sys

import pytest

from packages.alpha_lifecycle.baseline_campaign import _read
from packages.alpha_lifecycle.contracts.authority import FamilyReview,PrimarySelection
from packages.alpha_lifecycle.contracts.execution import EvaluationManifest,InputSet
from packages.alpha_lifecycle.contracts.lifecycle import RegistrationProof
from packages.alpha_lifecycle.contracts.results import ReplayReceipt,EvaluationResult
from packages.alpha_lifecycle.operation_input import FAMILY_IDS
from packages.alpha_lifecycle.primary_selection import family_disclosure_digest
from packages.alpha_lifecycle.replay import run_replays
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_qualification import synthetic_oos
from tests.p3.test_candidate_closure import _candidate_closure
from tests.p3.test_publication import _changed
from tests.p3.test_replica_execution import _seal
from tests.p3.test_baseline_operation import _cli_authorization


@pytest.fixture(scope='module')
def retained_family(synthetic_oos,tmp_path_factory):
    store,evaluation,proof=synthetic_oos
    manifest=_read(store,evaluation.manifest_ref,EvaluationManifest)
    inputs=_read(store,manifest.input_set_ref,InputSet)
    registration=_read(store,manifest.registration_proof_ref,RegistrationProof)
    from scripts.generate_p3_specs import _candidate_specs,POLICY_SOURCE
    specs=_candidate_specs(json.loads(POLICY_SOURCE.read_bytes()))
    root=Path(__file__).resolve().parents[2]
    reports=[]; outcomes=[]
    for index in range(4):
        if index:
            spec_ref=store.put_bytes(canonical_json_bytes(specs[index]),media_type='application/json')
            candidate=_changed(manifest,candidate_spec_ref=spec_ref,candidate_head_ref=registration.candidate_head_refs[index])
            candidate_ref=store.put_bytes(canonical_json_bytes(candidate),media_type='application/json')
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda value:Path('/usr/bin/bwrap'))
                executor=BubblewrapExecutor(store=store,store_root=store._root,release_root=root,python=Path(sys.executable),
                    source=inputs.source,environment_ref=inputs.environment_ref,sandbox_policy_digest='c'*64)
                patch.setattr(executor,'_argv',lambda request,result,output,seccomp_fd:(sys.executable,'-I','-B',
                    str(root/'scripts/run_p3_evaluation_child.py'),str(request),str(store._root),str(result)))
                proof=run_replays(candidate_ref,executor,logical_trial_id=f'p3-oos-a{index}-v1',
                    output_root=tmp_path_factory.mktemp(f'synthetic-family-a{index}'))
            evaluation=_read(store,_read(store,proof.receipt_refs[0],ReplayReceipt).result_ref,EvaluationResult)
        _,closure_ref,_,_=_candidate_closure((store,evaluation,proof),index=index)
        reports.append(closure_ref)
        result_ref=store.put_bytes(canonical_json_bytes(evaluation),media_type='application/json')
        outcomes.extend(_seal(store,schema_version='p3-trial-outcome-v1',trial_key=key,status='COMPLETED',
            result_ref=result_ref,execution_receipt_refs=proof.receipt_refs) for key in evaluation.deterministic_trial_keys)
    placeholder=store.put_bytes(b'{}',media_type='application/json')
    family_ref=_seal(store,schema_version='p3-family-review-v1',input_set_ref=manifest.input_set_ref,
        candidate_report_refs=reports,trial_outcome_refs=outcomes,review_ref=placeholder,complete_disclosure=True)
    family=_read(store,family_ref,FamilyReview)
    now=datetime.now(UTC)
    def utc(value): return value.isoformat(timespec='microseconds').replace('+00:00','Z')
    review_ref=_seal(store,schema_version='p3-review-approval-v1',source=inputs.source,
        subject_digests=(family_disclosure_digest(family),),operator_identity='synthetic-operator',
        reviewer_identity='synthetic-family-reviewer',review_execution_id='synthetic-family-review',verdict='APPROVED',
        issued_at=utc(now-timedelta(minutes=2)),expires_at=utc(now+timedelta(hours=2)),evidence_ref=placeholder,
        authority=dict(broker=False,live=False,network=False,production=False))
    family=_changed(family,review_ref=review_ref)
    family_ref=store.put_bytes(canonical_json_bytes(family),media_type='application/json')
    return store,inputs,family_ref


@pytest.mark.parametrize('fault',[None,'forged_primary'])
def test_select_primary_cli_preserves_complete_failed_family(retained_family,tmp_path,monkeypatch,capsys,fault):
    from scripts import run_p3_alpha_campaign as command
    store,inputs,family_ref=retained_family
    family=_read(store,family_ref,FamilyReview)
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-select-primary-v1',
        operation='OOS',input_set_ref=family.input_set_ref,allowed_alpha_ids=FAMILY_IDS,body=dict(family_review_ref=family_ref))
    auth_ref=_cli_authorization(store,intent_ref,inputs.source)
    for name,value in [('manifest',intent_ref),('source',inputs.source),('environment',inputs.environment_ref),('authorization',auth_ref)]:
        (tmp_path/name).write_bytes(canonical_json_bytes(value))
    root=Path(__file__).resolve().parents[2]
    def no_execution(**kwargs): raise AssertionError('selection started another research trial')
    monkeypatch.setattr(command,'BubblewrapExecutor',no_execution)
    monkeypatch.setattr(sys,'argv',[str(command.__file__),
        '--manifest-ref',str(tmp_path/'manifest'),'--source',str(tmp_path/'source'),
        '--environment-ref',str(tmp_path/'environment'),'--authorization-ref',str(tmp_path/'authorization'),
        '--job-id','synthetic_select_job','--store',str(store._root),'--release',str(root),'--python',sys.executable,
        '--sandbox-policy-digest','c'*64,'--logical-trial-id','p3-select-primary-v1','--output',str(tmp_path/'runs')])
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store._root.iterdir()}
    command.main()
    result=PrimarySelection.model_validate_json(capsys.readouterr().out)
    assert result.outcome=='NONE_QUALIFIED' and result.primary_alpha_id is None
    assert result.family_review_ref==family_ref and len(family.trial_outcome_refs)==28
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store._root.iterdir()}
    raw=canonical_json_bytes(result)
    assert (tmp_path/'runs'/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')).read_bytes()==raw

    if fault=='forged_primary':
        from packages.alpha_lifecycle.contracts.lifecycle import CampaignClosureReport,PublicationReceipt
        closure=_read(store,family.candidate_report_refs[0],CampaignClosureReport)
        receipt=_read(store,closure.publication_ref,PublicationReceipt)
        result=_changed(result,outcome='SELECTED',primary_alpha_id=FAMILY_IDS[0],primary_version='1.0.0',
            primary_candidate_head_ref=receipt.registry_event_refs[-1])
        (tmp_path/'runs'/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')).unlink()
        raw=canonical_json_bytes(result)
        (tmp_path/'runs'/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')).write_bytes(raw)
    import os
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from packages.job_contracts import JobType
    from services.job_store.records import ClaimedJob
    from services.job_worker.p3_output import P3OutputCustody
    from services.job_worker.p3_output_validation import validate_official_output
    intent=_read(store,intent_ref,P3OperationInput)
    authorization=_read(store,auth_ref,RunAuthorization)
    payload=build_alpha_campaign_payload(authorization,inputs.source,intent.workflow_operation,operation_input=intent)
    job=ClaimedJob('synthetic_select_job',JobType.ALPHA_CAMPAIGN,payload,'attempt_'+'2'*32,1,
        'synthetic-worker','synthetic-token',datetime.now(UTC)+timedelta(hours=1),1)
    parent=tmp_path/'owned'; parent.mkdir(mode=0o700)
    name=hashlib.sha256(f'{job.job_id}/{job.attempt_id}'.encode()).hexdigest()
    (tmp_path/'runs').rename(parent/name)
    pfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    ofd=os.open(parent/name,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        custody=P3OutputCustody(pfd,ofd,name,store)
    finally:
        os.close(pfd); os.close(ofd)
    try:
        inventory_ref=custody.retain()
        if fault is None:
            validate_official_output(job,result,custody,inventory_ref)
        else:
            with pytest.raises(ValueError,match='recomputation'):
                validate_official_output(job,result,custody,inventory_ref)
    finally:
        custody.abandon()
