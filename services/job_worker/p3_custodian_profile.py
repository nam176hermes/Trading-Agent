"""Protected custodian admission and one connection; no service/key provisioning."""
from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import socket

from packages.alpha_lifecycle.contracts.authority import CustodyRecord,GithubRunIdentity,LocalRunIdentity,LocalRunAuthorization,RunAuthorization
from packages.alpha_lifecycle.authority import validate_local_operation_authority
from packages.alpha_lifecycle.contracts.base import Sha256,SourceIdentity,StrictModel
from packages.alpha_lifecycle.custody import ReleaseRequest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.pre_p3_provenance import canonical_source_identity
from packages.project_status import require_p3_source_ready
from packages.data_contracts import ArtifactRefV1
from packages.runtime_release.config import ProtectedAuthorityError,_absolute,read_protected_canonical_json_current
from services.job_store.p3_catalog import CUSTODIAN_CATALOG_SHA256, SESSION_CATALOG_SHA256
from services.job_store.p3_custodian_release import CustodianReleaseRepository
from .p3_holdout_release import CustodianEndpoint,_request_identity,serve_release
from .p3_spawn import ROOT,_directory_identity
from typing import Annotated,Literal
from pydantic import Field,TypeAdapter


class _CustodianProfileFields(StrictModel):
    request: ReleaseRequest
    endpoint: CustodianEndpoint
    metadata_root: str
    credentials_directory: str
    catalog_sha256: Sha256


class CustodianHostProfile(_CustodianProfileFields):
    schema_version: Literal['p3-custodian-host-profile-v1', 'p3-custodian-session-profile-v1']


class LocalCustodianProfile(_CustodianProfileFields):
    schema_version: Literal['p3-local-custodian-profile-v1']
    identity: LocalRunIdentity
    authorization_ref: ArtifactRefV1


class GithubCustodianProfile(_CustodianProfileFields):
    """Hosted session custody, bound to the API-attested run that owns the HOLDOUT."""
    schema_version: Literal['p3-github-custodian-profile-v1']
    identity: GithubRunIdentity
    authorization_ref: ArtifactRefV1


_SESSION_CUSTODY={'p3-custodian-session-profile-v1','p3-local-custodian-profile-v1','p3-github-custodian-profile-v1'}
CUSTODIAN_PROFILE=TypeAdapter(Annotated[CustodianHostProfile | LocalCustodianProfile | GithubCustodianProfile,
    Field(discriminator='schema_version')])


def read_custodian_profile(path: Path,expected_digest: str) -> CustodianHostProfile | LocalCustodianProfile | GithubCustodianProfile:
    try:
        document,digest=read_protected_canonical_json_current(path)
    except ProtectedAuthorityError as error:
        raise ValueError('custodian profile requires protected root custody') from error
    if digest!=expected_digest:raise ValueError('custodian profile digest changed')
    profile=CUSTODIAN_PROFILE.validate_json(canonical_json_bytes(document))
    _request_identity(profile.request,profile.endpoint)
    catalog = SESSION_CATALOG_SHA256 if profile.schema_version in _SESSION_CUSTODY else CUSTODIAN_CATALOG_SHA256
    if (profile.request.source!=SourceIdentity.model_validate(canonical_source_identity(ROOT))
        or profile.catalog_sha256!=catalog):
        raise ValueError('custodian source or catalog differs')
    require_p3_source_ready(ROOT,profile.identity if isinstance(profile,(LocalCustodianProfile,GithubCustodianProfile)) else None)
    _ = _absolute(profile.credentials_directory)
    _ = _directory_identity(_absolute(profile.metadata_root))
    if isinstance(profile,LocalCustodianProfile):
        from packages.alpha_lifecycle.replica_store import _read
        from packages.alpha_lifecycle.holdout import derive_holdout_request
        authorization=_read(LocalArtifactStore(Path(profile.metadata_root)),profile.authorization_ref,LocalRunAuthorization)
        if (authorization.identity!=profile.identity or authorization.operation!='HOLDOUT'
            or profile.authorization_ref.content_sha256!=profile.request.authorization_digest):
            raise ValueError('local custodian operation issuer or digest differs')
        admitted=validate_local_operation_authority(authorization,profile.request.source,root=ROOT)
        intent=admitted.operation_input
        if (intent is None or intent.digest!=profile.request.intent_digest
            or hashlib.sha256(canonical_json_bytes(derive_holdout_request(intent,authorization,
                expected_source=profile.request.source))).hexdigest()!=profile.request.holdout_request_sha256):
            raise ValueError('local custodian request differs from protected operation')
    if isinstance(profile,GithubCustodianProfile):
        # Custodian release is an admission point: the run must be freshly attested and the
        # run-published request must still be the reviewed HOLDOUT this profile names.
        from packages.alpha_lifecycle.replica_store import _read
        from packages.alpha_lifecycle.holdout import derive_holdout_request
        from packages.alpha_lifecycle.authority import AuthorityHeld, validate_request
        from packages.alpha_lifecycle.github_run import github_run_directory
        authorization=_read(LocalArtifactStore(Path(profile.metadata_root)),profile.authorization_ref,RunAuthorization)
        identity=profile.identity
        if ((authorization.issuer_run_id,authorization.issuer_attempt)!=(identity.run_id,identity.attempt)
            or authorization.operation!='HOLDOUT'
            or profile.authorization_ref.content_sha256!=profile.request.authorization_digest):
            raise ValueError('hosted custodian operation run or digest differs')
        try:
            admitted=validate_request(github_run_directory(identity.run_id,identity.attempt)/'operations'/
                f'{authorization.nonce}.json',profile.request.source,'HOLDOUT')
        except AuthorityHeld as error:
            raise ValueError('hosted custodian operation is not currently admitted') from error
        intent=admitted.operation_input
        if (admitted.authorization!=authorization or intent is None or intent.digest!=profile.request.intent_digest
            or hashlib.sha256(canonical_json_bytes(derive_holdout_request(intent,authorization,
                expected_source=profile.request.source))).hexdigest()!=profile.request.holdout_request_sha256):
            raise ValueError('hosted custodian request differs from protected operation')
    if read_protected_canonical_json_current(path)[1]!=expected_digest:
        raise ValueError('custodian profile changed during admission')
    return profile


def serve_profiled_release(channel: socket.socket,*,profile_path: Path,profile_digest: str,
    read_plaintext: Callable[[CustodyRecord],bytes],
) -> None:
    profile=read_custodian_profile(profile_path,profile_digest)
    if os.geteuid()!=profile.endpoint.custodian_uid:
        raise ValueError('unapproved custodian UID')
    root=Path(profile.metadata_root)
    identity=_directory_identity(root)
    def recheck() -> CustodianEndpoint:
        if (read_custodian_profile(profile_path,profile_digest)!=profile
            or _directory_identity(root)!=identity):
            raise ValueError('custodian profile or metadata root changed')
        return profile.endpoint
    repository=CustodianReleaseRepository.from_systemd_credentials(
        {'CREDENTIALS_DIRECTORY':profile.credentials_directory},
        session=profile.schema_version in _SESSION_CUSTODY)
    store=LocalArtifactStore(root)
    io_deadline=None
    if isinstance(profile,LocalCustodianProfile):
        from packages.alpha_lifecycle.replica_store import _read
        io_deadline=_read(store,profile.authorization_ref,LocalRunAuthorization).expires_at
    elif isinstance(profile,GithubCustodianProfile):
        from packages.alpha_lifecycle.replica_store import _read
        io_deadline=_read(store,profile.authorization_ref,RunAuthorization).expires_at
    serve_release(channel,profile.endpoint,store,repository=repository,
        request_sha256=hashlib.sha256(canonical_json_bytes(profile.request)).hexdigest(),
        read_plaintext=read_plaintext,recheck_profile=recheck,io_deadline=io_deadline)
