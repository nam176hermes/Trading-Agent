"""Official native attribution is separate from fixture process replacement."""
from dataclasses import replace
import os

import pytest
from psycopg import OperationalError

from packages.job_contracts import JobType
from tests.jobs.test_worker_lifecycle import claim, outcome
from tests.jobs.test_repository_transition_capabilities import _Connection, _worker
from tests.p3.test_job_api import _alpha_request


def native_claim():
    return replace(claim(), job_type=JobType.ALPHA_CAMPAIGN,
        payload=_alpha_request().payload.model_copy(update={
            'operation': 'PARITY', 'logical_trial_id': 'p3-native-parity-v1'}))


def test_native_replacement_preserves_exact_identity_in_one_transaction():
    connection = _Connection({'replaced': True})
    repository = _worker(connection)
    previous = outcome().identity
    current = replace(previous, pid=previous.pid+1, process_group=previous.pid+1)
    claimed = native_claim()
    assert repository.replace_alpha_native_process(claimed, previous, current, 'native:replace')
    statement, parameters = connection.calls[0]
    assert 'job_plane.worker_replace_alpha_native_process' in statement
    assert parameters == (claimed.job_id, claimed.attempt_id, claimed.worker_id, claimed.lease_token,
        previous.pid, previous.process_group, previous.start_ticks, previous.command_fingerprint,
        current.pid, current.process_group, current.start_ticks, current.command_fingerprint)
    assert connection.transaction_events == [('enter', None), ('exit', None)]


@pytest.mark.parametrize('fault', ['fixture', 'operation', 'same_identity'])
def test_native_replacement_rejects_other_lanes_before_sql(fault):
    connection = _Connection({'replaced': True})
    repository = _worker(connection)
    claimed = native_claim()
    previous = outcome().identity
    current = replace(previous, pid=previous.pid+1, process_group=previous.pid+1)
    if fault == 'fixture': claimed = replace(claimed, payload=claimed.payload.model_copy(
        update={'logical_trial_id': 'p3-integration-fixture-v1'}))
    elif fault == 'operation': claimed = replace(claimed, payload=claimed.payload.model_copy(update={'operation': 'HOLDOUT'}))
    else: current = previous
    with pytest.raises(ValueError):
        repository.replace_alpha_native_process(claimed, previous, current, 'native:reject')
    assert not connection.calls


def test_uncertain_native_process_update_is_not_retried(monkeypatch):
    connection = _Connection({'replaced': True})
    repository = _worker(connection)
    calls = []
    def lost(*args):
        calls.append(args)
        raise OperationalError('lost acknowledgement')
    monkeypatch.setattr(connection, 'execute', lost)
    previous = outcome().identity
    with pytest.raises(OperationalError, match='acknowledgement'):
        repository.replace_alpha_native_process(native_claim(), previous,
            replace(previous, pid=previous.pid+1), 'native:lost')
    assert len(calls) == 1


@pytest.mark.parametrize('accepted', [True, False])
@pytest.mark.parametrize('operation', ['PARITY', 'BASELINES'])
def test_worker_replaces_native_identity_before_renewing_lease(accepted, operation):
    from services.job_worker.process_runner import HeartbeatDecision
    from services.job_worker.worker import JobWorker
    from tests.jobs.test_worker_lifecycle import safety_evidence
    from tests.p3.test_worker_profile import P3Repository

    first = outcome().identity
    second = replace(first, pid=first.pid+1, process_group=first.pid+1)

    class Repository(P3Repository):
        def start_attempt(self, *args, **kwargs):
            self.calls.append(('start', args))
            return True

        def heartbeat_control(self, *args, **kwargs):
            self.calls.append(('heartbeat', args))
            return 'CONTINUE'

        def replace_alpha_native_process(self, claimed, previous, current, trace_id):
            self.calls.append(('native', previous, current))
            return accepted

    claimed = native_claim()
    if operation == 'BASELINES':
        claimed = replace(claimed, payload=claimed.payload.model_copy(update={
            'operation': operation, 'logical_trial_id': 'p3-baselines-v1'}))
    repository = Repository(claimed)
    renewed = accepted and operation == 'PARITY'

    class Runner:
        def run(self, prepare, environment, timeout, heartbeat, **kwargs):
            assert heartbeat(first) is HeartbeatDecision.CONTINUE
            assert heartbeat(first) is HeartbeatDecision.CONTINUE
            assert heartbeat(second) is (HeartbeatDecision.CONTINUE if renewed else HeartbeatDecision.STALE_LEASE)
            return replace(outcome('STALE_LEASE'), identity=second)

    worker = JobWorker(repository, Runner(), object(), worker_id='worker-1',
        code_commit='e'*40, environment=object(), safety_preflight=lambda: safety_evidence('4'*64),
        prepare_spawn=lambda _: object(), p3_profile=True, p3_publisher=object())
    assert worker.run_once()
    calls = [c for c in repository.calls if c[0] in {'start', 'native', 'heartbeat'}]
    assert [c[0] for c in calls] == ['start', 'heartbeat', 'heartbeat'] + (
        ['native'] if operation == 'PARITY' else []) + (['heartbeat'] if renewed else [])
    assert [c for c in calls if c[0] == 'native'] == (
        [('native', first, second)] if operation == 'PARITY' else [])


@pytest.mark.runtime_postgres
@pytest.mark.skipif(os.environ.get('P3_NATIVE_SQL_SOURCE_TEST') != '1',
    reason='explicit disposable native process SQL source selection required')
def test_native_process_replacement_in_disposable_postgres():
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.pre_p3_provenance import canonical_source_identity
    from services.job_worker import p3_fixture_sql
    source = SourceIdentity.model_validate(canonical_source_identity(p3_fixture_sql.ROOT))
    result = p3_fixture_sql.run_sql_fixture(source, native_process_source_checks=True)
    assert result['sql_revision'] == '0028_p3_native_process'
    assert result['native_process_checks']['verdict'] == 'PASS'
    assert result['cleanup']['root_absent'] and result['cleanup']['server_stopped']
