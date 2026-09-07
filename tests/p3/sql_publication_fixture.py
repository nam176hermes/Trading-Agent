"""Synthetic SQL publication source check; called only by the disposable runner."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts import canonical_json_bytes
from packages.job_contracts import AlphaCampaignOperation
from services.job_store.p3_publication_repository import P3PublicationRepository
from services.job_store.p3_sql import DomainAppendEntry
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.recovery import ProcessIdentity
from tests.p3.test_sql_authority import _entry


def check_publication(sock, name, root, payload):
    store_root = root / 'publication-cas'
    store_root.mkdir(mode=0o700)
    store = LocalArtifactStore(store_root)
    evidence_ref = store.put_bytes(b'{"purpose":"synthetic-source-test"}', media_type='application/json')
    authorization = canonical_json_bytes({'purpose': 'synthetic-registration-source-test'})
    authorization_ref = store.put_bytes(authorization, media_type='application/json')
    payload = payload.model_copy(update={
        'operation': AlphaCampaignOperation.REGISTER_FAMILY,
        'logical_trial_id': 'p3-register-family-v1',
        'authorization_ref': authorization_ref,
    })
    with psycopg.connect(host=str(sock), dbname=name, user='trading_owner') as owner:
        owner.execute('SET ROLE trading_p3_owner')
        owner.execute("INSERT INTO public.p3_campaign_authorizations(request_digest,input_set_digest,source_commit_sha,source_identity_text,operation,expires_at,authorization_text) VALUES (%s,%s,%s,%s,'REGISTER_FAMILY',clock_timestamp()+interval '1 hour',%s)",
            (authorization_ref.content_sha256,payload.manifest_ref.content_sha256,payload.expected_source.commit_sha,canonical_json_bytes(payload.expected_source).decode(),authorization.decode()))
    raw = canonical_json_bytes(payload)
    with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
        api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
            ('job_publication',raw.decode(),hashlib.sha256(raw).hexdigest(),'publication-test','source-test',0,'publication:enqueue','event_publication_enqueue'))
    pool = ConnectionPool(make_conninfo(host=str(sock),dbname=name,user='trading_job_worker'), min_size=1,max_size=2,kwargs={'row_factory':dict_row})
    worker = object.__new__(WorkerRepository)
    worker._pool = pool
    try:
        claim = worker.claim_next_alpha_campaign('worker_publication',30,'publication:claim')
        assert claim is not None and claim.job_id == 'job_publication'
        assert worker.start_attempt(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,
            ProcessIdentity(301,301,301,'d'*64),'publication:start',alpha_campaign=True)
        template = json.loads(_entry().canonical_event_text)
        entries, refs, heads = [], [], []
        for candidate in range(4):
            alpha_id = f'a{candidate}.source.fixture'
            heads.append({'alpha_id':alpha_id,'version':'1.0.0','sequence':0,'event_digest':None})
            predecessor = None
            stream_id = uuid5(NAMESPACE_URL, alpha_id)
            for sequence, status in ((1,'IDEA'),(2,'CANDIDATE')):
                event = json.loads(canonical_json_bytes(template))
                registry = json.loads(event['payload']['registry_event_text'])
                registry['record'].update(alpha_id=alpha_id,lifecycle_status=status)
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
        request_payload = {
            'schema_version':'p3-publication-request-v1','idempotency_key':'source-registration',
            'semantic_request_digest':hashlib.sha256(canonical_json_bytes(refs)).hexdigest(),
            'stage':'REGISTER','evidence_ref':evidence_ref,'expected_heads':heads,
            'proposed_event_refs':refs,'job_id':claim.job_id,
        }
        request_payload['digest'] = hashlib.sha256(canonical_json_bytes(request_payload)).hexdigest()
        request = PublicationRequest.model_validate_json(canonical_json_bytes(request_payload))
        publisher = P3PublicationRepository(pool,store)
        from dataclasses import replace
        try:
            publisher.publish(request,replace(claim,lease_token='z'*32),tuple(entries),trace_id='publication:stale')
        except psycopg.Error as error:
            assert error.sqlstate == 'P3D01'
        else:
            raise AssertionError('stale publication fence accepted')
        print('PUBLICATION_STALE_FENCE_PASS',flush=True)

        bad_entries = tuple(entries[:-1]) + (entries[-1].model_copy(update={
            'outbox_payload_text': canonical_json_bytes({'event_id':str(entries[0].event_id)}).decode(),
        }),)
        try:
            publisher.publish(request,claim,bad_entries,trace_id='publication:rollback')
        except psycopg.errors.InvalidParameterValue:
            pass
        else:
            raise AssertionError('wrong final outbox accepted')
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as inspector:
            for table in ('p3_alpha_heads','p3_alpha_job_commits','domain_events','event_outbox','event_append_idempotency'):
                assert inspector.execute(f'SELECT count(*) FROM public.{table}').fetchone() == (0,)
            assert inspector.execute('SELECT state FROM public.jobs WHERE job_id=%s',(claim.job_id,)).fetchone() == ('RUNNING',)
        print('PUBLICATION_MID_BATCH_ROLLBACK_PASS',flush=True)
        with psycopg.connect(host=str(sock),dbname=name,user='postgres',autocommit=True) as injector:
            injector.execute("CREATE FUNCTION public.p3_source_reject_terminal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.reason_code='P3_COMMITTED' THEN RAISE EXCEPTION 'source-test terminal failure'; END IF; RETURN NEW; END $$")
            injector.execute("CREATE TRIGGER p3_source_reject_terminal BEFORE INSERT ON public.job_events FOR EACH ROW EXECUTE FUNCTION public.p3_source_reject_terminal()")
            try:
                try:
                    publisher.publish(request,claim,tuple(entries),trace_id='publication:terminal-fault')
                except psycopg.errors.RaiseException as error:
                    assert 'source-test terminal failure' in str(error)
                else:
                    raise AssertionError('terminal fault did not abort publication')
                for table in ('p3_alpha_heads','p3_alpha_job_commits','domain_events','event_outbox','event_append_idempotency'):
                    assert injector.execute(f'SELECT count(*) FROM public.{table}').fetchone() == (0,)
                assert injector.execute('SELECT state FROM public.jobs WHERE job_id=%s',(claim.job_id,)).fetchone() == ('RUNNING',)
                assert injector.execute('SELECT to_state FROM public.job_events WHERE job_id=%s ORDER BY sequence DESC LIMIT 1',(claim.job_id,)).fetchone() == ('RUNNING',)
            finally:
                injector.execute('DROP TRIGGER p3_source_reject_terminal ON public.job_events')
                injector.execute('DROP FUNCTION public.p3_source_reject_terminal()')
        print('PUBLICATION_TERMINAL_FAILURE_ROLLBACK_PASS',flush=True)

        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)

        def contender():
            barrier.wait(timeout=5)
            return publisher.publish(request,claim,tuple(entries),trace_id='publication:concurrent')

        import time
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as locker:
            locker.execute('SELECT job_id FROM public.jobs WHERE job_id=%s FOR UPDATE',(claim.job_id,))
            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = executor.submit(contender), executor.submit(contender)
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        locker.execute('SELECT pg_stat_clear_snapshot()')
                        waiting = locker.execute("SELECT pid FROM pg_stat_activity WHERE usename='trading_job_worker' AND wait_event_type='Lock' AND query LIKE '%%worker_commit_alpha_campaign%%'").fetchall()
                        if len({row[0] for row in waiting}) == 2:
                            break
                        time.sleep(.01)
                    else:
                        raise AssertionError('two publication connections did not contend')
                finally:
                    locker.rollback()
                result = first.result(timeout=10)
                assert second.result(timeout=10) == result
        print('PUBLICATION_TWO_CONNECTIONS_PASS',flush=True)
        assert result.alpha_outcome == 'NOT_EVALUATED'
        assert publisher.publish(request,claim,tuple(entries),trace_id='publication:replay') == result
        conflict = request.model_dump(mode='json')
        conflict['semantic_request_digest'] = '0'*64
        conflict.pop('digest')
        conflict['digest'] = hashlib.sha256(canonical_json_bytes(conflict)).hexdigest()
        try:
            publisher.publish(PublicationRequest.model_validate_json(canonical_json_bytes(conflict)),claim,tuple(entries),trace_id='publication:conflict')
        except psycopg.errors.UniqueViolation:
            pass
        else:
            raise AssertionError('conflicting semantic request accepted')
        assert publisher.read_commit(request) == result
        print('PUBLICATION_IDEMPOTENCY_CONFLICT_PASS',flush=True)

        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as inspector:
            assert inspector.execute('SELECT count(*) FROM public.p3_alpha_heads').fetchone() == (4,)
            assert inspector.execute('SELECT count(*) FROM public.domain_events WHERE event_type=%s',('AlphaRegistryTransitionRecordedV1',)).fetchone() == (8,)
            assert inspector.execute('SELECT count(*) FROM public.event_outbox WHERE topic=%s',('p3.alpha-registry',)).fetchone() == (8,)
            assert inspector.execute('SELECT state,result_hash FROM public.jobs WHERE job_id=%s',(claim.job_id,)).fetchone() == ('SUCCEEDED',result.digest)
            assert inspector.execute('SELECT count(*) FROM public.p3_alpha_job_commits WHERE job_id=%s',(claim.job_id,)).fetchone() == (1,)
            assert inspector.execute('SELECT to_state,reason_code,attempt_id,metadata FROM public.job_events WHERE job_id=%s ORDER BY sequence DESC LIMIT 1',(claim.job_id,)).fetchone() == ('SUCCEEDED','P3_COMMITTED',claim.attempt_id,{'result_digest':result.digest})
        print('PUBLICATION_ATOMIC_REPLAY_PASS',flush=True)
    finally:
        worker.close()
