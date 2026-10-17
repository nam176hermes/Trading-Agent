"""LOCAL work has measured duration; issuance, cancellation and custody still apply."""
from pathlib import Path

import pytest


def test_local_policy_removes_only_cpu_and_work_wall_limits():
    from packages.alpha_lifecycle import sandbox_policy as p
    hosted = p._driver_profile('p3-bubblewrap-driver-profile-v1', 'SEALED_FILES')
    local = p._driver_profile('p3-bubblewrap-driver-profile-v2', 'SQUASHFS_IMAGE_V1')
    assert p.SANDBOX_PROFILE_SHA256 == '29c4c2c274c4ba2e8adade30ed72ecb39200ecc98a68285949660b036a13c8b8'
    assert hosted['outer']['wall_seconds'] == 1200
    assert local['outer']['wall_seconds'] is None
    assert local['outer']['cpu_seconds'] is None
    assert local['child']['child_wall_seconds'] is None
    assert local['child']['child_cpu_seconds'] is None
    for key in ('memory_bytes', 'open_files', 'processes', 'per_file_output_bytes', 'attempt_output_bytes'):
        assert local['outer'][key] == hosted['outer'][key]
    assert local['child_socket_filter_sha256'] == hosted['child_socket_filter_sha256']


@pytest.mark.parametrize('local', [False, True])
def test_nested_child_keeps_non_time_limits(monkeypatch, local):
    import resource
    from packages.alpha_lifecycle import sandbox, sandbox_policy as p
    executor = object.__new__(sandbox.BubblewrapExecutor)
    executor._sandbox_policy_digest = p.SANDBOX_PROFILE_V2_SHA256 if local else p.SANDBOX_PROFILE_SHA256
    calls = {}
    monkeypatch.setattr(resource, 'setrlimit', lambda kind, values: calls.update({kind: values}))
    executor._limits()
    assert (resource.RLIMIT_CPU in calls) is not local
    assert calls[resource.RLIMIT_AS] == (2147483648, 2147483648)
    assert calls[resource.RLIMIT_FSIZE] == (268435456, 268435456)
    assert calls[resource.RLIMIT_NOFILE] == (256, 256)
    assert calls[resource.RLIMIT_NPROC] == (16, 16)


@pytest.mark.host_coupled
@pytest.mark.parametrize('valid', [True, False])
def test_local_private_policy_checks_actual_bubblewrap_copy(valid):
    import os
    import subprocess
    from scripts.run_p3_native_child import LOCAL_POLICY
    from services.job_worker.engine_spawn import _sealed_memfd
    entry = Path(__file__).resolve().parents[2] / 'scripts/run_p3_native_child.py'
    fd = _sealed_memfd('local-work-policy', LOCAL_POLICY if valid else b'wrong-policy', mode=0o400)
    try:
        result = subprocess.run(['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--clearenv',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--ro-bind', str(entry), '/entry.py', '--perms', '0400',
            '--ro-bind-data', str(fd), '/inputs/policy', '--chdir', '/',
            '/usr/bin/python3.12', '-I', '-B', '-S', '-c',
            'import runpy;runpy.run_path("/entry.py")["read_local_policy"]("/inputs/policy")'],
            pass_fds=(fd,), capture_output=True, text=True)
    finally:
        os.close(fd)
    if valid:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0 and 'exact anonymous policy' in result.stderr


def test_local_policy_rejects_named_or_symlink_input(tmp_path):
    from scripts.run_p3_native_child import LOCAL_POLICY, read_local_policy
    substitute = tmp_path / 'substitute'
    substitute.write_bytes(LOCAL_POLICY); substitute.chmod(0o400)
    with pytest.raises(ValueError):
        read_local_policy(substitute)
    alias = tmp_path / 'alias'; alias.symlink_to(substitute)
    with pytest.raises(OSError):
        read_local_policy(alias)


def test_outer_driver_local_keeps_non_time_limits(monkeypatch):
    import resource
    from scripts import run_p3_driver_entry as entry
    from scripts import run_p3_native_child as native
    calls = {}
    executed = []
    monkeypatch.setattr(resource, 'setrlimit', lambda kind, values: calls.update({kind: values}))
    monkeypatch.setattr(entry.sys, 'argv', ['entry.py', native.LOCAL_FLAG, '--job-id', 'fixture'])
    monkeypatch.setattr(native, 'read_local_policy', lambda path: None)
    monkeypatch.setattr(entry.os, 'execv', lambda executable, argv: executed.append(argv))
    entry.main()
    assert resource.RLIMIT_CPU not in calls
    assert calls[resource.RLIMIT_AS] == (2147483648, 2147483648)
    assert native.LOCAL_FLAG not in executed[0]
    assert executed[0][-2:] == ('--job-id', 'fixture')


def test_local_postrun_has_no_elapsed_budget_but_cannot_restart(tmp_path, monkeypatch):
    import hashlib
    import os
    from packages.alpha_lifecycle.sandbox_policy import SANDBOX_PROFILE_V2_SHA256
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from services.job_worker import p3_output
    parent = tmp_path / 'outputs'; parent.mkdir(mode=0o700)
    name = hashlib.sha256(b'job/attempt').hexdigest()
    output = parent / name; output.mkdir(mode=0o700)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    output_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    (tmp_path/'cas').mkdir(mode=0o700)
    custody = p3_output.P3OutputCustody(parent_fd, output_fd, name,
        LocalArtifactStore(tmp_path/'cas'), sandbox_policy_digest=SANDBOX_PROFILE_V2_SHA256)
    os.close(parent_fd); os.close(output_fd)
    try:
        custody.begin_postrun()
        monkeypatch.setattr(p3_output.time, 'monotonic', lambda: 10**9)
        custody.check_deadline()
        with pytest.raises(ValueError, match='restarted'):
            custody.begin_postrun()
    finally:
        custody.abandon()
