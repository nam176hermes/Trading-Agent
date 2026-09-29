"""Current LOCAL authority must surround the durable enqueue transaction."""
from types import SimpleNamespace
import json

import pytest

from packages.alpha_lifecycle import authority
from packages.alpha_lifecycle.contracts.authority import LocalRunAuthorization
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.errors import JobStoreError
from services.job_worker.p3_operation_fixture import _authorization, _sealed
from tests.jobs.test_repository_transition_capabilities import _Connection, _Result, _job_row, _repository
from tests.p3.test_job_api import _alpha_request


def local_request():
    request=_alpha_request()
    raw,intent_raw,_=_authorization(request.payload.expected_source)
    value=json.loads(raw)
    for key in ('issuer_workflow','issuer_run_id','issuer_attempt'):value.pop(key)
    value.update(schema_version='p3-local-run-authorization-v1',identity=dict(issuer_kind='LOCAL',
        issuer_identity='synthetic',run_id='00000000-0000-4000-8000-000000000002',attempt=1,
        host_id='a'*64,boot_id='00000000-0000-4000-8000-000000000001'))
    auth=LocalRunAuthorization.model_validate_json(_sealed(value))
    intent=P3OperationInput.model_validate_json(intent_raw)
    payload=authority.build_alpha_campaign_payload(auth,request.payload.expected_source,
        intent.workflow_operation,operation_input=intent)
    return request.model_copy(update={'payload':payload}),auth,intent


@pytest.mark.parametrize('fault',[None,'revoked','expired','after_insert','changed','intent','obsolete_catalog','payload','missing_acceptance'])
def test_enqueue_rereads_local_authority_inside_transaction(monkeypatch,fault):
    request,auth,intent=local_request()
    if fault=='payload':
        request=request.model_copy(update={'payload':request.payload.model_copy(update={'authorization_ref':request.payload.authorization_ref.model_copy(update={'content_sha256':'0'*64,'locator':'0'*64+'.blob'})})})
    protected=SimpleNamespace(authorization=auth,operation_input=intent)
    reads=[]
    def current(*a,**kw):
        reads.append(1)
        if fault in {'revoked','expired'} or (fault=='after_insert' and len(reads)>1):
            raise authority.AuthorityHeld('synthetic secret must not escape')
        if fault=='intent':return SimpleNamespace(authorization=auth,operation_input=None)
        if fault=='changed' and len(reads)>1:return object()
        return protected
    monkeypatch.setattr('services.job_store.p3_enqueue.validate_local_operation_authority',current)
    class Connection(_Connection):
        def execute(self,sql,parameters=None):
            if 'version_num' in sql:
                return _Result({'version_num':'0031_p3_local_identity' if fault=='obsolete_catalog' else '0033_p3_core_closure_repair'})
            if 'api_read_p3_enqueue_authority' in sql:
                if fault=='missing_acceptance':return _Result(None)
                return _Result({'authorization_text':canonical_json_bytes(auth).decode(),
                    'intent_text':canonical_json_bytes(intent).decode()})
            return super().execute(sql,parameters)
    connection=Connection({'job_id':'job_fixed','outcome':'ENQUEUED'},_job_row(request))
    repository=_repository(connection)
    if fault:
        with pytest.raises(JobStoreError) as caught:
            repository.enqueue(request,trace_id='test:local-admission')
        assert 'synthetic secret' not in str(caught.value)
        if fault=='payload':assert not reads
        assert connection.transaction_events[-1][1] is not None
        if fault in {'revoked','expired','intent','obsolete_catalog','payload','missing_acceptance'}:assert not connection.calls
    else:
        result=repository.enqueue(request,trace_id='test:local-admission')
        assert result.job.job_id=='job_fixed'
        assert len(reads)==2
        assert connection.transaction_events[-1][1] is None


@pytest.mark.parametrize('revision',['0026_p3_holdout_disclosure','0030_p3_session_terminal_fences','0033_p3_core_closure_repair'])
def test_hosted_admission_does_not_require_local_profile(monkeypatch,revision):
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from services.job_worker.p3_operation_fixture import _authorization
    request=_alpha_request()
    raw,intent_raw,_=_authorization(request.payload.expected_source)
    auth=RunAuthorization.model_validate_json(raw)
    intent=P3OperationInput.model_validate_json(intent_raw)
    payload=authority.build_alpha_campaign_payload(auth,request.payload.expected_source,intent.workflow_operation,operation_input=intent)
    request=request.model_copy(update={'payload':payload})
    monkeypatch.setattr('services.job_store.p3_enqueue.validate_local_operation_authority',lambda *a,**k:pytest.fail('hosted read LOCAL approval'))
    class Connection(_Connection):
        def execute(self,sql,parameters=None):
            if 'version_num' in sql:return _Result({'version_num':revision})
            if 'api_read_p3_enqueue_authority' in sql:return _Result({'authorization_text':raw,'intent_text':intent_raw})
            return super().execute(sql,parameters)
    connection=Connection({'job_id':'job_fixed','outcome':'ENQUEUED'},_job_row(request))
    assert _repository(connection).enqueue(request,trace_id='test:hosted').job.job_id=='job_fixed'


def test_local_fixture_admission_has_no_operation_intent(monkeypatch):
    from services.job_store.p3_enqueue import read_enqueue_authority,recheck_enqueue_authority
    from services.job_worker.p3_operation_fixture import _sealed
    request,local,_=local_request()
    data=local.model_dump(mode='json')
    for key in ('allowed_alpha_ids','input_set_ref'):data.pop(key)
    data.update(schema_version='p3-local-fixture-authorization-v1',fixture_plan_ref=request.payload.manifest_ref,operation='PARITY')
    auth=authority.FIXTURE_AUTHORIZATION.validate_json(_sealed(data))
    payload=authority.build_alpha_campaign_payload(auth,request.payload.expected_source,'p3-integration-fixture-v1')
    connection=_Connection({'version_num':'0033_p3_core_closure_repair'},
        {'authorization_text':canonical_json_bytes(auth).decode(),'intent_text':None})
    protected=SimpleNamespace(authorization=auth,operation_input=None)
    monkeypatch.setattr('services.job_store.p3_enqueue.validate_local_operation_authority',lambda *a,**k:protected)
    assert read_enqueue_authority(connection,payload)==protected
    recheck_enqueue_authority(protected,payload)
