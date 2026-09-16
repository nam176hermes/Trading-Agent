"""Synthetic, owner-seeded claims exercise SQL disclosure; no plaintext is used."""
from collections.abc import Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import LiteralString, cast

import psycopg
from psycopg.sql import SQL,Identifier

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.authority import (
    CustodyRecord, FamilyReview, PrimarySelection, RunAuthorization,
)
from packages.alpha_lifecycle.contracts.execution import InputSet
from packages.alpha_lifecycle.holdout import derive_holdout_request
from packages.alpha_lifecycle.operation_input import FAMILY_IDS, HoldoutInput, P3OperationInput
from packages.engine_contracts.serialization import canonical_json_bytes
from .p3_operation_fixture import _authorization, _reference, _rejected, _sealed, _wait_for_lock, _first


CONSUME = 'SELECT * FROM job_plane.worker_consume_p3_holdout(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
READ = 'SELECT * FROM job_plane.worker_read_p3_holdout_disclosure(%s,%s,%s,%s,%s,%s,%s)'


def _inputs(
    source: SourceIdentity, *, epoch: str = 'synthetic.epoch', material: str = 'first',
    alpha: str = FAMILY_IDS[0], expiry: datetime | None = None,
) -> tuple[tuple[str, str, str], tuple[str, str, str, str, str, str]]:
    ref = _reference('{}')
    source_json = source.model_dump(mode='json')
    research = _sealed(dict(schema_version='p3-input-set-v1', source=source_json, epoch_id=epoch,
        family_digest='f'*64, policy_digest='a'*64, dataset_evidence_ref=ref, fold_manifest_ref=ref,
        pit_proof_ref=ref, environment_ref=ref, regime_threshold_ref=ref, integration_receipt_ref=ref,
        cost_model=dict(fee_bps=10, spread_bps=2, slippage_bps=3, funding_bps=0, borrow_bps=0)))
    holdout = _sealed({**json.loads(research), 'dataset_evidence_ref':_reference(material)})
    family = _sealed(dict(schema_version='p3-family-review-v1', input_set_ref=_reference(research),
        candidate_report_refs=[ref]*4, trial_outcome_refs=[ref], review_ref=ref, complete_disclosure=True))
    primary = _sealed(dict(schema_version='p3-primary-selection-v1', family_review_ref=_reference(family),
        selection_policy_digest='a'*64, primary_alpha_id=alpha, primary_version='1.0.0',
        primary_candidate_head_ref=ref, outcome='SELECTED'))
    custody = _sealed(dict(schema_version='p3-custody-record-v1',
        holdout_commitment=hashlib.sha256(('commit:'+material).encode()).hexdigest(),
        ciphertext_ref=ref, plaintext_bundle_digest=hashlib.sha256(('plain:'+material).encode()).hexdigest(),
        custodian_identity='synthetic.custodian', research_identity='synthetic.research',
        custodian_attestation_ref=ref, access_policy_digest='c'*64,
        classification='HISTORICAL_ACCESS_CONTROLLED_NOT_INFORMATIONALLY_BLIND'))
    body: dict[str,object] = {field:ref for field in HoldoutInput.model_fields}
    body.update(primary_selection_ref=_reference(primary), custody_record_ref=_reference(custody),
        holdout_input_set_ref=_reference(holdout), holdout_dataset_ref=json.loads(holdout)['dataset_evidence_ref'],policy_digest='a'*64)
    auth, intent, review = _authorization(source, 'p3-holdout-primary-v1', 'HOLDOUT', (alpha,), body)
    intent = _sealed({**json.loads(intent), 'input_set_ref':_reference(research)})
    review = _sealed({**json.loads(review), 'subject_digests':[json.loads(intent)['digest']]})
    auth = _sealed({**json.loads(auth), 'input_set_ref':_reference(research), 'review_ref':_reference(review),
        **({'expires_at':expiry.isoformat().replace('+00:00','Z')} if expiry else {})})
    request = canonical_json_bytes(derive_holdout_request(P3OperationInput.model_validate_json(intent),
        RunAuthorization.model_validate_json(auth), expected_source=source)).decode()
    for model, raw in ((InputSet,research),(InputSet,holdout),(FamilyReview,family),
                       (PrimarySelection,primary),(CustodyRecord,custody)):
        assert canonical_json_bytes(model.model_validate_json(raw)).decode() == raw
    return (auth, intent, review), (request, research, holdout, family, primary, custody)


def _seed(
    sock: Path, name: str, source: SourceIdentity, label: str, authorization: tuple[str, str, str],
    *, worker: str = 'worker_disclose', token: str = 'x' * 32, legacy_claim: bool = True,
) -> tuple[str, str, str, str]:
    auth, intent, review = authorization
    job, attempt = 'job_disclose_'+label, 'attempt_disclose_'+label
    with psycopg.connect(host=str(sock), dbname=name, user='trading_p3_authority') as authority:
        authority.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)', authorization)
    raw = canonical_json_bytes(dict(schema_version='p3-alpha-campaign-payload-v1', operation='HOLDOUT',
        logical_trial_id='p3-holdout-primary-v1', manifest_ref=_reference(intent), authorization_ref=_reference(auth),
        expected_source=source)).decode()
    with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
        api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
            (job,raw,hashlib.sha256(raw.encode()).hexdigest(),'p3:p3-holdout-primary-v1:'+json.loads(auth)['nonce'],
             'synthetic-operator',100,'test:disclosure','event_disclosure_'+label))
    with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker') as connection:
        assert connection.execute('SELECT * FROM job_plane.worker_claim_bound_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
            (attempt,worker,token,30,'test:disclosure','event_claim_'+label,False,job)).fetchone() is None
    if not legacy_claim:
        return job, attempt, worker, token
    # Deliberately simulate a legacy claim. This is not workflow qualification.
    with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
        owner.execute("""UPDATE public.jobs SET state='CLAIMED',attempt_count=1,lease_owner=%s,
            lease_token=%s,lease_expires_at=clock_timestamp()+interval '90 seconds' WHERE job_id=%s""",
            (worker,token,job))
        owner.execute("""INSERT INTO public.job_attempts(attempt_id,job_id,attempt_number,worker_id,
            outcome,lease_token,lease_expires_at,claimed_at)
            SELECT %s,job_id,attempt_count,lease_owner,'CLAIMED',lease_token,lease_expires_at,clock_timestamp()
            FROM public.jobs WHERE job_id=%s""",(attempt,job))
    return job, attempt, worker, token


def check_holdout_disclosure(sock: Path, name: str, source: SourceIdentity, mark: Callable[[str], None]) -> None:
    auth, graph=_inputs(source,epoch='synthetic.wide',material='wide')
    wide=_seed(sock,name,source,'wide',auth,worker='w'*128,token='x'*16)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
        assert worker.execute(CONSUME,(*wide,*graph,'trace:'+'x'*122)).fetchone()
    authorization, inputs = _inputs(source)
    claim = _seed(sock,name,source,'first',authorization)
    args = (*claim,*inputs,'test:disclosure')
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker',autocommit=True) as worker:
        # This real SQL call is the initial RED seam on 0025.
        with _rejected(psycopg.errors.InvalidParameterValue):
            worker.execute(CONSUME,('missing_job',*args[1:]))
        for index in range(1,4):
            bad=list(args);bad[index]='wrong'
            with _rejected(psycopg.errors.InvalidParameterValue):
                worker.execute(CONSUME,bad)
        for index in range(4,10):
            bad=list(args);bad[index]+=' '
            with _rejected(psycopg.errors.InvalidParameterValue):
                worker.execute(CONSUME,bad)
        for index,field,value in ((4,'policy_digest','b'*64),(5,'epoch_id','different'),
            (6,'family_digest','b'*64),(7,'complete_disclosure',False),(8,'primary_alpha_id',FAMILY_IDS[1]),
            (9,'plaintext_bundle_digest','b'*64)):
            bad=list(args);bad[index]=_sealed({**json.loads(bad[index]),field:value})
            with _rejected(psycopg.errors.InvalidParameterValue):
                worker.execute(CONSUME,bad)
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        assert owner.execute('SELECT count(*) FROM public.p3_holdout_disclosures WHERE job_id=%s',(claim[0],)).fetchone()==(0,)
        before=owner.execute("""SELECT to_jsonb(j),to_jsonb(a),
            (SELECT count(*) FROM public.job_events), (SELECT count(*) FROM public.domain_events),
            (SELECT count(*) FROM public.event_outbox), (SELECT count(*) FROM public.p3_alpha_job_commits)
            FROM public.jobs j JOIN public.job_attempts a USING(job_id) WHERE j.job_id=%s""",(claim[0],)).fetchone()
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
        accepted=worker.execute(CONSUME,args).fetchone()
        assert accepted and accepted[:3]==(hashlib.sha256(authorization[0].encode()).hexdigest(),
            json.loads(authorization[1])['digest'],hashlib.sha256(inputs[0].encode()).hexdigest())
        worker.commit()
    # The connection which committed is actually closed before reconciliation.
    read_args=(*claim[:3],*accepted[:3],'test:disclosure')
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker',autocommit=True) as worker:
        assert worker.execute(READ,read_args).fetchone()==accepted
        assert worker.execute(READ,(*read_args[:5],'e'*64,read_args[-1])).fetchone() is None
        assert worker.execute(READ,(*read_args[:-1],'test:different')).fetchone() is None
        assert worker.execute(CONSUME,args).fetchone()==accepted
        with _rejected(psycopg.errors.UniqueViolation):
            worker.execute(CONSUME,(*args[:-1],'test:different'))
        with _rejected(psycopg.errors.InvalidParameterValue):
            worker.execute(CONSUME,(*args[:-1],'invalid trace'))
        assert worker.execute('SELECT job_plane.worker_start_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (*claim,601,601,601,'d'*64,'test:disclosure','event_disclosure_start')).fetchone()==(False,)
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        assert owner.execute("""SELECT to_jsonb(j),to_jsonb(a),
            (SELECT count(*) FROM public.job_events), (SELECT count(*) FROM public.domain_events),
            (SELECT count(*) FROM public.event_outbox), (SELECT count(*) FROM public.p3_alpha_job_commits)
            FROM public.jobs j JOIN public.job_attempts a USING(job_id) WHERE j.job_id=%s""",(claim[0],)).fetchone()==before
        assert owner.execute('SELECT count(*) FROM public.p3_holdout_disclosures WHERE job_id=%s',(claim[0],)).fetchone()==(1,)
    mark('HOLDOUT_DISCLOSURE_EXACT_BINDING_AND_RECONCILIATION_PASS')
    _check_client(sock,name,source)
    # Fresh nonce, different primary/material, or new epoch cannot reuse disclosure.
    for label, epoch, material, alpha in (
        ('nonce', 'synthetic.epoch', 'first', FAMILY_IDS[0]),
        ('fallback', 'synthetic.epoch', 'fallback', FAMILY_IDS[1]),
        ('relabel', 'synthetic.new-epoch', 'first', FAMILY_IDS[0]),
    ):
        auth, graph=_inputs(source, epoch=epoch, material=material, alpha=alpha)
        other=_seed(sock,name,source,label,auth)
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
            with _rejected(psycopg.errors.UniqueViolation):
                worker.execute(CONSUME,(*other,*graph,'test:disclosure'))
    mark('HOLDOUT_DISCLOSURE_NO_FALLBACK_OR_MATERIAL_REUSE_PASS')
    _check_concurrent(sock,name,source,mark)
    for role in ('trading_job_api','trading_job_scheduler','trading_reader','trading_p3_authority'):
        with psycopg.connect(host=str(sock),dbname=name,user=role,autocommit=True) as connection:
            with _rejected(psycopg.errors.InsufficientPrivilege):
                connection.execute(CONSUME,args)
            with _rejected(psycopg.errors.InsufficientPrivilege):
                connection.execute(READ,read_args)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker',autocommit=True) as worker:
        for query in ('SELECT * FROM public.p3_holdout_disclosures',
            "INSERT INTO public.p3_holdout_disclosures DEFAULT VALUES",
            "UPDATE public.p3_holdout_disclosures SET epoch_id='changed'",
            'DELETE FROM public.p3_holdout_disclosures','TRUNCATE public.p3_holdout_disclosures'):
            with _rejected(psycopg.errors.InsufficientPrivilege):
                worker.execute(SQL(query))
    with psycopg.connect(host=str(sock),dbname=name,user='trading_owner',autocommit=True) as owner:
        owner.execute('SET ROLE trading_p3_owner')
        for query in ("UPDATE public.p3_holdout_disclosures SET epoch_id='changed'",
                      'DELETE FROM public.p3_holdout_disclosures','TRUNCATE public.p3_holdout_disclosures'):
            with _rejected(psycopg.errors.ObjectNotInPrerequisiteState):
                owner.execute(SQL(query))
        row=_first(owner.execute('SELECT to_jsonb(d) FROM public.p3_holdout_disclosures d WHERE job_id=%s',(claim[0],)).fetchone())
        digest_fields=[field for field in row if field.endswith(('_digest','_sha256')) or field=='holdout_commitment']
        for field,value in [*((field,'A'*64) for field in digest_fields),('epoch_id','invalid epoch'),
            ('worker_id','invalid worker'),('trace_id','invalid trace'),('primary_version','invalid'),
            ('primary_alpha_id','Invalid'),('job_id','invalid job'),('attempt_id','invalid attempt'),
            ('custodian_identity','Invalid'),('research_identity','Invalid')]:
            with _rejected(psycopg.errors.CheckViolation):
                owner.execute('INSERT INTO public.p3_holdout_disclosures SELECT (jsonb_populate_record(NULL::public.p3_holdout_disclosures,%s::jsonb)).*',
                    (canonical_json_bytes({**row,field:value}).decode(),))
    mark('HOLDOUT_DISCLOSURE_PRIVILEGES_AND_APPEND_ONLY_PASS')


def _check_client(sock: Path, name: str, source: SourceIdentity) -> None:
    """Real SQL commits/readback; acknowledgement loss is explicitly injected."""
    from contextlib import contextmanager
    from pathlib import Path
    import tempfile
    from psycopg.rows import DictRow,dict_row
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.job_contracts import JobType
    from services.job_store.records import ClaimedJob
    from services.job_store.p3_holdout_disclosure import consume
    for lost in (False,True):
        label='client'+str(int(lost))
        authorization,graph=_inputs(source,epoch='synthetic.'+label,material=label)
        identity=_seed(sock,name,source,label,authorization)
        auth=RunAuthorization.model_validate_json(authorization[0])
        intent=P3OperationInput.model_validate_json(authorization[1])
        payload=build_alpha_campaign_payload(auth,source,intent.workflow_operation,operation_input=intent)
        claim=ClaimedJob(identity[0],JobType.ALPHA_CAMPAIGN,payload,identity[1],1,identity[2],identity[3],
            datetime.now(UTC)+timedelta(seconds=60),1)
        sessions=[]
        class Pool:
            @contextmanager
            def connection(self):
                with psycopg.Connection[DictRow].connect(host=str(sock),dbname=name,user='trading_job_worker',row_factory=dict_row) as connection:
                    sessions.append(connection.info.backend_pid)
                    yield connection
                if lost and len(sessions)==1:
                    raise psycopg.OperationalError('injected acknowledgement loss after a real SQL commit')
        pool=Pool()
        with tempfile.TemporaryDirectory(prefix='disclosure-client-',dir=sock.parent) as path:
            store=LocalArtifactStore(Path(path))
            for raw in (*authorization,*graph):
                store.put_bytes(raw.encode(),media_type='application/json')
            stamp=consume(pool,claim,store,trace_id='test:client')
            assert len(sessions)==2 and len(set(sessions))==2
            assert consume(pool,claim,store,trace_id='test:client')==stamp
            assert len(sessions)==4 and len(set(sessions))==4
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            assert owner.execute('SELECT count(*) FROM public.p3_holdout_disclosures WHERE job_id=%s',
                (claim.job_id,)).fetchone()==(1,)


def _check_concurrent(sock: Path, name: str, source: SourceIdentity, mark: Callable[[str], None]) -> None:
    auth, graph=_inputs(source,epoch='synthetic.concurrent',material='concurrent')
    claim=_seed(sock,name,source,'concurrent',auth)
    args=(*claim,*graph,'test:concurrent')
    def consume():
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
            return worker.execute(CONSUME,args).fetchone()
    with ThreadPoolExecutor(max_workers=1) as threads, psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as first:
        accepted=first.execute(CONSUME,args).fetchone()
        pending=threads.submit(consume)
        try:
            with psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
                _wait_for_lock(observer,'trading_job_worker','SELECT * FROM job_plane.worker_consume_p3_holdout%')
            first.commit()
            assert pending.result(timeout=5)==accepted
        finally:
            first.rollback()
    auth, graph=_inputs(source,epoch='synthetic.two-jobs',material='two-jobs')
    first_claim=_seed(sock,name,source,'two_first',auth)
    second_auth,second_graph=_inputs(source,epoch='synthetic.two-jobs',material='two-jobs')
    second_claim=_seed(sock,name,source,'two_second',second_auth)
    args=(*second_claim,*second_graph,'test:two-jobs')
    with ThreadPoolExecutor(max_workers=1) as threads, psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as first:
        assert first.execute(CONSUME,(*first_claim,*graph,'test:two-jobs')).fetchone()
        pending=threads.submit(consume)
        try:
            with psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
                _wait_for_lock(observer,'trading_job_worker','SELECT * FROM job_plane.worker_consume_p3_holdout%')
            first.commit()
            with _rejected(psycopg.errors.UniqueViolation):
                pending.result(timeout=5)
        finally:
            first.rollback()
    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
        assert owner.execute("SELECT count(*) FROM public.p3_holdout_disclosures WHERE epoch_id='synthetic.two-jobs'").fetchone()==(1,)
    # Observe the lock barrier, change the held fence, then allow consumption.
    for index,mutation in enumerate(('cancel','job_lease','attempt_lease','payload','auth_job','auth_attempt')):
        expiry=datetime.now(UTC)+timedelta(seconds=1.25) if mutation.startswith('auth_') else None
        auth, graph=_inputs(source,epoch='synthetic.lock-'+mutation.replace('_','-'),material=mutation,expiry=expiry)
        claim=_seed(sock,name,source,'lock'+str(index),auth)
        args=(*claim,*graph,'test:lock')
        with ThreadPoolExecutor(max_workers=1) as threads, psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            if mutation in {'attempt_lease','auth_attempt'}:
                owner.execute('SELECT attempt_id FROM public.job_attempts WHERE attempt_id=%s FOR UPDATE',(claim[1],))
                if mutation=='attempt_lease':
                    owner.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE attempt_id=%s",(claim[1],))
            else:
                owner.execute('SELECT job_id FROM public.jobs WHERE job_id=%s FOR UPDATE',(claim[0],))
                if mutation=='cancel':
                    owner.execute("UPDATE public.jobs SET state='CANCEL_REQUESTED' WHERE job_id=%s",(claim[0],))
                elif mutation=='job_lease':
                    owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(claim[0],))
                elif mutation=='payload':
                    owner.execute("UPDATE public.jobs SET payload=jsonb_set(payload,'{expected_source,commit_sha}',to_jsonb(repeat('e',40))) WHERE job_id=%s",(claim[0],))
            pending=threads.submit(consume)
            try:
                _wait_for_lock(owner,'trading_job_worker','SELECT * FROM job_plane.worker_consume_p3_holdout%')
                if expiry is not None:
                    owner.execute('SELECT pg_sleep(%s)',(max(0,(expiry-datetime.now(UTC)).total_seconds())+.025,))
                    assert _first(owner.execute('SELECT clock_timestamp()').fetchone())>=expiry
                owner.commit()
                with _rejected(psycopg.errors.InvalidParameterValue):
                    pending.result(timeout=5)
            finally:
                owner.rollback()
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            assert owner.execute('SELECT count(*) FROM public.p3_holdout_disclosures WHERE job_id=%s',(claim[0],)).fetchone()==(0,)
    mark('HOLDOUT_DISCLOSURE_TWO_CONNECTION_FENCE_PASS')


def check_disclosure_catalog(sock: Path, name: str, mark: Callable[[str], None]) -> None:
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import URL,create_engine
    spec=importlib.util.spec_from_file_location('p3_disclosure_catalog',
        Path(__file__).resolve().parents[2]/'alembic/versions/0026_p3_holdout_disclosure.py')
    assert spec and spec.loader
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine=create_engine(URL.create('postgresql+psycopg',username='trading_owner',database=name,query={'host':str(sock)}))
    try:
        def check():
            with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
                migration._catalog()
        check()
        consume='job_plane.worker_consume_p3_holdout('+','.join(['text']*11)+')'
        read='job_plane.worker_read_p3_holdout_disclosure('+','.join(['text']*7)+')'
        binder='job_plane.p3_bind_operation_job(jsonb,text)'
        api='job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text)'
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            definitions={signature:_first(owner.execute('SELECT pg_get_functiondef(%s::regprocedure)',(signature,)).fetchone())
                for signature in (consume,read,api)}
            binding_definition=_first(owner.execute('SELECT pg_get_functiondef(%s::regprocedure)',(binder,)).fetchone())
            database=Identifier(name).as_string(owner)
            assert owner.execute("SELECT rolvaliduntil FROM pg_roles WHERE rolname='trading_job_worker'").fetchone()==(None,)
            constraints=owner.execute("""SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid='public.p3_holdout_disclosures'::regclass
                  AND (contype IN ('f','u') OR conname='p3_holdout_policy_digest_hex') ORDER BY conname""").fetchall()
            assert owner.execute("SELECT has_function_privilege('trading_jobs',%s,'EXECUTE')",(consume,)).fetchone()==(False,)
        vectors=[
            ('enqueue_actor',definitions[api].replace("p_actor_id=o.review_text::jsonb->>'operator_identity'",'true'),definitions[api]),
            ('binding_writer',binding_definition.replace('ON CONFLICT DO NOTHING;', 'ON CONFLICT DO NOTHING; RETURN;'),binding_definition),
            ('role_expiry',"ALTER ROLE trading_job_worker VALID UNTIL '2000-01-01 UTC'",
                "UPDATE pg_catalog.pg_authid SET rolvaliduntil=NULL WHERE rolname='trading_job_worker'"),
            ('database_role_setting','ALTER ROLE trading_job_worker IN DATABASE '+database+' SET statement_timeout=0',
                'ALTER ROLE trading_job_worker IN DATABASE '+database+' RESET statement_timeout'),
            ('database_setting','ALTER DATABASE '+database+' SET statement_timeout=0',
                'ALTER DATABASE '+database+' RESET statement_timeout'),
            ('owner_database_timezone','ALTER ROLE trading_owner IN DATABASE '+database+" SET timezone='Pacific/Honolulu'",
                'ALTER ROLE trading_owner IN DATABASE '+database+" SET timezone='UTC'"),
            ('owner_database_timezone_absent','ALTER ROLE trading_owner IN DATABASE '+database+' RESET timezone',
                'ALTER ROLE trading_owner IN DATABASE '+database+" SET timezone='UTC'"),
            ('append_trigger','ALTER TABLE public.p3_holdout_disclosures DISABLE TRIGGER p3_holdout_disclosures_append_only','ALTER TABLE public.p3_holdout_disclosures ENABLE TRIGGER p3_holdout_disclosures_append_only'),
            ('table_acl','GRANT INSERT ON public.p3_holdout_disclosures TO trading_job_worker','REVOKE INSERT ON public.p3_holdout_disclosures FROM trading_job_worker'),
            ('column_acl','GRANT UPDATE (epoch_id) ON public.p3_holdout_disclosures TO trading_job_api','REVOKE UPDATE (epoch_id) ON public.p3_holdout_disclosures FROM trading_job_api'),
            ('table_owner','ALTER TABLE public.p3_holdout_disclosures OWNER TO trading_owner','ALTER TABLE public.p3_holdout_disclosures OWNER TO trading_p3_owner'),
            ('binding_trigger','ALTER TABLE public.p3_operation_job_bindings DISABLE TRIGGER p3_operation_job_bindings_append_only','ALTER TABLE public.p3_operation_job_bindings ENABLE TRIGGER p3_operation_job_bindings_append_only'),
            ('authorization_acl','GRANT INSERT ON public.p3_operation_authorizations TO trading_job_api','REVOKE INSERT ON public.p3_operation_authorizations FROM trading_job_api'),
            ('authority_role','GRANT trading_p3_authority TO trading_reader','REVOKE trading_p3_authority FROM trading_reader'),
            ('owner_inherits','GRANT trading_reader TO trading_p3_owner','REVOKE trading_reader FROM trading_p3_owner'),
            ('worker_inherits','GRANT trading_reader TO trading_job_worker','REVOKE trading_reader FROM trading_job_worker'),
            ('reverse_worker','GRANT trading_job_worker TO trading_reader','REVOKE trading_job_worker FROM trading_reader'),
            ('schema_create','GRANT CREATE ON SCHEMA job_plane TO trading_job_worker','REVOKE CREATE ON SCHEMA job_plane FROM trading_job_worker'),
            ('default_function','ALTER DEFAULT PRIVILEGES FOR ROLE trading_p3_owner GRANT EXECUTE ON FUNCTIONS TO trading_reader','ALTER DEFAULT PRIVILEGES FOR ROLE trading_p3_owner REVOKE EXECUTE ON FUNCTIONS FROM trading_reader'),
            ('default_table','ALTER DEFAULT PRIVILEGES FOR ROLE trading_p3_owner GRANT INSERT ON TABLES TO trading_job_api','ALTER DEFAULT PRIVILEGES FOR ROLE trading_p3_owner REVOKE INSERT ON TABLES FROM trading_job_api'),
        ]
        for table in ('p3_campaign_authorizations','p3_operation_authorizations',
                      'p3_operation_job_bindings','p3_holdout_disclosures','p3_alpha_job_commits',
                      'jobs','job_attempts','job_events','domain_events','event_outbox'):
            vectors.append(('rewrite_rule_'+table,'CREATE RULE p3_disclosure_test_rule AS ON INSERT TO public.'+table+
                ' DO INSTEAD NOTHING','DROP RULE p3_disclosure_test_rule ON public.'+table))
        for signature,definition in definitions.items():
            caller='trading_job_api' if signature==api else 'trading_job_worker'
            vectors.extend((
                ('function_strict','ALTER FUNCTION '+signature+' STRICT','ALTER FUNCTION '+signature+' CALLED ON NULL INPUT'),
                ('function_acl','GRANT EXECUTE ON FUNCTION '+signature+' TO trading_reader','REVOKE EXECUTE ON FUNCTION '+signature+' FROM trading_reader'),
                ('function_path','ALTER FUNCTION '+signature+' SET search_path=public','ALTER FUNCTION '+signature+' SET search_path=pg_catalog'),
                ('function_owner','ALTER FUNCTION '+signature+' OWNER TO trading_owner','ALTER FUNCTION '+signature+' OWNER TO trading_p3_owner'),
                ('function_body',definition.replace(caller,'trading_reader'),definition),
            ))
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            owner.execute('CREATE TRUSTED LANGUAGE p3_disclosure_plpgsql HANDLER plpgsql_call_handler INLINE plpgsql_inline_handler VALIDATOR plpgsql_validator')
        try:
            missed=[]
            for signature,definition in definitions.items():
                vectors.append(('function_language',definition.replace('LANGUAGE plpgsql','LANGUAGE p3_disclosure_plpgsql'),definition))
            for label,damage,restore in vectors:
                with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                    owner.execute(SQL(cast(LiteralString,damage)))
                try:
                    try:
                        check()
                    except RuntimeError as error:
                        assert 'drift' in str(error)
                    else:
                        missed.append(label)
                finally:
                    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                        owner.execute(SQL(cast(LiteralString,restore)))
                try:
                    check()
                except RuntimeError as error:
                    raise AssertionError('catalog restoration failed: '+label) from error
            assert not missed, 'catalog accepted hostile changes: '+','.join(missed)
            for constraint,definition in constraints:
                target=Identifier('public','p3_holdout_disclosures')
                with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                    owner.execute(SQL('ALTER TABLE {} DROP CONSTRAINT {}').format(target,Identifier(constraint)))
                try:
                    with _rejected(RuntimeError,match='drift'):
                        check()
                finally:
                    with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                        owner.execute(SQL('ALTER TABLE {} ADD CONSTRAINT {} ').format(target,Identifier(constraint))+SQL(cast(LiteralString,definition)))
                check()
        finally:
            with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                owner.execute('DROP LANGUAGE p3_disclosure_plpgsql')
    finally:
        engine.dispose()
    mark('HOLDOUT_DISCLOSURE_CATALOG_DRIFT_REJECTED_PASS')
