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
import pwd
import re
import resource
import select
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

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
BUILD_PARENT = Path('/run/trading-agent-local/p3-transport-build')
REQUEST_PARENT = Path('/etc/trading-agent/local-p3-transport')
# The issuer store the LOCAL runtime profile is installed into, owned by the authority
# protocol. Named here so the controller reads the same place the installer writes.
STORE_ROOT = Path('/etc/trading-agent/local-qualification')

# The builder's own identity, reserved for it and shared with no other role. It is resolved by
# name rather than by a number in the request, because a uid a caller could choose is not a
# reservation: the point of the account is that nothing else on the host runs as it.
BUILDER_USER = 'ta-local-builder'

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
# The admitted workflow maximum the session profile already enforces. The controller does not
# own the run's deadline -- the session does, and B3.5's limits say run keeps the existing
# admitted policy limits -- so this only refuses to outlive it.
WORKFLOW_DEADLINE_SECONDS = 37 * 60
BUILDER_CPU_SECONDS = 120
BUILDER_ADDRESS_SPACE = 2 * 1024**3
BUILDER_FILE_SIZE = MAX_IMAGE_BYTES
BUILDER_OPEN_FILES = 256
BUILDER_PROCESSES = 32
# How often the mount/backing fence runs while the worker is alive. Short enough that a
# substitution has nowhere quiet to sit, long enough not to become the workload.
FENCE_INTERVAL_SECONDS = 1.0
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


def open_task_root(digest: str) -> Path:
    """Derive the task root and check the chain that leads to it before using it.

    An existing directory with the right name is not evidence that this task created it. The
    parent chain is checked for root ownership and for group or world writability, because a
    writable ancestor means the final component can be replaced between the check and the use,
    and the root itself is opened with O_NOFOLLOW so a symlink cannot stand in for it.
    """
    for ancestor in reversed(TASK_PARENT.parents):
        if ancestor == Path('/'):
            continue
        try:
            info = ancestor.stat()
        except FileNotFoundError:
            # Nothing to vouch for yet. The mkdir below creates it as root, under root's own
            # umask, which is the state this check would have demanded of it anyway. On a
            # fresh boot /run is empty, so refusing here would mean the first prepare after
            # every reboot failed on a directory the controller was about to create itself.
            continue
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise TransportHeld(f'{ancestor} is not a protected ancestor of the task root')
    # Root-owned and traversable, not private: the probe drops to the worker uid and has to
    # reach the task root through here. Tightening this to 0700 would close the one operation
    # the transport exists for. Traversal is not enumeration, and every entry below is
    # separately permissioned.
    TASK_PARENT.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chown(TASK_PARENT, 0, 0)
    os.chmod(TASK_PARENT, 0o755)
    root = task_root(digest)
    root.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise TransportHeld('task root is not a directory')
        if info.st_uid != 0 or info.st_gid != 0:
            raise TransportHeld('task root is not root-owned')
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    return root


def require_task_claim(root: Path, operation: str) -> dict | None:
    """A claimed task root is a recovery subject, never a session to resume.

    B3.1 is explicit: "A prepared session cannot outlive its owner unless a separately
    specified handoff transfers both ownership and descriptors; v2 does not implement such a
    handoff." An earlier version of this function did the opposite -- it carried ownership
    across invocations so a later controller could probe an image some earlier one had left,
    and its docstring called that "what has to survive between them". That is the handoff the
    contract excludes, and formalising it made the deviation harder to see rather than fixing
    it.

    So a claim is never continued. `prepare` and `probe` each open their own session and build
    their own image under their own lock, and a claim they did not write belongs to a session
    that is over. What the contract does allow a later invocation to do with it is look at it
    and release it, which is `inspect` and `cleanup`.
    """
    path = root / 'quarantine.json'
    if not path.exists():
        return None
    if operation not in ('inspect', 'cleanup'):
        raise TransportHeld(
            'this task root is already claimed; a prepared session cannot be resumed, so '
            'inspect it or release it with cleanup before opening a new one')
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        # `cleanup` already treats an unreadable record as a reason to retain rather than
        # remove, and `inspect` exists to look at exactly this.
        return None


class TaskLock:
    """Exclusive ownership for one invocation. Lifetime ownership is the claim, not this.

    An earlier version of this docstring said it covered the whole image lifetime. It never
    did: the lock is an open descriptor and dies with the process. What it gives is mutual
    exclusion between concurrent invocations, which is real and necessary; `require_task_claim`
    carries the part that has to survive between them.
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


def seal_verified_tool(path: Path, expected_sha256: str,
                       owner: tuple[int, int] | None = None) -> tuple[int, dict]:
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
        if owner is not None:
            # Sealing fixes the bytes, not the metadata, so this still takes effect -- and it
            # is the only reason a dropped builder can execute the descriptor at all.
            os.fchown(sealed, owner[0], owner[1])
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


def builder_identity(worker: tuple[int, int]) -> tuple[int, int]:
    """The reserved builder account, or a refusal. There is no fall back to root.

    An absent account used to mean the build ran as root, which is how the builder stayed
    privileged while four rlimits made it look bounded. Refusing here states the precondition
    instead: the host reserves an identity for the builder, or no image is built.
    """
    try:
        entry = pwd.getpwnam(BUILDER_USER)
    except KeyError as error:
        raise TransportHeld(
            f'builder identity {BUILDER_USER} is not reserved on this host; '
            'the transport does not build as root') from error
    if entry.pw_uid == 0 or entry.pw_gid == 0:
        raise TransportHeld('builder identity is privileged')
    if entry.pw_uid == worker[0]:
        # Compared on the uid alone. RLIMIT_NPROC counts against the real uid, so a builder
        # that shares the worker's uid shares its process budget however the groups differ;
        # comparing the whole tuple let exactly that through.
        raise TransportHeld('builder identity is shared with the worker')
    return entry.pw_uid, entry.pw_gid


def _builder_limits(uid: int, gid: int) -> Callable[[], None]:
    """Drop to the builder, then bound it. The order is what makes the bound real.

    RLIMIT_NPROC counts against the real uid and is not enforced for a process holding
    CAP_SYS_RESOURCE, so setting it while still root bounds nothing -- which is exactly why
    the previous version left it out and said so. Once the credentials are gone the limit
    applies at every fork, so it is set after the drop rather than dropped from the contract.

    This runs between fork and exec in the child, so it must not allocate or log; everything
    it needs is already resolved.
    """
    def apply() -> None:
        os.setgroups([])
        os.setresgid(gid, gid, gid)
        os.setresuid(uid, uid, uid)
        if os.getresuid() != (uid, uid, uid):
            raise RuntimeError('builder privilege drop did not take effect')
        resource.setrlimit(resource.RLIMIT_NPROC, (BUILDER_PROCESSES, BUILDER_PROCESSES))
        resource.setrlimit(resource.RLIMIT_CPU, (BUILDER_CPU_SECONDS, BUILDER_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (BUILDER_ADDRESS_SPACE, BUILDER_ADDRESS_SPACE))
        resource.setrlimit(resource.RLIMIT_FSIZE, (BUILDER_FILE_SIZE, BUILDER_FILE_SIZE))
        resource.setrlimit(resource.RLIMIT_NOFILE, (BUILDER_OPEN_FILES, BUILDER_OPEN_FILES))
    return apply


@contextmanager
def builder_lock(uid: int) -> Iterator[int]:
    """One build at a time for the reserved builder identity, across tasks.

    The identity is reserved and its `RLIMIT_NPROC` is one budget. Two tasks building at once
    would spend each other's processes, and the bound would stop meaning what it says. This is
    the existing `flock` primitive on a stable root-owned entry, not a scheduler.
    """
    BUILD_PARENT.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = BUILD_PARENT / f'builder-{uid}.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise TransportHeld(
                f'another task is building as the reserved builder identity {uid}') from error
        held = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            raise TransportHeld('builder lock entry was replaced while held')
        yield fd
    finally:
        os.close(fd)


def _builder_workspace(image: Path, uid: int, gid: int) -> Path:
    """A directory the builder owns, outside the root-only task root.

    The task root stays 0700 root-owned, so the builder cannot traverse it and cannot be given
    somewhere to write inside it without widening it. It writes here and root moves the result
    across; both live under the same tmpfs, so the move is a rename and never a copy.
    """
    BUILD_PARENT.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chown(BUILD_PARENT, 0, 0)
    os.chmod(BUILD_PARENT, 0o755)
    workspace = BUILD_PARENT / image.parent.name
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(mode=0o700)
    os.chown(workspace, uid, gid)
    return workspace


def _stage_is_reachable(stage: Path, uid: int, gid: int) -> None:
    """The builder has to be able to traverse to the stage, not merely read its entries.

    `normalise_stage_modes` makes the stage itself 0555, but an ancestor without o+x refuses
    the walk before any of that matters, and mksquashfs reports it as a missing source.
    """
    for ancestor in reversed((stage, *stage.parents)):
        info = ancestor.stat()
        if info.st_mode & 0o001:
            continue
        if info.st_uid == uid and info.st_mode & 0o100:
            continue
        if info.st_gid == gid and info.st_mode & 0o010:
            continue
        raise TransportHeld(f'{ancestor} is not traversable by the builder identity')


def build_image(stage: Path, image: Path, uid: int, gid: int, tool_fd: int, deadline: float,
                builder: tuple[int, int]) -> dict:
    """Deterministic build under enforced limits, run by the reserved builder identity.

    RLIMIT_FSIZE carries the image cap, so an oversized artifact cannot be written at all
    rather than being noticed afterwards. The builder drops to its own credentials before the
    exec, which is what lets RLIMIT_NPROC bound anything: the earlier version applied four
    limits to a process that was still root, where `-force-uid` relabelled the image contents
    and left the builder privileged.

    The build runs in a workspace the builder owns and root moves the finished image into the
    task root, so the task root is never widened to let the builder write into it.
    """
    if image.exists():
        raise TransportHeld('image already exists for this task')
    builder_uid, builder_gid = builder
    _stage_is_reachable(stage, builder_uid, builder_gid)
    with builder_lock(builder_uid):
        return _build_locked(stage, image, uid, gid, tool_fd, deadline, builder)


def _build_locked(stage: Path, image: Path, uid: int, gid: int, tool_fd: int, deadline: float,
                  builder: tuple[int, int]) -> dict:
    """The build itself, with the reserved identity's lock already held."""
    builder_uid, builder_gid = builder
    workspace = _builder_workspace(image, builder_uid, builder_gid)
    target = workspace / image.name
    command = [f'/proc/self/fd/{tool_fd}', str(stage), str(target), *MKSQUASHFS_OPTIONS,
               '-force-uid', str(uid), '-force-gid', str(gid)]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransportHeld('preparation deadline passed before the build started')
    started = time.monotonic()
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=remaining, preexec_fn=_builder_limits(builder_uid, builder_gid),
            pass_fds=(tool_fd,), cwd=str(workspace),
            env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8'))
        elapsed = time.monotonic() - started
        if result.returncode != 0:
            raise TransportHeld(f'image build failed ({result.returncode}): '
                                f'{result.stdout.decode()[-2000:]}')
        # Ownership is taken back before the move, so the artifact is never root-owned while
        # it still sits in a directory the builder can write.
        os.chown(target, 0, 0)
        os.chmod(target, 0o400)
        os.replace(target, image)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
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
                deadline: float | None = None, rehash: bool = True) -> dict:
    """Re-verify the whole chain: backing bytes, loop association and mount identity.

    `rehash` is the expensive half. The three fences around the launch take it, because that
    is where a substituted image would have to be caught. The fence that runs on an interval
    for the worker's whole life leaves it out: re-reading 173 MB every second would be the
    workload rather than a check on it, and the association and mount identity it does verify
    are what a swap has to move.
    """
    status = _verify_backing(descriptor, image_fd, loop_fd,
                             deadline=deadline if deadline is not None else time.monotonic() + 120,
                             rehash=rehash)
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


def _read_streams(descriptors: dict[str, int], deadline: float,
                  fence: Callable[[], object] | None = None,
                  fence_interval: float = FENCE_INTERVAL_SECONDS) -> dict[str, tuple[bytes, bool]]:
    """Poll every stream together, each bounded by the contract's 1 MiB, fencing as it goes.

    Draining one stream to EOF before touching the next deadlocks as soon as the worker fills
    another pipe: it blocks writing, so it never closes the one being read. The bound is per
    stream and overflow stops that stream rather than the whole read, so the others still
    report what they saw.

    `fence` is the existing mount/backing check, run on an interval for as long as the worker
    lives. Three checks around the launch describe three moments; the execution between them
    is where a substitution would land, and this is the loop that is already awake to notice.
    A fence that refuses stops the read and propagates, so the caller's cleanup path decides
    what to release.
    """
    poller = select.poll()
    next_fence = time.monotonic()
    payloads = {name: bytearray() for name in descriptors}
    truncated = {name: False for name in descriptors}
    by_descriptor = {descriptor: name for name, descriptor in descriptors.items()}
    live = set(descriptors)
    for descriptor in descriptors.values():
        poller.register(descriptor, select.POLLIN)
    while live:
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            for name in live:
                truncated[name] = True
            break
        if fence is not None and now >= next_fence:
            fence()
            next_fence = now + fence_interval
        wait = min(remaining, fence_interval if fence is not None else 1.0)
        for descriptor, _ in poller.poll(max(wait, 0.0) * 1000):
            name = by_descriptor[descriptor]
            block = os.read(descriptor, 1 << 16)
            if not block:
                poller.unregister(descriptor)
                live.discard(name)
                continue
            payloads[name] += block
            if len(payloads[name]) > MAX_WORKER_OUTPUT_BYTES:
                del payloads[name][MAX_WORKER_OUTPUT_BYTES:]
                truncated[name] = True
                poller.unregister(descriptor)
                live.discard(name)
    return {name: (bytes(payloads[name]), truncated[name]) for name in descriptors}


def _exec_reporting_failure(descriptor: int, argv: list[str], environment: dict) -> None:
    """Exec, and say so through a channel that lives exactly until the exec succeeds.

    The report pipe is closed before the exec, because exec leaves no afterwards in which to
    write -- which also means an exec failure has no way home through it. This channel is
    marked close-on-exec: a successful exec closes it and the controller reads EOF, and a
    failure writes the reason before the child exits. It never returns.
    """
    os.set_inheritable(descriptor, False)
    try:
        os.execve(argv[0], argv, environment)
    except BaseException as error:                                   # noqa: BLE001
        message = f'{type(error).__name__}: {error}'.encode()[:MAX_WORKER_OUTPUT_BYTES]
        try:
            os.write(descriptor, message)
            os.close(descriptor)
        except OSError:
            pass
        os._exit(127)
    os._exit(127)


def _official_deadline(bound: float, expires_at: str) -> float:
    """The run's deadline is the admitting authority's, not a fresh allowance.

    `bound` is the admitted workflow maximum measured from this invocation's start, so a
    build that takes four minutes spends four of them. The authority's expiry can only
    shorten that: a run must not outlive the approval that admitted it, and a retry must not
    restart the clock.
    """
    from datetime import datetime                                    # noqa: PLC0415
    if not isinstance(expires_at, str) or not expires_at.endswith('Z'):
        raise TransportHeld('admitting authority expiry must be canonical UTC')
    try:
        moment = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
    except ValueError as error:
        raise TransportHeld('admitting authority expiry is not a timestamp') from error
    remaining = moment.timestamp() - time.time()
    if remaining <= 0:
        raise TransportHeld('the admitting authority has expired; no run deadline is granted')
    return min(bound, time.monotonic() + remaining)


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
        snapshot = snapshots / f'{index:02d}-{Path(record["target"]).name}'
        try:
            # Copied out of the sealed descriptor, never re-read from the host path. The
            # previous version sealed the library, closed the seal, read the host file a
            # second time and compared the two digests. That detects a writer who moved in
            # between, but it does not exclude one -- and it threw away the sealed copy that
            # would have. Writing from the seal makes the bytes in the snapshot the bytes
            # that were hashed, by construction rather than by comparison.
            payload, offset = bytearray(), 0
            while chunk := os.pread(fd, 1 << 20, offset):
                payload += chunk
                offset += len(chunk)
        finally:
            os.close(fd)
        snapshot.write_bytes(bytes(payload))
        os.chown(snapshot, 0, 0)
        os.chmod(snapshot, 0o555)
        info = snapshot.lstat()
        # A readback, not a race check: it says the write landed intact. There is no longer a
        # second read of the host file for a writer to get between.
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != record['sha256']:
            raise TransportHeld(f'library snapshot for {record["target"]} did not land intact')
        record['source'] = str(snapshot)
        record['source_dev'], record['source_ino'] = info.st_dev, info.st_ino
        libraries.append(record)
    if sum(library['size_bytes'] for library in libraries) > MAX_EXTERNAL_LIBRARY_TOTAL_BYTES:
        raise TransportHeld('external libraries exceed the accepted aggregate bound')
    return libraries


def _prepare_image(request: dict, root: Path, deadline: float) -> dict:
    """Build the image, then publish its descriptor durably before anything consumes it."""
    stage = Path(request['stage'])
    uid, gid = request['worker_uid'], request['worker_gid']
    # Resolved before anything is touched. `normalise_stage_modes` rewrites every mode in the
    # stage, so discovering an unreserved builder after it would mean refusing the operation
    # with the stage already modified.
    builder = builder_identity((uid, gid))
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
    # The sealed copy is handed to the builder rather than widened. Measured: a dropped
    # builder cannot exec a root-owned 0500 memfd through /proc/self/fd at all, and making it
    # world-executable would hand every local uid the same descriptor. The seal already fixes
    # the bytes, so ownership is the only thing left to decide, and it belongs to the one
    # identity that has to run it.
    tool_fd, toolchain = seal_verified_tool(MKSQUASHFS, request['mksquashfs_sha256'],
                                            owner=builder)
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
        built = build_image(stage, root / 'runtime.squashfs', uid, gid, tool_fd,
                            deadline, builder)
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
    return dict(summary=summary, descriptor=descriptor, inventory=inventory)


def _durable_terminal(root: Path, request: dict, outcome: str, detail: str | None) -> Path:
    """Record what this invocation concluded, durably, before the task lock is released.

    B4 asks for a terminal record that survives the process. Without one, a reader arriving
    after a crash sees artifacts and no verdict, and cannot tell a refusal from an invocation
    that never finished. A superseded record is kept beside the new one rather than
    overwritten: a failure that a later retry replaced is still what happened.
    """
    path = root / 'terminal.json'
    if path.exists():
        previous = path.read_bytes()
        kept = path.with_name(f'{path.name}.{hashlib.sha256(previous).hexdigest()[:16]}')
        if not kept.exists():
            _durable_write(kept, previous, mode=0o400)
    pid = os.getpid()
    _durable_write(path, canonical_json_bytes(dict(
        schema_version='p3-local-transport-terminal-v1', task=request.get('job_id'),
        operation=request.get('operation'), outcome=outcome, detail=detail,
        owner_pid=pid, owner_start_time=_process_start_time(pid),
        boot_id=_host_identity()[1], recorded_at_ns=time.time_ns())) + b'\n', mode=0o400)
    return path


def command_prepare(request: dict, root: Path, deadline: float) -> dict:
    """Build the image and stop. The session ends with this process.

    Building without running is admitted because a build is worth inspecting on its own, but
    what it leaves behind is not a session anyone can pick up: the next `probe` builds its own
    image under its own lock. An image left here is a recovery subject for `inspect` and
    `cleanup`, which is the only thing B3.1 lets a later invocation do with it.
    """
    return _prepare_image(request, root, deadline)['summary']


def command_probe(request: dict, root: Path, deadline: float) -> dict:
    """Build, mount, launch, reap and release in one lock hold.

    B3.1: "No unlocked gap between prepare and run." The two used to be separate invocations,
    so the lock was released in between and the image was picked up by a second controller
    reading the descriptor off disk. That is the handoff the contract says v2 does not
    implement, and this closes it by never releasing the lock in the first place.
    """
    prepared = _prepare_image(request, root, deadline)
    session = run_session(request, root, deadline,
                          prepared['descriptor'], prepared['inventory'])
    session['prepared'] = prepared['summary']
    return session


def _official_run_directory(request: dict) -> Path:
    """The protected run directory this identity already owns.

    B3.1 says root paths are derived from the protected run identity, never taken from a
    caller. `local_run_directory` is the owner of that derivation and is reused rather than
    re-spelled here, so the controller and the worker cannot disagree about where the session
    lives.
    """
    from packages.alpha_lifecycle.authority import local_run_directory   # noqa: PLC0415
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity  # noqa: PLC0415
    identity = request['identity']
    if identity.get('issuer_kind') != 'LOCAL':
        raise TransportHeld('run is a LOCAL lane operation')
    # Parsed from JSON, not from the dict: the identity model is strict, so a run_id that
    # arrived as the string every protected request actually carries is not a UUID instance
    # and `model_validate` rejects it.
    return local_run_directory(
        LocalRunIdentity.model_validate_json(canonical_json_bytes(identity)))


def _admitted_official_operation(request: dict) -> str:
    """The operation the worker will accept, checked before an image is built.

    `run_official_once` refuses anything outside its implemented set, and it refuses it after
    the controller would have built a 170 MB image and mounted it. The same closed set is
    checked here so the refusal costs nothing.
    """
    from packages.alpha_lifecycle.authority import WORKFLOW_OPERATIONS   # noqa: PLC0415
    official = request.get('official')
    if not isinstance(official, dict):
        raise TransportHeld('run requires the closed official launch fields')
    operation = official.get('operation')
    if WORKFLOW_OPERATIONS.get(operation) not in {'BASELINES', 'REGISTER_FAMILY', 'OOS', 'HOLDOUT'}:
        raise TransportHeld(f'{operation} is not an admitted official operation for run')
    return operation


def _runtime_authority(request: dict) -> dict:
    """The protected LOCAL runtime profile for this issuer, read fresh each time it is used."""
    from packages.runtime_release.config import (                        # noqa: PLC0415
        ProtectedAuthorityError, read_protected_canonical_json_current)
    path = STORE_ROOT / request['identity']['issuer_identity'] / 'runtime.json'
    try:
        document, _ = read_protected_canonical_json_current(path)
    except (FileNotFoundError, OSError, ProtectedAuthorityError) as error:
        raise TransportHeld(
            f'run requires the protected LOCAL runtime profile at {path}; it is installed by '
            'the owner protocol, not by this controller') from error
    if document.get('revoked') is not False:
        raise TransportHeld('the LOCAL runtime profile is revoked')
    return document


def _worker_environment(request: dict, mount: Path, release_root: str, private: Path) -> dict:
    """The closed environment the existing worker entrypoint reads. Nothing is inherited.

    Every authority-bearing value comes from protected owner material: the LOCAL runtime
    profile supplies the qualified control-plane stage and the Package6 approval digest, and
    the protected host profile supplies the credential directory and artifact root that
    `p3_official.py` rechecks against the profile it reads itself. The controller routes them;
    it validates none of them, because the worker owns those decisions.

    The two staging paths are the exception: no profile carries them, so they arrive in the
    closed `official` field the request is required to carry for `run`. That field admits an
    operation and two absolute paths and nothing else -- no shell text, no argv.
    """
    from packages.runtime_release.config import (                        # noqa: PLC0415
        ProtectedAuthorityError, read_protected_canonical_json_current)
    from packages.runtime_release.staging_v2 import (                    # noqa: PLC0415
        PACKAGE6_APPROVAL_SHA256_ENV, STAGING_ACTIVATION_PATH_ENV, STAGING_AUTHORITY_PATH_ENV,
        STAGING_SCOPE, STAGING_SCOPE_ENV)
    operation = _admitted_official_operation(request)
    official = request['official']
    directory = _official_run_directory(request)

    def protected(path: Path, what: str) -> dict:
        try:
            document, _ = read_protected_canonical_json_current(path)
        except (FileNotFoundError, OSError, ProtectedAuthorityError) as error:
            raise TransportHeld(
                f'run requires the {what} at {path}; it is installed by the owner protocol, '
                'not by this controller') from error
        return document

    host = protected(directory / 'profile.json', 'protected host profile')
    runtime = _runtime_authority(request)

    def absolute(source: dict, field: str, what: str) -> str:
        value = source.get(field)
        if not isinstance(value, str) or not value.startswith('/'):
            raise TransportHeld(f'{what} does not name an absolute {field}')
        return value

    # The interpreter and working directory are the qualified control-plane stage's, never the
    # research image's. The image carries the closure the sandbox mounts; it is not a Git
    # checkout, and the worker derives its own source identity from where its module lives.
    application_root = absolute(runtime, 'application_root', 'runtime profile')
    application_python = absolute(runtime, 'application_python', 'runtime profile')
    return dict(
        PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8', TZ='UTC',
        HOME=str(private), PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1',
        P3_OPERATION=operation, P3_ISSUER_KIND='LOCAL',
        P3_AUTHORITY_REQUEST_FILE=str(directory / 'authority-request.json'),
        P3_PREFLIGHT_DIRECTORY=str(directory / 'preflight'),
        P3_ARTIFACT_ROOT=absolute(host, 'store_root', 'host profile'),
        P3_MANIFEST_FILE=str(directory / 'manifest.json'),
        P3_REVIEW_FILE=str(directory / 'review.json'),
        CREDENTIALS_DIRECTORY=absolute(host, 'worker_credentials_directory', 'host profile'),
        **{STAGING_SCOPE_ENV: STAGING_SCOPE,
           STAGING_AUTHORITY_PATH_ENV: absolute(official, 'staging_authority_path', 'run request'),
           STAGING_ACTIVATION_PATH_ENV: absolute(official, 'staging_activation_path', 'run request'),
           PACKAGE6_APPROVAL_SHA256_ENV: str(runtime['package6_approval_sha256'])},
        P3_APPLICATION_ROOT=application_root, P3_APPLICATION_PYTHON=application_python)


def command_run(request: dict, root: Path, deadline: float) -> dict:
    """Build, mount, then become the worker entrypoint that already exists.

    Plan section 3.4 step 7: "start the existing worker entrypoint in that namespace, using
    the qualified control-plane stage". The official runbook is blunter -- "no second launcher
    or execution engine is needed" -- and the phase workflow repeats it as a rule. So this
    mounts the image, drops to the worker, and execs `python -m services.job_worker`.

    What it deliberately does not do is assemble the worker's authority. The campaign claim,
    the lease, the protected profile and the job store are the worker's to obtain; a
    controller that produced them would be manufacturing research authority rather than
    exercising it.
    """
    # Resolved before anything is built. `_prepare_image` writes a durable claim and a
    # 170 MB artifact; discovering an absent protected profile afterwards would leave the task
    # claimed for a session that never started, and the next run would refuse it. This is the
    # same ordering the builder identity needed.
    mount, private = root / 'mnt', root / 'worker'
    environment = _worker_environment(request, mount, request['release_root'], private)
    # Re-read at admission rather than carried from the environment build: the contract wants
    # the authority revalidated at the moment it is used, not at the moment it was found.
    granted = _official_deadline(deadline, _runtime_authority(request).get('expires_at'))
    # The qualified control-plane stage runs the worker; the research image stays mounted in
    # this namespace for the profile and the spawn provider to read. Running the worker out of
    # the image would point its source identity at a tree with no Git metadata and bypass the
    # Package6-qualified stage the runtime validator requires.
    argv = [environment['P3_APPLICATION_PYTHON'], '-I', '-B', '-m', 'services.job_worker']
    prepared = _prepare_image(request, root, granted)
    session = run_session(request, root, granted, prepared['descriptor'], prepared['inventory'],
                          launch=_worker_launch, environment=environment, argv=argv)
    session['prepared'] = prepared['summary']
    return session


def _worker_verdict(observed: dict, reaped: dict, truncated: bool,
                    expected_challenge: object) -> int:
    """The transport's verdict on the worker. A clean exit is not an economic result.

    `run_session` recorded the wait status and then never looked at it, so a worker that
    exited non-zero while answering the challenge correctly still produced `status: OK`. The
    recorded value is also a raw wait status rather than an exit code -- exit 1 is 256 and a
    signal hides in the low bits -- so it is decoded through the standard API instead of being
    compared against zero.

    What this establishes is that the transport delivered the closure and the worker ran to a
    normal end. What the worker concluded is a separate question answered by the terminal Job
    API result, never by this exit code.
    """
    # The child reports an exec or setup failure through its own channel. Blaming the exit
    # code first would hide the cause behind a number.
    reported = observed.get('error')
    if reported:
        raise TransportHeld(f'the worker reported: {reported}')
    if truncated:
        raise TransportHeld('worker output exceeded its bound before the deadline')
    if not reaped.get('reaped'):
        raise TransportHeld('worker did not reap within the deadline')
    if reaped.get('escalation'):
        raise TransportHeld('worker required escalation: ' + ', '.join(reaped['escalation']))
    code = os.waitstatus_to_exitcode(reaped['status'])
    if code < 0:
        raise TransportHeld(f'worker was killed by signal {-code}')
    if code != 0:
        raise TransportHeld(f'worker exited {code}')
    if observed.get('challenge') != expected_challenge:
        raise TransportHeld('mount readback challenge did not match this generation')
    return code


def _probe_launch(context: dict) -> dict:
    """Run the unmodified startup probe and report what it observed."""
    from services.job_worker.p3_startup import probe_p3_startup      # noqa: PLC0415
    mount, descriptor, request = context['mount'], context['descriptor'], context['request']
    # The diagnostic used to run bubblewrap directly from /usr/bin/bwrap. That was an
    # execution straight from a path, with no seal and no digest check at the moment it ran,
    # inside the one operation whose whole point is that nothing executes unverified.
    interpreter = mount / descriptor['python_root'] / 'bin/python3.11'
    diagnostics = dict(interpreter_mode=oct(os.stat(interpreter).st_mode & 0o7777),
                       can_execute=os.access(interpreter, os.X_OK))
    closure = build_v2_closure(mount, descriptor, descriptor['external_libraries'],
        context['inventory'], request['source'], request['environment_ref'],
        request['job_id'], request['bwrap_sha256'])
    return dict(startup=probe_p3_startup(closure, private_root=context['private']),
                diagnostics=diagnostics)


def _worker_launch(context: dict) -> dict:
    """Report the readback, then become the existing worker entrypoint. Does not return.

    Plan section 3.4 step 7 says the controller starts the worker entrypoint that already
    exists, and the official runbook says it plainly: "no second launcher or execution engine
    is needed". So this execs `python -m services.job_worker` inside the mount and stops being
    the controller's business. Everything the worker needs authority for -- claiming a job,
    its lease, the protected profile, the job store -- the worker obtains itself. A controller
    that assembled a claim here would be manufacturing research authority, not exercising it.

    The readback is written and the pipe closed before the exec, because exec replaces this
    process: there is no "afterwards" in which to report.
    """
    write = context['write']
    os.write(write, canonical_json_bytes(dict(
        challenge=context['challenge'], launch='WORKER_ENTRYPOINT',
        argv=context['argv'], open_fds=sorted(os.listdir('/proc/self/fd')))))
    os.close(write)
    _exec_reporting_failure(context['exec_channel'], context['argv'], context['environment'])
    raise TransportHeld('worker entrypoint did not replace the process')   # unreachable


def run_session(request: dict, root: Path, deadline: float, descriptor: dict, inventory: dict,
                launch=_probe_launch, environment: dict | None = None,
                argv: list[str] | None = None) -> dict:
    """Mount the image this session just built and run the startup probe as the worker.

    This is the acceptance the whole transport exists for: the complete closure reaching an
    actual bubblewrap launch instead of being refused at the parser's token capacity.

    The descriptor and inventory are handed in by the prepare that ran moments ago in this
    same process, under this same lock. They used to be read back off disk, which is what made
    a probe look like something you could run against an image some earlier, now-dead
    controller had left behind. B3.1 says a prepared session cannot outlive its owner and that
    v2 implements no handoff, so there is no such thing to read back.
    """
    # Load the launcher and every model the closure needs while still privileged. The
    # worker cannot traverse the source tree after dropping to its own uid, and importing
    # application code before the drop is also the order the contract requires.
    import services.job_worker.p3_startup                      # noqa: F401
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
            # The worker's own streams, and a channel that survives exactly until its exec
            # succeeds. Captured rather than inherited: the contract bounds logs at 1 MiB and
            # an inherited terminal is neither bounded nor attributable.
            out_read, out_write = os.pipe()
            err_read, err_write = os.pipe()
            exec_read, exec_write = os.pipe()
            child = os.fork()
            if child == 0:
                status = 0
                try:
                    for closing in (read, out_read, err_read, exec_read, loop_fd, image_fd):
                        os.close(closing)
                    os.dup2(out_write, 1)
                    os.dup2(err_write, 2)
                    os.close(out_write)
                    os.close(err_write)
                    drop_to_worker(descriptor['worker_uid'], descriptor['worker_gid'], controller)
                    # The challenge is computed before the launch because a launch that execs
                    # has no afterwards in which to answer.
                    context = dict(mount=mount, descriptor=descriptor, inventory=inventory,
                                   request=request, private=private, nonce=nonce, write=write,
                                   challenge=mount_challenge(mount, inventory, nonce),
                                   environment=environment, argv=argv, exec_channel=exec_write)
                    observed = launch(context)
                    os.write(write, canonical_json_bytes(dict(
                        observed, challenge=context['challenge'],
                        open_fds=sorted(os.listdir('/proc/self/fd')))))
                except BaseException as error:
                    os.write(write, canonical_json_bytes(dict(
                        error=f'{type(error).__name__}: {error}',
                        diagnostics=locals().get('diagnostics'))))
                    status = 1
                finally:
                    os.close(write)
                os._exit(status)
            for closing in (write, out_write, err_write, exec_write):
                os.close(closing)
            streams = _read_streams(
                dict(report=read, stdout=out_read, stderr=err_read, exec=exec_read), deadline,
                fence=lambda: mount_fence(descriptor, image_fd, loop_fd, mount, rehash=False))
            for closing in (read, out_read, err_read, exec_read):
                os.close(closing)
            payload, truncated = streams['report']
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
            # An exec that never happened is the cause; the exit code is only its shadow.
            exec_failure = streams['exec'][0].decode(errors='replace').strip()
            if exec_failure:
                observed.setdefault('error', f'worker exec failed: {exec_failure}')
            observed['streams'] = {name: dict(bytes=len(value), truncated=flag)
                                   for name, (value, flag) in streams.items()}
            observed['worker_stderr'] = streams['stderr'][0].decode(errors='replace')[-4096:]
            # Fenced before the verdict, so a refusal still records what the mount looked like
            # when the worker stopped rather than discarding that evidence.
            fences.append(dict(point='after-worker', nonce=nonce,
                               **mount_fence(descriptor, image_fd, loop_fd, mount)))
            observed['worker_exit_code'] = _worker_verdict(
                observed, reaped, truncated, expected_challenge)
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
        root = open_task_root(digest)
        # Measured from the invocation, so a build that takes four minutes spends four of
        # them. `run` carries the admitted workflow maximum; everything else carries the
        # prepare/probe total the contract sets.
        started = time.monotonic()
        deadline = started + (WORKFLOW_DEADLINE_SECONDS
                              if request['operation'] == 'run' else PREPARE_WALL_SECONDS)
        with TaskLock(root):
            operation = request['operation']
            # Before the claim check, so a refusal to take a task someone else owns does not
            # write a verdict into their task root.
            require_task_claim(root, operation)
            try:
                if operation == 'prepare':
                    result = command_prepare(request, root, deadline)
                elif operation == 'probe':
                    result = command_probe(request, root, deadline)
                elif operation == 'run':
                    result = command_run(request, root, deadline)
                elif operation == 'inspect':
                    result = command_inspect(root)
                elif operation == 'cleanup':
                    result = command_cleanup(root)
                else:
                    raise TransportHeld(f'operation {operation} is not in the accepted set')
            except BaseException as error:
                # Durable before the lock goes. A reader arriving after this process cannot
                # otherwise tell a refusal from an invocation that never finished.
                _durable_terminal(root, request, 'HELD', f'{type(error).__name__}: {error}')
                raise
            _durable_terminal(root, request, 'OK', None)
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
