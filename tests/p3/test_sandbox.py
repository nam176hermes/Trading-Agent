from pathlib import Path

import pytest

from packages.alpha_lifecycle.sandbox import BubblewrapExecutor, SandboxHeld, require_official_sandbox


def test_missing_bubblewrap_remains_held(tmp_path: Path) -> None:
    with pytest.raises(SandboxHeld, match="HELD"):
        require_official_sandbox(tmp_path / "missing-bwrap")


def test_bubblewrap_recipe_denies_network_and_credentials() -> None:
    source = Path(__file__).parents[2].joinpath("packages/alpha_lifecycle/sandbox.py").read_text()
    source += Path(__file__).parents[2].joinpath("packages/alpha_lifecycle/sandbox_policy.py").read_text()
    assert '--unshare-all' in source
    assert '--clearenv' in source
    assert '--ro-bind' in source
    assert "RLIMIT_CPU" in source and "RLIMIT_AS" in source
    assert "stdin=subprocess.DEVNULL" in source


def test_inner_namespace_receives_sealed_runtime_and_socket_filter(monkeypatch):
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda path:path)
    source=dict(commit_sha='a'*40,tree_sha='b'*40,closure_schema_version='test',
        closure_policy_sha256='c'*64,closure_sha256='d'*64)
    from packages.data_contracts import ArtifactRefV1
    reference=ArtifactRefV1(content_sha256='e'*64,size_bytes=1,media_type='application/json',locator='e'*64+'.blob')
    executor=BubblewrapExecutor(store=None,store_root=Path('/p3/store'),release_root=Path('/p3/release'),
        python=Path('/p3/python/bin/python3.11'),source=source,environment_ref=reference,
        sandbox_policy_digest='f'*64,bwrap=Path('/p3/bin/bwrap'),
        runtime_mounts=(Path('/lib/ld-linux.so'),Path('/p3/python')))
    argv=executor._argv(Path('/p3/output/r1/input'),Path('/p3/output/r1/result'),Path('/p3/output/r1'),17,
                        runtime_fds={Path('/lib/ld-linux.so'):18})
    assert ('--ro-bind','/p3/python','/p3/python') in tuple(zip(argv,argv[1:],argv[2:]))
    assert ('--ro-bind-data','18','/lib/ld-linux.so') in tuple(zip(argv,argv[1:],argv[2:]))
    assert argv[argv.index('--seccomp')+1] == '17'


@pytest.mark.parametrize('readonly',[False,True])
def test_inner_bwrap_requires_readonly_mount_when_owned_by_namespace_user(monkeypatch,readonly):
    import os
    from types import SimpleNamespace
    path=Path('/p3/bin/bwrap')
    monkeypatch.setattr(Path,'stat',lambda *a,**kw:SimpleNamespace(st_mode=0o100500,st_uid=os.geteuid()))
    monkeypatch.setattr(os,'access',lambda *a:True)
    monkeypatch.setattr(os,'statvfs',lambda *a:SimpleNamespace(f_flag=os.ST_RDONLY if readonly else 0))
    if readonly:
        assert require_official_sandbox(path) == path
    else:
        with pytest.raises(SandboxHeld):
            require_official_sandbox(path)


def test_memfd_failure_remains_a_p3_sandbox_hold(isolated_baseline_input,tmp_path,monkeypatch):
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest,InputSet
    from services.job_worker.engine_spawn_interface import EngineSpawnError
    store,ref=isolated_baseline_input
    manifest=BaselineManifest.model_validate_json(store.read_bytes(ref))
    inputs=InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox',lambda path:path)
    def unavailable(*args,**kwargs):
        raise EngineSpawnError('ENGINE_IMMUTABLE_SNAPSHOT_UNAVAILABLE','synthetic memfd failure')
    monkeypatch.setattr('services.job_worker.engine_spawn._sealed_memfd',unavailable)
    executor=BubblewrapExecutor(store=store,store_root=tmp_path/'inputs',release_root=tmp_path,
        python=Path('/p3/python/bin/python3.11'),source=inputs.source,
        environment_ref=inputs.environment_ref,sandbox_policy_digest='c'*64)
    with pytest.raises(SandboxHeld,match='bounded child execution'):
        executor.execute(ref,replicate='R1',logical_trial_id='synthetic',output_dir=tmp_path)


@pytest.mark.parametrize('fault', ['none', 'writable', 'spawn'])
def test_inner_library_descriptors_are_bounded_readonly_and_closed(
        isolated_baseline_input, tmp_path, monkeypatch, fault):
    import os
    import subprocess
    from types import SimpleNamespace
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest, InputSet
    store, ref = isolated_baseline_input
    manifest = BaselineManifest.model_validate_json(store.read_bytes(ref))
    inputs = InputSet.model_validate_json(store.read_bytes(manifest.input_set_ref))
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.require_official_sandbox', lambda p: p)
    executor = BubblewrapExecutor(store=store, store_root=tmp_path/'inputs', release_root=tmp_path,
        python=Path('/p3/python/bin/python3.11'), source=inputs.source,
        environment_ref=inputs.environment_ref, sandbox_policy_digest='c'*64)
    library = tmp_path/'libexample.so'
    library.write_bytes(b'library')
    executor._runtime_mounts = (library,)
    monkeypatch.setattr(os, 'fstatvfs', lambda fd: SimpleNamespace(
        f_flag=0 if fault == 'writable' else os.ST_RDONLY))
    retained = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == library:
            assert flags & os.O_NOFOLLOW
            retained.append(fd)
        return fd

    monkeypatch.setattr(os, 'open', tracked_open)

    def argv(*args, runtime_fds):
        assert fault != 'writable', 'writable library reached child preparation'
        assert os.pread(runtime_fds[library], 20, 0) == b'library'
        return ('probe',)

    def spawn(*args, **kwargs):
        assert retained[0] in kwargs['pass_fds']
        if fault == 'spawn':
            raise OSError('synthetic spawn failure')
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(executor, '_argv', argv)
    monkeypatch.setattr('packages.alpha_lifecycle.sandbox.subprocess.run', spawn)
    with pytest.raises(SandboxHeld):
        executor.execute(ref, replicate='R1', logical_trial_id='synthetic', output_dir=tmp_path)
    assert retained
    for fd in retained:
        with pytest.raises(OSError):
            os.fstat(fd)
