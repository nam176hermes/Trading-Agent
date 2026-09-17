"""Local bounded probe processes are source checks, never official research."""
import sys
import os

import pytest
from tests.p3.test_spawn_capability import synthetic_provider


def test_probe_captures_bounded_output_and_reaps_its_process():
    from services.job_worker.p3_startup import _run_probe
    result=_run_probe((sys.executable,'-I','-c',"print('synthetic probe')"),())
    assert result==(0,b'synthetic probe\n',b'')


def test_probe_limits_apply_before_the_executable_runs():
    import json
    from services.job_worker.p3_startup import _run_probe
    code,stdout,stderr=_run_probe((sys.executable,'-I','-c',
        'import json,resource;print(json.dumps([resource.getrlimit(k) for k in (resource.RLIMIT_CPU,resource.RLIMIT_AS,resource.RLIMIT_FSIZE)]))'),())
    assert code==0 and stderr==b''
    assert json.loads(stdout)==[[5,5],[2147483648,2147483648],[65536,65536]]


@pytest.mark.parametrize('program',[
    "import os;os.write(1,b'x'*1000000)",
    "import time;time.sleep(30)",
    "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])",
])
def test_probe_rejects_excess_or_inherited_pipe_without_leaking_processes(program,monkeypatch):
    from services.job_worker import p3_startup as module
    popen=module.subprocess.Popen;children=[]
    def start(*args,**kwargs):
        child=popen(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(module.subprocess,'Popen',start)
    with pytest.raises(ValueError,match='bound|timed out'):
        module._run_probe((sys.executable,'-I','-c',program),())
    assert len(children)==1 and children[0].returncode is not None
    assert children[0].stdout.closed and children[0].stderr.closed


@pytest.mark.parametrize('fault',[None,'version','capability','kernel','symlink','rename','fifo','mode','size'])
def test_startup_uses_pinned_files_and_cleans_only_its_probe(synthetic_provider,monkeypatch,fault):
    from services.job_worker import p3_startup as module
    from packages.alpha_lifecycle.sandbox_policy import BWRAP_VERSION,BWRAP_CAPABILITIES,socket_filter_bytes
    _,_,closure=synthetic_provider
    root=closure.python_root.parent/'probe-parent';root.mkdir(mode=0o700)
    preserved=root/'keep';preserved.write_bytes(b'user data')
    calls=[]
    def execute(argv,passed):
        calls.append((argv,passed))
        assert argv[0]==f'/proc/self/fd/{passed[0]}'
        assert all(not os.get_inheritable(fd) for fd in passed)
        if argv[-1]=='--version':
            return 0,('wrong' if fault=='version' else BWRAP_VERSION).encode()+b'\n',b''
        if argv[-1]=='--help':
            return 0,b'' if fault=='capability' else ' '.join(BWRAP_CAPABILITIES).encode(),b''
        assert '--unshare-all' in argv and '--clearenv' in argv and '--remount-ro' in argv
        assert os.pread(int(argv[argv.index('--seccomp')+1]),4096,0)==socket_filter_bytes()
        for mount in closure.mounts:
            index=argv.index(str(mount.target))
            assert argv[index-2]=='--ro-bind-data'
            assert os.pread(int(argv[index-1]),mount.size+1,0)==mount.source.read_bytes()
        if fault=='kernel':
            return 1,b'',b'synthetic failure'
        output=int(argv[argv.index('--bind-fd')+1])
        if fault=='symlink':
            target=root/'target';target.write_bytes(b'allowed')
            os.symlink(target,'probe-output',dir_fd=output)
            return 0,b'P3_STARTUP_PROBE_PASS\n',b''
        if fault=='fifo':
            os.mkfifo('probe-output',mode=0o600,dir_fd=output)
            return 0,b'P3_STARTUP_PROBE_PASS\n',b''
        fd=os.open('probe-output',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=output)
        try:
            os.write(fd,b'allowed!' if fault=='size' else b'allowed')
            if fault=='mode': os.fchmod(fd,0o644)
        finally: os.close(fd)
        if fault=='rename':
            original=next(p for p in root.iterdir() if p.name.startswith('.p3-startup-'))
            original.rename(root/'moved')
            original.mkdir(mode=0o700)
            (original/'preserve-replacement').write_bytes(b'replacement')
        return 0,b'P3_STARTUP_PROBE_PASS\n',b''
    monkeypatch.setattr(module,'_run_probe',execute)
    before=set(os.listdir('/proc/self/fd'))
    if fault:
        with pytest.raises(ValueError):
            module.probe_p3_startup(closure,private_root=root)
    else:
        proof=module.probe_p3_startup(closure,private_root=root)
        assert proof['closure_sha256']==closure.closure_sha256 and proof['root_absent'] is True
        assert proof['bound_job_id']==closure.bound_job_id
        assert proof['bound_payload_fingerprint']==closure.bound_payload_fingerprint
        assert len(calls)==3
    assert set(os.listdir('/proc/self/fd'))==before
    if fault=='rename':
        assert (root/'moved').is_dir()
        replacement=next(p for p in root.iterdir() if p.name.startswith('.p3-startup-'))
        assert (replacement/'preserve-replacement').read_bytes()==b'replacement'
    else:
        assert set(p.name for p in root.iterdir())==({'keep','target'} if fault=='symlink' else {'keep'})
    assert preserved.read_bytes()==b'user data'


def test_invalid_mount_role_is_rejected_before_any_probe_exec(synthetic_provider,monkeypatch):
    from dataclasses import replace
    from pathlib import PurePosixPath
    from services.job_worker import p3_startup as module
    from services.job_worker.p3_spawn import _digest
    _,_,closure=synthetic_provider
    mounts=tuple(sorted((replace(closure.mounts[0],target=PurePosixPath('/run/escape')),*closure.mounts[1:]),key=lambda m:m.target))
    closure=replace(closure,mounts=mounts,closure_sha256=_digest(dict(source=closure.source,environment_ref=closure.environment_ref,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in mounts],
        sandbox_sha256=closure.sandbox.executable_sha256,sandbox_policy_sha256=closure.sandbox.profile_sha256)))
    calls=[]
    monkeypatch.setattr(module,'_run_probe',lambda *args:calls.append(args) or (1,b'',b'failed'))
    with pytest.raises(ValueError):
        module.probe_p3_startup(closure,private_root=closure.python_root)
    assert calls==[]


@pytest.mark.parametrize('field,value',[
    ('version','wrong'),('profile_sha256','0'*64),('capabilities',()),('mode',0o777),
])
def test_invalid_sandbox_policy_is_rejected_before_exec(synthetic_provider,monkeypatch,field,value):
    from dataclasses import replace
    from services.job_worker import p3_startup as module
    from services.job_worker.p3_spawn import _digest
    _,_,closure=synthetic_provider
    sandbox=replace(closure.sandbox,**{field:value})
    closure=replace(closure,sandbox=sandbox,closure_sha256=_digest(dict(source=closure.source,environment_ref=closure.environment_ref,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in closure.mounts],
        sandbox_sha256=sandbox.executable_sha256,sandbox_policy_sha256=sandbox.profile_sha256)))
    calls=[]
    monkeypatch.setattr(module,'_run_probe',lambda *args:calls.append(args) or (1,b'',b'failed'))
    with pytest.raises(ValueError):
        module.probe_p3_startup(closure,private_root=closure.python_root)
    assert calls==[]
