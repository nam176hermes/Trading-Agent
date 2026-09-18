"""Real disposable local identity checks; synthetic metadata, no plaintext release."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, create_engine, text
from typing_extensions import override

from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import JobState
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .p3_holdout_fixture import _inputs, _seed, CONSUME
from .p3_operation_fixture import _rejected, _sealed
from .recovery import ProcessIdentity, ProcProcessInspector

REVISION='0032_p3_enqueue_freshness'


def check_local_identity(sock: Path,name: str,source) -> dict[str,object]:
    from services.job_store.p3_catalog import SESSION_CATALOG_SQL, SESSION_CATALOG_SHA256
    engine=create_engine(URL.create('postgresql+psycopg',username='trading_owner',database=name,query={'host':str(sock)}))
    try:
        with engine.begin() as connection:
            config=Config(str(Path(__file__).resolve().parents[2]/'alembic.ini'))
            config.attributes['connection']=connection
            with _rejected(RuntimeError,match='parent authority catalog differs'):
                with connection.begin_nested():
                    connection.execute(text('SET LOCAL ROLE trading_p3_owner'))
                    connection.execute(text('GRANT UPDATE ON public.p3_alpha_heads TO trading_job_worker'))
                    connection.execute(text('RESET ROLE'))
                    command.upgrade(config,REVISION)
            command.upgrade(config,'0031_p3_local_identity')
            with _rejected(RuntimeError,match='parent authority catalog differs'):
                with connection.begin_nested():
                    connection.execute(text('SET LOCAL ROLE trading_p3_owner'))
                    connection.execute(text('GRANT UPDATE ON public.p3_alpha_heads TO trading_job_worker'))
                    connection.execute(text('RESET ROLE'))
                    command.upgrade(config,REVISION)
            command.upgrade(config,REVISION)
            connection.execute(text('SET LOCAL search_path=pg_catalog'))
            catalog=connection.execute(text(SESSION_CATALOG_SQL)).scalar()
            print('LOCAL_SESSION_CATALOG_SHA256='+str(catalog),flush=True)
            assert catalog==SESSION_CATALOG_SHA256
    finally:
        engine.dispose()
    enqueue_checks=check_enqueue_freshness(sock,name,source)
    # Existing terminal fixture committed a real SQL exit. Read it after upgrade,
    # then prove stale heads and non-worker roles cannot export that commitment.
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        row=owner.execute("SELECT job_id FROM public.jobs WHERE payload->>'operation'='PHASE_EXIT' AND state='SUCCEEDED'").fetchone()
        assert row is not None
        job=row[0]
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
        row=worker.execute('SELECT job_plane.worker_read_phase_exit(%s)',(job,)).fetchone()
        assert row is not None
        first=row[0]
        assert worker.execute('SELECT job_plane.worker_read_phase_exit(%s)',(job,)).fetchone()==(first,)
        observed=json.loads(first)
        assert observed['publication_request']['job_id']==job
        assert observed['attempt_id'] and observed['commit_result']['alpha_outcome']=='PASS'
    for role in ('trading_reader','trading_job_api','trading_p3_custodian','trading_p3_authority'):
        with psycopg.connect(host=str(sock),dbname=name,user=role) as denied:
            with _rejected(psycopg.errors.InsufficientPrivilege):
                denied.execute('SELECT job_plane.worker_read_phase_exit(%s)',(job,))
    head=observed['registry_head']
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        owner.execute('UPDATE public.p3_alpha_heads SET registry_event_sha256=%s WHERE alpha_id=%s',('0'*64,head['alpha_id']))
    try:
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
            with _rejected(psycopg.Error,match='current head unavailable'):
                worker.execute('SELECT job_plane.worker_read_phase_exit(%s)',(job,))
    finally:
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            owner.execute('UPDATE public.p3_alpha_heads SET registry_event_sha256=%s WHERE alpha_id=%s',(head['event_digest'],head['alpha_id']))
    identity=LocalRunIdentity.model_validate_json(canonical_json_bytes(dict(issuer_kind='LOCAL',
        issuer_identity='synthetic-local',run_id=str(uuid4()),attempt=1,host_id='a'*64,boot_id=str(uuid4()))))
    class SocketSettings(JobStoreSettings):
        @override
        def conninfo(self):return make_conninfo(host=str(sock),dbname=name,user=self.user)
    settings=SocketSettings('localhost',5432,name,'trading_job_worker','synthetic')
    def seed(*,local=True,epoch=None,material=None,expiry=None):
        label='local_'+uuid4().hex[:12]
        authorization,graph=_inputs(source,epoch=epoch or 'synthetic.'+label,material=material or label,expiry=expiry)
        if local:
            a=json.loads(authorization[0])
            for key in ('issuer_workflow','issuer_run_id','issuer_attempt'):a.pop(key)
            authorization=(_sealed({**a,'schema_version':'p3-local-run-authorization-v1','identity':identity}),*authorization[1:])
        job,*_=_seed(sock,name,source,label,authorization,legacy_claim=False)
        return job,graph
    def consume(claim,graph):
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as connection:
            return connection.execute(CONSUME,(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,*graph,'test:local-consume')).fetchone()
    with WorkerRepository(settings) as repository:
        repository.assert_session_runtime_identity()
        job,graph=seed()
        for field,value in (('attempt',2),('host_id','b'*64),('issuer_identity','different'),('run_id',uuid4())):
            assert repository.claim_local_holdout('local-worker',30,'test:wrong-identity',job_id=job,
                identity=identity.model_copy(update={field:value})) is None
        assert repository.claim_session_holdout('local-worker',30,'test:wrong-lane',job_id=job,workflow_run_id=1,workflow_attempt=1) is None
        sql='SELECT * FROM job_plane.worker_claim_session_holdout(%s,%s,%s,%s,%s,%s,%s,%s)'
        parameters=('attempt_local_forbidden','local-worker','x'*32,30,'test:roles','event_local_forbidden',job,canonical_json_bytes(identity).decode())
        for role in ('trading_job_api','trading_job_scheduler','trading_reader','trading_p3_authority','trading_p3_custodian'):
            with psycopg.connect(host=str(sock),dbname=name,user=role,autocommit=True) as connection:
                with _rejected(psycopg.errors.InsufficientPrivilege):connection.execute(sql,parameters)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(repository.claim_local_holdout,'local-worker',30,'test:race',job_id=job,identity=identity) for _ in range(2)]
            claims=[claim for future in futures if (claim:=future.result()) is not None]
        assert len(claims)==1
        claim=claims[0]
        args=(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token)
        assert not repository.start_attempt(*args,ProcessIdentity(71001,71001,100,'b'*64),'test:before-disclosure',
            alpha_campaign=True,session_workflow=identity)
        assert consume(claim,graph) is not None
        assert repository.start_attempt(*args,ProcessIdentity(71001,71001,100,'b'*64),'test:after-disclosure',
            alpha_campaign=True,session_workflow=identity)
        assert repository.heartbeat_control(*args,30,alpha_campaign=True,session_workflow=identity)=='CONTINUE'
        assert repository.heartbeat_control(*args,30,alpha_campaign=True,
            session_workflow=identity.model_copy(update={'attempt':2}))=='STALE'
        assert repository.finalize(*args,expected_state=JobState.RUNNING,expected_attempt_outcome='RUNNING',
            final_state=JobState.BLOCKED,reason_code='P3_AUTHORITY_HELD',trace_id='test:terminal',
            alpha_campaign=True,session_workflow=identity)
        assert repository.claim_local_holdout('local-worker',30,'test:no-retry',job_id=job,identity=identity) is None
        for local_first in (True,False):
            epoch='synthetic.cross.'+uuid4().hex;material='cross.'+uuid4().hex
            def claim_lane(local):
                job,graph=seed(local=local,epoch=epoch,material=material)
                result=(repository.claim_local_holdout('local-worker',30,'test:cross',job_id=job,identity=identity) if local else
                    repository.claim_session_holdout('local-worker',30,'test:cross',job_id=job,workflow_run_id=1,workflow_attempt=1))
                assert result is not None
                return result,graph
            first,first_graph=claim_lane(local_first)
            assert consume(first,first_graph) is not None
            second,second_graph=claim_lane(not local_first)
            with _rejected(psycopg.errors.UniqueViolation):consume(second,second_graph)
        recovery_job,_=seed()
        recovery_claim=repository.claim_local_holdout('local-worker',30,'test:recovery',job_id=recovery_job,identity=identity)
        assert recovery_claim is not None
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(recovery_job,))
            owner.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE attempt_id=%s",(recovery_claim.attempt_id,))
        recovered=repository.recover_expired_leases(ProcProcessInspector(),alpha_campaign=True,fixture_only=False,
            job_id=recovery_job,session_workflow=identity,trace_id='test:recover-local')
        assert len(recovered)==1 and recovered[0][1]!='LEASE_RECOVERY_STALE'
        assert repository.claim_local_holdout('local-worker',30,'test:no-recovery-retry',job_id=recovery_job,identity=identity) is None
        expired,_=seed(expiry=datetime.now(UTC)+timedelta(seconds=1))
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
            observer.execute('SELECT pg_sleep(1.1)')
        assert repository.claim_local_holdout('local-worker',30,'test:expired',job_id=expired,identity=identity) is None
    return dict(verdict='PASS',enqueue_freshness=enqueue_checks,catalog_sha256=catalog,concurrent_winners=1,identity_denials=5,role_denials=5,
        cross_lane_second_disclosures_rejected=2,expiry='REJECTED',start_before_disclosure='REJECTED',
        wrong_identity_heartbeat='STALE',terminal='COMMITTED',recovery='TERMINAL_NO_RETRY',retry='REJECTED',migration_parent_drift='REJECTED',phase_exit_readback='COMMITTED_CURRENT_HEAD_ONLY')


def check_enqueue_freshness(sock,name,source):
    """Real API/repository rollback; protected-file observations are synthetic."""
    from types import SimpleNamespace
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from apps.job_api.app import create_p3_session_app
    from apps.job_api.config import JobApiSettings
    from packages.alpha_lifecycle.authority import AuthorityHeld,build_alpha_campaign_payload
    from packages.alpha_lifecycle.contracts.authority import LocalRunAuthorization
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.job_contracts import EnqueueJobRequest,ActorIdentity,ActorType
    from services.job_store.repository import JobRepository
    from services.job_store.errors import JobStoreError
    from .p3_operation_fixture import _authorization
    from tests.jobs.test_job_api import isolated_job_plane_authority,TOKEN,AUTH
    class Settings(JobStoreSettings):
        @override
        def conninfo(self):return make_conninfo(host=str(sock),dbname=name,user=self.user)
    settings=Settings('localhost',5432,name,'trading_job_api','synthetic')
    auth_raw,intent_raw,review_raw=_authorization(source)
    value=json.loads(auth_raw)
    for key in ('issuer_workflow','issuer_run_id','issuer_attempt'):value.pop(key)
    value.update(schema_version='p3-local-run-authorization-v1',identity=dict(issuer_kind='LOCAL',
        issuer_identity='synthetic',run_id=str(uuid4()),attempt=1,host_id='a'*64,boot_id=str(uuid4())))
    auth_raw=_sealed(value)
    auth=LocalRunAuthorization.model_validate_json(auth_raw)
    intent=P3OperationInput.model_validate_json(intent_raw)
    payload=build_alpha_campaign_payload(auth,source,intent.workflow_operation,operation_input=intent)
    principal=ActorIdentity(actor_type=ActorType.OPERATOR,actor_id='synthetic-operator')
    request=EnqueueJobRequest.model_validate(dict(job_type='ALPHA_CAMPAIGN',payload=payload,
        idempotency_key='p3:'+intent.workflow_operation+':'+str(auth.nonce),actor=principal))
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_authority') as connection:
        connection.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)',(auth_raw,intent_raw,review_raw))
    reader='SELECT * FROM job_plane.api_read_p3_enqueue_authority(%s)'
    raw=canonical_json_bytes(payload).decode()
    for role in ('trading_job_worker','trading_p3_authority','trading_reader','trading_p3_custodian'):
        with psycopg.connect(host=str(sock),dbname=name,user=role) as connection:
            with _rejected(psycopg.errors.InsufficientPrivilege):connection.execute(reader,(raw,))
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as connection:
        assert connection.execute(reader,(raw,)).fetchone()==(auth_raw,intent_raw)
        with _rejected(psycopg.errors.InvalidParameterValue):connection.execute(reader,(raw+' ',))
    def counts():
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
            counts=[]
            for table in ('public.jobs','public.job_events','public.p3_operation_job_bindings'):
                row=observer.execute('SELECT count(*) FROM '+table).fetchone()
                assert row is not None
                counts.append(row[0])
            return tuple(counts)
    admitted=SimpleNamespace(authorization=auth,operation_input=intent)
    with JobRepository(settings) as repository:
        for fault in ('revoked','expired','after_insert','intent','changed'):
            before=counts();reads=[]
            def current(*a,**kw):
                reads.append(1)
                if fault in {'revoked','expired'} or (fault=='after_insert' and len(reads)>1):
                    raise AuthorityHeld('synthetic revoked')
                if fault=='intent':return SimpleNamespace(authorization=auth,operation_input=None)
                if fault=='changed' and len(reads)>1:return object()
                return admitted
            with patch('services.job_store.p3_enqueue.validate_local_operation_authority',current):
                client=TestClient(create_p3_session_app(JobApiSettings(bearer_token=TOKEN,principal=principal),
                    repository,isolated_job_plane_authority()))
                response=client.post('/v1/jobs',json=request.model_dump(mode='json',exclude={'actor'}),headers=AUTH)
                assert response.status_code==503,response.text
            assert counts()==before,'failed admission leaked durable rows'
            assert len(reads)==(2 if fault in {'after_insert','changed'} else 1)
        with patch('services.job_store.p3_enqueue.validate_local_operation_authority',return_value=admitted):
            first=repository.enqueue(request,trace_id='test:local-valid')
            assert repository.enqueue(request,trace_id='test:local-dedup').job.job_id==first.job.job_id
        before=counts()
        with patch('services.job_store.p3_enqueue.validate_local_operation_authority',side_effect=AuthorityHeld('revoked')):
            with _rejected(JobStoreError):repository.enqueue(request,trace_id='test:local-replay')
        assert counts()==before
    return dict(revoked='REJECTED',expired='REJECTED',after_insert='ROLLED_BACK',intent='REJECTED',
        changed='ROLLED_BACK',dedup='CURRENT_AUTHORITY_REQUIRED',api_status=503,role_denials=4)
