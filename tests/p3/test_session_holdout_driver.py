"""Real holdout driver/calculation children with synthetic sandbox and authority."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from packages.alpha_lifecycle.holdout import derive_holdout_request
from packages.alpha_lifecycle.holdout_view import HoldoutCalculationView, build_holdout_calculation_view
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.replica_store import _read
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from tests.p3.test_holdout_session import session_inputs  # noqa: F401
from tests.p3.test_reference_input import reference_seed  # noqa: F401


@pytest.fixture
def projected_release(tmp_path):
    root = Path(__file__).resolve().parents[2]
    release = tmp_path/'release'
    for name in json.loads((root/'docs/implementation/p3/p3-driver-files-v1.json').read_bytes())['paths']:
        destination = release/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/name, destination)
    return release


def test_projected_driver_imports(projected_release):
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        'import sys,runpy; sys.path.insert(0,sys.argv[1]); '
        'import packages.alpha_lifecycle.holdout; '
        'runpy.run_path(sys.argv[1]+"/scripts/run_p3_evaluation_child.py"); '
        'runpy.run_path(sys.argv[1]+"/scripts/run_p3_alpha_campaign.py")', str(projected_release)],
        cwd=projected_release, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.host_coupled
@pytest.mark.parametrize('mode', ['600', '644'])
@pytest.mark.skipif(os.environ.get('P3_SEALED_VIEW_SOURCE_TEST') != '1',
    reason='explicit transient Bubblewrap source check required')
def test_anonymous_view_transport(projected_release, mode):
    from services.job_worker.engine_spawn import _sealed_memfd
    fd = _sealed_memfd('view-reader-test', b'{}', mode=0o600)
    try:
        result = subprocess.run(['/usr/bin/bwrap', '--unshare-all', '--die-with-parent',
            '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
            '--perms', '700', '--dir', '/tmp/inputs', '--perms', mode,
            '--ro-bind-data', str(fd), '/tmp/inputs/view.json', sys.executable, '-I', '-B', '-c',
            'import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            'from packages.alpha_lifecycle.holdout_view import read_calculation_view; '
            'assert read_calculation_view(Path("/tmp/inputs/view.json")) == b"{}"', str(projected_release)],
            pass_fds=(fd,), capture_output=True, text=True, timeout=10)
    finally:
        os.close(fd)
    if mode == '600':
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0 and 'calculation view metadata' in result.stderr


@pytest.mark.parametrize('fault', ['mode', 'hardlink', 'symlink', 'fifo', 'directory', 'empty'])
def test_view_reader_rejects_unsafe_named_inputs(tmp_path, fault):
    from packages.alpha_lifecycle.holdout_view import read_calculation_view
    directory = tmp_path/'inputs'; directory.mkdir(mode=0o700)
    path = directory/'view'; path.write_bytes(b'{}'); path.chmod(0o600)
    if fault == 'mode':
        path.chmod(0o644)
    elif fault == 'hardlink':
        os.link(path, directory/'alias')
    elif fault in ('symlink', 'fifo'):
        path.unlink()
        if fault == 'symlink':
            path.symlink_to(directory/'missing')
        else:
            os.mkfifo(path, mode=0o600)
    elif fault == 'directory':
        directory.chmod(0o755)
    else:
        path.write_bytes(b'')
    with pytest.raises((ValueError, OSError)):
        read_calculation_view(path)


def test_view_read_allows_access_time_update(tmp_path):
    from packages.alpha_lifecycle.holdout_view import read_calculation_view
    path = tmp_path/'view'; path.write_bytes(b'{}'); path.chmod(0o600)
    os.utime(path, (1, 1))
    assert read_calculation_view(path) == b'{}'


@pytest.mark.parametrize('extra', [None, 'raw', 'unrelated'])
def test_driver_holdout_validates_before_retention(session_inputs, reference_seed, projected_release, tmp_path, monkeypatch, capsys, extra):
    from scripts import run_p3_alpha_campaign as command
    from packages.alpha_lifecycle import sandbox
    from services.job_worker.p3_output import P3OutputCustody
    from services.job_worker.p3_output_validation import validate_holdout_output
    x = session_inputs
    job = x.claim('HOLDOUT')
    intent = _read(x.store, job.payload.manifest_ref, P3OperationInput)
    auth = _read(x.store, job.payload.authorization_ref, RunAuthorization)
    request = derive_holdout_request(intent, auth, expected_source=job.payload.expected_source)
    request_ref = x.store.put_bytes(canonical_json_bytes(request), media_type='application/json')
    view = HoldoutCalculationView(build_holdout_calculation_view(x.manifest, x.spec, x.store), x.manifest, x.spec)
    inputs = tmp_path/'inputs'; inputs.mkdir(mode=0o700)
    view_path = inputs/'view.json'; view_path.write_bytes(view.raw); view_path.chmod(0o600)
    argv = [str(command.__file__)]
    for name, value in [('manifest-ref', job.payload.manifest_ref), ('source', job.payload.expected_source),
        ('environment-ref', x.session.profile.environment_ref), ('authorization-ref', job.payload.authorization_ref),
        ('holdout-manifest-ref', x.manifest), ('holdout-request-ref', request_ref), ('instrument-spec-ref', x.spec)]:
        path = inputs/(name+'.json'); path.write_bytes(canonical_json_bytes(value))
        argv += ['--'+name, str(path)]
    runs = tmp_path/'runs'; runs.mkdir(mode=0o700)
    name = hashlib.sha256(f'{job.job_id}/{job.attempt_id}'.encode()).hexdigest()
    output = runs/name
    root = projected_release
    argv += ['--store', str(x.store._root), '--release', str(root), '--python', sys.executable,
        '--sandbox-policy-digest', 'c'*64, '--logical-trial-id', job.payload.logical_trial_id,
        '--job-id', job.job_id, '--output', str(output), '--holdout-view', str(view_path)]
    monkeypatch.setattr(sys, 'argv', argv)
    grants = []
    monkeypatch.setattr(command, 'parent_replica_fence', lambda: lambda: grants.append(True))
    monkeypatch.setattr(sandbox, 'require_official_sandbox', lambda path: path)
    def child_argv(self, manifest, result, output_dir, seccomp_fd, view_fd=None):
        return (sys.executable, '-I', '-B', str(root/'scripts/run_p3_evaluation_child.py'),
            str(manifest), str(view_path), str(result), '--instrument-spec-ref', canonical_json_bytes(x.spec).decode())
    monkeypatch.setattr(sandbox.BubblewrapExecutor, '_argv', child_argv)
    for ref in (*reference_seed[4], reference_seed[5]):
        (x.store._root/ref.locator).unlink()
    before = set(x.store._root.iterdir())
    command.main()
    assert grants and set(x.store._root.iterdir()) == before
    assert 'p3-holdout-operation-result-v1' in capsys.readouterr().out
    private = LocalArtifactStore(output/'artifacts')
    if extra:
        private.put_bytes(view.read_bytes(reference_seed[4][0]) if extra == 'raw'
            else b'{"unexpected":true}', media_type='application/json')
    parent_fd = os.open(runs, os.O_RDONLY|os.O_DIRECTORY)
    output_fd = os.open(output, os.O_RDONLY|os.O_DIRECTORY)
    try:
        custody = P3OutputCustody(parent_fd, output_fd, name, x.store,
            before_retain=lambda inventory, reader: validate_holdout_output(job, inventory, reader, x.store, view))
    finally:
        os.close(parent_fd); os.close(output_fd)
    try:
        if extra:
            with pytest.raises(ValueError, match='outside recomputed outputs'):
                custody.retain()
            assert set(x.store._root.iterdir()) == before
        else:
            custody.retain_and_cleanup()
            assert not output.exists()
        assert all(not (x.store._root/ref.locator).exists() for ref in (*reference_seed[4], reference_seed[5]))
    finally:
        custody.abandon()
        view.close()
