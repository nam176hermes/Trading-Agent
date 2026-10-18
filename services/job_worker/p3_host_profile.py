"""Read a protected per-job profile; this consumer never provisions host authority."""
from collections.abc import Mapping
from pathlib import Path,PurePosixPath
import hashlib
import stat
from typing import Annotated,Literal

from pydantic import Field,TypeAdapter,model_validator

from packages.alpha_lifecycle.authority import stage_alpha_campaign_payload, validate_operation_issuer, AuthorityHeld
from packages.alpha_lifecycle.baseline_campaign import ReadbackStore,_read
from packages.alpha_lifecycle.contracts.authority import RUN_AUTHORIZATION, LocalRunAuthorization, LocalRunIdentity
from packages.alpha_lifecycle.contracts.base import Sha256,StrictModel,SourceIdentity
from packages.alpha_lifecycle.contracts.execution import EnvironmentIdentity,InputSet
from packages.alpha_lifecycle.operation_input import P3OperationInput
from packages.alpha_lifecycle.sandbox_policy import (
    BWRAP_CAPABILITIES,BWRAP_VERSION,IMAGE_SCHEMA_VERSION,MAX_CLOSURE_BYTES,MAX_CLOSURE_FILES,
    MAX_EXTERNAL_LIBRARIES,MAX_EXTERNAL_LIBRARY_BYTES,MAX_EXTERNAL_LIBRARY_TOTAL_BYTES,
    MAX_IMAGE_BYTES,SANDBOX_PROFILE_SHA256,SANDBOX_PROFILE_V2_SHA256,
)
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts import canonical_json_bytes
from packages.job_contracts import AlphaCampaignPayload,payload_fingerprint
from packages.pre_p3_provenance import canonical_source_identity
from packages.project_status import require_p3_source_ready
from packages.runtime_release.config import read_protected_canonical_json_current,_absolute
from services.job_store.records import validate_p3_job_id
from .engine_profiles import P1_REAL_BACKTEST_POLICY
from .p3_holdout_release import CustodianEndpoint
from .p3_qualification_readback import validate_integration_job_result,_read as _bounded_bytes
from .p3_spawn import (
    verify_image_mount,
    ROOT,CompleteP3Closure,P3ClosureMount,P3Sandbox,_digest,_directory_identity,_file_bytes,
    _release_files,inspect_python_runtime,
)


class _OfficialHostProfileFields(StrictModel):
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
    def _custody(self) -> '_OfficialHostProfileFields':
        if (self.payload.operation=='HOLDOUT')!=(self.custodian_endpoint is not None):
            raise ValueError('custodian endpoint is required exclusively for HOLDOUT')
        return self


class P3ExternalLibrary(StrictModel):
    """One allowlisted host library delivered as a sealed snapshot, not a directory bind.

    `sha256` is the digest of the sealed bytes and is the only byte authority. `source_dev`
    and `source_ino` record where the snapshot was taken from; they are provenance, because
    an inode number says nothing about the bytes it holds later.
    """
    target: Annotated[str, Field(min_length=1,max_length=256,pattern=r'^/[A-Za-z0-9._/+-]+$')]
    mode: Literal[0o400,0o500,0o444,0o555]
    size_bytes: Annotated[int, Field(ge=1,le=MAX_EXTERNAL_LIBRARY_BYTES)]
    sha256: Sha256
    source_dev: Annotated[int, Field(ge=0)]
    source_ino: Annotated[int, Field(ge=1)]


class P3RuntimeImageDescriptor(StrictModel):
    """Durable identity of one LOCAL v2 runtime image.

    Deliberately excludes mount identity. Backing inode, loop association, mount generation
    and controller identity are properties of a single run and are checked at the admission
    and replica fences; folding them into this digest would make the artifact unusable
    across the very fences that verify it.
    """
    # Spelled out because a Literal parameterised by a name is not a literal type. The
    # constant is still the authority: the assertion below refuses to load if they part.
    schema_version: Literal['p3-local-runtime-image-v1']
    image_sha256: Sha256
    image_size_bytes: Annotated[int, Field(ge=1,le=MAX_IMAGE_BYTES)]
    entry_count: Annotated[int, Field(ge=1,le=MAX_CLOSURE_FILES)]
    logical_bytes: Annotated[int, Field(ge=1,le=MAX_CLOSURE_BYTES)]
    closure_fingerprint: Sha256
    inventory_ref: ArtifactRefV1
    toolchain_manifest_sha256: Sha256
    worker_uid: Annotated[int, Field(ge=1,le=65533)]
    worker_gid: Annotated[int, Field(ge=1,le=65533)]
    release_root: Annotated[str, Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9._-]+$')]
    python_root: Annotated[str, Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9._-]+$')]
    external_libraries: tuple[P3ExternalLibrary, ...]

    @model_validator(mode='after')
    def _bounds(self) -> 'P3RuntimeImageDescriptor':
        libraries = self.external_libraries
        if not 0 < len(libraries) <= MAX_EXTERNAL_LIBRARIES:
            raise ValueError('P3 image external library count is outside the accepted bound')
        targets = [library.target for library in libraries]
        if len(set(targets)) != len(targets) or targets != sorted(targets):
            raise ValueError('P3 image external libraries must be unique and ordered')
        if sum(library.size_bytes for library in libraries) > MAX_EXTERNAL_LIBRARY_TOTAL_BYTES:
            raise ValueError('P3 image external libraries exceed the accepted aggregate bound')
        if self.release_root == self.python_root:
            raise ValueError('P3 image release and Python roots must differ')
        return self


class LocalOfficialHostProfileLane(_OfficialHostProfileFields):
    """Everything the LOCAL lane shares, whatever transport it carries.

    Lane predicates test this base, so a v2 profile answers "is this LOCAL?" the same way a
    v1 profile does. Transport and version questions read the concrete class or the
    discriminator instead; a lane base must never be used to answer those.
    """
    identity: LocalRunIdentity


class OfficialHostProfile(_OfficialHostProfileFields):
    schema_version: Literal['p3-official-host-profile-v1']


class LocalOfficialHostProfile(LocalOfficialHostProfileLane):
    schema_version: Literal['p3-local-official-host-profile-v1']


class LocalOfficialHostProfileV2(LocalOfficialHostProfileLane):
    schema_version: Literal['p3-local-official-host-profile-v2']
    image: P3RuntimeImageDescriptor


if P3RuntimeImageDescriptor.model_fields['schema_version'].annotation != Literal[IMAGE_SCHEMA_VERSION]:
    raise RuntimeError('P3 image descriptor schema version differs from the pinned constant')

ANY_OFFICIAL_PROFILE = OfficialHostProfile | LocalOfficialHostProfile | LocalOfficialHostProfileV2
# Typed explicitly: inferred through Annotated, validate_json loses the union and every
# isinstance narrowing downstream degrades to Unknown.
OFFICIAL_PROFILE: TypeAdapter[ANY_OFFICIAL_PROFILE] = TypeAdapter(Annotated[
    ANY_OFFICIAL_PROFILE, Field(discriminator='schema_version')])


class _HostFile(StrictModel):
    source: str
    target: str
    size: Annotated[int,Field(ge=0,le=268435456)]
    mode: Literal[0o400,0o500,0o444,0o555]
    sha256: Sha256


class _HostFiles(StrictModel):
    schema_version: Literal['p3-host-files-v1']
    files: Annotated[tuple[_HostFile,...],Field(min_length=1,max_length=MAX_CLOSURE_FILES)]


_RELEASE_ROOT = PurePosixPath('/p3/release')
_PYTHON_ROOT = PurePosixPath('/p3/python')


def _transport_policy(profile) -> str:
    """The sandbox policy identity that belongs to this profile's transport.

    Read from the concrete class, never from the lane base: this is a version question,
    and answering it from the lane would let a v2 profile present the v1 identity.
    """
    return SANDBOX_PROFILE_V2_SHA256 if type(profile) is LocalOfficialHostProfileV2 else SANDBOX_PROFILE_SHA256


def _verify_image_transport(profile, python_root: Path, mounts):
    """Bind the image descriptor to the inventory this profile actually declares.

    Returns (None, None) on the sealed-file transport. On v2 the mount root is derived from
    the verified Python root rather than supplied separately, so the interpreter that was
    just read back and the roots that get bound cannot come from different places.
    """
    if type(profile) is not LocalOfficialHostProfileV2:
        return None,None
    image=profile.image
    if python_root.name!=image.python_root:
        raise ValueError('P3 image Python root does not match the descriptor')
    mount=python_root.parent
    # The mount capability is checked here, not only at spawn. Without it the readback would
    # authenticate bytes that happened to sit in an ordinary directory and hand the launcher
    # a root nobody had shown to be the verified read-only image.
    verify_image_mount(mount)
    release_root=mount/image.release_root
    if not release_root.is_dir() or release_root==python_root:
        raise ValueError('P3 image release root is missing or collides with the Python root')
    device=mount.stat().st_dev
    imaged=[m for m in mounts
            if m.target.is_relative_to(_RELEASE_ROOT) or m.target.is_relative_to(_PYTHON_ROOT)]
    if len(imaged)!=image.entry_count or sum(m.size for m in imaged)!=image.logical_bytes:
        raise ValueError('P3 image descriptor does not match the declared closure inventory')
    # Every imaged entry has to have been read back from the aggregate root that will be
    # bound, and from the device that root sits on. Otherwise the bytes could be verified
    # from one place and a different release directory bound in their name.
    for item in imaged:
        root=release_root if item.target.is_relative_to(_RELEASE_ROOT) else python_root
        if not item.source.is_relative_to(root) or item.source.stat().st_dev!=device:
            raise ValueError('P3 image closure entry was not read back from the bound image root')
    fingerprint=_digest(dict(schema_version=IMAGE_SCHEMA_VERSION,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in imaged]))
    if fingerprint!=image.closure_fingerprint:
        raise ValueError('P3 image closure fingerprint differs from the declared inventory')
    imaged_targets={m.target for m in imaged}
    external={m.target:m for m in mounts if m.target not in imaged_targets}
    declared={PurePosixPath(library.target):library for library in image.external_libraries}
    if set(declared)!=set(external):
        raise ValueError('P3 image external library set differs from the declared inventory')
    # The target set alone says which libraries are delivered, not which bytes. Each declared
    # field is compared with the entry that was actually read back, so a library whose digest,
    # size, mode or snapshot inode differs from the descriptor cannot be carried along under
    # an accepted name.
    for target,library in declared.items():
        item=external[target]
        if (library.sha256!=item.sha256 or library.size_bytes!=item.size
            or library.mode!=item.mode or (library.source_dev,library.source_ino)!=item.identity):
            raise ValueError(f'P3 external library {target} differs from the declared descriptor')
    return image,mount


def read_official_profile(
    path: Path, expected_digest: str, *, job_id: str, payload: object,
    admission: bool,
) -> tuple[OfficialHostProfile | LocalOfficialHostProfile | LocalOfficialHostProfileV2,
           CompleteP3Closure]:
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
    profile=OFFICIAL_PROFILE.validate_json(canonical_json_bytes(document))
    # The discriminated union admits exactly these three, and the transport and lane
    # questions below read the concrete class. Stating that here keeps the returned type
    # the one the union defines rather than the shared lane base a reader could assume.
    if type(profile) not in (OfficialHostProfile,LocalOfficialHostProfile,LocalOfficialHostProfileV2):
        raise ValueError('P3 protected profile is not one of the admitted profile versions')
    _absolute(profile.worker_credentials_directory)
    validate_p3_job_id(job_id)
    if profile.job_id!=job_id or type(payload) is not AlphaCampaignPayload or profile.payload!=payload:
        raise ValueError('P3 protected profile belongs to another workflow job')
    source=SourceIdentity.model_validate(canonical_source_identity(ROOT))
    if source!=payload.expected_source:
        raise ValueError('P3 protected profile source is not current')
    # LOCAL issuer admission below already authenticates current prerequisites
    # against the exact authorization identity, source and expiry. GITHUB issuer
    # validation only checks workflow context and still needs this source gate.
    if not isinstance(profile,LocalOfficialHostProfileLane):
        require_p3_source_ready(ROOT)
    store_root,output_root,python_root=map(_absolute,(profile.store_root,profile.output_root,profile.python_root))
    if (_directory_identity(store_root)==_directory_identity(output_root)
        or store_root in output_root.parents or output_root in store_root.parents):
        raise ValueError('P3 profile transport roots overlap')
    store=LocalArtifactStore(store_root)
    intent=_read(store,payload.manifest_ref,P3OperationInput)
    authorization=_read(store,payload.authorization_ref,RUN_AUTHORIZATION)
    if (isinstance(profile,LocalOfficialHostProfileLane) != isinstance(authorization,LocalRunAuthorization)
        or isinstance(profile,LocalOfficialHostProfileLane) and profile.identity != getattr(authorization,'identity',None)):
        raise ValueError('P3 protected profile and operation issuer differ')
    try:
        validate_operation_issuer(authorization,source,admission=admission,root=ROOT)
    except AuthorityHeld as error:
        raise ValueError('P3 protected profile differs from the current workflow or local authority') from error
    if stage_alpha_campaign_payload(ReadbackStore(store,store),authorization,source,payload.logical_trial_id,
        canonical_json_bytes(intent),operation_input=intent)!=payload:
        raise ValueError('P3 protected payload or review differs')
    inputs=_read(store,intent.input_set_ref,InputSet)
    environment=_read(store,profile.environment_ref,EnvironmentIdentity)
    if (inputs.source!=source or inputs.environment_ref!=profile.environment_ref
        or inputs.integration_receipt_ref!=profile.integration_receipt_ref or environment.python_version!='3.11'
        or environment.native_manifest_digest!=P1_REAL_BACKTEST_POLICY.closure_sha256
        or environment.sandbox_policy_digest!=_transport_policy(profile)):
        raise ValueError('P3 profile environment, InputSet or qualification differs')
    integration=validate_integration_job_result(profile.integration_receipt_ref,
        job_detail_ref=profile.qualification_job_detail_ref,source=source,
        postgres_binary_sha256=profile.postgres_binary_sha256,store=store)
    if not isinstance(profile,LocalOfficialHostProfileLane) and getattr(integration,'identity',None) is not None:
        raise ValueError('P3 GitHub profile cannot use LOCAL integration qualification')
    if isinstance(profile,LocalOfficialHostProfileLane) and getattr(integration,'identity',None) != profile.identity:
        raise ValueError('P3 local profile and integration qualification issuer differ')
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
        _transport_policy(profile),BWRAP_VERSION,BWRAP_CAPABILITIES)
    fingerprint=_digest(dict(source=source,environment_ref=profile.environment_ref,
        files=[dict(target=str(m.target),sha256=m.sha256,size=m.size,mode=m.mode) for m in mounts],
        sandbox_sha256=sandbox.executable_sha256,sandbox_policy_sha256=sandbox.profile_sha256))
    if fingerprint!=profile.closure_sha256:
        raise ValueError('P3 profile closure digest differs')
    if read_protected_canonical_json_current(path)[1]!=expected_digest:
        raise ValueError('P3 protected profile changed during readback')
    image,image_mount=_verify_image_transport(profile,python_root,mounts)
    return profile,CompleteP3Closure(source,profile.environment_ref,python_root,tuple(mounts),sandbox,
        fingerprint,job_id,payload_fingerprint(payload),image,image_mount)
