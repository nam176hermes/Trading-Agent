"""Post-exec observation never manufactures a protected session or touches SQL."""
from datetime import UTC, datetime, timedelta
import json
import os
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_worker.recovery import ProcProcessInspector
from tests.jobs.test_worker_lifecycle import claim


@pytest.mark.parametrize('fault', [None, 'owner', 'deadline', 'expired', 'guard'])
@pytest.mark.parametrize('minutes', [37, 55])
def test_owner_wait_binds_actual_process_and_keeps_original_deadline(tmp_path, monkeypatch, fault, minutes):
    from services.job_worker import p3_session as module
    from packages.runtime_release.config import ProtectedAuthorityError
    from packages.alpha_lifecycle.local_qualification import _host_identity
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    host, boot = _host_identity()
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='LOCAL',
        issuer_identity='test-owner', run_id='00000000-0000-4000-8000-000000000002',
        attempt=1, host_id=host, boot_id=str(boot))))
    source = SourceIdentity(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
        closure_policy_sha256='c'*64, closure_sha256='d'*64)
    job = claim()
    ref = dict(content_sha256='e'*64, locator='e'*64+'.blob', size_bytes=2, media_type='application/json')
    profile = SimpleNamespace(identity=identity, job_id=job.job_id, output_root=str(tmp_path),
        source=source, environment_ref=ref)
    closure = SimpleNamespace(source=source, closure_sha256='d'*64)
    expiry = datetime.now(UTC) + timedelta(minutes=minutes+3)
    issued = datetime.now(UTC)-timedelta(minutes=3)
    deadline = issued+timedelta(minutes=minutes)
    process = ProcProcessInspector().inspect(os.getpid())
    launch = dict(schema_version='p3-local-session-launch-v1', worker_pid=os.getpid(),
        worker_start_ticks=process.start_ticks, worker_uid=os.geteuid(), controller_pid=os.getppid(),
        identity=identity, source=source, job_id=job.job_id, host_profile_sha256='a'*64,
        issued_at=issued.isoformat().replace('+00:00','Z'),
        workflow_deadline=deadline.isoformat().replace('+00:00','Z'))
    # Exercise the producer at the exact operation expiry, including clock-read latency.
    from scripts import p3_local_transport as transport
    from packages.runtime_release import config
    observed = 1000.0
    monotonic_end = observed+(deadline-datetime.now(UTC)).total_seconds()+0.25
    produced = []
    monkeypatch.setattr(transport, '_official_run_directory', lambda _:tmp_path)
    monkeypatch.setattr(transport, 'time', SimpleNamespace(monotonic=lambda:observed))
    monkeypatch.setattr(transport, 'os', SimpleNamespace(getpid=lambda:os.getppid()))
    monkeypatch.setattr(transport, '_process_start_time', lambda _:process.start_ticks)
    monkeypatch.setattr(config, 'read_protected_canonical_json_current', lambda _:(
        dict(payload=dict(expected_source=source), job_id=job.job_id), 'a'*64))
    monkeypatch.setattr(transport, '_durable_write', lambda _, raw, **__:produced.append(json.loads(raw)))
    transport._publish_session_launch(dict(identity=identity), dict(worker_uid=os.geteuid()),
        os.getpid(), monotonic_end, monotonic_end+1, issued, deadline)
    assert produced == [json.loads(canonical_json_bytes(launch))]
    launch = produced[0]
    expiry = deadline
    reads = []
    original_link = os.link
    def link(source, destination, **kwargs):
        original_link(source, destination, **kwargs)
        assert os.stat(destination).st_nlink == 1, 'published observation must have one link'
    monkeypatch.setattr(module.os, 'link', link)
    def read(path):
        if path.name == 'session-launch.json': return json.loads(canonical_json_bytes(launch)), 'b'*64
        assert (tmp_path/job.job_id/'session-owner'/'observation.log').stat().st_nlink == 1
        observation = json.loads((tmp_path/job.job_id/'session-owner'/'observation.log').read_bytes())
        reads.append(observation)
        if len(reads) == 1 and fault != 'expired':
            raise ProtectedAuthorityError()
        value = dict(schema_version='p3-local-holdout-session-v1', source=source,
            environment_ref=ref, primary_selection_ref=ref, custody_record_ref=ref,
            holdout_commitment='f'*64, closure_sha256='d'*64,
            parent=observation['parent'], identity=identity, boot_id=str(boot),
            issued_at=observation['issued_at'], expires_at=observation['workflow_deadline'],
            workflow_deadline=observation['workflow_deadline'])
        # Session expiry is strictly earlier than enclosing deadline by model contract.
        end = datetime.fromisoformat(value['workflow_deadline'].replace('Z','+00:00'))
        value['expires_at'] = (end-timedelta(seconds=1)).isoformat().replace('+00:00','Z')
        if fault == 'owner': value['parent'] = {**value['parent'], 'pid':os.getpid()+1}
        if fault == 'deadline': value['issued_at'] = (end-timedelta(minutes=36)).isoformat().replace('+00:00','Z')
        return value, 'a'*64
    monkeypatch.setattr(module, 'read_protected_canonical_json_current', read)
    monkeypatch.setattr(module.time, 'sleep', lambda _:None)
    def guard():
        if fault == 'guard': raise ValueError('runtime withdrawn')
    if fault:
        with pytest.raises(ValueError):
            module.await_local_session_owner(profile, closure, host_profile_digest='a'*64,
                expires_at=datetime.now(UTC)-timedelta(seconds=1) if fault=='expired' else expiry,
                guard=guard)
    else:
        observation = module.await_local_session_owner(profile, closure, host_profile_digest='a'*64,
            expires_at=expiry, guard=guard)
        assert observation.parent == ProcProcessInspector().inspect(os.getpid())
        assert len(reads) == 2 and reads[0] == reads[1]
        assert observation.issued_at == issued
        assert observation.workflow_deadline == deadline
        with pytest.raises((OSError, ValueError)):
            module.await_local_session_owner(profile, closure, host_profile_digest='a'*64,
                expires_at=expiry, guard=guard)
