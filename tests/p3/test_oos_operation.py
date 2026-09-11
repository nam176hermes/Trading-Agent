"""Real portable OOS children with synthetic review; never official qualification."""
import hashlib
from pathlib import Path
import sys
import pytest

from packages.alpha_lifecycle.baseline_campaign import _read
from packages.alpha_lifecycle.contracts.execution import EvaluationManifest,InputSet
from packages.alpha_lifecycle.contracts.lifecycle import PrePublicationEvidence
from packages.alpha_lifecycle.contracts.results import QualificationBundle,ReplayProof,ReplayReceipt
from packages.alpha_lifecycle.operation_input import FAMILY_IDS
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
from packages.alpha_lifecycle.sandbox import BubblewrapExecutor
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_sql import PublicationProposal
from tests.p3.test_baseline_operation import _cli_authorization
from tests.p3.test_replica_execution import _seal


def test_oos_cli_runs_three_children_and_retains_failed_candidate_proposal(synthetic_oos,tmp_path,monkeypatch,capsys):
    from scripts import run_p3_alpha_campaign as command
    store,evaluation,_=synthetic_oos
    manifest=_read(store,evaluation.manifest_ref,EvaluationManifest)
    inputs=_read(store,manifest.input_set_ref,InputSet)
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-oos-a0-v1',
        operation='OOS',input_set_ref=manifest.input_set_ref,allowed_alpha_ids=(FAMILY_IDS[0],),
        body=dict(evaluation_manifest_ref=evaluation.manifest_ref))
    auth_ref=_cli_authorization(store,intent_ref,inputs.source)
    for name,value in [('manifest',intent_ref),('source',inputs.source),('environment',inputs.environment_ref),('authorization',auth_ref)]:
        (tmp_path/name).write_bytes(canonical_json_bytes(value))
    root=Path(__file__).resolve().parents[2]
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda value:Path('/usr/bin/bwrap'))
    calls=[]
    def executor(**kwargs):
        result=BubblewrapExecutor(**kwargs)
        def argv(request,result_path,output,seccomp_fd):
            calls.append(output.name)
            return (sys.executable,'-I','-B',str(root/'scripts/run_p3_evaluation_child.py'),
                str(request),str(store._root),str(result_path))
        monkeypatch.setattr(result,'_argv',argv)
        return result
    monkeypatch.setattr(command,'BubblewrapExecutor',executor)
    monkeypatch.setattr(sys,'argv',[str(command.__file__),
        '--manifest-ref',str(tmp_path/'manifest'),'--source',str(tmp_path/'source'),
        '--environment-ref',str(tmp_path/'environment'),'--authorization-ref',str(tmp_path/'authorization'),
        '--job-id','synthetic_oos_job','--store',str(store._root),'--release',str(root),'--python',sys.executable,
        '--sandbox-policy-digest','c'*64,'--logical-trial-id','p3-oos-a0-v1','--output',str(tmp_path/'runs')])
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store._root.iterdir()}
    command.main()
    proposal=PublicationProposal.model_validate_json(capsys.readouterr().out)
    assert calls==['r1','r2','r3']
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store._root.iterdir()}
    reader=ReplicaArtifactStore(store._root,tmp_path/'runs'/'artifacts')
    evidence=_read(reader,proposal.request.evidence_ref,PrePublicationEvidence)
    bundle=_read(reader,evidence.qualification_bundle_ref,QualificationBundle)
    proof=_read(reader,bundle.replay_proof_ref,ReplayProof)
    assert bundle.alpha_verdict=='FAIL'
    assert proposal.request.job_id=='synthetic_oos_job'
    assert proposal.request.stage=='RESEARCH_DECISION' and len(proposal.entries)==2
    assert tuple(_read(reader,ref,ReplayReceipt).replicate for ref in proof.receipt_refs)==('R1','R2','R3')
    raw=canonical_json_bytes(proposal)
    assert (tmp_path/'runs'/'artifacts'/(hashlib.sha256(raw).hexdigest()+'.blob')).read_bytes()==raw

    # Worker readback is a separate seam; this fixture still grants no SQL authority.
    import os
    from datetime import UTC,datetime,timedelta
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from packages.job_contracts import JobType
    from services.job_store.records import ClaimedJob
    from services.job_worker.p3_output import P3OutputCustody
    from services.job_worker.p3_output_validation import validate_official_output
    intent=_read(store,intent_ref,P3OperationInput)
    auth=_read(store,auth_ref,RunAuthorization)
    payload=build_alpha_campaign_payload(auth,inputs.source,intent.workflow_operation,operation_input=intent)
    job=ClaimedJob('synthetic_oos_job',JobType.ALPHA_CAMPAIGN,payload,'attempt_'+'2'*32,1,
        'synthetic-worker','synthetic-token',datetime.now(UTC)+timedelta(hours=1),1)
    parent=tmp_path/'owned'
    parent.mkdir(mode=0o700)
    name=hashlib.sha256(f'{job.job_id}/{job.attempt_id}'.encode()).hexdigest()
    (tmp_path/'runs').rename(parent/name)
    pfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    ofd=os.open(parent/name,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        custody=P3OutputCustody(pfd,ofd,name,store)
    finally:
        os.close(pfd)
        os.close(ofd)
    try:
        inventory_ref=custody.retain()
        validate_official_output(job,proposal,custody,inventory_ref)
        from services.job_worker.p3_output_validation import _validate_replicas,_reference
        output_refs=tuple(ref for path,ref in custody.inventory.items() if path.startswith('artifacts/'))
        receipt=_read(store,proof.receipt_refs[1],ReplayReceipt)
        for fault in ('wrong_result','wrong_manifest','missing_inventory','extra_file'):
            inventory=dict(custody.inventory)
            refs=output_refs
            if fault=='wrong_result':
                inventory['r2/result.json']=_reference(b'{}')
            elif fault=='wrong_manifest':
                inventory['r2/manifest-ref.json']=_reference(b'{}')
            elif fault=='missing_inventory':
                refs=tuple(ref for ref in refs if ref.content_sha256!=receipt.output_inventory_digest)
            else:
                inventory['undeclared.json']=_reference(b'{}')
            with pytest.raises(ValueError):
                _validate_replicas(proof,evaluation.manifest_ref,bundle.evaluation_ref,job,
                    inventory,refs,store)
    finally:
        custody.abandon()
