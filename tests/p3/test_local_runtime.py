"""LOCAL startup composition is separate from installed protected approval."""
from datetime import UTC,datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
import os
import sys

import pytest


@pytest.mark.parametrize('fault',[None,'uid','root','source','approval','static','expired','duration','revoked'])
def test_local_runtime_binding_rejects_mismatches(tmp_path,monkeypatch,fault):
    from services.job_worker import p3_local_runtime as module
    from tests.p3.test_enqueue_freshness import local_request
    request,auth,_=local_request()
    source=request.payload.expected_source
    now=datetime.now(UTC)
    root=Path(module.__file__).resolve().parents[2]
    profile=module.LocalRuntimeProfile(schema_version='p3-local-runtime-profile-v1',identity=auth.identity,
        source=source,application_root=str(root),application_python=sys.executable,uid=os.geteuid(),
        static_authority_sha256='a'*64,package6_approval_sha256='b'*64,
        issued_at=now-timedelta(minutes=2),expires_at=now+timedelta(minutes=10),revoked=False)
    material=SimpleNamespace(authority_sha256='a'*64,authority_generated_at=now-timedelta(minutes=2),
        authority_expires_at=now+timedelta(minutes=15))
    runtime=SimpleNamespace(scope='PACKAGE6_STAGING_ONLY',application_root=root,
        application_python=Path(sys.executable),source_commit=source.commit_sha,source_tree=source.tree_sha,
        package6_approval_sha256='b'*64,_material=material)
    if fault=='uid':profile=profile.model_copy(update={'uid':os.geteuid()+1})
    if fault=='root':runtime.application_root=tmp_path
    if fault=='source':runtime.source_tree='0'*40
    if fault=='approval':runtime.package6_approval_sha256='0'*64
    if fault=='static':material.authority_sha256='0'*64
    if fault=='expired':profile=profile.model_copy(update={'expires_at':now-timedelta(seconds=1)})
    if fault=='duration':material.authority_expires_at=now+timedelta(seconds=1)
    if fault=='revoked':profile=profile.model_copy(update={'revoked':True})
    # Unit seam only; the subprocess integration test exercises actual Git/runtime.
    monkeypatch.setattr(module,'canonical_source_identity',lambda root:source.model_dump())
    monkeypatch.setattr(module,'_git',lambda *a:b'')
    monkeypatch.setattr(module,'_host_identity',lambda:(auth.identity.host_id,auth.identity.boot_id))
    if fault:
        with pytest.raises(ValueError):module.validate_runtime_binding(profile,runtime,auth,source)
    else:module.validate_runtime_binding(profile,runtime,auth,source)


def test_stage_carries_the_dashboard_build_the_hwc_proof_binds(tmp_path):
    """A git-clone stage has no ignored .next; the LOCAL HWC readback hashes it there."""
    from scripts.prepare_p3_local_runtime import _install_dashboard_build
    from scripts.qualify_hwc_headless import _tree_sha
    build=tmp_path/'build';(build/'static').mkdir(parents=True)
    (build/'BUILD_ID').write_bytes(b'qualified\n');(build/'static/app.js').write_bytes(b'x'*257)
    application=tmp_path/'application';(application/'apps/dashboard').mkdir(parents=True)
    assert _install_dashboard_build(build,application)==_tree_sha(build)
    assert (application/'apps/dashboard/.next/BUILD_ID').read_bytes()==b'qualified\n'
    with pytest.raises(ValueError,match='must be new'):_install_dashboard_build(build,application)
    with pytest.raises(ValueError,match='absolute'):_install_dashboard_build(Path('build'),application)
    # A link is copied as a link, so the stage seal refuses it instead of following it.
    other=tmp_path/'other';(other/'apps/dashboard').mkdir(parents=True)
    (build/'escape').symlink_to('/etc/passwd')
    _install_dashboard_build(build,other)
    assert (other/'apps/dashboard/.next/escape').is_symlink()


def test_local_stage_builder_requires_a_clean_committed_source(tmp_path):
    from scripts.prepare_p3_local_runtime import prepare_stage
    with pytest.raises(ValueError,match='clean|source'):
        prepare_stage(tmp_path,tmp_path/'stage',Path(sys.base_prefix),Path('/nonexistent/uv'))


def test_full_source_stage_runs_real_attester_without_github(tmp_path,package6_staging_lease):
    """Synthetic staging authority only; no root approval, SQL, service or job."""
    import json
    import shutil
    import subprocess
    from scripts.prepare_p3_local_runtime import prepare_stage
    from packages.pre_p3_provenance import canonical_source_identity
    from packages.runtime_release import staging_v2 as stage_api
    from packages.engine_contracts.serialization import canonical_json_bytes
    root=Path(__file__).resolve().parents[2]
    fixture=tmp_path/'source';fixture.mkdir()
    # Commit a test-only copy of current source bytes, never the user's checkout.
    files=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root).split(b'\0')
    for raw in files:
        if not raw:continue
        relative=os.fsdecode(raw);target=fixture/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/relative,target)
    def git(*args):return subprocess.check_output(['git',*args],cwd=fixture,stderr=subprocess.PIPE)
    git('init','-q');git('add','.')
    git('-c','user.name=Source test','-c','user.email=source-test@example.invalid','commit','-qm','synthetic runtime source')
    private=package6_staging_lease.root
    stage=private/'stage'
    archive=Path(os.environ.get('P3_TEST_PYTHON_ARCHIVE',str(Path.home()/'.cache/trading-agent/runtime-release/cpython-3.11.15+20260414-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz')))
    if not archive.is_file():pytest.skip('pinned offline CPython archive unavailable')
    result=prepare_stage(fixture,stage,archive,Path(shutil.which('uv')).resolve())
    assert result['status']=='PREPARED_NOT_APPROVED'
    source=canonical_source_identity(fixture)
    assert canonical_source_identity(stage/'application')==source
    for name in ('runtime','semantic','authority'):(private/name).mkdir(mode=0o700)
    for name in ('artifacts','reports','scratch','signals'):(private/'runtime'/name).mkdir(mode=0o700)
    (private/'semantic/input').mkdir(mode=0o711)
    now=datetime.now(UTC).replace(microsecond=0);expires=now+timedelta(minutes=10)
    paths={name:private/relative for name,relative in {
        'artifact_root':'runtime/artifacts','reports_root':'runtime/reports','scratch_root':'runtime/scratch',
        'signals_root':'runtime/signals','safety_snapshot':'runtime/safety-state.json',
        'semantic_authority':'semantic/active.json','semantic_input_root':'semantic/input'}.items()}
    static,static_digest=stage_api.build_staging_release_authority_v2(stage,source_commit=source['commit_sha'],
        source_tree=source['tree_sha'],disposable_root=private,production_release_authority_sha256='a'*64,
        runtime_paths=paths,generated_at=now,expires_at=expires)
    static_path=private/'authority/release-authority-v2.json'
    static_path.write_bytes(stage_api.canonical_json_bytes(static));static_path.chmod(0o444)
    source_file=private/'source.json';source_file.write_bytes(canonical_json_bytes(source))
    program=r'''
import json,os,sys,time
from pathlib import Path
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4
from packages.runtime_release import staging_v2 as s
from packages.runtime_release.config import load_runtime_authority_v2
from services.job_worker.command_registry import attest_worker_runtime_authority
from services.job_worker.p3_local_runtime import LocalRuntimeProfile,validate_runtime_binding
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.alpha_lifecycle.local_qualification import _host_identity
from services.job_worker.p3_spawn import _release_files
private=Path(sys.argv[1]);source=SourceIdentity.model_validate_json((private/'source.json').read_bytes())
static=json.loads((private/'authority/release-authority-v2.json').read_bytes())
static_digest=s.canonical_digest(static)
now=datetime.now(UTC).replace(microsecond=0);expires=now+timedelta(minutes=2)
def write(path,value,mode):
 raw=s.canonical_json_bytes(value);path.write_bytes(raw);path.chmod(mode);return s.sha256_bytes(raw)
semantic_digest=write(private/'semantic/active.json',dict(classification='PACKAGE6_PROVIDER_FREE_SEMANTIC',schema_version=1,source_commit=source.commit_sha),0o444)
# Generate the declared source-test snapshot immediately before actual admission.
safety_digest=write(private/'runtime/safety-state.json',dict(schema_version=1,requested_mode='PAPER',effective_mode='PAPER',
 live_execution_enabled=False,live_trading_approved=False,kill_switch_state='INACTIVE',exporter_commit=source.commit_sha,
 source_fingerprint='d'*64,generated_at=s._timestamp_text(now),expires_at=s._timestamp_text(now+timedelta(seconds=6))),0o600)
activation,_=s.build_staging_activation_v2(authority_sha256=static_digest,package6_approval_sha256='c'*64,
 safety_snapshot_sha256=safety_digest,safety_exporter_commit=source.commit_sha,safety_source_fingerprint='d'*64,
 semantic_active_authority_sha256=semantic_digest,semantic_version_manifest_sha256=semantic_digest,
 semantic_input_fingerprint='1'*64,semantic_manifest_version='package6-provider-free-v1',semantic_policy_sha256='f'*64,
 semantic_generated_at=datetime.fromisoformat(static['validity']['generated_at_utc'].replace('Z','+00:00')),semantic_expires_at=expires,
 generated_at=datetime.fromisoformat(static['validity']['generated_at_utc'].replace('Z','+00:00')),
 expires_at=datetime.fromisoformat(static['validity']['expires_at_utc'].replace('Z','+00:00')))
write(private/'authority/release-activation-v2.json',activation,0o444)
started=time.monotonic()
runtime=load_runtime_authority_v2()
authority=attest_worker_runtime_authority()
host,boot=_host_identity();identity=LocalRunIdentity(issuer_kind='LOCAL',issuer_identity='synthetic',run_id=uuid4(),attempt=1,host_id=host,boot_id=boot)
profile=LocalRuntimeProfile(schema_version='p3-local-runtime-profile-v1',identity=identity,source=source,
 application_root=str(private/'stage/application'),application_python=sys.executable,uid=os.geteuid(),
 static_authority_sha256=static_digest,package6_approval_sha256='c'*64,issued_at=now,expires_at=expires,revoked=False)
operation=SimpleNamespace(identity=identity,expires_at=expires)
validate_runtime_binding(profile,authority.runtime_authority,operation,source)
assert _release_files(source)
for field,value in [('uid',os.geteuid()+1),('application_python','/usr/bin/python3'),('static_authority_sha256','0'*64),('revoked',True)]:
 try:validate_runtime_binding(profile.model_copy(update={field:value}),runtime,operation,source)
 except ValueError:pass
 else:raise AssertionError('mismatched protected binding accepted: '+field)
assert not any(k.startswith('GITHUB_') for k in os.environ)
assert all(Path(m.__file__).is_relative_to(private/'stage/application') for n,m in sys.modules.items()
 if n.split('.')[0] in {'apps','services','packages'} and getattr(m,'__file__',None))
# Same-path source tampering is rejected by both Git identity and sealed attester.
target=private/'stage/application/services/job_worker/p3_local_runtime.py';target.chmod(0o644)
target.write_bytes(target.read_bytes()+b'\n# synthetic tamper\n');target.chmod(0o444)
try:validate_runtime_binding(profile,runtime,operation,source)
except ValueError:pass
else:raise AssertionError('dirty source accepted')
try:attest_worker_runtime_authority()
except Exception:pass
else:raise AssertionError('changed stage accepted')
print(json.dumps({'status':'PASS_SOURCE_COMPOSITION','elapsed_seconds':time.monotonic()-started,'installed_approval':False}))
'''
    env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','PYTHONDONTWRITEBYTECODE':'1',
        'TRADING_PACKAGE6_STAGING_SCOPE':'PACKAGE6_STAGING_ONLY',
        'TRADING_PACKAGE6_STAGING_AUTHORITY_PATH':str(static_path),
        'TRADING_PACKAGE6_STAGING_ACTIVATION_PATH':str(private/'authority/release-activation-v2.json'),
        'TRADING_PACKAGE6_APPROVAL_SHA256':'c'*64}
    completed=subprocess.run([result['application_python'],'-I','-B','-c',program,str(private)],
        env=env,cwd=private,capture_output=True,text=True,timeout=120)
    assert completed.returncode==0,completed.stderr
    print(completed.stdout,flush=True)
    assert json.loads(completed.stdout)['status']=='PASS_SOURCE_COMPOSITION'


# The shared fixture owns and verifies cleanup of a direct private /tmp root.
from tests.foundation._package6_staging_fixture import package6_staging_lease as package6_staging_lease


def test_user_owned_runtime_profile_is_not_host_approval(tmp_path,monkeypatch):
    from services.job_worker import p3_local_runtime as module
    from packages.runtime_release.config import ProtectedAuthorityError
    from tests.p3.test_enqueue_freshness import local_request
    request,auth,_=local_request()
    monkeypatch.setattr(module,'PROFILE_ROOT',tmp_path)
    parent=tmp_path/auth.identity.issuer_identity;parent.mkdir()
    path=parent/'runtime.json';path.write_text('{}\n');path.chmod(0o444)
    with pytest.raises(ProtectedAuthorityError):
        module.require_local_runtime(object(),auth.identity,request.payload.expected_source,auth.expires_at)


@pytest.mark.parametrize('fault',['revoked','replaced','missing','custody'])
def test_runtime_guard_pins_and_rereads_approval(monkeypatch,fault):
    from services.job_worker import p3_local_runtime as module
    from services.job_worker.errors import SafetyBlockedError
    digest=['a'*64]
    def read(*args):
        if fault=='missing' and digest[0]=='changed':raise OSError('missing')
        if fault=='custody' and digest[0]=='changed':raise module.ProtectedAuthorityError('custody')
        if fault=='revoked' and digest[0]=='changed':raise ValueError('revoked')
        return digest[0]
    monkeypatch.setattr(module,'require_local_runtime',read)
    guard=module.local_runtime_guard(None,None,None,None)
    guard()
    digest[0]='changed'
    with pytest.raises(SafetyBlockedError,match='LOCAL runtime') as caught:guard()
    assert caught.value.reason_code=='SAFETY_AUTHORITY_CHANGED'


def test_session_runtime_deadline_keeps_startup_digest(monkeypatch):
    from functools import partial
    from services.job_worker import p3_local_runtime as module
    from services.job_worker.errors import SafetyBlockedError
    seen=[];digest=['a'*64]
    def read(authority,identity,source,expires_at):
        seen.append(expires_at)
        return digest[0]
    monkeypatch.setattr(module,'require_local_runtime',read)
    guard=module.local_runtime_guard(None,None,None,'operation-expiry')
    session_guard=partial(guard,deadline='session-deadline')
    session_guard()
    assert seen==['operation-expiry','session-deadline']
    digest[0]='b'*64
    with pytest.raises(SafetyBlockedError):session_guard()
