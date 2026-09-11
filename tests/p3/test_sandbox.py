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
    argv=executor._argv(Path('/p3/output/r1/input'),Path('/p3/output/r1/result'),Path('/p3/output/r1'),17)
    for path in ('/p3/python','/lib/ld-linux.so'):
        assert any(argv[i:i+3] == ('--ro-bind',path,path) for i in range(len(argv)-2))
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


def test_memfd_failure_remains_a_p3_sandbox_hold(tmp_path,monkeypatch):
    from tests.p3.test_replica_execution import baseline_inputs
    from packages.alpha_lifecycle.contracts.execution import BaselineManifest,InputSet
    from services.job_worker.engine_spawn_interface import EngineSpawnError
    store,ref=baseline_inputs(tmp_path/'inputs')
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
