"""Protected admission for the official research InputSet of a LOCAL campaign.

`P3_RESEARCH_INPUTS` gates one campaign InputSet. It is deliberately not part of
the fixed Pre-P3 prerequisite set: Pre-P3 qualifies the source, this qualifies the
data that source is about to consume, and folding one into the other would create
a cycle.

The expected source, policy digest, InputSet reference and environment reference
are supplied by the caller from the operation currently being admitted. Nothing
here trusts a value the profile declares about itself, and nothing here accepts an
authority path from the environment: the protected path is derived from the
already-authenticated issuer identity inside `validate_local_qualification`.

This returns a structural receipt, never a lease. Every admission boundary — SQL
acceptance, enqueue, worker startup, custodian release — must call again.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import model_validator

from packages.alpha_lifecycle.contracts.base import StrictModel, Text
from packages.alpha_lifecycle.contracts.authority import (
    LocalRunIdentity,
    QualificationBinding,
    QualificationReceipt,
)
from packages.alpha_lifecycle.local_qualification import (
    LocalQualificationError,
    _host_identity,
    validate_local_qualification,
)
from packages.alpha_lifecycle.replica_store import ArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


_RESEARCH_ROOT = Path('/etc/trading-agent/local-research')
_SAFE_ISSUER = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')
RESEARCH_INPUTS_GATE = 'P3_RESEARCH_INPUTS'


def _now() -> datetime:
    return datetime.now(UTC)


def require_research_inputs_qualified(
    identity: LocalRunIdentity,
    *,
    source,
    policy_digest: str,
    input_set_ref: ArtifactRefV1,
    environment_ref: ArtifactRefV1,
    store: ArtifactStore,
) -> QualificationReceipt:
    """Authenticate `P3_RESEARCH_INPUTS` for exactly this operation's InputSet.

    Raises `LocalQualificationError` or `ValueError`; never returns on doubt. A
    receipt for a different InputSet, source, policy or environment is rejected
    even when its own approval is current, because the binding is rebuilt here
    from the operation under admission rather than read from the profile.
    """
    identity = LocalRunIdentity.model_validate_json(_canonical(identity, LocalRunIdentity))
    if identity.issuer_kind != 'LOCAL':
        raise LocalQualificationError(
            'E_LOCAL_LANE', 'research inputs admission accepts only the LOCAL lane')
    binding = QualificationBinding.model_validate(dict(
        gate=RESEARCH_INPUTS_GATE,
        source=source,
        policy_digest=policy_digest,
        input_ref=_ref(input_set_ref),
        environment_ref=_ref(environment_ref),
    ))
    receipt = validate_local_qualification(identity, binding, store=store)
    if receipt.binding.gate != RESEARCH_INPUTS_GATE:
        raise LocalQualificationError(
            'E_LOCAL_BINDING', 'research inputs receipt gate differs')
    if receipt.binding.input_ref != binding.input_ref:
        raise LocalQualificationError(
            'E_LOCAL_INPUT', 'research inputs receipt binds a different InputSet')
    if receipt.identity != identity:
        raise LocalQualificationError(
            'E_LOCAL_IDENTITY', 'research inputs receipt identity differs')
    if _host_identity() != (identity.host_id, identity.boot_id):
        raise LocalQualificationError(
            'E_LOCAL_HOST', 'research inputs host or boot changed during admission')
    if not receipt.completed_at <= _now() < receipt.expires_at:
        raise LocalQualificationError(
            'E_LOCAL_RECEIPT_EXPIRED', 'research inputs receipt is not current')
    return receipt


def _canonical(value, model) -> bytes:
    from packages.engine_contracts.serialization import canonical_json_bytes
    if isinstance(value, model):
        return canonical_json_bytes(value)
    return canonical_json_bytes(value)


def _ref(value) -> ArtifactRefV1:
    if isinstance(value, ArtifactRefV1):
        return value
    return ArtifactRefV1.model_validate(value)


class LocalResearchProfile(StrictModel):
    """Protected research profile; its path derives from the authenticated issuer.

    The profile declares where its proof store lives and which commitment and
    backup receipt back the InputSet. It never declares which InputSet is being
    admitted: that comes from the operation, so a stale profile cannot certify a
    campaign it was not issued for.
    """
    schema_version: Literal['local-research-profile-v1']
    identity: LocalRunIdentity
    store_root: Text
    binding: QualificationBinding
    commitment_ref: ArtifactRefV1
    backup_receipt_ref: ArtifactRefV1

    @model_validator(mode='after')
    def _gate(self):
        if self.binding.gate != RESEARCH_INPUTS_GATE:
            raise ValueError('local research profile binds the wrong gate')
        if self.identity.issuer_kind != 'LOCAL':
            raise ValueError('local research profile accepts only the LOCAL lane')
        return self


def research_profile_path(identity: LocalRunIdentity) -> Path:
    """Derive the protected path from the authenticated issuer, never from env."""
    issuer = LocalRunIdentity.model_validate(identity).issuer_identity
    if not _SAFE_ISSUER.fullmatch(issuer):
        raise ValueError('local research issuer identity is not a safe path segment')
    return _RESEARCH_ROOT / issuer / 'research-inputs.json'


def read_local_research_inputs(
    identity: LocalRunIdentity,
    *,
    source,
    policy_digest: str,
    input_set_ref: ArtifactRefV1,
) -> QualificationReceipt:
    """Admit one operation's InputSet through current protected research custody.

    Reads the protected profile, derives its proof store, then rebuilds the
    expected binding from the operation under admission. The profile is re-read
    afterwards so a withdrawal during admission is detected.
    """
    from packages.alpha_lifecycle.local_qualification import ProtectedQualificationStore
    from packages.runtime_release.config import (
        ProtectedAuthorityError,
        read_protected_canonical_json_current,
    )

    identity = LocalRunIdentity.model_validate(identity)
    path = research_profile_path(identity)
    try:
        document, digest = read_protected_canonical_json_current(path)
        profile = LocalResearchProfile.model_validate_json(canonical_json_bytes(document))
    except (OSError, ProtectedAuthorityError, ValueError) as error:
        raise LocalQualificationError(
            'E_LOCAL_RESEARCH_PROFILE', 'local research profile is absent or unauthenticated') from error
    if profile.identity != identity:
        raise LocalQualificationError(
            'E_LOCAL_RESEARCH_IDENTITY', 'local research profile identity differs')
    root = Path(profile.store_root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('local research proof store path differs')
    store = ProtectedQualificationStore(root)
    receipt = require_research_inputs_qualified(identity, source=source,
        policy_digest=policy_digest, input_set_ref=input_set_ref,
        environment_ref=profile.binding.environment_ref, store=store)
    for ref in (profile.commitment_ref, profile.backup_receipt_ref):
        store.read_bytes(ref)
    try:
        if read_protected_canonical_json_current(path)[1] != digest:
            raise ValueError('local research profile changed during admission')
    except ProtectedAuthorityError as error:
        raise ValueError('local research profile was withdrawn during admission') from error
    return receipt
