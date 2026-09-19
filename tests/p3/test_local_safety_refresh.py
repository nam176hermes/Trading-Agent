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
