"""Real disposable PostgreSQL fixture execution and evidence, owned by the worker."""
import os
import signal
import time
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5
from datetime import UTC, datetime

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, URL
from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.alpha_lifecycle.registry import AlphaLifecycleStatus, AlphaRecordV1, QualificationDecision
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts import canonical_json_bytes
from packages.job_contracts import AlphaCampaignOperation, EnqueueJobRequest
from services.job_store.p3_publication_repository import P3PublicationRepository
from services.job_store.p3_sql import DomainAppendEntry
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.recovery import ProcessIdentity, ProcProcessInspector
from services.job_worker.process_runner import _session_members_proc, HeartbeatDecision, HeartbeatInstruction, _default_pidfd_api

ROOT = Path(__file__).resolve().parents[2]
BIN = Path('/usr/lib/postgresql/16/bin')
REQUIRED_SQL_CHECKS = frozenset({
    'SQL_PROCESS_IDENTITY_BOUND_PASS','FIXTURE_CLAIM_ISOLATION_PASS',
    'WORKER_ALPHA_RECOVERY_BLOCKED_PASS','FIXTURE_RECOVERY_ISOLATION_PASS',
    'MIGRATION_CHAIN_PASS','EMPTY_DOWNGRADE_POLICY_PASS','REPLACE_FENCE_PRIVILEGES_PASS',
    'TWO_CONNECTION_EXPIRED_LEASE_PASS','AUTHORIZATION_DIGEST_BINDING_PASS',
    'AUTHORIZATION_MANIFEST_BINDING_PASS','AUTHORIZATION_SOURCE_BINDING_PASS',
    'PUBLICATION_STALE_FENCE_PASS','PUBLICATION_WRONG_ATTEMPT_PASS','PUBLICATION_STALE_HEAD_PASS',
    'PUBLICATION_MID_BATCH_ROLLBACK_PASS','PUBLICATION_TERMINAL_FAILURE_ROLLBACK_PASS',
    'PUBLICATION_TWO_CONNECTIONS_PASS','PUBLICATION_IDEMPOTENCY_CONFLICT_PASS',
    'PUBLICATION_DUPLICATE_EVENT_PASS','PUBLICATION_CANCEL_BEFORE_PASS',
    'PUBLICATION_CANCEL_AFTER_PASS','PUBLICATION_LOST_COMMIT_RESPONSE_PASS',
    'PUBLICATION_ATOMIC_REPLAY_PASS',
})


def _alpha_request() -> EnqueueJobRequest:
    def ref(value: str) -> dict[str, object]:
        return {
            "content_sha256": value * 64,
            "size_bytes": 1,
            "media_type": "application/json",
            "locator": f"{value * 64}.blob",
        }
    return EnqueueJobRequest.model_validate({
        "job_type": "ALPHA_CAMPAIGN",
        "payload": {
            "schema_version": "p3-alpha-campaign-payload-v1",
            "operation": "BASELINES",
            "manifest_ref": ref("a"),
            "authorization_ref": ref("b"),
            "expected_source": {
                "commit_sha": "c" * 40,
                "tree_sha": "d" * 40,
                "closure_schema_version": "source-closure-v1",
                "closure_policy_sha256": "e" * 64,
                "closure_sha256": "f" * 64,
            },
            "logical_trial_id": "p3-baselines-v1",
        },
        "idempotency_key": "p3:baselines:1",
        "actor": {"actor_type": "OPERATOR", "actor_id": "operator-p3"},
    })

def _entry() -> DomainAppendEntry:
    event_id = UUID("11111111-1111-5111-8111-111111111111")
    stream_id = UUID("22222222-2222-5222-8222-222222222222")
    record = AlphaRecordV1(
        alpha_id="a0.test", version="1.0.0", source_sha="a" * 40,
        implementation_identity="packages.alpha_lifecycle.candidates:run_candidate",
        dataset_snapshot_sha256="b" * 64, feature_set=("close",),
        parameter_set_sha256="c" * 64,
        training_start_at="2020-01-01T00:00:00Z",
        training_end_at="2021-01-01T00:00:00Z",
        validation_start_at="2021-01-02T00:00:00Z",
        validation_end_at="2022-01-01T00:00:00Z",
        oos_start_at="2022-01-02T00:00:00Z",
        oos_end_at="2023-01-01T00:00:00Z",
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


def _wait_for_lock(connection, role, query_pattern, count=1, *, wait_event=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        connection.execute('SELECT pg_stat_clear_snapshot()')
        rows = connection.execute("SELECT pid FROM pg_stat_activity WHERE usename=%s AND wait_event_type='Lock' AND query LIKE %s AND (%s::text IS NULL OR wait_event=%s)",(role,query_pattern,wait_event,wait_event)).fetchall()
        if len({row[0] for row in rows}) >= count:
            return
        time.sleep(.01)  # Poll observed locks; elapsed time never establishes the barrier.
    raise AssertionError('required SQL lock barrier was not observed')

def check_publication(sock, name, root, payload, mark):
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
        assert worker.claim_next_alpha_campaign('worker_fixture',30,'fixture:skip-research',fixture_only=True) is None
        mark('FIXTURE_CLAIM_ISOLATION_PASS')
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
        mark('PUBLICATION_STALE_FENCE_PASS',flush=True)
        try:
            publisher.publish(request,replace(claim,attempt_id='attempt_wrong'),tuple(entries),trace_id='publication:wrong-attempt')
        except psycopg.Error as error:
            assert error.sqlstate == 'P3D01'
        else:
            raise AssertionError('wrong publication attempt accepted')
        mark('PUBLICATION_WRONG_ATTEMPT_PASS')
        wrong_head = request.model_dump(mode='json')
        wrong_head['expected_heads'][0].update(sequence=1,event_digest='0'*64)
        wrong_head.pop('digest')
        wrong_head['digest'] = hashlib.sha256(canonical_json_bytes(wrong_head)).hexdigest()
        try:
            publisher.publish(PublicationRequest.model_validate_json(canonical_json_bytes(wrong_head)),claim,tuple(entries),trace_id='publication:stale-head')
        except psycopg.errors.SerializationFailure:
            pass
        else:
            raise AssertionError('stale registry head accepted')
        mark('PUBLICATION_STALE_HEAD_PASS')
        duplicate_event = json.loads(entries[-1].canonical_event_text)
        duplicate_event['event_id'] = str(entries[0].event_id)
        duplicate = entries[-1].model_copy(update={
            'event_id':entries[0].event_id,'canonical_event_text':canonical_json_bytes(duplicate_event).decode(),
            'outbox_payload_text':canonical_json_bytes({'event_id':str(entries[0].event_id)}).decode(),
        })
        try:
            publisher.publish(request,claim,(*entries[:-1],duplicate),trace_id='publication:duplicate-event')
        except psycopg.errors.UniqueViolation:
            pass
        else:
            raise AssertionError('duplicate publication event accepted')
        mark('PUBLICATION_DUPLICATE_EVENT_PASS')


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
        mark('PUBLICATION_MID_BATCH_ROLLBACK_PASS',flush=True)
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
        mark('PUBLICATION_TERMINAL_FAILURE_ROLLBACK_PASS',flush=True)

        # Cancellation owns the job lock before publication enters its capability.
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as api:
            api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
                ('job_cancel_before',raw.decode(),hashlib.sha256(raw).hexdigest(),'cancel-before','source-test',0,'cancel:enqueue','event_cancel_enqueue'))
        cancel_claim = worker.claim_next_alpha_campaign('worker_cancel',30,'cancel:claim')
        assert cancel_claim is not None and cancel_claim.job_id == 'job_cancel_before'
        assert worker.start_attempt(cancel_claim.job_id,cancel_claim.attempt_id,cancel_claim.worker_id,
            cancel_claim.lease_token,ProcessIdentity(401,401,401,'e'*64),'cancel:start',alpha_campaign=True)
        cancel_body = request.model_dump(mode='json',exclude={'digest'})
        cancel_body.update(job_id=cancel_claim.job_id,idempotency_key='cancel-before')
        cancel_body['digest'] = hashlib.sha256(canonical_json_bytes(cancel_body)).hexdigest()
        cancel_request = PublicationRequest.model_validate_json(canonical_json_bytes(cancel_body))
        from concurrent.futures import ThreadPoolExecutor
        with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as api, psycopg.connect(host=str(sock),dbname=name,user='postgres') as observer:
            assert api.execute('SELECT * FROM job_plane.api_cancel_alpha_campaign(%s,%s,%s,%s)',
                (cancel_claim.job_id,'source-test','cancel:before','event_cancel_before')).fetchone()[1:] == ('CANCEL_REQUESTED',True)
            with ThreadPoolExecutor(max_workers=1) as executor:
                blocked = executor.submit(publisher.publish,cancel_request,cancel_claim,tuple(entries),trace_id='publication:cancel-before')
                try:
                    _wait_for_lock(observer,'trading_job_worker','%worker_commit_alpha_campaign%')
                finally:
                    api.commit()
                try:
                    blocked.result(timeout=10)
                except psycopg.Error as error:
                    assert error.sqlstate == 'P3D02'
                else:
                    raise AssertionError('publication passed cancellation that held the job lock')
            assert observer.execute('SELECT count(*) FROM public.p3_alpha_job_commits WHERE job_id=%s',(cancel_claim.job_id,)).fetchone() == (0,)
        mark('PUBLICATION_CANCEL_BEFORE_PASS')
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(cancel_claim.job_id,))
            owner.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE attempt_id=%s",(cancel_claim.attempt_id,))
        assert worker.recover_expired_leases(ProcProcessInspector(),recovery_id='worker-startup-recovery',alpha_campaign=True,fixture_only=True) == ()
        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
            assert owner.execute('SELECT state FROM public.jobs WHERE job_id=%s',(cancel_claim.job_id,)).fetchone() == ('CANCEL_REQUESTED',)
        mark('FIXTURE_RECOVERY_ISOLATION_PASS')

        assert worker.heartbeat_control(claim.job_id,claim.attempt_id,claim.worker_id,claim.lease_token,30,alpha_campaign=True) == 'CONTINUE'


        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)

        from contextlib import contextmanager
        from threading import Lock
        class DisconnectOncePool:
            def __init__(self):
                self.pending = True
                self.disconnected = False
                self.lock = Lock()

            @contextmanager
            def connection(self):
                with self.lock:
                    disconnect = self.pending
                    self.pending = False
                with psycopg.connect(host=str(sock),dbname=name,user='trading_job_worker',row_factory=dict_row) as connection:
                    yield connection
                    if disconnect:
                        # Real SQL COMMIT has completed. Discard the client result and
                        # close the actual connection to exercise durable reconciliation.
                        connection.close()
                        self.disconnected = True
                        raise psycopg.OperationalError('fixture injected disconnect after COMMIT')

        disconnect_pool = DisconnectOncePool()
        publisher = P3PublicationRepository(disconnect_pool,store)

        def contender():
            barrier.wait(timeout=5)
            return publisher.publish(request,claim,tuple(entries),trace_id='publication:concurrent')

        def cancel_after():
            with psycopg.connect(host=str(sock),dbname=name,user='trading_job_api') as api:
                return api.execute('SELECT * FROM job_plane.api_cancel_alpha_campaign(%s,%s,%s,%s)',
                    (claim.job_id,'source-test','cancel:after','event_cancel_after')).fetchone()

        with psycopg.connect(host=str(sock),dbname=name,user='postgres',autocommit=True) as gate:
            gate.execute("CREATE FUNCTION public.p3_source_commit_barrier() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.reason_code='P3_COMMITTED' THEN PERFORM pg_advisory_xact_lock(706003); END IF; RETURN NEW; END $$")
            gate.execute('CREATE TRIGGER p3_source_commit_barrier BEFORE INSERT ON public.job_events FOR EACH ROW EXECUTE FUNCTION public.p3_source_commit_barrier()')
            gate.execute('SELECT pg_advisory_lock(706003)')
            try:
                with psycopg.connect(host=str(sock),dbname=name,user='postgres') as locker:
                    locker.execute('SELECT job_id FROM public.jobs WHERE job_id=%s FOR UPDATE',(claim.job_id,))
                    with ThreadPoolExecutor(max_workers=3) as executor:
                        first, second = executor.submit(contender), executor.submit(contender)
                        try:
                            _wait_for_lock(gate,'trading_job_worker','%worker_commit_alpha_campaign%',2)
                            locker.rollback()
                            _wait_for_lock(gate,'trading_job_worker','%worker_commit_alpha_campaign%',wait_event='advisory')
                            cancelled = executor.submit(cancel_after)
                            _wait_for_lock(gate,'trading_job_api','%api_cancel_alpha_campaign%')
                        finally:
                            locker.rollback()
                            gate.execute('SELECT pg_advisory_unlock(706003)')
                        result = first.result(timeout=10)
                        assert second.result(timeout=10) == result
                        assert cancelled.result(timeout=10)[1:] == ('SUCCEEDED',False)
            finally:
                gate.execute('SELECT pg_advisory_unlock(706003)')
                gate.execute('DROP TRIGGER p3_source_commit_barrier ON public.job_events')
                gate.execute('DROP FUNCTION public.p3_source_commit_barrier()')
        assert disconnect_pool.disconnected
        mark('PUBLICATION_LOST_COMMIT_RESPONSE_PASS')
        mark('PUBLICATION_CANCEL_AFTER_PASS')
        mark('PUBLICATION_TWO_CONNECTIONS_PASS',flush=True)
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
        mark('PUBLICATION_IDEMPOTENCY_CONFLICT_PASS',flush=True)

        with psycopg.connect(host=str(sock),dbname=name,user='postgres') as inspector:
            assert inspector.execute('SELECT count(*) FROM public.p3_alpha_heads').fetchone() == (4,)
            assert inspector.execute('SELECT count(*) FROM public.domain_events WHERE event_type=%s',('AlphaRegistryTransitionRecordedV1',)).fetchone() == (8,)
            assert inspector.execute('SELECT count(*) FROM public.event_outbox WHERE topic=%s',('p3.alpha-registry',)).fetchone() == (8,)
            assert inspector.execute('SELECT state,result_hash FROM public.jobs WHERE job_id=%s',(claim.job_id,)).fetchone() == ('SUCCEEDED',result.digest)
            assert inspector.execute('SELECT count(*) FROM public.p3_alpha_job_commits WHERE job_id=%s',(claim.job_id,)).fetchone() == (1,)
            assert inspector.execute('SELECT to_state,reason_code,attempt_id,metadata FROM public.job_events WHERE job_id=%s ORDER BY sequence DESC LIMIT 1',(claim.job_id,)).fetchone() == ('SUCCEEDED','P3_COMMITTED',claim.attempt_id,{'result_digest':result.digest})
        mark('PUBLICATION_ATOMIC_REPLAY_PASS',flush=True)
    finally:
        worker.close()


class SQLFixtureCleanupError(RuntimeError):
    cleanup_unverified = True


class SQLFixtureStopped(RuntimeError):
    def __init__(self, instruction):
        decision = instruction.decision if isinstance(instruction,HeartbeatInstruction) else instruction
        super().__init__(f"SQL fixture heartbeat stopped: {decision}")
        self.control = decision
        self.reason_code = getattr(instruction,"reason_code",None)


def _cleanup_cluster(root: Path, data: Path, sock: Path, started: bool, run, *, process=None, identity=None) -> None:
    stop_error = None
    if started and process is not None:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
        if (data/"postmaster.pid").exists() or tuple(sock.glob(".s.PGSQL.*")):
            raise RuntimeError(f"shutdown unverified; owned cluster retained at {root}")
    elif started:
        try:
            run([str(BIN/'pg_ctl'), '-D', str(data), '-w', '-m', 'fast', 'stop'])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            stop_error = error
        if (data/'postmaster.pid').exists() or tuple(sock.glob('.s.PGSQL.*')):
            raise RuntimeError(f"shutdown unverified; owned cluster retained at {root}") from stop_error
    if process is not None:
        process.wait(timeout=10)
        if identity is None or _session_members_proc(identity):
            raise RuntimeError(f"PostgreSQL session cleanup unverified; owned cluster retained at {root}")
    shutil.rmtree(root)
    if root.exists():
        raise RuntimeError(f"owned cluster cleanup incomplete at {root}")
    if stop_error is not None:
        raise stop_error
    print('OWNED_CLUSTER_CLEANUP_PASS', flush=True)

def run_sql_fixture(source, *, progress=lambda: None, heartbeat=None, owned_root=None) -> dict:
    if not __debug__:
        raise RuntimeError("source checks require assertions enabled")
    root = Path(tempfile.mkdtemp(prefix='p3-source-pg-', dir='/tmp')) if owned_root is None else owned_root
    if len(os.fsencode(root/"socket")) > 90:
        raise ValueError("fixture private root is too long for a Unix socket")
    if owned_root is not None:
        root.mkdir(mode=0o700,exist_ok=False)
    data = root / 'data'
    sock = root / 'socket'
    sock.mkdir(mode=0o700)
    name = 'trading_agent_disposable_test'  # Legacy reviewed catalog name, newly owned isolated cluster.
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LC_ALL': 'C', 'TZ': 'UTC'}
    started = False
    process = identity = None
    checks = []
    started_at = datetime.now(UTC).isoformat()

    def mark(name, *, flush=True):
        checks.append(name)
        print(name, flush=flush)
        if heartbeat is not None and identity is not None:
            instruction = heartbeat(identity)
            decision = instruction.decision if isinstance(instruction,HeartbeatInstruction) else instruction
            if decision != HeartbeatDecision.CONTINUE:
                raise SQLFixtureStopped(instruction)
        progress()

    def run(args, raw=None):
        result = subprocess.run(args, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=60)
        if result.returncode:
            raise RuntimeError(f'{Path(args[0]).name} failed with code {result.returncode}')
        return result

    def psql(raw, database='postgres'):
        run([str(BIN/'psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', str(sock),
             '-U', 'postgres', '-d', database], raw.encode())

    def upgrade(revision):
        engine = create_engine(URL.create('postgresql+psycopg', username='trading_owner',
            database=name, query={'host': str(sock)}))
        try:
            with engine.begin() as connection:
                cfg = Config(str(ROOT/'alembic.ini'))
                cfg.attributes['connection'] = connection
                command.upgrade(cfg, revision)
        finally:
            engine.dispose()
        mark(f'UPGRADE_{revision}_PASS', flush=True)

    try:
        run([str(BIN/'initdb'), '-D', str(data), '-U', 'postgres', '--auth-local=trust', '--auth-host=reject', '--no-locale', '--encoding=UTF8'])
        pidfd_open, pidfd_signal = _default_pidfd_api()
        with (root/'postgres.log').open('xb') as log:
            process = subprocess.Popen([str(BIN/'postgres'),'-D',str(data),'-k',str(sock),
                '-c',"listen_addresses=",'-c','log_min_error_statement=panic'],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,start_new_session=True)
        started = True
        inspector = ProcProcessInspector()
        identity = inspector.inspect(process.pid)
        descriptor = pidfd_open(process.pid,0)
        try:
            if (identity is None or identity.pid != identity.process_group
                or inspector.inspect(process.pid) != identity or process.poll() is not None):
                raise RuntimeError('PostgreSQL process identity changed before fencing')
            pidfd_signal(descriptor,0,None,0)
            mark('SQL_PROCESS_IDENTITY_BOUND_PASS' if heartbeat is not None else 'SQL_PROCESS_IDENTITY_OBSERVED_PASS')
            deadline = time.monotonic() + 30
            while True:
                ready = subprocess.run([str(BIN/'pg_isready'),'-h',str(sock),'-U','postgres'],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=3)
                if ready.returncode == 0:
                    break
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('PostgreSQL fixture startup failed')
                progress()
                time.sleep(.05)
        finally:
            os.close(descriptor)
        passwords = {role: secrets.token_hex(24) for role in ('owner','migrator','reader','jobs')}
        prefix = ''.join(f"\\set {role}_password '{password}'\n" for role,password in passwords.items())
        base = (ROOT/'ops/postgres/provision-roles.sql').read_text().replace('trading_agent', name)
        psql(prefix + base)
        upgrade('0004_durable_research_jobs')
        role_sql = (ROOT/'ops/postgres/provision-job-roles.sql').read_text().replace('trading_agent', name)
        for role in ('trading_job_api','trading_job_worker','trading_job_scheduler'):
            password = secrets.token_hex(24)
            marker = f'\\password {role}\n'
            assert role_sql.count(marker) == 1
            role_sql = role_sql.replace(marker, marker + password + '\n' + password + '\n')
        psql("SET log_min_error_statement='panic'; SET track_activities=off;\n" + role_sql, name)
        upgrade('0019_p2_security_master')
        psql('CREATE ROLE trading_p3_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; GRANT trading_p3_owner TO trading_owner;', name)
        upgrade('0020_p3_alpha_campaign_authority')
        mark('MIGRATION_CHAIN_PASS', flush=True)
        engine = create_engine(URL.create('postgresql+psycopg',username='trading_owner',database=name,query={'host':str(sock)}))
        try:
            try:
                with engine.begin() as connection:
                    cfg = Config(str(ROOT/'alembic.ini'))
                    cfg.attributes['connection'] = connection
                    command.downgrade(cfg,'0019_p2_security_master')
            except RuntimeError as error:
                assert str(error) == '0020 P3 alpha campaign authority is forward-only; use a reviewed forward repair'
            else:
                raise AssertionError('unapproved empty downgrade accepted')
        finally:
            engine.dispose()
        with psycopg.connect(host=str(sock),dbname=name,user='trading_owner') as owner:
            assert owner.execute('SELECT version_num FROM alembic_version').fetchone() == ('0020_p3_alpha_campaign_authority',)
        mark('EMPTY_DOWNGRADE_POLICY_PASS')

        payload = _alpha_request().payload.model_copy(update={"expected_source": source, "operation": AlphaCampaignOperation.PARITY, "logical_trial_id": "p3-integration-fixture-v1"})
        authorization = canonical_json_bytes({"purpose": "disposable-source-test"})
        authorization_digest = hashlib.sha256(authorization).hexdigest()
        payload = payload.model_copy(update={"authorization_ref": payload.authorization_ref.model_copy(update={
            "content_sha256": authorization_digest, "size_bytes": len(authorization),
            "locator": authorization_digest + ".blob",
        })})
        raw = canonical_json_bytes(payload).decode()
        with psycopg.connect(host=str(sock), dbname=name, user="trading_owner") as owner:
            owner.execute("SET ROLE trading_p3_owner")
            try:
                with owner.transaction():
                    owner.execute("INSERT INTO public.p3_campaign_authorizations(request_digest,input_set_digest,source_commit_sha,source_identity_text,operation,expires_at,authorization_text) VALUES (%s,%s,%s,%s,'PARITY',clock_timestamp()+interval '1 hour','{}')", ("b"*64,"a"*64,source.commit_sha,canonical_json_bytes(payload.expected_source).decode()))
            except psycopg.errors.CheckViolation:
                pass
            else:
                raise AssertionError("authorization digest mismatch accepted")
            owner.execute("INSERT INTO public.p3_campaign_authorizations(request_digest,input_set_digest,source_commit_sha,source_identity_text,operation,expires_at,authorization_text) VALUES (%s,%s,%s,%s,'PARITY',clock_timestamp()+interval '1 hour',%s)", (authorization_digest,"a"*64,source.commit_sha,canonical_json_bytes(payload.expected_source).decode(),authorization.decode()))
        mark('AUTHORIZATION_DIGEST_BINDING_PASS', flush=True)
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            row = api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("job_fixture",raw,hashlib.sha256(raw.encode()).hexdigest(),"fixture-1","source-test",0,"fixture:enqueue","event_enqueue")).fetchone()
            assert row == ("job_fixture", "ENQUEUED"), row
        mark('API_ENQUEUE_PASS', flush=True)
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as worker:
            row = worker.execute("SELECT * FROM job_plane.worker_claim_alpha_campaign(%s,%s,%s,%s,%s,%s)",
                ("attempt_fixture","worker_fixture","t"*32,30,"fixture:claim","event_claim")).fetchone()
            assert row is not None and row[0] == "job_fixture", row
        mark('WORKER_CLAIM_PASS', flush=True)
        from concurrent.futures import ThreadPoolExecutor
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as worker:
            row = worker.execute("SELECT job_plane.worker_start_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("job_fixture","attempt_fixture","worker_fixture","t"*32,100,100,100,"d"*64,"fixture:start","event_start")).fetchone()
            assert row == (True,), row
        mark('WORKER_START_PASS', flush=True)
        replacement = "SELECT job_plane.worker_replace_alpha_fixture_process(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        params = ("job_fixture","attempt_fixture","worker_fixture","t"*32,100,100,100,"d"*64,101,101,101,"e"*64)
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as worker:
            assert worker.execute(replacement, params).fetchone() == (True,)
            assert worker.execute(replacement, params).fetchone() == (False,)
            assert worker.execute(replacement, (*params[:3],"z"*32,*params[4:])).fetchone() == (False,)
            assert worker.execute(replacement, (params[0],"wrong_attempt",*params[2:])).fetchone() == (False,)
            for index in range(4):
                malformed = list(params)
                malformed[index] = "bad space"
                try:
                    with worker.transaction():
                        worker.execute(replacement, malformed)
                except psycopg.errors.InvalidParameterValue:
                    pass
                else:
                    raise AssertionError('malformed authority accepted')
            try:
                with worker.transaction():
                    worker.execute("UPDATE public.job_attempts SET child_pid=777 WHERE attempt_id='attempt_fixture'")
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError('direct worker table write accepted')
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            try:
                with api.transaction():
                    api.execute(replacement, params)
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError('API role acquired worker capability')
        mark('REPLACE_FENCE_PRIVILEGES_PASS', flush=True)
        with psycopg.connect(host=str(sock), dbname=name, user="postgres") as locker, psycopg.connect(host=str(sock), dbname=name, user="trading_job_worker") as contender:
            contender.execute("SET statement_timeout='5s'")
            contender.commit()
            assert locker.info.backend_pid != contender.info.backend_pid
            locker.execute("SELECT job_id FROM public.jobs WHERE job_id='job_fixture' FOR UPDATE")
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: contender.execute(replacement,
                    (*params[:4],101,101,101,"e"*64,102,102,102,"f"*64)).fetchone())
                try:
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline:
                        waiting = locker.execute("SELECT wait_event_type FROM pg_catalog.pg_stat_activity WHERE pid=%s", (contender.info.backend_pid,)).fetchone()
                        if waiting == ('Lock',):
                            break
                        locker.execute("SELECT pg_stat_clear_snapshot()")
                        time.sleep(.01)
                    else:
                        raise AssertionError('second connection did not block on row lock')
                    locker.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id='job_fixture'")
                    locker.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE attempt_id='attempt_fixture'")
                    locker.commit()
                    assert future.result(timeout=5) == (False,)
                finally:
                    locker.rollback()
        mark('TWO_CONNECTION_EXPIRED_LEASE_PASS', flush=True)
        bad_payload = payload.model_copy(update={"manifest_ref": payload.manifest_ref.model_copy(update={"content_sha256":"f"*64,"locator":"f"*64+".blob"})})
        bad_raw = canonical_json_bytes(bad_payload).decode()
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            try:
                with api.transaction():
                    api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                        ("job_bad_manifest",bad_raw,hashlib.sha256(bad_raw.encode()).hexdigest(),"bad-manifest","source-test",0,"fixture:bad","event_bad_manifest"))
            except psycopg.errors.InvalidParameterValue:
                pass
            else:
                raise AssertionError('unapproved manifest accepted by SQL authority')
        mark('AUTHORIZATION_MANIFEST_BINDING_PASS', flush=True)
        for field, value in (("tree_sha", "0"*40), ("closure_schema_version", "wrong-v1"),
                             ("closure_policy_sha256", "0"*64), ("closure_sha256", "0"*64)):
            bad_source = payload.expected_source.model_copy(update={field: value})
            bad_raw = canonical_json_bytes(payload.model_copy(update={"expected_source": bad_source})).decode()
            with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
                try:
                    with api.transaction():
                        api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                            ("job_bad_source",bad_raw,hashlib.sha256(bad_raw.encode()).hexdigest(),"bad-source","source-test",0,"fixture:bad","event_bad_source"))
                except psycopg.errors.InvalidParameterValue:
                    pass
                else:
                    raise AssertionError(f'unapproved source {field} accepted by SQL authority')
        mark('AUTHORIZATION_SOURCE_BINDING_PASS', flush=True)

        from services.job_worker.artifacts import ArtifactMetadata
        with psycopg.connect(host=str(sock), dbname=name, user="trading_job_api") as api:
            api.execute("SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("job_final",raw,hashlib.sha256(raw.encode()).hexdigest(),"fixture-final","source-test",0,"fixture:final","event_final_enqueue"))
        # The socket-only source fixture injects a real pool; runtime settings remain loopback-only.
        repository = object.__new__(WorkerRepository)
        repository._pool = ConnectionPool(make_conninfo(host=str(sock), dbname=name, user="trading_job_worker"), min_size=1, max_size=2, kwargs={"row_factory":dict_row})
        try:
            repository.assert_p3_runtime_identity()
            claimed = repository.claim_next_alpha_campaign("worker_fixture",30,"fixture:final-claim")
            assert claimed is not None and claimed.job_id == "job_final"
            assert repository.start_attempt(claimed.job_id,claimed.attempt_id,claimed.worker_id,
                claimed.lease_token,ProcessIdentity(201,201,201,"d"*64),"fixture:final-start",alpha_campaign=True)
            repository.worker_heartbeat(claimed.worker_id,source.commit_sha,"BUSY",current_job_id=claimed.job_id,
                current_attempt_id=claimed.attempt_id,metadata={})
            mark('WORKER_BUSY_HEARTBEAT_PASS', flush=True)
            artifact = ArtifactMetadata("stdout",f"{claimed.job_id}/{claimed.attempt_id}/stdout.log","a"*64,3,"application/octet-stream",False)
            assert repository.finalize(claimed.job_id,claimed.attempt_id,claimed.worker_id,claimed.lease_token,
                expected_state="RUNNING",expected_attempt_outcome="RUNNING",final_state="SUCCEEDED",
                reason_code="RESULT_VALIDATED",trace_id="fixture:final-result",exit_code=0,result_hash="a"*64,
                artifacts=(artifact,),alpha_campaign=True)
            # Capture an actual process identity, then prove its absence before recovery.
            with subprocess.Popen(['/bin/cat'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,start_new_session=True) as child:
                absent_identity = ProcProcessInspector().inspect(child.pid)
                assert absent_identity is not None
                child.stdin.close()
                child.wait(timeout=5)
            assert ProcProcessInspector().inspect(absent_identity.pid) is None
            with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                owner.execute("UPDATE public.job_attempts SET child_pid=%s,process_group_id=%s,process_start_ticks=%s,command_fingerprint=%s WHERE attempt_id='attempt_fixture'",
                    (absent_identity.pid,absent_identity.process_group,absent_identity.start_ticks,absent_identity.command_fingerprint))
            try:
                repository.recover_expired_leases(ProcProcessInspector(),recovery_id='p3-fixture-startup-recovery',alpha_campaign=True,fixture_only=True)
            except psycopg.errors.InvalidParameterValue:
                pass
            else:
                raise AssertionError('unapproved SQL recovery identity accepted')
            recovered = repository.recover_expired_leases(ProcProcessInspector(),recovery_id='worker-startup-recovery',alpha_campaign=True,fixture_only=True)
            assert ('job_fixture','P3_FIXTURE_CLEANUP_UNVERIFIED') in recovered
            with psycopg.connect(host=str(sock),dbname=name,user='postgres') as owner:
                assert owner.execute("SELECT state FROM public.jobs WHERE job_id='job_fixture'").fetchone() == ('BLOCKED',)
            mark('WORKER_ALPHA_RECOVERY_BLOCKED_PASS')
        finally:
            repository.close()
        with psycopg.connect(host=str(sock), dbname=name, user="postgres") as owner:
            assert owner.execute("SELECT state,result_hash FROM public.jobs WHERE job_id='job_final'").fetchone() == ('SUCCEEDED','a'*64)
            assert owner.execute("SELECT count(*) FROM public.job_artifacts WHERE job_id='job_final'").fetchone() == (1,)
        mark('WORKER_DURABLE_RESULT_PASS', flush=True)
        check_publication(sock, name, root, payload, mark)



    finally:
        primary_error = sys.exception()
        try:
            _cleanup_cluster(root, data, sock, started, run, process=process, identity=identity)
        except BaseException as cleanup_error:
            cause = (BaseExceptionGroup("source check and cleanup failed", [primary_error, cleanup_error])
                     if primary_error is not None else cleanup_error)
            raise SQLFixtureCleanupError(f"SQL cleanup unverified; retained {root}") from cause
    return {"source": source.model_dump(mode="json"), "checks": checks,
            "postgres_binary_sha256": hashlib.sha256((BIN/'postgres').read_bytes()).hexdigest(),
            "started_at": started_at, "finished_at": datetime.now(UTC).isoformat(),
            "cleanup": {"cluster_id": root.name, "root_absent": not root.exists(),
                        "server_stopped": True}}
