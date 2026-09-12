"""Synthetic spawn capabilities never authorize a protected P3 run."""
import pytest
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath


@pytest.fixture
def synthetic_provider(tmp_path, monkeypatch):
    from services.job_worker import p3_spawn as module
    from packages.alpha_lifecycle.authority import build_alpha_campaign_payload
    from packages.alpha_lifecycle.contracts.authority import RunAuthorization
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
    from packages.alpha_lifecycle.operation_input import P3OperationInput
    from packages.job_contracts import JobType
    from services.job_store.worker_repository import ClaimedJob
    from tests.p3.test_baseline_operation import _cli_authorization
    from tests.p3.test_replica_execution import _seal
    from tests.p3.test_job_api import _alpha_request
    from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
    from packages.data_catalog.artifact_store import LocalArtifactStore
    root=tmp_path/'store';root.mkdir(mode=0o700)
    store=LocalArtifactStore(root)
    placeholder=store.put_bytes(b'{}',media_type='application/json')
    # Transport tests never calculate or dereference dataset/PIT/fold artifacts.
    environment_ref=_seal(store,schema_version='p3-environment-identity-v1',python_version='3.11',
        root_lock_digest=hashlib.sha256(b'synthetic lock').hexdigest(),
        native_manifest_digest=P1_REAL_BACKTEST_POLICY.closure_sha256,
        sandbox_policy_digest=module.SANDBOX_PROFILE_SHA256,platform='linux-x86_64',decimal_precision=50)
    input_ref=_seal(store,schema_version='p3-input-set-v1',source=_alpha_request().payload.expected_source,
        epoch_id='synthetic',policy_digest='c'*64,family_digest='e'*64,
        dataset_evidence_ref=placeholder,fold_manifest_ref=placeholder,pit_proof_ref=placeholder,
        environment_ref=environment_ref,regime_threshold_ref=placeholder,integration_receipt_ref=placeholder,
        cost_model=dict(fee_bps=0,spread_bps=0,slippage_bps=0,funding_bps=0,borrow_bps=0))
    inputs=InputSet.model_validate_json(store.read_bytes(input_ref))
    baseline_ref=_seal(store,schema_version='p3-baseline-manifest-v1',input_set_ref=input_ref,
        required_baselines=['B0_CASH','B1_BUY_AND_HOLD','B2_EQUAL_WEIGHT','B3_SIMPLE_MOMENTUM','B4_SIMPLE_MEAN_REVERSION'])
    baseline=BaselineManifest.model_validate_json(store.read_bytes(baseline_ref))
    intent_ref = _seal(store,schema_version='p3-operation-input-v1',workflow_operation='p3-baselines-v1',
        operation='BASELINES',input_set_ref=baseline.input_set_ref,allowed_alpha_ids=[],body={'baseline_manifest_ref':baseline_ref})
    intent = P3OperationInput.model_validate_json(store.read_bytes(intent_ref))
    authorization_ref = _cli_authorization(store,intent_ref,inputs.source)
    authorization = RunAuthorization.model_validate_json(store.read_bytes(authorization_ref))
    payload = build_alpha_campaign_payload(authorization,inputs.source,'p3-baselines-v1',operation_input=intent)
    job = ClaimedJob('job_'+'1'*32,JobType.ALPHA_CAMPAIGN,payload,'attempt_'+'2'*32,1,
        'synthetic-worker','synthetic-token',datetime.now(UTC)+timedelta(minutes=5),1)
    runtime, output = tmp_path/'python',tmp_path/'output'
    runtime.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    release = {PurePosixPath('/p3/release/scripts/run_p3_alpha_campaign.py'):b'synthetic driver',
        PurePosixPath('/p3/release/scripts/run_p3_driver_entry.py'):b'synthetic bounded entry',
        PurePosixPath('/p3/release/uv.lock'):b'synthetic lock'}
    mounts = []
    def mount(path,target,raw,mode=0o400):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)
        info = path.stat()
        mounts.append(module.P3ClosureMount(path,PurePosixPath(target),
            (info.st_dev,info.st_ino),len(raw),mode,hashlib.sha256(raw).hexdigest()))
    for target, raw in release.items():
        mount(tmp_path/'release'/target.relative_to('/p3/release'),target,raw)
    mount(runtime/'bin/python3.11','/p3/python/bin/python3.11',b'synthetic python',0o500)
    mount(runtime/'lib/python3.11/site-packages/example.py','/p3/python/lib/python3.11/site-packages/example.py',b'synthetic dependency')
    sandbox = tmp_path/'bwrap'
    sandbox.write_bytes(b'synthetic bwrap')
    sandbox.chmod(0o500)
    info = sandbox.stat()
    proof = module.P3Sandbox(sandbox,(info.st_dev,info.st_ino),hashlib.sha256(sandbox.read_bytes()).hexdigest(),
        0o500,module.SANDBOX_PROFILE_SHA256,module.BWRAP_VERSION,module.BWRAP_CAPABILITIES)
    mounts = tuple(sorted(mounts,key=lambda m:m.target))
    # Explicitly synthetic core/source attestation; no child executes here.
    monkeypatch.setattr(module,'inspect_python_runtime',lambda *a,**kw:None)
    monkeypatch.setattr(module,'_release_files',lambda source:{t:('100644',v) for t,v in release.items()})
    digest = module._digest(dict(source=inputs.source,environment_ref=environment_ref,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in mounts],
        sandbox_sha256=proof.executable_sha256,sandbox_policy_sha256=proof.profile_sha256))
    closure = module.CompleteP3Closure(inputs.source,environment_ref,runtime,mounts,proof,digest)
    provider = module.P3SpawnProvider(attest_closure=lambda:closure,store=store,
        store_root=tmp_path/'store',output_root=output)
    return provider,job,closure


def test_forged_p3_capability_cannot_be_consumed():
    from services.job_worker.p3_spawn import PreparedP3Spawn, consume_prepared_p3_spawn
    from services.job_worker.p3_spawn_interface import P3SpawnError
    with pytest.raises(P3SpawnError, match='capability'):
        consume_prepared_p3_spawn(PreparedP3Spawn())


def test_paper_and_engine_capabilities_cannot_be_consumed_as_p3():
    from services.job_worker.command_registry import PreparedSpawn
    from services.job_worker.engine_spawn import PreparedEngineSpawn
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    from services.job_worker.p3_spawn_interface import P3SpawnError
    for token in (PreparedSpawn(), PreparedEngineSpawn(), object()):
        with pytest.raises(P3SpawnError, match='capability'):
            consume_prepared_p3_spawn(token)


def test_consumption_pins_empty_environment_exact_inputs_and_one_use(synthetic_provider):
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    from services.job_worker.p3_spawn_interface import P3SpawnError
    provider,job,closure = synthetic_provider
    token = provider.prepare(job)
    built = consume_prepared_p3_spawn(token)
    try:
        assert built.environment == {} and built.cwd == Path('/')
        assert (built.job_id,built.attempt_id) == (job.job_id,job.attempt_id)
        assert built.source_revision == closure.source.commit_sha
        assert built.pass_fds == built.close_after_spawn_fds
        assert '--unshare-all' in built.argv and '--clearenv' in built.argv
        assert '--new-session' not in built.argv
        assert built.argv.count('/p3/release/scripts/run_p3_driver_entry.py') == 2  # mount and execution
        assert all(os.get_inheritable(fd) is False for fd in built.pass_fds)
        with pytest.raises(P3SpawnError,match='consumed'):
            consume_prepared_p3_spawn(token)
    finally:
        for fd in built.close_after_spawn_fds:
            os.close(fd)
        built.output_custody.abandon()


@pytest.mark.parametrize('fault',['closure','release','python','dependency','sandbox','expired','extra_dependency','source'])
def test_changed_capability_refuses_without_leaking_descriptors(synthetic_provider,monkeypatch,fault):
    from services.job_worker import p3_spawn as module
    provider,job,closure = synthetic_provider
    token = provider.prepare(job)
    before = set(os.listdir('/proc/self/fd'))
    if fault == 'closure':
        provider._attest_closure = lambda:replace(closure,closure_sha256='9'*64)
    elif fault == 'source':
        monkeypatch.setattr(module,'_release_files',lambda source:{PurePosixPath('/p3/release/uv.lock'):b'different source'})
    elif fault == 'expired':
        monkeypatch.setattr(module.time,'monotonic_ns',lambda:10**30)
    elif fault == 'extra_dependency':
        (closure.python_root/'extra.py').write_bytes(b'extra')
    else:
        path = closure.sandbox.executable if fault == 'sandbox' else next(m.source for m in closure.mounts if (
            fault == 'release' and str(m.target).endswith('run_p3_alpha_campaign.py')
            or fault == 'python' and str(m.target).endswith('bin/python3.11')
            or fault == 'dependency' and str(m.target).endswith('example.py')))
        mode = path.stat().st_mode & 0o777
        path.chmod(0o600)
        path.write_bytes(b'changed')
        path.chmod(mode)
    with pytest.raises(module.P3SpawnError):
        module.consume_prepared_p3_spawn(token)
    assert set(os.listdir('/proc/self/fd')) == before
    with pytest.raises(module.P3SpawnError,match='consumed'):
        module.consume_prepared_p3_spawn(token)


def test_process_runner_accepts_only_exact_p3_branch(synthetic_provider,tmp_path,monkeypatch):
    from services.job_worker import process_runner as module
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    from tests.jobs.test_process_runner import runner,FakeProcess,Inspector,identity
    provider,job,_ = synthetic_provider
    token = provider.prepare(job)
    consumed = []
    def consume(value):
        built = consume_prepared_p3_spawn(value)
        consumed.append(built)
        return built
    captured = []
    monkeypatch.setattr(module,'consume_prepared_p3_spawn',consume)
    monkeypatch.setattr(module,'build_child_environment',lambda value:pytest.fail('P3 consulted ambient paper environment'))
    process = FakeProcess([None,0])
    execution = runner(tmp_path,process,Inspector([identity()]),captured)
    outcome = execution.run(lambda:token,object(),1200,
        lambda value:module.HeartbeatDecision.CONTINUE,job_id=job.job_id,attempt_id=job.attempt_id)
    assert outcome.exit_code == 0
    assert outcome.p3_output_inventory_ref == outcome.p3_output_custody.inventory_ref
    outcome.p3_output_custody.abandon()
    assert len(consumed) == 1
    built = consumed[0]
    assert len([c for c in captured if isinstance(c[0],list)]) == 1
    options = captured[0][1]
    assert options['env'] == {} and options['cwd'] == '/' and options['start_new_session'] is True
    for fd in built.close_after_spawn_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_shared_cas_is_read_only_and_directories_are_pinned(synthetic_provider):
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    provider,job,_ = synthetic_provider
    built = consume_prepared_p3_spawn(provider.prepare(job))
    try:
        argv = built.argv
        index = next(i for i,v in enumerate(argv) if v == '/p3/store' and i > 1 and argv[i-2] in {'--ro-bind-fd','--ro-bind'})
        assert argv[index-2] == '--ro-bind-fd'
        assert int(argv[index-1]) in built.pass_fds
        assert argv[argv.index('--output')+1] == '/p3/output'
        output = next(argv[i-1] for i,v in enumerate(argv) if v == '/p3/output' and i > 1 and argv[i-2] == '--bind-fd')
        assert int(output) in built.pass_fds
        assert len(list(provider._output_root.iterdir())) == 1
    finally:
        for fd in built.close_after_spawn_fds:
            os.close(fd)
        built.output_custody.abandon()


def test_provider_cannot_validate_a_different_store_from_its_mount(synthetic_provider,tmp_path):
    from services.job_worker.p3_spawn import P3SpawnProvider
    from packages.data_catalog.artifact_store import LocalArtifactStore
    provider,_,closure = synthetic_provider
    other = tmp_path/'other-store'
    other.mkdir(mode=0o700)
    with pytest.raises((TypeError,ValueError)):
        P3SpawnProvider(attest_closure=lambda:closure,store=LocalArtifactStore(other),
            store_root=provider._store_root,output_root=provider._output_root)


def test_failed_consumption_removes_only_its_empty_attempt_directory(synthetic_provider,monkeypatch):
    from services.job_worker import p3_spawn as module
    provider,job,_ = synthetic_provider
    token = provider.prepare(job)
    existing = provider._output_root/'existing'
    existing.mkdir()
    (existing/'preserve').write_bytes(b'user work')
    inputs = provider._inputs
    calls = 0
    def expire_after_materialization(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError('synthetic authority expiry after directory creation')
        return inputs(*args)
    monkeypatch.setattr(provider,'_inputs',expire_after_materialization)
    before = set(os.listdir('/proc/self/fd'))
    with pytest.raises(module.P3SpawnError):
        module.consume_prepared_p3_spawn(token)
    assert set(os.listdir('/proc/self/fd')) == before
    assert tuple(provider._output_root.iterdir()) == (existing,)
    assert (existing/'preserve').read_bytes() == b'user work'


def test_final_input_read_cannot_extend_capability_ttl(synthetic_provider,monkeypatch):
    from services.job_worker import p3_spawn as module
    provider,job,_=synthetic_provider
    token=provider.prepare(job)
    inputs=provider._inputs
    calls=0
    def slow_inputs(*args):
        nonlocal calls
        calls+=1
        result=inputs(*args)
        if calls == 2:
            monkeypatch.setattr(module.time,'monotonic_ns',lambda:10**30)
        return result
    monkeypatch.setattr(provider,'_inputs',slow_inputs)
    with pytest.raises(module.P3SpawnError):
        built=module.consume_prepared_p3_spawn(token)
        for fd in built.pass_fds:
            os.close(fd)
        built.output_custody.abandon()
    assert not list(provider._output_root.iterdir())


def test_output_parent_descriptor_never_reaches_driver(synthetic_provider):
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    provider,job,_=synthetic_provider
    identity=provider._output_root.stat()
    built=consume_prepared_p3_spawn(provider.prepare(job))
    try:
        assert all((os.fstat(fd).st_dev,os.fstat(fd).st_ino) != (identity.st_dev,identity.st_ino)
            for fd in built.pass_fds)
    finally:
        for fd in built.pass_fds:
            os.close(fd)
        built.output_custody.abandon()


def test_runner_abandons_private_output_on_final_preflight_refusal(synthetic_provider,tmp_path):
    from services.job_worker import process_runner as module
    from tests.jobs.test_process_runner import runner,FakeProcess,Inspector,identity
    provider,job,_=synthetic_provider
    calls=[]
    execution=runner(tmp_path,FakeProcess([None,0]),Inspector([identity()]),calls)
    count=0
    from tests.jobs.test_worker_lifecycle import safety_evidence
    def preflight():
        nonlocal count
        count+=1
        if count == 2:
            raise RuntimeError('synthetic final SQL refusal')
        return safety_evidence('4'*64)
    with pytest.raises(RuntimeError,match='final SQL refusal'):
        execution.run(lambda:provider.prepare(job),object(),1200,
            lambda value:module.HeartbeatDecision.CONTINUE,preflight=preflight,
            job_id=job.job_id,attempt_id=job.attempt_id)
    assert not calls
    assert not list(provider._output_root.iterdir())


def test_sealed_mounts_preserve_their_attested_file_modes(synthetic_provider):
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    provider,job,closure=synthetic_provider
    built=consume_prepared_p3_spawn(provider.prepare(job))
    try:
        for target,mode in [(str(m.target),m.mode) for m in closure.mounts]+[('/p3/bin/bwrap',0o500)]:
            index=built.argv.index(target)
            assert built.argv[index-4:index-1] == ('--perms',f'{mode:o}','--ro-bind-data')
    finally:
        for fd in built.pass_fds:
            os.close(fd)
        built.output_custody.abandon()


@pytest.mark.parametrize('fault',['popen','shape','identity','cancel','timeout','invalid_output',None])
def test_runner_output_custody_covers_terminal_paths(synthetic_provider,tmp_path,monkeypatch,fault):
    from services.job_worker import process_runner as module
    from services.job_worker.p3_spawn import consume_prepared_p3_spawn
    from tests.jobs.test_process_runner import runner,FakeProcess,Inspector,identity
    provider,job,_=synthetic_provider
    consumed=[]
    def consume(token):
        built=consume_prepared_p3_spawn(token)
        consumed.append(built)
        return replace(built,cwd=Path('/unsafe')) if fault == 'shape' else built
    monkeypatch.setattr(module,'consume_prepared_p3_spawn',consume)
    process=FakeProcess([None,0])
    calls=[]
    execution=runner(tmp_path,process,Inspector([None if fault == 'identity' else identity()]),calls,
        clock_values=[0,1201,1202,1203] if fault == 'timeout' else None)
    popen=execution._popen
    raw=b'{"synthetic_output":true}'
    artifact_name=hashlib.sha256(raw).hexdigest()+'.blob'
    def child(*args,**kwargs):
        if fault == 'popen':
            raise OSError('synthetic Popen failure')
        output=next(provider._output_root.iterdir())
        (output/'artifacts').mkdir(mode=0o700)
        artifact=output/'artifacts'/artifact_name
        artifact.write_bytes(b'{}' if fault == 'invalid_output' else raw)
        artifact.chmod(0o600)
        return popen(*args,**kwargs)
    execution._popen=child
    def run():
        return execution.run(lambda:provider.prepare(job),object(),1200,
            lambda _:module.HeartbeatDecision.CANCEL if fault == 'cancel' else module.HeartbeatDecision.CONTINUE,
            job_id=job.job_id,attempt_id=job.attempt_id)
    try:
        if fault in {'popen','shape','identity'}:
            with pytest.raises((OSError,ValueError,RuntimeError)):
                run()
        else:
            outcome=run()
            assert outcome.termination_reason == {'cancel':'CANCELLED','timeout':'TIMEOUT',
                'invalid_output':'P3_OUTPUT_INVALID',None:None}[fault]
            if fault is None:
                assert outcome.p3_output_inventory_ref is not None
                assert 'p3_output_inventory_ref' in outcome.lineage.command
                assert (provider._store_root/artifact_name).read_bytes() == raw
                assert list(provider._output_root.iterdir()),'private output must survive until validation'
                outcome.p3_output_custody.cleanup()
            else:
                assert outcome.p3_output_inventory_ref is None
        assert bool(list(provider._output_root.iterdir())) is (fault in {'identity','invalid_output','cancel','timeout'})
        for fd in consumed[0].pass_fds:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        process.stdout.close()
        process.stderr.close()


def test_projected_driver_imports_official_calculation_owners(tmp_path):
    """Import from only the reviewed projection; the repository is absent from sys.path."""
    import json
    import subprocess
    import sys
    root=Path(__file__).resolve().parents[2]
    inventory=json.loads((root/'docs/implementation/p3/p3-driver-files-v1.json').read_bytes())
    for relative in inventory['paths']:
        target=tmp_path/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((root/relative).read_bytes())
    probe="""import sys
sys.path.insert(0,sys.argv[1])
from scripts import run_p3_alpha_campaign
from packages.alpha_lifecycle import primary_selection, qualification, research_custody, pit_evidence, pit_suite
print('projected official owners imported')
"""
    result=subprocess.run([sys.executable,'-I','-B','-c',probe,str(tmp_path)],cwd=tmp_path,
        capture_output=True,text=True,timeout=30,check=False)
    assert result.returncode==0,result.stderr
    assert result.stdout=='projected official owners imported\n'
