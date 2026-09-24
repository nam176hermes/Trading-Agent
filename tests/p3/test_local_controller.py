"""Controller checks use synthetic documents and never install host authority."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.alpha_lifecycle.contracts.authority import ReviewApproval
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_local_research_admission import sealed


def test_research_review_requires_exact_subjects_and_full_validity():
    from scripts.p3_local_controller import validate_research_review
    now = datetime.now(UTC).replace(microsecond=0)
    source = dict(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
                  closure_policy_sha256='c'*64, closure_sha256='d'*64)
    ref = dict(content_sha256='e'*64, locator='e'*64+'.blob', size_bytes=2, media_type='application/json')
    value = dict(schema_version='p3-review-approval-v1', source=source,
        subject_digests=['1'*64,'2'*64], operator_identity='operator', reviewer_identity='agent-reviewer',
        review_execution_id='independent-1', verdict='APPROVED',
        issued_at=(now-timedelta(minutes=1)).isoformat().replace('+00:00','Z'),
        expires_at=(now+timedelta(minutes=40)).isoformat().replace('+00:00','Z'),
        evidence_ref=ref, authority=dict(broker=False, live=False, network=False, production=False))
    def check(change):
        review = ReviewApproval.model_validate_json(canonical_json_bytes(sealed({**value, **change})))
        validate_research_review(review, source=review.source, subject_digests=('1'*64,'2'*64),
            now=now, expires_at=now+timedelta(minutes=30))
    check({})
    for change in ({'reviewer_identity':'operator'}, {'verdict':'REJECTED'},
                   {'subject_digests':['1'*64]}, {'subject_digests':['1'*64,'2'*64,'3'*64]},
                   {'expires_at':(now+timedelta(minutes=2)).isoformat().replace('+00:00','Z')}):
        with pytest.raises(ValueError): check(change)


def test_protected_publication_refuses_untrusted_parent_and_symlink(tmp_path):
    from scripts.p3_local_controller import publish_protected
    target = tmp_path/'authority.json'
    with pytest.raises((ValueError, PermissionError)):
        publish_protected(target, b'{}')
    assert not target.exists()
    link = tmp_path/'link'
    link.symlink_to('/etc')
    with pytest.raises((OSError, ValueError, PermissionError)):
        publish_protected(link/'must-not-exist.json', b'{}')
    assert not Path('/etc/must-not-exist.json').exists()


def test_stage_history_rejects_reordered_duplicate_or_different_session():
    from scripts.p3_local_controller import next_stage
    from services.job_worker.p3_session import SessionAttempt
    def attempt(operation, label):
        return SessionAttempt(operation=operation, job_id='job_'+label,
            attempt_id='attempt_'+label, worker_id='worker', lease_token_sha256='f'*64)
    first = next_stage(None, attempt('HOLDOUT', 'one'), session_digest='a'*64, host_digest='b'*64)
    second = next_stage(first, attempt('PARITY', 'two'), session_digest='a'*64, host_digest='c'*64)
    assert len(second.attempts) == 2 and second.attempts[0] == first.attempts[0]
    for current, session_digest in ((attempt('PHASE_EXIT','two'),'a'*64),
                                    (attempt('PARITY','one'),'a'*64),
                                    (attempt('PARITY','two'),'d'*64)):
        with pytest.raises(ValueError):
            next_stage(first, current, session_digest=session_digest, host_digest='c'*64)


@pytest.mark.parametrize('fault',[None,'database','uid','extra'])
def test_stage_observer_keeps_worker_credentials_private_and_original_deadline(monkeypatch,tmp_path,fault):
    import time
    from scripts import p3_local_controller as module, p3_local_host as worker, p3_local_transport as transport
    from services.job_store import config
    from services.job_worker.p3_host_profile import LocalOfficialHostProfileV2
    from tests.p3.test_host_profile import _host_profile_document, _image_descriptor
    from tests.p3.test_local_transport_fences import _identity
    host = LocalOfficialHostProfileV2.model_validate_json(canonical_json_bytes(
        _host_profile_document('p3-local-official-host-profile-v2',identity=_identity(),image=_image_descriptor())))
    now = datetime.now(UTC)
    runtime = dict(uid=host.image.worker_uid+(1 if fault=='uid' else 0),
        identity=host.identity.model_dump(mode='json'),source=host.payload.expected_source.model_dump(mode='json'),
        application_root='/qualified/application',application_python='/qualified/python',
        expires_at=(now+timedelta(seconds=20)).isoformat().replace('+00:00','Z'))
    monkeypatch.setattr(transport,'_runtime_authority',lambda _:runtime)
    values = {'database-host':'127.0.0.1','database-port':'55435','database-name':'synthetic'}
    reads = []
    def credential(env,key):
        assert env == {'CREDENTIALS_DIRECTORY':str(tmp_path/'root-observer')}
        reads.append(key)
        return 'private-observer-password' if key=='database-password' else values[key]
    monkeypatch.setattr(config,'read_systemd_credential',credential)
    def observed(uid,gid,deadline,environment,command):
        assert (uid,gid) == (host.image.worker_uid,host.image.worker_gid)
        assert 0 < deadline-time.monotonic() <= 20
        assert environment['P3_APPLICATION_ROOT'] == runtime['application_root']
        assert environment['P3_APPLICATION_PYTHON'] == runtime['application_python']
        assert command == dict(action='database-identity',credentials_directory=host.worker_credentials_directory)
        result = values.copy()
        if fault=='database':result['database-name']='other'
        if fault=='extra':result['database-password']='must-be-rejected'
        return result
    monkeypatch.setattr(worker,'as_worker',observed)
    if fault:
        with pytest.raises(ValueError):
            module.owner_database_settings(tmp_path/'root-observer',host,now+timedelta(seconds=90))
        assert 'database-password' not in reads
        if fault=='uid':assert not reads
    else:
        settings, observed_runtime, deadline = module.owner_database_settings(tmp_path/'root-observer',host,now+timedelta(seconds=90))
        assert (settings.host,settings.port,settings.database,settings.user) == ('127.0.0.1',55435,'synthetic','trading_owner')
        assert reads == [*values,'database-password']
        assert observed_runtime == runtime and 0 < deadline-time.monotonic() <= 20


def test_operation_issuer_rejects_auth_intent_mismatch_before_any_authority_io(monkeypatch, tmp_path):
    from scripts import p3_local_controller as module
    from packages.alpha_lifecycle.authority import _LocalRequestFile, AuthorityHeld
    from tests.p3.test_authority import _request
    from tests.p3.test_operation_input import intent
    import json
    now = datetime.now(UTC)
    request, source = _request((now+timedelta(minutes=5)).isoformat().replace('+00:00','Z'))
    auth = request['authorization']
    for key in ('issuer_workflow', 'issuer_run_id', 'issuer_attempt'):
        auth.pop(key)
    auth.update(schema_version='p3-local-run-authorization-v1', operation='OOS',
        allowed_alpha_ids=['a0.donchian-20-10-close-confirm'],
        issued_at=(now-timedelta(seconds=1)).isoformat().replace('+00:00','Z'),
        identity=dict(issuer_kind='LOCAL', issuer_identity='operator-a',
            run_id='00000000-0000-4000-8000-000000000002', attempt=1,
            host_id='a'*64, boot_id='00000000-0000-4000-8000-000000000001'))
    request.update(schema_version='p3-local-authority-request-v1', authorization=sealed(auth),
        operation_input=json.loads(intent(input_set_ref=auth['input_set_ref'])))
    request = _LocalRequestFile.model_validate_json(canonical_json_bytes(request))
    monkeypatch.setattr(module.os, 'geteuid', lambda:0)
    monkeypatch.setattr(module, '_protected_model', lambda *_:request)
    monkeypatch.setattr(module, '_committed_source', lambda:source)
    def unexpected(*_):
        pytest.fail('mismatched operation reached authority/proof access')
    monkeypatch.setattr(module, 'ProtectedQualificationStore', unexpected)
    monkeypatch.setattr(module, 'publish_protected', unexpected)
    with pytest.raises(AuthorityHeld, match='operation differs'):
        module.install_operation(tmp_path/'request', tmp_path/'review', tmp_path/'proof')


@pytest.mark.parametrize('fault',[None,'runtime_expired','runtime_replaced'])
def test_stage_publication_rechecks_runtime_after_readonly_sql(tmp_path,monkeypatch,fault):
    """Issuer inputs and SQL are substituted; no root or database authority is granted."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    import time
    import psycopg
    from scripts import p3_local_controller as module, p3_local_transport as transport
    from services.job_worker.p3_session import LocalSessionProfile
    from services.job_worker.recovery import ProcProcessInspector
    identity = SimpleNamespace(issuer_identity='synthetic',model_dump=lambda **_:dict(issuer_kind='LOCAL'))
    parent = SimpleNamespace(pid=123)
    now = datetime.now(UTC)
    session = SimpleNamespace(identity=identity,source='source',parent=parent,
        issued_at=now-timedelta(seconds=1),expires_at=now+timedelta(seconds=60))
    monkeypatch.setattr(LocalSessionProfile,'model_validate_json',lambda *_:session)
    request = SimpleNamespace(authorization=SimpleNamespace(identity=identity,operation='HOLDOUT'),execution_source='source')
    host = SimpleNamespace(identity=identity,job_id='job_claimed',payload={})
    context = (request,tmp_path,host,'b'*64)
    monkeypatch.setattr(module,'_session_context',lambda _:context)
    monkeypatch.setattr(module,'_protected_model',lambda *_:session)
    monkeypatch.setattr(module,'read_protected_canonical_json_current',lambda _:({},'a'*64))
    monkeypatch.setattr(ProcProcessInspector,'inspect',lambda *_:parent)
    runtime = {'generation':'admitted'}
    deadline = -1 if fault=='runtime_expired' else time.monotonic()+30
    settings = SimpleNamespace(conninfo=lambda:'synthetic-not-a-connection')
    monkeypatch.setattr(module,'owner_database_settings',lambda *_:(settings,runtime,deadline))
    queries = []
    class Connection:
        def execute(self,sql,parameters=None):
            queries.append(sql)
            if 'session_user' in sql:return SimpleNamespace(fetchone=lambda:('trading_owner','trading_owner'))
            assert parameters == ('job_claimed','{}')
            return SimpleNamespace(fetchone=lambda:('attempt_claimed','worker','c'*64))
    def connect(_conninfo,**kwargs):
        assert kwargs['options']=='-c default_transaction_read_only=on -c statement_timeout=5000'
        return nullcontext(Connection())
    monkeypatch.setattr(psycopg,'connect',connect)
    monkeypatch.setattr(module,'AuthorityStore',lambda _:SimpleNamespace(locked=lambda:nullcontext()))
    def current(_):
        assert len(queries)==2
        return {'generation':'replaced'} if fault=='runtime_replaced' else runtime
    monkeypatch.setattr(transport,'_runtime_authority',current)
    published = []
    monkeypatch.setattr(module,'publish_protected',lambda *a,**kw:published.append(a))
    if fault:
        with pytest.raises(ValueError,match='stage authority changed'):
            module.install_stage(tmp_path/'request',tmp_path/'root-observer')
        assert not published
    else:
        result = module.install_stage(tmp_path/'request',tmp_path/'root-observer')
        assert len(published)==1 and result.attempts[0].job_id=='job_claimed'


@pytest.mark.parametrize('wrong_owner', [False, True])
def test_session_issuer_observes_actual_process_before_publication(tmp_path, monkeypatch, wrong_owner):
    """Real /proc observations; protected input/issuer IO is explicitly substituted."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    import os
    from scripts import p3_local_controller as module
    from services.job_worker.p3_session import LocalSessionLaunch
    from services.job_worker.recovery import ProcProcessInspector
    from packages.alpha_lifecycle.operation_input import HoldoutInput
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.alpha_lifecycle.local_qualification import _host_identity
    from tests.p3.test_replay import _ref
    host_id, boot_id = _host_identity()
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='LOCAL',
        issuer_identity='test-owner', run_id='00000000-0000-4000-8000-000000000002', attempt=1,
        host_id=host_id, boot_id=str(boot_id))))
    source = SourceIdentity(commit_sha='a'*40, tree_sha='b'*40, closure_schema_version='v1',
        closure_policy_sha256='c'*64, closure_sha256='d'*64)
    now = datetime.now(UTC)
    process = ProcProcessInspector().inspect(os.getpid())
    parent = dict(pid=process.pid, process_group=process.process_group,
        start_ticks=process.start_ticks, command_fingerprint=process.command_fingerprint)
    deadline = now+timedelta(minutes=30)
    launch = LocalSessionLaunch.model_validate_json(canonical_json_bytes(dict(
        schema_version='p3-local-session-launch-v1', identity=identity, source=source, job_id='job_owner',
        host_profile_sha256='a'*64, worker_pid=os.getpid(), worker_start_ticks=process.start_ticks,
        worker_uid=os.geteuid(), controller_pid=os.getppid(),
        issued_at=now.isoformat().replace('+00:00','Z'), workflow_deadline=deadline.isoformat().replace('+00:00','Z'))))
    observation = dict(schema_version='p3-local-session-owner-observation-v1',
        identity=identity, source=source, job_id='job_owner', host_profile_sha256='a'*64,
        parent={**parent, 'pid':os.getpid()+1} if wrong_owner else parent,
        issued_at=now.isoformat().replace('+00:00','Z'), workflow_deadline=deadline.isoformat().replace('+00:00','Z'))
    path = tmp_path/'job_owner/session-owner/observation.log'
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json_bytes(observation)); path.chmod(0o600)
    body = HoldoutInput.model_validate_json(canonical_json_bytes({key:
        'f'*64 if key=='policy_digest' else _ref('e'*64) for key in HoldoutInput.model_fields}))
    host = SimpleNamespace(job_id='job_owner', output_root=str(tmp_path),
        environment_ref=body.environment_ref, closure_sha256=source.closure_sha256)
    request = SimpleNamespace(operation_input=SimpleNamespace(body=body), execution_source=source,
        authorization=SimpleNamespace(identity=identity, expires_at=deadline))
    monkeypatch.setattr(module, '_session_context', lambda _:(request, tmp_path, host, 'a'*64))
    monkeypatch.setattr(module, '_protected_model', lambda *_:launch)
    monkeypatch.setattr(module, 'ProtectedQualificationStore', lambda _:object())
    monkeypatch.setattr(module, '_read', lambda *_:SimpleNamespace(holdout_commitment='f'*64))
    monkeypatch.setattr(module, 'AuthorityStore', lambda _:SimpleNamespace(locked=nullcontext))
    published = []
    monkeypatch.setattr(module, 'publish_protected', lambda path, raw:published.append((path,raw)))
    if wrong_owner:
        with pytest.raises(ValueError, match='current transport child'):
            module.install_session(tmp_path/'request', tmp_path/'proof')
        assert published == []
    else:
        session = module.install_session(tmp_path/'request', tmp_path/'proof')
        assert session.parent == process and session.workflow_deadline == deadline
        assert session.issued_at == now and len(published) == 1
