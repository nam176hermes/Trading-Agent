"""Bind a complete sealed LOCAL source runtime to independent host approval."""
from collections.abc import Callable
from datetime import UTC,datetime
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Annotated,Literal

from pydantic import Field
from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.alpha_lifecycle.contracts.base import StrictModel,SourceIdentity,Sha256,Text
from packages.alpha_lifecycle.local_qualification import _host_identity
from packages.engine_contracts.serialization import CanonicalUtcDateTime,canonical_json_bytes
from packages.pre_p3_provenance import canonical_source_identity,_git
from packages.runtime_release.config import read_protected_canonical_json_current,_absolute,ProtectedAuthorityError

ROOT=Path(__file__).resolve().parents[2]
PROFILE_ROOT=Path('/etc/trading-agent/local-qualification')


class LocalRuntimeProfile(StrictModel):
    schema_version: Literal['p3-local-runtime-profile-v1']
    identity: LocalRunIdentity
    source: SourceIdentity
    application_root: Text
    application_python: Text
    uid: Annotated[int,Field(ge=1)]
    static_authority_sha256: Sha256
    package6_approval_sha256: Sha256
    issued_at: CanonicalUtcDateTime
    expires_at: CanonicalUtcDateTime
    revoked: bool


def validate_runtime_binding(profile,runtime,authorization,source):
    """The caller authenticates profile custody and invokes the existing attester."""
    now=datetime.now(UTC)
    material=runtime._material
    if (material is None or runtime.scope!='PACKAGE6_STAGING_ONLY' or profile.revoked
        or profile.identity!=authorization.identity or profile.source!=source
        or _host_identity()!=(profile.identity.host_id,profile.identity.boot_id)
        or profile.uid!=os.geteuid()
        or _absolute(profile.application_root)!=ROOT or runtime.application_root!=ROOT
        or _absolute(profile.application_python)!=Path(sys.executable)
        or runtime.application_python!=Path(sys.executable)
        or (runtime.source_commit,runtime.source_tree)!=(source.commit_sha,source.tree_sha)
        or material.authority_sha256!=profile.static_authority_sha256
        or runtime.package6_approval_sha256!=profile.package6_approval_sha256
        or not material.authority_generated_at<=profile.issued_at<=now<authorization.expires_at<=profile.expires_at<=material.authority_expires_at
        or _git(ROOT,'status','--porcelain','--untracked-files=all')
        or SourceIdentity.model_validate(canonical_source_identity(ROOT))!=source):
        raise ValueError('LOCAL runtime differs from protected source, host or validity binding')


def require_local_runtime(authority,identity,source,expires_at):
    path=PROFILE_ROOT/identity.issuer_identity/'runtime.json'
    document,digest=read_protected_canonical_json_current(path)
    profile=LocalRuntimeProfile.model_validate_json(canonical_json_bytes(document))
    validate_runtime_binding(profile,authority.runtime_authority,
        SimpleNamespace(identity=identity,expires_at=expires_at),source)
    if read_protected_canonical_json_current(path)[1]!=digest:
        raise ValueError('LOCAL runtime approval changed during startup')
    return digest


def local_runtime_guard(authority,identity,source,expires_at):
    """Pin once, then revalidate through the worker's existing safety fences."""
    pinned=require_local_runtime(authority,identity,source,expires_at)
    def check(*,deadline=expires_at):
        from .errors import SafetyBlockedError
        try:
            if require_local_runtime(authority,identity,source,deadline)!=pinned:
                raise ValueError('runtime profile replaced')
        except (OSError,ValueError,ProtectedAuthorityError) as error:
            raise SafetyBlockedError('SAFETY_AUTHORITY_CHANGED',
                'LOCAL runtime approval changed or is unavailable') from error
    return check


class LocalRuntimeSafetyPreflight:
    """Complete static attestation before producing the unchanged six-second proof."""

    def __init__(self,authority,identity,source,expires_at,*,refresh_dynamic_evidence: Callable[[], None]):
        if not callable(refresh_dynamic_evidence):
            raise TypeError('LOCAL safety refresh requires its host producer')
        self._initial_authority=authority
        self._current=authority
        self._refresh=refresh_dynamic_evidence
        self._guard=local_runtime_guard(authority,identity,source,expires_at)

    def matches_authority(self,authority):
        return self._initial_authority is authority

    def __call__(self):
        from packages.runtime_release.config import SafetyAuthority,refresh_runtime_authority_v2
        from .command_registry import refresh_staging_worker_runtime_authority
        from .safety_state import AuthorityBoundSafetyPreflight,SafetyStateClient,validate_current_safety_evidence
        from .errors import SafetyBlockedError
        try:
            self._guard()
            current=refresh_staging_worker_runtime_authority(self._current,
                refresh_dynamic_evidence=self._refresh)
            self._current=current
            safety=current.runtime_authority.safety
            if not isinstance(safety,SafetyAuthority):
                raise ValueError('LOCAL runtime safety authority is invalid')
            client=SafetyStateClient(current.safety_snapshot_path,
                expected_exporter_commit=current.safety_exporter_commit,
                expected_source_fingerprint=current.safety_source_fingerprint,
                expected_owner_uid=safety.expected_owner_uid,
                protected_root_owned=safety.protected_root_owned)
            evidence=AuthorityBoundSafetyPreflight(current,client,
                authority_loader=lambda:refresh_runtime_authority_v2(current.runtime_authority))()
            self._guard()
            return validate_current_safety_evidence(evidence,datetime.now(UTC))
        except SafetyBlockedError:
            raise
        except Exception as error:
            raise SafetyBlockedError('SAFETY_AUTHORITY_CHANGED',
                'LOCAL static or dynamic authority changed during refresh') from error
