"""Bounded P3 startup probes; importing this module performs no host action."""
import os
import hashlib
from collections.abc import Iterator
from contextlib import ExitStack,contextmanager
from pathlib import Path,PurePosixPath
import re
import resource
import selectors
import fcntl
import signal
import subprocess
import time
import threading
import uuid

from packages.alpha_lifecycle.sandbox_policy import (
    BWRAP_VERSION,BWRAP_CAPABILITIES,SANDBOX_ARGS,FILESYSTEM_ARGS,ROOT_READONLY_ARGS,
    ENVIRONMENT_ARGS,MAX_CLOSURE_FILES,MAX_CLOSURE_BYTES,MAX_ARGV_BYTES,
    BWRAP_PARSER_TOKEN_CAPACITY,SANDBOX_PROFILE_SHA256,SANDBOX_PROFILE_V2_SHA256,socket_filter_bytes,
)
from services.job_store.records import validate_p3_job_id
from services.operator_control.protected_fs import ProtectedDirectory,open_private_directory,read_private_file
from .engine_spawn import _sealed_memfd
from .p3_spawn import (CompleteP3Closure,P3ClosureMount,P3Sandbox,_RELEASE,_PYTHON,
    _file_bytes,_digest,_image_root_args)
from .p3_output import _identity


_PROGRAM='''
import ast,errno,os,socket,sys
# Assert on what each descriptor refers to, not on its number. Numbers are reused: the
# sealed-file transport happened to allocate high ones, but a transport that seals only a
# handful leaves low numbers free for the sandboxed interpreter to reopen for itself, and a
# numeric test then reports a leak that did not happen.
# A substring denylist only names the leaks somebody thought of. Measured outside the
# sandbox, matching 'memfd:p3-' or '/p3-transport/' let a differently named memfd, a mount
# namespace handle, a directory reference and a unix socket through untouched -- four of
# five inherited descriptors. The sandboxed process is supposed to hold nothing beyond its
# standard streams, so the set it holds is enumerated and anything outside that is a
# finding, whatever it is called.
def held():
 found=[]
 for entry in os.listdir('/proc/self/fd'):
  try: link=os.readlink('/proc/self/fd/'+entry)
  except OSError: continue
  found.append((int(entry),link))
 return sorted(found)
def findings(permitted):
 return ['%d:%s'%(n,t) for n,t in held() if n not in permitted]
print('P3_STARTUP_FD_INVENTORY '+repr(held()))
carried=findings({0,1,2})
if carried: raise AssertionError('descriptor survived exec: '+','.join(carried))
# The boundary has to hold for what the driver spawns as well, not only for the process
# bwrap exec'd. A descriptor that crossed into a nested child would be just as reachable by
# research code, and nothing above this point would have noticed.
nested_read,nested_write=os.pipe()
nested=os.fork()
if nested==0:
 os.close(nested_read)
 os.write(nested_write,repr((held(),findings({0,1,2,nested_write}))).encode())
 os.close(nested_write)
 os._exit(0)
os.close(nested_write)
inherited=os.read(nested_read,65536).decode()
os.close(nested_read)
if os.waitpid(nested,0)[1]!=0: raise AssertionError('nested descriptor probe did not complete')
print('P3_STARTUP_NESTED_FD_INVENTORY '+inherited)
if not inherited.endswith(', [])'): raise AssertionError('descriptor reached a nested child: '+inherited)
# A forked child inherits every descriptor; one that execs keeps only those without
# close-on-exec. Research code can create either, and only the forked case was measured, so
# a descriptor that survives exec would have gone unseen here.
native_read,native_write=os.pipe()
os.set_inheritable(native_write,True)
reader=['import os','fd=%d'%native_write,'found=[]',
 'for entry in os.listdir("/proc/self/fd"):',
 ' try: link=os.readlink("/proc/self/fd/"+entry)',
 ' except OSError: continue',
 ' found.append((int(entry),link))',
 'os.write(fd,repr(sorted(found)).encode())']
native=os.fork()
if native==0:
 os.close(native_read)
 # argv[0] has to be the full path: CPython locates its prefix from it, and a bare name
 # makes it warn about <exec_prefix> on stderr, which this probe treats as a failure.
 os.execv('/p3/python/bin/python3.11',['/p3/python/bin/python3.11','-I','-B','-c',chr(10).join(reader)])
 os._exit(127)
os.close(native_write)
raw=os.read(native_read,65536).decode()
os.close(native_read)
if os.waitpid(native,0)[1]!=0: raise AssertionError('native descriptor probe did not complete')
if not raw: raise AssertionError('native descriptor probe produced nothing')
carried_native=ast.literal_eval(raw)
extra=['%d:%s'%(n,t) for n,t in carried_native if n not in (0,1,2,native_write)]
print('P3_STARTUP_NATIVE_FD_INVENTORY '+repr((carried_native,extra)))
if extra: raise AssertionError('descriptor reached a native child: '+','.join(extra))
for create in (socket.socket,socket.socketpair):
 try: create()
 except OSError as error: assert error.errno==errno.EPERM
 else: raise AssertionError('socket creation was permitted')
for path in ('/p3/injected','/p3/store/forbidden'):
 try: os.mkdir(path)
 except OSError as error: assert error.errno==errno.EROFS
 else: raise AssertionError('read-only namespace was writable')
descriptor=os.open('/p3/output/probe-output',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(descriptor,'wb') as stream: stream.write(b'allowed')
print('P3_STARTUP_PROBE_PASS')
'''


def _emitted_directories(targets: tuple[PurePosixPath, ...], imaged: bool) -> set[PurePosixPath]:
    """The directories this launch actually emits a --dir for.

    One definition, used both by the capacity estimate and by the builder. They were separate,
    and the estimate counted ancestors the builder skips on the image transport, so the two
    disagreed about the same command.
    """
    directories={PurePosixPath('/p3'),PurePosixPath('/p3/store'),PurePosixPath('/p3/output')}
    for target in targets:
        if imaged and (target.is_relative_to(_RELEASE) or target.is_relative_to(_PYTHON)):
            continue      # the mount supplies this tree, and a --dir would shadow it
        directories.update(p for p in target.parents if p!=PurePosixPath('/'))
    return directories


_FD_INVENTORY='P3_STARTUP_FD_INVENTORY '
_NESTED_FD_INVENTORY='P3_STARTUP_NESTED_FD_INVENTORY '
_NATIVE_FD_INVENTORY='P3_STARTUP_NATIVE_FD_INVENTORY '


def _close_on_exec(descriptors: tuple[int, ...]) -> None:
    """Mark descriptors close-on-exec in the child, after fork and before exec.

    The sandbox binary is executed as /proc/self/fd/N so that the bytes that run are the
    bytes that were verified. subprocess must therefore inherit that descriptor, which
    clears close-on-exec, and bwrap does not close it, so it reaches the sandboxed process.
    execve resolves and loads the image before closing close-on-exec descriptors, so
    restoring the flag here keeps verified-descriptor execution and still closes the tool.
    """
    for descriptor in descriptors:
        fcntl.fcntl(descriptor, fcntl.F_SETFD,
                    fcntl.fcntl(descriptor, fcntl.F_GETFD) | fcntl.FD_CLOEXEC)


def _probe_limits(file_size: int = 65536) -> None:
    """File size must cover the largest sealed payload bwrap materialises.

    --ro-bind-data writes each sealed file's content through the sandbox, so a fixed 64 KiB
    budget only ever suited a synthetic closure: one real shared library is larger than that
    and the launch fails with 'Can't write data to file ... File too large'.
    """
    for kind,value in ((resource.RLIMIT_CPU,5),(resource.RLIMIT_AS,2147483648),
                       (resource.RLIMIT_FSIZE,max(65536,file_size))):
        resource.setrlimit(kind,(value,value))


def _run_probe(argv: tuple[str, ...], passed: tuple[int, ...],
    file_size: int = 65536, cloexec: tuple[int, ...] = ()) -> tuple[int | None, bytes, bytes]:
    """After Popen returns, allow 5s execution and 2s reaping; 64 KiB per stream.

    An enclosing host startup deadline must also cover preexec/exec and I/O.
    """
    if threading.active_count()!=1 or signal.getsignal(signal.SIGCHLD)!=signal.SIG_DFL:
        raise ValueError('P3 startup probe requires exclusive child reaping')
    process=subprocess.Popen(argv,cwd='/',env={},stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,pass_fds=passed,
        start_new_session=True,
        preexec_fn=lambda:(_probe_limits(file_size),_close_on_exec(cloexec)))
    captured={'stdout':bytearray(),'stderr':bytearray()}
    deadline=time.monotonic()+5
    try:
        with selectors.DefaultSelector() as selector:
            for name in captured:
                stream=getattr(process,name)
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_READ,name)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise ValueError('P3 startup probe timed out')
                for key,_ in selector.select(remaining):
                    chunk=os.read(key.fd,65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        captured[key.data].extend(chunk)
                        if len(captured[key.data])>65536:
                            raise ValueError('P3 startup probe output exceeded its bound')
        # Observe exit without reaping: the owned PID cannot be reused before
        # the process-group cleanup below. EOF alone does not establish exit.
        while os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:
            if time.monotonic()>=deadline:
                raise ValueError('P3 startup probe timed out')
            time.sleep(.01)
    finally:
        try:
            os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        finally:
            for stream in (process.stdout,process.stderr):
                if stream is not None:
                    stream.close()
    return process.returncode,bytes(captured['stdout']),bytes(captured['stderr'])


@contextmanager
def _probe_directories(private_root: Path) -> Iterator[list[ProtectedDirectory]]:
    with ExitStack() as stack:
        parent=stack.enter_context(open_private_directory(private_root))
        name='.p3-startup-'+uuid.uuid4().hex
        os.mkdir(name,mode=0o700,dir_fd=parent.descriptor)
        owned=stack.enter_context(open_private_directory(parent.path/name))
        children=[]
        try:
            for child_name in ('inputs','output'):
                owned.recheck()
                os.mkdir(child_name,mode=0o700,dir_fd=owned.descriptor)
                children.append(stack.enter_context(open_private_directory(owned.path/child_name)))
            parent.recheck()
            yield children
        finally:
            # A replaced path is evidence to preserve, never a cleanup target.
            for directory in (parent,owned,*children):
                directory.recheck()
            if set(os.listdir(owned.descriptor))!={child.path.name for child in children}:
                raise ValueError('P3 startup root contains an unexpected path')
            for child in children:
                names=os.listdir(child.descriptor)
                if set(names)-({'probe-output'} if child.path.name=='output' else set()):
                    raise ValueError('P3 startup directory contains an unexpected path')
                for entry in names:
                    os.unlink(entry,dir_fd=child.descriptor)
                child.recheck()
                os.rmdir(child.path.name,dir_fd=owned.descriptor)
                if os.fstat(child.descriptor).st_nlink!=0:
                    raise ValueError('P3 startup child cleanup differs')
            owned.recheck()
            os.rmdir(name,dir_fd=parent.descriptor)
            if os.fstat(owned.descriptor).st_nlink!=0 or name in os.listdir(parent.descriptor):
                raise ValueError('P3 startup owned-root cleanup differs')
            parent.recheck()


def _read_probe_output(output: ProtectedDirectory) -> None:
    output.recheck()
    before=_identity(os.stat('probe-output',dir_fd=output.descriptor,follow_symlinks=False))
    raw=read_private_file(output,'probe-output',max_bytes=7)
    after=_identity(os.stat('probe-output',dir_fd=output.descriptor,follow_symlinks=False))
    if raw!=b'allowed' or before!=after:
        raise ValueError('P3 startup output changed or differs')


def probe_p3_startup(closure: CompleteP3Closure, *, private_root: Path) -> dict[str, str | bool]:
    """Exercise the pinned files/kernel before job admission; never run research."""
    if (type(closure) is not CompleteP3Closure or not isinstance(closure.mounts,tuple)
        or not 1<=len(closure.mounts)<=MAX_CLOSURE_FILES
        or any(type(m) is not P3ClosureMount or type(m.size) is not int or not 0<=m.size<=268435456 for m in closure.mounts)
        or sum(m.size for m in closure.mounts)>MAX_CLOSURE_BYTES):
        raise ValueError('P3 startup requires a bounded complete closure')
    targets=tuple(m.target for m in closure.mounts)
    if targets!=tuple(sorted(set(targets))):
        raise ValueError('P3 startup inventory is not sorted and unique')
    # Capacity is a property of the pinned backend and is knowable from the inventory alone,
    # so it is the cheapest rejection available and runs before any digest, snapshot, probe
    # directory or memfd. The historical failure built thousands of descriptors first and
    # then surfaced bwrap's opaque token error.
    imaged=closure.image is not None
    # Only the directories this transport actually emits. Counting every target's ancestors
    # regardless of transport is what the image form exists to avoid: the mount supplies that
    # tree and no --dir is emitted for it, so a closure with thousands of image directories
    # was refused here before anything was built, on a budget it would never have spent.
    directory_estimate=len(_emitted_directories(targets,imaged))
    lower_bound=(6 if imaged else 5*len(targets))+2*directory_estimate+len(SANDBOX_ARGS)+len(FILESYSTEM_ARGS)
    if lower_bound>BWRAP_PARSER_TOKEN_CAPACITY:
        raise ValueError('P3 startup closure exceeds the pinned sandbox parser capacity')
    sandbox=closure.sandbox
    expected_policy=SANDBOX_PROFILE_V2_SHA256 if imaged else SANDBOX_PROFILE_SHA256
    if (type(sandbox) is not P3Sandbox or sandbox.profile_sha256!=expected_policy
        or sandbox.version!=BWRAP_VERSION or sandbox.capabilities!=BWRAP_CAPABILITIES
        or sandbox.mode not in {0o500,0o555,0o755}):
        raise ValueError('P3 startup sandbox policy differs')
    validate_p3_job_id(closure.bound_job_id)
    if re.fullmatch('[0-9a-f]{64}',closure.bound_payload_fingerprint) is None:
        raise ValueError('P3 startup payload binding differs')
    for mount in closure.mounts:
        if (not isinstance(mount.target,PurePosixPath) or not mount.target.is_absolute() or '..' in mount.target.parts
            or mount.mode not in {0o400,0o500,0o444,0o555}
            or not any(mount.target.is_relative_to(root) and mount.target!=PurePosixPath(root)
                for root in ('/p3/release','/p3/python','/lib','/lib64','/usr/lib'))):
            raise ValueError('P3 startup file mount role differs')
    fingerprint=_digest(dict(source=closure.source,environment_ref=closure.environment_ref,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in closure.mounts],
        sandbox_sha256=sandbox.executable_sha256,sandbox_policy_sha256=sandbox.profile_sha256))
    if fingerprint!=closure.closure_sha256:
        raise ValueError('P3 startup closure digest differs')
    # Count the descriptors this transport will actually open. On v1 that is one per closure
    # file; on v2 the image-backed files arrive through the mount and only the external
    # libraries are sealed, so charging the full inventory would refuse a launch that needs
    # a dozen descriptors.
    sealed=tuple(t for t in targets
                 if not (imaged and (t.is_relative_to(_RELEASE) or t.is_relative_to(_PYTHON))))
    soft,_=resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft!=resource.RLIM_INFINITY and len(os.listdir('/proc/self/fd'))+len(sealed)+20>=soft:
        raise ValueError('P3 startup descriptor bound is insufficient')
    descriptors=[]
    started=time.monotonic()
    with _probe_directories(private_root) as (inputs,output):
        try:
            raw=_file_bytes(sandbox.executable,sandbox.identity,sandbox.executable.stat(follow_symlinks=False).st_size,
                sandbox.mode,sandbox.executable_sha256)
            fd=_sealed_memfd('p3-startup-bwrap',raw,mode=0o500);descriptors.append(fd)
            fd_sandbox=fd
            executable=f'/proc/self/fd/{fd}'
            code,version,stderr=_run_probe((executable,'--version'),(fd,))
            if code or stderr or version!=BWRAP_VERSION.encode()+b'\n':
                raise ValueError('P3 startup sandbox version differs')
            code,help_text,stderr=_run_probe((executable,'--help'),(fd,))
            if code or stderr or any(cap.encode() not in help_text.split() for cap in BWRAP_CAPABILITIES):
                raise ValueError('P3 startup sandbox capabilities differ')
            argv=[executable,*SANDBOX_ARGS,*FILESYSTEM_ARGS]
            directories=_emitted_directories(targets,imaged)
            for directory in sorted(directories,key=lambda p:(len(p.parts),str(p))):
                argv.extend(('--dir',str(directory)))
            for mount in closure.mounts:
                raw=_file_bytes(mount.source,mount.identity,mount.size,mount.mode,mount.sha256)
                if imaged and (mount.target.is_relative_to(_RELEASE) or mount.target.is_relative_to(_PYTHON)):
                    continue
                fd=_sealed_memfd('p3-startup-file',raw,mode=mount.mode);descriptors.append(fd)
                argv.extend(('--perms',f'{mount.mode:o}','--ro-bind-data',str(fd),str(mount.target)))
                if time.monotonic()-started>=120:
                    raise ValueError('P3 startup preparation timed out')
            if imaged:
                argv.extend(_image_root_args(closure))
            input_fd=os.dup(inputs.descriptor);descriptors.append(input_fd)
            output_fd=os.dup(output.descriptor);descriptors.append(output_fd)
            filter_fd=_sealed_memfd('p3-startup-filter',socket_filter_bytes(),mode=0o400);descriptors.append(filter_fd)
            argv.extend(('--ro-bind-fd',str(input_fd),'/p3/store','--bind-fd',str(output_fd),'/p3/output',
                *ROOT_READONLY_ARGS,'--chdir','/p3',*ENVIRONMENT_ARGS,'--seccomp',str(filter_fd),
                '/p3/python/bin/python3.11','-I','-B','-c',_PROGRAM,*map(str,descriptors)))
            if sum(len(arg.encode())+1 for arg in argv)>MAX_ARGV_BYTES:
                raise ValueError('P3 startup argument bound exceeded')
            if len(argv)-1>BWRAP_PARSER_TOKEN_CAPACITY:
                raise ValueError('P3 startup argument count exceeds the pinned sandbox parser capacity')
            sealed_bytes=max((m.size for m in closure.mounts
                if not (imaged and (m.target.is_relative_to(_RELEASE)
                                    or m.target.is_relative_to(_PYTHON)))),default=0)
            code,stdout,stderr=_run_probe(tuple(argv),tuple(descriptors),sealed_bytes+65536,(fd_sandbox,))
            # Four lines, in this order: the descriptor set the sandboxed process holds, the
            # set its forked child inherits, the set a child that execs keeps, each with its
            # findings against the allowlist, and the pass marker. Matching one fixed byte
            # string was enough while the program printed only the marker; it is not a reason
            # to stop recording what the boundary actually held, so the shape is checked and
            # the inventories are kept.
            lines=stdout.decode('utf-8','replace').splitlines()
            shaped=(len(lines)==4 and lines[0].startswith(_FD_INVENTORY)
                and lines[1].startswith(_NESTED_FD_INVENTORY)
                and lines[2].startswith(_NATIVE_FD_INVENTORY)
                and lines[3]=='P3_STARTUP_PROBE_PASS')
            if (code or stderr or not shaped
                or os.listdir(inputs.descriptor) or os.listdir(output.descriptor)!=['probe-output']):
                # Report what the probe actually produced. The historical message named no
                # cause, so a transport failure and a kernel failure looked identical.
                raise ValueError('P3 startup kernel/transport probe failed: '
                    f'exit={code} stdout={stdout[:400]!r} stderr={stderr[:400]!r} '
                    f'argv_tokens={len(argv)-1}')
            held,nested=lines[0][len(_FD_INVENTORY):],lines[1][len(_NESTED_FD_INVENTORY):]
            native=lines[2][len(_NATIVE_FD_INVENTORY):]
            _read_probe_output(output)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    return dict(schema_version='p3-startup-probe-v1',closure_sha256=closure.closure_sha256,
        bound_job_id=closure.bound_job_id,bound_payload_fingerprint=closure.bound_payload_fingerprint,
        sandbox_sha256=sandbox.executable_sha256,program_sha256=hashlib.sha256(_PROGRAM.encode()).hexdigest(),
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),root_absent=True,
        fd_inventory=held,nested_fd_inventory=nested,native_fd_inventory=native)
