"""Opt-in native process capability checks in the existing disposable cluster."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import LiteralString
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, create_engine
from typing_extensions import override

from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.operation_input import FAMILY_IDS
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.config import JobStoreSettings
from services.job_store.records import ClaimedJob
from services.job_store.worker_repository import WorkerRepository
from services.job_worker.recovery import ProcessIdentity
from .p3_operation_fixture import _authorization, _reference, _rejected, _wait_for_lock

REVISION = '0028_p3_native_process'
REPLACE: LiteralString = '''SELECT job_plane.worker_replace_alpha_native_process(
    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'''


def check_native_process(sock: Path, name: str, source: SourceIdentity) -> dict[str, object]:
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

    # Only the test address changes; claims, SQL fences and transactions are real.
    settings = SocketSettings('localhost', 5432, name, 'trading_job_worker', 'synthetic')
    previous = ProcessIdentity(71001, 71001, 100, 'a'*64)
    current = ProcessIdentity(71002, 71002, 101, 'b'*64)

    def seed(repository: WorkerRepository) -> ClaimedJob:
        body = {key: _reference('{}') for key in ('holdout_manifest_ref',
            'primary_reference_ref', 'baseline_reference_ref', 'instrument_spec_ref', 'native_request_ref')}
        auth, intent, review = _authorization(source, 'p3-native-parity-v1', 'PARITY', FAMILY_IDS[:1], body)
        with psycopg.connect(host=str(sock), dbname=name, user='trading_p3_authority') as authority:
            _ = authority.execute('SELECT job_plane.accept_p3_operation_authorization(%s,%s,%s)',
                (auth, intent, review))
        job_id = 'job_native_'+uuid4().hex
        raw = canonical_json_bytes(dict(schema_version='p3-alpha-campaign-payload-v1',
            operation='PARITY', manifest_ref=_reference(intent), authorization_ref=_reference(auth),
            expected_source=source.model_dump(mode='json'), logical_trial_id='p3-native-parity-v1')).decode()
        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_api') as api:
            _ = api.execute('SELECT * FROM job_plane.api_enqueue_alpha_campaign(%s,%s,%s,%s,%s,%s,%s,%s)',
                (job_id, raw, hashlib.sha256(raw.encode()).hexdigest(),
                 'p3:p3-native-parity-v1:'+json.loads(auth)['nonce'], 'synthetic-operator', 100,
                 'test:native', 'event_'+uuid4().hex))
        claimed = repository.claim_next_alpha_campaign('native-worker', 30, 'test:native',
            fixture_only=False, job_id=job_id)
        assert claimed is not None and claimed.max_attempts == 1
        assert repository.start_attempt(claimed.job_id, claimed.attempt_id, claimed.worker_id,
            claimed.lease_token, previous, 'test:native', alpha_campaign=True)
        return claimed

    def identity(claimed: ClaimedJob) -> tuple[object, ...]:
        with psycopg.Connection[tuple[object, ...]].connect(host=str(sock), dbname=name, user='postgres') as owner:
            row = owner.execute('''SELECT child_pid,process_group_id,process_start_ticks,command_fingerprint
                FROM public.job_attempts WHERE attempt_id=%s''', (claimed.attempt_id,)).fetchone()
        assert row is not None
        return row

    with WorkerRepository(settings) as repository:
        claimed = seed(repository)
        old = identity(claimed)
        parameters = (claimed.job_id, claimed.attempt_id, claimed.worker_id, claimed.lease_token,
            *old, current.pid, current.process_group, current.start_ticks, current.command_fingerprint)
        for role in ('trading_job_api', 'trading_job_scheduler', 'trading_reader',
                     'trading_p3_authority', 'trading_p3_custodian'):
            with psycopg.connect(host=str(sock), dbname=name, user=role, autocommit=True) as other:
                with _rejected(psycopg.errors.InsufficientPrivilege):
                    _ = other.execute(REPLACE, parameters)
        with psycopg.connect(host=str(sock), dbname=name, user='trading_job_worker', autocommit=True) as worker:
            for index in range(12):
                malformed = list(parameters)
                malformed[index] = None
                with _rejected(psycopg.errors.InvalidParameterValue):
                    _ = worker.execute(REPLACE, malformed)
            for index, replacement in ((0, 'job_absent'), (1, 'attempt_absent'), (2, 'wrong-worker'),
                    (3, 'wrong-token-123456'), (4, 71003), (5, 71003), (6, 99), (7, 'c'*64)):
                wrong = list(parameters)
                wrong[index] = replacement
                assert worker.execute(REPLACE, wrong).fetchone() == (False,)
        assert identity(claimed) == old
        assert repository.replace_alpha_native_process(claimed, previous, current, 'test:native')
        assert identity(claimed) == (current.pid, current.process_group, current.start_ticks, current.command_fingerprint)
        assert not repository.replace_alpha_native_process(claimed, previous, current, 'test:native')

        # Competing writers observe the previous identity under both row locks.
        claimed = seed(repository)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(repository.replace_alpha_native_process,
                claimed, previous, replace(current, pid=71002+i, process_group=71002+i), 'test:race')
                for i in range(2)]
            assert sorted(future.result() for future in futures) == [False, True]

        # The update must recheck the fence after waiting, not at transaction start.
        mutations: tuple[LiteralString, ...] = (
            "UPDATE public.jobs SET state='CANCEL_REQUESTED' WHERE job_id=%s",
            "UPDATE public.jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
            "UPDATE public.job_attempts SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
            "UPDATE public.jobs SET attempt_count=attempt_count+1 WHERE job_id=%s",
        )
        for mutation in mutations:
            claimed = seed(repository)
            old = identity(claimed)
            with ThreadPoolExecutor(max_workers=1) as executor:
                with psycopg.connect(host=str(sock), dbname=name, user='postgres') as locker:
                    _ = locker.execute('SELECT job_id FROM public.jobs WHERE job_id=%s FOR UPDATE', (claimed.job_id,))
                    future = executor.submit(repository.replace_alpha_native_process,
                        claimed, previous, current, 'test:locked')
                    _wait_for_lock(locker, 'trading_job_worker', '%worker_replace_alpha_native_process%')
                    _ = locker.execute(mutation, (claimed.job_id,))
                assert future.result() is False
            assert identity(claimed) == old
    return {'verdict': 'PASS', 'role_denials': 5, 'null_denials': 12,
            'identity_denials': 8, 'lock_fences': len(mutations), 'concurrent_winners': 1}
