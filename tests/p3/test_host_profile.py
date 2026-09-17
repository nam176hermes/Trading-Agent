"""Synthetic protected-reader injection tests; no profile or host is provisioned."""
import hashlib
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from tests.p3.test_spawn_capability import synthetic_provider


def test_unprotected_profile_cannot_issue_a_closure(tmp_path):
    from services.job_worker.p3_host_profile import read_official_profile
    path=tmp_path/'profile.json';path.write_bytes(b'{}\n');path.chmod(0o444)
    with pytest.raises(ValueError):
        read_official_profile(path,hashlib.sha256(b'{}\n').hexdigest(),
            job_id='job_'+'1'*32,payload=None,context={})


@pytest.fixture
def profile_inputs(synthetic_provider,monkeypatch):
    from services.job_worker import p3_host_profile as module
    from services.job_worker import p3_spawn
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    provider,job,closure=synthetic_provider
    store=provider._store
    intent=P3OperationInput.model_validate_json(store.read_bytes(job.payload.manifest_ref))
    inputs=InputSet.model_validate_json(store.read_bytes(intent.input_set_ref))
    auth=RunAuthorization.model_validate_json(store.read_bytes(job.payload.authorization_ref))
    inventory=store.put_bytes(canonical_json_bytes(dict(schema_version='p3-host-files-v1',files=[
        dict(source=str(m.source),target=str(m.target),size=m.size,mode=m.mode,sha256=m.sha256)
        for m in closure.mounts])),media_type='application/json')
    document=dict(schema_version='p3-official-host-profile-v1',job_id=job.job_id,
        payload=job.payload.model_dump(mode='json'),environment_ref=closure.environment_ref.model_dump(mode='json'),
        store_root=str(provider._store_root),output_root=str(provider._output_root),python_root=str(closure.python_root),
        worker_credentials_directory='/run/credentials/p3-worker',
        sandbox_executable=str(closure.sandbox.executable),sandbox_sha256=closure.sandbox.executable_sha256,
        sandbox_version=closure.sandbox.version,
        inventory_ref=inventory.model_dump(mode='json'),closure_sha256=closure.closure_sha256,
        integration_receipt_ref=inputs.integration_receipt_ref.model_dump(mode='json'),
        qualification_job_detail_ref=store.put_bytes(b'{}',media_type='application/json').model_dump(mode='json'),
        postgres_binary_sha256='b'*64)
    context=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='nam176hermes/Trading-Agent',
        GITHUB_REF='refs/heads/main',GITHUB_SHA=closure.source.commit_sha,GITHUB_REF_PROTECTED='true',
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_RUN_ID=str(auth.issuer_run_id),
        GITHUB_RUN_ATTEMPT=str(auth.issuer_attempt),
        GITHUB_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main')
    def digest():
        return hashlib.sha256(canonical_json_bytes(document)+b'\n').hexdigest()
    monkeypatch.setattr(module,'read_protected_canonical_json_current',lambda path:(document,digest()))
    monkeypatch.setattr(module,'_release_files',p3_spawn._release_files)
    monkeypatch.setattr(module,'inspect_python_runtime',p3_spawn.inspect_python_runtime)
    monkeypatch.setattr(module,'canonical_source_identity',lambda root:closure.source.model_dump(mode='json'))
    monkeypatch.setattr(module,'derive_project_status',lambda root:dict(gates={
        'HWC_SOURCE_READY':'PASS','PRE_P3_READY':'PASS'},p3_alpha_development_allowed=True))
    observations=[]
    def qualify(ref,**kwargs):
        observations.append((ref,kwargs))
    monkeypatch.setattr(module,'validate_integration_job_result',qualify)
    return module,provider,job,closure,document,context,digest,observations


def test_profile_reconstructs_bound_closure_and_rechecks_receipt(profile_inputs):
    module,provider,job,closure,document,context,digest,observations=profile_inputs
    profile,actual=module.read_official_profile(Path('/synthetic/profile.json'),digest(),
        job_id=job.job_id,payload=job.payload,context=context)
    assert actual==closure
    assert profile.store_root==str(provider._store_root)
    assert profile.worker_credentials_directory==document['worker_credentials_directory']
    assert len(observations)==1
    assert observations[0][1]['source']==closure.source
    assert observations[0][1]['job_detail_ref'].model_dump(mode='json')==document['qualification_job_detail_ref']


@pytest.mark.parametrize('value',[None,'relative','/run/../credentials'])
def test_profile_requires_explicit_canonical_worker_credentials(profile_inputs,value):
    module,_,job,_,document,context,digest,_=profile_inputs
    if value is None:
        document.pop('worker_credentials_directory')
    else:
        document['worker_credentials_directory']=value
    with pytest.raises(ValueError):
        module.read_official_profile(Path('/synthetic/profile.json'),digest(),
            job_id=job.job_id,payload=job.payload,context=context)


@pytest.mark.parametrize('fault',['digest','job','payload','source','context','gates','receipt','inventory','file'])
def test_profile_rejects_changed_binding_or_evidence(profile_inputs,monkeypatch,fault):
    module,provider,job,closure,document,context,digest,_=profile_inputs
    expected=digest()
    if fault=='digest':
        expected='0'*64
    elif fault=='job':
        document['job_id']='job_'+'9'*32
    elif fault=='payload':
        document['payload']['authorization_ref']=document['qualification_job_detail_ref']
    elif fault=='source':
        monkeypatch.setattr(module,'canonical_source_identity',lambda root:dict(
            closure.source.model_dump(mode='json'),commit_sha='9'*40))
    elif fault=='context':
        context['GITHUB_RUN_ATTEMPT']='2'
    elif fault=='gates':
        monkeypatch.setattr(module,'derive_project_status',lambda root:dict(gates={
            'HWC_SOURCE_READY':'HELD','PRE_P3_READY':'PASS'},p3_alpha_development_allowed=False))
    elif fault=='receipt':
        monkeypatch.setattr(module,'validate_integration_job_result',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('bad retained proof')))
    elif fault=='inventory':
        document['inventory_ref']=document['qualification_job_detail_ref']
    else:
        mount=closure.mounts[0];mount.source.chmod(0o600);mount.source.write_bytes(b'changed')
    if fault!='digest':
        expected=digest()  # Even a coherently root-pinned document must obey bindings.
    reasons=dict(digest='digest changed',job='another workflow job',payload='another workflow job',
        source='source is not current',context='current workflow',gates='source gates',
        receipt='bad retained proof',inventory='schema_version|files',file='file identity differs')
    with pytest.raises(ValueError,match=reasons[fault]):
        module.read_official_profile(Path('/synthetic/profile.json'),expected,
            job_id=job.job_id,payload=job.payload,context=context)


def test_holdout_profile_requires_a_custodian_endpoint(profile_inputs):
    from services.job_worker.p3_host_profile import OfficialHostProfile
    _,_,_,_,document,*_=profile_inputs
    document['payload']['operation']='HOLDOUT'
    document['payload']['logical_trial_id']='p3-holdout-primary-v1'
    with pytest.raises(ValueError,match='custodian'):
        OfficialHostProfile.model_validate_json(canonical_json_bytes(document))
    document['custodian_endpoint']=dict(schema_version='p3-custodian-endpoint-v1',
        socket_path='/run/p3/custodian.sock',custodian_uid=17001,research_uid=17002,
        custodian_identity='custodian',research_identity='research')
    assert OfficialHostProfile.model_validate_json(canonical_json_bytes(document)).custodian_endpoint.research_uid==17002
    document['payload']['operation']='BASELINES'
    document['payload']['logical_trial_id']='p3-baselines-v1'
    with pytest.raises(ValueError):OfficialHostProfile.model_validate_json(canonical_json_bytes(document))
