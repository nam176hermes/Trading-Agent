"""Bounded P3 startup probes; importing this module performs no host action."""
import os
import hashlib
from collections.abc import Iterator
from contextlib import ExitStack,contextmanager
from pathlib import Path,PurePosixPath
import re
import resource
import selectors
import signal
import subprocess
import time
import threading
import uuid

from packages.alpha_lifecycle.sandbox_policy import (
    BWRAP_VERSION,BWRAP_CAPABILITIES,SANDBOX_ARGS,FILESYSTEM_ARGS,ROOT_READONLY_ARGS,
    ENVIRONMENT_ARGS,MAX_CLOSURE_FILES,MAX_CLOSURE_BYTES,MAX_ARGV_BYTES,SANDBOX_PROFILE_SHA256,socket_filter_bytes,
)
from services.job_store.records import validate_p3_job_id
from services.operator_control.protected_fs import ProtectedDirectory,open_private_directory,read_private_file
from .engine_spawn import _sealed_memfd
from .p3_spawn import CompleteP3Closure,P3ClosureMount,P3Sandbox,_file_bytes,_digest
from .p3_output import _identity


_PROGRAM='''
import errno,os,socket,sys
for descriptor in map(int,sys.argv[1:]):
 try: os.fstat(descriptor)
 except OSError as error: assert error.errno==errno.EBADF
 else: raise AssertionError('transport descriptor survived exec')
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


def _probe_limits() -> None:
    for kind,value in ((resource.RLIMIT_CPU,5),(resource.RLIMIT_AS,2147483648),(resource.RLIMIT_FSIZE,65536)):
        resource.setrlimit(kind,(value,value))


def _run_probe(argv: tuple[str, ...], passed: tuple[int, ...]) -> tuple[int | None, bytes, bytes]:
    """After Popen returns, allow 5s execution and 2s reaping; 64 KiB per stream.

    An enclosing host startup deadline must also cover preexec/exec and I/O.
    """
    if threading.active_count()!=1 or signal.getsignal(signal.SIGCHLD)!=signal.SIG_DFL:
        raise ValueError('P3 startup probe requires exclusive child reaping')
    process=subprocess.Popen(argv,cwd='/',env={},stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,pass_fds=passed,
        start_new_session=True,preexec_fn=_probe_limits)
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
    sandbox=closure.sandbox
    if (type(sandbox) is not P3Sandbox or sandbox.profile_sha256!=SANDBOX_PROFILE_SHA256
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
    soft,_=resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft!=resource.RLIM_INFINITY and len(os.listdir('/proc/self/fd'))+len(targets)+20>=soft:
        raise ValueError('P3 startup descriptor bound is insufficient')
    descriptors=[]
    started=time.monotonic()
    with _probe_directories(private_root) as (inputs,output):
        try:
            raw=_file_bytes(sandbox.executable,sandbox.identity,sandbox.executable.stat(follow_symlinks=False).st_size,
                sandbox.mode,sandbox.executable_sha256)
            fd=_sealed_memfd('p3-startup-bwrap',raw,mode=0o500);descriptors.append(fd)
            executable=f'/proc/self/fd/{fd}'
            code,version,stderr=_run_probe((executable,'--version'),(fd,))
            if code or stderr or version!=BWRAP_VERSION.encode()+b'\n':
                raise ValueError('P3 startup sandbox version differs')
            code,help_text,stderr=_run_probe((executable,'--help'),(fd,))
            if code or stderr or any(cap.encode() not in help_text.split() for cap in BWRAP_CAPABILITIES):
                raise ValueError('P3 startup sandbox capabilities differ')
            argv=[executable,*SANDBOX_ARGS,*FILESYSTEM_ARGS]
            directories={PurePosixPath('/p3'),PurePosixPath('/p3/store'),PurePosixPath('/p3/output')}
            for target in targets:
                directories.update(p for p in target.parents if p!=PurePosixPath('/'))
            for directory in sorted(directories,key=lambda p:(len(p.parts),str(p))):
                argv.extend(('--dir',str(directory)))
            for mount in closure.mounts:
                raw=_file_bytes(mount.source,mount.identity,mount.size,mount.mode,mount.sha256)
                fd=_sealed_memfd('p3-startup-file',raw,mode=mount.mode);descriptors.append(fd)
                argv.extend(('--perms',f'{mount.mode:o}','--ro-bind-data',str(fd),str(mount.target)))
                if time.monotonic()-started>=120:
                    raise ValueError('P3 startup preparation timed out')
            input_fd=os.dup(inputs.descriptor);descriptors.append(input_fd)
            output_fd=os.dup(output.descriptor);descriptors.append(output_fd)
            filter_fd=_sealed_memfd('p3-startup-filter',socket_filter_bytes(),mode=0o400);descriptors.append(filter_fd)
            argv.extend(('--ro-bind-fd',str(input_fd),'/p3/store','--bind-fd',str(output_fd),'/p3/output',
                *ROOT_READONLY_ARGS,'--chdir','/p3',*ENVIRONMENT_ARGS,'--seccomp',str(filter_fd),
                '/p3/python/bin/python3.11','-I','-B','-c',_PROGRAM,*map(str,descriptors)))
            if sum(len(arg.encode())+1 for arg in argv)>MAX_ARGV_BYTES:
                raise ValueError('P3 startup argument bound exceeded')
            code,stdout,stderr=_run_probe(tuple(argv),tuple(descriptors))
            if (code or stdout!=b'P3_STARTUP_PROBE_PASS\n' or stderr
                or os.listdir(inputs.descriptor) or os.listdir(output.descriptor)!=['probe-output']):
                raise ValueError('P3 startup kernel/transport probe failed')
            _read_probe_output(output)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    return dict(schema_version='p3-startup-probe-v1',closure_sha256=closure.closure_sha256,
        bound_job_id=closure.bound_job_id,bound_payload_fingerprint=closure.bound_payload_fingerprint,
        sandbox_sha256=sandbox.executable_sha256,program_sha256=hashlib.sha256(_PROGRAM.encode()).hexdigest(),
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),root_absent=True)
