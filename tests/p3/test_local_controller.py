"""Controller checks use synthetic documents and never install host authority."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.alpha_lifecycle.contracts.authority import ReviewApproval
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_local_research_admission import sealed
from tests.p3.test_local_host_producer import native_profile_inputs  # noqa: F401


@pytest.fixture
def parity_stage_owner(tmp_path, monkeypatch):
    """Real stage ordering; SQL, protected IO and issuer authority are synthetic."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    import time
    import psycopg
    from scripts import p3_local_controller as module, p3_local_transport as transport
    from services.job_worker.p3_session import LocalSessionProfile, SessionAttempt
    from services.job_worker.recovery import ProcProcessInspector
    from services.job_worker.p3_host_profile import LocalOfficialHostProfileV2
    from tests.p3.test_host_profile import _host_profile_document, _image_descriptor
    from tests.p3.test_local_transport_fences import _identity
    document = _host_profile_document('p3-local-official-host-profile-v2',
        identity=_identity(), image=_image_descriptor())
    document['payload']['operation'] = 'PARITY'
    host = LocalOfficialHostProfileV2.model_validate_json(canonical_json_bytes(document))
    parent = SimpleNamespace(pid=123)
    now = datetime.now(UTC)
    session = SimpleNamespace(identity=host.identity, source=host.payload.expected_source, parent=parent,
        environment_ref=host.environment_ref, issued_at=now-timedelta(seconds=1),
        expires_at=now+timedelta(seconds=60))
    request = SimpleNamespace(authorization=SimpleNamespace(identity=host.identity, operation='PARITY'),
        execution_source=session.source, payload=host.payload)
    prior = SessionAttempt(operation='HOLDOUT', job_id='job_'+'0'*32,
        attempt_id='attempt_'+'0'*32, worker_id='worker', lease_token_sha256='c'*64)
    previous = module.next_stage(None, prior, session_digest='a'*64, host_digest='d'*64)
    (tmp_path/'stage.json').touch()
    context = (request, tmp_path, host, 'b'*64, host.identity)
    monkeypatch.setattr(module, '_session_context', lambda _: context)
    monkeypatch.setattr(LocalSessionProfile, 'model_validate_json', lambda *_: session)
    monkeypatch.setattr(module, '_protected_model', lambda *_: previous)
    monkeypatch.setattr(module, 'read_protected_canonical_json_current', lambda _: ({}, 'a'*64))
    monkeypatch.setattr(ProcProcessInspector, 'inspect', lambda *_: parent)
    runtime = {'generation': 'admitted'}
    deadline = time.monotonic()+30
    monkeypatch.setattr(module, 'owner_database_settings', lambda *_:
        (SimpleNamespace(conninfo=lambda: 'synthetic-not-a-connection'), runtime, deadline))
    current_runtime = [runtime]
    monkeypatch.setattr(transport, '_runtime_authority', lambda _: current_runtime[0])
    queries = []
    class Connection:
        def execute(self, sql, parameters=None):
            queries.append(sql)
            if 'session_user' in sql:
                return SimpleNamespace(fetchone=lambda: ('trading_owner', 'trading_owner'))
            assert parameters == (host.job_id, canonical_json_bytes(request.payload).decode())
            return SimpleNamespace(fetchone=lambda: ('attempt_'+'1'*32, 'worker', 'c'*64))
    def connect(_conninfo, **kwargs):
        assert kwargs['options'] == '-c default_transaction_read_only=on -c statement_timeout=5000'
        return nullcontext(Connection())
    monkeypatch.setattr(psycopg, 'connect', connect)
    monkeypatch.setattr('scripts.p3_lane.issuer_lock', lambda _: nullcontext())
    published = []
    monkeypatch.setattr(module, 'publish_protected', lambda *args, **kwargs: published.append(args))
    return SimpleNamespace(module=module, request_path=tmp_path/'request', credentials=tmp_path/'observer',
        host=host, session=session, previous=previous, published=published, queries=queries, runtime=runtime,
        request=request, directory=tmp_path, current_runtime=current_runtime)


def test_parity_stage_requires_native_readiness_before_publication(parity_stage_owner):
    x = parity_stage_owner
    with pytest.raises(ValueError, match='native'):
        x.module.install_stage(x.request_path, x.credentials)
    assert not x.published


@pytest.fixture
def qualified_parity_owner(parity_stage_owner, native_profile_inputs, monkeypatch):
    import hashlib
    from services.job_worker.nautilus_closure import NautilusClosureConfig
    from services.job_worker.p3_native_spawn import NativeLaunchProfile
    from scripts import p3_local_host as worker
    x, native = parity_stage_owner, native_profile_inputs
    x.host = x.host.model_copy(update=dict(payload=native.payload, store_root=str(native.store_root),
        output_root=str(native.output)))
    x.request.operation_input = native.intent
    x.request.payload = x.host.payload
    x.runtime.update(uid=x.host.image.worker_uid, identity=x.host.identity.model_dump(mode='json'),
        source=x.session.source.model_dump(mode='json'),
        application_root='/qualified/app', application_python='/qualified/python')
    monkeypatch.setattr(x.module, '_session_context', lambda _: (x.request, x.directory, x.host, 'b'*64, x.host.identity))
    x.files = {}
    def read(path):
        if path.name == 'session.json':
            return {}, 'a'*64
        raw = x.files[path.name]
        return __import__('json').loads(raw), hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(x.module, 'read_protected_canonical_json_current', read)
    def publish(path, raw, **kwargs):
        x.published.append((path, raw, kwargs))
        x.files[path.name] = raw
    monkeypatch.setattr(x.module, 'publish_protected', publish)
    def model(path, cls):
        return NativeLaunchProfile.model_validate_json(x.files[path.name]) if path.name == 'native.json' else x.previous
    monkeypatch.setattr(x.module, '_protected_model', model)
    x.helper_calls = []
    def as_worker(uid, gid, deadline, environment, command):
        assert (uid, gid) == (x.host.image.worker_uid, x.host.image.worker_gid)
        assert command['previous_job_id'] == x.previous.attempts[0].job_id
        x.helper_calls.append(command)
        return worker.native_profile_action(command)
    monkeypatch.setattr(worker, 'as_worker', as_worker)
    x.config = NautilusClosureConfig(Path('/qualified/p1/runtime'), Path('/qualified/p1/artifacts'),
                                    Path(x.host.sandbox_executable))
    x.native = native
    return x


def test_parity_owner_publishes_exact_native_profile_before_stage(qualified_parity_owner):
    import json
    x = qualified_parity_owner
    stage = x.module.install_stage(x.request_path, x.credentials, native_closure=x.config)
    assert [p.name for p, _, _ in x.published] == ['native.json', 'stage.json']
    native = json.loads(x.files['native.json'])
    assert (native['job_id'], native['attempt_id']) == (stage.attempts[-1].job_id, stage.attempts[-1].attempt_id)
    assert native['session_sha256'] == stage.session_sha256
    assert native['native_commitment_ref'] == x.native.ref.model_dump(mode='json')
    assert len(x.helper_calls) == 2
    assert all('SELECT' in query for query in x.queries)


@pytest.mark.parametrize('native_inputs', [False, True])
@pytest.mark.parametrize('operation', ['HOLDOUT', 'PHASE_EXIT'])
def test_other_stage_owners_require_no_native_inputs(qualified_parity_owner, monkeypatch, operation, native_inputs):
    from services.job_worker.p3_session import SessionAttempt
    x = qualified_parity_owner
    x.request.authorization.operation = operation
    payload = type(x.host.payload).model_validate_json(canonical_json_bytes(dict(
        x.host.payload.model_dump(mode='json'), operation=operation)))
    x.host = x.host.model_copy(update=dict(payload=payload))
    x.request.payload = x.host.payload
    if operation == 'HOLDOUT':
        (x.directory/'stage.json').unlink()
    else:
        prior = SessionAttempt(operation='PARITY', job_id='job_'+'9'*32, attempt_id='attempt_'+'9'*32,
            worker_id='worker', lease_token_sha256='c'*64)
        x.previous = x.module.next_stage(x.previous, prior, session_digest='a'*64, host_digest='d'*64)
    if native_inputs:
        database_calls = []
        settings = x.module.owner_database_settings
        monkeypatch.setattr(x.module, 'owner_database_settings',
            lambda *args: (database_calls.append(args) or settings(*args)))
        with pytest.raises(ValueError, match='native inputs are only valid for PARITY'):
            x.module.install_stage(x.request_path, x.credentials, native_closure=x.config)
        assert not database_calls and not x.queries and not x.helper_calls and not x.published
        return
    stage = x.module.install_stage(x.request_path, x.credentials)
    assert stage.attempts[-1].operation == operation
    assert [p.name for p, _, _ in x.published] == ['stage.json']
    assert not x.helper_calls


def test_stage_cli_passes_explicit_p1_config_and_rejects_partial_inputs(monkeypatch):
    from scripts import p3_local_controller as module
    seen = []
    monkeypatch.setattr(module, 'install_stage', lambda *a, **kw:
        (seen.append((a, kw)) or {}))
    args = ['install-stage', '--request', '/synthetic/request', '--owner-credentials', '/synthetic/observer']
    module.main(args)
    assert seen[-1][1]['native_closure'] is None
    module.main(args+['--p1-closure-root', '/qualified/runtime', '--p1-closure-artifacts',
                     '/qualified/artifacts', '--bubblewrap', '/qualified/bwrap'])
    assert seen[-1][1]['native_closure'].artifact_directory == Path('/qualified/artifacts')
    with pytest.raises(SystemExit):
        module.main(args+['--p1-closure-root', '/qualified/runtime'])
    assert len(seen) == 2


@pytest.mark.parametrize('fault', ['missing-root', 'wrong-sandbox', 'worker-binding', 'session',
                                  'runtime', 'claim', 'expiry', 'closure', 'publication', 'readback', 'existing'])
def test_parity_owner_never_admits_unready_stage(qualified_parity_owner, monkeypatch, fault):
    import json
    from dataclasses import replace
    from services.job_worker import p1_nautilus_closure
    from scripts import p3_local_host as worker
    x = qualified_parity_owner
    config = x.config
    if fault == 'missing-root':
        config = replace(config, artifact_directory=Path('relative'))
    elif fault == 'wrong-sandbox':
        config = replace(config, sandbox_executable=Path('/unqualified/bwrap'))
    elif fault == 'closure':
        def reject(_):
            raise ValueError('synthetic pinned closure rejected')
        monkeypatch.setattr(p1_nautilus_closure, 'attest_p1_nautilus_closure', reject)
    elif fault in {'publication', 'readback', 'existing'}:
        publish = x.module.publish_protected
        if fault == 'existing':
            (x.directory/'native.json').touch()
            x.files['native.json'] = canonical_json_bytes({**x.native.command['profile'], 'attempt_id':'stale'})
        def publish_fault(path, raw, **kwargs):
            if path.name == 'native.json' and fault == 'publication':
                raise ValueError('synthetic publication failed')
            publish(path, raw, **kwargs)
            if path.name == 'native.json' and fault == 'readback':
                document = json.loads(raw)
                document['session_sha256'] = 'e'*64
                x.files[path.name] = canonical_json_bytes(document)
        monkeypatch.setattr(x.module, 'publish_protected', publish_fault)
    else:
        helper = worker.as_worker
        def changed(*args):
            result = helper(*args)
            if fault == 'worker-binding':
                result['attempt_id'] = 'substituted'
            elif fault == 'session':
                read = x.module.read_protected_canonical_json_current
                monkeypatch.setattr(x.module, 'read_protected_canonical_json_current',
                    lambda path: ({}, 'e'*64) if path.name == 'session.json' else read(path))
            elif fault == 'runtime':
                x.current_runtime[0] = {'generation':'replaced'}
            elif fault == 'claim':
                observed = x.module._observe_stage_claim
                monkeypatch.setattr(x.module, '_observe_stage_claim',
                    lambda *a: observed(*a).model_copy(update={'attempt_id':'replaced'}))
            elif fault == 'expiry':
                x.session.expires_at = datetime.now(UTC)-timedelta(seconds=1)
            return result
        monkeypatch.setattr(worker, 'as_worker', changed)
    with pytest.raises(ValueError):
        x.module.install_stage(x.request_path, x.credentials, native_closure=config)
    assert not any(p.name == 'stage.json' for p, *_ in x.published)


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
            module.owner_database_settings(tmp_path/'root-observer',host,now+timedelta(seconds=90),host.identity)
        assert 'database-password' not in reads
        if fault=='uid':assert not reads
    else:
        settings, observed_runtime, deadline = module.owner_database_settings(tmp_path/'root-observer',host,now+timedelta(seconds=90),host.identity)
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
    context = (request,tmp_path,host,'b'*64,identity)
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
    monkeypatch.setattr('scripts.p3_lane.issuer_lock',lambda _:nullcontext())
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


@pytest.mark.parametrize('lane', ['LOCAL', 'GITHUB'])
@pytest.mark.parametrize('wrong_owner', [False, True])
def test_session_issuer_observes_actual_process_before_publication(tmp_path, monkeypatch, wrong_owner, lane):
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
    launch_model, launch_schema, observation_schema = LocalSessionLaunch, 'p3-local-session-launch-v1', 'p3-local-session-owner-observation-v1'
    if lane == 'GITHUB':
        from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity
        from services.job_worker.p3_session import GithubSessionLaunch
        from tests.p3.github_lane import GithubLane
        identity = GithubRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='GITHUB',
            issuer_identity='p3-authority', repository='nam176hermes/Trading-Agent', workflow_ref='p3-authority.yml',
            run_id=41, attempt=2)))
        lane_double = GithubLane(monkeypatch, source=source, run_id=41, attempt=2, operation='p3-holdout-primary-v1')
        launch_model, launch_schema, observation_schema = GithubSessionLaunch, 'p3-session-launch-v1', 'p3-session-owner-observation-v1'
    launch = launch_model.model_validate_json(canonical_json_bytes(dict(
        schema_version=launch_schema, identity=identity, source=source, job_id='job_owner',
        host_profile_sha256='a'*64, worker_pid=os.getpid(), worker_start_ticks=process.start_ticks,
        worker_uid=os.geteuid(), controller_pid=os.getppid(),
        issued_at=now.isoformat().replace('+00:00','Z'), workflow_deadline=deadline.isoformat().replace('+00:00','Z'))))
    observation = dict(schema_version=observation_schema,
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
    monkeypatch.setattr(module, '_session_context', lambda _:(request, tmp_path, host, 'a'*64, identity))
    monkeypatch.setattr(module, '_protected_model', lambda *_:launch)
    monkeypatch.setattr(module, 'ProtectedQualificationStore', lambda _:object())
    monkeypatch.setattr(module, '_read', lambda *_:SimpleNamespace(holdout_commitment='f'*64))
    monkeypatch.setattr('scripts.p3_lane.issuer_lock', lambda _:nullcontext())
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
        if lane == 'GITHUB':
            # The hosted session binds the attested run and the attested boot, never a LOCAL identity.
            from tests.p3.github_lane import HOST
            assert (session.workflow_run_id, session.workflow_attempt) == (41, 2)
            assert session.boot_id == HOST[1] and not hasattr(session, 'identity')
            lane_double.drift('revoked', 'p3-holdout-primary-v1')
            with pytest.raises(ValueError):
                module.install_session(tmp_path/'request', tmp_path/'proof')
