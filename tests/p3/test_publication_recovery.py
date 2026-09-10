"""Fault-injection checks for publication retries; not PostgreSQL qualification."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import hashlib
import json

import pytest
from psycopg import OperationalError
from psycopg.errors import DeadlockDetected, SerializationFailure

from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult, P3PublicationRepository
from tests.jobs.test_worker_lifecycle import claim
from tests.p3.test_sql_authority import _entry, _request


def _publication(tmp_path, failures, *, recovered=False, result_updates=None):
    root = tmp_path / 'cas'
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    entry = _entry()
    ref = store.put_bytes(b'{}', media_type='application/json')
    envelope = json.loads(entry.canonical_event_text)
    envelope['payload']['evidence_sha256'] = ref.content_sha256
    entry = entry.model_copy(update={'canonical_event_text':canonical_json_bytes(envelope).decode()})
    registry_ref = store.put_bytes(envelope['payload']['registry_event_text'].encode(), media_type='application/json')
    payload = _request().model_dump(mode='json', exclude={'digest'})
    payload.update(evidence_ref=ref.model_dump(mode='json'),
                   proposed_event_refs=[registry_ref.model_dump(mode='json')])
    payload['digest'] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    request = PublicationRequest.model_validate_json(canonical_json_bytes(payload))
    value = dict(schema_version='p3-job-commit-result-v1', job_id=request.job_id,
                 idempotency_key=request.idempotency_key,
                 semantic_request_digest=request.semantic_request_digest,
                 prepublication_ref=ref.model_dump(mode='json'),
                 ledger_event_ids=[str(entry.event_id)],
                 registry_event_refs=[registry_ref.model_dump(mode='json')],
                 alpha_outcome='NOT_EVALUATED')
    value.update(result_updates or {})
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    class Pool:
        def __init__(self):
            self.commits = []
            self.reads = 0
            self.failures = iter(failures)
            self.commit_error = None
            self.transactions_committed = 0

        @contextmanager
        def connection(self):
            yield self

        @contextmanager
        def transaction(self):
            yield self
            if self.commit_error is not None:
                raise self.commit_error
            self.transactions_committed += 1

        def execute(self, sql, parameters):
            if sql == P3PublicationRepository.COMMIT_SQL:
                self.commits.append(parameters)
                error = next(self.failures, None)
                if type(error) is OperationalError:
                    self.commit_error = error
                elif error is not None:
                    raise error
                result = value
            else:
                assert sql == P3PublicationRepository.READ_SQL
                self.reads += 1
                result = value if recovered else None
            return SimpleNamespace(fetchone=lambda: {'result': result})

    pool = Pool()
    repository = P3PublicationRepository(pool, store)
    claimed = replace(claim(), job_id=request.job_id)
    return repository, pool, request, claimed, entry, JobCommitResult.model_validate_json(canonical_json_bytes(value))


@pytest.mark.parametrize('error_type', [DeadlockDetected, SerializationFailure])
def test_known_transaction_abort_retries_identical_publication_at_most_three_times(tmp_path, error_type):
    repository, pool, request, claimed, entry, expected = _publication(
        tmp_path, [error_type(), error_type()])
    assert repository.publish(request, claimed, (entry,), trace_id='p3:test') == expected
    assert len(pool.commits) == 3
    assert all(parameters == pool.commits[0] for parameters in pool.commits)
    assert pool.reads == 0


@pytest.mark.parametrize('error_type', [DeadlockDetected, SerializationFailure])
def test_third_transaction_abort_is_terminal(tmp_path, error_type):
    repository, pool, request, claimed, entry, _ = _publication(
        tmp_path, [error_type() for _ in range(4)])
    with pytest.raises(error_type):
        repository.publish(request, claimed, (entry,), trace_id='p3:test')
    assert len(pool.commits) == 3


@pytest.mark.parametrize('recovered', [False, True])
def test_unknown_commit_is_reconciled_without_blind_retry(tmp_path, recovered):
    repository, pool, request, claimed, entry, expected = _publication(
        tmp_path, [OperationalError('connection lost')], recovered=recovered)
    if recovered:
        assert repository.publish(request, claimed, (entry,), trace_id='p3:test') == expected
    else:
        with pytest.raises(OperationalError):
            repository.publish(request, claimed, (entry,), trace_id='p3:test')
    assert len(pool.commits) == 1
    assert pool.reads == 1


@pytest.mark.parametrize('path', ['publish', 'recover', 'read'])
@pytest.mark.parametrize('updates', [
    {'job_id': 'another-job'},
    {'idempotency_key': 'another-request'},
    {'semantic_request_digest': 'f' * 64},
    {'prepublication_ref': _request().evidence_ref.model_dump(mode='json')},
    {'registry_event_refs': [_request().proposed_event_refs[0].model_dump(mode='json')]},
    {'alpha_outcome': 'PASS'},
])
def test_commit_result_is_bound_to_request_on_every_return_path(tmp_path, path, updates):
    repository, pool, request, claimed, entry, _ = _publication(
        tmp_path, [OperationalError('connection lost')] if path == 'recover' else [],
        recovered=True, result_updates=updates)
    with pytest.raises(ValueError, match='commit result'):
        if path == 'read':
            repository.read_commit(request)
        else:
            repository.publish(request, claimed, (entry,), trace_id='p3:test')
    assert len(pool.commits) <= 1  # Never repeat an unknown transaction outcome.
    assert pool.transactions_committed == 0


@pytest.mark.parametrize('recovered', [False, True])
def test_publication_rejects_other_ledger_event_even_with_valid_result_digest(tmp_path, recovered):
    repository, pool, request, claimed, entry, _ = _publication(
        tmp_path, [OperationalError('connection lost')] if recovered else [],
        recovered=recovered,
        result_updates={'ledger_event_ids': ['99999999-9999-5999-8999-999999999999']})
    with pytest.raises(ValueError, match='commit result'):
        repository.publish(request, claimed, (entry,), trace_id='p3:test')
    assert len(pool.commits) == 1


@pytest.mark.parametrize('edge', [None, 'missing', 'legacy', 'wrong_job', 'noncanonical', 'wrong_result', 'missing_time', 'non_utc_zone', 'noncanonical_result'])
def test_receipt_recovery_uses_only_the_committed_database_record(tmp_path, edge):
    from datetime import UTC, datetime
    from tests.p3.test_publication import _chain, _changed
    from packages.alpha_lifecycle.publication import recover_publication_receipt
    store, request, commit, expected, _ = _chain(tmp_path)
    timestamp = datetime(2026, 9, 5, tzinfo=UTC)
    raw = canonical_json_bytes(request).decode()
    row = {'request_text': raw, 'result': commit.model_dump(mode='json'), 'committed_at': timestamp}
    if edge == 'legacy':
        row['request_text'] = None
    elif edge == 'wrong_job':
        row['request_text'] = canonical_json_bytes(_changed(request, job_id='other-job')).decode()
    elif edge == 'noncanonical':
        row['request_text'] += ' '
    elif edge == 'wrong_result':
        row['result'] = _changed(commit, semantic_request_digest='f'*64).model_dump(mode='json')
    elif edge == 'missing_time':
        row['committed_at'] = None
    elif edge == 'non_utc_zone':
        from zoneinfo import ZoneInfo
        row['committed_at'] = timestamp.replace(tzinfo=ZoneInfo('Etc/UTC'))
    row['result_text'] = canonical_json_bytes(row['result']).decode()
    if edge == 'noncanonical_result':
        row['result_text'] += ' '
    class Pool:
        @contextmanager
        def connection(self):
            yield self
        def execute(self, sql, parameters):
            assert 'worker_read_alpha_publication' in sql
            assert parameters == (request.job_id,)
            return SimpleNamespace(fetchone=lambda: None if edge == 'missing' else row)
    output = tmp_path / 'recovered-cas'
    output.mkdir(mode=0o700)
    store = LocalArtifactStore(output)
    repository = P3PublicationRepository(Pool(), store)
    before = set(output.glob('*'))
    if edge is None:
        assert recover_publication_receipt(request.job_id, repository=repository, store=store) == expected
        assert recover_publication_receipt(request.job_id, repository=repository, store=store) == expected
    else:
        with pytest.raises((ValueError, RuntimeError)):
            recover_publication_receipt(request.job_id, repository=repository, store=store)
        assert set(output.glob('*')) == before
