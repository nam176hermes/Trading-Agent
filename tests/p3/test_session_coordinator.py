"""Synthetic host admission around the real single-owner coordinator.

These checks exercise sequencing and recovery, not protected-host qualification.
Calculation/child/custody and SQL capabilities have separate executable checks.
"""
from types import SimpleNamespace

import pytest

from packages.job_contracts import JobState
from tests.p3.test_holdout_session import session_inputs  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401


@pytest.mark.parametrize('fault', [None, 'cancel', 'crash', 'lease', 'deadline', 'unconfirmed', 'stage_order'])
def test_coordinator_keeps_one_view_and_never_reclaims(session_inputs, monkeypatch, fault):
    from services.job_worker import p3_session as module, p3_host_profile, p3_official as coordinator
    x = session_inputs
    session = x.session
    current = [x.claim('HOLDOUT')]
    claims, executions, views, cleanups = [], [], [], []
    states = {}
    monkeypatch.setattr(module, 'P3HoldoutSession', lambda *args, **kwargs: session)
    monkeypatch.setattr('services.job_worker.p3_spawn.P3SpawnProvider', lambda **kwargs: object())
    monkeypatch.setattr(coordinator, 'read_protected_canonical_json_current', module.read_protected_canonical_json_current)
    original_host = p3_host_profile.read_official_profile
    closure = SimpleNamespace(source=session.profile.source, environment_ref=session.profile.environment_ref,
        closure_sha256=session.profile.closure_sha256)
    def profile():
        job = current[0]
        return SimpleNamespace(job_id=job.job_id, payload=job.payload,
            store_root=str(x.store._root), output_root=str(x.store._root.parent/'output'),
            environment_ref=session.profile.environment_ref, custodian_endpoint=None,
            model_dump=lambda **kwargs: {'runtime': 'fixed'})
    def host(path, digest, **kwargs):
        checked, _ = original_host(path, digest, **kwargs)
        admitted = profile()
        admitted.custodian_endpoint = checked.custodian_endpoint
        return admitted, closure
    monkeypatch.setattr(p3_host_profile, 'read_official_profile', host)
    monkeypatch.setattr(p3_host_profile.OfficialHostProfile, 'model_validate_json', lambda raw: profile())
    def no_unbounded_wait(_):
        raise AssertionError('unexpected unbounded wait')
    monkeypatch.setattr(module.time, 'sleep', no_unbounded_wait)
    class Repository:
        def assert_session_runtime_identity(self): pass
        def recover_expired_leases(self, *args, **kwargs):
            assert kwargs['job_id'] == current[0].job_id
        def claim_session_holdout(self, *args, **kwargs):
            assert kwargs['workflow_run_id'] == kwargs['workflow_attempt'] == 1
            return self.claim_next_alpha_campaign(*args, **kwargs)
        def claim_next_alpha_campaign(self, *args, **kwargs):
            job = current[0]
            assert kwargs['job_id'] == job.job_id and job.job_id not in claims
            claims.append(job.job_id)
            states[job.job_id] = (JobState.CLAIMED, 'CLAIMED')
            return job
        def session_attempt_state(self, claim): return states[claim.job_id]
        def pre_spawn_control(self, *args, **kwargs):
            assert kwargs['alpha_campaign'] is True
            assert (kwargs['session_workflow'] is not None) == (current[0].payload.operation == 'HOLDOUT')
            if len(claims) == 2 and fault in {'cancel', 'lease'}:
                if fault == 'cancel': states[current[0].job_id] = (JobState.CANCEL_REQUESTED, 'CLAIMED')
                return 'CANCEL' if fault == 'cancel' else 'STALE'
            return 'CONTINUE'
        heartbeat_control = pre_spawn_control
        def finalize_execution(self, claim, **kwargs):
            cleanups.append((claim.job_id, kwargs['final_state']))
            assert kwargs['result'] is None
            return True
    repository = Repository()
    def build(repo, values, **kwargs):
        assert repo is repository and kwargs['p3_session'] is session
        class Worker:
            _worker_id = 'worker'
            def _safety_preflight(self): pass
            def run_once(self, *, session_claim):
                operation = session_claim.payload.operation
                executions.append(operation)
                if operation == 'HOLDOUT': session.release(repository, trace_id='test:coordinator')
                views.append(session.view)
                if operation == 'PARITY':
                    assert len(session.native_requests()) == 2
                    if fault == 'crash': raise RuntimeError('synthetic crash')
                if operation == 'HOLDOUT' and fault == 'deadline':
                    session._deadline = 0
                    return True
                if fault != 'unconfirmed':
                    session.completed_jobs.append(session_claim.job_id)
                    states[session_claim.job_id] = (JobState.SUCCEEDED, 'SUCCEEDED')
                if operation != 'PHASE_EXIT':
                    next_stage = 'PARITY' if operation == 'HOLDOUT' else 'PHASE_EXIT'
                    if fault == 'stage_order': next_stage = 'PHASE_EXIT'
                    current[0] = x.claim(next_stage)
                return True
        return Worker()
    initial = profile()
    values = dict(GITHUB_RUN_ID='1', GITHUB_RUN_ATTEMPT='1')
    if fault:
        with pytest.raises((ValueError, RuntimeError)):
            coordinator.run_session(values, repository=repository, profile=initial, closure=closure,
                authority=object(), build_worker=build)
        assert len(claims) <= 2
        if fault == 'cancel': assert cleanups == [('job_PARITY', JobState.CANCELLED)]
    else:
        assert coordinator.run_session(values, repository=repository, profile=initial, closure=closure,
            authority=object(), build_worker=build) == 0
        assert executions == ['HOLDOUT', 'PARITY', 'PHASE_EXIT']
        assert claims == session.completed_jobs == ['job_HOLDOUT', 'job_PARITY', 'job_PHASE_EXIT']
        assert not cleanups
    assert x.events.count('release') == 1
    assert views and all(view is views[0] for view in views)
    assert session._closed
    with pytest.raises(ValueError, match='closed'): views[0].read_bytes(x.manifest)


def test_official_entry_dispatches_holdout_to_session_after_host_admission(synthetic_provider, monkeypatch):
    from services.job_worker import p3_official as module
    provider, job, closure = synthetic_provider
    profile = SimpleNamespace(store_root=str(provider._store_root), output_root=str(provider._output_root),
        worker_credentials_directory='/synthetic/credentials')
    values = dict(P3_OPERATION='p3-holdout-primary-v1', P3_AUTHORITY_REQUEST_FILE='/synthetic/request',
        P3_PREFLIGHT_DIRECTORY='/synthetic/enqueue', P3_ARTIFACT_ROOT=str(provider._store_root),
        P3_MANIFEST_FILE='/synthetic/manifest', P3_REVIEW_FILE='/synthetic/review',
        GITHUB_RUN_ID='1', GITHUB_RUN_ATTEMPT='1', CREDENTIALS_DIRECTORY='/synthetic/credentials')
    monkeypatch.setattr(module, 'read_enqueued_job', lambda *a, **k: (job.job_id, SimpleNamespace(payload=job.payload), 'operator'))
    monkeypatch.setattr(module, 'read_protected_canonical_json_current', lambda *a: ({}, 'a'*64))
    monkeypatch.setattr(module, 'read_official_profile', lambda *a, **k: (profile, closure))
    monkeypatch.setattr(module, 'probe_p3_startup', lambda *a, **k: None)
    authority = SimpleNamespace(application_revision=closure.source.commit_sha)
    monkeypatch.setattr(module, 'attest_worker_runtime_authority', lambda: authority)
    monkeypatch.setattr(module.JobStoreSettings, 'from_systemd_credentials', lambda *a, **k: object())
    class Repository:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def assert_p3_runtime_identity(self): pytest.fail('ordinary schema admitted session')
    monkeypatch.setattr(module, 'WorkerRepository', Repository)
    def session(actual, **kwargs):
        assert actual is values and kwargs['profile'] is profile and kwargs['authority'] is authority
        return 3
    monkeypatch.setattr(module, 'run_session', session)
    assert module.run_official_once(values, build_worker=object()) == 3


from tests.p3.test_spawn_capability import synthetic_provider  # noqa: E402,F401


@pytest.mark.parametrize('fault', [None, 'lost_ack', 'unknown_commit', 'wrong_readback'])
def test_worker_parent_parity_reconciles_without_reexecuting(exit_graph, tmp_path, monkeypatch, fault):
    from psycopg import OperationalError
    from packages.alpha_lifecycle.parity import ParityPair
    from packages.alpha_lifecycle.contracts.results import ParityResult
    from packages.alpha_lifecycle.replica_store import _read
    from services.job_worker.worker import JobWorker
    from services.job_worker.results import ResultValidator
    from services.job_worker.process_runner import HeartbeatDecision
    from tests.p3.test_job_api import _alpha_request
    store, intent, _, _ = exit_graph
    pair = _read(store, intent.body.parity_ref, ParityPair)
    parity = _read(store, pair.primary_parity_ref, ParityResult)
    calls, writes = [], []
    def calculate(**kwargs):
        calls.append('calculate')
        return parity
    session = SimpleNamespace(fence=lambda: None, calculate_parity=calculate,
        native_parent_proof_ref=intent.body.parity_ref, parity_pair_ref=intent.body.parity_ref, completed_jobs=[])
    claim = SimpleNamespace(job_id='job_parity', attempt_id='attempt_parity',
        payload=_alpha_request().payload.model_copy(update={'operation': 'PARITY', 'logical_trial_id': 'p3-native-parity-v1'}))
    class Repository:
        def finalize_execution(self, actual, **kwargs):
            assert actual is claim and kwargs['alpha_campaign'] is True
            writes.append(kwargs['result'])
            if fault in {'lost_ack', 'unknown_commit'}: raise OperationalError('synthetic commit response lost')
            return True
        def session_result_matches(self, actual, sha):
            assert actual is claim and sha == writes[0].sha256
            calls.append('readback')
            return fault not in {'unknown_commit', 'wrong_readback'}
    worker = object.__new__(JobWorker)
    worker._p3_session = session
    worker._p3_profile = True
    worker._session_holdout = False
    worker._repository = Repository()
    worker._validator = ResultValidator(tmp_path, tmp_path, tmp_path)
    monkeypatch.setattr(worker, '_worker_heartbeat', lambda *a: None)
    def run():
        worker._execute_session_late_stage(claim, heartbeat=lambda _: HeartbeatDecision.CONTINUE,
            preflight=lambda: None, trace_id='test:parent-parity')
    if fault in {'unknown_commit', 'wrong_readback'}:
        with pytest.raises((OperationalError, ValueError)): run()
        assert not session.completed_jobs
    else:
        run()
        assert session.completed_jobs == [claim.job_id]
    assert calls.count('calculate') == len(writes) == 1
    assert 'readback' in calls
    assert writes[0].validation_metadata['producer'] == 'p3-session-parent'


from tests.p3.test_phase_exit import exit_graph  # noqa: E402,F401


@pytest.mark.parametrize('fault', [None, 'lost_ack', 'unknown_commit', 'wrong_readback'])
def test_worker_holdout_reads_exact_commit_without_second_release(session_inputs, tmp_path, fault):
    from psycopg import OperationalError
    from services.job_worker.worker import JobWorker
    from services.job_worker.results import ResultValidator
    from tests.p3.test_worker_profile import P3Repository
    from tests.jobs.test_worker_lifecycle import Runner, outcome, safety_evidence
    from packages.engine_contracts.serialization import canonical_json_bytes
    from tests.p3.test_replica_execution import _seal
    x = session_inputs
    claim = x.claim('HOLDOUT')
    committed, reads = [], []
    class Repository(P3Repository):
        def claim_next_alpha_campaign(self, *args, **kwargs): pytest.fail('preclaimed session claimed again')
        def finalize_execution(self, actual, **kwargs):
            assert actual == claim and kwargs['session_workflow'] == (1, 1)
            committed.append(kwargs['result'])
            if fault in {'lost_ack', 'unknown_commit'}: raise OperationalError('synthetic commit response lost')
            return True
        def session_result_matches(self, actual, sha):
            reads.append(sha)
            assert actual == claim and sha == committed[0].sha256
            return fault not in {'unknown_commit', 'wrong_readback'}
    class Validator(ResultValidator):
        def validate_p3(self, *args, **kwargs):
            assert kwargs['holdout_view'] is x.session.view
            ref = _seal(x.store, schema_version='p3-holdout-operation-result-v1',
                holdout_request_ref=x.session._request, holdout_manifest_ref=x.manifest,
                holdout_evaluation_ref=x.manifest, holdout_replay_ref=x.manifest,
                executable_ref=x.manifest, baseline_executable_ref=x.manifest)
            self.raw = x.store.read_bytes(ref)+b'\n'
            return self._seal(claim, self.raw, 'p3-holdout-operation-result-v1', {})
        def _read_p3_stream(self, *args, **kwargs): return self.raw
    runner = Runner(outcome())
    worker = JobWorker(Repository(claim), runner, Validator(tmp_path, tmp_path, tmp_path),
        worker_id=claim.worker_id, code_commit='e'*40, environment=object(),
        safety_preflight=lambda: safety_evidence('4'*64), prepare_spawn=lambda _: object(),
        p3_profile=True, p3_job_id=claim.job_id, p3_publisher=object(), p3_session=x.session)
    def run():
        with x.session.stage(claim, fence=lambda: None):
            assert worker.run_once(session_claim=claim)
    with x.session:
        if fault in {'unknown_commit', 'wrong_readback'}:
            with pytest.raises((OperationalError, ValueError)): run()
            assert x.session.holdout_result is None
        else:
            run()
            assert x.session.holdout_result is not None
            assert canonical_json_bytes(x.session.holdout_result)+b'\n' == worker._validator.raw
            assert x.session.completed_jobs == [claim.job_id]
    assert x.events.count('release') == len(runner.decisions) == len(committed) == 1
    assert reads
