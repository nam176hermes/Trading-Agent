"""Fault-injection checks for publication retries; not PostgreSQL qualification."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import hashlib

import pytest
from psycopg import OperationalError
from psycopg.errors import DeadlockDetected, SerializationFailure

from packages.alpha_lifecycle.contracts.lifecycle import PublicationRequest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from services.job_store.p3_publication_repository import JobCommitResult, P3PublicationRepository
from tests.jobs.test_worker_lifecycle import claim
from tests.p3.test_sql_authority import _entry, _request


def _publication(tmp_path, failures, *, recovered=False):
    root = tmp_path / 'cas'
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    entry = _entry()
    ref = store.put_bytes(b'{}', media_type='application/json')
    payload = _request().model_dump(mode='json', exclude={'digest'})
    payload.update(evidence_ref=ref.model_dump(mode='json'),
                   proposed_event_refs=[ref.model_dump(mode='json')])
    payload['digest'] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    request = PublicationRequest.model_validate_json(canonical_json_bytes(payload))
    value = dict(schema_version='p3-job-commit-result-v1', job_id=request.job_id,
                 idempotency_key=request.idempotency_key,
                 semantic_request_digest=request.semantic_request_digest,
                 prepublication_ref=ref.model_dump(mode='json'),
                 ledger_event_ids=[str(entry.event_id)],
                 registry_event_refs=[ref.model_dump(mode='json')],
                 alpha_outcome='NOT_EVALUATED')
    value['digest'] = hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    class Pool:
        def __init__(self):
            self.commits = []
            self.reads = 0
            self.failures = iter(failures)
            self.commit_error = None

        @contextmanager
        def connection(self):
            yield self

        @contextmanager
        def transaction(self):
            yield self
            if self.commit_error is not None:
                raise self.commit_error

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
