"""Canonical fail-closed P3 phase status derived from tracked receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.p3_provenance import (
    PhaseExitReceipt, PromotionReceipt, P3SourceStatus as P3SourceStatus,
    P3StatusGates as P3StatusGates, valid_status_output as valid_status_output,
)
from packages.pre_p3_provenance import canonical_source_identity


P3_ROOT = Path("docs/implementation/p3")
PHASE_EXIT_PATH = P3_ROOT / "receipts/p3-phase-exit-v1.json"
STATUS_PATH = P3_ROOT / "p3-source-status.json"
PROMOTION_ROOT = P3_ROOT / "promotions"


def _attested(
    root: Path, path: Path, *, workflow: str, source_digest: str
) -> bool:
    """Verify imported GitHub evidence; structural parsing alone never passes a gate."""

    try:
        completed = subprocess.run(
            [
                "gh", "attestation", "verify", str(path),
                "--repo", "nam176hermes/Trading-Agent",
                "--signer-workflow", f"nam176hermes/Trading-Agent/.github/workflows/{workflow}",
                "--source-ref", "refs/heads/main",
                "--source-digest", source_digest,
                "--signer-digest", source_digest,
                "--format", "json",
            ],
            cwd=root, capture_output=True, check=True, timeout=30,
        )
        verified = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    return isinstance(verified, list) and bool(verified)


def _semantic_identity(source: SourceIdentity) -> tuple[str, str, str]:
    return source.closure_schema_version, source.closure_policy_sha256, source.closure_sha256


def _current_promotion(root: Path, source: SourceIdentity) -> PromotionReceipt | None:
    """Select the nearest first-parent promotion across output-only commits."""
    current = source
    while _semantic_identity(current) == _semantic_identity(source):
        path = root / PROMOTION_ROOT / f"{current.commit_sha}-v1.json"
        if path.exists():
            # A malformed or mismatched nearest receipt must never fall back.
            value = PromotionReceipt.model_validate_json(path.read_bytes())
            if value.promoted_source != current:
                raise ValueError("promotion does not bind its committed source")
            return value
        parent = subprocess.run(
            ["git", "rev-parse", "--verify", f"{current.commit_sha}^1"],
            cwd=root, capture_output=True, check=False,
        )
        if parent.returncode:
            return None
        current = SourceIdentity.model_validate(
            canonical_source_identity(root, parent.stdout.decode("ascii").strip())
        )
    return None


def derive_p3_status(root: Path) -> P3SourceStatus:
    source = SourceIdentity.model_validate(canonical_source_identity(root))
    blockers = []
    phase = None
    promotion = None
    try:
        phase = PhaseExitReceipt.model_validate_json((root / PHASE_EXIT_PATH).read_bytes())
    except Exception:
        blockers.append("E_P3_PHASE_EXIT_RECEIPT")
    if phase is not None:
        try:
            promotion = _current_promotion(root, source)
        except (OSError, ValueError):
            promotion = None
        if promotion is None:
            blockers.append("E_P3_PROTECTED_PROMOTION")
    phase_valid = (
        phase is not None
        and _semantic_identity(phase.source) == _semantic_identity(source)
        and phase.qualification_workflow == "p3-authority.yml"
        and _attested(
            root, root / PHASE_EXIT_PATH,
            workflow="p3-authority.yml", source_digest=phase.source.commit_sha,
        )
    )
    promotion_valid = (
        phase_valid and phase is not None and promotion is not None
        and promotion.qualified_source == phase.source
        and promotion.phase_exit_receipt_digest == phase.digest
        and _semantic_identity(promotion.promoted_source) == _semantic_identity(source)
        and promotion.workflow_ref == "foundation.yml"
        and _attested(
            root,
            root / PROMOTION_ROOT / f"{promotion.promoted_source.commit_sha}-v1.json",
            workflow="foundation.yml", source_digest=promotion.promoted_source.commit_sha,
        )
    )
    if phase is not None and not phase_valid:
        blockers.append("E_P3_SOURCE_STALE")
    if promotion is not None and not promotion_valid:
        blockers.append("E_P3_PROMOTION_STALE")
    pass_gate = "PASS" if phase_valid else "HELD"
    gates = P3StatusGates(
        pipeline_complete=pass_gate,family_disclosed=pass_gate,primary_selected=pass_gate,
        holdout_pass=pass_gate,native_parity_pass=pass_gate,registry_lineage=pass_gate,
        protected_promotion="PASS" if promotion_valid else "HELD",
        phase_complete="PASS" if phase_valid and promotion_valid else "HELD",
    )
    payload = {
        "schema_version":"p3-source-status-v1",
        "qualified_source":None if phase is None else phase.source,
        "phase_exit_receipt_digest":None if phase is None else phase.digest,
        "promotion_receipt_digest":None if promotion is None else promotion.digest,
        "current_semantic_closure_digest":source.closure_sha256,
        "gates":gates,"blocker_codes":tuple(sorted(set(blockers))),
        "authority":{"broker":False,"live":False,"network":False,"production":False},
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return P3SourceStatus.model_validate(payload)


__all__ = ["P3SourceStatus", "P3StatusGates", "derive_p3_status", "valid_status_output"]
