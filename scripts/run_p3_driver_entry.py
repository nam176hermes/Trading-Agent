"""Apply fixed outer-driver bounds, then exec the one P3 campaign entrypoint."""
import os
from pathlib import Path
import resource
import sys


def main():
    # The private policy input is mounted only by the authenticated LOCAL parent.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.run_p3_native_child import LOCAL_FLAG, read_local_policy
    args = sys.argv[1:]
    local = args[:1] == [LOCAL_FLAG]
    if local:
        read_local_policy('/p3/inputs/local-work-policy')
        args = args[1:]
    for kind, value in ((resource.RLIMIT_CPU,960),(resource.RLIMIT_AS,2147483648),
        (resource.RLIMIT_FSIZE,268435456),(resource.RLIMIT_NOFILE,256),(resource.RLIMIT_NPROC,16)):
        if local and kind == resource.RLIMIT_CPU:
            continue
        resource.setrlimit(kind,(value,value))
    command = Path(__file__).with_name('run_p3_alpha_campaign.py')
    os.execv(sys.executable,(sys.executable,'-I','-B',str(command),*args))


if __name__ == '__main__':
    main()
