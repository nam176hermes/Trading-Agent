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
ENVIRONMENT = dict(HOME='/tmp', LANG='C.UTF-8', LC_ALL='C.UTF-8', TZ='UTC',
    PYTHONHASHSEED='0', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
MAX_REQUEST_BYTES = 131072
RESOURCE_LIMITS = dict(RLIMIT_CPU=240, RLIMIT_AS=2147483648, RLIMIT_NOFILE=256,
    RLIMIT_NPROC=16, RLIMIT_FSIZE=268435456)


def main() -> None:
    if (tuple(sys.orig_argv) != COMMAND or sys.version_info[:2] != (3, 12)
        or not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode
        or dict(os.environ) != ENVIRONMENT or os.getcwd() != '/'):
        raise ValueError('P3 native requires its fixed isolated entry')
    for kind, bound in RESOURCE_LIMITS.items():
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
