"""Fixture workflow ordering uses fake boundaries and grants no host authority."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('fault',[None,'enqueue','changed','revision','credentials','payload'])
def test_fixture_entry_binds_enqueue_before_database_and_recovery(monkeypatch,fault):
    from services.job_worker import main
    from scripts import p3_authority
    from tests.p3.test_job_api import _alpha_request
    payload=_alpha_request().payload.model_copy(update={
        'operation':'PARITY','logical_trial_id':'p3-integration-fixture-v1'})
    calls=[]
    values=dict(TRADING_WORKER_PROFILE='p3-fixture-v1',P3_OPERATION='p3-integration-fixture-v1',
        P3_AUTHORITY_REQUEST_FILE='/synthetic/request',P3_PREFLIGHT_DIRECTORY='/synthetic/preflight',
        P3_ARTIFACT_ROOT='/synthetic/store',P3_MANIFEST_FILE='/synthetic/manifest',P3_REVIEW_FILE='/synthetic/review')
    for name,value in values.items(): monkeypatch.setenv(name,value)
    if fault=='credentials':
        monkeypatch.delenv('CREDENTIALS_DIRECTORY',raising=False)
    else:
        monkeypatch.setenv('CREDENTIALS_DIRECTORY','/synthetic/worker-credentials')
    def read_job(*args,**kwargs):
        if fault=='enqueue' or fault=='changed' and 'credentials' in calls:
            raise ValueError('synthetic enqueue changed or expired')
        calls.append('enqueue')
        return 'job_fixture',SimpleNamespace(payload=SimpleNamespace() if fault=='payload' else payload),'synthetic.operator'
    monkeypatch.setattr(p3_authority,'read_enqueued_job',read_job)
    monkeypatch.setattr(main,'attest_worker_runtime_authority',lambda:calls.append('authority') or SimpleNamespace(
        application_revision='0'*40 if fault=='revision' else payload.expected_source.commit_sha))
    monkeypatch.setattr(main.JobStoreSettings,'from_env',lambda **kwargs:pytest.fail('fixture used ambient database values'))
    def credentials(**kwargs):
        calls.append('credentials')
        assert kwargs=={'expected_user':'trading_job_worker'}
        return object()
    monkeypatch.setattr(main.JobStoreSettings,'from_systemd_credentials',credentials)
    class Repository:
        def __init__(self,*args): calls.append('database')
        def __enter__(self): return self
        def __exit__(self,*args): calls.append('closed')
        def assert_p3_runtime_identity(self): calls.append('schema')
    monkeypatch.setattr(main,'WorkerRepository',Repository)
    def build(repository,source,*,authority,job_id=None):
        assert job_id=='job_fixture'
        calls.append('build')
        return SimpleNamespace(run_once=lambda:calls.append('run') or True)
    monkeypatch.setattr(main,'build_p3_fixture_worker',build)
    if fault:
        with pytest.raises(ValueError,match='synthetic|revision|credential|operation'):
            main.main()
        assert 'database' not in calls and 'build' not in calls
    else:
        assert main.main()==0
        assert calls==['enqueue','authority','credentials','enqueue','database','schema','build','run','closed']
