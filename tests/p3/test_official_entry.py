"""Explicit fake host boundaries verify entry order, not host qualification."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from tests.p3.test_spawn_capability import synthetic_provider


def test_main_routes_official_once_before_generic_database_setup(monkeypatch):
    from services.job_worker import main,p3_official
    monkeypatch.setenv('TRADING_WORKER_PROFILE','p3-official-v1')
    monkeypatch.setattr(main,'attest_worker_runtime_authority',lambda:pytest.fail('generic authority ran'))
    def once(values,*,build_worker):
        assert build_worker is main.build_worker
        return 7
    monkeypatch.setattr(p3_official,'run_official_once',once)
    assert main.main()==7


@pytest.mark.parametrize('fault',[None,'enqueue','profile','probe','changed','revision','after_credentials','credential_directory'])
def test_official_entry_has_no_database_before_all_host_checks(synthetic_provider,monkeypatch,fault):
    from services.job_worker import p3_official as module
    provider,job,closure=synthetic_provider
    calls=[]
    values=dict(P3_OPERATION='p3-baselines-v1',P3_AUTHORITY_REQUEST_FILE='/synthetic/request',
        P3_PREFLIGHT_DIRECTORY='/synthetic/enqueue',P3_ARTIFACT_ROOT=str(provider._store_root),
        P3_MANIFEST_FILE='/synthetic/manifest',P3_REVIEW_FILE='/synthetic/review',
        GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='2',
        CREDENTIALS_DIRECTORY='/synthetic/other' if fault=='credential_directory' else '/synthetic/protected-worker-credentials')
    profile=SimpleNamespace(store_root=str(provider._store_root),output_root=str(provider._output_root),
        worker_credentials_directory='/synthetic/protected-worker-credentials')
    def step(name,value):
        calls.append(name)
        if fault==name:
            raise ValueError('synthetic '+name+' refusal')
        return value
    monkeypatch.setattr(module,'read_enqueued_job',lambda *args,**kwargs:step('enqueue',(job.job_id,SimpleNamespace(payload=job.payload),'synthetic-operator')))
    def protected(path):
        assert path==Path('/run/trading-agent-p3/123-2/profile.json')
        return {},'a'*64
    monkeypatch.setattr(module,'read_protected_canonical_json_current',protected)
    reads=[]
    def read_profile(path,digest,**kwargs):
        assert digest=='a'*64 and kwargs['job_id']==job.job_id and kwargs['payload']==job.payload
        reads.append(digest)
        if fault=='changed' and len(reads)>1:
            raise ValueError('synthetic profile changed')
        if fault=='after_credentials' and 'credentials' in calls:
            raise ValueError('synthetic authorization expired during credential loading')
        return step('profile',(profile,closure))
    monkeypatch.setattr(module,'read_official_profile',read_profile)
    monkeypatch.setattr(module,'probe_p3_startup',lambda *args,**kwargs:step('probe',{}))
    monkeypatch.setattr(module,'attest_worker_runtime_authority',lambda:step('authority',SimpleNamespace(
        application_revision='0'*40 if fault=='revision' else closure.source.commit_sha)))
    def settings(env,**kwargs):
        assert env=={'CREDENTIALS_DIRECTORY':profile.worker_credentials_directory}
        assert kwargs=={'expected_user':'trading_job_worker'}
        return step('credentials',object())
    monkeypatch.setattr(module.JobStoreSettings,'from_systemd_credentials',settings)
    class Repository:
        def __init__(self,*args): step('database',None)
        def __enter__(self): return self
        def __exit__(self,*args): calls.append('closed')
        def assert_p3_runtime_identity(self): calls.append('schema')
    monkeypatch.setattr(module,'WorkerRepository',Repository)
    def worker(repository,source,**kwargs):
        assert kwargs['p3_job_id']==job.job_id
        actual=kwargs['p3_spawn_provider']._attest_closure()
        assert actual==closure
        return SimpleNamespace(run_once=lambda:step('run',True))
    if fault:
        with pytest.raises(ValueError,match='synthetic|revision|credential'):
            module.run_official_once(values,build_worker=worker)
        if fault!='after_credentials':
            assert 'credentials' not in calls
        assert 'database' not in calls
    else:
        assert module.run_official_once(values,build_worker=worker)==0
        assert calls[:8]==['enqueue','profile','probe','profile','authority','credentials','profile','database']
        assert calls[-2:]==['run','closed']


@pytest.mark.parametrize('operation',['p3-native-parity-v1','p3-phase-exit-v1','p3-integration-fixture-v1','unknown'])
def test_unimplemented_official_operation_never_reads_host_authority(monkeypatch,operation):
    from services.job_worker import p3_official as module
    monkeypatch.setattr(module,'read_enqueued_job',lambda *args,**kwargs:pytest.fail('unsupported operation reached authority'))
    with pytest.raises(ValueError,match='operation'):
        module.run_official_once({'P3_OPERATION':operation},build_worker=lambda *args:pytest.fail('unexpected worker'))
