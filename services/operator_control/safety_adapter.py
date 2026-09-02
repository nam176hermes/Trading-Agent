"""Normalize protected exported safety evidence for operator policy."""

from __future__ import annotations

from packages.operator_control.contracts import OperatorSafetyEvidenceV1
from packages.operator_control.hashing import evidence_sha256
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety_state import SafetyEvidence


def normalize_operator_safety_evidence(
    snapshot: SafetyEvidence, *, source_fingerprint: str
) -> OperatorSafetyEvidenceV1:
    if not isinstance(snapshot, SafetyEvidence):
        raise SafetyBlockedError(
            "SAFETY_STATE_INVALID", "protected safety evidence is required"
        )
    payload = {
        "schema_version": "operator-safety-evidence-v1",
        "requested_mode": snapshot.requested_mode.value,
        "effective_mode": snapshot.effective_mode.value,
        "live_execution_enabled": snapshot.live_execution_enabled,
        "live_trading_approved": snapshot.live_trading_approved,
        "kill_switch_state": snapshot.kill_switch_state.value,
        "observed_at": snapshot.generated_at.isoformat().replace("+00:00", "Z"),
        "source_fingerprint": source_fingerprint,
    }
    return OperatorSafetyEvidenceV1.model_validate(
        {**payload, "evidence_sha256": evidence_sha256(payload)}
    )


__all__ = ["normalize_operator_safety_evidence"]
