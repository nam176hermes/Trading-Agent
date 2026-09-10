"""Synthetic operation vectors inside the worker-owned disposable SQL fixture.

These deliberately synthetic reviews and inputs never grant official authority.
"""
import hashlib
import json
import time
import re
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, LiteralString
from uuid import UUID, NAMESPACE_URL, uuid5, uuid4

import psycopg
from psycopg.sql import SQL, Identifier
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine
from sqlalchemy.exc import DBAPIError
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus, AlphaRecordV1, QualificationDecision
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.job_contracts import AlphaCampaignOperation
from services.job_store.p3_sql import DomainAppendEntry

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_OPERATION_CHECKS = frozenset({
    'OPERATION_MIGRATION_CHAIN_PASS',
    'LEGACY_UNSCOPED_OFFICIAL_AUTHORITY_REJECTED_PASS',
    'OFFICIAL_INTENT_INPUT_REVIEW_AND_JOB_BINDING_PASS',
    'ENQUEUE_TWO_CONNECTION_AUTHORIZATION_EXPIRY_PASS',
    'OFFICIAL_PUBLICATION_ATOMIC_CUSTODY_AND_RECEIPT_PASS',
    'HOLDOUT_CANNOT_START_WITHOUT_DURABLE_CONSUMPTION_PASS',
})


@contextmanager
def _rejected(error_type, *, match=None):
    try:
        yield
    except error_type as error:
        if match is not None and re.search(match,str(error)) is None:
            raise AssertionError('SQL rejection reason differs') from error
    else:
        raise AssertionError('SQL accepted a rejected fixture vector')


def _first(row: tuple[Any, ...] | None) -> Any:
    if row is None or not row:
        raise RuntimeError('SQL fixture returned no expected row')
    return row[0]


def _entry() -> DomainAppendEntry:
    event_id = UUID("11111111-1111-5111-8111-111111111111")
    stream_id = UUID("22222222-2222-5222-8222-222222222222")
    record = AlphaRecordV1(
        alpha_id="a0.test", version="1.0.0", source_sha="a" * 40,
        implementation_identity="packages.alpha_lifecycle.candidates:run_candidate",
        dataset_snapshot_sha256="b" * 64, feature_set=("close",),
        parameter_set_sha256="c" * 64,
        training_start_at=datetime(2020,1,1,tzinfo=UTC),
        training_end_at=datetime(2021,1,1,tzinfo=UTC),
        validation_start_at=datetime(2021,1,2,tzinfo=UTC),
        validation_end_at=datetime(2022,1,1,tzinfo=UTC),
        oos_start_at=datetime(2022,1,2,tzinfo=UTC),
        oos_end_at=datetime(2023,1,1,tzinfo=UTC),
        universe=("BTCUSDT.BINANCE",), cost_model_sha256="d" * 64,
        baseline_id="B0_CASH", baseline_version="1.0.0",
        metrics_sha256=None, robustness_sha256=None,
        qualification_decision=QualificationDecision.NOT_EVALUATED,
        qualification_reason="preregistered", artifact_digests=("e" * 64,),
        lineage=("p3-btc-d1-e1",), superseded_version=None,
        lifecycle_status=AlphaLifecycleStatus.IDEA,
    )
    registry_event = {
        "predecessor_sha256": None,
        "record": record,
        "schema_version": "alpha-registry-event-v1",
        "sequence": 1,
    }
    registry_event_text = canonical_json_bytes(registry_event).decode()
    event = {
        "causation_id": "33333333-3333-5333-8333-333333333333",
        "correlation_id": "44444444-4444-5444-8444-444444444444",
        "effective_at": "2026-09-05T00:00:00Z",
        "event_id": str(event_id),
        "event_type": "AlphaRegistryTransitionRecordedV1",
        "expires_at": "2026-09-06T00:00:00Z",
        "ingested_at": "2026-09-05T00:00:00Z",
        "observed_at": "2026-09-05T00:00:00Z",
        "payload": {
            "alpha_id": "a0.test",
            "alpha_version": "1.0.0",
            "epoch_id": "p3-btc-d1-e1",
            "evidence_sha256": "d" * 64,
            "predecessor_sha256": None,
            "registry_event_sha256": hashlib.sha256(registry_event_text.encode()).hexdigest(),
            "registry_event_text": registry_event_text,
            "registry_sequence": 1,
            "schema_version": "alpha-registry-transition-recorded-v1",
        },
        "produced_at": "2026-09-05T00:00:00Z",
        "schema_version": "event-envelope-v1",
        "sequence": 1,
        "source": "p3-alpha-lifecycle",
        "stream_id": str(stream_id),
        "trace_id": "55555555-5555-5555-8555-555555555555",
    }
    return DomainAppendEntry(
        event_id=event_id,
        stream_id=stream_id,
        sequence=1,
        event_type="AlphaRegistryTransitionRecordedV1",
        canonical_event_text=canonical_json_bytes(event).decode(),
        topic="p3.alpha-registry",
        outbox_payload_text=canonical_json_bytes({"event_id": str(event_id)}).decode(),
    )

def publication_entries(store, evidence_ref, alpha_ids, *, source_sha='a'*40):
    template = json.loads(_entry().canonical_event_text)
    entries, refs, heads = [], [], []
    for alpha_id in alpha_ids:
        heads.append({'alpha_id':alpha_id,'version':'1.0.0','sequence':0,'event_digest':None})
        predecessor = None
        stream_id = uuid5(NAMESPACE_URL, alpha_id)
        for sequence, status in ((1,'IDEA'),(2,'CANDIDATE')):
            event = json.loads(canonical_json_bytes(template))
            registry = json.loads(event['payload']['registry_event_text'])
            registry['record'].update(alpha_id=alpha_id,lifecycle_status=status,source_sha=source_sha)
            registry.update(sequence=sequence,predecessor_sha256=predecessor)
            registry_raw = canonical_json_bytes(registry)
            ref = store.put_bytes(registry_raw,media_type='application/json')
            refs.append(ref)
            event_id = uuid5(NAMESPACE_URL, f'{alpha_id}:{sequence}')
            event.update(event_id=str(event_id),stream_id=str(stream_id),sequence=sequence)
            event['payload'].update(alpha_id=alpha_id,registry_sequence=sequence,
                predecessor_sha256=predecessor,registry_event_sha256=ref.content_sha256,
                registry_event_text=registry_raw.decode(),evidence_sha256=evidence_ref.content_sha256)
            entries.append(DomainAppendEntry.model_validate_json(canonical_json_bytes({
                'event_id':str(event_id),'stream_id':str(stream_id),'sequence':sequence,
                'event_type':'AlphaRegistryTransitionRecordedV1','canonical_event_text':canonical_json_bytes(event).decode(),
                'topic':'p3.alpha-registry','outbox_payload_text':canonical_json_bytes({'event_id':str(event_id)}).decode(),
            })))
            predecessor = ref.content_sha256
    return tuple(entries), refs, heads


def check_operations(sock, name, root, payload, mark):
    # Synthetic role belongs only to this newly created socket-only cluster.
    with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
        owner.execute('CREATE ROLE trading_p3_authority LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
        owner.execute(SQL('GRANT CONNECT ON DATABASE {} TO trading_p3_authority').format(Identifier(name)))
    engine = create_engine(URL.create('postgresql+psycopg', username='trading_owner',
                                     database=name, query={'host': str(sock)}))
    try:
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            owner.execute('GRANT trading_job_api TO trading_p3_authority')
        with _rejected(DBAPIError, match='P3 protected operation authority unavailable'):
            with engine.begin() as connection:
                config = Config(str(ROOT / 'alembic.ini'))
                config.attributes['connection'] = connection
                command.upgrade(config, 'head')
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            owner.execute('REVOKE trading_job_api FROM trading_p3_authority')
            owner.execute('GRANT EXECUTE ON FUNCTION job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text) TO trading_reader')
        with _rejected(RuntimeError, match='reviewed function drift'):
            with engine.begin() as connection:
                config = Config(str(ROOT / 'alembic.ini'))
                config.attributes['connection'] = connection
                command.upgrade(config, 'head')
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            owner.execute('REVOKE EXECUTE ON FUNCTION job_plane.api_enqueue_alpha_campaign(text,text,text,text,text,integer,text,text) FROM trading_reader')
        with engine.begin() as connection:
            config = Config(str(ROOT / 'alembic.ini'))
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
    finally:
        engine.dispose()
    mark('OPERATION_MIGRATION_CHAIN_PASS',flush=True)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_owner') as owner:
        owner.execute('SET ROLE trading_p3_owner')
        for updates in ({'size_bytes': 1 << 40}, {'media_type': 'text/markdown'}):
            ref = {**_reference('{}'), **updates}
            assert owner.execute('SELECT job_plane.p3_valid_ref(%s::jsonb)',
                                 (canonical_json_bytes(ref).decode(),)).fetchone() == (True,)
    authorization = canonical_json_bytes({'purpose': 'synthetic-registration-source-test'})
    digest = hashlib.sha256(authorization).hexdigest()
    legacy = payload.model_copy(update={
        'operation': AlphaCampaignOperation.REGISTER_FAMILY,
        'logical_trial_id': 'p3-register-family-v1',
        'authorization_ref': payload.authorization_ref.model_copy(update={
            'content_sha256': digest, 'size_bytes': len(authorization),
            'locator': digest + '.blob'}),
    })
    raw = canonical_json_bytes(legacy)
    with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
        with _rejected(psycopg.errors.InvalidParameterValue):
            api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
                        ('job_unscoped', raw.decode(), hashlib.sha256(raw).hexdigest(),
                         'unscoped', 'source-test', 0, 'test:unscoped', 'event_unscoped'))
    mark('LEGACY_UNSCOPED_OFFICIAL_AUTHORITY_REJECTED_PASS')
    _check_accepted(sock, name, payload.expected_source, mark)
    _check_enqueue_expiry(sock, name, payload.expected_source, mark)
    _check_publication(sock, name, root, payload.expected_source, mark)
    _check_holdout_denial(sock, name, payload.expected_source, mark)


def _sealed(value):
    value = {key: item for key, item in value.items() if key != 'digest'}
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return canonical_json_bytes(value).decode()


def _reference(raw):
    raw = raw.encode()
    digest = hashlib.sha256(raw).hexdigest()
    return dict(content_sha256=digest, size_bytes=len(raw), media_type='application/json', locator=digest+'.blob')


def _authorization(source, workflow='p3-baselines-v1', operation='BASELINES', ids=(), body=None):
    now = datetime.now(UTC)
    utc = lambda value: value.isoformat().replace('+00:00', 'Z')
    ref = _reference('{"purpose":"synthetic-input-not-an-official-inputset"}')
    intent = _sealed(dict(schema_version='p3-operation-input-v1', workflow_operation=workflow,
        operation=operation, input_set_ref=ref, allowed_alpha_ids=list(ids), body=body or {'baseline_manifest_ref': ref}))
    safe = dict(broker=False, live=False, network=False, production=False)
    review = _sealed(dict(schema_version='p3-review-approval-v1', source=source.model_dump(mode='json'),
        subject_digests=[json.loads(intent)['digest']], operator_identity='synthetic-operator',
        reviewer_identity='synthetic-reviewer', review_execution_id='synthetic-source-test', verdict='APPROVED',
        issued_at=utc(now-timedelta(minutes=2)), expires_at=utc(now+timedelta(minutes=10)),
        evidence_ref=ref, authority=safe))
    auth = _sealed(dict(schema_version='p3-run-authorization-v1', input_set_ref=ref, review_ref=_reference(review),
        operation=operation, allowed_alpha_ids=list(ids), issued_at=utc(now-timedelta(minutes=1)),
        expires_at=utc(now+timedelta(minutes=5)), nonce=str(uuid4()), issuer_workflow='p3-authority.yml',
        issuer_run_id=1, issuer_attempt=1, authority=safe))
    return auth, intent, review


def _check_accepted(sock, name, source, mark):
    auth, intent, review = _authorization(source)
    accept = 'SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)'
    for role in ('trading_job_api', 'trading_job_worker'):
        with psycopg.connect(host=str(sock), dbname=name, user=role) as connection:
            with _rejected(psycopg.errors.InsufficientPrivilege):
                connection.execute(accept, (auth, intent, review))
    with psycopg.connect(host=str(sock), dbname=name, user='trading_p3_authority') as authority:
        for operation, raw, reviewed in (
            (_sealed({**json.loads(auth), 'review_ref': _reference('{}')}), intent, review),
            (auth, _sealed({**json.loads(intent), 'input_set_ref': _reference('{}')}), review),
            (auth, _sealed({**json.loads(intent), 'workflow_operation': 'p3-oos-a1-v1'}), review),
            (auth, intent, _sealed({**json.loads(review), 'verdict': 'REJECTED'})),
            (auth+' ', intent, review),
        ):
            with _rejected(psycopg.errors.InvalidParameterValue), authority.transaction():
                authority.execute(accept, (operation, raw, reviewed))
        wrongly_accepted = []
        for edge in ('naive_time', 'numeric_source', 'invalid_review_subject', 'boolean_operator', 'boolean_reviewer', 'boolean_execution', 'hour_24'):
            bad_auth, bad_intent, bad_review = _authorization(source)
            a, r = json.loads(bad_auth), json.loads(bad_review)
            if edge == 'naive_time':
                a['issued_at'] = a['issued_at'].removesuffix('Z')
            elif edge == 'numeric_source':
                r['source']['commit_sha'] = int('1'*40)
            elif edge == 'invalid_review_subject':
                r['subject_digests'].append('not-a-sha256')
            elif edge.startswith('boolean_'):
                field = {'boolean_operator':'operator_identity', 'boolean_reviewer':'reviewer_identity', 'boolean_execution':'review_execution_id'}[edge]
                r[field] = True
            else:
                yesterday = datetime.now(UTC).date() - timedelta(days=1)
                a['issued_at'] = yesterday.isoformat() + 'T24:00:00Z'
                r['issued_at'] = yesterday.isoformat() + 'T23:59:59Z'
            bad_review = _sealed(r)
            a['review_ref'] = _reference(bad_review)
            try:
                with authority.transaction(force_rollback=True):
                    authority.execute(accept, (_sealed(a), bad_intent, bad_review))
            except psycopg.errors.InvalidParameterValue:
                pass
            else:
                wrongly_accepted.append(edge)
        assert not wrongly_accepted, wrongly_accepted
        authority.execute('SELECT transaction_timestamp()')
        issued = _first(authority.execute('SELECT clock_timestamp()').fetchone())
        auth = _sealed({**json.loads(auth), 'issued_at': issued.isoformat().replace('+00:00','Z')})
        digest = _first(authority.execute(accept, (auth, intent, review)).fetchone())
        assert digest == _reference(auth)['content_sha256']
        assert authority.execute(accept, (auth, intent, review)).fetchone() == (digest,)
        with _rejected(psycopg.errors.InsufficientPrivilege), authority.transaction():
            authority.execute('INSERT INTO public.p3_operation_job_bindings VALUES (%s,%s,clock_timestamp())', (digest,'job_invented'))
    payload: dict[str, Any] = dict(schema_version='p3-alpha-campaign-payload-v1', operation='BASELINES',
        manifest_ref=_reference(intent), authorization_ref=_reference(auth),
        expected_source=source.model_dump(mode='json'), logical_trial_id='p3-baselines-v1')
    nonce = json.loads(auth)['nonce']
    key = 'p3:p3-baselines-v1:'+nonce
    enqueue = 'SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)'
    def params(value, job='job_scoped', actor='synthetic-operator', idempotency=key):
        raw = canonical_json_bytes(value).decode()
        return (job,raw,hashlib.sha256(raw.encode()).hexdigest(),idempotency,actor,0,'test:operation','event_'+job)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as api:
        for altered in (
            params({**payload,'manifest_ref':json.loads(auth)['input_set_ref']}),
            params({**payload,'logical_trial_id':'p3-select-primary-v1'}),
            params(payload,actor='another-operator'),
            params(payload,idempotency='another-key'),
        ):
            with _rejected(psycopg.errors.InvalidParameterValue), api.transaction():
                api.execute(enqueue,altered)
        assert api.execute(enqueue,params(payload)).fetchone() == ('job_scoped','ENQUEUED')
        assert api.execute(enqueue,params(payload,job='job_alias')).fetchone() == ('job_scoped','DEDUPLICATED')
    with psycopg.connect(host=str(sock),dbname=name,user='trading_owner') as owner:
        owner.execute('SET ROLE trading_p3_owner')
        assert _first(owner.execute('SELECT accepted_at FROM public.p3_campaign_authorizations WHERE request_digest=%s',(digest,)).fetchone()) >= issued
        assert owner.execute('SELECT input_set_digest FROM public.p3_campaign_authorizations WHERE request_digest=%s',(digest,)).fetchone() == (json.loads(auth)['input_set_ref']['content_sha256'],)
        assert json.loads(auth)['input_set_ref']['content_sha256'] != payload['manifest_ref']['content_sha256']
        assert owner.execute('SELECT job_id FROM public.p3_operation_job_bindings WHERE authorization_digest=%s',(digest,)).fetchone() == ('job_scoped',)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
        claimed = worker.execute('SELECT * FROM job_plane.worker_claim_alpha_campaign(%s,%s,%s,%s,%s,%s)',
            ('attempt_scoped','worker_scoped','s'*32,30,'test:claim','event_claim_scoped')).fetchone()
        assert claimed is not None and claimed[0] == 'job_scoped'
        assert worker.execute('SELECT job_plane.worker_start_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            ('job_scoped','attempt_scoped','worker_scoped','s'*32,401,401,401,'d'*64,'test:start','event_start_scoped')).fetchone() == (True,)
    mark('OFFICIAL_INTENT_INPUT_REVIEW_AND_JOB_BINDING_PASS')


def _check_enqueue_expiry(sock, name, source, mark):
    """Wait on the actual unique-key conflict, then release after expiry."""
    accepted = []
    for keep_first in (False, True):
        auth, intent, review = _authorization(source)
        expiry = datetime.now(UTC) + timedelta(seconds=3)
        auth = _sealed({**json.loads(auth), 'expires_at':expiry.isoformat().replace('+00:00','Z')})
        with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_authority') as authority:
            authority.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)', (auth,intent,review))
        payload: dict[str, Any] = dict(schema_version='p3-alpha-campaign-payload-v1',operation='BASELINES',
            manifest_ref=_reference(intent),authorization_ref=_reference(auth),
            expected_source=source.model_dump(mode='json'),logical_trial_id='p3-baselines-v1')
        raw = canonical_json_bytes(payload).decode()
        key = 'p3:p3-baselines-v1:'+json.loads(auth)['nonce']
        prefix = 'expiry_keep' if keep_first else 'expiry_rollback'
        sql: LiteralString = 'SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)'
        def params(job):
            return (job,raw,hashlib.sha256(raw.encode()).hexdigest(),key,'synthetic-operator',0,'test:expiry','event_'+job)
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as first, psycopg.connect(host=str(sock),dbname=name,user='trading_job_api',application_name=prefix) as second, psycopg.connect(host=str(sock),dbname=name,user='postgres',autocommit=True) as observer:
            first.execute(sql,params(prefix+'_first'))
            def enqueue_waiter():
                try:
                    with second.transaction():
                        return second.execute(sql,params(prefix+'_second')).fetchone()
                except psycopg.errors.InvalidParameterValue:
                    return None
            with ThreadPoolExecutor(max_workers=1) as threads:
                waiting = threads.submit(enqueue_waiter)
                try:
                    deadline = time.monotonic()+2
                    while time.monotonic()<deadline:
                        if observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE application_name=%s",(prefix,)).fetchone() == ('Lock',):
                            break
                        time.sleep(.01)
                    else:
                        raise AssertionError('second connection never waited on unique-key lock')
                    observer.execute('SELECT pg_sleep(greatest(0,extract(epoch from (%s::timestamptz-clock_timestamp())))+.1)',(expiry,))
                finally:
                    first.commit() if keep_first else first.rollback()
                if waiting.result(timeout=5) is not None:
                    accepted.append(prefix)
            if prefix not in accepted:
                assert observer.execute('SELECT count(*) FROM public.jobs WHERE job_id=%s',(prefix+'_second',)).fetchone() == (0,)
    assert not accepted, accepted
    mark('ENQUEUE_TWO_CONNECTION_AUTHORIZATION_EXPIRY_PASS')


def _check_publication(sock, name, root, source, mark):
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
    from packages.alpha_lifecycle.publication import recover_publication_receipt
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_store.p3_publication_repository import P3PublicationRepository
    from services.job_store.worker_repository import WorkerRepository
    from services.job_worker.recovery import ProcessIdentity
    ref = _reference('{}')
    body = dict(baseline_selection_ref=ref,candidate_spec_refs=[ref]*4,candidate_record_refs=[ref]*4)
    auth, intent, review = _authorization(source,'p3-register-family-v1','REGISTER_FAMILY',FAMILY_IDS,body)
    expiry = datetime.now(UTC)+timedelta(seconds=30)
    auth = _sealed({**json.loads(auth),'expires_at':expiry.isoformat().replace('+00:00','Z')})
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_authority') as authority:
        authority.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)',(auth,intent,review))
    payload: dict[str, Any] = dict(schema_version='p3-alpha-campaign-payload-v1',operation='REGISTER_FAMILY',
        manifest_ref=_reference(intent),authorization_ref=_reference(auth),expected_source=source.model_dump(mode='json'),logical_trial_id='p3-register-family-v1')
    raw = canonical_json_bytes(payload).decode()
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as api:
        api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
            ('job_official_pub',raw,hashlib.sha256(raw.encode()).hexdigest(),'p3:p3-register-family-v1:'+json.loads(auth)['nonce'],'synthetic-operator',100,'test:publication','event_official_pub'))
    store_root = root/'operation-publication-cas'
    store_root.mkdir(mode=0o700)
    store = LocalArtifactStore(store_root)
    evidence = store.put_bytes(b'{"purpose":"synthetic-operation-source-test"}',media_type='application/json')
    entries, refs, heads = publication_entries(store,evidence,FAMILY_IDS,source_sha=source.commit_sha)
    with ConnectionPool(make_conninfo(host=str(sock),dbname=name,user='trading_job_worker'),min_size=1,max_size=2,kwargs={'row_factory':dict_row}) as pool:
        worker = object.__new__(WorkerRepository)
        worker._pool = pool
        worker.assert_p3_runtime_identity()
        claim = worker.claim_next_alpha_campaign('worker_official_pub',30,'test:claim-publication')
        assert claim is not None and claim.job_id == 'job_official_pub'
        assert worker.start_attempt(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,ProcessIdentity(501,501,501,'d'*64),'test:start-publication',alpha_campaign=True)
        request = PublicationRequest.model_validate_json(_sealed(dict(schema_version='p3-publication-request-v1',
            idempotency_key='source-official-registration',semantic_request_digest=hashlib.sha256(canonical_json_bytes(refs)).hexdigest(),stage='REGISTER',evidence_ref=evidence.model_dump(mode='json'),expected_heads=heads,proposed_event_refs=[r.model_dump(mode='json') for r in refs],job_id=claim.job_id)))
        publisher = P3PublicationRepository(pool,store)
        assert not worker.finalize(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,
            expected_state='RUNNING',expected_attempt_outcome='RUNNING',final_state='SUCCEEDED',
            reason_code='PROCESS_EXITED',trace_id='test:no-generic-publication',alpha_campaign=True)
        wrong_event = json.loads(entries[0].canonical_event_text)
        wrong_event['payload']['evidence_sha256'] = 'e'*64
        wrong_evidence_entries = (entries[0].model_copy(update={'canonical_event_text':canonical_json_bytes(wrong_event).decode()}),*entries[1:])
        transport = canonical_json_bytes(dict(job_id=claim.job_id,attempt_id=claim.attempt_id,
            worker_id=claim.worker_id,lease_token=claim.lease_token,request=request,entries=wrong_evidence_entries)).decode()
        with _rejected(psycopg.Error,match='P3 source or operation publication authority rejected'), pool.connection() as connection:
            connection.execute(P3PublicationRepository.COMMIT_SQL,(claim.job_id,claim.attempt_id,
                claim.worker_id,claim.lease_token,transport,'test:wrong-prepublication-evidence'))
        for fault in ('order','size','media','missing_ref_field','malformed_evidence'):
            malformed = request.model_dump(mode='json')
            if fault == 'order':
                malformed['proposed_event_refs'].reverse()
            elif fault == 'size':
                malformed['proposed_event_refs'][0]['size_bytes'] += 1
            elif fault == 'media':
                malformed['proposed_event_refs'][0]['media_type'] = 'text/plain'
            elif fault == 'missing_ref_field':
                del malformed['proposed_event_refs'][0]['size_bytes']
            else:
                del malformed['evidence_ref']['size_bytes']
            transport = canonical_json_bytes(dict(job_id=claim.job_id,attempt_id=claim.attempt_id,
                worker_id=claim.worker_id,lease_token=claim.lease_token,
                request=json.loads(_sealed(malformed)),entries=entries)).decode()
            with _rejected(psycopg.Error,match='P3 source or operation publication authority rejected'), pool.connection() as connection:
                connection.execute(P3PublicationRepository.COMMIT_SQL,(claim.job_id,claim.attempt_id,
                    claim.worker_id,claim.lease_token,transport,'test:publication-ref-'+fault))
        wrong_entries, wrong_refs, _ = publication_entries(store,evidence,FAMILY_IDS,source_sha='f'*40)
        wrong = request.model_dump(mode='json')
        wrong['proposed_event_refs'] = [r.model_dump(mode='json') for r in wrong_refs]
        wrong['semantic_request_digest'] = hashlib.sha256(canonical_json_bytes(wrong_refs)).hexdigest()
        with _rejected(psycopg.Error):
            publisher.publish(PublicationRequest.model_validate_json(_sealed(wrong)),claim,wrong_entries,trace_id='test:wrong-record-source')
        bad_entries = (*entries[:-1],entries[-1].model_copy(update={'outbox_payload_text':canonical_json_bytes({'event_id':str(entries[0].event_id)}).decode()}))
        with _rejected(psycopg.errors.InvalidParameterValue):
            publisher.publish(request,claim,bad_entries,trace_id='test:atomic-rollback')
        event_ids = [entry.event_id for entry in entries]
        with psycopg.connect(host=str(sock),dbname=name,user='postgres',autocommit=True) as observer, psycopg.connect(host=str(sock),dbname=name,user='postgres') as blocker:
            for table in ('domain_events','event_outbox'):
                assert observer.execute(f'SELECT count(*) FROM public.{table} WHERE event_id=ANY(%s)',(event_ids,)).fetchone() == (0,)
            assert observer.execute('SELECT count(*) FROM public.p3_alpha_job_commits WHERE job_id=%s',(claim.job_id,)).fetchone() == (0,)
            assert observer.execute('SELECT count(*) FROM public.p3_alpha_heads WHERE alpha_id=ANY(%s)',(list(FAMILY_IDS),)).fetchone() == (0,)
            assert observer.execute('SELECT state FROM public.jobs WHERE job_id=%s',(claim.job_id,)).fetchone() == ('RUNNING',)
            blocker.execute('SELECT job_id FROM public.jobs WHERE job_id=%s FOR UPDATE',(claim.job_id,))
            with ThreadPoolExecutor(max_workers=2) as threads:
                futures = [threads.submit(publisher.publish,request,claim,entries,trace_id='test:concurrent-publication') for _ in range(2)]
                try:
                    deadline = time.monotonic()+3
                    while time.monotonic()<deadline:
                        if observer.execute("SELECT count(*) FROM pg_stat_activity WHERE usename='trading_job_worker' AND wait_event_type='Lock' AND query LIKE 'SELECT job_plane.worker_commit_alpha_campaign%%'").fetchone() == (2,):
                            break
                        time.sleep(.01)
                    else:
                        raise AssertionError('two SQL publication connections did not wait on the job lock')
                finally:
                    blocker.rollback()
                first, second = [future.result(timeout=5) for future in futures]
                assert first == second
                result = first
        with _rejected(RuntimeError, match='custody unavailable'):
            recover_publication_receipt('job_publication',repository=publisher,store=store)
        altered = request.model_dump(mode='json')
        altered['expected_heads'][0].update(sequence=1,event_digest='a'*64)
        with _rejected(psycopg.errors.UniqueViolation):
            publisher.publish(PublicationRequest.model_validate_json(_sealed(altered)),claim,entries,trace_id='test:changed-idempotent-request')
        receipt = recover_publication_receipt(claim.job_id,repository=publisher,store=store)
        assert receipt == recover_publication_receipt(claim.job_id,repository=publisher,store=store)
        assert result.registry_event_refs == receipt.registry_event_refs
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
            stored = observer.execute('SELECT publication_request_text,result_json,committed_at FROM public.p3_alpha_job_commits WHERE job_id=%s',(claim.job_id,)).fetchone()
            assert stored is not None
            assert stored == (canonical_json_bytes(request).decode(),result.model_dump(mode='json'),receipt.committed_at)
            assert _first(observer.execute('SELECT result_metadata FROM public.jobs WHERE job_id=%s',(claim.job_id,)).fetchone()) == stored[1]
            for table in ('domain_events','event_outbox'):
                assert observer.execute(f'SELECT count(*) FROM public.{table} WHERE event_id = ANY(%s)',(list(result.ledger_event_ids),)).fetchone() == (8,)
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
            # Synthetic test window leaves room for the real concurrent writes.
            # Keep the outer fixture's SQL process fence current while it expires.
            while (remaining := (expiry-datetime.now(UTC)).total_seconds()) > 0:
                observer.execute('SELECT pg_sleep(%s)',(min(5,remaining)+.1,))
                mark('OPERATION_AUTHORIZATION_EXPIRY_WAIT',flush=True)
        assert publisher.publish(request,claim,entries,trace_id='test:expired-commit-readback') == result
        assert publisher.recover_receipt(claim.job_id) == receipt
        for role in ('trading_job_api','trading_p3_authority'):
            with psycopg.connect(host=str(sock),dbname=name,user=role) as denied:
                with _rejected(psycopg.errors.InsufficientPrivilege):
                    denied.execute('SELECT * FROM job_plane.worker_read_alpha_publication(%s)',(claim.job_id,))
    mark('OFFICIAL_PUBLICATION_ATOMIC_CUSTODY_AND_RECEIPT_PASS')


def _check_holdout_denial(sock, name, source, mark):
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS, HoldoutInput
    ref = _reference('{}')
    body: dict[str, object] = {field:ref for field in HoldoutInput.model_fields}
    body['policy_digest'] = 'a'*64
    auth, intent, review = _authorization(source,'p3-holdout-primary-v1','HOLDOUT',FAMILY_IDS[:1],body)
    with psycopg.connect(host=str(sock),dbname=name,user='trading_p3_authority') as authority:
        authority.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)',(auth,intent,review))
    payload: dict[str, Any] = dict(schema_version='p3-alpha-campaign-payload-v1',operation='HOLDOUT',
        manifest_ref=_reference(intent),authorization_ref=_reference(auth),expected_source=source.model_dump(mode='json'),logical_trial_id='p3-holdout-primary-v1')
    raw = canonical_json_bytes(payload).decode()
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as api:
        api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
            ('job_holdout_held',raw,hashlib.sha256(raw.encode()).hexdigest(),'p3:p3-holdout-primary-v1:'+json.loads(auth)['nonce'],'synthetic-operator',100,'test:holdout-held','event_holdout_enqueue'))
    with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker') as worker:
        claimed = worker.execute('SELECT * FROM job_plane.worker_claim_alpha_campaign(%s,%s,%s,%s,%s,%s)',
            ('attempt_holdout','worker_holdout','h'*32,30,'test:holdout-claim','event_holdout_claim')).fetchone()
        assert claimed is not None and claimed[0] == 'job_holdout_held'
        assert worker.execute('SELECT job_plane.worker_start_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            ('job_holdout_held','attempt_holdout','worker_holdout','h'*32,601,601,601,'d'*64,'test:holdout-start','event_holdout_start')).fetchone() == (False,)
    mark('HOLDOUT_CANNOT_START_WITHOUT_DURABLE_CONSUMPTION_PASS')
