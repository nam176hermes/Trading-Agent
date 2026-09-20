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


def test_the_toolchain_manifest_covers_the_whole_dependency_closure(tmp_path):
    """Hashing only the tool's own record left every library it loads unpinned."""
    import hashlib as _hashlib

    loader_digest = _hashlib.sha256(transport.LOADER.read_bytes()).hexdigest()
    tool_digest = _hashlib.sha256(transport.MKSQUASHFS.read_bytes()).hexdigest()
    loader_fd, _ = transport.open_verified_tool(transport.LOADER, loader_digest)
    tool_fd, record = transport.open_verified_tool(transport.MKSQUASHFS, tool_digest)
    try:
        manifest = transport.toolchain_manifest(transport.MKSQUASHFS, record, loader_fd, tmp_path)
    finally:
        os.close(loader_fd)
        os.close(tool_fd)
    roles = [member['role'] for member in manifest['members']]
    assert roles.count('tool') == 1
    assert roles.count('dependency') >= 1, 'the dependency closure must not be empty'
    assert all(len(member['sha256']) == 64 for member in manifest['members'])
    declared = transport._elf_needed(transport.MKSQUASHFS)
    named = {Path(member['path']).name for member in manifest['members']
             if member['role'] == 'dependency'}
    assert declared.issubset(named), f'unpinned declared dependencies: {declared - named}'
    assert not any(member.get('path', '').startswith('/proc/self/fd/')
                   for member in manifest['members'])


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
        stdout = (b'\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x1)\n'
                  b'\tlibnothing.so.9 => ' + str(planted).encode() + b' (0x2)\n')

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Result())
    with pytest.raises(TransportHeld, match='nothing in the closure declares'):
        transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage', loader_fd=-1)


def test_a_resolved_dependency_that_cannot_be_read_is_refused(tmp_path, monkeypatch):
    class _Missing:
        stdout = b'\tlibghost.so.1 => /nonexistent/libghost.so.1 (0x1)\n'

    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: _Missing())
    with pytest.raises(TransportHeld, match='could not be read'):
        transport.library_closure([transport.MKSQUASHFS], tmp_path/'stage', loader_fd=-1)


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
