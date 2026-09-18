#!/usr/bin/env python3
"""Prepare a sealed full-source LOCAL stage. Never issue approval or start a job."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from packages.pre_p3_provenance import canonical_source_identity,_git
from packages.runtime_release.staging_v2 import walk_sealed_stage,_validate_private_root
from packages.runtime_release.v2 import (
    PAPER_UV_PROVENANCE,construct_python_runtime_archive,construct_paper_backend_artifact,
    verify_python_runtime_execution,
)


def prepare_stage(source: Path,stage: Path,python_runtime: Path,uv: Path):
    try:
        if not (source/'.git').is_dir() or _git(source,'status','--porcelain','--untracked-files=all'):
            raise ValueError
        identity=canonical_source_identity(source)
    except (OSError,ValueError,subprocess.SubprocessError):
        raise ValueError('LOCAL runtime requires clean committed source') from None
    if not stage.is_absolute() or stage.name!='stage' or stage.exists() or stage.is_symlink():
        raise ValueError('LOCAL stage destination must be new')
    _validate_private_root(stage.parent)
    if (not uv.is_absolute() or uv.is_symlink()
        or hashlib.sha256(uv.read_bytes()).hexdigest()!=PAPER_UV_PROVENANCE['sha256']):
        raise ValueError('LOCAL runtime requires the pinned uv executable')
    env={'PATH':'/usr/bin:/bin','HOME':os.environ['HOME'],'LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
        'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_NO_REPLACE_OBJECTS':'1',
        'UV_OFFLINE':'1','UV_COMPILE_BYTECODE':'0','PYTHONDONTWRITEBYTECODE':'1'}
    def run(argv):subprocess.run(list(map(str,argv)),env=env,check=True,capture_output=True)
    stage.mkdir(mode=0o700)
    application=stage/'application'
    run(['/usr/bin/git','clone','--no-local','--no-hardlinks','--no-tags',source,application])
    run(['/usr/bin/git','-C',application,'checkout','--detach',identity['commit_sha']])
    if canonical_source_identity(application)!=identity:raise ValueError('cloned source differs')
    construct_python_runtime_archive(python_runtime,application/'.venv')
    construct_paper_backend_artifact(application,stage/'backend')
    construct_python_runtime_archive(python_runtime,stage/'backend/.venv')
    interpreter=application/'.venv/bin/python3.11'
    # Reuse pinned uv, the root frozen lock and its already populated offline
    # cache. Missing cached wheels fail closed; preparation never downloads.
    with tempfile.TemporaryDirectory(prefix='p3-dependencies-',dir=stage.parent) as tmp:
        requirements=Path(tmp)/'requirements.txt'
        run([uv,'export','--project',application,'--frozen','--no-emit-project','--no-annotate',
            '--no-header','--offline','--no-python-downloads','--output-file',requirements])
        run([uv,'pip','sync',requirements,'--python',interpreter,'--require-hashes','--strict',
            '--only-binary=:all:','--offline','--link-mode','copy','--no-python-downloads'])
    for entry in (application/'.venv/bin').iterdir():
        if entry.name!='python3.11':entry.unlink()
    # The locked greenlet wheel adds a C development header to CPython's core
    # include tree. P3 runs the prebuilt extension; retain the exact pinned core.
    header=application/'.venv/include/python3.11/greenlet/greenlet.h'
    if header.exists():header.unlink()
    site=application/'.venv/lib/python3.11/site-packages'
    (site/'p3_source.pth').write_text('../../../..\n../../../../apps/control_api\n')
    verify_python_runtime_execution(application/'.venv')
    verify_python_runtime_execution(stage/'backend/.venv')
    for path in sorted(stage.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        info=path.lstat()
        if path.is_symlink() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('LOCAL stage cannot contain links or special files')
        path.chmod(0o555 if path.is_dir() or info.st_mode&0o111 else 0o444)
    stage.chmod(0o555)
    walk_sealed_stage(stage)
    if _git(application,'status','--porcelain','--untracked-files=all'):
        raise ValueError('sealed source differs')
    return {'source':identity,'application_root':str(application),'application_python':str(interpreter),
        'scope':'PACKAGE6_STAGING_ONLY','status':'PREPARED_NOT_APPROVED'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','stage','python-runtime','uv'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try:
        result=prepare_stage(args.source,args.stage,args.python_runtime,args.uv)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        print(json.dumps({'status':'HELD','reason':'LOCAL_STAGE_PREPARATION_FAILED'}));return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
