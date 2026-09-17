"""Private HOLDOUT claim checks in the existing disposable SQL source harness."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4, uuid5

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, create_engine, text
from typing_extensions import override

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.job_contracts import JobState
from .recovery import ProcessIdentity
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .p3_holdout_fixture import _inputs, _seed, CONSUME
from .p3_operation_fixture import _rejected

REVISION = '0030_p3_session_terminal_fences'
CLAIM = 'SELECT * FROM job_plane.worker_claim_session_holdout(%s,%s,%s,%s,%s,%s,%s,%s,%s)'


def check_session_holdout(sock: Path, name: str, source: SourceIdentity) -> dict[str, object]:
    engine = create_engine(URL.create('postgresql+psycopg', username='trading_owner',
        database=name, query={'host': str(sock)}))
    try:
        with engine.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[2]/'alembic.ini'))
            config.attributes['connection'] = connection
            command.upgrade(config, '0029_p3_session_holdout_claim')
            with _rejected(RuntimeError, match='parent authority catalog differs'):
                with connection.begin_nested():
                    connection.execute(text('SET LOCAL ROLE trading_p3_owner'))
                    connection.execute(text('GRANT UPDATE ON public.p3_alpha_heads TO trading_job_worker'))
                    connection.execute(text('RESET ROLE'))
                    command.upgrade(config, REVISION)
            command.upgrade(config, REVISION)
            from services.job_store.p3_catalog import SESSION_CATALOG_SQL, SESSION_CATALOG_SHA256
            connection.execute(text("SET LOCAL search_path=pg_catalog"))
            assert connection.execute(text(SESSION_CATALOG_SQL)).scalar() == SESSION_CATALOG_SHA256
    finally:
        engine.dispose()

    class SocketSettings(JobStoreSettings):
        @override
        def conninfo(self) -> str:
            return make_conninfo(host=str(sock), dbname=name, user=self.user)

    def seed(*, expiry: datetime | None = None) -> tuple[str, tuple[str, ...]]:
        label = 'session_'+uuid4().hex[:12]
        authorization, graph = _inputs(source, epoch='synthetic.'+label, material=label, expiry=expiry)
        job, _, _, _ = _seed(sock, name, source, label, authorization, legacy_claim=False)
        return job, graph

    settings = SocketSettings('localhost', 5432, name, 'trading_job_worker', 'synthetic')
    from apps.job_api.app import _probe_repository
    from services.job_store.repository import JobRepository
    api_settings = SocketSettings('localhost', 5432, name, 'trading_job_api', 'synthetic')
    with JobRepository(api_settings) as api_repository:
        assert _probe_repository(api_repository, REVISION, session=True) == (True, True)
        # A real catalog mutation must invalidate the already-created API profile.
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            _ = owner.execute('GRANT UPDATE ON public.p3_alpha_heads TO trading_job_worker')
        try:
            assert _probe_repository(api_repository, REVISION, session=True) == (True, False)
        finally:
            with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                _ = owner.execute('REVOKE UPDATE ON public.p3_alpha_heads FROM trading_job_worker')
        assert _probe_repository(api_repository, REVISION, session=True) == (True, True)
    with WorkerRepository(settings) as repository:
        _check_terminal_boundaries(sock, name, source, repository)
        repository.assert_session_runtime_identity()
        with _rejected(RuntimeError):
            repository.assert_p3_runtime_identity()
        job, graph = seed()
        assert repository.claim_next_alpha_campaign('session-worker', 30, 'test:ordinary',
            fixture_only=False, job_id=job) is None
        for run_id, attempt in ((2, 1), (1, 2)):
            assert repository.claim_session_holdout('session-worker', 30, 'test:wrong-workflow',
                job_id=job, workflow_run_id=run_id, workflow_attempt=attempt) is None

        parameters = ('attempt_private', 'session-worker', 'x'*32, 30, 'test:private',
            'event_private', job, 1, 1)
        for role in ('trading_job_api', 'trading_job_scheduler', 'trading_reader',
                     'trading_p3_authority', 'trading_p3_custodian'):
            with psycopg.connect(host=str(sock), dbname=name, user=role, autocommit=True) as other:
                with _rejected(psycopg.errors.InsufficientPrivilege):
                    _ = other.execute(CLAIM, parameters)
        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker', autocommit=True) as worker:
            for index in range(9):
                malformed: list[object] = list(parameters)
                malformed[index] = None
                with _rejected(psycopg.errors.InvalidParameterValue):
                    _ = worker.execute(CLAIM, malformed)

        # Claiming a locked job must leave it queued, rather than change scope.
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as locker:
            _ = locker.execute('SELECT job_id FROM public.jobs WHERE job_id=%s FOR UPDATE', (job,))
            assert repository.claim_session_holdout('session-worker', 30, 'test:locked',
                job_id=job, workflow_run_id=1, workflow_attempt=1) is None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(repository.claim_session_holdout, 'session-worker', 30, 'test:race',
                job_id=job, workflow_run_id=1, workflow_attempt=1) for _ in range(2)]
            claims = [value for future in futures if (value := future.result()) is not None]
        assert len(claims) == 1
        claimed = claims[0]
        assert claimed.job_id == job and claimed.attempt_number == claimed.max_attempts == 1
        assert repository.claim_session_holdout('session-worker', 30, 'test:repeat',
            job_id=job, workflow_run_id=1, workflow_attempt=1) is None
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as observer:
            assert observer.execute('SELECT count(*) FROM public.job_attempts WHERE job_id=%s', (job,)).fetchone() == (1,)
        # This disclosure uses the actual capability-created claim. No owner
        # state update or attempt insert is used; no plaintext is loaded.
        args = (claimed.job_id, claimed.attempt_id, claimed.worker_id, claimed.lease_token, *graph, 'test:session-consume')
        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker', autocommit=True) as worker:
            accepted = worker.execute(CONSUME, args).fetchone()
            assert accepted is not None
            # Same-claim metadata reconciliation is idempotent; a changed trace
            # cannot obtain another disclosure binding. This never releases bytes.
            assert worker.execute(CONSUME, args).fetchone() == accepted
            with _rejected(psycopg.errors.UniqueViolation):
                _ = worker.execute(CONSUME, (*args[:-1], 'test:another-disclosure'))
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as observer:
            assert observer.execute('SELECT count(*) FROM public.p3_holdout_disclosures WHERE job_id=%s', (job,)).fetchone() == (1,)

        from packages.alpha_lifecycle.contracts.authority import CustodyRecord, HoldoutRequest
        from packages.alpha_lifecycle.custody import ReleaseRequest
        from services.job_store.p3_custodian_release import CustodianReleaseRepository
        from packages.engine_contracts.serialization import canonical_json_bytes
        custody = CustodyRecord.model_validate_json(graph[-1])
        request = HoldoutRequest.model_validate_json(graph[0])
        release = ReleaseRequest.model_validate_json(canonical_json_bytes(dict(
            schema_version='p3-holdout-release-request-v1', source=source,
            job_id=claimed.job_id, attempt_id=claimed.attempt_id, worker_id=claimed.worker_id,
            authorization_digest=accepted[0], intent_digest=accepted[1], holdout_request_sha256=accepted[2],
            custody_record_ref=request.custody_record_ref, holdout_commitment=custody.holdout_commitment,
            plaintext_bundle_digest=custody.plaintext_bundle_digest, custodian_identity=custody.custodian_identity,
            research_identity=custody.research_identity, custodian_uid=17001, research_uid=17002,
            row_inventory_digest='9'*64)))
        custodian_settings = SocketSettings('localhost', 5432, name, 'trading_p3_custodian', 'synthetic')
        with _rejected(ValueError):
            CustodianReleaseRepository(custodian_settings).claim(release)
        custodian = CustodianReleaseRepository(custodian_settings, session=True)
        custodian.claim(release)
        custodian.fence(release)
        with _rejected(psycopg.errors.UniqueViolation):
            custodian.claim(release)

        identity = ProcessIdentity(72001, 72001, 101, 'd'*64)
        ids = (claimed.job_id, claimed.attempt_id, claimed.worker_id, claimed.lease_token)
        assert repository.pre_spawn_control(*ids, 30, alpha_campaign=True) == 'STALE'
        assert repository.pre_spawn_control(*ids, 30, alpha_campaign=True, session_workflow=(1, 1)) == 'CONTINUE'
        assert not repository.start_attempt(*ids, identity, 'test:ordinary-start', alpha_campaign=True)
        assert not repository.start_attempt(*ids, identity, 'test:wrong-start',
            alpha_campaign=True, session_workflow=(1, 2))
        assert repository.start_attempt(*ids, identity, 'test:private-start', alpha_campaign=True, session_workflow=(1, 1))
        assert repository.heartbeat_control(*ids, 30, alpha_campaign=True, session_workflow=(1, 1)) == 'CONTINUE'
        assert repository.finalize(*ids, expected_state=JobState.RUNNING,
            expected_attempt_outcome='RUNNING', final_state=JobState.SUCCEEDED,
            reason_code='RESULT_VALIDATED', trace_id='test:private-result',
            result_hash='e'*64, result_metadata={'synthetic': True}, alpha_campaign=True, session_workflow=(1, 1))
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as observer:
            assert observer.execute('SELECT state,result_hash FROM public.jobs WHERE job_id=%s', (job,)).fetchone() == ('SUCCEEDED', 'e'*64)
        assert repository.session_result_matches(claimed, 'e'*64)
        assert not repository.session_result_matches(claimed, 'f'*64)
        assert repository.session_attempt_state(claimed) == (JobState.SUCCEEDED, 'SUCCEEDED')
        assert repository.claim_session_holdout('session-worker', 30, 'test:completed-retry',
            job_id=job, workflow_run_id=1, workflow_attempt=1) is None

        # Expiry across a write must roll back the transition and lease renewal.
        for action in ('start', 'control', 'finalize'):
            raced_job, raced_graph = seed()
            raced = repository.claim_session_holdout('session-worker', 30, 'test:expiry-claim',
                job_id=raced_job, workflow_run_id=1, workflow_attempt=1)
            assert raced is not None
            rid = (raced.job_id, raced.attempt_id, raced.worker_id, raced.lease_token)
            with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker') as worker:
                _ = worker.execute(CONSUME, (*rid, *raced_graph, 'test:expiry-consume'))
            if action != 'start':
                assert repository.start_attempt(*rid, identity, 'test:expiry-start',
                    alpha_campaign=True, session_workflow=(1, 1))
            with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                _ = owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()+interval '2 seconds' WHERE job_id=%s", (raced_job,))
                _ = owner.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()+interval '2 seconds' WHERE attempt_id=%s", (raced.attempt_id,))
                _ = owner.execute("""CREATE FUNCTION public.session_expiry_test() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(2.1); RETURN NEW; END $$""")
                _ = owner.execute("CREATE TRIGGER session_expiry_test BEFORE UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.session_expiry_test()")
            try:
                with _rejected(psycopg.errors.InvalidParameterValue):
                    if action == 'start':
                        _ = repository.start_attempt(*rid, identity, 'test:expires-start', alpha_campaign=True, session_workflow=(1, 1))
                    elif action == 'control':
                        _ = repository.heartbeat_control(*rid, 30, alpha_campaign=True, session_workflow=(1, 1))
                    else:
                        _ = repository.finalize(*rid, expected_state=JobState.RUNNING, expected_attempt_outcome='RUNNING',
                            final_state=JobState.SUCCEEDED, reason_code='RESULT_VALIDATED', trace_id='test:expires-finalize',
                            result_hash='f'*64, alpha_campaign=True, session_workflow=(1, 1))
            finally:
                with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                    _ = owner.execute('DROP TRIGGER session_expiry_test ON public.jobs')
                    _ = owner.execute('DROP FUNCTION public.session_expiry_test()')
            expected = JobState.CLAIMED if action == 'start' else JobState.RUNNING
            assert repository.session_attempt_state(raced) == (expected, expected.value)
            assert not repository.session_result_matches(raced, 'f'*64)
            assert repository.pre_spawn_control(*rid, 30, alpha_campaign=True, session_workflow=(1, 1)) == 'STALE'
        repository.assert_session_runtime_identity()

        class AbsentProcess:
            def inspect(self, pid: int) -> ProcessIdentity | None:
                return None

        for state in ('CLAIMED', 'RUNNING', 'CANCEL_REQUESTED'):
            recovery_job, recovery_graph = seed()
            recovery_claim = repository.claim_session_holdout('session-worker', 30, 'test:recovery-claim',
                job_id=recovery_job, workflow_run_id=1, workflow_attempt=1)
            assert recovery_claim is not None
            recovery_ids = (recovery_job, recovery_claim.attempt_id, recovery_claim.worker_id, recovery_claim.lease_token)
            if state == 'RUNNING':
                with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker', autocommit=True) as worker:
                    assert worker.execute(CONSUME, (*recovery_ids, *recovery_graph, 'test:recovery-consume')).fetchone()
                assert repository.start_attempt(*recovery_ids, identity, 'test:recovery-start', alpha_campaign=True, session_workflow=(1, 1))
            if state == 'CANCEL_REQUESTED':
                with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
                    _ = api.execute('SELECT * FROM job_plane.api_cancel_alpha_campaign(%s,%s,%s,%s)',
                        (recovery_job, 'synthetic-operator', 'test:cancel-active', 'event_'+uuid4().hex))
                assert repository.pre_spawn_control(*recovery_ids, 30, alpha_campaign=True, session_workflow=(1, 1)) == 'CANCEL'
                assert repository.finalize(*recovery_ids, expected_state=JobState.CANCEL_REQUESTED,
                    expected_attempt_outcome='CLAIMED', final_state=JobState.CANCELLED,
                    reason_code='CANCELLED', trace_id='test:cancel-finalize', alpha_campaign=True, session_workflow=(1, 1))
            else:
                with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                    _ = owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (recovery_job,))
                assert repository.recover_expired_leases(AbsentProcess(), alpha_campaign=True,
                    fixture_only=False, job_id=recovery_job, trace_id='test:ordinary-recovery') == ()
                recovered = repository.recover_expired_leases(AbsentProcess(), fixture_only=False,
                    job_id=recovery_job, trace_id='test:private-recovery', alpha_campaign=True, session_workflow=(1, 1))
                assert len(recovered) == 1 and recovered[0][1] != 'LEASE_RECOVERY_STALE'
            assert repository.claim_session_holdout('session-worker', 30, 'test:no-retry',
                job_id=recovery_job, workflow_run_id=1, workflow_attempt=1) is None

        cancelled, _ = seed()
        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
            _ = api.execute('SELECT * FROM job_plane.api_cancel_alpha_campaign(%s,%s,%s,%s)',
                (cancelled, 'synthetic-operator', 'test:cancel', 'event_'+uuid4().hex))
        assert repository.claim_session_holdout('session-worker', 30, 'test:cancelled',
            job_id=cancelled, workflow_run_id=1, workflow_attempt=1) is None
        expired, _ = seed(expiry=datetime.now(UTC)+timedelta(seconds=1))
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as observer:
            _ = observer.execute('SELECT pg_sleep(1.1)')
            assert observer.execute('''SELECT a.expires_at<=clock_timestamp() FROM public.jobs j
                JOIN public.p3_campaign_authorizations a ON a.request_digest=j.payload#>>'{authorization_ref,content_sha256}'
                WHERE j.job_id=%s''', (expired,)).fetchone() == (True,)
        assert repository.claim_session_holdout('session-worker', 30, 'test:expired',
            job_id=expired, workflow_run_id=1, workflow_attempt=1) is None
        expiring, _ = seed(expiry=datetime.now(UTC)+timedelta(seconds=2))
        # Inject latency only in this disposable cluster, after the job/attempt
        # writes. Expired authority must roll back all three claim writes.
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            _ = owner.execute('''CREATE FUNCTION public.session_claim_delay() RETURNS trigger
                LANGUAGE plpgsql AS $$BEGIN PERFORM pg_sleep(2.1); RETURN NEW; END$$;
                CREATE TRIGGER session_claim_delay BEFORE INSERT ON public.job_events
                FOR EACH ROW EXECUTE FUNCTION public.session_claim_delay()''')
        try:
            with _rejected(psycopg.errors.InvalidParameterValue, match='expired during claim'):
                _ = repository.claim_session_holdout('session-worker', 30, 'test:mid-claim-expiry',
                    job_id=expiring, workflow_run_id=1, workflow_attempt=1)
        finally:
            with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                _ = owner.execute('DROP TRIGGER session_claim_delay ON public.job_events; DROP FUNCTION public.session_claim_delay()')
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as observer:
            assert observer.execute('SELECT state,attempt_count FROM public.jobs WHERE job_id=%s', (expiring,)).fetchone() == ('QUEUED', 0)
            assert observer.execute('SELECT count(*) FROM public.job_attempts WHERE job_id=%s', (expiring,)).fetchone() == (0,)
            assert observer.execute("SELECT count(*) FROM public.job_events WHERE job_id=%s AND to_state='CLAIMED'", (expiring,)).fetchone() == (0,)
    return {'verdict': 'PASS', 'ordinary_holdout_claim': 'CLOSED', 'concurrent_winners': 1,
        'workflow_denials': 2, 'role_denials': 5, 'null_denials': 9,
        'cancelled_and_expired': 'REJECTED', 'expired_during_claim': 'ROLLED_BACK',
        'one_use_disclosure': 'ACTUAL_PRIVATE_CLAIM',
        'late_stage_lease_and_authority_expiry': 'ROLLED_BACK',
        'late_stage_success_and_publication_readback': 'PASS',
        'expanded_catalog_acl_drift': 6, 'migration_parent_drift': 'REJECTED'}


def _check_terminal_boundaries(sock, name, source, repository):
    """Real SQL, synthetic operation evidence: no official data or authorization."""
    import hashlib
    import json
    from psycopg.sql import SQL, Identifier
    from packages.alpha_lifecycle.operation_input import FAMILY_IDS
    from packages.engine_contracts.serialization import canonical_json_bytes
    from services.job_store.p3_publication_repository import P3PublicationRepository
    from .p3_operation_fixture import _authorization, _reference, _sealed, _entry

    # Capture each late-stage operation with the same actual claim/start owner.
    alpha = FAMILY_IDS[1]
    failures = []
    for operation, fault in ((operation, fault) for operation in ('PARITY', 'PHASE_EXIT')
                             for fault in ('lease', 'authority', None)):
        workflow = 'p3-native-parity-v1' if operation == 'PARITY' else 'p3-phase-exit-v1'
        keys = ('holdout_manifest_ref', 'primary_reference_ref', 'baseline_reference_ref',
            'instrument_spec_ref', 'native_request_ref') if operation == 'PARITY' else (
            'primary_selection_ref', 'primary_qualification_ref', 'baseline_selection_ref',
            'holdout_request_ref', 'holdout_evaluation_ref', 'holdout_replay_ref',
            'executable_ref', 'baseline_executable_ref', 'parity_ref', 'current_primary_head_ref')
        auth, intent, review = _authorization(source, workflow, operation, (alpha,),
            {key: _reference('{}') for key in keys})
        if fault == 'authority':
            expiry = datetime.now(UTC)+timedelta(seconds=1.5)
            auth = _sealed({**json.loads(auth), 'expires_at': expiry.isoformat().replace('+00:00', 'Z')})
        with psycopg.connect(host=str(sock), dbname=name, user='trading_p3_authority') as authority:
            authority.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)', (auth, intent, review))
        job = 'job_terminal_'+uuid4().hex
        raw = canonical_json_bytes(dict(schema_version='p3-alpha-campaign-payload-v1',
            operation=operation, manifest_ref=_reference(intent), authorization_ref=_reference(auth),
            expected_source=source, logical_trial_id=workflow)).decode()
        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
            api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
                (job, raw, hashlib.sha256(raw.encode()).hexdigest(),
                 'p3:'+workflow+':'+json.loads(auth)['nonce'], 'synthetic-operator', 100,
                 'test:terminal', 'event_'+uuid4().hex))
        claim = repository.claim_next_alpha_campaign('terminal-worker', 30, 'test:terminal', fixture_only=False, job_id=job)
        assert claim is not None
        ids = (job, claim.attempt_id, claim.worker_id, claim.lease_token)
        assert repository.start_attempt(*ids, ProcessIdentity(74001,74001,100,'a'*64),
            'test:terminal-start', alpha_campaign=True)
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            head = owner.execute('SELECT stream_id,registry_event_text,registry_event_sha256,registry_sequence FROM public.p3_alpha_heads WHERE alpha_id=%s', (alpha,)).fetchone()
            assert head is not None
        registry = json.loads(head[1])
        registry.update(sequence=head[3]+1, predecessor_sha256=head[2])
        registry['record'].update(lifecycle_status='QUALIFIED', qualification_decision='PASS',
            qualification_reason='synthetic-exit', metrics_sha256='a'*64, robustness_sha256='b'*64)
        registry_text = canonical_json_bytes(registry).decode()
        ref = _reference(registry_text)
        evidence = _reference('{"purpose":"synthetic-terminal-test"}')
        event = json.loads(_entry().canonical_event_text)
        event_id = str(uuid5(head[0], canonical_json_bytes([head[3]+1, ref['content_sha256']]).decode()))
        event.update(event_id=event_id, stream_id=str(head[0]), sequence=head[3]+1)
        event['payload'].update(alpha_id=alpha, alpha_version='1.0.0', registry_sequence=head[3]+1,
            predecessor_sha256=head[2], registry_event_sha256=ref['content_sha256'],
            registry_event_text=registry_text, evidence_sha256=evidence['content_sha256'])
        entry = dict(event_id=event_id, stream_id=str(head[0]), sequence=head[3]+1,
            event_type='AlphaRegistryTransitionRecordedV1', canonical_event_text=canonical_json_bytes(event).decode(),
            topic='p3.alpha-registry', outbox_payload_text=canonical_json_bytes({'event_id':event_id}).decode())
        request = json.loads(_sealed(dict(schema_version='p3-publication-request-v1', job_id=job,
            idempotency_key='synthetic-terminal', semantic_request_digest=hashlib.sha256(canonical_json_bytes([ref])).hexdigest(),
            stage='EXIT_DECISION', evidence_ref=evidence, expected_heads=[dict(alpha_id=alpha,version='1.0.0',
                sequence=head[3],event_digest=head[2])], proposed_event_refs=[ref])))
        transport = canonical_json_bytes(dict(job_id=job, attempt_id=claim.attempt_id,
            worker_id=claim.worker_id, lease_token=claim.lease_token, request=request,
            entries=[entry], output_inventory_ref=_reference('[]'))).decode()
        if fault is not None:
            with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                if fault == 'lease':
                    owner.execute("UPDATE public.jobs SET lease_expires_at=clock_timestamp()+interval '0.4 seconds' WHERE job_id=%s", (job,))
                    owner.execute("UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()+interval '0.4 seconds' WHERE attempt_id=%s", (claim.attempt_id,))
                owner.execute("""CREATE FUNCTION public.terminal_delay_test() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(3.2); RETURN NEW; END $$;
                    CREATE TRIGGER terminal_delay_test BEFORE INSERT ON public.job_events
                    FOR EACH ROW EXECUTE FUNCTION public.terminal_delay_test()""")
        try:
            try:
                if operation == 'PARITY':
                    accepted = repository.finalize(*ids, expected_state=JobState.RUNNING,
                        expected_attempt_outcome='RUNNING', final_state=JobState.SUCCEEDED,
                        reason_code='RESULT_VALIDATED', trace_id='test:terminal-expiry',
                        result_hash='f'*64, alpha_campaign=True)
                    assert accepted, 'did not exercise the post-write expiry boundary'
                else:
                    with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker') as worker:
                        committed = worker.execute(P3PublicationRepository.COMMIT_SQL, (*ids, transport, 'test:terminal-expiry')).fetchone()
                    if fault is None:
                        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker') as worker:
                            assert worker.execute(P3PublicationRepository.READ_SQL, (job, request['idempotency_key'], request['semantic_request_digest'])).fetchone() == committed
                            assert worker.execute(P3PublicationRepository.COMMIT_SQL, (*ids, transport, 'test:lost-ack')).fetchone() == committed
            except psycopg.Error as error:
                assert fault is not None
                assert error.sqlstate in ('22023', 'P3D03') and 'expired during' in str(error), str(error)
            else:
                if fault is not None:
                    failures.append(operation+' committed past its '+fault)
        finally:
            if fault is not None:
                with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                    owner.execute('DROP TRIGGER terminal_delay_test ON public.job_events; DROP FUNCTION public.terminal_delay_test()')
        if fault is None:
            assert repository.session_attempt_state(claim) == (JobState.SUCCEEDED, 'SUCCEEDED')
            if operation == 'PARITY':
                assert repository.session_result_matches(claim, 'f'*64)
        elif not failures:
            assert repository.session_attempt_state(claim) == (JobState.RUNNING, 'RUNNING')
            with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
                assert owner.execute('SELECT count(*) FROM public.p3_alpha_job_commits WHERE job_id=%s', (job,)).fetchone() == (0,)
                assert owner.execute('SELECT count(*) FROM public.domain_events WHERE event_id=%s', (event_id,)).fetchone() == (0,)
                assert owner.execute('SELECT registry_sequence FROM public.p3_alpha_heads WHERE alpha_id=%s', (alpha,)).fetchone() == (head[3],)
    # The new session snapshot must include publication and its append dependencies.
    from services.job_store import p3_catalog
    catalog_sql = p3_catalog.SESSION_CATALOG_SQL
    for table in ('p3_alpha_heads', 'p3_alpha_projection', 'event_append_idempotency',
                  'event_publications', 'job_artifacts', 'worker_heartbeats'):
        with psycopg.connect(host=str(sock), dbname=name, user='postgres') as owner:
            owner.execute('SET LOCAL search_path=pg_catalog')
            before = owner.execute(catalog_sql).fetchone()
            owner.execute(SQL('GRANT UPDATE ON public.{} TO trading_job_worker WITH GRANT OPTION').format(Identifier(table)))
            after = owner.execute(catalog_sql).fetchone()
            owner.rollback()
        if before == after:
            failures.append(table+' ACL drift is invisible')
    assert not failures, failures
