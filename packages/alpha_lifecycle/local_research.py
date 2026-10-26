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
import os
import re
import stat
from typing import Annotated, Literal

from pydantic import BeforeValidator, model_validator

from packages.alpha_lifecycle.contracts.base import StrictModel, Text, SourceIdentity, DigestModel, SafeAuthority
from packages.alpha_lifecycle.contracts.execution import InputSet, EnvironmentIdentity
from packages.alpha_lifecycle.contracts.data import PITProof
from packages.alpha_lifecycle.contracts.models import json_array
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
from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.research_custody import (
    P3LocalResearchBatchCommitment, P3LocalResearchBackupReceipt,
)
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes, CanonicalUtcDateTime
from packages.alpha_lifecycle.pit_evidence import _reference


_RESEARCH_ROOT = Path('/etc/trading-agent/local-research')
_SAFE_ISSUER = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')
RESEARCH_INPUTS_GATE = 'P3_RESEARCH_INPUTS'


def protected_backup_identity(path: Path) -> tuple[int, int, int, int]:
    """Observe issuer-sealed root custody without granting access to private objects."""
    from packages.runtime_release.config import _safe_directory
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('backup custody path is not absolute')
    flags=os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    descriptor=os.open('/',flags)
    try:
        if not _safe_directory(os.fstat(descriptor)):
            raise ValueError('backup custody ancestor is unprotected')
        for part in path.parts[1:]:
            child=os.open(part,flags,dir_fd=descriptor)
            os.close(descriptor)
            descriptor=child
            if not _safe_directory(os.fstat(descriptor)):
                raise ValueError('backup custody root or ancestor is unprotected')
        info=os.fstat(descriptor)
        if stat.S_IMODE(info.st_mode)!=0o500:
            raise ValueError('backup custody root must be private root-owned 0500')
        return info.st_dev,info.st_ino,info.st_mtime_ns,info.st_ctime_ns
    finally:
        os.close(descriptor)


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
    identity = LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
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


def _ref(value) -> ArtifactRefV1:
    if isinstance(value, ArtifactRefV1):
        return value
    return ArtifactRefV1.model_validate(value)


class LocalResearchProof(DigestModel):
    """Bounded producer readback proposal; only protected approval authenticates it."""
    schema_version: Literal['p3-local-research-proof-v1']
    identity: LocalRunIdentity
    source_store_root: Text
    backup_store_root: Text
    commitment_ref: ArtifactRefV1
    backup_receipt_ref: ArtifactRefV1
    backup_inventory_ref: ArtifactRefV1
    verified_at: CanonicalUtcDateTime
    status: Literal['READBACK_VERIFIED']
    authority: SafeAuthority

    @model_validator(mode='after')
    def _roots(self):
        source, backup = Path(self.source_store_root), Path(self.backup_store_root)
        if (not source.is_absolute() or not backup.is_absolute()
            or '..' in source.parts or '..' in backup.parts
            or source == backup or source in backup.parents or backup in source.parents):
            raise ValueError('research source and backup custody roots must be distinct and non-nested')
        _reference(self.commitment_ref, 65536)
        _reference(self.backup_receipt_ref, 65536)
        _reference(self.backup_inventory_ref, 67108864)
        return self


class LocalResearchProfile(StrictModel):
    """Protected research profile; its path derives from the authenticated issuer.

    The profile pins a declared InputSet, its compact proofs and sealed backup
    root. Admission independently derives the expected InputSet from the operation
    and its environment and policy from retained InputSet bytes.
    """
    schema_version: Literal['local-research-profile-v1']
    identity: LocalRunIdentity
    store_root: Text
    binding: QualificationBinding
    commitment_ref: ArtifactRefV1
    backup_receipt_ref: ArtifactRefV1
    proof_ref: ArtifactRefV1
    source_store_root: Text
    backup_store_root: Text
    backup_root_identity: Annotated[tuple[int,int,int,int], BeforeValidator(json_array)]

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
    if protected_backup_identity(Path(profile.backup_store_root)) != profile.backup_root_identity:
        raise ValueError('local research backup custody root changed')
    inputs = _read(store, _ref(input_set_ref), InputSet)
    if inputs.source != SourceIdentity.model_validate(source) or inputs.policy_digest != policy_digest:
        raise ValueError('local research InputSet source or canonical policy differs')
    binding = QualificationBinding(gate=RESEARCH_INPUTS_GATE, source=inputs.source,
        policy_digest=policy_digest, input_ref=_ref(input_set_ref), environment_ref=inputs.environment_ref)
    if profile.binding != binding:
        raise ValueError('local research profile binding differs from InputSet')
    receipt = require_research_inputs_qualified(identity, source=source,
        policy_digest=policy_digest, input_set_ref=input_set_ref,
        environment_ref=inputs.environment_ref, store=store)
    validate_local_research_graph(profile, inputs, receipt, store)
    # Recheck the approval after semantic reads, including a withdrawal in the last read.
    if protected_backup_identity(Path(profile.backup_store_root)) != profile.backup_root_identity:
        raise ValueError('local research backup custody root changed during admission')
    if require_research_inputs_qualified(identity, source=source, policy_digest=policy_digest,
        input_set_ref=input_set_ref, environment_ref=inputs.environment_ref, store=store) != receipt:
        raise ValueError('local research approval changed during semantic validation')
    try:
        if read_protected_canonical_json_current(path)[1] != digest:
            raise ValueError('local research profile changed during admission')
    except ProtectedAuthorityError as error:
        raise ValueError('local research profile was withdrawn during admission') from error
    return receipt


def validate_local_research_graph(profile, inputs, receipt, store):
    """One compact semantic validator shared by issuer preflight and admission."""
    if inputs.synthetic_generation_ref is not None:
        raise ValueError('LOCAL research cannot admit synthetic purpose')
    identity = profile.identity
    policy_digest = inputs.policy_digest
    _read(store, inputs.environment_ref, EnvironmentIdentity)
    pit = _read(store, inputs.pit_proof_ref, PITProof)
    commitment = _read(store, profile.commitment_ref, P3LocalResearchBatchCommitment)
    backup = _read(store, profile.backup_receipt_ref, P3LocalResearchBackupReceipt)
    proof = _read(store, profile.proof_ref, LocalResearchProof)
    if (profile.commitment_ref not in receipt.evidence_refs
        or profile.backup_receipt_ref not in receipt.evidence_refs
        or profile.proof_ref not in receipt.evidence_refs
        or proof.identity != identity
        or proof.source_store_root != profile.source_store_root
        or proof.backup_store_root != profile.backup_store_root
        or proof.commitment_ref != profile.commitment_ref
        or proof.backup_receipt_ref != profile.backup_receipt_ref
        or proof.backup_inventory_ref.content_sha256 != backup.object_inventory_content_sha256
        or proof.backup_inventory_ref.size_bytes != backup.object_inventory_size_bytes
        or not backup.verified_at <= proof.verified_at <= receipt.completed_at
        or pit.revision_proof_ref != profile.commitment_ref
        or pit.dataset_ref != inputs.dataset_evidence_ref
        or pit.fold_manifest_ref != inputs.fold_manifest_ref
        or pit.vintage_class != 'RETROSPECTIVE_CURRENT_ARCHIVE'
        or pit.historical_vintage_verified
        or pit.limitations != ('retrospective.current_archive',)
        or commitment.identity != identity or backup.identity != identity
        or commitment.source != inputs.source or backup.source != inputs.source
        or commitment.policy_digest != policy_digest
        or commitment.dataset_content_sha256 != inputs.dataset_evidence_ref.content_sha256
        or commitment.backup_receipt_ref != profile.backup_receipt_ref
        or backup.inventory_content_sha256 != commitment.inventory_content_sha256
        or backup.dataset_content_sha256 != commitment.dataset_content_sha256
        or backup.snapshot_content_sha256 != commitment.snapshot_content_sha256
        or backup.object_count < commitment.inventory_entry_count
        or backup.total_bytes < commitment.inventory_size_bytes + inputs.dataset_evidence_ref.size_bytes
        or not commitment.completed_at <= backup.verified_at <= commitment.issued_at <= receipt.completed_at):
        raise ValueError('local research proof subject, producer, backup or timing differs')
