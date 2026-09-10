"""The protected authority login is separate from Job API/worker credentials."""
from contextlib import contextmanager
import hashlib
from types import SimpleNamespace

import pytest

from packages.alpha_lifecycle.contracts.authority import RunAuthorization, ReviewApproval
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_worker.p3_operation_fixture import _authorization
from tests.p3.test_job_api import _alpha_request


@pytest.mark.parametrize('fault', [None, 'api_role', 'changed_digest', 'database_error', 'remote_host'])
def test_acceptance_uses_protected_login_and_commits_only_bound_response(monkeypatch,tmp_path,fault):
    from services.job_store import p3_operation_authority as module
    raw, intent_raw, review_raw = _authorization(_alpha_request().payload.expected_source)
    authorization = RunAuthorization.model_validate_json(raw)
    intent = P3OperationInput.model_validate_json(intent_raw)
    review = ReviewApproval.model_validate_json(review_raw)
    expected = hashlib.sha256(raw.encode()).hexdigest()
    calls, commits = [], []
    credentials = {'database-host':'127.0.0.1','database-port':'55432','database-name':'disposable-source-test','database-password':'synthetic-private-password'}
    if fault == 'remote_host':
        credentials['database-host'] = 'example.com'
    def credential(env,name):
        assert env == {'CREDENTIALS_DIRECTORY':str(tmp_path)}
        return credentials[name]
    monkeypatch.setattr(module,'read_systemd_credential',credential)
    class Connection:
        def execute(self,sql,parameters=()):
            calls.append((sql,parameters))
            if 'session_user' in sql:
                user = 'trading_job_api' if fault == 'api_role' else 'trading_p3_authority'
                return SimpleNamespace(fetchone=lambda:(user,user))
            assert 'accept_p3_operation_authorization' in sql
            assert parameters == (raw,intent_raw,review_raw)
            if fault == 'database_error':
                raise module.psycopg.OperationalError('synthetic-private-password')
            return SimpleNamespace(fetchone=lambda:('f'*64 if fault == 'changed_digest' else expected,))
    @contextmanager
    def connect(conninfo,**kwargs):
        assert module.psycopg.conninfo.conninfo_to_dict(conninfo)['user'] == 'trading_p3_authority'
        yield Connection()
        commits.append(True)
    monkeypatch.setattr(module.psycopg,'connect',connect)
    if fault is None:
        module.accept_operation_authorization(authorization,intent,review,credential_directory=tmp_path)
        assert commits == [True]
        assert len(calls) == 2
    else:
        with pytest.raises(module.AuthorityHeld) as caught:
            module.accept_operation_authorization(authorization,intent,review,credential_directory=tmp_path)
        assert 'synthetic-private-password' not in str(caught.value)
        assert not commits
        if fault in {'api_role','remote_host'}:
            assert all('accept_p3_operation_authorization' not in sql for sql,_ in calls)


def test_job_settings_do_not_allow_the_protected_authority_role():
    from services.job_store.config import JobStoreSettings
    settings = JobStoreSettings('127.0.0.1',5432,'synthetic','trading_p3_authority','synthetic')
    with pytest.raises(ValueError,match='not allowed'):
        settings.require_user('trading_p3_authority')


def _dispatch_inputs(tmp_path,monkeypatch):
    from scripts import p3_authority as dispatch
    from services.job_store import p3_operation_authority as authority
    from services.job_worker import p3_integration
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from packages.data_catalog.artifact_store import LocalArtifactStore
    source = _alpha_request().payload.expected_source
    raw, intent_raw, review_raw = _authorization(source)
    authorization = RunAuthorization.model_validate_json(raw)
    intent = P3OperationInput.model_validate_json(intent_raw)
    review = ReviewApproval.model_validate_json(review_raw)
    payload = build_alpha_campaign_payload(authorization,source,intent.workflow_operation,operation_input=intent)
    request = SimpleNamespace(authorization=authorization,operation_input=intent)
    store_root = tmp_path/'cas'
    store_root.mkdir(mode=0o700)
    store = LocalArtifactStore(store_root)
    store.put_bytes(b'{"purpose":"synthetic-input-not-an-official-inputset"}',media_type='application/json')
    monkeypatch.setattr(dispatch,'preflight',lambda *args:(request,payload))
    monkeypatch.setattr(p3_integration,'_read_review',lambda *args:review)
    monkeypatch.setattr(p3_integration,'_read_authority_bytes',lambda *args:intent_raw.encode())
    monkeypatch.setattr(dispatch,'_read_token',lambda *args:'synthetic-api-token')
    monkeypatch.setenv('P3_AUTHORITY_CREDENTIALS_DIRECTORY',str(tmp_path/'credentials'))
    return dispatch,authority,authorization,intent,review,payload


@pytest.mark.parametrize('mode', ['accepted','denied','missing_credentials'])
def test_official_dispatch_accepts_sql_authority_before_job_api(tmp_path,monkeypatch,mode):
    dispatch,authority,authorization,intent,review,payload = _dispatch_inputs(tmp_path,monkeypatch)
    if mode == 'missing_credentials':
        monkeypatch.delenv('P3_AUTHORITY_CREDENTIALS_DIRECTORY',raising=False)
    else:
        monkeypatch.setenv('P3_AUTHORITY_CREDENTIALS_DIRECTORY',str(tmp_path/'credentials'))
    calls = []
    def accept(a,i,r,*,credential_directory):
        assert (a,i,r) == (authorization,intent,review)
        assert credential_directory == tmp_path/'credentials'
        calls.append('accept')
        if mode == 'denied':
            raise authority.AuthorityHeld('HELD E_SQL_AUTHORITY: denied')
    monkeypatch.setattr(authority,'accept_operation_authorization',accept)
    class Enqueued(Exception):
        pass
    def post(method,path,token,body):
        assert calls == ['accept']
        assert (method,path) == ('POST','/v1/jobs')
        calls.append('post')
        raise Enqueued
    monkeypatch.setattr(dispatch,'_request_json',post)
    expected = Enqueued if mode == 'accepted' else authority.AuthorityHeld
    with pytest.raises(expected):
        dispatch.dispatch(tmp_path/'request',tmp_path/'token',tmp_path/'output',intent.workflow_operation,
            artifact_root=tmp_path/'cas',manifest_file=tmp_path/'manifest',review_file=tmp_path/'review')
    assert calls == (['accept','post'] if mode == 'accepted' else ['accept'] if mode == 'denied' else [])


@pytest.mark.parametrize('phase', ['enqueue','detail'])
@pytest.mark.parametrize('fault', ['payload','fingerprint','priority','actor','job_type','job_id'])
def test_dispatch_rejects_valid_envelopes_for_other_jobs(tmp_path,monkeypatch,phase,fault):
    import copy
    from packages.job_contracts import payload_fingerprint
    from apps.job_api.contracts import JobDeduplicatedEnvelope,JobDetailEnvelope
    dispatch,authority,authorization,intent,review,payload = _dispatch_inputs(tmp_path,monkeypatch)
    monkeypatch.setattr(authority,'accept_operation_authorization',lambda *args,**kwargs:None)
    outputs = []
    monkeypatch.setattr(dispatch,'_write',lambda directory,name,value:outputs.append(name))
    meta = dict(schema_version='1.0.0',trace_id='synthetic-response',generated_at='2026-09-10T00:00:00Z')
    job = dict(job_id='job_accepted',job_type='ALPHA_CAMPAIGN',state='SUCCEEDED',
        payload=payload.model_dump(mode='json'),payload_fingerprint=payload_fingerprint(payload),
        actor=dict(actor_type='OPERATOR',actor_id=review.operator_identity),priority=0,
        requested_at='2026-09-10T00:00:00Z',updated_at='2026-09-10T00:00:00Z',attempt_count=1)
    def response(method,path,token,body=None):
        changed = copy.deepcopy(job)
        target = (method == 'POST') == (phase == 'enqueue')
        if target:
            if fault == 'payload':
                changed['payload']['expected_source']['commit_sha'] = 'f'*40
            elif fault == 'fingerprint':
                changed['payload_fingerprint'] = 'f'*64
            elif fault == 'actor':
                changed['actor']['actor_id'] = 'other-operator'
            elif fault == 'job_type':
                changed.update(job_type='SNAPSHOT',payload={'scope':'default','requested_as_of':None})
            elif fault == 'priority':
                changed['priority'] = 1
            elif phase == 'detail':
                changed['job_id'] = 'job_other'
        envelope = dict(**meta,data=dict(outcome='DEDUPLICATED',job=changed)) if method == 'POST' else dict(**meta,data=dict(job=changed))
        (JobDeduplicatedEnvelope if method == 'POST' else JobDetailEnvelope).model_validate(envelope)
        return envelope
    monkeypatch.setattr(dispatch,'_request_json',response)
    # The enqueue response first assigns the opaque ID; only later responses
    # can be checked against it. This vector is the matching positive route.
    if phase == 'enqueue' and fault == 'job_id':
        dispatch.dispatch(tmp_path/'request',tmp_path/'token',tmp_path/'output',intent.workflow_operation,
            artifact_root=tmp_path/'cas',manifest_file=tmp_path/'manifest',review_file=tmp_path/'review')
        assert outputs == ['enqueue-response.json','job-result.json']
    else:
        with pytest.raises(RuntimeError,match='HELD E_JOB_API'):
            dispatch.dispatch(tmp_path/'request',tmp_path/'token',tmp_path/'output',intent.workflow_operation,
                artifact_root=tmp_path/'cas',manifest_file=tmp_path/'manifest',review_file=tmp_path/'review')
        assert 'job-result.json' not in outputs
