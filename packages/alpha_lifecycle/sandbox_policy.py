"""Fixed P3 isolation topology, transport bounds and socket-denial program."""
import hashlib
import json
from pathlib import Path
import struct

from packages.engine_contracts.serialization import canonical_json_bytes


POLICY_SET_SHA256 = '75a9030d017df1de47c5c42aeb95b612f50fd34630c99f82ffd8d200da506336'
_raw = (Path(__file__).resolve().parents[2]/'docs/implementation/p3/specs/p3-policy-set-v21.json').read_bytes()
if hashlib.sha256(_raw).hexdigest() != POLICY_SET_SHA256:
    raise RuntimeError('P3 sandbox requires the exact accepted policy set')
CHILD_POLICY = json.loads(_raw)['sandbox']
DRIVER_WALL_SECONDS = 4 * CHILD_POLICY['child_wall_seconds']
MAX_CLOSURE_FILES = 8192
MAX_CLOSURE_BYTES = 1073741824
MAX_ARGV_BYTES = 1048576
BWRAP_VERSION = 'bubblewrap 0.9.0'
SANDBOX_ARGS = ('--unshare-all','--die-with-parent','--clearenv')
ROOT_READONLY_ARGS = ('--remount-ro','/')
FILESYSTEM_ARGS = ('--proc','/proc','--dev','/dev','--tmpfs','/tmp')
ENVIRONMENT_ARGS = tuple(arg for key,value in (
    ('HOME','/tmp'),('LANG','C.UTF-8'),('LC_ALL','C.UTF-8'),('TZ','UTC'),('PYTHONHASHSEED','0'),
    ('OMP_NUM_THREADS','1'),('OPENBLAS_NUM_THREADS','1'),('MKL_NUM_THREADS','1'))
    for arg in ('--setenv',key,value))
RO_FILE_FLAG, RO_PATH_FLAG, RO_DIRECTORY_FLAG, RW_DIRECTORY_FLAG = '--ro-bind-data','--ro-bind','--ro-bind-fd','--bind-fd'
DRIVER_ENTRY = ('/p3/python/bin/python3.11','-I','-B','/p3/release/scripts/run_p3_driver_entry.py')
CHILD_ENTRY = 'scripts/run_p3_evaluation_child.py'
MAX_ATTEMPT_OUTPUT_BYTES = 4 * CHILD_POLICY['child_output_bytes']
MAX_OUTPUT_INVENTORY_BYTES = 4_194_304
BWRAP_CAPABILITIES = tuple(sorted(set(
    arg for arg in (*SANDBOX_ARGS,*FILESYSTEM_ARGS,*ENVIRONMENT_ARGS,*ROOT_READONLY_ARGS,
        RO_FILE_FLAG,RO_PATH_FLAG,RO_DIRECTORY_FLAG,RW_DIRECTORY_FLAG,'--dir','--chdir','--seccomp','--bind','--perms')
    if arg.startswith('--'))))


def socket_filter_bytes() -> bytes:
    # Linux x86_64 seccomp_data: nr at0, audit arch at4. Kill another ABI,
    # including x32; deny socket/socketpair before any endpoint can be used.
    return b''.join(struct.pack('=HBBI',*instruction) for instruction in (
        (0x20,0,0,4),(0x15,1,0,0xc000003e),(0x06,0,0,0x80000000),
        (0x20,0,0,0),(0x35,0,1,0x40000000),(0x06,0,0,0x80000000),
        (0x15,2,0,41),(0x15,1,0,53),(0x06,0,0,0x7fff0000),(0x06,0,0,0x00050001)))


SANDBOX_PROFILE_SHA256 = hashlib.sha256(canonical_json_bytes(dict(
    schema_version='p3-bubblewrap-driver-profile-v1',policy_set_sha256=POLICY_SET_SHA256,
    bwrap_version=BWRAP_VERSION,bwrap_capabilities=BWRAP_CAPABILITIES,
    platform='linux-x86_64',sandbox_args=SANDBOX_ARGS,filesystem_args=FILESYSTEM_ARGS,root_readonly_args=ROOT_READONLY_ARGS,
    environment_args=ENVIRONMENT_ARGS,driver_entry=DRIVER_ENTRY,child_entry=CHILD_ENTRY,
    mount_flags=dict(sealed_files=RO_FILE_FLAG,inner_inputs=RO_PATH_FLAG,
        input_directory=RO_DIRECTORY_FLAG,output_directory=RW_DIRECTORY_FLAG,
        sealed_file_mode_flag='--perms',reference_mode='400',sandbox_mode='500'),
    outer=dict(namespaces='UNSHARE_ALL',environment=ENVIRONMENT_ARGS,
        inputs='READ_ONLY_DIRECTORY_DESCRIPTOR',outputs='EXCLUSIVE_ATTEMPT_DIRECTORY_DESCRIPTOR',
        runtime='SEALED_FILES',cpu_seconds=960,wall_seconds=DRIVER_WALL_SECONDS,
        memory_bytes=2147483648,open_files=256,processes=16,per_file_output_bytes=268435456,attempt_output_bytes=MAX_ATTEMPT_OUTPUT_BYTES),
    child=CHILD_POLICY,child_socket_filter_sha256=hashlib.sha256(socket_filter_bytes()).hexdigest(),
    max_closure_files=MAX_CLOSURE_FILES,max_closure_bytes=MAX_CLOSURE_BYTES,max_argv_bytes=MAX_ARGV_BYTES,
))).hexdigest()
