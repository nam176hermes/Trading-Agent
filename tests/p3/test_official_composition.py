"""Composition checks use synthetic attestation; they never activate a host."""
from types import SimpleNamespace
import pytest

from tests.p3.test_spawn_capability import synthetic_provider


@pytest.mark.parametrize('local',[False,True])
@pytest.mark.parametrize('job_id',['job_'+'a'*32,'job_'+'b'*32])
def test_official_composition_reuses_canonical_worker_publication_and_recovery(synthetic_provider,monkeypatch,tmp_path,job_id,local):
    from services.job_worker import main
    provider,job,_=synthetic_provider
    calls=[]
    publisher=object()
    class Repository:
        def recover_expired_leases(self,inspector,**kwargs):
            calls.append(('recover',kwargs))
        def alpha_publication_repository(self,store):
            assert store is provider._store
            return publisher
    authority=SimpleNamespace(runtime_authority=SimpleNamespace(scope='PACKAGE6_STAGING_ONLY'),runtime_paths=SimpleNamespace(
        artifact_root=tmp_path,reports_root=tmp_path,signals_root=tmp_path),
        safety_snapshot_path=tmp_path/'safety',safety_exporter_commit='a'*40,
        safety_source_fingerprint='b'*64,application_revision=job.payload.expected_source.commit_sha)
    def environment(runtime,values):
        assert 'TRADING_PACKAGE6_APPROVAL_SHA256' not in values
        return object()
    monkeypatch.setattr(main.ResearchEnvironmentSettings,'from_authority',environment)
    monkeypatch.setattr(main,'SafetyStateClient',lambda *args,**kwargs:object())
    monkeypatch.setattr(main,'AuthorityBoundSafetyPreflight',lambda *args:lambda:calls.append(('safety',{})))
    monkeypatch.setattr(main,'JobWorker',lambda *args,**kwargs:calls.append(('worker',kwargs)) or kwargs)
    revoked=[False]
    def runtime_guard():
        from services.job_worker.errors import SafetyBlockedError
        if revoked[0]:raise SafetyBlockedError('SAFETY_AUTHORITY_CHANGED','LOCAL runtime revoked')
    supplied={'P3_ISSUER_KIND':'LOCAL' if local else 'GITHUB','TRADING_PACKAGE6_APPROVAL_SHA256':'f'*64}
    # Both lanes refuse official composition without pinned runtime approval.
    with pytest.raises(ValueError,match='pinned runtime approval'):
        main.build_worker(Repository(),supplied,authority=authority,p3_spawn_provider=provider,p3_job_id=job_id)
    assert calls==[]
    result=main.build_worker(Repository(),supplied,
        authority=authority,p3_spawn_provider=provider,p3_job_id=job_id,
        p3_runtime_guard=runtime_guard)
    assert [name for name,_ in calls]==['safety','recover','worker']
    assert calls[1][1]==dict(recovery_id='worker-startup-recovery',alpha_campaign=True,fixture_only=False,
        **({'job_id':job_id} if job_id is not None else {}))
    assert result['p3_job_id']==job_id
    assert result['p3_profile'] is True
    assert result['p3_publisher'] is publisher
    assert result['prepare_spawn']==provider.prepare
    assert result['p3_fixture_executor'] is None
    assert result['engine_spawn_provider'] is None
    with pytest.raises(ValueError,match='P3 safety refresh'):
        main.build_worker(Repository(),{'P3_ISSUER_KIND':'LOCAL' if local else 'GITHUB'},
            authority=authority,p3_spawn_provider=provider,p3_job_id=job_id,
            p3_safety_preflight=lambda:None)
    if True:
        assert supplied['TRADING_PACKAGE6_APPROVAL_SHA256']=='f'*64
        with pytest.raises(ValueError,match='legacy research fixture'):
            main.build_worker(Repository(),{**supplied,'TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH':'/tmp/legacy'},
                authority=authority,p3_spawn_provider=provider,p3_job_id=job_id,p3_runtime_guard=runtime_guard)
        from services.job_worker.errors import SafetyBlockedError
        revoked[0]=True
        with pytest.raises(SafetyBlockedError):result['safety_preflight']()
        assert [name for name,_ in calls]==['safety','recover','worker']


@pytest.mark.parametrize('case',['accepted','wrong-authority','mixed-guard','github'])
def test_local_safety_refresh_composition_is_bound_before_recovery(synthetic_provider,monkeypatch,tmp_path,case):
    from services.job_worker import main,p3_local_runtime
    provider,job,_=synthetic_provider
    events=[]
    class Repository:
        def recover_expired_leases(self,*args,**kwargs):events.append('recover')
        def alpha_publication_repository(self,store):return object()
    authority=SimpleNamespace(runtime_authority=object(),runtime_paths=SimpleNamespace(
        artifact_root=tmp_path,reports_root=tmp_path,signals_root=tmp_path),
        safety_snapshot_path=tmp_path/'safety',safety_exporter_commit='a'*40,
        safety_source_fingerprint='b'*64,application_revision=job.payload.expected_source.commit_sha)
    monkeypatch.setattr(main.ResearchEnvironmentSettings,'from_authority',lambda *args:object())
    monkeypatch.setattr(main,'SafetyStateClient',lambda *args,**kwargs:object())
    monkeypatch.setattr(main,'AuthorityBoundSafetyPreflight',lambda *args:lambda:None)
    monkeypatch.setattr(main,'JobWorker',lambda *args,**kwargs:events.append('worker') or kwargs)
    monkeypatch.setattr(p3_local_runtime,'require_local_runtime',lambda *args:'a'*64)
    # This test exercises routing only; the preflight's validators have separate tests.
    monkeypatch.setattr(p3_local_runtime.LocalRuntimeSafetyPreflight,'__call__',lambda self:events.append('safety'))
    preflight=p3_local_runtime.LocalRuntimeSafetyPreflight(
        object() if case=='wrong-authority' else authority,None,None,None,refresh_dynamic_evidence=lambda:None)
    def build():
        return main.build_worker(Repository(),{'P3_ISSUER_KIND':'GITHUB' if case=='github' else 'LOCAL'},
            authority=authority,p3_spawn_provider=provider,p3_job_id=job.job_id,
            p3_runtime_guard=(lambda:None) if case=='mixed-guard' else None,p3_safety_preflight=preflight)
    if case in {'accepted','github'}:
        # The GITHUB lane composes the same staging-scope refresh as LOCAL.
        assert build()['safety_preflight'] is preflight
        assert events==['safety','recover','worker']
    else:
        with pytest.raises(ValueError,match='P3 safety refresh'):build()
        assert events==[]


@pytest.mark.parametrize('fault',['forged','fixture','engine'])
def test_official_composition_rejects_mixed_authority_before_attestation(synthetic_provider,monkeypatch,fault):
    from services.job_worker import main
    from services.job_worker.p3_integration import P3IntegrationFixtureExecutor
    provider,_,_=synthetic_provider
    monkeypatch.setattr(main,'attest_worker_runtime_authority',lambda:pytest.fail('mixed authority reached attestation'))
    kwargs=dict(p3_spawn_provider=object() if fault=='forged' else provider,p3_job_id='job_'+'a'*32)
    if fault=='fixture':
        kwargs['p3_fixture_executor']=object.__new__(P3IntegrationFixtureExecutor)
    elif fault=='engine':
        kwargs.update(engine_spawn_provider=object(),engine_event_ingestor=object())
    with pytest.raises((ValueError,TypeError),match='P3'):
        main.build_worker(object(),{},**kwargs)


def test_official_composition_requires_bound_job_before_attestation(synthetic_provider,monkeypatch):
    from services.job_worker import main
    provider,_,_=synthetic_provider
    monkeypatch.setattr(main,'attest_worker_runtime_authority',lambda:pytest.fail('unbound composition reached authority'))
    with pytest.raises(ValueError,match='job'):
        main.build_worker(object(),{},p3_spawn_provider=provider)
