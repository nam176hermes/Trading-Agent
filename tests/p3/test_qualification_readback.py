"""Synthetic retained native streams exercise parsing, never host qualification."""
import hashlib
from dataclasses import asdict,replace
from datetime import UTC,datetime
from decimal import Decimal
from uuid import NAMESPACE_URL,uuid5

import pytest

from packages.engine_contracts import EngineCommandEnvelope,EventAttribute,canonical_json_bytes,payload_digest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.nautilus_runtime_contracts import event_message_id,semantic_digest
from packages.nautilus_runtime_contracts.result import _decode_event,validate_p1_result
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from tests.jobs.test_worker_lifecycle import outcome,safety_evidence
from services.job_worker.process_runner import _safety_metadata
from tests.nautilus_runtime_contracts.test_result import _batch
from tests.p3.test_job_api import _alpha_request


@pytest.fixture
def native_proof(tmp_path):
    return _native_proof(tmp_path,_alpha_request().payload.expected_source)


def _native_proof(tmp_path,source):
    from services.job_worker.p3_fixture_native import build_native_fixture_inputs
    root=tmp_path/'store';root.mkdir(mode=0o700)
    inputs=tmp_path/'inputs';inputs.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    command,_=build_native_fixture_inputs(inputs)
    context=dict(source=source,job_id='job_'+'1'*32,attempt_id='attempt_'+'2'*32,
        worker_id='synthetic-fixture-worker',expected_request_digest=payload_digest(command),store=store)
    records=[]
    now=datetime.now(UTC)
    for replica in (1,2,3):
        def identifier(purpose):
            return uuid5(NAMESPACE_URL,f"p3-fixture:{context['job_id']}:{context['attempt_id']}:{replica}:{purpose}")
        request=EngineCommandEnvelope(message_id=identifier('message'),correlation_id=identifier('correlation'),
            causation_id=identifier('causation'),engine_run_id=identifier('run'),stream_sequence=1,
            event_time=now,initialization_time=now,schema_version='1.0.0',producer_identity=context['worker_id'],
            source_commit=source.commit_sha,config_digest=payload_digest({name:getattr(command,name) for name in (
                'engine_configuration','instrument_catalog','strategy_configuration')}),payload_digest=payload_digest(command),payload=command)
        _,original=_batch()
        decoded=tuple(_decode_event(event) for event in original)
        decoded=tuple(event.model_copy(update=dict(closure_digest=P1_REAL_BACKTEST_POLICY.closure_sha256,
            **(dict(config_digest=request.config_digest,catalog_digest=command.instrument_catalog.sha256,
                data_digest=command.market_data.sha256) if event.event_type=='RunStarted' else {})))
            if event.event_type in {'RunStarted','RunCompleted'} else event for event in decoded)
        decoded=(*decoded[:-1],decoded[-1].model_copy(update={'semantic_digest':semantic_digest(decoded)}))
        events=[]
        for original_event,event in zip(original,decoded,strict=True):
            fields=event.model_dump(mode='json')
            attributes=tuple(EventAttribute(name=name,value=canonical_json_bytes(value).decode() if isinstance(value,list) else value)
                for name,value in fields.items() if name!='event_type' and value is not None)
            payload=original_event.payload.model_copy(update={'attributes':attributes})
            events.append(original_event.model_copy(update=dict(message_id=event_message_id(request.message_id,event),
                correlation_id=request.correlation_id,causation_id=request.causation_id,engine_run_id=request.engine_run_id,
                event_time=now,initialization_time=now,producer_identity=request.producer_identity,source_commit=source.commit_sha,
                config_digest=request.config_digest,payload=payload,payload_digest=payload_digest(payload))))
        raw=b''.join(canonical_json_bytes(event)+b'\n' for event in events)
        result=validate_p1_result(request,tuple(events),raw=raw,expected_closure_digest=P1_REAL_BACKTEST_POLICY.closure_sha256)
        stdout=store.put_bytes(raw,media_type='application/jsonl')
        stderr=store.put_bytes(b'',media_type='application/octet-stream')
        observed=outcome()
        def stream(name,ref):
            return replace(observed.stdout,artifact_type=name,relative_ref=f"{context['job_id']}/{context['attempt_id']}/{name}.log",
                sha256=ref.content_sha256,size_bytes=ref.size_bytes,media_type='application/octet-stream',truncated=False)
        observed=replace(observed,identity=replace(observed.identity,pid=100+replica,process_group=100+replica),result_validator_id=P1_REAL_BACKTEST_POLICY.result_validator_id,
            stdout=stream('stdout',stdout),stderr=stream('stderr',stderr),lineage=replace(observed.lineage,
                safety_initial=_safety_metadata(safety_evidence('4'*64)),
                safety_final=_safety_metadata(safety_evidence('4'*64)),command=dict(
                engine_closure_sha256=P1_REAL_BACKTEST_POLICY.closure_sha256,
                os_sandbox_profile_sha256=P1_REAL_BACKTEST_POLICY.sandbox_profile_sha256,
                engine_request_sha256=payload_digest(request))))
        records.append(dict(replica=replica,request=request,outcome=asdict(observed),
            result={key:str(value) if isinstance(value,Decimal) else value for key,value in asdict(result).items() if key!='events'},
            stdout_ref=stdout,stderr_ref=stderr))
    raw=canonical_json_bytes(dict(schema_version='p3-native-fixture-proof-v1',source=source,runs=records))
    ref=store.put_bytes(raw,media_type='application/json')
    return ref,context


def test_native_receipt_readback_reconstructs_all_three_retained_results(native_proof):
    from services.job_worker.p3_qualification_readback import validate_native_fixture_proof
    ref,context=native_proof
    before={path.name:path.read_bytes() for path in context['store']._root.iterdir()}
    validated=validate_native_fixture_proof(ref,**context)
    assert len(validated)==3
    assert len({result.semantic_sha256 for _,result in validated})==1
    assert all(result.product_closure_sha256==P1_REAL_BACKTEST_POLICY.closure_sha256 for _,result in validated)
    assert before=={path.name:path.read_bytes() for path in context['store']._root.iterdir()}


@pytest.mark.parametrize('fault',['source','count','replica','request_id','request_scope','request_hash',
    'exit','cleanup','closure','request_lineage','sandbox','live','stream_sha','stream_path','stream_truncated',
    'missing_stdout','result','result_bool','divergence','duplicate_run'])
def test_native_readback_rejects_rehashed_false_proof(native_proof,fault):
    import json
    from services.job_worker.p3_qualification_readback import validate_native_fixture_proof
    ref,context=native_proof
    store=context['store']
    value=json.loads(store.read_bytes(ref))
    run=value['runs'][0]
    if fault=='source': value['source']['commit_sha']='f'*40
    elif fault=='count': value['runs'].pop()
    elif fault=='replica': run['replica']=True
    elif fault=='request_id': run['request']['engine_run_id']=run['request']['message_id']
    elif fault=='request_scope': run['request']['producer_identity']='other-worker'
    elif fault=='request_hash': run['request']['payload_digest']='f'*64
    elif fault=='exit': run['outcome']['exit_code']=1
    elif fault=='cleanup': run['outcome']['termination_reason']='PROCESS_GROUP_CLEANUP_UNPROVEN'
    elif fault=='closure': run['outcome']['lineage']['command']['engine_closure_sha256']='f'*64
    elif fault=='request_lineage': run['outcome']['lineage']['command']['engine_request_sha256']='f'*64
    elif fault=='sandbox': run['outcome']['lineage']['command']['os_sandbox_profile_sha256']='f'*64
    elif fault=='live': run['outcome']['lineage']['safety_final']['live_trading_approved']=True
    elif fault=='stream_sha': run['outcome']['stdout']['sha256']='f'*64
    elif fault=='stream_path': run['outcome']['stdout']['relative_ref']='other/attempt/stdout.log'
    elif fault=='stream_truncated': run['outcome']['stdout']['truncated']=True
    elif fault=='missing_stdout': run['stdout_ref'].update(content_sha256='f'*64,locator='f'*64+'.blob')
    elif fault=='result': run['result']['product_closure_sha256']='f'*64
    elif fault=='result_bool': run['result']['target_count']=True
    elif fault=='divergence': value['runs'][1]['result']['semantic_sha256']='f'*64
    else: value['runs'][1]=run
    forged=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_native_fixture_proof(forged,**context)


@pytest.mark.parametrize('fault',['timestamp','digest','window','reversed'])
def test_native_readback_rejects_incomplete_historical_safety(native_proof,fault):
    import json
    from services.job_worker.p3_qualification_readback import validate_native_fixture_proof
    ref,context=native_proof
    store=context['store'];value=json.loads(store.read_bytes(ref))
    safety=value['runs'][0]['outcome']['lineage']
    if fault=='timestamp': safety['safety_initial'].pop('generated_at')
    elif fault=='digest': safety['safety_initial']['snapshot_sha256']='not-a-digest'
    elif fault=='window': safety['safety_initial']['expires_at']=safety['safety_initial']['generated_at']
    else:
        from datetime import timedelta
        for key in ('generated_at','expires_at'):
            value_at=datetime.fromisoformat(safety['safety_initial'][key])+timedelta(seconds=1)
            safety['safety_initial'][key]=value_at.isoformat()
    forged=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_native_fixture_proof(forged,**context)


@pytest.mark.parametrize('fault',['job_id','attempt_id','same_process','mixed_backend','offset'])
def test_native_readback_requires_exact_launch_identity(native_proof,fault):
    import json
    from datetime import timedelta,timezone
    from services.job_worker.p3_qualification_readback import validate_native_fixture_proof
    ref,context=native_proof
    value=json.loads(context['store'].read_bytes(ref))
    if fault in {'job_id','attempt_id'}:
        context[fault]='generic-identifier'
        with pytest.raises(ValueError,match='job binding'):
            validate_native_fixture_proof(ref,**context)
        return
    if fault=='same_process': value['runs'][1]['outcome']['identity']=value['runs'][0]['outcome']['identity']
    elif fault=='mixed_backend': value['runs'][1]['outcome']['backend_revision']='f'*40
    else:
        safety=value['runs'][0]['outcome']['lineage']['safety_initial']
        for key in ('generated_at','expires_at'):
            safety[key]=datetime.fromisoformat(safety[key]).astimezone(timezone(timedelta(hours=1))).isoformat()
    ref=context['store'].put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_native_fixture_proof(ref,**context)


def _seal(store,model,**values):
    values.pop('digest',None)
    values={key:value.isoformat().replace('+00:00','Z') if isinstance(value,datetime) else value for key,value in values.items()}
    values['digest']=hashlib.sha256(canonical_json_bytes(values)).hexdigest()
    value=model.model_validate_json(canonical_json_bytes(values))
    return store.put_bytes(canonical_json_bytes(value),media_type='application/json')


@pytest.fixture
def integration_proof(native_proof):
    return _integration_proof(native_proof)


def _integration_proof(native_proof):
    import json
    from datetime import timedelta
    from services.job_worker.p3_integration import FixturePlan
    from services.job_worker.p3_fixture_sql import REQUIRED_SQL_CHECKS,ROOT
    from packages.alpha_lifecycle.authority import _FixtureAuthorization
    from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt,ReviewApproval
    from packages.job_contracts import AlphaCampaignPayload
    native_ref,context=native_proof
    source=context['source'];store=context['store']
    now=datetime.fromisoformat(json.loads(store.read_bytes(native_ref))['runs'][0]['request']['event_time'].replace('Z','+00:00'))
    safe=dict(broker=False,live=False,network=False,production=False)
    plan_ref=_seal(store,FixturePlan,schema_version='p3-integration-fixture-plan-v1',source=source,
        purpose='SYNTHETIC_ONLY',native_request_digest=context['expected_request_digest'],
        policy_set_sha256=hashlib.sha256((ROOT/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()).hexdigest(),
        sql_revision='0026_p3_holdout_disclosure',cleanup_policy='OWNED_ROOTS_ONLY')
    review_ref=_seal(store,ReviewApproval,schema_version='p3-review-approval-v1',source=source,
        subject_digests=[json.loads(store.read_bytes(plan_ref))['digest']],operator_identity='synthetic-operator',
        reviewer_identity='synthetic-reviewer',review_execution_id='synthetic-review',verdict='APPROVED',
        issued_at=now-timedelta(minutes=1),expires_at=now+timedelta(hours=1),
        evidence_ref=store.put_bytes(b'synthetic test review only',media_type='text/plain'),authority=safe)
    authorization_ref=_seal(store,_FixtureAuthorization,schema_version='p3-fixture-authorization-v1',
        fixture_plan_ref=plan_ref,review_ref=review_ref,operation='PARITY',issued_at=now-timedelta(minutes=1),
        expires_at=now+timedelta(hours=1),nonce='00000000-0000-4000-8000-000000000001',
        issuer_workflow='p3-authority.yml',issuer_run_id=123,issuer_attempt=1,authority=safe)
    payload=AlphaCampaignPayload.model_validate_json(canonical_json_bytes(dict(schema_version='p3-alpha-campaign-payload-v1',
        operation='PARITY',manifest_ref=plan_ref,authorization_ref=authorization_ref,expected_source=source,
        logical_trial_id='p3-integration-fixture-v1')))
    sql_cleanup=dict(cluster_id='sql-'+hashlib.sha256(f"{context['job_id']}/{context['attempt_id']}".encode()).hexdigest()[:32],
        root_absent=True,server_stopped=True)
    sql_ref=store.put_bytes(canonical_json_bytes(dict(source=source,checks=sorted(REQUIRED_SQL_CHECKS|{
        'UPGRADE_0004_durable_research_jobs_PASS','UPGRADE_0019_p2_security_master_PASS',
        'UPGRADE_0020_p3_alpha_campaign_authority_PASS','API_ENQUEUE_PASS','WORKER_CLAIM_PASS',
        'WORKER_START_PASS','WORKER_BUSY_HEARTBEAT_PASS','WORKER_DURABLE_RESULT_PASS'
        })+['RESEARCH_PUBLICATION_REQUIRES_COMPLETE_DECISION_BATCH_PASS'],
        sql_revision='0026_p3_holdout_disclosure',postgres_binary_sha256='d'*64,
        started_at=(now+timedelta(minutes=1)).isoformat(),finished_at=(now+timedelta(minutes=2)).isoformat(),cleanup=sql_cleanup)),media_type='application/json')
    cleanup_ref=store.put_bytes(canonical_json_bytes(dict(schema_version='p3-fixture-cleanup-v1',source=source,
        native_root_absent=True,native_process_cleanup=True,sql_cleanup=sql_cleanup)),media_type='application/json')
    receipt_ref=_seal(store,IntegrationReceipt,schema_version='p3-integration-qualified-v1',source=source,
        sql_proof_ref=sql_ref,native_fixture_proof_ref=native_ref,cleanup_proof_ref=cleanup_ref,
        workflow_run_id=123,workflow_attempt=1,status='PASS',authority=safe)
    return receipt_ref,{**{key:value for key,value in context.items() if key!='expected_request_digest'},
        'fixture_payload':payload,'postgres_binary_sha256':'d'*64}


def test_integration_readback_binds_retained_sql_native_cleanup_and_historical_review(integration_proof):
    from services.job_worker.p3_qualification_readback import validate_integration_receipt
    ref,context=integration_proof
    before={path.name:path.read_bytes() for path in context['store']._root.iterdir()}
    receipt=validate_integration_receipt(ref,**context)
    assert receipt.workflow_run_id==123 and receipt.status=='PASS'
    assert before=={path.name:path.read_bytes() for path in context['store']._root.iterdir()}


@pytest.mark.parametrize('fault',['receipt_source','workflow_run','workflow_attempt','plan_source','policy',
    'review_source','review_rejected','review_self','review_subject','review_early','review_expired','review_evidence',
    'auth_plan','auth_workflow','auth_early','auth_expired','auth_predates_review','auth_outlives_review','sql_source','sql_revision','sql_binary','sql_checks',
    'sql_duplicate','sql_unknown','sql_missing_extra','sql_time_reversed','sql_time_offset','sql_before_native','sql_expired','sql_cleanup_root','sql_cleanup_server',
    'sql_cleanup_attempt','cleanup_source','cleanup_native_root','cleanup_process','cleanup_sql'])
def test_integration_readback_rejects_rehashed_cross_artifact_drift(integration_proof,fault):
    import json
    from datetime import timedelta,timezone
    from packages.alpha_lifecycle.authority import _FixtureAuthorization
    from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt,ReviewApproval
    from services.job_worker.p3_integration import FixturePlan
    from services.job_worker.p3_qualification_readback import validate_integration_receipt
    ref,context=integration_proof;store=context['store'];payload=context['fixture_payload']
    def load(reference): return json.loads(store.read_bytes(reference))
    receipt=load(ref);plan=load(payload.manifest_ref);authorization=load(payload.authorization_ref)
    review=load(authorization['review_ref']);sql=load(receipt['sql_proof_ref']);cleanup=load(receipt['cleanup_proof_ref'])
    if fault=='receipt_source': receipt['source']['commit_sha']='f'*40
    elif fault=='workflow_run': receipt['workflow_run_id']+=1
    elif fault=='workflow_attempt': receipt['workflow_attempt']=0
    elif fault=='plan_source': plan['source']['commit_sha']='f'*40
    elif fault=='policy': plan['policy_set_sha256']='f'*64
    elif fault=='review_source': review['source']['commit_sha']='f'*40
    elif fault=='review_rejected': review['verdict']='REJECTED'
    elif fault=='review_self': review['reviewer_identity']=review['operator_identity']
    elif fault=='review_subject': review['subject_digests']=['f'*64]
    elif fault=='review_early': review['issued_at']=datetime.fromisoformat(sql['finished_at']).isoformat().replace('+00:00','Z')
    elif fault=='review_expired': review['expires_at']=sql['started_at'].replace('+00:00','Z')
    elif fault=='review_evidence': review['evidence_ref'].update(content_sha256='f'*64,locator='f'*64+'.blob')
    elif fault=='auth_workflow': authorization['issuer_workflow']='other.yml'
    elif fault=='auth_early': authorization['issued_at']=sql['finished_at'].replace('+00:00','Z')
    elif fault=='auth_expired': authorization['expires_at']=sql['started_at'].replace('+00:00','Z')
    elif fault=='auth_predates_review': review['issued_at']=(datetime.fromisoformat(review['issued_at'].replace('Z','+00:00'))+timedelta(seconds=30)).isoformat().replace('+00:00','Z')
    elif fault=='auth_outlives_review': review['expires_at']=(datetime.fromisoformat(review['expires_at'].replace('Z','+00:00'))-timedelta(minutes=1)).isoformat().replace('+00:00','Z')
    elif fault=='sql_source': sql['source']['commit_sha']='f'*40
    elif fault=='sql_revision': sql['sql_revision']='0021_p3_official_operations'
    elif fault=='sql_binary': sql['postgres_binary_sha256']='f'*64
    elif fault=='sql_checks': sql['checks'].remove('OFFICIAL_PUBLICATION_ATOMIC_CUSTODY_AND_RECEIPT_PASS')
    elif fault=='sql_duplicate': sql['checks'].append('MIGRATION_CHAIN_PASS')
    elif fault=='sql_unknown': sql['checks'].append('UNREVIEWED_EXTRA_PASS')
    elif fault=='sql_missing_extra': sql['checks'].remove('API_ENQUEUE_PASS')
    elif fault=='sql_time_reversed': sql['finished_at']=(datetime.fromisoformat(sql['started_at'])-timedelta(seconds=1)).isoformat()
    elif fault=='sql_time_offset': sql['started_at']=datetime.fromisoformat(sql['started_at']).astimezone(timezone(timedelta(hours=1))).isoformat()
    elif fault=='sql_before_native': sql['started_at']=(datetime.fromisoformat(sql['started_at'])-timedelta(hours=1)).isoformat()
    elif fault=='sql_expired': sql['finished_at']=(datetime.fromisoformat(sql['finished_at'])+timedelta(days=1)).isoformat()
    elif fault=='sql_cleanup_root': sql['cleanup']['root_absent']=False
    elif fault=='sql_cleanup_server': sql['cleanup']['server_stopped']=False
    elif fault=='sql_cleanup_attempt': sql['cleanup']['cluster_id']='sql-'+'f'*32
    elif fault=='cleanup_source': cleanup['source']['commit_sha']='f'*40
    elif fault=='cleanup_native_root': cleanup['native_root_absent']=False
    elif fault=='cleanup_process': cleanup['native_process_cleanup']=False
    elif fault=='cleanup_sql': cleanup['sql_cleanup']['root_absent']=False
    plan_ref=_seal(store,FixturePlan,**plan)
    if fault!='review_subject': review['subject_digests']=[load(plan_ref)['digest']]
    authorization['review_ref']=_seal(store,ReviewApproval,**review)
    authorization['fixture_plan_ref']=ref if fault=='auth_plan' else plan_ref
    authorization_ref=_seal(store,_FixtureAuthorization,**authorization)
    context['fixture_payload']=payload.model_copy(update=dict(manifest_ref=plan_ref,authorization_ref=authorization_ref))
    receipt['sql_proof_ref']=store.put_bytes(canonical_json_bytes(sql),media_type='application/json')
    receipt['cleanup_proof_ref']=store.put_bytes(canonical_json_bytes(cleanup),media_type='application/json')
    ref=_seal(store,IntegrationReceipt,**receipt)
    with pytest.raises(ValueError): validate_integration_receipt(ref,**context)


def test_integration_readback_rejects_a_consistently_rebound_non_fixture_command(monkeypatch,request):
    from services.job_worker import p3_fixture_native
    from services.job_worker.p3_qualification_readback import validate_integration_receipt
    original=p3_fixture_native.build_native_fixture_inputs
    def altered(root):
        command,bindings=original(root)
        return command.model_copy(update={'engine_configuration':command.engine_configuration.model_copy(update={'sha256':'e'*64})}),bindings
    monkeypatch.setattr(p3_fixture_native,'build_native_fixture_inputs',altered)
    ref,context=request.getfixturevalue('integration_proof')
    with pytest.raises(ValueError,match='fixed fixture'): validate_integration_receipt(ref,**context)


@pytest.mark.parametrize('field',['snapshot_sha256','command_fingerprint','capability_fingerprint','backend_revision'])
def test_native_readback_keeps_self_reported_identity_informational(native_proof,field):
    """Only protected producer/job provenance authenticates these observations."""
    import json
    from services.job_worker.p3_qualification_readback import validate_native_fixture_proof
    ref,context=native_proof;store=context['store'];value=json.loads(store.read_bytes(ref))
    for run in value['runs']:
        observed=run['outcome']
        if field=='snapshot_sha256':
            for name in ('safety_initial','safety_final'): observed['lineage'][name][field]='f'*64
        elif field=='command_fingerprint': observed['identity'][field]='f'*64
        else: observed[field]='f'*(40 if field=='backend_revision' else 64)
    ref=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    assert len(validate_native_fixture_proof(ref,**context))==3


@pytest.mark.parametrize('fault',['predates','outlives'])
def test_fixture_execution_rejects_authorization_outside_its_review(integration_proof,fault,tmp_path,monkeypatch):
    import json
    from datetime import timedelta
    from types import SimpleNamespace
    from dataclasses import replace
    from packages.alpha_lifecycle.authority import _FixtureAuthorization,AuthorityHeld
    from packages.alpha_lifecycle.contracts.authority import ReviewApproval
    from packages.job_contracts import JobType
    from services.job_worker import p3_integration
    from tests.jobs.test_worker_lifecycle import claim
    _,context=integration_proof;store=context['store'];payload=context['fixture_payload'];source=context['source']
    authorization=json.loads(store.read_bytes(payload.authorization_ref))
    review=json.loads(store.read_bytes(authorization['review_ref']))
    key,delta=('issued_at',timedelta(seconds=30)) if fault=='predates' else ('expires_at',-timedelta(minutes=1))
    review[key]=(datetime.fromisoformat(review[key].replace('Z','+00:00'))+delta).isoformat().replace('+00:00','Z')
    review_ref=_seal(store,ReviewApproval,**review)
    authorization['review_ref']=review_ref
    authorization_ref=_seal(store,_FixtureAuthorization,**authorization)
    payload=payload.model_copy(update={'authorization_ref':authorization_ref})
    job=replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,payload=payload)
    # Only authority time ordering is exercised; source/root approval are explicit doubles.
    monkeypatch.setattr(p3_integration.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=b''))
    monkeypatch.setattr(p3_integration,'canonical_source_identity',lambda _:source.model_dump(mode='json'))
    monkeypatch.setattr(p3_integration,'_read_review',lambda *a:ReviewApproval.model_validate_json(store.read_bytes(review_ref)))
    for name,value in dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='nam176hermes/Trading-Agent',GITHUB_REF='refs/heads/main',
        GITHUB_SHA=source.commit_sha,GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_REF_PROTECTED='true',
        GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main').items(): monkeypatch.setenv(name,value)
    executor=p3_integration.P3IntegrationFixtureExecutor(store=store,closure_config=object(),
        private_root=tmp_path,review_file=tmp_path/'synthetic-review')
    with pytest.raises(AuthorityHeld,match='approval'): executor._attest(job)


@pytest.fixture
def integration_job(integration_proof):
    import json
    from datetime import timedelta
    from apps.job_api.contracts import JobDetailEnvelope
    from packages.job_contracts import payload_fingerprint
    receipt_ref,context=integration_proof;store=context['store'];payload=context['fixture_payload']
    receipt=json.loads(store.read_bytes(receipt_ref))
    sql=json.loads(store.read_bytes(receipt['sql_proof_ref']))
    authorization=json.loads(store.read_bytes(payload.authorization_ref))
    requested=datetime.fromisoformat(authorization['issued_at'].replace('Z','+00:00'))+timedelta(seconds=10)
    finished=datetime.fromisoformat(sql['finished_at'])+timedelta(seconds=1)
    detail=dict(job=dict(job_id=context['job_id'],job_type='ALPHA_CAMPAIGN',state='SUCCEEDED',payload=payload,
        payload_fingerprint=payload_fingerprint(payload),actor=dict(actor_type='OPERATOR',actor_id='synthetic-operator'),
        priority=0,requested_at=requested.isoformat(),updated_at=finished.isoformat(),attempt_count=1,
        reason_code='RESULT_VALIDATED',result_hash=receipt_ref.content_sha256),
        attempts=[dict(attempt_id=context['attempt_id'],attempt_number=1,worker_id=context['worker_id'],
            claimed_at=(requested+timedelta(seconds=1)).isoformat(),started_at=(requested+timedelta(seconds=2)).isoformat(),
            finished_at=finished.isoformat(),exit_code=None,termination_reason=None,artifact_count=1)],
        events=[dict(event_id=f'event_{index}',sequence=index,from_state=before,to_state=after,
            reason_code=reason,actor=dict(actor_type=actor_type,actor_id=actor_id),trace_id=f'trace-{index}',created_at=when.isoformat())
            for index,(before,after,reason,actor_type,actor_id,when) in enumerate((
                (None,'QUEUED','ENQUEUED','OPERATOR','synthetic-operator',requested),
                ('QUEUED','CLAIMED','CLAIMED','WORKER',context['worker_id'],requested+timedelta(seconds=1)),
                ('CLAIMED','RUNNING','STARTED','WORKER',context['worker_id'],requested+timedelta(seconds=2)),
                ('RUNNING','SUCCEEDED','RESULT_VALIDATED','WORKER',context['worker_id'],finished)),1)],
        artifacts=[dict(artifact_id='artifact_result',attempt_id=context['attempt_id'],artifact_type='result',
            validator_id='p3-integration-qualified-v1',sha256=receipt_ref.content_sha256,size_bytes=receipt_ref.size_bytes,
            created_at=finished.isoformat())])
    envelope=JobDetailEnvelope.model_validate_json(canonical_json_bytes(dict(schema_version='1.0.0',trace_id='synthetic-detail',
        generated_at=(finished+timedelta(seconds=1)).isoformat(),data=detail)))
    detail_ref=store.put_bytes(canonical_json_bytes(envelope),media_type='application/json')
    return receipt_ref,dict(job_detail_ref=detail_ref,source=context['source'],
        postgres_binary_sha256=context['postgres_binary_sha256'],store=store)


def test_fixture_job_readback_binds_the_canonical_parent_result(integration_job):
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job
    before={path.name:path.read_bytes() for path in context['store']._root.iterdir()}
    assert validate_integration_job_result(ref,**context).workflow_run_id==123
    assert before=={path.name:path.read_bytes() for path in context['store']._root.iterdir()}


def test_fixture_job_readback_accepts_transaction_time_before_final_statement(integration_job):
    import json
    from datetime import timedelta
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job;store=context['store']
    value=json.loads(store.read_bytes(context['job_detail_ref']))
    data=value['data'];finished=datetime.fromisoformat(data['attempts'][-1]['finished_at'])
    # job_events/job_artifacts default to transaction_timestamp(); finalization
    # writes statement_timestamp(). Preserve those SQL semantics rather than equalizing them.
    transaction_time=(finished-timedelta(milliseconds=1)).isoformat().replace('+00:00','Z')
    data['events'][-1]['created_at']=transaction_time
    data['artifacts'][-1]['created_at']=transaction_time
    context['job_detail_ref']=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    assert validate_integration_job_result(ref,**context).status=='PASS'


@pytest.mark.parametrize('fault',['state','result_hash','fingerprint','operator','priority','reason','attempt_count',
    'attempt_sequence','worker','unfinished','parent_exit','termination','missing_start','artifact_missing',
    'artifact_extra','artifact_count','artifact_attempt','artifact_hash','artifact_size','artifact_validator','artifact_type',
    'event_missing','event_sequence','event_duplicate','event_chain','terminal_actor','terminal_reason','event_before_sql',
    'finished_after_authority','naive_time'])
def test_fixture_job_readback_rejects_rehashed_observation_drift(integration_job,fault):
    import json
    from datetime import timedelta
    from apps.job_api.contracts import JobDetailEnvelope
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job;store=context['store'];value=json.loads(store.read_bytes(context['job_detail_ref']))
    data=value['data'];job=data['job'];attempt=data['attempts'][-1];artifact=data['artifacts'][-1];event=data['events'][-1]
    if fault=='state': job['state']='RUNNING'
    elif fault=='result_hash': job['result_hash']='f'*64
    elif fault=='fingerprint': job['payload_fingerprint']='f'*64
    elif fault=='operator': job['actor']['actor_id']='other-operator'
    elif fault=='priority': job['priority']=1
    elif fault=='reason': job['reason_code']='OTHER_RESULT'
    elif fault=='attempt_count': job['attempt_count']=2
    elif fault=='attempt_sequence': attempt['attempt_number']=2
    elif fault=='worker': attempt['worker_id']='other-worker'
    elif fault=='unfinished': attempt['finished_at']=None
    elif fault=='parent_exit': attempt['exit_code']=0
    elif fault=='termination': attempt['termination_reason']='PROCESS_FAILED'
    elif fault=='missing_start': attempt['started_at']=None
    elif fault=='artifact_missing': data['artifacts']=[];attempt['artifact_count']=0
    elif fault=='artifact_extra': data['artifacts'].append({**artifact,'artifact_id':'artifact_other'});attempt['artifact_count']=2
    elif fault=='artifact_count': attempt['artifact_count']=2
    elif fault=='artifact_attempt': artifact['attempt_id']='attempt_'+'f'*32
    elif fault=='artifact_hash': artifact['sha256']='f'*64
    elif fault=='artifact_size': artifact['size_bytes']+=1
    elif fault=='artifact_validator': artifact['validator_id']='other-validator'
    elif fault=='artifact_type': artifact['artifact_type']='RESULT'
    elif fault=='event_missing': data['events']=[]
    elif fault=='event_sequence': event['sequence']+=1
    elif fault=='event_duplicate': event['event_id']=data['events'][0]['event_id']
    elif fault=='event_chain': event['from_state']='CLAIMED'
    elif fault=='terminal_actor': event['actor']['actor_id']='other-worker'
    elif fault=='terminal_reason': event['reason_code']='OTHER_RESULT'
    elif fault=='event_before_sql': event['created_at']=(datetime.fromisoformat(event['created_at'])-timedelta(minutes=1)).isoformat().replace('+00:00','Z')
    elif fault=='finished_after_authority':
        late=(datetime.fromisoformat(attempt['finished_at'])+timedelta(hours=2)).isoformat().replace('+00:00','Z')
        attempt['finished_at']=job['updated_at']=event['created_at']=value['generated_at']=late
    else: attempt['claimed_at']=datetime.fromisoformat(attempt['claimed_at']).replace(tzinfo=None).isoformat()
    # All adversarial observations still pass the public wire contract.
    envelope=JobDetailEnvelope.model_validate_json(canonical_json_bytes(value))
    context['job_detail_ref']=store.put_bytes(canonical_json_bytes(envelope),media_type='application/json')
    with pytest.raises(ValueError): validate_integration_job_result(ref,**context)


@pytest.mark.parametrize('fault',['actor','reason'])
def test_fixture_job_readback_binds_the_enqueue_event_to_the_operator(integration_job,fault):
    import json
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job;store=context['store'];value=json.loads(store.read_bytes(context['job_detail_ref']))
    first=value['data']['events'][0]
    if fault=='actor': first['actor']['actor_id']='other-operator'
    else: first['reason_code']='OTHER_ENQUEUE'
    context['job_detail_ref']=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_integration_job_result(ref,**context)


def test_fixture_job_readback_rejects_a_second_attempt_in_the_single_attempt_sql_lane(integration_job):
    import json
    from datetime import timedelta
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job;store=context['store'];value=json.loads(store.read_bytes(context['job_detail_ref']))
    data=value['data'];current=data['attempts'][0]
    earlier=(datetime.fromisoformat(current['claimed_at'])-timedelta(milliseconds=1)).isoformat().replace('+00:00','Z')
    previous={**current,'attempt_id':'attempt_'+'f'*32,'started_at':None,'claimed_at':earlier,'finished_at':earlier,'artifact_count':0}
    current['attempt_number']=2
    data['attempts']=[previous,current];data['job']['attempt_count']=2
    context['job_detail_ref']=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_integration_job_result(ref,**context)


@pytest.mark.parametrize('fault',['claim_actor','start_reason','extra_event','artifact_before_proof'])
def test_fixture_job_readback_requires_the_exact_single_attempt_event_path(integration_job,fault):
    import json
    from datetime import timedelta
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job;store=context['store'];value=json.loads(store.read_bytes(context['job_detail_ref']))
    data=value['data']
    if fault=='claim_actor': data['events'][1]['actor']['actor_id']='other-worker'
    elif fault=='start_reason': data['events'][2]['reason_code']='PROCESS_STARTED'
    elif fault=='extra_event':
        event={**data['events'][2],'event_id':'event_extra','from_state':'RUNNING','sequence':4}
        data['events'].insert(3,event);data['events'][-1]['sequence']=5
    else:
        artifact=data['artifacts'][0]
        artifact['created_at']=(datetime.fromisoformat(artifact['created_at'])-timedelta(minutes=1)).isoformat().replace('+00:00','Z')
    context['job_detail_ref']=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_integration_job_result(ref,**context)


def test_fixture_job_readback_rejects_reversed_serial_event_times(integration_job):
    import json
    from datetime import timedelta
    from services.job_worker.p3_qualification_readback import validate_integration_job_result
    ref,context=integration_job;store=context['store'];value=json.loads(store.read_bytes(context['job_detail_ref']))
    events=value['data']['events']
    events[1]['created_at']=(datetime.fromisoformat(events[2]['created_at'])+timedelta(milliseconds=1)).isoformat().replace('+00:00','Z')
    context['job_detail_ref']=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    with pytest.raises(ValueError): validate_integration_job_result(ref,**context)
