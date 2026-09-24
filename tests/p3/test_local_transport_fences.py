"""The controller's fences must bind the image, not merely describe it.

The first version of these fences compared metadata: inode, size, mode, and that a read-only
squashfs was mounted somewhere. None of that reaches the bytes. The review's finding was
specific -- the retained backing bytes were never re-hashed, the mount was never tied to the
loop device serving it, the loop window was never constrained, and the challenge had both
sides read the *current* mount, so a substituted filesystem that answered consistently
produced matching responses on both sides.

These hold the properties that close that: bytes over metadata, and a challenge answered
against what the inventory recorded rather than against whatever is mounted now.
"""
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    'p3_local_transport', Path(__file__).resolve().parents[2]/'scripts'/'p3_local_transport.py')
transport = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(transport)

TransportHeld = transport.TransportHeld
NONCE = 'a1b2c3d4e5f60718' + '9' * 48


def _stage(tmp_path, contents):
    stage = tmp_path/'stage'
    for relative, payload in contents.items():
        target = stage/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return stage


# ---------------------------------------------------------------------------
# The challenge has to be answered against the admitted bytes
# ---------------------------------------------------------------------------

def _inventory(mount):
    return transport.logical_inventory(mount)


def test_the_challenge_is_answered_against_the_inventory_not_the_mount(tmp_path):
    """A mount serving other bytes must fail here, not agree with itself.

    Both sides reading the mount proves the mount is stable. It does not prove the bytes are
    the admitted ones, which is the only thing the verified backing reaches.
    """
    mount = _stage(tmp_path, {f'release/f{i}': f'payload-{i}'.encode() for i in range(12)})
    inventory = _inventory(mount)
    assert transport.mount_challenge(mount, inventory, NONCE)

    substituted = _stage(tmp_path/'other', {f'release/f{i}': f'payload-{i}'.encode()
                                            for i in range(12)})
    (substituted/'release'/'f0').write_bytes(b'substituted')
    with pytest.raises(TransportHeld, match='serves different bytes'):
        transport.mount_challenge(substituted, inventory, NONCE)


def test_the_challenge_response_is_bound_to_its_nonce(tmp_path):
    mount = _stage(tmp_path, {f'release/f{i}': f'payload-{i}'.encode() for i in range(12)})
    inventory = _inventory(mount)
    other = 'f1e2d3c4b5a60718' + '0' * 48
    assert transport.mount_challenge(mount, inventory, NONCE) \
        != transport.mount_challenge(mount, inventory, other)


def test_the_challenge_reads_a_bounded_prefix(tmp_path):
    """A large entry must not turn a liveness check into an unbounded read.

    The consequence is stated rather than hidden: for an entry above the bound the challenge
    covers its prefix, so a difference confined to the tail is not what this catches. The
    whole-file digest is bound by the image digest and the closure fingerprint instead.

    What the prefix *is* compared against is the digest the inventory recorded. Comparing
    only entries that fit inside the bound would have left every large entry unchecked, with
    both sides reading the current mount and agreeing with each other -- the exact shape the
    fix was supposed to remove.
    """
    prefix = b'x' * (2 * transport.CHALLENGE_ENTRY_BYTES)
    mount = _stage(tmp_path, dict({f'release/f{i}': b'small' for i in range(11)},
                                  **{'release/big': prefix + b'original-tail'}))
    inventory = _inventory(mount)
    # Every entry is challenged over all eight draws, so the big one is reached.
    baseline = transport.mount_challenge(mount, inventory, NONCE)
    (mount/'release'/'big').write_bytes(prefix + b'different-tail!')
    assert transport.mount_challenge(mount, inventory, NONCE) == baseline, \
        'a tail change beyond the bound was read, so the challenge is not bounded'
    (mount/'release'/'big').write_bytes(b'y' + prefix[1:] + b'original-tail')
    with pytest.raises(TransportHeld, match='serves different bytes'):
        transport.mount_challenge(mount, inventory, NONCE)


# ---------------------------------------------------------------------------
# The backing bytes, and the loop window that serves them
# ---------------------------------------------------------------------------

def _image(tmp_path, payload=b'squashfs-image-bytes'):
    path = tmp_path/'runtime.squashfs'
    path.write_bytes(payload)
    os.chmod(path, 0o400)
    fd = os.open(path, os.O_RDONLY)
    info = os.fstat(fd)
    descriptor = dict(backing_dev=info.st_dev, backing_ino=info.st_ino,
                      image_size_bytes=info.st_size,
                      image_sha256=hashlib.sha256(payload).hexdigest())
    return fd, descriptor, info


def _loop(info, **overrides):
    status = dict(backing_dev=info.st_dev, backing_ino=info.st_ino, offset=0,
                  sizelimit=info.st_size, number=0, read_only=True, autoclear=True)
    status.update(overrides)
    return status


@pytest.mark.skipif(os.geteuid() == 0, reason='the fixture image must not be root-owned here')
def test_backing_verification_requires_root_ownership(tmp_path, monkeypatch):
    fd, descriptor, info = _image(tmp_path)
    monkeypatch.setattr(transport, 'loop_status', lambda _fd: _loop(info))
    try:
        with pytest.raises(TransportHeld, match='root-owned 0400 file'):
            transport._verify_backing(descriptor, fd, -1, deadline=float('inf'), rehash=True)
    finally:
        os.close(fd)


def test_backing_bytes_are_rehashed_not_assumed(tmp_path, monkeypatch):
    """Inode and size say the file looks right; only the digest says the bytes are right."""
    fd, descriptor, info = _image(tmp_path)
    monkeypatch.setattr(transport, 'loop_status', lambda _fd: _loop(info))
    monkeypatch.setattr(transport.stat, 'S_IMODE', lambda _mode: 0o400)
    monkeypatch.setattr(transport.os, 'fstat', lambda _fd: _Stat(info))
    try:
        assert transport._verify_backing(descriptor, fd, -1, deadline=float('inf'),
                                         rehash=True)['read_only']
        descriptor['image_sha256'] = 'f' * 64
        with pytest.raises(TransportHeld, match='differ from the admitted image digest'):
            transport._verify_backing(descriptor, fd, -1, deadline=float('inf'), rehash=True)
    finally:
        os.close(fd)


class _Stat:
    """A stat result that reports the fixture as the root-owned single-link image."""

    def __init__(self, info):
        self.st_dev, self.st_ino, self.st_size = info.st_dev, info.st_ino, info.st_size
        self.st_mode = stat.S_IFREG | 0o400
        self.st_uid, self.st_nlink = 0, 1


@pytest.mark.parametrize('fault,expected', [
    (dict(offset=512), 'serve the whole verified image'),
    (dict(sizelimit=8), 'serve the whole verified image'),
    (dict(read_only=False), 'read-only with autoclear'),
    (dict(autoclear=False), 'read-only with autoclear'),
    (dict(backing_ino=999999), 'different backing inode'),
])
def test_the_loop_window_must_serve_the_whole_verified_image(tmp_path, monkeypatch, fault, expected):
    """A device that verifies against the file and then serves a window of it is the
    substitution these fences exist to refuse."""
    fd, descriptor, info = _image(tmp_path)
    monkeypatch.setattr(transport, 'loop_status', lambda _fd: _loop(info, **fault))
    monkeypatch.setattr(transport.os, 'fstat', lambda _fd: _Stat(info))
    try:
        with pytest.raises(TransportHeld, match=expected):
            transport._verify_backing(descriptor, fd, -1, deadline=float('inf'), rehash=True)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# The stage walker
# ---------------------------------------------------------------------------

def test_a_symlinked_directory_in_the_stage_is_refused(tmp_path):
    """chmod follows symlinks, so normalising a symlinked directory by name reaches outside."""
    stage = _stage(tmp_path, {'release/file': b'x'})
    outside = tmp_path/'outside'
    outside.mkdir()
    (stage/'python').symlink_to(outside, target_is_directory=True)
    with pytest.raises(TransportHeld, match='symlinked directory'):
        transport.normalise_stage_modes(stage)
    assert stat.S_IMODE(outside.stat().st_mode) != 0o555, 'the mode outside the stage changed'


def test_the_inventory_enumerates_the_tree_not_only_the_files(tmp_path):
    stage = _stage(tmp_path, {'release/a': b'1', 'release/nested/b': b'2', 'python/c': b'3'})
    inventory = transport.logical_inventory(stage)
    assert inventory['entry_count'] == 3
    assert set(inventory['directories']) == {'release', 'release/nested', 'python'}
    assert inventory['directory_count'] == 3


def test_an_extended_attribute_in_the_stage_is_refused(tmp_path):
    """The image is built with -no-xattrs, so an attribute here would be silently dropped."""
    stage = _stage(tmp_path, {'release/a': b'1'})
    try:
        os.setxattr(stage/'release'/'a', b'user.p3', b'1')
    except OSError as error:
        pytest.skip(f'extended attributes are unavailable on this filesystem: {error}')
    with pytest.raises(TransportHeld, match='extended attributes'):
        transport.logical_inventory(stage)


# ---------------------------------------------------------------------------
# The toolchain has to be pinned by the issuer, not by whatever is at the path
# ---------------------------------------------------------------------------

def test_the_request_must_pin_every_executable_the_transport_runs(tmp_path, monkeypatch):
    """A digest read off the current bytes and then used as the authority pins nothing.

    mksquashfs, the dynamic loader and bubblewrap all execute during a transport run, so a
    request that names no expected digest for one of them cannot be satisfied.
    """
    accepted = dict(schema_version='p3-local-transport-request-v1', operation='prepare',
                    identity=dict(host_id='h', boot_id='b'),
                    mksquashfs_sha256='a'*64, loader_sha256='b'*64, bwrap_sha256='c'*64)
    monkeypatch.setattr(transport, '_host_identity', lambda: ('h', 'b'))
    for field in ('mksquashfs_sha256', 'loader_sha256', 'bwrap_sha256'):
        request = dict(accepted)
        request.pop(field)
        with pytest.raises(TransportHeld, match=f'does not pin {field}'):
            _read_request_body(request, monkeypatch)
    for field, value in (('mksquashfs_sha256', 'not-a-digest'), ('loader_sha256', 'A'*64)):
        with pytest.raises(TransportHeld, match=f'does not pin {field}'):
            _read_request_body(dict(accepted, **{field: value}), monkeypatch)
    assert _read_request_body(accepted, monkeypatch)['operation'] == 'prepare'


def _read_request_body(request, monkeypatch):
    """Drive read_request's validation over a supplied document, without the protected file."""
    from packages.engine_contracts.serialization import canonical_json_bytes
    raw = canonical_json_bytes(request)
    calls = {}

    def fake_open(path, flags):
        calls['path'] = path
        return -1

    monkeypatch.setattr(transport.os, 'open', fake_open)
    monkeypatch.setattr(transport.os, 'close', lambda _fd: None)
    monkeypatch.setattr(transport.os, 'read', lambda _fd, _size: raw)
    monkeypatch.setattr(transport.os, 'fstat', lambda _fd: _RootOwned())
    monkeypatch.setattr(transport.stat, 'S_ISREG', lambda _mode: True)
    return transport.read_request('request.json')


class _RootOwned:
    st_mode = stat.S_IFREG | 0o600
    st_uid = st_gid = 0


def test_the_declared_dependencies_come_from_the_object_itself():
    """DT_NEEDED is read from the ELF, so the resolver's list can be checked, not trusted."""
    declared = transport._elf_needed(transport.MKSQUASHFS)
    assert 'libc.so.6' in declared
    assert all(name and '/' not in name for name in declared)


def test_a_non_elf_object_is_refused(tmp_path):
    plain = tmp_path/'not-an-elf'
    plain.write_bytes(b'#!/bin/sh\necho hello\n')
    with pytest.raises(TransportHeld, match='not a little-endian 64-bit ELF'):
        transport._elf_needed(plain)


def _digest_of(path):
    import hashlib as _hashlib

    return _hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_the_toolchain_manifest_covers_every_executable_the_transport_runs(tmp_path):
    """Hashing one tool's own record left both its dependencies and the other tool unpinned.

    bubblewrap executes too. Covering only mksquashfs was the same omission one level up.
    """
    loader_fd, _ = transport.open_verified_tool(transport.LOADER, _digest_of(transport.LOADER))
    tool_fd, tool = transport.open_verified_tool(transport.MKSQUASHFS,
                                                 _digest_of(transport.MKSQUASHFS))
    sandbox_fd, sandbox = transport.open_verified_tool(transport.BWRAP,
                                                       _digest_of(transport.BWRAP))
    try:
        manifest = transport.toolchain_manifest(
            [(transport.MKSQUASHFS, tool), (transport.BWRAP, sandbox)], loader_fd, tmp_path)
    finally:
        for fd in (loader_fd, tool_fd, sandbox_fd):
            os.close(fd)
    roles = [member['role'] for member in manifest['members']]
    assert roles.count('tool') == 2
    assert roles.count('dependency') >= 1, 'the dependency closure must not be empty'
    assert all(len(member['sha256']) == 64 for member in manifest['members'])
    named = {Path(member['path']).name for member in manifest['members']
             if member['role'] == 'dependency'}
    for executable in (transport.MKSQUASHFS, transport.BWRAP):
        declared = transport._elf_needed(executable)
        assert declared.issubset(named), f'unpinned dependencies of {executable}: {declared - named}'
    assert not any(member.get('path', '').startswith('/proc/self/fd/')
                   for member in manifest['members'])
    assert len({member.get('path') for member in manifest['members']
                if member['role'] == 'dependency'}) == roles.count('dependency'), \
        'a shared dependency must be listed once'


def test_a_sealed_tool_cannot_be_written_and_still_executes(tmp_path):
    """Executing the file binds the inode, not the bytes it held when it was hashed.

    A writer with access to the file can change them in place between the verification and
    the exec, so the verified bytes are not the bytes that run. The sealed copy removes that
    window rather than narrowing it.
    """
    import subprocess as _subprocess

    fd, record = transport.seal_verified_tool(transport.MKSQUASHFS, _digest_of(transport.MKSQUASHFS))
    try:
        assert record['sealed'] is True
        with pytest.raises(OSError):
            os.pwrite(fd, b'\0', 0)
        result = _subprocess.run([f'/proc/self/fd/{fd}', '-version'],
                                 capture_output=True, pass_fds=(fd,))
        assert result.returncode == 0 and b'mksquashfs version' in result.stdout
    finally:
        os.close(fd)


def test_a_tool_that_does_not_match_its_pinned_digest_is_refused():
    with pytest.raises(TransportHeld, match='does not match its pinned digest'):
        transport.seal_verified_tool(transport.MKSQUASHFS, 'f' * 64)


def test_a_library_nothing_in_the_closure_declares_is_refused(tmp_path, monkeypatch):
    """The resolver's output is checked against DT_NEEDED, not taken on trust.

    Running the listing through the verified loader binds the bytes that produce it. It does
    not bind the answer, so a resolver that named a library the closure never asked for would
    otherwise have it sealed and delivered.
    """
    import shutil

    outside = tmp_path/'outside'
    outside.mkdir()
    # A real ELF object, so the refusal is about the name nothing declares rather than about
    # the file being unreadable.
    planted = outside/'libnothing.so.9'
    shutil.copy('/lib/x86_64-linux-gnu/libz.so.1', planted)

    class _Result:
        returncode = 0
        stderr = b''
        stdout = (b'\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x1)\n'
                  b'\tlibnothing.so.9 => ' + str(planted).encode() + b' (0x2)\n')

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Result())
    with pytest.raises(TransportHeld, match='nothing in the closure declares'):
        transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage', loader_fd=-1)


def test_a_resolved_dependency_that_cannot_be_read_is_refused(tmp_path, monkeypatch):
    class _Missing:
        returncode = 0
        stderr = b''
        stdout = b'\tlibghost.so.1 => /nonexistent/libghost.so.1 (0x1)\n'

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Missing())
    with pytest.raises(TransportHeld, match='could not be read'):
        transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage', loader_fd=-1)


def test_an_intermediate_inside_the_stage_still_declares_its_dependencies(tmp_path, monkeypatch):
    """A stage-internal library is skipped for sealing, not for the declared union.

    Found by running `prepare` on the real stage: `libpython3.so` declares one name, and the
    loader resolves libdl, libm, librt and libutil through the in-stage
    `libpython3.11.so.1.0`. Computing the union over the external set alone made all four
    look undeclared and refused a correct closure.

    The graph here is that shape, built from real ELF files: the root declares the
    intermediate, the intermediate sits inside the stage, and the name it brings in is
    resolved outside the stage. Take the in-stage object out of the union and the last one
    is undeclared, which is exactly the refusal that was wrong.
    """
    import shutil

    stage = tmp_path/'stage'
    stage.mkdir()
    root = stage/'root.so'
    shutil.copy('/lib/x86_64-linux-gnu/liblzma.so.5', root)
    declared_by_root = transport._elf_needed(root)
    assert declared_by_root == {'libc.so.6'}, declared_by_root

    # A real ELF, placed inside the stage under a name the root declares. What matters is
    # that it declares names the root does not.
    intermediate = stage/'libc.so.6'
    shutil.copy(transport.MKSQUASHFS, intermediate)
    brought_in = sorted(transport._elf_needed(intermediate) - declared_by_root
                        - {'ld-linux-x86-64.so.2'})
    assert brought_in, 'the intermediate must declare something the root does not'

    class _Result:
        returncode = 0
        stderr = b''
        stdout = (f'\tlibc.so.6 => {intermediate} (0x1)\n'
                  + ''.join(f'\t{name} => /lib/x86_64-linux-gnu/{name} (0x2)\n'
                            for name in brought_in)).encode()

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Result())
    resolved = transport.library_closure([root], stage, loader_fd=-1)
    assert not any(str(item).startswith(str(stage)) for item in resolved), \
        'a stage-internal library must not be sealed as an external one'
    assert {Path(item).name for item in resolved} >= set(brought_in)


def test_a_path_form_dependency_is_parsed_and_counted(tmp_path, monkeypatch):
    """A DT_NEEDED written as a path is listed by the loader with no `=>` at all.

    Found by running `prepare`: CPython's libpython3.so declares
    `$ORIGIN/../lib/libpython3.11.so.1.0`, and the loader prints the resolved file on its own
    line. Parsing only the arrow form dropped that object from the closure, so the four
    libraries it brings in -- libdl, librt, libm, libutil -- looked undeclared and a correct
    closure was refused.
    """
    import shutil

    stage = tmp_path/'stage'
    stage.mkdir()
    root = stage/'root.so'
    shutil.copy('/lib/x86_64-linux-gnu/liblzma.so.5', root)
    intermediate = stage/'libintermediate.so.1'
    shutil.copy(transport.MKSQUASHFS, intermediate)
    brought_in = sorted(transport._elf_needed(intermediate)
                        - transport._elf_needed(root) - {'ld-linux-x86-64.so.2'})
    assert brought_in, 'the intermediate must declare something the root does not'

    class _Result:
        returncode = 0
        stderr = b''
        # The intermediate appears in path form, exactly as the loader prints it.
        stdout = (f'\tlinux-vdso.so.1 (0x1)\n'
                  f'\t{intermediate} (0x2)\n'
                  + ''.join(f'\t{name} => /lib/x86_64-linux-gnu/{name} (0x3)\n'
                            for name in brought_in)).encode()

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Result())
    resolved = transport.library_closure([root], stage, loader_fd=-1)
    assert {Path(item).name for item in resolved} >= set(brought_in)
    assert not any(str(item).startswith(str(stage)) for item in resolved)
    assert not any('vdso' in str(item) for item in resolved), 'the vdso has no file to seal'


def test_the_resolved_set_is_accepted_when_every_name_is_declared(tmp_path, monkeypatch):
    real = transport.subprocess.run
    resolved = transport.library_closure([transport.MKSQUASHFS], tmp_path,
                                         loader_fd=_verified_loader())
    assert any(Path(item).name == 'libc.so.6' for item in resolved)
    assert transport.LOADER_SONAME.as_posix() in [str(item) for item in resolved]
    del real


def _verified_loader() -> int:
    import hashlib as _hashlib

    fd, _ = transport.open_verified_tool(
        transport.LOADER, _hashlib.sha256(transport.LOADER.read_bytes()).hexdigest())
    return fd


def test_a_loader_that_fails_is_not_treated_as_an_empty_closure(tmp_path, monkeypatch):
    """A non-zero exit means the listing is incomplete, not that there is nothing to seal."""
    class _Failed:
        returncode = 1
        stdout = b''
        stderr = b'object file has no dynamic section'

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Failed())
    with pytest.raises(TransportHeld, match='the loader could not resolve'):
        transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage', loader_fd=-1)


def test_the_resolver_runs_with_an_explicit_environment(tmp_path, monkeypatch):
    """Inheriting the ambient environment let LD_PRELOAD steer the resolution being sealed."""
    seen = {}

    class _Result:
        returncode = 0
        stdout = b''
        stderr = b''

    def record(*args, **kwargs):
        seen.update(kwargs)
        return _Result()

    monkeypatch.setattr(transport.subprocess, 'run', record)
    monkeypatch.setenv('LD_PRELOAD', '/tmp/evil.so')
    transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage', loader_fd=-1)
    assert 'env' in seen, 'the resolver must not inherit the ambient environment'
    assert not any(key.startswith('LD_') for key in seen['env'])


def test_a_sibling_directory_is_not_inside_the_stage(tmp_path):
    """String prefixes say /stage-other is inside /stage; path containment does not."""
    stage = tmp_path/'stage'
    stage.mkdir()
    sibling = tmp_path/'stage-other'
    sibling.mkdir()
    assert transport._within(str(stage/'lib.so'), stage)
    assert not transport._within(str(sibling/'lib.so'), stage)


def test_an_object_whose_dependency_is_absent_is_recorded_not_skipped(tmp_path, monkeypatch):
    """The pinned core ships extensions this host cannot load, and both easy answers are wrong.

    Refusing the build makes the canonical closure unbuildable anywhere the optional library
    is not installed. Skipping it silently is the partial result the exit-status check exists
    to stop. It is recorded, so a reader sees which objects cannot load and why.
    """
    class _Missing:
        returncode = 127
        stdout = b''
        stderr = (b'/stage/python/lib/_tkinter.so: error while loading shared libraries: '
                  b'libtcl9.0.so: cannot open shared object file: No such file or directory\n')

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Missing())
    found: list = []
    resolved = transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage',
                                         loader_fd=-1, unresolvable=found)
    assert all('tkinter' not in str(item) for item in resolved)
    assert len(found) == 1
    assert found[0]['missing'] == ['libtcl9.0.so'] and found[0]['exit'] == 127
    assert found[0]['loadable_on_this_host'] is False
    assert found[0]['resolved_in_image'] == []


def test_another_loader_failure_still_refuses(tmp_path, monkeypatch):
    """Only an absent shared object is tolerated; anything else is still a refusal."""
    class _Broken:
        returncode = 1
        stdout = b''
        stderr = b'not a dynamic executable'

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Broken())
    with pytest.raises(TransportHeld, match='the loader could not resolve'):
        transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage',
                                  loader_fd=-1, unresolvable=[])


def test_a_missing_name_the_stage_carries_is_not_a_finding(tmp_path, monkeypatch):
    """manylinux bundles libraries that carry no RUNPATH of their own.

    They are only ever loaded as dependencies of the extension that does, so listing one in
    isolation fails on the host while the closure is complete. Seven of the eight objects
    this rule first reported were exactly that.
    """
    import shutil

    stage = tmp_path/'stage'
    (stage/'pkg.libs').mkdir(parents=True)
    shutil.copy('/lib/x86_64-linux-gnu/libz.so.1', stage/'pkg.libs'/'libsibling.so.1')

    class _Missing:
        returncode = 127
        stdout = b''
        stderr = (b'/stage/pkg.libs/a.so: error while loading shared libraries: '
                  b'libsibling.so.1: cannot open shared object file: No such file\n')

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Missing())
    found: list = []
    transport.library_closure([transport.MKSQUASHFS], stage, loader_fd=-1, unresolvable=found)
    assert found[0]['missing'] == []
    assert found[0]['resolved_in_image'] == ['libsibling.so.1']
    assert found[0]['loadable_on_this_host'] is True


# ---------------------------------------------------------------------------
# B3.5 The builder has an identity of its own, and no fall back to root
# ---------------------------------------------------------------------------

class _Entry:
    def __init__(self, uid, gid):
        self.pw_uid, self.pw_gid = uid, gid


def test_an_unreserved_builder_identity_refuses_the_build(monkeypatch):
    # The previous behaviour was to build as root and apply four rlimits, which looks bounded
    # and is not. An absent reservation is a precondition failure, not a reason to fall back.
    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(transport.pwd, 'getpwnam', missing)
    with pytest.raises(TransportHeld, match='does not build as root'):
        transport.builder_identity((980, 980))


def test_a_privileged_builder_identity_is_refused(monkeypatch):
    monkeypatch.setattr(transport.pwd, 'getpwnam', lambda name: _Entry(0, 0))
    with pytest.raises(TransportHeld, match='builder identity is privileged'):
        transport.builder_identity((980, 980))


def test_a_builder_identity_shared_with_the_worker_is_refused(monkeypatch):
    # Sharing the worker's uid would make RLIMIT_NPROC count the worker's processes against
    # the builder's bound, and would let either reach the other's files.
    monkeypatch.setattr(transport.pwd, 'getpwnam', lambda name: _Entry(980, 980))
    with pytest.raises(TransportHeld, match='shared with the worker'):
        transport.builder_identity((980, 980))


def test_the_reserved_builder_identity_is_accepted(monkeypatch):
    monkeypatch.setattr(transport.pwd, 'getpwnam', lambda name: _Entry(979, 979))
    assert transport.builder_identity((980, 980)) == (979, 979)


def test_the_builder_bound_includes_the_process_limit():
    # RLIMIT_NPROC is the one the earlier version left out, on the correct observation that it
    # bounds nothing for a privileged process. It is in the set now because the credentials
    # are gone before it is applied.
    applied = []

    class _Recorder:
        RLIMIT_NPROC = transport.resource.RLIMIT_NPROC
        RLIMIT_CPU = transport.resource.RLIMIT_CPU
        RLIMIT_AS = transport.resource.RLIMIT_AS
        RLIMIT_FSIZE = transport.resource.RLIMIT_FSIZE
        RLIMIT_NOFILE = transport.resource.RLIMIT_NOFILE

        @staticmethod
        def setrlimit(which, pair):
            applied.append((which, pair))

    order = []
    calls = dict(setgroups=lambda groups: order.append('setgroups'),
                 setresgid=lambda *a: order.append('setresgid'),
                 setresuid=lambda *a: order.append('setresuid'),
                 getresuid=lambda: (979, 979, 979))
    saved = {name: getattr(transport.os, name) for name in calls}
    saved_resource = transport.resource
    try:
        for name, value in calls.items():
            setattr(transport.os, name, value)
        transport.resource = _Recorder
        transport._builder_limits(979, 979)()
    finally:
        for name, value in saved.items():
            setattr(transport.os, name, value)
        transport.resource = saved_resource

    assert order == ['setgroups', 'setresgid', 'setresuid']
    assert applied[0] == (transport.resource.RLIMIT_NPROC,
                          (transport.BUILDER_PROCESSES, transport.BUILDER_PROCESSES))
    assert transport.BUILDER_PROCESSES == 32


def test_a_stage_behind_an_untraversable_ancestor_is_refused(tmp_path):
    closed = tmp_path/'closed'
    closed.mkdir(mode=0o700)
    stage = closed/'stage'
    stage.mkdir(mode=0o555)
    os.chmod(closed, 0o700)
    with pytest.raises(TransportHeld, match='not traversable by the builder identity'):
        transport._stage_is_reachable(stage, 979, 979)


def test_a_stage_whose_whole_chain_the_identity_can_traverse_is_accepted(tmp_path):
    # Checked against the running uid rather than the builder's, because `tmp_path` lives under
    # a private home: the owner reaches it through the owner-execute bit on each ancestor. An
    # earlier version of this asserted the builder could reach it, which was a claim about this
    # host's home permissions rather than about the function, and it failed for that reason.
    stage = tmp_path/'stage'
    stage.mkdir(mode=0o555)
    transport._stage_is_reachable(stage, os.getuid(), os.getgid())


def test_a_stage_under_a_private_home_is_refused_for_another_identity(tmp_path):
    # The same path, the same modes, a different identity. This is the case that matters: a
    # stage staged into someone's home is genuinely unreachable by the builder, and the build
    # would otherwise fail deep inside mksquashfs as a missing source.
    stage = tmp_path/'stage'
    stage.mkdir(mode=0o555)
    with pytest.raises(TransportHeld, match='not traversable by the builder identity'):
        transport._stage_is_reachable(stage, 979, 979)


# ---------------------------------------------------------------------------
# B3.1 A prepared session cannot outlive its owner
# ---------------------------------------------------------------------------
#
# The contract is explicit that v2 implements no handoff, so a claim is never continued. An
# earlier version of these tests asserted the opposite -- that a claim from this boot admitted
# probe and run -- which held the handoff in place rather than catching it.


def _claim(tmp_path, **fields):
    root = tmp_path/'task'
    root.mkdir(parents=True, exist_ok=True)
    (root/'quarantine.json').write_bytes(
        transport.canonical_json_bytes(dict(task='job', state='PREPARED', **fields)) + b'\n')
    return root


def test_an_unclaimed_task_root_admits_every_operation(tmp_path):
    # Nothing to resume and nothing to refuse: prepare and probe each build their own image.
    root = tmp_path/'task'
    root.mkdir()
    for operation in ('prepare', 'probe', 'run', 'inspect', 'cleanup'):
        assert transport.require_task_claim(root, operation) is None


def test_a_claimed_task_root_refuses_a_new_session(tmp_path):
    root = _claim(tmp_path, boot_id=transport._host_identity()[1])
    for operation in ('prepare', 'probe', 'run'):
        with pytest.raises(TransportHeld, match='cannot be resumed'):
            transport.require_task_claim(root, operation)


def test_a_claim_from_this_boot_is_refused_exactly_like_one_from_another(tmp_path):
    # The owner being on this boot is not what makes a session resumable. Nothing does.
    here = _claim(tmp_path/'a', boot_id=transport._host_identity()[1])
    there = _claim(tmp_path/'b', boot_id='0' * 36)
    for root in (here, there):
        with pytest.raises(TransportHeld, match='cannot be resumed'):
            transport.require_task_claim(root, 'probe')


def test_a_claim_can_always_be_inspected_and_released(tmp_path):
    root = _claim(tmp_path, boot_id='0' * 36)
    for operation in ('inspect', 'cleanup'):
        assert transport.require_task_claim(root, operation)['boot_id'] == '0' * 36


def test_an_unreadable_claim_still_refuses_a_new_session(tmp_path):
    root = tmp_path/'task'
    root.mkdir()
    (root/'quarantine.json').write_bytes(b'{ this is not json')
    for operation in ('prepare', 'probe', 'run'):
        with pytest.raises(TransportHeld, match='cannot be resumed'):
            transport.require_task_claim(root, operation)
    for operation in ('inspect', 'cleanup'):
        assert transport.require_task_claim(root, operation) is None


# ---------------------------------------------------------------------------
# B3.1 run starts the worker entrypoint that already exists
# ---------------------------------------------------------------------------


def _host_profile(**overrides):
    return dict({'worker_credentials_directory': '/run/creds',
                 'store_root': '/var/lib/artifacts'}, **overrides)


def _runtime_profile(**overrides):
    return dict({'application_root': '/opt/stage/application',
                 'application_python': '/opt/stage/application/.venv/bin/python3.11',
                 'package6_approval_sha256': 'c' * 64, 'revoked': False,
                 'expires_at': '2099-01-01T00:00:00Z'}, **overrides)


def _protected(**by_name):
    """Stand in for the protected reader, answering per path the way the owner store does."""
    def read(path):
        for suffix, document in by_name.items():
            if str(path).endswith(suffix):
                return document, 'digest'
        if path.name in {'authority-request.json', '00000000-0000-4000-8000-000000000004.json'}:
            return {'authorization':{'schema_version':'p3-local-run-authorization-v1',
                'identity':_identity(), 'nonce':'00000000-0000-4000-8000-000000000004'}}, 'digest'
        raise FileNotFoundError(path)
    return read


def _run_request(operation='p3-baselines-v1', **overrides):
    return dict(identity=_identity(), operation='run', release_root='release',
                python_root='python',
                official=dict(operation=operation,
                              staging_authority_path='/run/authority.json',
                              staging_activation_path='/run/activation.json'),
                **overrides)


def _identity(**overrides):
    return dict(issuer_kind='LOCAL', issuer_identity='operator-a',
                run_id='00000000-0000-4000-8000-000000000002', attempt=1,
                host_id='a' * 64, boot_id='b' * 8 + '-0000-4000-8000-000000000003', **overrides)


def test_the_run_directory_is_derived_from_the_protected_identity(monkeypatch):
    # B3.1: root paths come from the protected run identity, never from a caller. The
    # derivation is the existing owner's, so controller and worker cannot disagree.
    directory = transport._official_run_directory({'identity': _identity()})
    assert directory == Path('/run/trading-agent-p3/local/operator-a'
                             '/00000000-0000-4000-8000-000000000002-1')


def test_a_github_identity_is_not_admitted_to_the_local_run(monkeypatch):
    with pytest.raises(TransportHeld, match='LOCAL lane operation'):
        transport._official_run_directory({'identity': dict(_identity(), issuer_kind='GITHUB')})


def test_run_refuses_without_the_protected_host_profile(tmp_path, monkeypatch):
    # The session the profile describes is installed by the owner protocol. Absent, the run
    # stops here rather than launching a worker with nothing to authenticate against.
    def missing(path):
        raise FileNotFoundError(path)
    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
                        missing)
    with pytest.raises(TransportHeld, match='requires the protected host profile'):
        transport._worker_environment(_run_request(), tmp_path, 'release', tmp_path/'worker')


def test_a_profile_without_a_credential_directory_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
                        _protected(**{'profile.json': _host_profile(
                                          worker_credentials_directory='relative/path'),
                                      'runtime.json': _runtime_profile()}))
    with pytest.raises(TransportHeld, match='absolute worker_credentials_directory'):
        transport._worker_environment(_run_request(), tmp_path, 'release', tmp_path/'worker')


# Every name `services/job_worker/p3_official.py::run_official_once` reads before it can
# reach its own authority checks. Asserting the set the controller happens to emit proves
# nothing; this is the consumer's list, and the controller has to satisfy it.
WORKER_REQUIRED = (
    'P3_OPERATION', 'P3_ISSUER_KIND', 'CREDENTIALS_DIRECTORY',
    'P3_AUTHORITY_REQUEST_FILE', 'P3_PREFLIGHT_DIRECTORY', 'P3_ARTIFACT_ROOT',
    'P3_MANIFEST_FILE', 'P3_REVIEW_FILE',
    'TRADING_PACKAGE6_STAGING_SCOPE', 'TRADING_PACKAGE6_STAGING_AUTHORITY_PATH',
    'TRADING_PACKAGE6_STAGING_ACTIVATION_PATH', 'TRADING_PACKAGE6_APPROVAL_SHA256')


def test_the_worker_environment_satisfies_every_name_the_consumer_reads(tmp_path, monkeypatch):
    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
                        _protected(**{'profile.json': _host_profile(),
                                      'runtime.json': _runtime_profile()}))
    environment = transport._worker_environment(_run_request(), tmp_path/'mnt', 'release',
                                                tmp_path/'worker')
    missing = [name for name in WORKER_REQUIRED if name not in environment]
    assert not missing, f'the worker reads these and the controller does not supply them: {missing}'
    assert environment['P3_ISSUER_KIND'] == 'LOCAL'
    assert environment['P3_AUTHORITY_REQUEST_FILE'] == (
        '/etc/trading-agent/local-qualification/operator-a/operations/'
        '00000000-0000-4000-8000-000000000004.json')
    # Nothing ambient: no loader injection and no inherited search path.
    assert not any(name.startswith('LD_') for name in environment)


def test_the_transport_environment_selects_the_official_worker_entrypoint(tmp_path, monkeypatch):
    from services.job_worker import main, p3_official

    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
                        _protected(**{'profile.json': _host_profile(),
                                      'runtime.json': _runtime_profile()}))
    environment = transport._worker_environment(_run_request(), tmp_path/'mnt', 'release',
                                                tmp_path/'worker')
    monkeypatch.setattr(main.os, 'environ', environment)
    monkeypatch.setattr(main, 'attest_worker_runtime_authority',
                        lambda: pytest.fail('official transport entered the paper worker'))
    monkeypatch.setattr(p3_official, 'run_official_once', lambda values, *, build_worker: 73)
    assert main.main() == 73


def test_a_revoked_runtime_profile_admits_no_run(tmp_path, monkeypatch):
    # Revocation is the owner withdrawing the stage. It is checked where the profile is read,
    # not left for the worker to discover after an image was built for it.
    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
                        _protected(**{'profile.json': _host_profile(),
                                      'runtime.json': _runtime_profile(revoked=True)}))
    with pytest.raises(TransportHeld, match='runtime profile is revoked'):
        transport._worker_environment(_run_request(), tmp_path/'mnt', 'release',
                                      tmp_path/'worker')


def test_an_operation_the_worker_does_not_implement_is_refused(tmp_path, monkeypatch):
    # `run_official_once` rejects anything outside BASELINES/REGISTER_FAMILY/OOS/HOLDOUT. The
    # controller must not build a 170 MB image and mount it to discover that.
    monkeypatch.setattr('packages.runtime_release.config.read_protected_canonical_json_current',
                        _protected(**{'profile.json': _host_profile(),
                                      'runtime.json': _runtime_profile()}))
    request = _run_request(operation='p3-phase-exit-v1')
    with pytest.raises(TransportHeld, match='not an admitted official operation'):
        transport._worker_environment(request, tmp_path/'mnt', 'release', tmp_path/'worker')


def test_the_worker_launch_execs_the_existing_entrypoint(tmp_path, monkeypatch):
    # "no second launcher or execution engine is needed": the launch must exec the module that
    # already exists, after reporting, and must not return.
    calls = {}
    monkeypatch.setattr(transport, '_exec_reporting_failure',
                        lambda fd, argv, env: calls.update(fd=fd, argv=argv, env=env))
    written = []
    monkeypatch.setattr(transport.os, 'write', lambda fd, data: written.append(data))
    monkeypatch.setattr(transport.os, 'close', lambda fd: None)
    monkeypatch.setattr(transport.os, 'listdir', lambda path: ['0', '1', '2'])
    argv = ['/opt/stage/application/.venv/bin/python3.11', '-I', '-B',
            '-m', 'services.job_worker']
    with pytest.raises(TransportHeld, match='did not replace the process'):
        transport._worker_launch(dict(mount=tmp_path, descriptor={}, write=7, exec_channel=9,
                                      challenge={'x': 'y'}, argv=argv,
                                      environment={'P3_ISSUER_KIND': 'LOCAL'}))
    assert calls['argv'][-2:] == ['-m', 'services.job_worker']
    # The exec goes through the channel that reports its own failure, not a bare execve.
    assert calls['fd'] == 9
    # Reported before the exec, because exec leaves no afterwards in which to answer.
    assert written and b'WORKER_ENTRYPOINT' in written[0]


# ---------------------------------------------------------------------------
# B3.2 The library snapshot comes from the seal, not from a second read
# ---------------------------------------------------------------------------

def test_a_library_changed_after_sealing_does_not_reach_the_snapshot(tmp_path, monkeypatch):
    """The race the previous implementation detected but did not exclude.

    It sealed the library, closed the seal, read the host path a second time and compared the
    two digests. A writer between those reads was caught, so nothing wrong was ever admitted --
    but the bytes that landed in the snapshot came from the second read, and the sealed copy
    that could not be changed was thrown away. Here the host file is rewritten in exactly that
    window. The snapshot has to carry the sealed bytes, and the run has to succeed, because
    nothing raced anything: there is no second read.
    """
    original, substituted = b'\x7fELF' + b'original' * 64, b'\x7fELF' + b'SUBSTITUTED' * 64
    library = tmp_path/'libexample.so.1'
    library.write_bytes(original)
    snapshots = tmp_path/'libraries'
    snapshots.mkdir()

    real_seal = transport.seal_library

    def seal_then_overwrite(path, expected_sha256=None):
        fd, record = real_seal(path, expected_sha256)
        # The window. Same path, same name, different bytes, after the seal was taken.
        Path(path).write_bytes(substituted)
        return fd, record

    monkeypatch.setattr(transport, 'seal_library', seal_then_overwrite)
    monkeypatch.setattr(transport.os, 'chown', lambda *a, **k: None)
    libraries = transport._seal_external_libraries([library], snapshots)

    assert len(libraries) == 1
    snapshot = Path(libraries[0]['source'])
    assert snapshot.read_bytes() == original
    assert snapshot.read_bytes() != substituted
    assert library.read_bytes() == substituted, 'the host file really was changed'
    assert libraries[0]['sha256'] == hashlib.sha256(original).hexdigest()


def test_the_snapshot_digest_is_the_sealed_digest_not_the_host_path(tmp_path, monkeypatch):
    # The record a consumer binds to must describe the sealed bytes. If it described the host
    # path, a substitution would travel under an accepted digest.
    payload = b'\x7fELF' + b'pinned' * 100
    library = tmp_path/'libpinned.so.2'
    library.write_bytes(payload)
    snapshots = tmp_path/'libraries'
    snapshots.mkdir()
    monkeypatch.setattr(transport.os, 'chown', lambda *a, **k: None)
    libraries = transport._seal_external_libraries([library], snapshots)
    record = libraries[0]
    assert record['sha256'] == hashlib.sha256(payload).hexdigest()
    assert record['size_bytes'] == len(payload)
    snapshot = Path(record['source'])
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == record['sha256']
    # Provenance, not authority: the snapshot is a task-owned file, not the host library.
    assert snapshot != library and snapshots in snapshot.parents


# ---------------------------------------------------------------------------
# The worker's outcome has to reach the transport verdict
# ---------------------------------------------------------------------------
#
# `run_session` recorded `worker_status` and then never looked at it, so a worker that exited
# non-zero with a valid challenge produced `status: OK`. The status is also the raw wait
# status, not an exit code: exit 1 is 256 and a signal hides in the low bits.


def _reaped(status, escalation=(), reaped=True):
    return dict(status=status, escalation=list(escalation), reaped=reaped)


def test_a_worker_that_exits_non_zero_is_not_a_successful_transport(tmp_path):
    observed = dict(challenge='c', worker_stderr='startup failed before job claim\n')
    with pytest.raises(TransportHeld, match='worker exited 1'):
        transport._worker_verdict(tmp_path, observed, _reaped(1 << 8), False, 'c')
    diagnostic = tmp_path/'worker-stderr.log'
    assert diagnostic.read_bytes() == b'startup failed before job claim\n'
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o400


def test_a_worker_killed_by_a_signal_is_not_a_successful_transport(tmp_path):
    # SIGKILL leaves 9 in the low bits; waitstatus_to_exitcode reports it as -9.
    with pytest.raises(TransportHeld, match='worker was killed by signal 9'):
        transport._worker_verdict(tmp_path, dict(challenge='c'), _reaped(9), False, 'c')


def test_a_worker_that_needed_escalation_is_not_a_successful_transport(tmp_path):
    with pytest.raises(TransportHeld, match='escalation'):
        transport._worker_verdict(tmp_path, dict(challenge='c'), _reaped(0, escalation=['TERM']), False, 'c')


def test_an_unreaped_worker_is_not_a_successful_transport(tmp_path):
    with pytest.raises(TransportHeld, match='did not reap'):
        transport._worker_verdict(tmp_path, dict(challenge='c'), _reaped(None, reaped=False), False, 'c')


def test_truncated_worker_output_is_not_a_successful_transport(tmp_path):
    with pytest.raises(TransportHeld, match='exceeded its bound'):
        transport._worker_verdict(tmp_path, dict(challenge='c'), _reaped(0), True, 'c')


def test_a_worker_that_exited_cleanly_and_answered_the_challenge_is_accepted(tmp_path):
    assert transport._worker_verdict(tmp_path, dict(challenge='c'), _reaped(0), False, 'c') == 0


def test_a_clean_exit_with_the_wrong_challenge_is_still_refused(tmp_path):
    with pytest.raises(TransportHeld, match='challenge did not match'):
        transport._worker_verdict(tmp_path, dict(challenge='other'), _reaped(0), False, 'c')


def test_an_exec_failure_is_reported_before_the_exit_status_is_blamed(tmp_path):
    # The child reports an exec failure through its own channel. Blaming the exit code would
    # hide the cause behind a number.
    observed = dict(challenge='c', error='FileNotFoundError: interpreter')
    with pytest.raises(TransportHeld, match='FileNotFoundError'):
        transport._worker_verdict(tmp_path, observed, _reaped(1 << 8), False, 'c')


# ---------------------------------------------------------------------------
# B3.5 Builder isolation is per UID, and one at a time
# ---------------------------------------------------------------------------


def test_a_builder_sharing_the_worker_uid_is_refused_whatever_the_gid(monkeypatch):
    # RLIMIT_NPROC counts against the real uid, so a shared uid shares the budget however the
    # groups differ. Comparing the whole tuple let that through.
    monkeypatch.setattr(transport.pwd, 'getpwnam', lambda name: _Entry(980, 12345))
    with pytest.raises(TransportHeld, match='shared with the worker'):
        transport.builder_identity((980, 980))


def test_two_tasks_cannot_build_under_the_same_builder_uid_at_once(tmp_path, monkeypatch):
    # One reserved identity, one RLIMIT_NPROC budget. A second concurrent build would spend
    # the first one's processes.
    monkeypatch.setattr(transport, 'BUILD_PARENT', tmp_path/'build')
    with transport.builder_lock(979):
        with pytest.raises(TransportHeld, match='another task is building'):
            with transport.builder_lock(979):
                pass


def test_the_builder_lock_is_released_for_the_next_task(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, 'BUILD_PARENT', tmp_path/'build')
    with transport.builder_lock(979):
        pass
    with transport.builder_lock(979):
        pass


# ---------------------------------------------------------------------------
# B3.4/B4 Streams, exec failure and the deadline the authority actually grants
# ---------------------------------------------------------------------------


def test_several_streams_are_polled_together_rather_than_in_turn(tmp_path):
    # Reading the report to EOF before touching stderr deadlocks as soon as the worker fills
    # the stderr pipe: it blocks writing, so it never closes the report.
    reads, writes = zip(*(os.pipe() for _ in range(3)))
    for index, write in enumerate(writes):
        os.write(write, f'stream-{index}'.encode())
        os.close(write)
    streams = transport._read_streams(dict(report=reads[0], stdout=reads[1], stderr=reads[2]),
                                      transport.time.monotonic() + 5)
    assert streams['report'][0] == b'stream-0'
    assert streams['stdout'][0] == b'stream-1'
    assert streams['stderr'][0] == b'stream-2'
    assert not any(truncated for _, truncated in streams.values())


def test_a_stream_over_the_contract_bound_is_truncated_and_flagged(tmp_path):
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        # The child must never return into pytest: an exception here would run the session's
        # atexit cleanup a second time and corrupt the trusted temp directory.
        status = 0
        try:
            os.close(read)
            os.write(write, b'x' * (transport.MAX_WORKER_OUTPUT_BYTES + 4096))
            os.close(write)
        except BaseException:
            status = 1
        os._exit(status)
    os.close(write)
    streams = transport._read_streams(dict(stdout=read), transport.time.monotonic() + 10)
    os.waitpid(pid, 0)
    payload, truncated = streams['stdout']
    assert truncated is True
    assert len(payload) == transport.MAX_WORKER_OUTPUT_BYTES


def test_a_failed_exec_reaches_the_controller_after_the_report_is_closed(tmp_path):
    # The report pipe is closed before exec, so an exec failure has no way home through it.
    # A separate close-on-exec channel is silent when exec succeeds and carries the error when
    # it does not.
    read, write = os.pipe()
    os.set_inheritable(write, True)
    pid = os.fork()
    if pid == 0:
        status = 0
        try:
            os.close(read)
            transport._exec_reporting_failure(write, ['/nonexistent/interpreter', '-c', ''], {})
        except BaseException:
            status = 1
        os._exit(status)
    os.close(write)
    payload, _ = transport._read_streams(dict(exec=read),
                                         transport.time.monotonic() + 5)['exec']
    os.waitpid(pid, 0)
    assert b'FileNotFoundError' in payload or b'No such file' in payload


def test_the_run_deadline_is_bounded_by_the_authority_that_grants_it(tmp_path):
    from datetime import UTC, datetime, timedelta
    soon = (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
    # The controller must not hand itself 37 fresh minutes when the authority expires in five.
    granted = transport._official_deadline(transport.time.monotonic() + 37 * 60, soon)
    assert granted < transport.time.monotonic() + 37 * 60
    assert granted > transport.time.monotonic() + 4 * 60


def test_an_expired_authority_grants_no_run_deadline(tmp_path):
    from datetime import UTC, datetime, timedelta
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace('+00:00', 'Z')
    with pytest.raises(TransportHeld, match='authority has expired'):
        transport._official_deadline(transport.time.monotonic() + 37 * 60, past)


def test_the_invocation_bound_still_applies_when_the_authority_outlives_it(tmp_path):
    from datetime import UTC, datetime, timedelta
    far = (datetime.now(UTC) + timedelta(hours=9)).isoformat().replace('+00:00', 'Z')
    bound = transport.time.monotonic() + 37 * 60
    assert transport._official_deadline(bound, far) == bound


# ---------------------------------------------------------------------------
# B3.4 The fence has to cover the run, not three moments in it
# ---------------------------------------------------------------------------


def test_the_fence_runs_while_the_worker_does(tmp_path):
    # Three point-in-time fences leave the whole execution unfenced between them.
    read, write = os.pipe()
    calls = []
    pid = os.fork()
    if pid == 0:
        status = 0
        try:
            os.close(read)
            transport.time.sleep(0.6)
            os.write(write, b'done')
            os.close(write)
        except BaseException:
            status = 1
        os._exit(status)
    os.close(write)
    transport._read_streams(dict(report=read), transport.time.monotonic() + 10,
                            fence=lambda: calls.append(transport.time.monotonic()),
                            fence_interval=0.1)
    os.close(read)
    os.waitpid(pid, 0)
    assert len(calls) >= 3, f'the fence ran {len(calls)} times during a 0.6s run'


def test_a_fence_that_breaks_mid_run_stops_the_read(tmp_path):
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        status = 0
        try:
            os.close(read)
            transport.time.sleep(2)
            os.close(write)
        except BaseException:
            status = 1
        os._exit(status)
    os.close(write)

    def broken():
        raise TransportHeld('backing image changed under the mount')

    with pytest.raises(TransportHeld, match='backing image changed'):
        transport._read_streams(dict(report=read), transport.time.monotonic() + 10,
                                fence=broken, fence_interval=0.05)
    os.close(read)
    os.kill(pid, 9)
    os.waitpid(pid, 0)


def test_without_a_fence_the_read_behaves_as_before(tmp_path):
    read, write = os.pipe()
    os.write(write, b'payload')
    os.close(write)
    streams = transport._read_streams(dict(report=read), transport.time.monotonic() + 5)
    os.close(read)
    assert streams['report'] == (b'payload', False)


# ---------------------------------------------------------------------------
# B4 What the invocation concluded is durable before the lock is released
# ---------------------------------------------------------------------------


def test_a_terminal_record_is_written_for_an_outcome(tmp_path):
    root = tmp_path/'task'
    root.mkdir()
    transport._durable_terminal(root, dict(job_id='job', operation='probe'), 'OK', None)
    record = json.loads((root/'terminal.json').read_bytes())
    assert record['outcome'] == 'OK'
    assert record['operation'] == 'probe'
    assert record['schema_version'] == 'p3-local-transport-terminal-v1'
    # Root-owned identity so a later reader can tell which invocation concluded it.
    assert record['owner_pid'] == os.getpid()
    assert (root/'terminal.json').stat().st_mode & 0o777 == 0o400


def test_a_refusal_records_its_reason_rather_than_vanishing(tmp_path):
    root = tmp_path/'task'
    root.mkdir()
    transport._durable_terminal(root, dict(job_id='job', operation='run'), 'HELD',
                                'the admitting authority has expired')
    record = json.loads((root/'terminal.json').read_bytes())
    assert record['outcome'] == 'HELD'
    assert 'authority has expired' in record['detail']


def test_a_later_invocation_does_not_silently_overwrite_a_terminal_record(tmp_path):
    root = tmp_path/'task'
    root.mkdir()
    transport._durable_terminal(root, dict(job_id='job', operation='probe'), 'OK', None)
    first = (root/'terminal.json').read_bytes()
    transport._durable_terminal(root, dict(job_id='job', operation='probe'), 'HELD', 'later')
    assert (root/'terminal.json').read_bytes() != first
    kept = sorted(p.name for p in root.glob('terminal.json.*'))
    assert kept, 'the superseded record has to remain; a failure is not erased by a retry'
def test_durable_publication_applies_exact_mode_under_private_umask(tmp_path):
    import os
    import stat
    from scripts.p3_local_transport import _durable_write
    previous = os.umask(0o077)
    try:
        target = tmp_path/'launch.json'
        _durable_write(target, b'{}', mode=0o444)
        assert stat.S_IMODE(target.stat().st_mode) == 0o444
    finally:
        os.umask(previous)
