"""Read a protected per-job profile; this consumer never provisions host authority."""
from collections.abc import Mapping
from pathlib import Path,PurePosixPath
import hashlib
import stat
from typing import Annotated,Literal

from pydantic import Field,model_validator

from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload
from packages.alpha_lifecycle.baseline_campaign import ReadbackStore,_read
from packages.alpha_lifecycle.contracts.authority import RunAuthorization
from packages.alpha_lifecycle.contracts.base import Sha256,StrictModel,SourceIdentity
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity,InputSet
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.sandbox_policy import (
    BWRAP_CAPABILITIES,BWRAP_VERSION,MAX_CLOSURE_BYTES,MAX_CLOSURE_FILES,SANDBOX_PROFILE_SHA256,
)
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload,payload_fingerprint
from packages.pre_p3_provenance import canonical_source_identity
from packages.project_status import derive_project_status
from packages.runtime_release.config import read_protected_canonical_json_current,_absolute
from services.job_store.records import validate_p3_job_id
from .engine_profiles import P1_REAL_BACKTEST_POLICY
from .p3_holdout_release import CustodianEndpoint
from .p3_qualification_readback import validate_integration_job_result,_read as _bounded_bytes
from .p3_spawn import (
    ROOT,CompleteP3Closure,P3ClosureMount,P3Sandbox,_digest,_directory_identity,_file_bytes,
    _release_files,inspect_python_runtime,
)


class OfficialHostProfile(StrictModel):
    schema_version: Literal['p3-official-host-profile-v1']
    job_id: str
    payload: AlphaCampaignPayload
    environment_ref: ArtifactRefV1
    store_root: str
    output_root: str
    python_root: str
    worker_credentials_directory: str
    sandbox_executable: str
    sandbox_sha256: Sha256
    sandbox_version: Literal['bubblewrap 0.9.0']
    inventory_ref: ArtifactRefV1
    closure_sha256: Sha256
    integration_receipt_ref: ArtifactRefV1
    qualification_job_detail_ref: ArtifactRefV1
    postgres_binary_sha256: Sha256
    custodian_endpoint: CustodianEndpoint | None = None

    @model_validator(mode='after')
    def _custody(self) -> 'OfficialHostProfile':
        if (self.payload.operation=='HOLDOUT')!=(self.custodian_endpoint is not None):
            raise ValueError('custodian endpoint is required exclusively for HOLDOUT')
        return self


class _HostFile(StrictModel):
    source: str
    target: str
    size: Annotated[int,Field(ge=0,le=268435456)]
    mode: Literal[0o400,0o500,0o444,0o555]
    sha256: Sha256


class _HostFiles(StrictModel):
    schema_version: Literal['p3-host-files-v1']
    files: Annotated[tuple[_HostFile,...],Field(min_length=1,max_length=MAX_CLOSURE_FILES)]


def read_official_profile(
    path: Path, expected_digest: str, *, job_id: str, payload: object,
    context: Mapping[str, str],
) -> tuple[OfficialHostProfile, CompleteP3Closure]:
    """Recheck root custody, current source/gates, qualified inputs and exact file bytes.

    The producer must authenticate the retained Job API observation. A real pinned
    startup sandbox probe is additionally required before database admission.
    """
    try:
        document,digest=read_protected_canonical_json_current(path)
    except Exception as error:
        raise ValueError('P3 profile requires protected root custody') from error
    if digest!=expected_digest:
        raise ValueError('P3 protected profile digest changed')
    profile=OfficialHostProfile.model_validate_json(canonical_json_bytes(document))
    _absolute(profile.worker_credentials_directory)
    validate_p3_job_id(job_id)
    if profile.job_id!=job_id or type(payload) is not AlphaCampaignPayload or profile.payload!=payload:
        raise ValueError('P3 protected profile belongs to another workflow job')
    source=SourceIdentity.model_validate(canonical_source_identity(ROOT))
    if source!=payload.expected_source:
        raise ValueError('P3 protected profile source is not current')
    status=derive_project_status(ROOT)
    if (status['gates']['HWC_SOURCE_READY']!='PASS' or status['gates']['PRE_P3_READY']!='PASS'
        or status['p3_alpha_development_allowed'] is not True):
        raise ValueError('P3 protected source gates are not current')
    store_root,output_root,python_root=map(_absolute,(profile.store_root,profile.output_root,profile.python_root))
    if (_directory_identity(store_root)==_directory_identity(output_root)
        or store_root in output_root.parents or output_root in store_root.parents):
        raise ValueError('P3 profile transport roots overlap')
    store=LocalArtifactStore(store_root)
    intent=_read(store,payload.manifest_ref,P3OperationInput)
    authorization=_read(store,payload.authorization_ref,RunAuthorization)
    expected_context=dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY='nam176hermes/Trading-Agent',
        GITHUB_REF='refs/heads/main',GITHUB_SHA=source.commit_sha,GITHUB_REF_PROTECTED='true',
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_RUN_ID=str(authorization.issuer_run_id),
        GITHUB_RUN_ATTEMPT=str(authorization.issuer_attempt),
        GITHUB_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main')
    if (authorization.issuer_workflow!='p3-authority.yml' or authorization.issuer_run_id<1
        or authorization.issuer_attempt<1 or any(context.get(k)!=v for k,v in expected_context.items())):
        raise ValueError('P3 protected profile differs from the current workflow')
    if stage_alpha_campaign_payload(ReadbackStore(store,store),authorization,source,payload.logical_trial_id,
        canonical_json_bytes(intent),operation_input=intent)!=payload:
        raise ValueError('P3 protected payload or review differs')
    inputs=_read(store,intent.input_set_ref,InputSet)
    environment=_read(store,profile.environment_ref,EnvironmentIdentity)
    if (inputs.source!=source or inputs.environment_ref!=profile.environment_ref
        or inputs.integration_receipt_ref!=profile.integration_receipt_ref or environment.python_version!='3.11'
        or environment.native_manifest_digest!=P1_REAL_BACKTEST_POLICY.closure_sha256
        or environment.sandbox_policy_digest!=SANDBOX_PROFILE_SHA256):
        raise ValueError('P3 profile environment, InputSet or qualification differs')
    validate_integration_job_result(profile.integration_receipt_ref,
        job_detail_ref=profile.qualification_job_detail_ref,source=source,
        postgres_binary_sha256=profile.postgres_binary_sha256,store=store)
    raw=_bounded_bytes(profile.inventory_ref,store,media_type='application/json',max_bytes=4194304)
    inventory=_HostFiles.model_validate_json(raw)
    if canonical_json_bytes(inventory)!=raw or sum(item.size for item in inventory.files)>MAX_CLOSURE_BYTES:
        raise ValueError('P3 profile inventory is not bounded canonical data')
    targets=tuple(PurePosixPath(_absolute(item.target)) for item in inventory.files)
    if targets!=tuple(sorted(set(targets))):
        raise ValueError('P3 profile inventory targets must be sorted and unique')
    release=_release_files(source)
    if hashlib.sha256(release[PurePosixPath('/p3/release/uv.lock')][1]).hexdigest()!=environment.root_lock_digest:
        raise ValueError('P3 profile dependency lock differs')
    inspect_python_runtime(python_root,require_empty_site_packages=False)
    runtime={PurePosixPath('/p3/python')/p.relative_to(python_root).as_posix():p
        for p in python_root.rglob('*') if not p.is_dir()}
    if ({p for p in targets if p.is_relative_to('/p3/release')}!=set(release)
        or {p for p in targets if p.is_relative_to('/p3/python')}!=set(runtime)):
        raise ValueError('P3 profile source or Python inventory is incomplete')
    mounts=[]
    for item,target in zip(inventory.files,targets,strict=True):
        if not any(target.is_relative_to(root) for root in ('/p3/release','/p3/python','/lib','/lib64','/usr/lib')):
            raise ValueError('P3 profile file mount role is invalid')
        file=_absolute(item.source);info=file.stat(follow_symlinks=False)
        identity=(info.st_dev,info.st_ino)
        raw=_file_bytes(file,identity,item.size,item.mode,item.sha256)
        if (target in release and (raw!=release[target][1] or bool(item.mode&0o111)!=(release[target][0]=='100755'))
            or target in runtime and file!=runtime[target]):
            raise ValueError('P3 profile bytes differ from source or Python root')
        mounts.append(P3ClosureMount(file,target,identity,item.size,item.mode,item.sha256))
    if not any(m.target==PurePosixPath('/p3/python/bin/python3.11') and m.mode in {0o500,0o555} for m in mounts):
        raise ValueError('P3 profile Python entrypoint is not executable')
    sandbox_path=_absolute(profile.sandbox_executable);info=sandbox_path.stat(follow_symlinks=False)
    identity=(info.st_dev,info.st_ino);mode=stat.S_IMODE(info.st_mode)
    if mode not in {0o500,0o555,0o755}:
        raise ValueError('P3 profile sandbox mode is invalid')
    _file_bytes(sandbox_path,identity,info.st_size,mode,profile.sandbox_sha256)
    sandbox=P3Sandbox(sandbox_path,identity,profile.sandbox_sha256,mode,
        SANDBOX_PROFILE_SHA256,BWRAP_VERSION,BWRAP_CAPABILITIES)
    fingerprint=_digest(dict(source=source,environment_ref=profile.environment_ref,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in mounts],
        sandbox_sha256=sandbox.executable_sha256,sandbox_policy_sha256=sandbox.profile_sha256))
    if fingerprint!=profile.closure_sha256:
        raise ValueError('P3 profile closure digest differs')
    if read_protected_canonical_json_current(path)[1]!=expected_digest:
        raise ValueError('P3 protected profile changed during readback')
    return profile,CompleteP3Closure(source,profile.environment_ref,python_root,tuple(mounts),sandbox,
        fingerprint,job_id,payload_fingerprint(payload))
