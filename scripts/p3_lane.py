"""Issuer-lane dispatch for the root host controller (LOCAL or GITHUB).

Each root step first re-admits the protected operation request of its own lane, derives
the run directory from the authenticated authorization, and serializes publication under
that lane's issuer lock. Nothing here grants authority; it routes to the lane's validators.
"""
from __future__ import annotations

from pathlib import Path

from packages.alpha_lifecycle.contracts.authority import GithubRunIdentity, LocalRunAuthorization, RunAuthorization
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.engine_contracts.serialization import canonical_json_bytes

GITHUB_AUTHORITY_ROOT = Path('/etc/trading-agent-p3/github/authority')
_LOCAL_ROOT = Path('/etc/trading-agent/local-qualification')


def _committed_source() -> SourceIdentity:
    from scripts.seal_p3_dataset import _committed_source as committed
    return SourceIdentity.model_validate(committed())


def _protected_document(path: Path):
    from packages.runtime_release.config import read_protected_canonical_json_current
    document, _ = read_protected_canonical_json_current(path)
    return document


def admitted_operation(request_path: Path, *, source: SourceIdentity | None = None):
    """Return (request, identity, run directory) for a current admitted official request."""
    from packages.alpha_lifecycle.authority import (
        _LocalRequestFile, _RequestFile, local_run_directory, validate_local_operation_authority, validate_request,
    )
    from packages.alpha_lifecycle.github_run import github_run_directory, github_run_identity
    source = _committed_source() if source is None else source
    document = _protected_document(request_path)
    if request_path.is_relative_to(_LOCAL_ROOT):
        request = _LocalRequestFile.model_validate_json(canonical_json_bytes(document))
        if (not isinstance(request.authorization, LocalRunAuthorization)
                or validate_local_operation_authority(request.authorization, source) != request):
            raise ValueError('LOCAL request is not the current admitted operation')
        identity = request.authorization.identity
        return request, identity, local_run_directory(identity)
    request = _RequestFile.model_validate_json(canonical_json_bytes(document))
    if (not isinstance(request.authorization, RunAuthorization)
            or validate_request(request_path, source, request.authorization.operation) != request):
        raise ValueError('GITHUB request is not the current admitted operation')
    identity = github_run_identity(request.authorization)
    return request, identity, github_run_directory(identity.run_id, identity.attempt)


def issuer_lock(identity):
    """The lane's single publication lock; GITHUB uses its own store, never the LOCAL one."""
    from packages.local_authority import AuthorityStore
    if isinstance(identity, GithubRunIdentity):
        return AuthorityStore(identity.issuer_identity, root=GITHUB_AUTHORITY_ROOT).locked()
    return AuthorityStore(identity.issuer_identity).locked()


def issuer_kind(identity) -> str:
    return 'GITHUB' if isinstance(identity, GithubRunIdentity) else 'LOCAL'


def authorization_identity(authorization):
    """Run identity of an admitted authorization; GITHUB derives it from the issuer run."""
    from packages.alpha_lifecycle.github_run import github_run_identity
    if isinstance(authorization, LocalRunAuthorization):
        return authorization.identity
    return github_run_identity(authorization)


def transport_identity(identity, host_id: str, boot_id: str) -> dict:
    """The identity a transport request carries; GITHUB adds the attested host and boot."""
    value = identity.model_dump(mode='json')
    if isinstance(identity, GithubRunIdentity):
        value.update(host_id=host_id, boot_id=boot_id)
    return value


def identity_from_transport(value: dict):
    """Inverse of transport_identity; strict models reject anything else."""
    from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
    if value.get('issuer_kind') == 'GITHUB':
        fields = {key: value[key] for key in ('issuer_kind', 'issuer_identity', 'repository', 'workflow_ref',
                                              'run_id', 'attempt')}
        return GithubRunIdentity.model_validate_json(canonical_json_bytes(fields))
    return LocalRunIdentity.model_validate_json(canonical_json_bytes(value))


def run_directory(identity) -> Path:
    from packages.alpha_lifecycle.authority import local_run_directory
    from packages.alpha_lifecycle.github_run import github_run_directory
    if isinstance(identity, GithubRunIdentity):
        return github_run_directory(identity.run_id, identity.attempt)
    return local_run_directory(identity)


__all__ = ['GITHUB_AUTHORITY_ROOT', 'admitted_operation', 'identity_from_transport', 'issuer_kind',
           'issuer_lock', 'run_directory', 'transport_identity']
