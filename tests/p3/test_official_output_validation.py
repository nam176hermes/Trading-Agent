"""Valid JSON and old shared CAS are insufficient official execution evidence."""
import io
from dataclasses import replace
from types import SimpleNamespace

import pytest

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import JobType
from services.job_worker.artifacts import ArtifactWriter
from services.job_worker.results import ResultValidator,ResultValidationError
from tests.jobs.test_worker_lifecycle import claim
from tests.p3.test_baseline_selection_validation import retained_baseline


def test_old_baseline_cas_cannot_succeed_without_private_attempt_evidence(retained_baseline,tmp_path):
    _,_,selection=retained_baseline
    job=replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,
        payload=SimpleNamespace(operation='BASELINES',logical_trial_id='p3-baselines-v1'))
    root=tmp_path/'artifacts'
    stream=ArtifactWriter(root).capture_stream(job.job_id,job.attempt_id,'stdout',
        io.BytesIO(canonical_json_bytes(selection)))
    validator=ResultValidator(tmp_path/'reports',tmp_path/'replay',root)
    with pytest.raises(ResultValidationError,match='output custody'):
        validator.validate_p3('p3-baseline-selection-v1',job,stream=stream,exit_code=0)
    assert not (root/'results').exists()


@pytest.fixture(scope='module')
def portable_attempt(tmp_path_factory):
    import os
    from pathlib import Path
    import sys
    from datetime import UTC,datetime,timedelta
    from packages.alpha_lifecycle import sandbox
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest,InputSet
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from scripts import run_p3_alpha_campaign as command
    from tests.p3.test_replica_execution import baseline_inputs,_seal
    from tests.p3.test_baseline_operation import _cli_authorization
    from services.job_store.worker_repository import ClaimedJob
    root=tmp_path_factory.mktemp('synthetic-official-output')
    from tests.p3 import test_replica_execution as replica_fixture
    from services.job_worker.p3_output_validation import _reference
    original_bar=replica_fixture._bar
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(replica_fixture,'_bar',lambda day:original_bar(day).model_copy(update={'partition_ref':_reference(b'{}')}))
        store,manifest_ref=baseline_inputs(root/'store')
    manifest=BaselineManifest.model_validate_json(store.read_bytes(manifest_ref))
    inputs=InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    from packages.alpha_lifecycle.contracts.results import RegimeThreshold
    from tests.p3.test_publication import _changed
    threshold=RegimeThreshold.model_validate_json(store.read_bytes(inputs.regime_threshold_ref))
    threshold=_changed(threshold,training_range={'start':str(threshold.training_range.start),
        'end':str(threshold.training_range.start+timedelta(days=299))},sample_count=280)
    threshold_ref=store.put_bytes(canonical_json_bytes(threshold),media_type='application/json')
    inputs=_changed(inputs,regime_threshold_ref=threshold_ref.model_dump(mode='json'))
    input_ref=store.put_bytes(canonical_json_bytes(inputs),media_type='application/json')
    manifest=_changed(manifest,input_set_ref=input_ref.model_dump(mode='json'))
    manifest_ref=store.put_bytes(canonical_json_bytes(manifest),media_type='application/json')
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-baselines-v1',
        operation='BASELINES',input_set_ref=manifest.input_set_ref,allowed_alpha_ids=[],body={'baseline_manifest_ref':manifest_ref})
    intent=P3OperationInput.model_validate_json(store.read_bytes(intent_ref))
    auth_ref=_cli_authorization(store,intent_ref,inputs.source)
    auth=RunAuthorization.model_validate_json(store.read_bytes(auth_ref))
    payload=build_alpha_campaign_payload(auth,inputs.source,'p3-baselines-v1',operation_input=intent)
    job=ClaimedJob('job_'+'1'*32,JobType.ALPHA_CAMPAIGN,payload,'attempt_'+'2'*32,1,
        'synthetic-worker','synthetic-token',datetime.now(UTC)+timedelta(hours=1),1)
    release=Path(__file__).resolve().parents[2]
    for name,value in [('manifest',intent_ref),('source',inputs.source),('environment',inputs.environment_ref),('authorization',auth_ref)]:
        (root/name).write_bytes(canonical_json_bytes(value))
    captured=SimpleNamespace(buffer=io.BytesIO())
    with pytest.MonkeyPatch.context() as patch:
        # Real deterministic children; portable transport intentionally replaces host isolation.
        patch.setattr(sandbox,'require_official_sandbox',lambda path:path)
        patch.setattr(sandbox.BubblewrapExecutor,'_argv',lambda self,request,result,output,seccomp_fd:
            (sys.executable,'-I','-B',str(release/'scripts/run_p3_evaluation_child.py'),str(request),str(root/'store'),str(result)))
        patch.setattr(sys,'argv',[str(command.__file__),'--manifest-ref',str(root/'manifest'),
            '--source',str(root/'source'),'--environment-ref',str(root/'environment'),
            '--store',str(root/'store'),'--release',str(release),'--python',sys.executable,
            '--sandbox-policy-digest','c'*64,'--logical-trial-id','p3-baselines-v1',
            '--output',str(root/'output'),'--job-id',job.job_id,'--authorization-ref',str(root/'authorization')])
        patch.setattr(command.sys,'stdout',captured)
        command.main()
    output={str(path.relative_to(root/'output')):(path.read_bytes(),path.stat().st_mode & 0o777)
        for path in (root/'output').rglob('*') if path.is_file()}
    # Preserve the input snapshot separately; tests may deliberately preload old output CAS.
    initial={path.name:path.read_bytes() for path in (root/'store').iterdir()}
    return job,captured.buffer.getvalue(),initial,output


@pytest.mark.parametrize('fault',[None,'empty','missing','extra','wrong_replica','invalid_stdout','omit_pack','duplicate','permutation'])
def test_official_baseline_requires_exact_attempt_output_closure(portable_attempt,tmp_path,fault):
    import hashlib
    import os
    from packages.alpha_lifecycle.contracts.results import BaselineSelection
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_worker.p3_output import P3OutputCustody
    job,raw,initial,output=portable_attempt
    retained=tmp_path/'retained'
    retained.mkdir(mode=0o700)
    store=LocalArtifactStore(retained)
    for value in initial.values():
        store.put_bytes(value,media_type='application/json')
    for name,(value,_) in output.items():
        if name.startswith('artifacts/'):
            store.put_bytes(value,media_type='application/json')
    parent=tmp_path/'runs'
    parent.mkdir(mode=0o700)
    attempt=parent/hashlib.sha256(f'{job.job_id}/{job.attempt_id}'.encode()).hexdigest()
    attempt.mkdir(mode=0o700)
    output=dict(output)
    selection=BaselineSelection.model_validate_json(raw)
    if fault == 'empty':
        output={}
    elif fault == 'missing':
        del output['artifacts/'+selection.pack_ref.locator]
    elif fault == 'extra':
        extra=b'{"unrelated":true}'
        output['artifacts/'+hashlib.sha256(extra).hexdigest()+'.blob']=(extra,0o600)
    elif fault == 'wrong_replica':
        output['r2/result.json']=(b'{}',0o600)
    elif fault == 'invalid_stdout':
        raw=b'{}'
    elif fault in {'omit_pack','duplicate','permutation'}:
        import json
        from packages.alpha_lifecycle.contracts.results import ReplayProof,ReplayReceipt
        from tests.p3.test_publication import _changed
        from services.job_worker.p3_output_validation import _reference
        def read(ref,model):
            return model.model_validate_json(output['artifacts/'+ref.locator][0])
        def replace_artifact(old_ref,value):
            new_raw=canonical_json_bytes(value)
            new_ref=_reference(new_raw)
            output.pop('artifacts/'+old_ref.locator,None)
            output['artifacts/'+new_ref.locator]=(new_raw,0o600)
            return new_ref
        proof=read(selection.baseline_replay_proof_ref,ReplayProof)
        receipts=[read(ref,ReplayReceipt) for ref in proof.receipt_refs]
        inventory_path='artifacts/'+receipts[0].output_inventory_digest+'.blob'
        old_raw=output[inventory_path][0]
        entries=json.loads(old_raw)
        if fault == 'omit_pack':
            entries=[entry for entry in entries if entry['locator'] != selection.pack_ref.locator]
        elif fault == 'duplicate':
            entries.append(entries[0])
        else:
            entries.reverse()
        assert canonical_json_bytes(entries) != old_raw
        new_inventory=replace_artifact(_reference(old_raw),entries)
        refs=[replace_artifact(ref,_changed(receipt,output_inventory_digest=new_inventory.content_sha256))
            for ref,receipt in zip(proof.receipt_refs,receipts,strict=True)]
        new_proof=replace_artifact(selection.baseline_replay_proof_ref,
            _changed(proof,receipt_refs=[ref.model_dump(mode='json') for ref in refs]))
        changed_selection=_changed(selection,baseline_replay_proof_ref=new_proof.model_dump(mode='json'))
        replace_artifact(_reference(canonical_json_bytes(selection)),changed_selection)
        raw=canonical_json_bytes(changed_selection)
    for name,(value,mode) in output.items():
        path=attempt/name
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        path.write_bytes(value)
        path.chmod(mode)
    pfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    ofd=os.open(attempt,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        handle=P3OutputCustody(pfd,ofd,attempt.name,store)
    finally:
        os.close(pfd)
        os.close(ofd)
    inventory_ref=handle.retain()
    artifacts=tmp_path/'job-artifacts'
    stream=ArtifactWriter(artifacts).capture_stream(job.job_id,job.attempt_id,'stdout',io.BytesIO(raw))
    validator=ResultValidator(tmp_path/'reports',tmp_path/'replay',artifacts)
    try:
        if fault:
            with pytest.raises(ResultValidationError):
                validator.validate_p3('p3-baseline-selection-v1',job,stream=stream,exit_code=0,
                    output_custody=handle,output_inventory_ref=inventory_ref)
            assert attempt.exists()
        else:
            result=validator.validate_p3('p3-baseline-selection-v1',job,stream=stream,exit_code=0,
                output_custody=handle,output_inventory_ref=inventory_ref)
            assert result.validation_metadata['result_digest'] == selection.digest
            assert attempt.exists()
            handle.cleanup()
            assert not attempt.exists()
    finally:
        handle.abandon()


@pytest.mark.parametrize('fault',[None,'empty','extra'])
def test_registration_proposal_is_reconstructed_before_publication(portable_attempt,tmp_path,fault):
    import hashlib,json,os
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest
    from packages.alpha_lifecycle.contracts.results import BaselineSelection
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.operation_input import P3OperationInput,FAMILY_IDS
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_worker.p3_output import P3OutputCustody
    from services.job_worker.p3_publication_producer import prepare_family_registration
    from scripts.generate_p3_specs import POLICY_SOURCE,_candidate_specs
    from tests.p3.test_replica_execution import _seal
    from tests.p3.test_baseline_operation import _cli_authorization
    from tests.p3.test_registration_proof import registration_chain
    job,raw,initial,baseline_output=portable_attempt
    root=tmp_path/'retained'
    root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    for value in initial.values():
        store.put_bytes(value,media_type='application/json')
    for name,(value,_) in baseline_output.items():
        if name.startswith('artifacts/'):
            store.put_bytes(value,media_type='application/json')
    selection=BaselineSelection.model_validate_json(raw)
    baseline_intent=P3OperationInput.model_validate_json(store.read_bytes(job.payload.manifest_ref))
    manifest=BaselineManifest.model_validate_json(store.read_bytes(baseline_intent.body.baseline_manifest_ref))
    _,evidence,_,_=registration_chain((store,manifest.input_set_ref,selection))
    specs=tuple(store.put_bytes(canonical_json_bytes(value),media_type='application/json')
        for value in _candidate_specs(json.loads(POLICY_SOURCE.read_bytes())))
    intent_ref=_seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-register-family-v1',
        operation='REGISTER_FAMILY',input_set_ref=manifest.input_set_ref,allowed_alpha_ids=FAMILY_IDS,
        body=dict(baseline_selection_ref=evidence.baseline_selection_ref,candidate_spec_refs=specs,
            candidate_record_refs=evidence.candidate_record_refs))
    intent=P3OperationInput.model_validate_json(store.read_bytes(intent_ref))
    auth_ref=_cli_authorization(store,intent_ref,job.payload.expected_source)
    auth=RunAuthorization.model_validate_json(store.read_bytes(auth_ref))
    payload=build_alpha_campaign_payload(auth,job.payload.expected_source,'p3-register-family-v1',operation_input=intent)
    job=replace(job,payload=payload)
    parent=tmp_path/'runs'
    parent.mkdir(mode=0o700)
    output=parent/hashlib.sha256(f'{job.job_id}/{job.attempt_id}'.encode()).hexdigest()
    output.mkdir(mode=0o700)
    (output/'artifacts').mkdir(mode=0o700)
    proposal=prepare_family_registration(intent,job_id=job.job_id,observed_at=auth.issued_at,
        expires_at=auth.expires_at,store=ReplicaArtifactStore(root,output/'artifacts'))
    if fault == 'empty':
        for path in (output/'artifacts').iterdir():
            store.put_bytes(path.read_bytes(),media_type='application/json')
            path.unlink()
    elif fault == 'extra':
        LocalArtifactStore(output/'artifacts').put_bytes(b'{"extra":true}',media_type='application/json')
    pfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    ofd=os.open(output,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        handle=P3OutputCustody(pfd,ofd,output.name,store)
    finally:
        os.close(pfd)
        os.close(ofd)
    inventory_ref=handle.retain()
    artifacts=tmp_path/'job-artifacts'
    stream=ArtifactWriter(artifacts).capture_stream(job.job_id,job.attempt_id,'stdout',io.BytesIO(canonical_json_bytes(proposal)))
    validator=ResultValidator(tmp_path/'reports',tmp_path/'replay',artifacts)
    try:
        if fault:
            with pytest.raises(ResultValidationError):
                validator.validate_p3('p3-publication-register-v1',job,stream=stream,exit_code=0,
                    output_custody=handle,output_inventory_ref=inventory_ref)
            assert output.exists()
        else:
            result=validator.validate_p3('p3-publication-register-v1',job,stream=stream,exit_code=0,
                output_custody=handle,output_inventory_ref=inventory_ref)
            assert result.request == proposal.request and result.entries == proposal.entries
            assert output.exists()
            handle.cleanup()
            assert not output.exists()
    finally:
        handle.abandon()
