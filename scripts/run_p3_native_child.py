"""Fixed private entry over sealed P1 dependencies and the separate P3 adapter."""
import io
import os
import resource
import runpy
import stat
import sys

ENTRY = '/p3-native/entry.py'
ADAPTER = '/p3-native/adapter.py'
REQUEST = '/inputs/p3-native.json'
COMMAND = ('/usr/bin/python3.12', '-I', '-B', '-S', ENTRY)
LOCAL_FLAG = '--p3-local-measure-work'
LOCAL_POLICY_PATH = '/inputs/p3-local-work-policy'
LOCAL_POLICY = b'P3_LOCAL_MEASURE_WORK_V1\n'
# Bubblewrap adds PWD for its fixed --chdir even after --clearenv.
ENVIRONMENT = dict(PWD='/', HOME='/tmp', LANG='C.UTF-8', LC_ALL='C.UTF-8', TZ='UTC',
    PYTHONHASHSEED='0', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
MAX_REQUEST_BYTES = 131072
RESOURCE_LIMITS = dict(RLIMIT_CPU=240, RLIMIT_AS=2147483648, RLIMIT_NOFILE=256,
    RLIMIT_NPROC=16, RLIMIT_FSIZE=268435456)


def read_local_policy(path) -> None:
    """Only the attested parent can mount this fixed anonymous policy input."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0
            or stat.S_IMODE(info.st_mode) != 0o400 or info.st_size != len(LOCAL_POLICY)
            or os.pread(fd, len(LOCAL_POLICY)+1, 0) != LOCAL_POLICY):
            raise ValueError('LOCAL execution requires its exact anonymous policy')
        # ro-bind-data copies the sealed parent bytes into a read-only mount.
        if not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
            raise ValueError('LOCAL execution policy mount is not read-only')
    finally:
        os.close(fd)


def main() -> None:
    local = tuple(sys.orig_argv) == (*COMMAND, LOCAL_FLAG)
    if (tuple(sys.orig_argv) not in (COMMAND, (*COMMAND, LOCAL_FLAG)) or sys.version_info[:2] != (3, 12)
        or not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode
        or dict(os.environ) != ENVIRONMENT or os.getcwd() != '/'):
        raise ValueError('P3 native requires its fixed isolated entry')
    if local:
        read_local_policy(LOCAL_POLICY_PATH)
    for kind, bound in RESOURCE_LIMITS.items():
        if local and kind == 'RLIMIT_CPU':
            continue
        resource.setrlimit(getattr(resource, kind), (bound, bound))
    fd = os.open(REQUEST, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0
            or stat.S_IMODE(info.st_mode) != 0o400 or not 0 < info.st_size <= MAX_REQUEST_BYTES):
            raise ValueError('P3 native requires a bounded anonymous request')
        raw = os.pread(fd, info.st_size+1, 0)
        if len(raw) != info.st_size:
            raise ValueError('P3 native request changed')
    finally:
        os.close(fd)
    sys.path.insert(0, '/engine')
    from runtime_v1.dependency_scope import sealed_wheel_imports
    with sealed_wheel_imports():
        sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8')
        runpy.run_path(ADAPTER, run_name='__main__')


if __name__ == '__main__':
    main()
