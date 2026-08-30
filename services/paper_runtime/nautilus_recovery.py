"""Canonical no-clobber recovery receipts for P1 Nautilus paper sessions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_runtime_contracts.paper import PaperSessionCheckpoint
from packages.safety_evidence import CanonicalKillSwitchState

from .nautilus_checkpoint import NautilusCheckpointRecord, ZERO_CHECKPOINT_SHA256
from .nautilus_reconciliation import (
    NautilusChildState,
    NautilusRecoveryDisposition,
    NautilusRecoveryEvidence,
    NautilusRecoveryReason,
    reconcile_nautilus_paper,
)


NAUTILUS_RECOVERY_RECEIPT_SCHEMA = "trading-agent-nautilus-paper-recovery/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOP_LEVEL_KEYS = {
    "authority_limits",
    "checkpoint_sha256",
    "closure_digest",
    "config_digest",
    "engine_version",
    "evidence",
    "evidence_sha256",
    "reason_codes",
    "schema",
    "session_id",
    "source_commit",
    "verdict",
}
_EVIDENCE_KEYS = {
    "checkpoint",
    "child_outcome_proven",
    "child_state",
    "closure_digest",
    "config_digest",
    "current_child_identity",
    "engine_version",
    "expected_closure_digest",
    "expected_config_digest",
    "expected_engine_version",
    "expected_source_commit",
    "expected_target_schedule_cursor",
    "final_engine_observation_sha256",
    "kill_switch_state",
    "ledger_event_prefix_sha256",
    "ledger_last_event_digest",
    "ledger_last_sequence",
    "portfolio_state_hash",
    "session_id",
    "source_commit",
    "target_schedule_cursor",
}
_CHECKPOINT_RECORD_KEYS = {
    "checkpoint",
    "checkpoint_sha256",
    "event_batch_sha256",
    "parity_receipt_sha256",
}
_AUTHORITY_LIMITS = {
    "live_authorized": False,
    "network_query_allowed": False,
    "production_authorized": False,
}


@dataclass(frozen=True, slots=True)
class NautilusRecoveryReceipt:
    schema: str
    verdict: NautilusRecoveryDisposition
    reason_codes: tuple[NautilusRecoveryReason, ...]
    session_id: UUID
    engine_version: str
    closure_digest: str
    source_commit: str
    config_digest: str
    checkpoint_sha256: str
    evidence_sha256: str
    receipt_sha256: str


def _checkpoint_document(record: NautilusCheckpointRecord | None) -> object:
    if record is None:
        return None
    return {
        "checkpoint": record.checkpoint.model_dump(mode="json"),
        "checkpoint_sha256": record.checkpoint_sha256,
        "event_batch_sha256": record.event_batch_sha256,
        "parity_receipt_sha256": record.parity_receipt_sha256,
    }


def _evidence_document(evidence: NautilusRecoveryEvidence) -> dict[str, object]:
    return {
        "checkpoint": _checkpoint_document(evidence.checkpoint),
        "child_outcome_proven": evidence.child_outcome_proven,
        "child_state": evidence.child_state.value,
        "closure_digest": evidence.closure_digest,
        "config_digest": evidence.config_digest,
        "current_child_identity": evidence.current_child_identity,
        "engine_version": evidence.engine_version,
        "expected_closure_digest": evidence.expected_closure_digest,
        "expected_config_digest": evidence.expected_config_digest,
        "expected_engine_version": evidence.expected_engine_version,
        "expected_source_commit": evidence.expected_source_commit,
        "expected_target_schedule_cursor": evidence.expected_target_schedule_cursor,
        "final_engine_observation_sha256": evidence.final_engine_observation_sha256,
        "kill_switch_state": evidence.kill_switch_state.value,
        "ledger_event_prefix_sha256": evidence.ledger_event_prefix_sha256,
        "ledger_last_event_digest": evidence.ledger_last_event_digest,
        "ledger_last_sequence": evidence.ledger_last_sequence,
        "portfolio_state_hash": evidence.portfolio_state_hash,
        "session_id": str(evidence.session_id),
        "source_commit": evidence.source_commit,
        "target_schedule_cursor": evidence.target_schedule_cursor,
    }


def _build_document(evidence: NautilusRecoveryEvidence) -> dict[str, object]:
    decision = reconcile_nautilus_paper(evidence)
    evidence_document = _evidence_document(evidence)
    checkpoint_sha = (
        ZERO_CHECKPOINT_SHA256
        if evidence.checkpoint is None
        else evidence.checkpoint.checkpoint_sha256
    )
    return {
        "authority_limits": _AUTHORITY_LIMITS,
        "checkpoint_sha256": checkpoint_sha,
        "closure_digest": evidence.closure_digest,
        "config_digest": evidence.config_digest,
        "engine_version": evidence.engine_version,
        "evidence": evidence_document,
        "evidence_sha256": hashlib.sha256(
            canonical_json_bytes(evidence_document)
        ).hexdigest(),
        "reason_codes": [reason.value for reason in decision.reason_codes],
        "schema": NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
        "session_id": str(evidence.session_id),
        "source_commit": evidence.source_commit,
        "verdict": decision.disposition.value,
    }


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("recovery receipt contains a duplicate key")
        result[key] = value
    return result


def _checkpoint_from_document(value: object) -> NautilusCheckpointRecord | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _CHECKPOINT_RECORD_KEYS:
        raise ValueError("recovery checkpoint record is invalid")
    try:
        checkpoint = PaperSessionCheckpoint.model_validate_json(
            canonical_json_bytes(value["checkpoint"])
        )
        return NautilusCheckpointRecord(
            checkpoint=checkpoint,
            checkpoint_sha256=value["checkpoint_sha256"],
            event_batch_sha256=value["event_batch_sha256"],
            parity_receipt_sha256=value["parity_receipt_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recovery checkpoint record is invalid") from exc


def _evidence_from_document(value: object) -> NautilusRecoveryEvidence:
    if type(value) is not dict or set(value) != _EVIDENCE_KEYS:
        raise ValueError("recovery evidence is invalid")
    try:
        return NautilusRecoveryEvidence(
            session_id=UUID(value["session_id"]),
            engine_version=value["engine_version"],
            expected_engine_version=value["expected_engine_version"],
            closure_digest=value["closure_digest"],
            expected_closure_digest=value["expected_closure_digest"],
            source_commit=value["source_commit"],
            expected_source_commit=value["expected_source_commit"],
            config_digest=value["config_digest"],
            expected_config_digest=value["expected_config_digest"],
            child_state=NautilusChildState(value["child_state"]),
            current_child_identity=value["current_child_identity"],
            checkpoint=_checkpoint_from_document(value["checkpoint"]),
            ledger_last_sequence=value["ledger_last_sequence"],
            ledger_last_event_digest=value["ledger_last_event_digest"],
            ledger_event_prefix_sha256=value["ledger_event_prefix_sha256"],
            portfolio_state_hash=value["portfolio_state_hash"],
            target_schedule_cursor=value["target_schedule_cursor"],
            expected_target_schedule_cursor=value["expected_target_schedule_cursor"],
            final_engine_observation_sha256=value[
                "final_engine_observation_sha256"
            ],
            child_outcome_proven=value["child_outcome_proven"],
            kill_switch_state=CanonicalKillSwitchState(value["kill_switch_state"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recovery evidence is invalid") from exc


def _receipt(raw: bytes) -> NautilusRecoveryReceipt:
    if not raw.endswith(b"\n") or len(raw) > 256 * 1024:
        raise ValueError("recovery receipt bytes are invalid")
    try:
        document = json.loads(raw[:-1], object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("recovery receipt is not valid JSON") from exc
    if (
        type(document) is not dict
        or set(document) != _TOP_LEVEL_KEYS
        or canonical_json_bytes(document) + b"\n" != raw
        or document["schema"] != NAUTILUS_RECOVERY_RECEIPT_SCHEMA
        or document["authority_limits"] != _AUTHORITY_LIMITS
    ):
        raise ValueError("recovery receipt authority is invalid")
    evidence = _evidence_from_document(document["evidence"])
    expected = _build_document(evidence)
    if document != expected:
        raise ValueError("recovery receipt evidence does not match verdict")
    try:
        return NautilusRecoveryReceipt(
            schema=NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
            verdict=NautilusRecoveryDisposition(document["verdict"]),
            reason_codes=tuple(
                NautilusRecoveryReason(reason) for reason in document["reason_codes"]
            ),
            session_id=UUID(document["session_id"]),
            engine_version=document["engine_version"],
            closure_digest=document["closure_digest"],
            source_commit=document["source_commit"],
            config_digest=document["config_digest"],
            checkpoint_sha256=document["checkpoint_sha256"],
            evidence_sha256=document["evidence_sha256"],
            receipt_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("recovery receipt fields are invalid") from exc


def load_nautilus_recovery_receipt(path: Path) -> NautilusRecoveryReceipt:
    if not isinstance(path, Path):
        raise TypeError("recovery receipt path must be an exact Path")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 256 * 1024:
            raise ValueError("recovery receipt is not a bounded regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return _receipt(b"".join(chunks))


def write_nautilus_recovery_receipt(
    path: Path,
    evidence: NautilusRecoveryEvidence,
) -> NautilusRecoveryReceipt:
    if not isinstance(path, Path) or type(evidence) is not NautilusRecoveryEvidence:
        raise TypeError("exact recovery receipt inputs are required")
    raw = canonical_json_bytes(_build_document(evidence)) + b"\n"
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(path.parent, directory_flags)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("recovery receipt write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)
    return _receipt(raw)


__all__ = [
    "NAUTILUS_RECOVERY_RECEIPT_SCHEMA",
    "NautilusRecoveryReceipt",
    "load_nautilus_recovery_receipt",
    "write_nautilus_recovery_receipt",
]
