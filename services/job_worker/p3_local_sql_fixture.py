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

REVISION='0031_p3_local_identity'


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
            command.upgrade(config,REVISION)
            connection.execute(text('SET LOCAL search_path=pg_catalog'))
            catalog=connection.execute(text(SESSION_CATALOG_SQL)).scalar()
            print('LOCAL_SESSION_CATALOG_SHA256='+str(catalog),flush=True)
            assert catalog==SESSION_CATALOG_SHA256
    finally:
        engine.dispose()
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
    return dict(verdict='PASS',catalog_sha256=catalog,concurrent_winners=1,identity_denials=5,role_denials=5,
        cross_lane_second_disclosures_rejected=2,expiry='REJECTED',start_before_disclosure='REJECTED',
        wrong_identity_heartbeat='STALE',terminal='COMMITTED',recovery='TERMINAL_NO_RETRY',retry='REJECTED',migration_parent_drift='REJECTED',phase_exit_readback='COMMITTED_CURRENT_HEAD_ONLY')
