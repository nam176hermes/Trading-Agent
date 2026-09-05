"""Canonical fail-closed P3 phase status derived from tracked receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity, StrictModel
from packages.engine_contracts.serialization import canonical_json_bytes
from packages.p3_provenance import PhaseExitReceipt, PromotionReceipt
from packages.pre_p3_provenance import canonical_source_identity


P3_ROOT = Path("docs/implementation/p3")
PHASE_EXIT_PATH = P3_ROOT / "receipts/p3-phase-exit-v1.json"
STATUS_PATH = P3_ROOT / "p3-source-status.json"
PROMOTION_ROOT = P3_ROOT / "promotions"


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("blocker codes must be an array")
    return tuple(value)


Gate = Literal["PASS", "HELD"]


class P3StatusGates(StrictModel):
    pipeline_complete: Gate
    family_disclosed: Gate
    primary_selected: Gate
    holdout_pass: Gate
    native_parity_pass: Gate
    registry_lineage: Gate
    protected_promotion: Gate
    phase_complete: Gate


class P3SourceStatus(DigestModel):
    schema_version: Literal["p3-source-status-v1"]
    qualified_source: SourceIdentity | None
    phase_exit_receipt_digest: Sha256 | None
    promotion_receipt_digest: Sha256 | None
    current_semantic_closure_digest: Sha256
    gates: P3StatusGates
    blocker_codes: Annotated[
        tuple[Annotated[str, Field(pattern=r"^E_[A-Z0-9_]+$", max_length=96)], ...],
        BeforeValidator(_tuple), Field(max_length=64),
    ]
    authority: SafeAuthority


def valid_status_output(raw: bytes) -> bool:
    try:
        P3SourceStatus.model_validate_json(raw)
        return True
    except Exception:
        return False


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
        matches = []
        for path in (root / PROMOTION_ROOT).glob("*-v1.json"):
            try:
                value = PromotionReceipt.model_validate_json(path.read_bytes())
            except Exception:
                continue
            if path.name == f"{value.promoted_source.commit_sha}-v1.json":
                matches.append(value)
        if len(matches) == 1:
            promotion = matches[0]
        else:
            blockers.append("E_P3_PROTECTED_PROMOTION")
    phase_valid = (
        phase is not None
        and phase.source.closure_sha256 == source.closure_sha256
        and phase.qualification_workflow == "p3-authority.yml"
        and _attested(
            root, root / PHASE_EXIT_PATH,
            workflow="p3-authority.yml", source_digest=phase.source.commit_sha,
        )
    )
    promotion_valid = (
        phase_valid and promotion is not None
        and promotion.qualified_source == phase.source
        and promotion.phase_exit_receipt_digest == phase.digest
        and promotion.promoted_source == source
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
