"""Unit seams for refresh ordering; protected host custody is qualified separately."""
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.mark.parametrize('fault',[None,'revoked','aged'])
def test_local_refresh_checks_static_before_fresh_safety_and_rechecks_approval(monkeypatch,fault):
    from services.job_worker import p3_local_runtime as module
    from services.job_worker import command_registry,safety_state
    from packages.runtime_release import config
    from services.job_worker.errors import SafetyBlockedError
    now=[datetime.now(UTC)];digest=['a'*64];calls=[];snapshot=[]
    authority=SimpleNamespace(runtime_authority=SimpleNamespace(safety=config.SafetyAuthority('a'*40,Path('/tmp/safety'),'b'*64,1000,False)),
        safety_snapshot_path='/tmp/safety',safety_exporter_commit='a'*40,safety_source_fingerprint='b'*64)
    monkeypatch.setattr(module,'require_local_runtime',lambda *args:digest[0])
    def produce():
        calls.append('produce')
        snapshot[:]=[safety_state.SafetyEvidence(requested_mode=safety_state.SafetyMode.PAPER,effective_mode=safety_state.SafetyMode.PAPER,
            live_execution_enabled=False,live_trading_approved=False,kill_switch_state=safety_state.KillSwitchState.INACTIVE,
            snapshot_sha256='c'*64,generated_at=now[0],expires_at=now[0]+timedelta(seconds=6))]
    def refresh(current,*,refresh_dynamic_evidence):
        assert current is authority
        calls.append('full_static');now[0]+=timedelta(seconds=8)
        refresh_dynamic_evidence()
        return current
    def dynamic(current):
        assert current is authority.runtime_authority
        calls.append('dynamic')
        return current
    monkeypatch.setattr(command_registry,'refresh_staging_worker_runtime_authority',refresh)
    monkeypatch.setattr(config,'refresh_runtime_authority_v2',dynamic)
    monkeypatch.setattr(safety_state,'SafetyStateClient',lambda *args,**kwargs:object())
    def preflight(current,client,*,authority_loader):
        def read():
            authority_loader();authority_loader()
            if fault=='revoked':digest[0]='d'*64
            if fault=='aged':now[0]+=timedelta(seconds=7)
            return snapshot[0]
        return read
    monkeypatch.setattr(safety_state,'AuthorityBoundSafetyPreflight',preflight)
    class Clock:
        @staticmethod
        def now(tz):return now[0]
    monkeypatch.setattr(module,'datetime',Clock)
    check=module.LocalRuntimeSafetyPreflight(authority,None,None,None,refresh_dynamic_evidence=produce)
    if fault:
        with pytest.raises(SafetyBlockedError):check()
    else:
        assert check().expires_at-now[0]==timedelta(seconds=6)
    assert calls==['full_static','produce','dynamic','dynamic']


@pytest.mark.parametrize('fault', [None, 'scope', 'interpreter', 'source_root', 'host'])
def test_official_safety_producer_uses_attested_root_and_existing_exporter(tmp_path, monkeypatch, fault):
    import sys
    from services.job_worker import p3_local_runtime as module
    from services.safety_state_exporter import exporter
    from scripts import build_p1_package6_host_authority as producer
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    from packages.engine_contracts.serialization import canonical_json_bytes
    identity=LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(
        issuer_kind='LOCAL',issuer_identity='test',run_id='00000000-0000-4000-8000-000000000001',
        attempt=1,host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000002')))
    monkeypatch.setattr(module,'OFFICIAL_DATA_ROOT',tmp_path)
    source_root=tmp_path/str(identity.run_id)/'safety'
    source_root.mkdir(parents=True,mode=0o700)
    for name,content in (('.mode','PAPER'),('.kill_switch','INACTIVE')):
        file=source_root/name
        file.write_text(content)
        file.chmod(0o600)
    snapshot_root=tmp_path/'runtime';snapshot_root.mkdir(mode=0o700)
    snapshot=snapshot_root/'safety-state.json'
    runtime=SimpleNamespace(scope='PRODUCTION' if fault=='scope' else 'PACKAGE6_STAGING_ONLY',
        application_python=Path('/wrong/python') if fault=='interpreter' else Path(sys.executable),
        source_commit='b'*40,runtime_paths=SimpleNamespace(safety_snapshot=snapshot),
        safety=SimpleNamespace(source_fingerprint='0'*64 if fault=='source_root' else exporter.source_fingerprint(source_root)))
    monkeypatch.setattr(module,'_host_identity',lambda: ('0'*64 if fault=='host' else identity.host_id,identity.boot_id))
    activations=[]
    monkeypatch.setattr(producer,'_replace_activation',lambda actual,digest: activations.append((actual,digest)))
    if fault:
        with pytest.raises(ValueError):module.refresh_local_safety_evidence(SimpleNamespace(runtime_authority=runtime),identity)
        assert not snapshot.exists() and not activations
    else:
        import hashlib,json
        module.refresh_local_safety_evidence(SimpleNamespace(runtime_authority=runtime),identity)
        raw=snapshot.read_bytes();document=json.loads(raw)
        assert activations==[(runtime,hashlib.sha256(raw).hexdigest())]
        generated=datetime.fromisoformat(document['generated_at'].replace('Z','+00:00'))
        expires=datetime.fromisoformat(document['expires_at'].replace('Z','+00:00'))
        assert expires-generated==timedelta(seconds=6)
        assert document['effective_mode']=='PAPER'
        assert not document['live_execution_enabled'] and not document['live_trading_approved']
