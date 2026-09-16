"""Private HOLDOUT claim checks in the existing disposable SQL source harness."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, create_engine
from typing_extensions import override

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from services.job_store.config import JobStoreSettings
from services.job_store.worker_repository import WorkerRepository
from .p3_holdout_fixture import _inputs, _seed, CONSUME
from .p3_operation_fixture import _rejected

REVISION = '0029_p3_session_holdout_claim'
CLAIM = 'SELECT * FROM job_plane.worker_claim_session_holdout(%s,%s,%s,%s,%s,%s,%s,%s,%s)'


def check_session_holdout(sock: Path, name: str, source: SourceIdentity) -> dict[str, object]:
    engine = create_engine(URL.create('postgresql+psycopg', username='trading_owner',
        database=name, query={'host': str(sock)}))
    try:
        with engine.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[2]/'alembic.ini'))
            config.attributes['connection'] = connection
            command.upgrade(config, REVISION)
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
    with WorkerRepository(settings) as repository:
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
        'one_use_disclosure': 'ACTUAL_PRIVATE_CLAIM'}
