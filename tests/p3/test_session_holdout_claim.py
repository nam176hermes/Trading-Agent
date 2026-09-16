"""A private claim must not reopen the ordinary HOLDOUT lane."""
import os

import pytest

from tests.jobs.test_repository_transition_capabilities import _Connection, _worker
from packages.job_contracts import JobState
from services.job_worker.recovery import ProcessIdentity


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
    assert result['sql_revision'] == '0029_p3_session_holdout_claim'
    assert result['session_holdout_checks']['verdict'] == 'PASS'
    assert result['cleanup']['root_absent'] and result['cleanup']['server_stopped']
