"""Root controller for the P3 LOCAL v2 SquashFS runtime transport.

Implements the reviewed R02 contracts. The transport exists because the v1 sealed-file form
spends five bubblewrap parser tokens on every closure file, so a complete closure cannot be
launched under the pinned parser's 9,000-token capacity. v2 delivers the identical verified
bytes through one read-only image and two aggregate binds.

Trust boundary, stated rather than implied: root, the kernel, the image builder toolchain
and the loop and mount managers are trusted for the whole run. Root-owned 0400 is a
permission boundary, not immutable bytes. Nothing here defends against a malicious root.

Operations are a closed set. No path, tool or destination is taken from the command line
except the protected request file itself, and every other path is derived from the request.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import resource
import select
import signal
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.alpha_lifecycle.sandbox_policy import (            # noqa: E402
    BWRAP_PARSER_TOKEN_CAPACITY, IMAGE_SCHEMA_VERSION, MAX_CLOSURE_BYTES, MAX_CLOSURE_FILES,
    MAX_EXTERNAL_LIBRARIES, MAX_EXTERNAL_LIBRARY_BYTES, MAX_EXTERNAL_LIBRARY_TOTAL_BYTES,
    MAX_IMAGE_BYTES,
)
from packages.engine_contracts.serialization import canonical_json_bytes   # noqa: E402

OPERATIONS = ('prepare', 'probe', 'run', 'inspect', 'cleanup')
TASK_PARENT = Path('/run/trading-agent-local/p3-transport')
REQUEST_PARENT = Path('/etc/trading-agent/local-p3-transport')

MKSQUASHFS = Path('/usr/bin/mksquashfs')
BWRAP = Path('/usr/bin/bwrap')
# The SONAME the loader is asked for is a symlink, and the verified open refuses to
# follow one. The resolved file is what carries the bytes; the pinned digest is what
# decides whether they are the right ones, so resolving here loosens nothing.
LOADER_SONAME = Path('/lib64/ld-linux-x86-64.so.2')
LOADER = Path(os.path.realpath(LOADER_SONAME)) if LOADER_SONAME.exists() else LOADER_SONAME
MKSQUASHFS_OPTIONS = ('-noappend', '-no-recovery', '-no-xattrs', '-no-hardlinks', '-no-exports',
    '-reproducible', '-mkfs-time', '0', '-all-time', '0', '-root-mode', '0555',
    '-comp', 'gzip', '-b', '131072', '-processors', '1', '-mem', '1G',
    '-exit-on-error', '-no-progress')

PREPARE_WALL_SECONDS = 300
BUILDER_CPU_SECONDS = 120
BUILDER_ADDRESS_SPACE = 2 * 1024**3
BUILDER_FILE_SIZE = MAX_IMAGE_BYTES
BUILDER_OPEN_FILES = 256
TERMINATE_GRACE_SECONDS = 2
REAP_GRACE_SECONDS = 3
# The challenge reads a prefix of each selected entry rather than the whole file, so a
# large entry cannot turn a liveness check into an unbounded read.
CHALLENGE_ENTRY_BYTES = 8 * 1024

CLONE_NEWNS = 0x00020000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_REC = 1, 2, 4, 0x4000
MS_PRIVATE = 1 << 18
MNT_DETACH = 2
LOOP_CTL_GET_FREE = 0x4C82
LOOP_CONFIGURE = 0x4C0A
LOOP_CLR_FD = 0x4C01
LOOP_GET_STATUS64 = 0x4C05
LO_FLAGS_READ_ONLY = 1
LO_FLAGS_AUTOCLEAR = 4
_LOOP_INFO64 = 'QQQQQIIII64s64s32sQQ'
PR_SET_PDEATHSIG, PR_GET_PDEATHSIG = 1, 2
F_SEAL_SEAL, F_SEAL_SHRINK, F_SEAL_GROW, F_SEAL_WRITE = 0x0001, 0x0002, 0x0004, 0x0008
F_ADD_SEALS, F_GET_SEALS = 1033, 1034
_SEALS = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE

_libc = ctypes.CDLL('libc.so.6', use_errno=True)


class TransportHeld(Exception):
    """Fail-closed refusal. The controller never degrades to a weaker transport."""


def _checked(result: int, call: str) -> int:
    if result != 0:
        code = ctypes.get_errno()
        raise TransportHeld(f'{call} failed: {os.strerror(code)}')
    return result


def _fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | (os.O_DIRECTORY if path.is_dir() else 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_write(path: Path, payload: bytes, mode: int = 0o400) -> str:
    """Write, fsync, rename, then fsync the parent, in that order.

    A record that is not durable before the artifact it describes is published cannot be
    used for recovery: a crash in between leaves an artifact nothing can identify.
    """
    staging = path.with_name(path.name + '.staging')
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    os.replace(staging, path)
    _fsync_path(path.parent)
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# B3.1 Task identity, derived paths and the lifetime lock
# ---------------------------------------------------------------------------

def task_identity(request: dict) -> tuple[str, dict]:
    """Domain-separated canonical identity, hashed to derive the task directory.

    Includes job, source and issuer, not only run and attempt: one run can carry several
    jobs, and a directory keyed on run alone would collide between them.
    """
    identity = dict(
        schema_version='p3-local-transport-task-v1',
        issuer_kind=request['identity']['issuer_kind'],
        issuer_identity=request['identity']['issuer_identity'],
        run_id=request['identity']['run_id'],
        attempt=request['identity']['attempt'],
        host_id=request['identity']['host_id'],
        boot_id=request['identity']['boot_id'],
        job_id=request['job_id'],
        source=request['source'],
        transport_generation=request['transport_generation'])
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest(), identity


def _host_identity() -> tuple[str, str]:
    machine = hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()
    return machine, Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def read_request(name: str) -> dict:
    """The only caller-supplied value, and it names a protected file rather than a path.

    `name` is a bare filename inside the protected request directory, so no traversal,
    symlink or alternate root can reach a request the issuer did not place there.
    """
    if not name or '/' in name or name in ('.', '..') or not name.endswith('.json'):
        raise TransportHeld('transport request name is not a protected request filename')
    path = REQUEST_PARENT / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0:
            raise TransportHeld('transport request is not a root-owned regular file')
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise TransportHeld('transport request is group or world accessible')
        raw = os.read(fd, 1 << 20)
    finally:
        os.close(fd)
    request = json.loads(raw)
    if canonical_json_bytes(request) != raw.rstrip(b'\n'):
        raise TransportHeld('transport request is not canonical')
    if request.get('schema_version') != 'p3-local-transport-request-v1':
        raise TransportHeld('transport request schema is not accepted')
    if request.get('operation') not in OPERATIONS:
        raise TransportHeld('transport request operation is not in the accepted set')
    # Every executable the transport runs is pinned by the issuer, not by whatever the path
    # currently holds. A request that names no expected digest cannot be satisfied at all,
    # which is why these are required rather than defaulted.
    for field in ('mksquashfs_sha256', 'loader_sha256', 'bwrap_sha256'):
        value = request.get(field)
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
            raise TransportHeld(f'transport request does not pin {field}')
    host_id, boot_id = _host_identity()
    if request['identity']['host_id'] != host_id or request['identity']['boot_id'] != boot_id:
        raise TransportHeld('transport request was issued for a different host or boot')
    return request


def task_root(digest: str) -> Path:
    return TASK_PARENT / digest


class TaskLock:
    """Exclusive ownership for the whole image lifetime, not one operation.

    A prepared image outlives the call that built it, so a lock scoped to a single
    operation leaves a gap in which another controller could claim the same task.
    """

    def __init__(self, root: Path):
        self._path = root / 'task.lock'
        self._fd = -1

    def __enter__(self) -> 'TaskLock':
        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self._fd)
            raise TransportHeld('another controller owns this task') from error
        owner = dict(pid=os.getpid(), start_time=_process_start_time(os.getpid()),
                     boot_id=_host_identity()[1], acquired_at_ns=time.time_ns())
        os.ftruncate(self._fd, 0)
        os.write(self._fd, canonical_json_bytes(owner) + b'\n')
        os.fsync(self._fd)
        return self

    def __exit__(self, *exc) -> None:
        if self._fd >= 0:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = -1


def _process_start_time(pid: int) -> int:
    """Field 22 of /proc/<pid>/stat. A pid alone is not identity; it is reused."""
    raw = Path(f'/proc/{pid}/stat').read_bytes()
    return int(raw[raw.rindex(b')') + 2:].split()[19])


# ---------------------------------------------------------------------------
# B3.2 Verified tool execution, B3.3 sealed libraries
# ---------------------------------------------------------------------------

def open_verified_tool(path: Path, expected_sha256: str) -> tuple[int, dict]:
    """Open, verify and retain one descriptor; execution later uses that same object.

    Hashing a pathname and then executing that pathname leaves a window in which the name
    can be repointed. Executing /proc/self/fd/N binds the bytes that run to the bytes that
    were hashed. It does not make them immutable: writer exclusion is a separate contract.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != 0:
            raise TransportHeld(f'{path} is not a single-link root-owned regular file')
        if stat.S_IMODE(before.st_mode) & 0o022:
            raise TransportHeld(f'{path} is group or world writable')
        digest = hashlib.sha256()
        offset = 0
        while chunk := os.pread(fd, 1 << 20, offset):
            digest.update(chunk)
            offset += len(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != \
           (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise TransportHeld(f'{path} changed while it was being verified')
        if digest.hexdigest() != expected_sha256:
            raise TransportHeld(f'{path} does not match its pinned digest')
    except BaseException:
        os.close(fd)
        raise
    return fd, dict(path=str(path), dev=before.st_dev, ino=before.st_ino,
                    size=before.st_size, sha256=digest.hexdigest())


def seal_verified_tool(path: Path, expected_sha256: str) -> tuple[int, dict]:
    """Verify one pinned executable, then run it from a sealed copy, never from the file.

    Executing `/proc/self/fd/N` binds the bytes that run to the inode that was hashed. It does
    not bind them to the bytes that inode held at the moment of hashing: a writer with access
    to the file can still change them in place between the verification and the exec, and the
    verification would be of bytes that no longer run. Re-hashing just before the exec narrows
    that window; copying into a sealed memfd removes it, because the sealed image cannot be
    written by anyone afterwards. This is the same thing p3_startup already does for
    bubblewrap, applied to the tools the controller itself runs.
    """
    fd, record = open_verified_tool(path, expected_sha256)
    try:
        payload, offset = bytearray(), 0
        while chunk := os.pread(fd, 1 << 20, offset):
            payload += chunk
            offset += len(chunk)
    finally:
        os.close(fd)
    payload = bytes(payload)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise TransportHeld(f'{path} changed between verification and sealing')
    from services.job_worker.engine_spawn import _sealed_memfd
    sealed = _sealed_memfd(f'p3-tool-{path.name}', payload, mode=0o500)
    try:
        try:
            os.pwrite(sealed, b'\0', 0)
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.EBADF):
                raise
        else:
            raise TransportHeld(f'{path} sealed copy is still writable')
    except BaseException:
        os.close(sealed)
        raise
    return sealed, dict(record, sealed=True)


def seal_library(path: Path, expected_sha256: str | None = None) -> tuple[int, dict]:
    """Copy one allowlisted library into a sealed memfd and hand that to bwrap.

    `--ro-bind /proc/self/fd/N` is not a transport for this: bubblewrap 0.9.0 resolves bind
    sources through realpath, so a sealed or unlinked source cannot be bound at all. The
    accepted form is `--ro-bind-data FD TARGET`, which reads the content from the
    descriptor. The sealed snapshot digest is the byte authority; dev and ino are only
    provenance.
    """
    # A SONAME path is normally a symlink to the versioned file, and that is the path the
    # loader asks for, so it is resolved here rather than refused. The bind target keeps the
    # SONAME; binding the resolved name would publish libstdc++.so.6.0.x and leave the
    # requested libstdc++.so.6 missing.
    source = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode):
            raise TransportHeld(f'{path} is not a regular file')
        if info.st_size > MAX_EXTERNAL_LIBRARY_BYTES:
            raise TransportHeld(f'{path} exceeds the accepted external library size')
        payload = os.pread(source, info.st_size, 0)
    finally:
        os.close(source)
    # The accepted sealing owner, which already applies and verifies the required seals.
    # This build of CPython has no os.memfd_create, which is why that helper exists.
    from services.job_worker.engine_spawn import _sealed_memfd
    fd = _sealed_memfd(f'p3-library-{path.name}', payload, mode=0o555)
    try:
        try:
            os.pwrite(fd, b'\0', 0)
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.EBADF):
                raise
        else:
            raise TransportHeld(f'{path} snapshot is still writable after sealing')
        digest = hashlib.sha256(payload).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise TransportHeld(f'{path} snapshot does not match its pinned digest')
    except BaseException:
        os.close(fd)
        raise
    return fd, dict(target=str(path), mode=0o555, size_bytes=len(payload), sha256=digest,
                    source_dev=info.st_dev, source_ino=info.st_ino)


def _in_stage(soname: str, stage: Path) -> bool:
    """Is this library name a file the stage already carries?

    If it is, the image carries it too and the loader finds it there, whatever it could not
    find on the host while the object was being listed in isolation.
    """
    try:
        return any(candidate.is_file() for candidate in stage.rglob(soname))
    except OSError:
        return False


def _within(target: str, root: Path) -> bool:
    """Path containment, not a string prefix.

    `startswith(str(stage))` says /stage-other is inside /stage, so a sibling directory was
    treated as part of the image and the libraries under it were never sealed.
    """
    try:
        return Path(target).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _elf_needed(path: Path) -> set[str]:
    """The DT_NEEDED entries this ELF object declares, read from the file itself.

    Independent of any helper program, so the resolver's output can be checked against what
    the object actually asks for rather than taken on trust.
    """
    raw = path.read_bytes()
    if raw[:4] != b'\x7fELF' or raw[4] != 2 or raw[5] != 1:
        raise TransportHeld(f'{path} is not a little-endian 64-bit ELF object')
    e_phoff, = struct.unpack_from('<Q', raw, 0x20)
    e_phentsize, e_phnum = struct.unpack_from('<HH', raw, 0x36)
    dynamic = strtab = straddr = None
    segments = []
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        p_type, = struct.unpack_from('<I', raw, base)
        p_offset, p_vaddr = struct.unpack_from('<QQ', raw, base + 0x08)
        p_filesz, = struct.unpack_from('<Q', raw, base + 0x20)
        segments.append((p_vaddr, p_offset, p_filesz))
        if p_type == 2:                                   # PT_DYNAMIC
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        return set()

    def _at(address: int) -> int:
        for vaddr, offset, size in segments:
            if vaddr <= address < vaddr + size:
                return offset + (address - vaddr)
        raise TransportHeld(f'{path} has a dynamic address outside every segment')

    offset, size = dynamic
    needed_offsets = []
    for position in range(offset, offset + size, 16):
        tag, value = struct.unpack_from('<Qq', raw, position)
        if tag == 0:                                      # DT_NULL
            break
        if tag == 1:                                      # DT_NEEDED
            needed_offsets.append(value)
        elif tag == 5:                                    # DT_STRTAB
            straddr = value
    if straddr is None:
        return set()
    strtab = _at(straddr)
    names = set()
    for value in needed_offsets:
        end = raw.index(b'\0', strtab + value)
        names.add(raw[strtab + value:end].decode())
    return names


def library_closure(objects: list[Path], stage: Path, loader_fd: int,
                    unresolvable: list[dict] | None = None) -> list[Path]:
    """Exact host library file targets the staged ELF objects resolve to.

    The SONAME path the loader asks for is kept as the target. Binding a realpath publishes
    libstdc++.so.6.0.x and leaves the requested libstdc++.so.6 missing.

    Resolution runs through the verified loader descriptor rather than /usr/bin/ldd. ldd is a
    shell script: using it meant the dependency list of a verified closure was produced by an
    unverified program and an unverified shell. `ld.so --list` gives the same listing, and
    executing it as /proc/self/fd/N binds the bytes that produce the list to the bytes that
    were checked. Each resolved SONAME is then required to be one the object itself declares,
    so the resolver cannot introduce a library the closure never asked for.
    """
    needed: set[str] = set()
    for path in objects:
        # Three things are tracked, and they are not the same set.
        #
        # `paths` is every real file the loader pulled in, and it is what the declared union
        # is computed over. `names` is only the entries listed in SONAME form, which are the
        # ones the undeclared check can speak about. `external` is the host files that have
        # to be sealed.
        #
        # Two line shapes matter, measured on this host. `name => /path` is the familiar one.
        # A DT_NEEDED written as a path has no arrow at all: CPython's libpython3.so declares
        # `$ORIGIN/../lib/libpython3.11.so.1.0`, and the loader lists the resolved file on its
        # own. Parsing only the arrow form dropped that object, so the four libraries it
        # brings in -- libdl, librt, libm, libutil -- looked undeclared and a correct closure
        # was refused.
        paths: set[str] = set()
        names: dict[str, str] = {}
        # An explicit environment. Inheriting the ambient one let LD_PRELOAD, LD_LIBRARY_PATH
        # and the rest steer the very resolution whose output is then sealed and delivered.
        result = subprocess.run([f'/proc/self/fd/{loader_fd}', '--list', str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30, pass_fds=(loader_fd,),
                                env=dict(PATH='/usr/bin:/bin', LC_ALL='C'))
        # A non-zero exit means the listing is incomplete, and an incomplete listing that is
        # accepted becomes a closure missing libraries the object needs. Discarding stderr and
        # ignoring the status turned that into a silent partial result.
        if result.returncode != 0:
            stderr = result.stderr.decode()
            # The line reads "<object>: error while loading shared libraries: <soname>:
            # cannot open shared object file: No such file or directory", so the name is the
            # field before the message, not the second from the end -- which is the message.
            missing = []
            for line in stderr.splitlines():
                parts = [part.strip() for part in line.split(':')]
                for index, part in enumerate(parts):
                    if part == 'cannot open shared object file' and index:
                        missing.append(parts[index - 1])
            # An optional extension whose dependency is absent on this host cannot contribute
            # host libraries, because there are none to seal: the pinned core ships
            # _tkinter, which needs libtcl9.0.so, and this host has no tcl. Refusing the whole
            # build over it would make the canonical closure unbuildable anywhere tcl is not
            # installed. Skipping it silently is the partial result this check exists to stop,
            # so it is recorded instead, and every other failure still refuses.
            if unresolvable is not None and missing:
                # A name that is itself a file inside the stage travels in the image and
                # resolves there. The manylinux libraries under *.libs carry no RUNPATH of
                # their own: they are only ever loaded as dependencies of the extension that
                # does, so listing one in isolation fails on this host while the closure is
                # complete. Recording that as unresolvable would have been a false alarm on
                # seven of the eight objects this found.
                in_image = sorted({name for name in set(missing) if _in_stage(name, stage)})
                absent = sorted(set(missing) - set(in_image))
                unresolvable.append(dict(object=str(path.relative_to(stage))
                                         if _within(str(path), stage) else str(path),
                                         missing=absent, resolved_in_image=in_image,
                                         exit=result.returncode,
                                         loadable_on_this_host=not absent))
                continue
            raise TransportHeld(
                f'the loader could not resolve {path}: exit {result.returncode}; '
                f'{stderr[-300:]}')
        for line in result.stdout.decode().splitlines():
            entry = line.strip()
            if not entry:
                continue
            if '=>' in entry:
                soname, _, remainder = entry.partition('=>')
                name, target = soname.strip(), remainder.strip().split(' ')[0]
            else:
                # The loader itself and any path-form dependency. linux-vdso has no file.
                name, target = '', entry.split(' ')[0]
            # The loader reports itself under the path it was invoked as, which is this
            # process's own descriptor. It is named once, by its stable path, below.
            if not target.startswith('/') or target.startswith('/proc/self/fd/'):
                continue
            paths.add(target)
            if name:
                names[name] = target
        # Every SONAME the loader resolved has to be declared by the object itself or by
        # something else in the same closure, because a transitive dependency is declared by
        # the intermediate library rather than by the root object. Without this the
        # resolver's output is taken on trust, and it could introduce a library that nothing
        # in the closure ever asked for.
        declared = set(_elf_needed(path))
        for target in paths:
            try:
                declared |= _elf_needed(Path(os.path.realpath(target)))
            except OSError as error:
                raise TransportHeld(
                    f'resolved dependency {target} could not be read') from error
        undeclared = {name for name in names
                      if name not in declared and not name.startswith('ld-linux')}
        if undeclared:
            raise TransportHeld(
                f'{path} resolved libraries nothing in the closure declares: {sorted(undeclared)}')
        # Libraries bundled inside the stage already travel in the image; only the host
        # libraries the closure reaches outside it are external and need sealing.
        needed.update(target for target in paths if not _within(target, stage))
    loader = '/lib64/ld-linux-x86-64.so.2'
    if os.path.exists(loader):
        needed.add(loader)
    if len(needed) > MAX_EXTERNAL_LIBRARIES:
        raise TransportHeld('external library count exceeds the accepted bound')
    return [Path(p) for p in sorted(needed)]


def toolchain_manifest(tools: list[tuple[Path, dict]], loader_fd: int, stage: Path) -> dict:
    """Every executable the transport runs, and every object each of them loads, by digest.

    Hashing only one tool's own record left its dynamic dependencies unpinned: the bytes that
    actually execute during a build include every library the loader brings in, and none of
    them were named, let alone bound. Covering only mksquashfs was the same mistake one level
    up -- bubblewrap runs too, and its closure was not in the manifest either.
    """
    members, seen = [], set()
    for tool, record in tools:
        members.append(dict(record, role='tool'))
        for dependency in library_closure([tool], stage, loader_fd):
            if str(dependency) in seen:
                continue
            seen.add(str(dependency))
            info = dependency.lstat()
            if not stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                raise TransportHeld(f'toolchain dependency is not a file: {dependency}')
            resolved = Path(os.path.realpath(dependency))
            members.append(dict(role='dependency', path=str(dependency),
                                resolved=str(resolved), size=resolved.stat().st_size,
                                sha256=hashlib.sha256(resolved.read_bytes()).hexdigest()))
    members.sort(key=lambda member: (member['role'], member.get('path', '')))
    return dict(schema_version='p3-local-toolchain-manifest-v2', members=members)


# ---------------------------------------------------------------------------
# B3.5 Builder under enforced limits; the image and its descriptor
# ---------------------------------------------------------------------------

def normalise_stage_modes(stage: Path) -> dict:
    """Bring the stage to the accepted closure modes before the image is built.

    The launcher admits only 0400, 0500, 0444 and 0555, and mksquashfs copies whatever the
    stage carries, so ordinary 0644 and 0755 files have to be normalised here rather than
    after the image exists. Executability is preserved; nothing else about the bytes changes.
    """
    files = executables = directories = 0
    for current, names, entries in os.walk(stage, followlinks=False):
        for name in names:
            path = Path(current) / name
            # os.walk lists a symlink to a directory among the directory names without
            # descending into it, and chmod follows symlinks. Changing the mode by name here
            # would therefore reach outside the stage entirely. Directory entries are checked
            # for exactly what file entries are.
            if stat.S_ISLNK(path.lstat().st_mode):
                raise TransportHeld(f'stage contains a symlinked directory: {path.relative_to(stage)}')
            os.chmod(path, 0o555)
            directories += 1
        for name in entries:
            path = Path(current) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise TransportHeld(f'stage contains a symlink: {path.relative_to(stage)}')
            mode = 0o555 if info.st_mode & 0o111 else 0o444
            executables += mode == 0o555
            os.chmod(path, mode)
            files += 1
    os.chmod(stage, 0o555)
    return dict(files=files, executables=executables, directories=directories)


def _require_no_xattrs(path: Path, relative: str) -> None:
    """The image is built with -no-xattrs, so an attribute present here is silently dropped.

    An entry whose meaning depends on an extended attribute would be admitted from the stage
    and then served without it, so the difference is refused here rather than lost.
    """
    try:
        names = os.listxattr(path, follow_symlinks=False)
    except OSError:
        return
    if names:
        raise TransportHeld(f'closure entry carries extended attributes: {relative}')


def logical_inventory(stage: Path) -> dict:
    """Reject anything the accepted closure contract disallows, before any image exists.

    Directories are enumerated alongside files. A file-only inventory describes the bytes
    the image serves but not the tree they are served through, so a directory's mode, its
    type or an attribute on it was never part of what the descriptor bound.
    """
    entries: dict[str, dict] = {}
    tree: dict[str, dict] = {}
    total = 0
    for current, directories, files in os.walk(stage, followlinks=False):
        directories.sort()
        for name in directories:
            path = Path(current) / name
            info = path.lstat()
            relative = str(path.relative_to(stage))
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise TransportHeld(f'closure directory is not a plain directory: {relative}')
            _require_no_xattrs(path, relative)
            tree[relative] = dict(mode=stat.S_IMODE(info.st_mode))
        for name in sorted(files):
            path = Path(current) / name
            info = path.lstat()
            relative = str(path.relative_to(stage))
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise TransportHeld(f'closure entry is not a regular file: {relative}')
            if info.st_nlink != 1:
                raise TransportHeld(f'closure entry has unexpected links: {relative}')
            digest = hashlib.sha256()
            prefix = hashlib.sha256()
            seen = 0
            with open(path, 'rb', buffering=0) as handle:
                for block in iter(lambda: handle.read(1 << 20), b''):
                    digest.update(block)
                    if seen < CHALLENGE_ENTRY_BYTES:
                        prefix.update(block[:CHALLENGE_ENTRY_BYTES - seen])
                        seen += len(block)
            _require_no_xattrs(path, relative)
            # The prefix digest is what the mount challenge compares against. Without it a
            # challenge over a large entry has no admitted value to check, so both sides end
            # up reading whatever is mounted and agreeing with each other.
            entries[relative] = dict(size_bytes=info.st_size, mode=stat.S_IMODE(info.st_mode),
                                     sha256=digest.hexdigest(), prefix_sha256=prefix.hexdigest())
            total += info.st_size
    if not entries:
        raise TransportHeld('closure is empty')
    if len(entries) > MAX_CLOSURE_FILES:
        raise TransportHeld(f'closure has {len(entries)} files, over the accepted cap')
    if total > MAX_CLOSURE_BYTES:
        raise TransportHeld(f'closure has {total} bytes, over the accepted cap')
    _require_no_xattrs(stage, '.')
    return dict(entry_count=len(entries), total_bytes=total, entries=entries,
                directory_count=len(tree), directories=tree)


def _builder_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (BUILDER_CPU_SECONDS, BUILDER_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (BUILDER_ADDRESS_SPACE, BUILDER_ADDRESS_SPACE))
    resource.setrlimit(resource.RLIMIT_FSIZE, (BUILDER_FILE_SIZE, BUILDER_FILE_SIZE))
    resource.setrlimit(resource.RLIMIT_NOFILE, (BUILDER_OPEN_FILES, BUILDER_OPEN_FILES))


def build_image(stage: Path, image: Path, uid: int, gid: int, tool_fd: int, deadline: float) -> dict:
    """Deterministic build under enforced limits, executed through the verified descriptor.

    RLIMIT_FSIZE carries the image cap, so an oversized artifact cannot be written at all
    rather than being noticed afterwards. RLIMIT_NPROC is deliberately absent: it is not
    enforced for a privileged process and counts per real uid, so it would protect nothing
    here and claiming otherwise would be false.
    """
    if image.exists():
        raise TransportHeld('image already exists for this task')
    command = [f'/proc/self/fd/{tool_fd}', str(stage), str(image), *MKSQUASHFS_OPTIONS,
               '-force-uid', str(uid), '-force-gid', str(gid)]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransportHeld('preparation deadline passed before the build started')
    started = time.monotonic()
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=remaining, preexec_fn=_builder_limits, pass_fds=(tool_fd,),
        env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8'))
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise TransportHeld(f'image build failed ({result.returncode}): '
                            f'{result.stdout.decode()[-2000:]}')
    os.chown(image, 0, 0)
    os.chmod(image, 0o400)
    info = image.lstat()
    if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
        raise TransportHeld('backing image is not a single-link regular file')
    if info.st_size > MAX_IMAGE_BYTES:
        raise TransportHeld('image artifact exceeds the accepted cap')
    digest = hashlib.sha256()
    with open(image, 'rb', buffering=0) as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return dict(image_sha256=digest.hexdigest(), image_size_bytes=info.st_size,
                backing_dev=info.st_dev, backing_ino=info.st_ino,
                builder_milliseconds=int(elapsed * 1000))


# ---------------------------------------------------------------------------
# B3.1/B3.6 Loop association and mount, inside a private namespace
# ---------------------------------------------------------------------------

def unshare_mount_namespace() -> None:
    _checked(_libc.unshare(CLONE_NEWNS), 'unshare(CLONE_NEWNS)')
    _checked(_libc.mount(b'none', b'/', None, MS_REC | MS_PRIVATE, None), 'mount(/,rprivate)')


def attach_loop(image_fd: int, size: int, deadline: float) -> tuple[int, str, int]:
    """Associate the verified descriptor read-only, retrying past a lost race.

    LOOP_CTL_GET_FREE suggests a device but does not reserve it, so another process can take
    it first. Losing that race is ordinary and is retried; it is never resolved by detaching
    the device the competitor attached.
    """
    last = None
    while time.monotonic() < deadline:
        control = os.open('/dev/loop-control', os.O_RDWR | os.O_CLOEXEC)
        try:
            number = fcntl.ioctl(control, LOOP_CTL_GET_FREE)
        finally:
            os.close(control)
        if number < 0:
            raise TransportHeld('no free loop device is available')
        device = f'/dev/loop{number}'
        loop_fd = os.open(device, os.O_RDWR | os.O_CLOEXEC)
        try:
            info = struct.pack(_LOOP_INFO64, 0, 0, 0, 0, size, number, 0, 0,
                LO_FLAGS_READ_ONLY | LO_FLAGS_AUTOCLEAR, IMAGE_SCHEMA_VERSION.encode(), b'', b'', 0, 0)
            fcntl.ioctl(loop_fd, LOOP_CONFIGURE, struct.pack('II', image_fd, 0) + info + struct.pack('8Q', *([0] * 8)))
            return loop_fd, device, number
        except OSError as error:
            os.close(loop_fd)
            if error.errno != errno.EBUSY:
                raise TransportHeld(f'loop association failed: {os.strerror(error.errno)}') from error
            last = error
    raise TransportHeld(f'loop association did not succeed before the deadline: {last}')


def loop_status(loop_fd: int) -> dict:
    raw = fcntl.ioctl(loop_fd, LOOP_GET_STATUS64, bytes(struct.calcsize(_LOOP_INFO64)))
    values = struct.unpack(_LOOP_INFO64, raw)
    return dict(backing_dev=values[0], backing_ino=values[1], offset=values[3],
                sizelimit=values[4], number=values[5],
                read_only=bool(values[8] & LO_FLAGS_READ_ONLY),
                autoclear=bool(values[8] & LO_FLAGS_AUTOCLEAR))


def mount_image(device: str, target: Path) -> None:
    _checked(_libc.mount(device.encode(), str(target).encode(), b'squashfs',
                         MS_RDONLY | MS_NODEV | MS_NOSUID, None), 'mount(squashfs)')


def _backing_bytes_digest(image_fd: int, size: int, deadline: float) -> str:
    """Hash the retained backing bytes through the descriptor that holds them.

    Inode, size and mode say the file looks like the one that was built. They do not say the
    bytes are still those bytes, and nothing about a loop association re-reads them. This
    does, through the same descriptor the loop device is attached to, so what is hashed is
    what is served.
    """
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        if time.monotonic() >= deadline:
            raise TransportHeld('backing verification did not complete before the deadline')
        chunk = os.pread(image_fd, min(1 << 20, size - offset), offset)
        if not chunk:
            break
        digest.update(chunk)
        offset += len(chunk)
    if offset != size:
        raise TransportHeld('backing image is shorter than the descriptor records')
    return digest.hexdigest()


def _verify_backing(descriptor: dict, image_fd: int, loop_fd: int, *,
                    deadline: float, rehash: bool) -> dict:
    """The part of the chain that exists before any mount does: bytes, inode, association."""
    info = os.fstat(image_fd)
    if (info.st_dev, info.st_ino) != (descriptor['backing_dev'], descriptor['backing_ino']):
        raise TransportHeld('backing inode differs from the task descriptor')
    if info.st_size != descriptor['image_size_bytes']:
        raise TransportHeld('backing size differs from the task descriptor')
    if stat.S_IMODE(info.st_mode) != 0o400 or info.st_uid != 0 or info.st_nlink != 1:
        raise TransportHeld('backing image is not a single-link root-owned 0400 file')
    if rehash and _backing_bytes_digest(image_fd, info.st_size, deadline) != descriptor['image_sha256']:
        raise TransportHeld('backing bytes differ from the admitted image digest')
    status = loop_status(loop_fd)
    if not status['read_only'] or not status['autoclear']:
        raise TransportHeld('loop association is not read-only with autoclear')
    if (status['backing_dev'], status['backing_ino']) != (info.st_dev, info.st_ino):
        raise TransportHeld('loop is attached to a different backing inode')
    # Offset and size limit decide which bytes of the backing file the device serves. An
    # association that verifies against the whole file and then serves a window of it is
    # exactly the substitution these fences are meant to refuse.
    if status['offset'] != 0 or status['sizelimit'] != info.st_size:
        raise TransportHeld('loop association does not serve the whole verified image')
    return status


def backing_fence(descriptor: dict, image_fd: int, loop_fd: int,
                  deadline: float | None = None) -> dict:
    """Verify backing bytes, inode and loop association. Runs before the mount exists."""
    return _verify_backing(descriptor, image_fd, loop_fd,
                           deadline=deadline if deadline is not None else time.monotonic() + 120,
                           rehash=True)


def mount_challenge(mount: Path, inventory: dict, nonce: str) -> str:
    """A fresh readback challenge bound to the admitted bytes, not to the current mount.

    Both sides reading the mount and agreeing proves only that the mount is stable: a
    substituted filesystem that answers consistently produces the same response on both
    sides. The digest folded in here is the one the inventory recorded when the image was
    built, and the entry read from the mount has to reproduce it, so a mount serving other
    bytes fails here rather than agreeing with itself.

    The nonce selects which entries are read, so a response cannot be precomputed or
    replayed across generations.
    """
    entries = sorted(inventory['entries'])
    if not entries:
        raise TransportHeld('inventory is empty; nothing to challenge')
    digest = hashlib.sha256(nonce.encode())
    seed = int(nonce[:16], 16)
    for step in range(8):
        chosen = entries[(seed + step * 7919) % len(entries)]
        expected = inventory['entries'][chosen]
        observed = hashlib.sha256()
        read = 0
        with open(mount / chosen, 'rb', buffering=0) as handle:
            while read < CHALLENGE_ENTRY_BYTES:
                block = handle.read(min(1 << 16, CHALLENGE_ENTRY_BYTES - read))
                if not block:
                    break
                observed.update(block)
                read += len(block)
        # Compared against the prefix digest the inventory recorded, whatever the entry's
        # size. Comparing only when the whole entry fits within the bound would leave every
        # large entry unchecked, which is the case that matters most.
        if 'prefix_sha256' not in expected:
            raise TransportHeld(f'inventory records no challenge digest for {chosen}')
        if observed.hexdigest() != expected['prefix_sha256']:
            raise TransportHeld(f'mount serves different bytes for {chosen}')
        digest.update(chosen.encode())
        digest.update(expected['sha256'].encode())
        digest.update(observed.digest())
    return digest.hexdigest()


def mount_fence(descriptor: dict, image_fd: int, loop_fd: int, mount: Path,
                deadline: float | None = None) -> dict:
    """Re-verify the whole chain: backing bytes, loop association and mount identity."""
    status = _verify_backing(descriptor, image_fd, loop_fd,
                             deadline=deadline if deadline is not None else time.monotonic() + 120,
                             rehash=True)
    device = f'/dev/loop{status["number"]}'
    rdev = os.stat(device).st_rdev
    expected_device = f'{os.major(rdev)}:{os.minor(rdev)}'
    found = None
    with open('/proc/self/mountinfo', 'rb') as handle:
        for line in handle.read().decode().splitlines():
            fields = line.split()
            separator = fields.index('-')
            if fields[4] == str(mount):
                found = dict(device=fields[2], root=fields[3], filesystem=fields[separator + 1],
                             source=fields[separator + 2], options=fields[5].split(','))
    if found is None:
        raise TransportHeld('the task mount is not present in this namespace')
    if found['filesystem'] != 'squashfs':
        raise TransportHeld('the task mount is not a squashfs')
    if not {'ro', 'nodev', 'nosuid'}.issubset(set(found['options'])):
        raise TransportHeld('the task mount is not read-only, nodev and nosuid')
    # Without this the chain stops at 'a read-only squashfs is mounted here'. It says nothing
    # about whether that filesystem is the one this loop device serves, which is the only
    # thing the verified backing bytes reach.
    if found['device'] != expected_device:
        raise TransportHeld('the task mount is not served by the verified loop device')
    if found['root'] != '/':
        raise TransportHeld('the task mount exposes a subtree rather than the whole image')
    mount_dev = os.stat(mount).st_dev
    if (os.major(mount_dev), os.minor(mount_dev)) != (os.major(rdev), os.minor(rdev)):
        raise TransportHeld('the mount root is not on the verified loop device')
    return dict(loop=status, mount=found)


# ---------------------------------------------------------------------------
# B4 Privilege drop, parent-death chain and the worker side
# ---------------------------------------------------------------------------

def drop_to_worker(uid: int, gid: int, controller: tuple[int, int]) -> None:
    """Drop irreversibly, then arm the parent-death signal, in that order.

    Measured, not assumed: the kernel clears PR_SET_PDEATHSIG when credentials change, so
    arming before the drop leaves the worker with no parent-death signal at all and the
    controller-to-worker chain silently would not exist. It is armed after the final
    credential change and read back, and the controller's identity is re-checked so a
    controller that died inside the drop window is detected rather than inherited.
    """
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    if os.getresuid() != (uid, uid, uid) or os.getresgid() != (gid, gid, gid):
        raise TransportHeld('privilege drop did not take effect')
    try:
        os.setresuid(0, 0, 0)
    except PermissionError:
        pass
    else:
        raise TransportHeld('privilege drop was reversible')
    _libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    armed = ctypes.c_int(0)
    _libc.prctl(PR_GET_PDEATHSIG, ctypes.byref(armed), 0, 0, 0)
    if armed.value != int(signal.SIGKILL):
        raise TransportHeld('parent-death signal did not arm after the credential drop')
    pid, start = controller
    if os.getppid() != pid or _process_start_time(pid) != start:
        raise TransportHeld('the controller changed while the worker was dropping privilege')


def build_v2_closure(mount: Path, descriptor: dict, libraries: list[dict], inventory: dict,
                     source: dict, environment_ref: dict, job_id: str,
                     expected_sandbox_sha256: str) -> object:
    """Assemble the closure the launcher admits, from the mounted image and sealed libraries."""
    from packages.alpha_lifecycle.contracts.base import SourceIdentity
    from packages.data_contracts import ArtifactRefV1
    from packages.alpha_lifecycle.sandbox_policy import (
        BWRAP_CAPABILITIES, BWRAP_VERSION, SANDBOX_PROFILE_V2_SHA256)
    from services.job_worker.p3_host_profile import P3RuntimeImageDescriptor
    from services.job_worker.p3_spawn import (
        CompleteP3Closure, P3ClosureMount, P3Sandbox, _digest)
    from pathlib import PurePosixPath

    release_root, python_root = descriptor['release_root'], descriptor['python_root']
    mounts = []
    for relative, entry in sorted(inventory['entries'].items()):
        head, _, rest = relative.partition('/')
        if head == release_root:
            target = PurePosixPath('/p3/release') / rest
        elif head == python_root:
            target = PurePosixPath('/p3/python') / rest
        else:
            raise TransportHeld(f'image entry is outside the accepted roots: {relative}')
        path = mount / relative
        info = path.lstat()
        mounts.append(P3ClosureMount(path, target, (info.st_dev, info.st_ino),
                                     entry['size_bytes'], entry['mode'], entry['sha256']))
    for library in libraries:
        # The target keeps the SONAME the loader asks for, but the source has to be the
        # resolved file: /lib is a symlink to usr/lib on this host, and the protected read
        # walks ancestors with O_NOFOLLOW, so a SONAME source stops at the first symlink.
        path = Path(library['source'])
        info = path.lstat()
        mounts.append(P3ClosureMount(path, PurePosixPath(library['target']),
                                     (info.st_dev, info.st_ino), library['size_bytes'],
                                     library['mode'], library['sha256']))
    # PurePosixPath orders by parts, not by string, and the launcher compares against
    # sorted() on the paths themselves. Sorting by str would produce a different order.
    mounts.sort(key=lambda m: m.target)
    # The expected digest comes from the request. Hashing whatever /usr/bin/bwrap holds at
    # this moment and then using that digest as the sandbox's identity is self-fulfilling:
    # any binary sitting at the path would have been accepted as the one that was pinned.
    sandbox_fd, sandbox_record = open_verified_tool(BWRAP, expected_sandbox_sha256)
    os.close(sandbox_fd)
    info = BWRAP.lstat()
    sandbox = P3Sandbox(BWRAP, (sandbox_record['dev'], sandbox_record['ino']),
                        sandbox_record['sha256'], stat.S_IMODE(info.st_mode),
                        SANDBOX_PROFILE_V2_SHA256, BWRAP_VERSION, BWRAP_CAPABILITIES)
    identity = SourceIdentity.model_validate(source)
    reference = ArtifactRefV1.model_validate(environment_ref)
    fingerprint = _digest(dict(source=identity, environment_ref=reference,
        files=[dict(target=str(m.target), sha256=m.sha256, size=m.size, mode=m.mode) for m in mounts],
        sandbox_sha256=sandbox.executable_sha256, sandbox_policy_sha256=sandbox.profile_sha256))
    image = P3RuntimeImageDescriptor.model_validate_json(canonical_json_bytes(dict(
        schema_version=IMAGE_SCHEMA_VERSION, image_sha256=descriptor['image_sha256'],
        image_size_bytes=descriptor['image_size_bytes'], entry_count=inventory['entry_count'],
        logical_bytes=inventory['total_bytes'],
        closure_fingerprint=_digest(dict(schema_version=IMAGE_SCHEMA_VERSION,
            files=[dict(target=str(m.target), sha256=m.sha256, size=m.size, mode=m.mode)
                   for m in mounts if str(m.target).startswith(('/p3/release', '/p3/python'))])),
        inventory_ref=descriptor['inventory_ref'],
        toolchain_manifest_sha256=descriptor['toolchain_manifest_sha256'],
        worker_uid=descriptor['worker_uid'], worker_gid=descriptor['worker_gid'],
        release_root=release_root, python_root=python_root,
        external_libraries=sorted(({k: v for k, v in library.items() if k != 'source'}
                                   for library in libraries), key=lambda l: l['target']))))
    return CompleteP3Closure(identity, reference, mount / python_root, tuple(mounts), sandbox,
                             fingerprint, job_id, '0' * 64, image, mount)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

MAX_WORKER_OUTPUT_BYTES = 1024 * 1024


def _read_bounded(descriptor: int, deadline: float) -> tuple[bytes, bool]:
    """Read the worker's result under the deadline and an output cap.

    An unbounded read meant the escalation below only ran after EOF, so a worker that hung
    while holding the pipe open never reached its own deadline handler. Polling here is what
    makes that handler reachable.
    """
    payload = bytearray()
    poller = select.poll()
    poller.register(descriptor, select.POLLIN)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return bytes(payload), True
        if not poller.poll(min(remaining, 1.0) * 1000):
            continue
        block = os.read(descriptor, 1 << 16)
        if not block:
            return bytes(payload), False
        payload += block
        if len(payload) > MAX_WORKER_OUTPUT_BYTES:
            return bytes(payload[:MAX_WORKER_OUTPUT_BYTES]), True


def reap_with_escalation(child: int, deadline: float) -> dict:
    """Wait for the worker, then escalate. An unbounded wait is not a deadline.

    TERMINATE_GRACE_SECONDS and REAP_GRACE_SECONDS existed as constants before this and were
    never used, so the contract's escalation was declared rather than implemented. A task
    stuck in uninterruptible kernel sleep can still outlast this; that case is reported as
    unreaped rather than described as clean.
    """
    escalation = []
    for signal_number, grace in ((None, max(0.0, deadline - time.monotonic())),
                                 (signal.SIGTERM, TERMINATE_GRACE_SECONDS),
                                 (signal.SIGKILL, REAP_GRACE_SECONDS)):
        if signal_number is not None:
            try:
                os.kill(child, signal_number)
                escalation.append(signal.Signals(signal_number).name)
            except ProcessLookupError:
                break
        limit = time.monotonic() + grace
        while time.monotonic() < limit:
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid == child:
                return dict(status=status, escalation=escalation, reaped=True)
            time.sleep(0.02)
    return dict(status=None, escalation=escalation, reaped=False,
                note='the worker did not reap; resources are retained rather than '
                     'reported as cleaned')


def _closure_objects(stage: Path, inventory: dict) -> list[Path]:
    return [stage / relative for relative in inventory['entries']
            if relative.endswith('.so') or '.so.' in relative
            or relative.endswith('/bin/python3.11')]


def _seal_external_libraries(sources: list[Path], snapshots: Path) -> list[dict]:
    """Snapshot each host library the closure reaches into a task-private root-owned copy.

    The sandbox is then fed bytes that live under the task's own protected root rather than a
    host path a cooperating writer could still modify, and the accepted closure modes are
    satisfied without changing anything on the host.
    """
    libraries = []
    for index, path in enumerate(sources):
        fd, record = seal_library(path)
        os.close(fd)
        snapshot = snapshots / f'{index:02d}-{Path(record["target"]).name}'
        snapshot.write_bytes(Path(os.path.realpath(record['target'])).read_bytes())
        os.chown(snapshot, 0, 0)
        os.chmod(snapshot, 0o555)
        info = snapshot.lstat()
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != record['sha256']:
            raise TransportHeld(f'library snapshot for {record["target"]} does not match its seal')
        record['source'] = str(snapshot)
        record['source_dev'], record['source_ino'] = info.st_dev, info.st_ino
        libraries.append(record)
    if sum(library['size_bytes'] for library in libraries) > MAX_EXTERNAL_LIBRARY_TOTAL_BYTES:
        raise TransportHeld('external libraries exceed the accepted aggregate bound')
    return libraries


def command_prepare(request: dict, root: Path, deadline: float) -> dict:
    """Build the image, then publish its descriptor durably before anything consumes it."""
    stage = Path(request['stage'])
    uid, gid = request['worker_uid'], request['worker_gid']
    normalised = normalise_stage_modes(stage)
    inventory = logical_inventory(stage)
    inventory['normalised'] = normalised
    roots = {relative.split('/', 1)[0] for relative in inventory['entries']}
    if roots != {request['release_root'], request['python_root']}:
        raise TransportHeld('stage does not contain exactly the accepted release and python roots')
    snapshots = root / 'libraries'
    snapshots.mkdir(mode=0o755, exist_ok=True)
    os.chown(snapshots, 0, 0)
    # Both descriptors stay open across the build. The expected digests come from the
    # protected request, not from whatever the paths currently hold: hashing the present
    # bytes and then treating that digest as the authority pins nothing at all.
    loader_fd, loader = seal_verified_tool(LOADER, request['loader_sha256'])
    tool_fd, toolchain = seal_verified_tool(MKSQUASHFS, request['mksquashfs_sha256'])
    sandbox_fd, sandbox = open_verified_tool(BWRAP, request['bwrap_sha256'])
    try:
        # Every executable the transport runs is in the manifest, bubblewrap included. It is
        # not executed here, so it is verified rather than sealed; p3_startup reads it into
        # its own sealed memfd at launch.
        manifest = toolchain_manifest([(MKSQUASHFS, toolchain), (BWRAP, sandbox)],
                                      loader_fd, stage)
        manifest['loader'] = loader
        # Resolved before the image is built, not after. The library closure does not depend
        # on the image, and building first meant a closure this refuses left a 180 MB
        # artifact behind with no descriptor -- which cleanup then rightly declines to remove,
        # because it cannot account for it. Failing before anything is created costs nothing.
        unresolvable: list[dict] = []
        sources = library_closure(_closure_objects(stage, inventory), stage, loader_fd,
                                  unresolvable)
        # A durable record of the attempt, written before the artifact exists. Without it a
        # crash between creation and the PREPARED record leaves a file nothing identifies.
        #
        # It must never overwrite an existing record. Writing it unconditionally destroyed a
        # previous PREPARED claim on a task that already held an image: build_image then
        # refused, and the surviving record described an artifact it could not identify, so a
        # later cleanup had nothing to check the older image against.
        if (root / 'quarantine.json').exists():
            raise TransportHeld('this task already holds a quarantine record; '
                                'reclaiming it is a separate recovery decision')
        _durable_write(root / 'quarantine.json', canonical_json_bytes(dict(
            task=request['job_id'], owner_pid=os.getpid(),
            owner_start_time=_process_start_time(os.getpid()), boot_id=_host_identity()[1],
            image_path=str(root / 'runtime.squashfs'), state='PREPARING')) + b'\n')
        built = build_image(stage, root / 'runtime.squashfs', uid, gid, tool_fd, deadline)
        libraries = _seal_external_libraries(sources, snapshots)
    finally:
        os.close(sandbox_fd)
        os.close(tool_fd)
        os.close(loader_fd)
    descriptor = dict(schema_version=IMAGE_SCHEMA_VERSION, release_root=request['release_root'],
        python_root=request['python_root'], worker_uid=uid, worker_gid=gid,
        toolchain_manifest_sha256=hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        inventory_ref=request['inventory_ref'], external_libraries=libraries, **built)
    # Recorded in the summary and the inventory rather than in the descriptor, whose schema is
    # pinned. These objects travel in the image and remain unloadable on this host, which is
    # the same thing that would happen without the transport.
    inventory['unresolvable_objects'] = unresolvable
    _durable_write(root / 'quarantine.json', canonical_json_bytes(dict(
        task=request['job_id'], image_dev=built['backing_dev'], image_ino=built['backing_ino'],
        image_sha256=built['image_sha256'], owner_pid=os.getpid(),
        owner_start_time=_process_start_time(os.getpid()), boot_id=_host_identity()[1],
        state='PREPARED')) + b'\n')
    _durable_write(root / 'toolchain-manifest.json', canonical_json_bytes(manifest) + b'\n')
    _durable_write(root / 'image-descriptor.json', canonical_json_bytes(descriptor) + b'\n')
    _durable_write(root / 'inventory.json', canonical_json_bytes(inventory) + b'\n')
    summary = {k: v for k, v in descriptor.items() if k != 'external_libraries'}
    summary['external_library_count'] = len(libraries)
    summary['toolchain_members'] = len(manifest['members'])
    summary['unresolvable_objects'] = unresolvable
    summary['inventory'] = dict(entry_count=inventory['entry_count'],
                                total_bytes=inventory['total_bytes'],
                                directory_count=inventory['directory_count'])
    return summary


def command_probe(request: dict, root: Path, deadline: float) -> dict:
    """Mount the image and run the unmodified startup probe as the worker.

    This is the acceptance the whole transport exists for: the complete closure reaching an
    actual bubblewrap launch instead of being refused at the parser's token capacity.
    """
    descriptor = json.loads((root / 'image-descriptor.json').read_bytes())
    inventory = json.loads((root / 'inventory.json').read_bytes())
    # Load the launcher and every model the closure needs while still privileged. The
    # worker cannot traverse the source tree after dropping to its own uid, and importing
    # application code before the drop is also the order the contract requires.
    from services.job_worker.p3_startup import probe_p3_startup
    import packages.alpha_lifecycle.contracts.base            # noqa: F401
    import packages.data_contracts                            # noqa: F401
    import services.job_worker.p3_host_profile                # noqa: F401
    import services.job_worker.p3_spawn                       # noqa: F401
    import services.job_worker.engine_spawn                   # noqa: F401
    image_fd = os.open(root / 'runtime.squashfs', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    mount = root / 'mnt'
    controller = (os.getpid(), _process_start_time(os.getpid()))
    try:
        unshare_mount_namespace()
        # The worker must reach the mount and its own private directory. The protected
        # directory walk opens every ancestor for reading, so traversal alone is not enough
        # and the task root is readable for the duration of the probe. The backing image
        # stays root-owned 0400, so its name becomes visible but its bytes do not. The mode
        # is restored when the probe finishes, and the mount exists only in this namespace.
        os.chmod(root, 0o755)
        mount.mkdir(mode=0o555, exist_ok=True)
        os.chown(mount, 0, 0)
        os.chmod(mount, 0o555)
        loop_fd, device, number = attach_loop(image_fd, descriptor['image_size_bytes'], deadline)
        reaped = None
        try:
            backing_fence(descriptor, image_fd, loop_fd)
            mount_image(device, mount)
            # A generation nonce distinguishes this mount lifetime from a later one at the
            # same device name or numeric mount id, neither of which is identity.
            nonce = os.urandom(32).hex()
            fences = [dict(point='after-mount', nonce=nonce,
                           **mount_fence(descriptor, image_fd, loop_fd, mount))]
            expected_challenge = mount_challenge(mount, inventory, nonce)
            private = root / 'worker'
            private.mkdir(mode=0o700, exist_ok=True)
            os.chown(private, descriptor['worker_uid'], descriptor['worker_gid'])
            # Fence again immediately before the worker is admitted, so the window between
            # setup and use is covered rather than assumed.
            fences.append(dict(point='before-worker', nonce=nonce,
                               **mount_fence(descriptor, image_fd, loop_fd, mount)))
            read, write = os.pipe()
            child = os.fork()
            if child == 0:
                status = 0
                try:
                    os.close(read)
                    os.close(loop_fd)
                    os.close(image_fd)
                    drop_to_worker(descriptor['worker_uid'], descriptor['worker_gid'], controller)
                    # The diagnostic used to run bubblewrap directly from /usr/bin/bwrap.
                    # That was an execution straight from a path, with no seal and no digest
                    # check at the moment it ran, inside the one operation whose whole point
                    # is that nothing executes unverified. It is removed rather than sealed:
                    # the verified launch below already covers what it reported.
                    interpreter = mount / descriptor['python_root'] / 'bin/python3.11'
                    diagnostics = dict(
                        interpreter_mode=oct(os.stat(interpreter).st_mode & 0o7777),
                        can_execute=os.access(interpreter, os.X_OK))
                    closure = build_v2_closure(mount, descriptor, descriptor['external_libraries'],
                        inventory, request['source'], request['environment_ref'],
                        request['job_id'], request['bwrap_sha256'])
                    proof = probe_p3_startup(closure, private_root=private)
                    challenge = mount_challenge(mount, inventory, nonce)
                    os.write(write, canonical_json_bytes(dict(startup=proof,
                        challenge=challenge,
                        diagnostics=diagnostics,
                        open_fds=sorted(os.listdir('/proc/self/fd')))))
                except BaseException as error:
                    os.write(write, canonical_json_bytes(dict(
                        error=f'{type(error).__name__}: {error}',
                        diagnostics=locals().get('diagnostics'))))
                    status = 1
                finally:
                    os.close(write)
                os._exit(status)
            os.close(write)
            payload, truncated = _read_bounded(read, deadline)
            os.close(read)
            # Reaping comes before the payload is parsed, and parsing cannot raise past it.
            # The earlier order put json.loads first: a truncated payload -- which is exactly
            # what the output cap produces -- or any malformed output raised before `reaped`
            # was ever assigned, and the cleanup below then defaulted to believing the worker
            # had been reaped and released the mount and the loop. That is the error path the
            # cap exists to create, not a hypothetical one.
            reaped = reap_with_escalation(child, deadline)
            try:
                observed = json.loads(payload) if payload else dict(error='worker produced no output')
                if not isinstance(observed, dict):
                    raise ValueError('worker payload is not a JSON object')
            except ValueError as error:
                observed = dict(error=f'worker payload could not be parsed: {error}',
                                payload_bytes=len(payload))
            observed['output_truncated'] = truncated
            observed['worker_status'] = reaped['status']
            observed['worker_reaping'] = reaped
            if truncated:
                raise TransportHeld('worker output exceeded its bound before the deadline')
            if not reaped['reaped']:
                raise TransportHeld('worker did not reap within the deadline')
            # Fence once more after the worker finishes, and require the challenge the
            # worker computed to match what the controller computed for this generation.
            fences.append(dict(point='after-worker', nonce=nonce,
                               **mount_fence(descriptor, image_fd, loop_fd, mount)))
            if observed.get('challenge') != expected_challenge:
                # The worker catches everything and reports it in its payload, so a failure
                # inside the worker reaches here as a missing challenge. Reporting only the
                # mismatch hid the actual cause behind a message about something else.
                reported = observed.get('error')
                raise TransportHeld('mount readback challenge did not match this generation'
                                    + (f'; the worker reported: {reported}' if reported else ''))
        finally:
            # Unknown is unreaped. Reading this through locals() with a default of True meant
            # any exception raised before the assignment released the mount and the loop while
            # reporting a cleanup that had not been established.
            if reaped is not None and reaped.get('reaped'):
                if _libc.umount(str(mount).encode()) != 0:
                    _libc.umount2(str(mount).encode(), MNT_DETACH)
                fcntl.ioctl(loop_fd, LOOP_CLR_FD, 0)
                os.close(loop_fd)
            else:
                _durable_write(root / 'quarantine.json', canonical_json_bytes(dict(
                    task=request['job_id'], state='UNREAPED_WORKER',
                    loop_device=device, mount=str(mount),
                    note='mount and loop deliberately retained; an unreaped worker may still '
                         'hold them, and releasing them here would report a cleanup that did '
                         'not happen')) + b'\n', mode=0o600)
    finally:
        os.close(image_fd)
        os.chmod(root, 0o700)
    return dict(fences=fences, mount_nonce=nonce, loop_device=device,
                loop_number=number, observed=observed)


def command_inspect(root: Path) -> dict:
    record = root / 'quarantine.json'
    if not record.exists():
        return dict(state='ABSENT', root=str(root))
    return dict(state='RECORDED', quarantine=json.loads(record.read_bytes()),
                entries=sorted(p.name for p in root.iterdir()))


def command_cleanup(root: Path) -> dict:
    """Act only on artifacts the quarantine record identifies by inode, and fail closed.

    An absent or unreadable record means ownership is unknown, and unknown ownership is a
    reason to retain, never to delete. The record itself is removed last and only when
    nothing it describes was kept, so a retained artifact is never left unidentifiable.
    """
    record_path = root / 'quarantine.json'
    try:
        record = json.loads(record_path.read_bytes())
    except (OSError, ValueError):
        return dict(quarantined=True, reason='quarantine record is absent or unreadable',
                    removed=[], root_exists=root.exists())
    if record.get('state') == 'PREPARING':
        # A build that did not reach its PREPARED record. The record was written *before* the
        # artifact existed, so it carries no inode claim and cannot identify what is on disk
        # now: a name is not ownership. An earlier version removed by path, which is the one
        # thing the contract forbids here -- an artifact with no durable claim is retained,
        # not reclaimed.
        #
        # The only safe removal is the record itself, and only when there is no artifact for
        # it to be ambiguous about.
        image = root / 'runtime.squashfs'
        if image.exists() or image.is_symlink():
            return dict(quarantined=True,
                        reason='an incomplete build left an artifact with no inode claim; '
                               'retained for inspection because nothing identifies it',
                        removed=[], root_exists=root.exists())
        if record.get('boot_id') != _host_identity()[1]:
            return dict(quarantined=True, reason='incomplete build is from another boot',
                        removed=[], root_exists=root.exists())
        record_path.unlink(missing_ok=True)
        return dict(quarantined=False, reason='incomplete build left no artifact',
                    removed=['quarantine.json'], root_exists=root.exists())
    try:
        expected = (record['image_dev'], record['image_ino'])
    except KeyError:
        return dict(quarantined=True, reason='quarantine record is absent or unreadable',
                    removed=[], root_exists=root.exists())
    removed, retained = [], False
    image = root / 'runtime.squashfs'
    if image.exists():
        info = image.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            removed.append('runtime.squashfs: RETAINED, not a single-link regular file')
            retained = True
        elif (info.st_dev, info.st_ino) != expected:
            removed.append('runtime.squashfs: RETAINED, inode does not match the record')
            retained = True
        else:
            os.chmod(image, 0o600)
            image.unlink()
            removed.append('runtime.squashfs')
    # task.lock is deliberately absent from this list. Unlinking the entry while this
    # controller still holds it lets another controller create a fresh lock file and enter
    # the critical section while cleanup is still running, which is the one thing the lock
    # exists to prevent. The lock is released by closing the descriptor, not by deletion.
    for name in ('image-descriptor.json', 'inventory.json'):
        path = root / name
        if path.exists() and not retained:
            os.chmod(path, 0o600)
            path.unlink()
            removed.append(name)
    if retained:
        removed.append('quarantine.json: RETAINED, it identifies the retained artifacts')
    else:
        record_path.unlink(missing_ok=True)
        removed.append('quarantine.json')
    for directory in (root / 'mnt', root / 'worker', root):
        # The task root keeps its lock entry, so it is never empty and never removed here.
        # Reclaiming it belongs to a recovery path that can first establish no owner holds it.
        if directory == root:
            continue
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
            removed.append(str(directory))
    # A retained artifact means the task is not finished with, so the outcome is quarantine
    # rather than a clean cleanup. Reporting quarantined=False beside a RETAINED line was
    # contradictory.
    return dict(quarantined=retained, removed=removed, root_exists=root.exists())


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print(json.dumps(dict(error='the transport controller must run as root')))
        return 2
    if len(argv) != 2:
        print(json.dumps(dict(error='usage: p3_local_transport.py REQUEST_NAME')))
        return 2
    try:
        request = read_request(argv[1])
        digest, identity = task_identity(request)
        root = task_root(digest)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(root, 0, 0)
        os.chmod(root, 0o700)
        deadline = time.monotonic() + PREPARE_WALL_SECONDS
        with TaskLock(root):
            operation = request['operation']
            if operation == 'prepare':
                result = command_prepare(request, root, deadline)
            elif operation == 'probe':
                result = command_probe(request, root, deadline)
            elif operation == 'inspect':
                result = command_inspect(root)
            elif operation == 'cleanup':
                result = command_cleanup(root)
            else:
                raise TransportHeld(f'operation {operation} is accepted but not implemented')
    except TransportHeld as held:
        print(json.dumps(dict(status='HELD', reason=str(held))))
        return 1
    except Exception as error:                                   # noqa: BLE001
        print(json.dumps(dict(status='HELD', reason=f'{type(error).__name__}: {error}')))
        return 1
    print(json.dumps(dict(status='OK', operation=request['operation'],
                          task=digest, result=result), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
