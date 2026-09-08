"""The fixture receipt is a parent result, not a native child's stdout."""

import hashlib
from dataclasses import replace

import pytest

from packages.alpha_lifecycle.contracts.authority import IntegrationReceipt
from packages.engine_contracts import canonical_json_bytes
from packages.job_contracts import JobType
from services.job_worker.results import ResultValidator, ResultValidationError
from tests.jobs.test_worker_lifecycle import claim
from tests.p3.test_job_api import _alpha_request


def test_parent_receipt_is_sealed_without_a_fabricated_stream(tmp_path):
    job = replace(claim(), job_type=JobType.ALPHA_CAMPAIGN,
                  payload=_alpha_request().payload.model_copy(update={
                      'operation':'PARITY','logical_trial_id':'p3-integration-fixture-v1',
                  }))
    payload = dict(schema_version='p3-integration-qualified-v1', source=job.payload.expected_source,
                   sql_proof_ref=job.payload.manifest_ref,native_fixture_proof_ref=job.payload.manifest_ref,
                   cleanup_proof_ref=job.payload.manifest_ref,workflow_run_id=1,workflow_attempt=1,
                   status='PASS',authority=dict(broker=False,live=False,network=False,production=False))
    payload['digest'] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    receipt = IntegrationReceipt.model_validate(payload)
    validator = ResultValidator(tmp_path,tmp_path,tmp_path)
    result = validator.seal_integration_receipt(job,receipt)
    assert result.sha256 == hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
    assert not (tmp_path / job.job_id / job.attempt_id / 'stdout.log').exists()
    with pytest.raises(ResultValidationError):
        validator.seal_integration_receipt(replace(job,payload=_alpha_request().payload),receipt)


def test_sql_executor_is_owned_by_worker_and_does_not_import_tests():
    import inspect
    from services.job_worker import p3_fixture_sql

    source = inspect.getsource(p3_fixture_sql)
    assert 'from tests.' not in source and 'import tests.' not in source
    assert callable(p3_fixture_sql.run_sql_fixture)


@pytest.mark.parametrize('started', [False, True])
def test_fixture_failure_uses_the_actual_durable_state(started, monkeypatch):
    from services.job_worker.p3_integration import P3IntegrationFixtureExecutor, IntegrationExecutionError
    from services.job_worker.worker import JobWorker
    from tests.p3.test_worker_profile import P3Repository
    from tests.jobs.test_worker_lifecycle import outcome, safety_evidence

    job = replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,payload=_alpha_request().payload.model_copy(update={
        'operation':'PARITY','logical_trial_id':'p3-integration-fixture-v1',
    }))
    executor = object.__new__(P3IntegrationFixtureExecutor)
    actual = outcome() if started else None

    def fail(_job, *, heartbeat, **kwargs):
        if started:
            heartbeat(actual.identity)
        raise IntegrationExecutionError('fixture failed',outcome=actual)

    monkeypatch.setattr(executor,'run',fail)
    repository = P3Repository(job)
    worker = JobWorker(repository,object(),object(),worker_id='worker-1',code_commit='e'*40,
        environment=object(),safety_preflight=lambda:safety_evidence('4'*64),
        prepare_spawn=lambda _:None,p3_profile=True,p3_publisher=object(),p3_fixture_executor=executor)
    assert worker.run_once()
    final = next(call[2] for call in repository.calls if call[0]=='finalize')
    assert final['expected_state'] == ('RUNNING' if started else 'CLAIMED')
    assert final['final_state'] == ('FAILED' if started else 'BLOCKED')
    assert final['outcome'] is actual


def test_fixed_native_inputs_match_the_existing_approved_fixture(tmp_path):
    from services.job_worker.p3_fixture_native import build_native_fixture_inputs
    from services.job_worker.engine_artifacts import HashBoundArtifactResolver
    command, bindings = build_native_fixture_inputs(tmp_path)
    assert command.command_type == 'RunBacktest'
    assert len(bindings) == 4
    assert all(binding.source.stat().st_mode & 0o777 == 0o400 for binding in bindings)
    assert len(HashBoundArtifactResolver(bindings)(command)) == 4


def test_fixture_claim_is_filtered_before_claiming_research_jobs():
    from tests.jobs.test_repository_transition_capabilities import _Connection, _worker
    connection = _Connection(None)
    repository = _worker(connection)
    repository.claim_next_alpha_campaign('worker-fixture',30,'fixture:claim',fixture_only=True)
    assert connection.calls[0][1][-1] is True


@pytest.mark.parametrize('changed', [False, True])
def test_executor_reattests_authority_after_cleanup(changed, tmp_path, monkeypatch):
    from dataclasses import dataclass
    from types import SimpleNamespace
    from services.job_worker import p3_integration as module
    from tests.jobs.test_worker_lifecycle import outcome

    job = replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,payload=_alpha_request().payload.model_copy(update={
        'operation':'PARITY','logical_trial_id':'p3-integration-fixture-v1',
    }))
    root = tmp_path/'private'
    root.mkdir(mode=0o700)
    source = job.payload.expected_source
    authorization = SimpleNamespace(issuer_run_id=1,issuer_attempt=1)
    plan = SimpleNamespace(native_request_digest='a'*64)
    stages = []
    def attest(self, actual_job):
        assert actual_job is job
        stages.append('attest')
        if len(stages) == 2:
            assert list(root.iterdir()) == []
            if changed:
                raise module.AuthorityHeld('HELD E_SOURCE: source changed')
        return source,authorization,plan
    monkeypatch.setattr(module.P3IntegrationFixtureExecutor,'_attest',attest)
    monkeypatch.setattr(module,'build_native_fixture_inputs',lambda _: (object(),()))
    monkeypatch.setattr(module,'payload_digest',lambda _: 'a'*64)
    monkeypatch.setattr(module,'build_p1_engine_spawn_provider',lambda *args:object())
    @dataclass
    class Result:
        semantic_sha256: str = 'b'*64
    native = tuple(SimpleNamespace(replica=n,request=SimpleNamespace(engine_run_id=str(n)),
                                  outcome=outcome(),result=Result()) for n in range(1,4))
    # The orchestration test substitutes native execution explicitly; this is not a native proof.
    for run in native:
        run.request = {'engine_run_id': str(run.replica)}
    class Request(dict):
        @property
        def engine_run_id(self):
            return self['engine_run_id']
    for run in native:
        run.request = Request(run.request)
    monkeypatch.setattr(module,'run_native_fixture',lambda *args,**kwargs:native)
    monkeypatch.setattr(module,'run_sql_fixture',lambda *args,**kwargs:{
        'checks':sorted(module.REQUIRED_SQL_CHECKS),'cleanup':{'root_absent':True},
    })
    monkeypatch.setattr(module.ResultValidator,'_read_p3_stream',lambda *args,**kwargs:b'fixture')
    store = SimpleNamespace(put_bytes=lambda *args,**kwargs:job.payload.manifest_ref)
    executor = module.P3IntegrationFixtureExecutor(store=store,closure_config=object(),private_root=root,review_file=tmp_path/'review')
    if changed:
        with pytest.raises(module.IntegrationExecutionError,match='source changed'):
            executor.run(job,heartbeat=lambda *_:None,preflight=lambda:None,progress=lambda:None)
    else:
        assert executor.run(job,heartbeat=lambda *_:None,preflight=lambda:None,progress=lambda:None).receipt.status == 'PASS'
    assert stages == ['attest','attest']
    assert list(root.iterdir()) == []


def test_native_inputs_do_not_require_the_test_tree(tmp_path, monkeypatch):
    from pathlib import Path
    from services.job_worker.p3_fixture_native import build_native_fixture_inputs
    original = Path.read_bytes
    def read(path):
        if 'tests' in path.parts:
            raise FileNotFoundError('tests are absent from the worker installation')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    assert len(build_native_fixture_inputs(tmp_path)[1]) == 4


def test_native_stderr_is_retained_even_when_empty(tmp_path):
    import io
    from services.job_worker.artifacts import ArtifactWriter
    job = claim()
    stream = ArtifactWriter(tmp_path).capture_stream(job.job_id,job.attempt_id,'stderr',io.BytesIO(b''))
    assert ResultValidator(tmp_path,tmp_path,tmp_path)._read_p3_stream(job,stream,stream_name='stderr') == b''


def test_alpha_recovery_uses_its_sql_capability():
    from tests.jobs.test_repository_transition_capabilities import _Connection, _worker
    candidate = dict(job_id='job_test',attempt_id='attempt_test',state='RUNNING',
                     attempt_outcome='RUNNING',lease_owner='worker-test',lease_token='a'*32,
                     child_pid=123,process_group_id=123,process_start_ticks=1,command_fingerprint='b'*64)
    connection = _Connection({'outcome':'BLOCKED'})
    repository = _worker(connection)
    repository._recover_observed_candidate(candidate,'STILL_RUNNING','test:recovery','recovery',alpha_campaign=True)
    assert 'worker_recover_expired_alpha_campaign' in connection.calls[0][0]


def test_unverified_sql_cleanup_blocks_attempt_without_retry(monkeypatch):
    from services.job_worker.p3_integration import P3IntegrationFixtureExecutor, IntegrationExecutionError
    from services.job_worker.worker import JobWorker
    from tests.p3.test_worker_profile import P3Repository
    from tests.jobs.test_worker_lifecycle import outcome, safety_evidence
    job = replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,payload=_alpha_request().payload.model_copy(update={
        'operation':'PARITY','logical_trial_id':'p3-integration-fixture-v1',
    }))
    executor = object.__new__(P3IntegrationFixtureExecutor)
    def fail(_job, *, heartbeat, **kwargs):
        heartbeat(outcome().identity)
        raise IntegrationExecutionError('SQL cleanup unverified',cleanup_unverified=True)
    monkeypatch.setattr(executor,'run',fail)
    repository = P3Repository(job)
    worker = JobWorker(repository,object(),object(),worker_id='worker-1',code_commit='e'*40,
        environment=object(),safety_preflight=lambda:safety_evidence('4'*64),
        prepare_spawn=lambda _:None,p3_profile=True,p3_publisher=object(),p3_fixture_executor=executor)
    assert worker.run_once()
    final = next(call[2] for call in repository.calls if call[0] == 'finalize')
    assert final['final_state'] == 'BLOCKED'
    assert final['reason_code'] == 'P3_CLEANUP_UNVERIFIED'


def test_fixture_worker_entrypoint_composes_the_exact_executor(tmp_path, monkeypatch):
    from services.job_worker import main as module
    from services.job_worker.p3_integration import P3IntegrationFixtureExecutor
    tmp_path.chmod(0o700)
    values = {'P3_ARTIFACT_ROOT':str(tmp_path),'P3_PRIVATE_ROOT':str(tmp_path),
              'P3_REVIEW_FILE':str(tmp_path/'review'),'P3_NATIVE_RUNTIME_ROOT':str(tmp_path/'runtime'),
              'P3_NATIVE_ARTIFACT_DIRECTORY':str(tmp_path/'closure'),'P3_SANDBOX_EXECUTABLE':'/usr/bin/bwrap'}
    captured = {}
    def build(repository, source, **kwargs):
        captured.update(kwargs)
        return 'worker'
    monkeypatch.setattr(module,'build_worker',build)
    assert module.build_p3_fixture_worker(object(),values,authority=object()) == 'worker'
    assert type(captured['p3_fixture_executor']) is P3IntegrationFixtureExecutor
    assert captured['p3_fixture_executor'].private_root == tmp_path


@pytest.mark.parametrize('details,reason', [
    ({'blocked':True},'P3_AUTHORITY_HELD'),
    ({'control':'SAFETY_DRIFT','reason_code':'SAFETY_EXPIRED'},'SAFETY_EXPIRED'),
])
def test_post_native_authority_failure_blocks_and_retains_lineage(details, reason, monkeypatch):
    from services.job_worker.p3_integration import P3IntegrationFixtureExecutor, IntegrationExecutionError
    from services.job_worker.worker import JobWorker
    from tests.p3.test_worker_profile import P3Repository
    from tests.jobs.test_worker_lifecycle import outcome, safety_evidence
    job = replace(claim(),job_type=JobType.ALPHA_CAMPAIGN,payload=_alpha_request().payload.model_copy(update={
        'operation':'PARITY','logical_trial_id':'p3-integration-fixture-v1',
    }))
    actual = outcome()
    executor = object.__new__(P3IntegrationFixtureExecutor)
    def fail(_job, *, heartbeat, **kwargs):
        heartbeat(actual.identity)
        raise IntegrationExecutionError('authority changed',outcome=actual,**details)
    monkeypatch.setattr(executor,'run',fail)
    repository = P3Repository(job)
    worker = JobWorker(repository,object(),object(),worker_id='worker-1',code_commit='e'*40,
        environment=object(),safety_preflight=lambda:safety_evidence('4'*64),
        prepare_spawn=lambda _:None,p3_profile=True,p3_publisher=object(),p3_fixture_executor=executor)
    assert worker.run_once()
    final = next(call[2] for call in repository.calls if call[0] == 'finalize')
    assert (final['final_state'],final['reason_code'],final['outcome']) == ('BLOCKED',reason,actual)


@pytest.mark.parametrize('mode,accepted', [(0o640,True),(0o660,False),(0o644,False)])
def test_root_approval_can_be_read_by_worker_group_but_not_modified(mode, accepted, tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace
    from services.job_worker import p3_integration as module
    path = tmp_path/'approval'
    path.write_bytes(b'approved-bytes')
    path.chmod(mode)
    original = os.fstat
    def stat(fd):
        info = original(fd)
        return SimpleNamespace(st_mode=info.st_mode,st_uid=0,st_gid=info.st_gid,
                               st_nlink=info.st_nlink,st_size=info.st_size)
    monkeypatch.setattr(module.os,'fstat',stat)
    if accepted:
        assert module._read_authority_bytes(path) == b'approved-bytes'
    else:
        with pytest.raises(module.AuthorityHeld):
            module._read_authority_bytes(path)
