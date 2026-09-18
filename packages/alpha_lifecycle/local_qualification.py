"""Authenticate retained qualification through current protected local custody.

The caller supplies independently established expected source/policy/input/env
and run identity. No operation, phase gate or hosted attestation is issued here.
Every admission must call again: the returned structural model is not a lease.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re
from uuid import UUID

from packages.alpha_lifecycle.contracts.authority import (
    LocalQualificationApproval,
    LocalRunIdentity,
    QualificationBinding,
    QualificationReceipt,
)
from packages.alpha_lifecycle.pit_evidence import _reference
from packages.alpha_lifecycle.replica_store import ArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.runtime_release.config import (
    ProtectedAuthorityError,
    read_protected_canonical_json_current,
    read_protected_evidence_current,
    read_protected_file_current,
)


_APPROVAL_ROOT = Path('/etc/trading-agent/local-qualification')


class LocalQualificationError(ValueError):
    def __init__(self,code: str,message: str):
        super().__init__(message)
        self.code=code


class ProtectedQualificationStore:
    """Read immutable root-owned proof copies across worker/custodian UIDs."""
    def __init__(self,root: Path):
        if not root.is_absolute() or '..' in root.parts:
            raise ValueError('protected qualification store path differs')
        self.root=root

    def read_bytes(self,ref: ArtifactRefV1) -> bytes:
        _reference(ref,131072,media=ref.media_type)
        raw=read_protected_evidence_current(self.root/ref.locator)
        if len(raw)!=ref.size_bytes or hashlib.sha256(raw).hexdigest()!=ref.content_sha256:
            raise ValueError('protected qualification evidence differs')
        return raw

    def put_bytes(self,value: bytes,*,media_type: str) -> ArtifactRefV1:
        digest=hashlib.sha256(value).hexdigest()
        ref=ArtifactRefV1(content_sha256=digest,size_bytes=len(value),media_type=media_type,locator=digest+'.blob')
        if self.read_bytes(ref)!=value:
            raise ValueError('protected qualification recomputation differs')
        return ref


def _host_identity() -> tuple[str, UUID]:
    """Observe this Linux host/boot, never a caller environment variable."""
    with Path('/etc/machine-id').open('rb') as stream:
        machine = stream.read(129).strip()
    with Path('/proc/sys/kernel/random/boot_id').open('rb') as stream:
        boot = stream.read(129).strip().decode('ascii')
    if re.fullmatch(rb'[0-9a-f]{32}', machine) is None or machine == b'0' * 32:
        raise ValueError('local host identity is unavailable')
    boot_id = UUID(boot)
    if str(boot_id) != boot or boot_id.int == 0:
        raise ValueError('local boot identity is invalid')
    return hashlib.sha256(machine).hexdigest(), boot_id


def _read_evidence(store: ArtifactStore, ref: ArtifactRefV1) -> bytes:
    _reference(ref, 131072, media=ref.media_type)
    raw = store.read_bytes(ref)
    if len(raw) != ref.size_bytes or hashlib.sha256(raw).hexdigest() != ref.content_sha256:
        raise ValueError('qualification evidence bytes differ from their reference')
    return raw


def validate_local_qualification(
    expected_identity: LocalRunIdentity,
    expected_binding: QualificationBinding,
    *,
    store: ArtifactStore,
) -> QualificationReceipt:
    """Reread current root-owned approval and exact retained proof, fail closed.

    Only the protected issuer can replace/revoke the per-issuer/per-gate file.
    It pins one active run/attempt; changing lanes or replaying an older receipt
    cannot satisfy this expected identity. Durable single-use holds remain the
    job/session owner's responsibility, not an in-memory ledger here.
    """
    identity = LocalRunIdentity.model_validate(expected_identity)
    binding = QualificationBinding.model_validate(expected_binding)
    path = _APPROVAL_ROOT / identity.issuer_identity / f'{binding.gate}.json'
    try:
        document, approval_digest = read_protected_canonical_json_current(path)
    except ProtectedAuthorityError as error:
        code='E_LOCAL_APPROVAL_UNAUTHENTICATED'
        if not path.exists():
            code='E_LOCAL_APPROVAL_MISSING'
        else:
            try:
                read_protected_file_current(path)
                code='E_LOCAL_APPROVAL_MALFORMED'
            except ProtectedAuthorityError:
                pass
        raise LocalQualificationError(code,'local qualification requires current protected approval') from error
    try:
        approval = LocalQualificationApproval.model_validate(document)
    except ValueError as error:
        raise LocalQualificationError('E_LOCAL_APPROVAL_MALFORMED','local approval is malformed') from error
    started = datetime.now(UTC)
    if approval.revoked:
        raise LocalQualificationError('E_LOCAL_APPROVAL_REVOKED','local qualification approval is revoked')
    if not approval.issued_at <= started < approval.expires_at:
        raise LocalQualificationError('E_LOCAL_APPROVAL_EXPIRED','local qualification approval is not current')
    if approval.issuer_identity != identity.issuer_identity:
        raise ValueError('local qualification issuer identity differs')
    host = _host_identity()
    if host != (identity.host_id, identity.boot_id):
        raise LocalQualificationError('E_LOCAL_HOST','local qualification host or boot differs')
    _reference(approval.receipt_ref, 65536)
    raw = _read_evidence(store, approval.receipt_ref)
    try:
        receipt = QualificationReceipt.model_validate_json(raw)
    except ValueError as error:
        raise LocalQualificationError('E_LOCAL_RECEIPT_MALFORMED','local qualification receipt is malformed') from error
    if canonical_json_bytes(receipt) != raw:
        raise ValueError("qualification receipt is not canonical")
    if receipt.identity != identity or receipt.binding != binding:
        raise LocalQualificationError('E_LOCAL_SOURCE' if receipt.binding.source!=binding.source else 'E_LOCAL_BINDING',
            'local qualification identity or binding differs')
    if not receipt.completed_at <= approval.issued_at <= started < approval.expires_at <= receipt.expires_at:
        raise LocalQualificationError('E_LOCAL_RECEIPT_EXPIRED','local qualification approval and receipt validity differ')
    for ref in (binding.input_ref, binding.environment_ref, *receipt.evidence_refs):
        _read_evidence(store, ref)
    try:
        _, current_digest = read_protected_canonical_json_current(path)
    except ProtectedAuthorityError as error:
        raise ValueError('local qualification protected approval was withdrawn') from error
    finished = datetime.now(UTC)
    if (current_digest != approval_digest or _host_identity() != host
        or not started <= finished < approval.expires_at):
        raise ValueError('local qualification authority changed during readback')
    return receipt
