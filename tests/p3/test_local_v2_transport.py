"""LOCAL v2 delivers the complete closure through one read-only image, not per-file seals.

The v1 sealed-file transport spends five bwrap parser tokens on every closure file. The
pinned bubblewrap 0.9.0 parser accepts 9,000 tokens in total, so a complete closure of
roughly seven thousand files cannot be launched at all. These tests hold the property that
fixes it: the file count must not reach the token budget.
"""
from pathlib import Path, PurePosixPath

import pytest

from packages.alpha_lifecycle.sandbox_policy import (
    SANDBOX_PROFILE_SHA256, SANDBOX_PROFILE_V2_SHA256)
from services.job_worker.p3_spawn import _RELEASE, _PYTHON, _image_root_args

BWRAP_PARSER_TOKEN_CAPACITY = 9000
V1_TOKENS_PER_FILE = 5          # --perms MODE --ro-bind-data FD TARGET


class _Image:
    release_root = 'release'
    python_root = 'python'


class _Closure:
    def __init__(self, mount):
        self.image = _Image()
        self.image_mount = mount


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    """Two roots on a stand-in for the verified mount.

    The mount capability itself cannot be created in this suite: building a squashfs,
    attaching a loop device and mounting it need root and a mount namespace. So the
    capability check is replaced here and the argument shape is what these tests hold.
    What the real check refuses is held separately, unpatched, by
    test_an_ordinary_directory_is_not_an_image_mount; the controller's own fences are
    exercised on a real mount by the host probe, not here.
    """
    for name in ('release', 'python'):
        (tmp_path/name).mkdir()
    monkeypatch.setattr('services.job_worker.p3_spawn.verify_image_mount',
                        lambda mount: dict(filesystem='squashfs', root='/', device='7:0',
                                           options=['ro', 'nodev', 'nosuid']))
    return tmp_path


def test_an_ordinary_directory_is_not_an_image_mount(tmp_path):
    """The v2 transport must not fall back to a directory that merely holds two roots.

    Present-and-distinct is what an ordinary writable directory also satisfies, so accepting
    it would make an ordinary directory the fallback for a transport whose whole premise is
    a read-only image.
    """
    for name in ('release', 'python'):
        (tmp_path/name).mkdir()
    with pytest.raises(ValueError, match='not a mount point'):
        _image_root_args(_Closure(tmp_path))


def test_the_mount_capability_is_what_is_checked(tmp_path, monkeypatch):
    """Each property of the mount is required, not merely the presence of a mount entry."""
    from services.job_worker.p3_spawn import verify_image_mount

    for name in ('release', 'python'):
        (tmp_path/name).mkdir()
    accepted = dict(device='7:0', root='/', options=['ro', 'nodev', 'nosuid'],
                    filesystem='squashfs')
    faults = {
        'not a squashfs': dict(accepted, filesystem='ext4'),
        'read-only, nodev and nosuid': dict(accepted, options=['rw', 'nodev', 'nosuid']),
        'subtree rather than the whole image': dict(accepted, root='/release'),
    }
    for expected, record in faults.items():
        fields = (f"29 1 {record['device']} {record['root']} {tmp_path} "
                  f"{','.join(record['options'])} - {record['filesystem']} /dev/loop0 ro")
        monkeypatch.setattr('builtins.open', _mountinfo_reader(fields))
        with pytest.raises(ValueError, match=expected):
            verify_image_mount(tmp_path)
        monkeypatch.undo()


def _mountinfo_reader(line):
    """Return a stand-in for open() that serves one synthetic /proc/self/mountinfo line."""
    import builtins
    import io

    real = builtins.open

    def opener(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return io.BytesIO(line.encode())
        return real(path, *args, **kwargs)
    return opener


def test_image_roots_become_two_aggregate_binds(mounted):
    """Both roots are delivered by path, from the mount this run verified."""
    args = _image_root_args(_Closure(mounted))
    assert args == ('--ro-bind', str(mounted/'release'), str(_RELEASE),
                    '--ro-bind', str(mounted/'python'), str(_PYTHON))


@pytest.mark.parametrize('entry_count', [4306, 6986, 7562, 8192])
def test_closure_size_does_not_enter_the_token_budget(mounted, entry_count):
    """The count that broke v1 must not appear in the v2 argument count at all."""
    v1_lower_bound = V1_TOKENS_PER_FILE*entry_count
    assert v1_lower_bound > BWRAP_PARSER_TOKEN_CAPACITY, 'fixture must reproduce the blocker'
    v2 = len(_image_root_args(_Closure(mounted)))
    assert v2 == 6
    assert v2 < BWRAP_PARSER_TOKEN_CAPACITY


@pytest.mark.parametrize('fault', ['missing_mount', 'relative_mount', 'absent_roots', 'collided_roots'])
def test_image_roots_refuse_an_unusable_mount(tmp_path, fault):
    """A mount that is not present and distinct fails before any argument is emitted."""
    closure = _Closure(tmp_path)
    if fault == 'missing_mount':
        closure.image_mount = None
    elif fault == 'relative_mount':
        closure.image_mount = Path('relative/path')
    elif fault == 'absent_roots':
        pass                                    # tmp_path has neither root
    else:
        (tmp_path/'release').mkdir()
        closure.image.python_root = 'release'
    with pytest.raises(ValueError):
        _image_root_args(closure)


def test_each_transport_keeps_its_own_policy_identity():
    """Neither transport may present the other's accepted policy digest."""
    assert SANDBOX_PROFILE_SHA256 != SANDBOX_PROFILE_V2_SHA256
    assert SANDBOX_PROFILE_SHA256 == '29c4c2c274c4ba2e8adade30ed72ecb39200ecc98a68285949660b036a13c8b8'


def test_system_libraries_are_not_folded_into_the_image_roots(mounted):
    """Host libraries stay individual sealed targets; the plan forbids a directory bind."""
    args = _image_root_args(_Closure(mounted))
    bound = {PurePosixPath(args[i+2]) for i in (0, 3)}
    assert bound == {_RELEASE, _PYTHON}
    assert not any(str(target).startswith(('/lib', '/usr/lib')) for target in bound)


# ---------------------------------------------------------------------------
# Fail closed on the pinned parser's capacity, before any descriptor work
# ---------------------------------------------------------------------------

def _mounts(count):
    from services.job_worker.p3_spawn import P3ClosureMount
    # Zero padded so generation order is also lexical order: the probe requires a sorted,
    # unique inventory and rejects before reaching the capacity gate otherwise.
    return tuple(P3ClosureMount(Path(f'/stage/f{i:05d}'), _RELEASE/f'f{i:05d}', (1, 1000+i), 1,
                                0o444, f'{i:064x}') for i in range(count))


def _closure_with(mounts, *, image):
    from services.job_worker.p3_spawn import CompleteP3Closure, P3Sandbox
    policy = SANDBOX_PROFILE_V2_SHA256 if image else SANDBOX_PROFILE_SHA256
    sandbox = P3Sandbox(Path('/usr/bin/bwrap'), (1, 2), 'a'*64, 0o555, policy,
                        'bubblewrap 0.9.0', ())
    return CompleteP3Closure(object(), object(), Path('/stage/python'), mounts, sandbox,
                             'b'*64, 'job_'+'1'*32, 'c'*64,
                             _Image() if image else None, Path('/stage') if image else None)


def test_startup_refuses_an_unlaunchable_v1_closure_before_doing_any_work(tmp_path, monkeypatch):
    """A closure that cannot fit the parser must be rejected before descriptors exist.

    The historical failure built thousands of memfds, snapshotted the sandbox binary and ran
    probe subprocesses, and only then surfaced bwrap's opaque token error. The capacity is a
    property of the pinned backend, so it is knowable from the inventory alone.
    """
    from services.job_worker import p3_startup

    def refuse(*args, **kwargs):
        raise AssertionError('startup prepared descriptors before checking parser capacity')

    monkeypatch.setattr(p3_startup, '_sealed_memfd', refuse)
    monkeypatch.setattr(p3_startup, '_probe_directories', refuse)
    monkeypatch.setattr(p3_startup, '_run_probe', refuse)
    closure = _closure_with(_mounts(4306), image=False)
    with pytest.raises(ValueError, match='parser capacity'):
        p3_startup.probe_p3_startup(closure, private_root=tmp_path)


def test_the_same_inventory_is_within_capacity_on_the_image_transport(tmp_path):
    """The identical inventory clears the capacity gate once delivered as an image.

    The probe still stops later for unrelated reasons in this synthetic fixture, so the
    assertion is that the capacity gate specifically is no longer what rejects it.
    """
    from services.job_worker import p3_startup

    closure = _closure_with(_mounts(4306), image=True)
    with pytest.raises(ValueError) as raised:
        p3_startup.probe_p3_startup(closure, private_root=tmp_path)
    assert 'parser capacity' not in str(raised.value)


# Measured boundary for this fixture shape, against the directory set the builder actually
# emits: the two distinct parents of the fixture targets, the three fixed roots /p3,
# /p3/store and /p3/output, and the fixed sandbox and filesystem arguments. 1796 files reach
# 8999 and 1797 reach 9004.
#
# It moved by one entry when the estimate and the builder were made to share one definition.
# The estimate used to omit the three fixed roots, so it under-counted by six tokens and
# disagreed with the command it was estimating.
@pytest.mark.parametrize('entry_count,imaged,rejected', [
    (1796, False, False),      # 8999 tokens, the last inventory that fits
    (1797, False, True),       # 9004 tokens, the first that does not
    (4306, False, True),       # the accepted CPython core alone
    (8192, False, True),       # the accepted closure file cap
    (8192, True, False),       # the same cap, delivered as one image
])
def test_capacity_gate_tracks_the_transport_not_the_operator(tmp_path, entry_count, imaged, rejected):
    """The gate is derived from the pinned backend and the inventory, not configured."""
    from services.job_worker import p3_startup

    closure = _closure_with(_mounts(entry_count), image=imaged)
    with pytest.raises(ValueError) as raised:
        p3_startup.probe_p3_startup(closure, private_root=tmp_path)
    assert ('parser capacity' in str(raised.value)) is rejected


# ---------------------------------------------------------------------------
# The production spawn path must follow the transport too
# ---------------------------------------------------------------------------

def test_spawn_charges_only_the_targets_it_actually_seals():
    """Spawn kept the v1 inventory in its directory, argv and descriptor budgets.

    Startup was corrected first and spawn was not, so the production path still refused a
    launch that needs a dozen descriptors whenever the inventory was larger than
    RLIMIT_NOFILE. The seam is the same on both paths or it is not a seam.
    """
    import inspect

    from services.job_worker import p3_spawn

    source = inspect.getsource(p3_spawn.P3SpawnProvider._consume)
    assert 'sealed_targets' in source
    assert 'len(sealed_targets)+16' in source.replace(' ', '')
    assert 'for target in sealed_targets' in source
    # The transport decision has to be made before any budget that depends on it.
    assert source.index('imaged = closure.image is not None') < source.index('sealed_targets')
    assert 'BWRAP_PARSER_TOKEN_CAPACITY' in source


def test_spawn_has_a_final_parser_token_guard():
    """The exact count, after every argument has been added, on both paths."""
    import inspect

    from services.job_worker import p3_spawn

    source = inspect.getsource(p3_spawn.P3SpawnProvider._consume)
    assert 'len(argv)-1 > BWRAP_PARSER_TOKEN_CAPACITY' in source.replace('  ', ' ')


def test_the_capacity_estimate_matches_the_directory_set_the_builder_emits(tmp_path):
    """One definition, or the gate rejects commands it has mis-measured.

    On the image transport the mount supplies the release and Python trees and no --dir is
    emitted for them. The estimate counted their ancestors anyway, so a closure with a few
    thousand image directories was refused before anything was built, on a budget the launch
    would never have spent.
    """
    from services.job_worker.p3_startup import _emitted_directories

    imaged_targets = tuple(sorted(
        [PurePosixPath(f'/p3/release/d{i:04d}/file.py') for i in range(500)]
        + [PurePosixPath(f'/p3/python/lib/d{i:04d}/mod.py') for i in range(500)]))
    emitted = _emitted_directories(imaged_targets, True)
    assert emitted == {PurePosixPath('/p3'), PurePosixPath('/p3/store'),
                       PurePosixPath('/p3/output')}, \
        'the image transport emits no directory for the tree the mount supplies'
    assert len(_emitted_directories(imaged_targets, False)) > 1000, \
        'the sealed-file transport does emit them, and must still be charged for them'
