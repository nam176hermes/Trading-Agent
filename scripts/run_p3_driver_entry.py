"""Apply fixed outer-driver bounds, then exec the one P3 campaign entrypoint."""
import os
from pathlib import Path
import resource
import sys


def main():
    # Three replicas retain their 240s CPU/300s wall limits. One additional
    # parent budget covers validation and retention; economic policy is unchanged.
    for kind, value in ((resource.RLIMIT_CPU,960),(resource.RLIMIT_AS,2147483648),
        (resource.RLIMIT_FSIZE,268435456),(resource.RLIMIT_NOFILE,256),(resource.RLIMIT_NPROC,16)):
        resource.setrlimit(kind,(value,value))
    command = Path(__file__).with_name('run_p3_alpha_campaign.py')
    os.execv(sys.executable,(sys.executable,'-I','-B',str(command),*sys.argv[1:]))


if __name__ == '__main__':
    main()
