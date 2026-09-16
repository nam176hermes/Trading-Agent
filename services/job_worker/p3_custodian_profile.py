"""Protected custodian admission and one connection; no service/key provisioning."""
from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import socket

from packages.alpha_lifecycle.contracts.authority import CustodyRecord
from packages.alpha_lifecycle.contracts.base import Sha256,SourceIdentity,StrictModel
from packages.alpha_lifecycle.custody import ReleaseRequest
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.pre_p3_provenance import canonical_source_identity
from packages.project_status import derive_project_status
from packages.runtime_release.config import _absolute,read_protected_canonical_json_current
from services.job_store.p3_catalog import CUSTODIAN_CATALOG_SHA256
from services.job_store.p3_custodian_release import CustodianReleaseRepository
from .p3_holdout_release import CustodianEndpoint,_request_identity,serve_release
from .p3_spawn import ROOT,_directory_identity
from typing import Literal


class CustodianHostProfile(StrictModel):
    schema_version: Literal['p3-custodian-host-profile-v1']
    request: ReleaseRequest
    endpoint: CustodianEndpoint
    metadata_root: str
    credentials_directory: str
    catalog_sha256: Sha256


def read_custodian_profile(path: Path,expected_digest: str) -> CustodianHostProfile:
    try:
        document,digest=read_protected_canonical_json_current(path)
    except Exception as error:
        raise ValueError('custodian profile requires protected root custody') from error
    if digest!=expected_digest:raise ValueError('custodian profile digest changed')
    profile=CustodianHostProfile.model_validate_json(canonical_json_bytes(document))
    _request_identity(profile.request,profile.endpoint)
    if (profile.request.source!=SourceIdentity.model_validate(canonical_source_identity(ROOT))
        or profile.catalog_sha256!=CUSTODIAN_CATALOG_SHA256):
        raise ValueError('custodian source or catalog differs')
    status=derive_project_status(ROOT)
    if (status['gates']['HWC_SOURCE_READY']!='PASS' or status['gates']['PRE_P3_READY']!='PASS'
        or status['p3_alpha_development_allowed'] is not True):
        raise ValueError('custodian source gates are not current')
    _ = _absolute(profile.credentials_directory)
    _ = _directory_identity(_absolute(profile.metadata_root))
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
        {'CREDENTIALS_DIRECTORY':profile.credentials_directory})
    serve_release(channel,profile.endpoint,LocalArtifactStore(root),repository=repository,
        request_sha256=hashlib.sha256(canonical_json_bytes(profile.request)).hexdigest(),
        read_plaintext=read_plaintext,recheck_profile=recheck)
