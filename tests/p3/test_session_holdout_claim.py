"""A private claim must not reopen the ordinary HOLDOUT lane."""
import os

import pytest

from tests.jobs.test_repository_transition_capabilities import _Connection, _worker
from packages.job_contracts import JobState
from services.job_worker.recovery import ProcessIdentity
from tests.p3.test_native_process_identity import native_claim


@pytest.mark.parametrize('row', [None, {'state': 'RUNNING', 'outcome': 'RUNNING'},
    {'state': 'CANCEL_REQUESTED', 'outcome': 'RUNNING'}])
def test_session_attempt_readback_preserves_absence_and_cancellation(row):
    connection = _Connection(row)
    claim = native_claim()
    result = _worker(connection).session_attempt_state(claim)
    assert result == (None if row is None else (JobState(row['state']), row['outcome']))
    query, parameters = connection.calls[0]
    assert parameters == (claim.job_id, claim.attempt_id, claim.worker_id, claim.lease_token)
    assert 'j.attempt_count=a.attempt_number' in query
    assert len(connection.calls) == 1


@pytest.mark.parametrize('fault', [None, 'missing', 'job_state', 'attempt_state',
    'unfinished', 'result', 'invalid_hash', 'transport'])
def test_session_result_readback_requires_exact_completed_result(fault, monkeypatch):
    from psycopg import OperationalError
    row = dict(state='SUCCEEDED', outcome='SUCCEEDED', finished_at='2026-09-16T12:00:00Z',
        result_hash='a'*64)
    if fault == 'job_state': row['state'] = 'RUNNING'
    elif fault == 'attempt_state': row['outcome'] = 'FAILED'
    elif fault == 'unfinished': row['finished_at'] = None
    elif fault == 'result': row['result_hash'] = 'b'*64
    connection = _Connection(None if fault == 'missing' else row)
    repository = _worker(connection)
    claim = native_claim()
    if fault == 'transport':
        def lost(statement, parameters):
            connection.calls.append((statement, parameters))
            raise OperationalError('readback unavailable')
        monkeypatch.setattr(connection, 'execute', lost)
        with pytest.raises(OperationalError, match='readback unavailable'):
            repository.session_result_matches(claim, 'a'*64)
    elif fault == 'invalid_hash':
        with pytest.raises(ValueError, match='result hash'):
            repository.session_result_matches(claim, 'A'*64)
        assert not connection.calls
        return
    else:
        assert repository.session_result_matches(claim, 'a'*64) is (fault is None)
    assert len(connection.calls) == 1  # An uncertain read never reruns or writes research.
    query, parameters = connection.calls[0]
    assert parameters == (claim.job_id, claim.attempt_id, claim.worker_id, claim.lease_token)
    assert 'j.attempt_count=a.attempt_number' in query


@pytest.mark.parametrize('fault', [None, 'current_user', 'session_user', 'version_num',
    'restricted', 'missing_identity', 'catalog', 'missing_catalog'])
def test_session_database_admission_checks_identity_then_exact_catalog(fault):
    from services.job_store.p3_catalog import SESSION_CATALOG_SHA256, SESSION_REVISION
    identity = dict(current_user='trading_job_worker', session_user='trading_job_worker',
        version_num=SESSION_REVISION, restricted=True)
    if fault in identity: identity[fault] = False if fault == 'restricted' else 'other'
    catalog = {'catalog_sha256': '0'*64 if fault == 'catalog' else SESSION_CATALOG_SHA256}
    connection = _Connection(None, None if fault == 'missing_identity' else identity,
        None if fault == 'missing_catalog' else catalog)
    repository = _worker(connection)
    if fault is None:
        repository.assert_session_runtime_identity()
    else:
        with pytest.raises(ValueError, match='session database'):
            repository.assert_session_runtime_identity()
    assert connection.calls[0] == ('SET LOCAL search_path=pg_catalog', None)
    assert len(connection.calls) == (3 if fault in {None, 'catalog', 'missing_catalog'} else 2)
    assert connection.transaction_events == [('enter', None), ('exit', None if fault is None else ValueError)]


@pytest.mark.parametrize('job,fixture', [(None, False), ('job-session', True), ('job-session', None)])
def test_private_recovery_rejects_unbound_or_fixture_scope_before_sql(job, fixture):
    connection = _Connection()
    with pytest.raises(ValueError, match='exact non-fixture job'):
        _worker(connection).recover_expired_leases(object(), alpha_campaign=True,
            fixture_only=fixture, job_id=job, session_workflow=(123, 2))
    assert not connection.calls


def test_private_recovery_preserves_job_attempt_and_workflow_fences():
    candidate = dict(job_id='job-session', attempt_id='attempt-session', state='RUNNING',
        attempt_outcome='RUNNING', lease_owner='worker-session', lease_token='secret',
        child_pid=None, process_group_id=None, process_start_ticks=None, command_fingerprint=None)
    connection = _Connection([candidate], {'outcome': 'LEASE_EXPIRED_IDENTITY_UNVERIFIABLE'})
    assert _worker(connection).recover_expired_leases(object(), alpha_campaign=True,
        fixture_only=False, job_id='job-session', session_workflow=(123, 2),
        trace_id='test:recover', recovery_id='recovery-session') == (
            ('job-session', 'LEASE_EXPIRED_IDENTITY_UNVERIFIABLE'),)
    selection, bound_job = connection.calls[0]
    assert bound_job == ('job-session',)
    assert "j.payload->>'operation'='HOLDOUT'" in selection
    statement, parameters = connection.calls[1]
    assert 'job_plane.worker_recover_session_holdout' in statement
    assert parameters[:6] == ('job-session', 'attempt-session', 'RUNNING', 'RUNNING',
        'worker-session', 'secret')
    assert parameters[10:13] == ('UNVERIFIABLE', 'test:recover', 'recovery-session')
    assert parameters[-2:] == (123, 2)
    assert connection.transaction_events == [('enter', None), ('exit', None)]
    assert len(connection.calls) == 2


@pytest.mark.parametrize('action', ['start', 'control', 'finalize'])
def test_holdout_lifecycle_uses_private_workflow_capability(action):
    connection = _Connection({'started': True, 'control': 'CONTINUE', 'finalized': True})
    repository = _worker(connection)
    claim = ('job-session', 'attempt-session', 'worker-session', 'x'*32)
    scope = dict(alpha_campaign=True, session_workflow=(123, 2))
    if action == 'start':
        assert repository.start_attempt(*claim, ProcessIdentity(71, 71, 99, 'a'*64), 'test:start', **scope)
        name = 'worker_start_session_holdout'
    elif action == 'control':
        assert repository.pre_spawn_control(*claim, 30, **scope) == 'CONTINUE'
        name = 'worker_control_session_holdout'
    else:
        assert repository.finalize(*claim, expected_state=JobState.CLAIMED,
            expected_attempt_outcome='CLAIMED', final_state=JobState.BLOCKED,
            reason_code='P3_AUTHORITY_HELD', trace_id='test:finalize', **scope)
        name = 'worker_finalize_session_holdout'
    query, parameters = connection.calls[-1]
    assert name in query and parameters[-2:] == (123, 2)
    assert connection.transaction_events == [('enter', None), ('exit', None)]


def test_private_holdout_claim_is_workflow_and_job_bound():
    connection = _Connection(None)
    repository = _worker(connection)
    assert repository.claim_session_holdout('worker-session', 30, 'test:holdout',
        job_id='job-session', workflow_run_id=123, workflow_attempt=2) is None
    query, parameters = connection.calls[0]
    assert 'job_plane.worker_claim_session_holdout' in query
    assert parameters[1] == 'worker-session'
    assert parameters[3:5] == (30, 'test:holdout')
    assert parameters[-3:] == ('job-session', 123, 2)
    assert connection.transaction_events == [('enter', None), ('exit', None)]


@pytest.mark.parametrize('run,attempt', [(True, 1), (1, False), (0, 1), (1, 0), (2**63, 1)])
def test_private_holdout_claim_rejects_invalid_workflow_before_sql(run, attempt):
    connection = _Connection(None)
    with pytest.raises(ValueError):
        _worker(connection).claim_session_holdout('worker-session', 30, 'test:holdout',
            job_id='job-session', workflow_run_id=run, workflow_attempt=attempt)
    assert not connection.calls


@pytest.mark.runtime_postgres
@pytest.mark.skipif(os.environ.get('P3_SESSION_HOLDOUT_SQL_SOURCE_TEST') != '1',
    reason='explicit disposable session holdout SQL source selection required')
def test_private_holdout_claim_in_disposable_postgres():
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.pre_p3_provenance import canonical_source_identity
    from services.job_worker import p3_fixture_sql
    source = SourceIdentity.model_validate(canonical_source_identity(p3_fixture_sql.ROOT))
    result = p3_fixture_sql.run_sql_fixture(source, session_holdout_source_checks=True)
    assert result['sql_revision'] == '0030_p3_session_terminal_fences'
    assert result['session_holdout_checks']['verdict'] == 'PASS'
    assert result['cleanup']['root_absent'] and result['cleanup']['server_stopped']
