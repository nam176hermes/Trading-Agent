from uuid import UUID
import pytest

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.data_contracts import ArtifactRefV1
from packages.job_contracts import AlphaCampaignOperation, AlphaCampaignPayload
from scripts.p3_authority import build_enqueue_body


@pytest.mark.parametrize('reader', ['token', 'approval'])
def test_authority_readers_reject_fifo_without_waiting_for_a_writer(tmp_path, reader):
    import os
    import subprocess
    import sys

    path = tmp_path / 'authority.fifo'
    os.mkfifo(path, mode=0o600)
    code = '''
from pathlib import Path
import sys
from scripts.p3_authority import _read_token
from services.job_worker.p3_integration import _read_authority_bytes
reader = _read_token if sys.argv[1] == 'token' else _read_authority_bytes
try:
    reader(Path(sys.argv[2]))
except RuntimeError as error:
    assert 'HELD' in str(error)
else:
    raise AssertionError('FIFO accepted as authority')
'''
    completed = subprocess.run(
        [sys.executable, '-c', code, reader, str(path)],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _ref(value: str) -> ArtifactRefV1:
    return ArtifactRefV1(
        content_sha256=value * 64,
        size_bytes=1,
        media_type="application/json",
        locator=f"{value * 64}.blob",
    )


def test_dispatch_body_is_closed_and_idempotent_by_authorization_nonce() -> None:
    payload = AlphaCampaignPayload(
        schema_version="p3-alpha-campaign-payload-v1",
        operation=AlphaCampaignOperation.BASELINES,
        manifest_ref=_ref("a"),
        authorization_ref=_ref("b"),
        expected_source=SourceIdentity(
            commit_sha="c" * 40,
            tree_sha="d" * 40,
            closure_schema_version="source-closure-v1",
            closure_policy_sha256="e" * 64,
            closure_sha256="f" * 64,
        ),
        logical_trial_id="p3-baselines-v1",
    )
    body = build_enqueue_body(payload, UUID("00000000-0000-4000-8000-000000000001"))
    assert body.job_type.value == "ALPHA_CAMPAIGN"
    assert body.payload == payload
    assert (
        body.idempotency_key
        == "p3:p3-baselines-v1:00000000-0000-4000-8000-000000000001"
    )
    assert body.priority == 0


@pytest.mark.parametrize("drift", (None, "head", "revoked", "stale", "operation", "host"))
def test_shape_preflight_does_not_emit_an_execution_approval(
    tmp_path, monkeypatch, drift
):
    from datetime import UTC, datetime, timedelta
    import hashlib
    import json
    from scripts import p3_authority
    from packages.engine_contracts.serialization import canonical_json_bytes
    from tests.p3.test_authority import _request
    from tests.p3.test_operation_input import intent

    request, source = _request("2026-01-01T01:00:00Z")
    now = datetime.now(UTC)
    body = request["authorization"]
    body.update(
        issued_at=(now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        expires_at=(now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    )
    body.pop("digest")
    body["digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    request["operation_input"] = json.loads(intent(input_set_ref=body["input_set_ref"]))
    from tests.p3.github_lane import GithubLane
    lane = GithubLane(monkeypatch, source=source)
    lane.admit_inputs(body["input_set_ref"])
    path = lane.request(request)
    monkeypatch.setattr(
        p3_authority,
        "canonical_source_identity",
        lambda root: source.model_dump(mode="json"),
    )
    monkeypatch.setattr(
        __import__("packages.project_status",fromlist=["derive_project_status"]),
        "derive_project_status",
        lambda root: {
            "gates": {"HWC_SOURCE_READY": "PASS", "PRE_P3_READY": "PASS"},
            "p3_alpha_development_allowed": True,
        },
    )
    if drift:
        lane.drift(drift, "p3-baselines-v1")
    output = tmp_path / "output"
    if drift:
        from packages.alpha_lifecycle.authority import AuthorityHeld

        with pytest.raises(AuthorityHeld, match="ISSUER"):
            p3_authority.preflight(path, output, "p3-baselines-v1")
        return
    p3_authority.preflight(path, output, "p3-baselines-v1")
    artifact = json.loads((output / "preflight.json").read_bytes())
    assert artifact["status"] == "STRUCTURE_VALIDATED"
    assert artifact["execution_authorized"] is False
    inventory = json.loads((output / "input-inventory.json").read_bytes())
    payload = json.loads((output / "payload.json").read_bytes())
    assert inventory["authorization_ref"] == payload["authorization_ref"]


@pytest.fixture
def dispatch_fixture(tmp_path,monkeypatch):
    """Real request/envelope parsing; protected source/review and HTTP transport are synthetic."""
    from dataclasses import replace
    import hashlib
    from datetime import UTC,datetime,timedelta
    from types import SimpleNamespace
    from apps.job_api.app import _job_metadata
    from packages.alpha_lifecycle.authority import _FixtureAuthorization,build_alpha_campaign_payload
    from packages.alpha_lifecycle.contracts.authority import ReviewApproval
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    from packages.job_contracts import payload_fingerprint
    import json
    from scripts import p3_authority as command
    from services.job_worker import p3_integration
    from tests.jobs.test_job_api import job_record
    from tests.p3.test_authority import _request
    from tests.p3.test_replica_execution import _seal
    _,source=_request('2026-01-01T01:00:00Z')
    root=tmp_path/'store'; root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    safe=dict(broker=False,live=False,network=False,production=False)
    plan_ref=_seal(store,schema_version='p3-integration-fixture-plan-v1',source=source,
        purpose='SYNTHETIC_ONLY',native_request_digest='1'*64,
        policy_set_sha256=hashlib.sha256((p3_integration.ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()).hexdigest(),
        sql_revision='0026_p3_holdout_disclosure',cleanup_policy='OWNED_ROOTS_ONLY')
    now=datetime.now(UTC)
    def utc(value): return value.isoformat(timespec='microseconds').replace('+00:00','Z')
    plan=p3_integration.FixturePlan.model_validate_json(store.read_bytes(plan_ref))
    review_ref=_seal(store,schema_version='p3-review-approval-v1',source=source,subject_digests=(plan.digest,),
        operator_identity='operator-1',reviewer_identity='synthetic-reviewer',review_execution_id='synthetic-review',
        verdict='APPROVED',issued_at=utc(now-timedelta(minutes=2)),expires_at=utc(now+timedelta(hours=2)),
        evidence_ref=store.put_bytes(b'{}',media_type='application/json'),authority=safe)
    auth_ref=_seal(store,schema_version='p3-fixture-authorization-v1',fixture_plan_ref=plan_ref,review_ref=review_ref,
        operation='PARITY',issued_at=utc(now-timedelta(minutes=1)),expires_at=utc(now+timedelta(hours=1)),
        nonce='00000000-0000-4000-8000-000000000001',issuer_workflow='p3-authority.yml',issuer_run_id=1,
        issuer_attempt=1,authority=safe)
    authorization=_FixtureAuthorization.model_validate_json(store.read_bytes(auth_ref))
    from tests.p3.github_lane import GithubLane
    lane=GithubLane(monkeypatch,source=source,operation='p3-integration-fixture-v1')
    request=lane.request(json.loads(canonical_json_bytes(dict(schema_version='p3-authority-request-file-v1',
        execution_source=source,authorization=authorization))))
    payload=build_alpha_campaign_payload(authorization,source,'p3-integration-fixture-v1')
    monkeypatch.setattr(command,'canonical_source_identity',lambda root:source.model_dump(mode='json'))
    monkeypatch.setattr(__import__('packages.project_status',fromlist=['derive_project_status']),'derive_project_status',lambda root:dict(gates=dict(HWC_SOURCE_READY='PASS',PRE_P3_READY='PASS'),p3_alpha_development_allowed=True))
    monkeypatch.setattr(p3_integration,'_read_review',lambda *args:ReviewApproval.model_validate_json(store.read_bytes(review_ref)))
    monkeypatch.setattr(p3_integration,'_read_authority_bytes',lambda path:store.read_bytes(plan_ref))
    monkeypatch.setattr(command,'_read_token',lambda path:'synthetic-test-token')
    record=replace(job_record(),job_type='ALPHA_CAMPAIGN',payload=payload,payload_fingerprint=payload_fingerprint(payload),priority=0)
    calls=[]
    state=SimpleNamespace(worker_ran=False, request_json=command._request_json, lane=lane)
    def api(method,path,token,body=None):
        calls.append((method,path))
        envelope=dict(schema_version='1.0.0',trace_id='synthetic.flow',generated_at=utc(now))
        if method=='POST':
            return dict(**envelope,data=dict(outcome='ENQUEUED',job=_job_metadata(record)))
        assert state.worker_ran,'dispatcher polled before the workflow could run its worker'
        return dict(**envelope,data=dict(job=_job_metadata(replace(record,state='SUCCEEDED',result_hash='a'*64))))
    monkeypatch.setattr(command,'_request_json',api)
    args=(request,tmp_path/'token',tmp_path/'output','p3-integration-fixture-v1')
    kwargs=dict(artifact_root=root,manifest_file=tmp_path/'manifest',review_file=tmp_path/'review')
    return command,args,kwargs,calls,state


def test_enqueue_returns_before_worker_and_wait_only_reads_canonical_job(dispatch_fixture):
    import json
    command,args,kwargs,calls,state=dispatch_fixture
    command.enqueue(*args,**kwargs)
    assert calls==[('POST','/v1/jobs')]
    assert not (args[2]/'job-result.json').exists()
    state.worker_ran=True
    command.wait_for_result(*args,**kwargs)
    assert calls==[('POST','/v1/jobs'),('GET','/v1/jobs/job_123')]
    assert json.loads((args[2]/'job-result.json').read_bytes())['data']['job']['state']=='SUCCEEDED'


def test_worker_reads_bound_enqueue_without_waiting_or_repeating_acceptance(dispatch_fixture):
    command,args,kwargs,calls,state=dispatch_fixture
    expected=command.enqueue(*args,**kwargs)
    observed=command.read_enqueued_job(args[0],args[2],args[3],**kwargs)
    assert observed==expected
    assert calls==[('POST','/v1/jobs')] and state.worker_ran is False


@pytest.mark.parametrize('fault', ['payload','actor','noncanonical','symlink','oversize','permissions','result_only'])
def test_wait_does_not_trust_saved_transport_as_job_authority(dispatch_fixture,fault):
    import json
    from packages.engine_contracts.serialization import canonical_json_bytes
    command,args,kwargs,calls,state=dispatch_fixture
    command.enqueue(*args,**kwargs)
    state.worker_ran=True
    saved=args[2]/'enqueue-response.json'
    raw=saved.read_bytes()
    value=json.loads(raw)
    if fault=='payload':
        value['data']['job']['payload']['expected_source']['commit_sha']='f'*40
        saved.write_bytes(canonical_json_bytes(value)+b'\n')
    elif fault=='actor':
        value['data']['job']['actor']['actor_id']='other-operator'
        saved.write_bytes(canonical_json_bytes(value)+b'\n')
    elif fault=='noncanonical':
        saved.write_bytes(json.dumps(value,indent=2).encode())
    elif fault=='symlink':
        target=args[2]/'other.json'; saved.rename(target); saved.symlink_to(target)
    elif fault=='oversize':
        saved.write_bytes(b' '*(command.MAX_RESPONSE_BYTES+1))
    elif fault=='permissions':
        saved.chmod(0o644)
    else:
        saved.unlink()
        (args[2]/'job-result.json').write_bytes(raw)
    with pytest.raises(RuntimeError,match='HELD E_JOB_API'):
        command.wait_for_result(*args,**kwargs)
    assert calls==[('POST','/v1/jobs')]


@pytest.mark.parametrize('state_value', ['FAILED','BLOCKED','CANCELLED'])
def test_wait_preserves_failed_canonical_job_without_reenqueue(dispatch_fixture,monkeypatch,state_value):
    import json
    command,args,kwargs,calls,state=dispatch_fixture
    command.enqueue(*args,**kwargs)
    state.worker_ran=True
    original=command._request_json
    def response(*values,**options):
        result=original(*values,**options)
        result['data']['job']['state']=state_value
        return result
    monkeypatch.setattr(command,'_request_json',response)
    with pytest.raises(RuntimeError,match=f'terminal state {state_value}'):
        command.wait_for_result(*args,**kwargs)
    assert calls==[('POST','/v1/jobs'),('GET','/v1/jobs/job_123')]
    assert json.loads((args[2]/'job-result.json').read_bytes())['data']['job']['state']==state_value


def test_wait_rechecks_protected_source_before_any_job_read(dispatch_fixture,monkeypatch):
    command,args,kwargs,calls,state=dispatch_fixture
    command.enqueue(*args,**kwargs)
    state.worker_ran=True
    monkeypatch.setattr(__import__('packages.project_status',fromlist=['derive_project_status']),'derive_project_status',lambda root:dict(gates=dict(HWC_SOURCE_READY='HELD',PRE_P3_READY='PASS'),p3_alpha_development_allowed=False))
    with pytest.raises(RuntimeError,match='E_SOURCE_READY'):
        command.wait_for_result(*args,**kwargs)
    assert calls==[('POST','/v1/jobs')]


def test_wait_observes_authorized_job_beyond_single_command_budget(dispatch_fixture, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    import json

    command, args, kwargs, calls, state = dispatch_fixture
    command.enqueue(*args, **kwargs)
    state.worker_ran = True
    original = command._request_json
    started = datetime.now(UTC)
    elapsed = [0.0]

    def response(*values, **options):
        result = original(*values, **options)
        result['data']['job']['state'] = 'SUCCEEDED' if elapsed[0] >= 1800 else 'RUNNING'
        return result

    def sleep(seconds):
        elapsed[0] += 60

    monkeypatch.setattr(command, '_request_json', response)
    monkeypatch.setattr(command, 'time', SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    monkeypatch.setattr(command, 'datetime', SimpleNamespace(now=lambda tz: started + timedelta(seconds=elapsed[0])), raising=False)
    command.wait_for_result(*args, **kwargs)
    assert elapsed[0] == 1800
    assert json.loads((args[2]/'job-result.json').read_bytes())['data']['job']['state'] == 'SUCCEEDED'
    assert calls.count(('POST', '/v1/jobs')) == 1
    assert all(method == 'GET' for method, _ in calls[1:])


@pytest.mark.parametrize('terminal', ['SUCCEEDED', None, 'BLOCKED'])
def test_holdout_observation_covers_declared_execution_and_postrun(dispatch_fixture, monkeypatch, terminal):
    """An observation timeout cannot be shorter than the admitted operation's budgets."""
    import json
    from types import SimpleNamespace
    from packages.job_contracts import payload_fingerprint
    command, args, kwargs, calls, state = dispatch_fixture
    job_id, body, operator = command.enqueue(*args, **kwargs)
    payload = body.payload.model_copy(update={
        'operation': AlphaCampaignOperation.HOLDOUT,
        'logical_trial_id': 'p3-holdout-primary-v1',
    })
    request, _ = command._validated_request(args[0], args[3])
    body = command.build_enqueue_body(payload, request.authorization.nonce)
    monkeypatch.setattr(command, '_validated_request', lambda *values: (request, payload))
    original = command._request_json
    clock = [0.0]

    def sleep(seconds):
        clock[0] += 60

    def response(*values, **options):
        state.worker_ran = True
        result = original(*values, **options)
        job = result['data']['job']
        job['payload'] = payload.model_dump(mode='json')
        job['payload_fingerprint'] = payload_fingerprint(payload)
        job['state'] = terminal if terminal is not None and clock[0] >= 1320 else 'RUNNING'
        return result

    monkeypatch.setattr(command, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(command, '_request_json', response)
    if terminal == 'SUCCEEDED':
        command._wait(job_id, body, operator, args[1], args[2], request_file=args[0])
        assert json.loads((args[2]/'job-result.json').read_bytes())['data']['job']['state'] == terminal
        assert clock[0] == 1320
    else:
        expected = 'authorization wait expired' if terminal is None else 'terminal state BLOCKED'
        with pytest.raises(RuntimeError, match=expected):
            command._wait(job_id, body, operator, args[1], args[2], request_file=args[0])
        assert clock[0] == (3600 if terminal is None else 1320)
    assert calls.count(('POST', '/v1/jobs')) == 1
    assert all(method == 'GET' for method, _ in calls[1:])


@pytest.mark.parametrize('fault', ['expiry', 'clock_forward', 'clock_back', 'late_terminal'])
def test_wait_expiry_cannot_be_extended_or_accept_late_success(dispatch_fixture, monkeypatch, fault):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    command, args, kwargs, calls, state = dispatch_fixture
    command.enqueue(*args, **kwargs)
    state.worker_ran = True
    original = command._request_json
    started = datetime.now(UTC)
    elapsed = [0.0]

    def current_time(tz):
        offset = (3600 if fault == 'clock_forward' and elapsed[0] else
                  -900 if fault == 'clock_back' and elapsed[0] >= 1200 else 0)
        return started + timedelta(seconds=elapsed[0] + offset)

    def response(*values, **options):
        result = original(*values, **options)
        if fault == 'late_terminal':
            elapsed[0] = 3600
        result['data']['job']['state'] = 'SUCCEEDED' if fault == 'late_terminal' else 'RUNNING'
        return result

    def sleep(seconds):
        elapsed[0] += 60

    monkeypatch.setattr(command, '_request_json', response)
    monkeypatch.setattr(command, 'time', SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    monkeypatch.setattr(command, 'datetime', SimpleNamespace(now=current_time))
    with pytest.raises(RuntimeError, match='authorization wait expired'):
        command.wait_for_result(*args, **kwargs)
    assert elapsed[0] == (60 if fault == 'clock_forward' else 3600)
    assert not (args[2]/'job-result.json').exists()
    assert calls.count(('POST', '/v1/jobs')) == 1
    assert all(method == 'GET' for method, _ in calls[1:])


def test_wait_revalidates_same_request_before_observation(dispatch_fixture, monkeypatch):
    command, args, kwargs, calls, state = dispatch_fixture
    command.enqueue(*args, **kwargs)
    original = command._validated_request
    observed = []

    def changed(*values):
        request, payload = original(*values)
        observed.append(request)
        if len(observed) == 2:
            request = request.model_copy(update={'authorization': request.authorization.model_copy(
                update={'nonce': UUID('00000000-0000-4000-8000-000000000002')})})
        return request, payload

    monkeypatch.setattr(command, '_validated_request', changed)
    with pytest.raises(RuntimeError, match='wait request differs'):
        command.wait_for_result(*args, **kwargs)
    assert len(observed) == 2 and calls == [('POST', '/v1/jobs')]
    assert not (args[2]/'job-result.json').exists()


def test_dispatch_uses_bound_observer(dispatch_fixture):
    import json
    command, args, kwargs, calls, state = dispatch_fixture
    state.worker_ran = True
    command.dispatch(*args, **kwargs)
    assert calls == [('POST', '/v1/jobs'), ('GET', '/v1/jobs/job_123')]
    assert json.loads((args[2]/'job-result.json').read_bytes())['data']['job']['state'] == 'SUCCEEDED'


def test_wait_stops_on_job_api_authentication_failure(dispatch_fixture, monkeypatch):
    from urllib.error import HTTPError
    command, args, kwargs, calls, state = dispatch_fixture
    command.enqueue(*args, **kwargs)
    monkeypatch.setattr(command, '_request_json', state.request_json)
    requests = []

    def revoked(request, **options):
        requests.append(request.method)
        raise HTTPError(request.full_url, 403, 'Forbidden', None, None)

    monkeypatch.setattr(command, 'urlopen', revoked)
    with pytest.raises(RuntimeError, match='loopback Job API request failed'):
        command.wait_for_result(*args, **kwargs)
    assert requests == ['GET'] and not (args[2]/'job-result.json').exists()


@pytest.mark.parametrize('fault',[None,'head','revoked','stale','operation','host',
    'rejected','subject','expired','same_actor','source','policy'])
def test_fixture_stage_verifies_review_and_workflow_before_any_write(dispatch_fixture,monkeypatch,fault):
    from datetime import timedelta
    from packages.alpha_lifecycle.authority import AuthorityHeld,build_alpha_campaign_payload
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.engine_contracts.serialization import canonical_json_bytes
    from services.job_worker import p3_integration
    from tests.p3.test_publication import _changed
    command,args,kwargs,_,state=dispatch_fixture
    request,payload=command._validated_request(args[0],args[3])
    store=LocalArtifactStore(kwargs['artifact_root'])
    plan=p3_integration.FixturePlan.model_validate_json(store.read_bytes(payload.manifest_ref))
    approval=p3_integration._read_review(kwargs['review_file'],request.authorization.review_ref)
    if fault in state.lane.DRIFTS:
        state.lane.drift(fault,'p3-integration-fixture-v1')
    elif fault=='policy':
        plan=_changed(plan,policy_set_sha256='0'*64)
    elif fault:
        changes={'rejected':dict(verdict='REJECTED'),'subject':dict(subject_digests=('a'*64,)),
            'expired':dict(expires_at=(request.authorization.issued_at-timedelta(seconds=1)).isoformat().replace('+00:00','Z')),
            'same_actor':dict(reviewer_identity=approval.operator_identity),
            'source':dict(source=approval.source.model_copy(update={'commit_sha':'0'*40}))}
        approval=_changed(approval,**changes[fault])
    plan_ref=store.put_bytes(canonical_json_bytes(plan),media_type='application/json')
    review_ref=store.put_bytes(canonical_json_bytes(approval),media_type='application/json')
    authorization=_changed(request.authorization,fixture_plan_ref=plan_ref,review_ref=review_ref)
    request=request.model_copy(update={'authorization':authorization})
    payload=build_alpha_campaign_payload(authorization,payload.expected_source,args[3])
    monkeypatch.setattr(p3_integration,'_read_authority_bytes',lambda path:canonical_json_bytes(plan))
    monkeypatch.setattr(p3_integration,'_read_review',lambda *args:approval)
    writes=[]
    class ObservedStore:
        read_bytes=store.read_bytes
        def put_bytes(self,*args,**kwargs):
            writes.append(args)
            return store.put_bytes(*args,**kwargs)
    if fault:
        with pytest.raises(AuthorityHeld,match='HELD'):
            command._stage_request(request,payload,args[3],ObservedStore(),kwargs['manifest_file'],kwargs['review_file'])
        assert writes==[]
    else:
        actual,_=command._stage_request(request,payload,args[3],ObservedStore(),kwargs['manifest_file'],kwargs['review_file'])
        assert actual==payload and writes


@pytest.mark.parametrize('drift',['head','revoked','stale','operation','host'])
def test_fixture_worker_rejects_replayed_enqueue_before_credentials(dispatch_fixture,monkeypatch,drift):
    from packages.alpha_lifecycle.authority import AuthorityHeld
    from services.job_worker import main
    command,args,kwargs,_,state=dispatch_fixture
    command.enqueue(*args,**kwargs)
    values=dict(TRADING_WORKER_PROFILE='p3-fixture-v1',P3_OPERATION=args[3],
        P3_AUTHORITY_REQUEST_FILE=str(args[0]),P3_PREFLIGHT_DIRECTORY=str(args[2]),
        P3_ARTIFACT_ROOT=str(kwargs['artifact_root']),P3_MANIFEST_FILE=str(kwargs['manifest_file']),
        P3_REVIEW_FILE=str(kwargs['review_file']),CREDENTIALS_DIRECTORY='/synthetic/credentials')
    for key,value in values.items(): monkeypatch.setenv(key,value)
    state.lane.drift(drift,'p3-integration-fixture-v1')
    def forbidden(*args,**kwargs): pytest.fail('invalid fixture context reached runtime credentials or database')
    monkeypatch.setattr(main,'attest_worker_runtime_authority',forbidden)
    monkeypatch.setattr(main.JobStoreSettings,'from_systemd_credentials',forbidden)
    monkeypatch.setattr(main,'WorkerRepository',forbidden)
    with pytest.raises(AuthorityHeld,match='ISSUER'):
        main.main()


def test_enqueue_post_waits_longer_than_detail_polls(monkeypatch):
    # A slow first enqueue on a cold Job API lost the committed ACK twice at 10 s.
    from scripts import p3_authority as command

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b'{}'

    def urlopen(request, timeout):
        seen[request.get_method()] = timeout
        return Response()

    monkeypatch.setattr(command, 'urlopen', urlopen)
    command._request_json('POST', '/v1/jobs', 'synthetic-token', {})
    command._request_json('GET', '/v1/jobs/job_x', 'synthetic-token')
    assert seen == {'POST': 60, 'GET': 10}
