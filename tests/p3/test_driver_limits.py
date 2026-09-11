"""Real local kernel bounds, without starting any official research process."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_campaign_timeout_includes_parent_budget_after_three_replicas():
    from types import SimpleNamespace
    from services.job_worker.command_registry import p3_command_spec
    for operation,workflow in [('BASELINES','p3-baselines-v1'),*(('OOS',f'p3-oos-a{i}-v1') for i in range(4))]:
        spec = p3_command_spec(SimpleNamespace(operation=operation,logical_trial_id=workflow))
        assert spec.timeout_seconds == 4*300


def test_inner_filter_denies_socket_creation_before_network_use():
    from packages.alpha_lifecycle.sandbox_policy import socket_filter_bytes
    from services.job_worker.engine_spawn import _sealed_memfd
    fd = _sealed_memfd('synthetic-p3-seccomp',socket_filter_bytes(),mode=0o400)
    try:
        program = '''
import ctypes, errno, os, socket, struct, sys
raw=os.read(int(sys.argv[1]),4096)
class Filter(ctypes.Structure):
 _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint)]
class Program(ctypes.Structure):
 _fields_=[('length',ctypes.c_ushort),('filters',ctypes.POINTER(Filter))]
filters=(Filter*(len(raw)//8))(*(Filter(*x) for x in struct.iter_unpack('HBBI',raw)))
p=Program(len(filters),filters)
lib=ctypes.CDLL(None,use_errno=True)
assert lib.prctl(38,1,0,0,0)==0
assert lib.prctl(22,2,ctypes.byref(p),0,0)==0
for create in (socket.socket,socket.socketpair):
 try: create()
 except OSError as e: assert e.errno==errno.EPERM
 else: raise AssertionError('socket was permitted')
print('DENIED_BEFORE_USE')
'''
        os.lseek(fd,0,os.SEEK_SET)
        result = subprocess.run((sys.executable,'-I','-B','-c',program,str(fd)),
            pass_fds=(fd,),env={},cwd='/',capture_output=True,timeout=10,check=True)
        assert result.stdout == b'DENIED_BEFORE_USE\n'
    finally:
        os.close(fd)


def test_driver_entry_applies_bounds_and_exec_preserves_pid(tmp_path):
    from scripts import run_p3_driver_entry as module
    entry = tmp_path/'scripts'/'run_p3_driver_entry.py'
    entry.parent.mkdir()
    entry.write_bytes(Path(module.__file__).read_bytes())
    (entry.parent/'run_p3_alpha_campaign.py').write_text(
        "import json,os,resource\nprint(json.dumps(dict(pid=os.getpid(),cpu=resource.getrlimit(resource.RLIMIT_CPU),memory=resource.getrlimit(resource.RLIMIT_AS),files=resource.getrlimit(resource.RLIMIT_NOFILE),processes=resource.getrlimit(resource.RLIMIT_NPROC))))\n")
    process = subprocess.Popen((sys.executable,'-I','-B',str(entry)),
        env={},cwd='/',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stdout,stderr = process.communicate(timeout=10)
    assert process.returncode == 0,stderr
    value=json.loads(stdout)
    assert value['pid'] == process.pid
    assert value['cpu'] == [960,960]
    assert value['memory'] == [2147483648,2147483648]
    assert value['files'] == [256,256]
    assert value['processes'] == [16,16]


def test_real_bwrap_drops_transport_descriptors(tmp_path):
    import pytest
    if os.environ.get('TRADING_P3_LOCAL_SANDBOX_PROBE') != '1':
        pytest.skip('explicit disposable host sandbox probe required')
    from packages.alpha_lifecycle.sandbox_policy import ROOT_READONLY_ARGS
    inputs=tmp_path/'input'
    inputs.mkdir(mode=0o700)
    output=tmp_path/'output'
    output.mkdir(mode=0o700)
    fd=os.open(inputs,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    output_fd=os.open(output,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        program='''
import errno, os, sys
for fd in map(int,sys.argv[1:]):
 try: os.fstat(fd)
 except OSError as e: assert e.errno == errno.EBADF
 else: raise AssertionError('transport descriptor survived sandbox exec')
try: os.mkdir('/p3/injected-source')
except OSError as e: assert e.errno == errno.EROFS
else: raise AssertionError('sandbox source namespace is writable')
with open('/p3/output/allowed','w') as stream: stream.write('synthetic output')
try:
 with open('/p3/store/forbidden','w') as stream: stream.write('bad')
except OSError as e: assert e.errno == errno.EROFS
else: raise AssertionError('input mount is writable')
print('TRANSPORT_CLOSED_INPUT_READ_ONLY')
'''
        argv=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--clearenv',
            '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
            '--proc','/proc','--dev','/dev','--ro-bind-fd',str(fd),'/p3/store',
            '--bind-fd',str(output_fd),'/p3/output',*ROOT_READONLY_ARGS,
            '/usr/bin/python3','-I','-S','-c',program,str(fd),str(output_fd)]
        result=subprocess.run(argv,pass_fds=(fd,output_fd),env={},cwd='/',capture_output=True,timeout=10)
        assert result.returncode == 0,result.stderr.decode(errors='replace')
        assert result.stdout == b'TRANSPORT_CLOSED_INPUT_READ_ONLY\n'
        assert not list(inputs.iterdir())
        assert (output/'allowed').read_text() == 'synthetic output'
    finally:
        os.close(fd)
        os.close(output_fd)
