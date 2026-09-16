"""Private native launch checks; synthetic profiles never qualify a host."""
import pytest

from tests.p3.test_holdout_session import session_inputs  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401


def test_unissued_native_launch_cannot_reach_the_runner():
    from services.job_worker.p3_native_spawn import PreparedP3NativeSpawn, consume_prepared_p3_native_spawn
    with pytest.raises(ValueError, match='unissued'):
        consume_prepared_p3_native_spawn(PreparedP3NativeSpawn(), job_id='job_'+'1'*32,
            attempt_id='attempt_'+'2'*32)


def test_native_token_rejects_a_substituted_provider():
    from types import SimpleNamespace
    from services.job_worker.p3_native_spawn import PreparedP3NativeSpawn, consume_prepared_p3_native_spawn
    token = PreparedP3NativeSpawn()
    object.__setattr__(token, '_provider', SimpleNamespace(_consume=lambda *args, **kwargs: None))
    with pytest.raises(ValueError, match='unissued'):
        consume_prepared_p3_native_spawn(token, job_id='job_'+'1'*32, attempt_id='attempt_'+'2'*32)


@pytest.fixture
def native_provider(session_inputs, tmp_path, monkeypatch):
    import hashlib
    from dataclasses import replace
    from pathlib import PurePosixPath
    from types import SimpleNamespace
    from services.job_store.worker_repository import WorkerRepository
    from services.job_worker import p3_native_spawn as native
    from services.job_worker.engine_spawn import ReadOnlyClosureMount, OsSandboxProof
    from packages.engine_contracts.serialization import canonical_json_bytes
    x = session_inputs
    with x.session.stage(x.claim('HOLDOUT'), fence=lambda: None):
        x.session.release(object.__new__(WorkerRepository), trace_id='test:native')
    from packages.alpha_lifecycle.contracts.execution import HoldoutManifest, InstrumentSpec
    from packages.alpha_lifecycle.executable_reference import run_executable_reference, run_selected_baseline_reference
    from packages.alpha_lifecycle.replica_store import _read
    from packages.alpha_lifecycle.holdout import HoldoutOperationResult
    from tests.p3.test_replica_execution import _seal
    manifest = _read(x.store, x.manifest, HoldoutManifest)
    spec = _read(x.store, x.spec, InstrumentSpec)
    refs = tuple(x.store.put_bytes(canonical_json_bytes(calculate(manifest, spec, x.store)), media_type='application/json')
        for calculate in (run_executable_reference, run_selected_baseline_reference))
    placeholder = x.store.put_bytes(b'{}', media_type='application/json')
    held = _seal(x.store, schema_version='p3-holdout-operation-result-v1', holdout_request_ref=placeholder,
        holdout_manifest_ref=x.manifest, holdout_evaluation_ref=placeholder, holdout_replay_ref=placeholder,
        executable_ref=refs[0], baseline_executable_ref=refs[1])
    x.session.holdout_result = _read(x.store, held, HoldoutOperationResult)
    claim = replace(x.claim('PARITY', primary_reference_ref=refs[0], baseline_reference_ref=refs[1]), job_id='job_'+'1'*32, attempt_id='attempt_'+'2'*32)
    x.host['job_id'] = claim.job_id
    x.stage_profile['attempts'][-1].update(job_id=claim.job_id, attempt_id=claim.attempt_id)
    x.stage_profile['host_profile_sha256'] = hashlib.sha256(canonical_json_bytes(x.host)).hexdigest()
    try:
        with x.session.stage(claim, fence=lambda: None):
            document = dict(schema_version='p3-native-launch-profile-v1', source=x.session.profile.source,
                session_sha256=x.session._digest, native_commitment_ref=x.session.native_commitment_ref,
                job_id=claim.job_id, attempt_id=claim.attempt_id, runtime_root='/synthetic/runtime',
                artifact_directory='/synthetic/artifacts', sandbox_executable='/synthetic/bwrap',
                sandbox_sha256=hashlib.sha256(b'synthetic sandbox').hexdigest())
            monkeypatch.setattr(native, 'read_protected_canonical_json_current', lambda path:
                (document, hashlib.sha256(canonical_json_bytes(document)).hexdigest()))
            monkeypatch.setattr(native, 'canonical_source_identity', lambda root: x.session.profile.source.model_dump(mode='json'))
            monkeypatch.setattr(native, '_git', lambda root, command, ref: (root/ref.partition(':')[2]).read_bytes())
            def mount(name, target, raw, mode=0o400):
                path = tmp_path/name; path.write_bytes(raw); path.chmod(mode)
                info = path.stat()
                return ReadOnlyClosureMount(path, PurePosixPath(target), (info.st_dev,info.st_ino),
                    len(raw), mode, hashlib.sha256(raw).hexdigest())
            python = mount('python', '/usr/bin/python3.12', b'synthetic interpreter', 0o500)
            sandbox = mount('bwrap', '/unused', b'synthetic sandbox', 0o500)
            closure = SimpleNamespace(mounts=(python,),
                closure_manifest=mount('manifest', '/engine/closure-manifest.json', b'{}'),
                product_lineage=mount('lineage', '/engine/p1-product-lineage.json', b'{}'),
                sandbox=OsSandboxProof(sandbox.source, sandbox.identity, sandbox.sha256, 'b'*64,
                    'bubblewrap 0.9.0', ()), closure_sha256='c'*64)
            monkeypatch.setattr(native, 'attest_p1_nautilus_closure', lambda config: closure)
            monkeypatch.setattr(native, 'validate_p1_engine_closure_attestation', lambda value: value)
            yield x.session.native_spawn_provider(), x, closure, document
    except ValueError:
        if not x.session._closed:
            raise


def test_native_seals_exact_role_input_and_uses_six_single_use_tokens(native_provider):
    import fcntl
    import os
    from packages.alpha_lifecycle.native_request import native_step_bytes
    from services.job_worker import p3_native_spawn as native
    from services.job_worker.engine_spawn import _F_GET_SEALS, _REQUIRED_MEMFD_SEALS
    provider, x, _, _ = native_provider
    seen = set()
    for role, replica in native._ORDER:
        token = provider.prepare(role, replica)
        built = native.consume_prepared_p3_native_spawn(token,
            job_id=provider._claim.job_id, attempt_id=provider._claim.attempt_id)
        try:
            expected = x.session.native_requests()[0 if role == 'PRIMARY' else 1]
            index = built.argv.index('/inputs/p3-native.json')
            fd = int(built.argv[index-1])
            assert os.pread(fd, 131073, 0) == native_step_bytes(expected)
            assert all(fcntl.fcntl(fd, _F_GET_SEALS) == _REQUIRED_MEMFD_SEALS for fd in built.pass_fds)
            assert built.argv[-5:] == native._COMMAND
            assert '--seccomp' in built.argv and '--unshare-all' in built.argv
            assert built.environment == {} and built.close_after_spawn_fds == built.pass_fds
            seen.add(built.capability_fingerprint)
            with pytest.raises(ValueError, match='consumed'):
                native.consume_prepared_p3_native_spawn(token,
                    job_id=provider._claim.job_id, attempt_id=provider._claim.attempt_id)
        finally:
            for fd in built.close_after_spawn_fds: os.close(fd)
    assert len(seen) == 6
    with pytest.raises(ValueError, match='six'):
        provider.prepare('PRIMARY', 'R1')


@pytest.mark.parametrize('fault', ['profile', 'file', 'expiry', 'claim', 'revoked', 'late_profile'])
def test_changed_native_capability_refuses_and_closes_all_snapshots(native_provider, monkeypatch, fault):
    import os
    from pathlib import Path
    from services.job_worker import p3_native_spawn as native
    provider, x, closure, document = native_provider
    token = provider.prepare('PRIMARY', 'R1')
    if fault == 'profile': document['attempt_id'] = 'attempt_other'
    elif fault == 'file':
        path = closure.mounts[0].source
        path.chmod(0o700); path.write_bytes(b'changed interpreter'); path.chmod(0o500)
    elif fault == 'expiry': monkeypatch.setattr(native.time, 'monotonic', lambda: 1e30)
    elif fault == 'revoked': x.session.close()
    elif fault == 'late_profile':
        snapshot = native._sealed_closure_file_snapshot
        def change_profile(mount):
            fd = snapshot(mount)
            document['attempt_id'] = 'attempt_other'
            return fd
        monkeypatch.setattr(native, '_sealed_closure_file_snapshot', change_profile)
    before = set(Path('/proc/self/fd').iterdir())
    with pytest.raises((ValueError, RuntimeError)):
        native.consume_prepared_p3_native_spawn(token, job_id=provider._claim.job_id,
            attempt_id='attempt_other' if fault == 'claim' else provider._claim.attempt_id)
    assert set(Path('/proc/self/fd').iterdir()) == before
    with pytest.raises(ValueError, match='failed'):
        provider.prepare('PRIMARY', 'R2')


def test_native_role_order_cannot_skip_a_replica(native_provider):
    provider, x, _, _ = native_provider
    with pytest.raises(ValueError, match='ordered'):
        provider.prepare('SELECTED_BASELINE', 'R1')
    with pytest.raises(ValueError, match='already issued'):
        x.session.native_spawn_provider()


def test_dropped_native_token_cannot_skip_an_unlaunched_replica(native_provider):
    import gc
    provider, _, _, _ = native_provider
    token = provider.prepare('PRIMARY', 'R1')
    del token
    gc.collect()
    with pytest.raises(ValueError, match='ordered'):
        provider.prepare('PRIMARY', 'R2')


@pytest.mark.parametrize('fault', [None, 'cancel', 'popen'])
def test_existing_process_runner_consumes_native_authority_and_closes_fds(native_provider, tmp_path, monkeypatch, fault):
    import os
    from services.job_worker import process_runner, p3_native_spawn as native
    from tests.jobs.test_process_runner import runner, FakeProcess, Inspector, identity
    provider, _, _, _ = native_provider
    built_values = []
    consume = native.consume_prepared_p3_native_spawn
    def observe(*args, **kwargs):
        built = consume(*args, **kwargs); built_values.append(built)
        return built
    monkeypatch.setattr(native, 'consume_prepared_p3_native_spawn', observe)
    monkeypatch.setattr(process_runner, 'build_child_environment', lambda value: pytest.fail('ambient environment'))
    child, calls = FakeProcess([None, 0]), []
    execution = runner(tmp_path, child, Inspector([identity()]), calls)
    if fault == 'popen':
        def fail(*args, **kwargs): raise OSError('synthetic Popen refusal')
        execution._popen = fail
    def run():
        return execution.run(lambda: provider.prepare('PRIMARY', 'R1'), None, 300,
            lambda value: process_runner.HeartbeatDecision.CANCEL if fault == 'cancel'
                else process_runner.HeartbeatDecision.CONTINUE,
            job_id=provider._claim.job_id, attempt_id=provider._claim.attempt_id)
    try:
        if fault == 'popen':
            with pytest.raises(OSError, match='Popen'): run()
        else:
            result = run()
            assert result.termination_reason == ('CANCELLED' if fault == 'cancel' else None)
            assert calls[0][1]['env'] == {} and calls[0][1]['start_new_session'] is True
        assert len(built_values) == 1
        for fd in built_values[0].close_after_spawn_fds:
            with pytest.raises(OSError): os.fstat(fd)
    finally:
        child.stdout.close(); child.stderr.close()


@pytest.mark.parametrize('fault', [None, 'argv', 'version', 'isolation', 'environment', 'cwd',
    'linked', 'mode', 'oversized', 'empty', 'short_read', 'adapter'])
def test_native_entry_checks_transport_before_loading_adapter(tmp_path, monkeypatch, fault):
    import os
    import resource
    import sys
    from contextlib import contextmanager
    from types import SimpleNamespace
    from scripts import run_p3_native_child as entry
    from services.job_worker.engine_spawn import _sealed_memfd

    raw = b'' if fault == 'empty' else b'x' * 131073 if fault == 'oversized' else b'[]'
    if fault == 'linked':
        path = tmp_path/'request'; path.write_bytes(raw); path.chmod(0o400)
        source = os.open(path, os.O_RDONLY)
    else:
        source = _sealed_memfd('native-entry-test', raw, mode=0o600 if fault == 'mode' else 0o400)
    opened, limits, events = [], [], []
    state = SimpleNamespace(orig_argv=() if fault == 'argv' else entry.COMMAND,
        version_info=(3, 11) if fault == 'version' else (3, 12),
        flags=SimpleNamespace(isolated=fault != 'isolation', no_site=True, dont_write_bytecode=True),
        path=[], stdin=None)
    monkeypatch.setattr(entry, 'sys', state)
    def open_request(path, flags):
        assert path == entry.REQUEST
        assert flags == os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        descriptor = os.dup(source); opened.append(descriptor)
        return descriptor
    monkeypatch.setattr(entry, 'os', SimpleNamespace(**{name:getattr(os, name) for name in
        ('O_RDONLY', 'O_NOFOLLOW', 'O_CLOEXEC', 'O_NONBLOCK', 'fstat', 'close')},
        open=open_request, pread=(lambda *args: b'') if fault == 'short_read' else os.pread,
        environ={} if fault == 'environment' else entry.ENVIRONMENT,
        getcwd=lambda: '/tmp' if fault == 'cwd' else '/'))
    monkeypatch.setattr(entry, 'resource', SimpleNamespace(**{name:getattr(resource, name)
        for name in entry.RESOURCE_LIMITS}, setrlimit=lambda *args: limits.append(args)))
    @contextmanager
    def scope():
        events.append('enter')
        try: yield ()
        finally: events.append('exit')
    monkeypatch.setitem(sys.modules, 'runtime_v1.dependency_scope', SimpleNamespace(sealed_wheel_imports=scope))
    def adapter(path, run_name):
        assert path == entry.ADAPTER and run_name == '__main__'
        assert state.stdin.buffer.read() == raw and state.path == ['/engine']
        events.append('adapter')
        if fault == 'adapter': raise ValueError('synthetic adapter failure')
    monkeypatch.setattr(entry, 'runpy', SimpleNamespace(run_path=adapter))
    try:
        if fault is None:
            entry.main()
        else:
            with pytest.raises(ValueError): entry.main()
        if fault in (None, 'adapter'):
            assert events == ['enter', 'adapter', 'exit']
            assert limits == [(getattr(resource, name), (bound, bound))
                for name, bound in entry.RESOURCE_LIMITS.items()]
        else:
            assert not events
        for descriptor in opened:
            with pytest.raises(OSError): os.fstat(descriptor)
    finally:
        os.close(source)
        if state.stdin is not None: state.stdin.close()
