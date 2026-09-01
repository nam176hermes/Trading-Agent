"""Derived, fail-closed canonical project status from immutable receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from packages.engine_contracts.serialization import canonical_json_bytes


RECEIPT_DIR = Path("docs/implementation/pre-p3/receipts")
P0_RECEIPT = "p0-source-complete-v1.json"
RECEIPTS = {
    "P1_H_COMPLETE": "p1-h-complete-v1.json",
    "P1_LTS_READY": "p1-lts-ready-v1.json",
    "P2_SOURCE_COMPLETE": "p2-source-complete-v1.json",
    "P2_RUNTIME_QUALIFIED": "p2-runtime-qualified-v1.json",
    "P2_QUALIFIED": "p2-qualified-v1.json",
    "P3_BASELINES_FROZEN": "p3-baselines-frozen-v1.json",
    "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v1.json",
    "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v1.json",
}
_RECEIPT_ONLY_PATHS = (
    "docs/implementation/pre-p3/receipts/",
    "docs/implementation/project-status.json",
)


class ProjectStatusError(ValueError):
    """Project authority evidence is malformed, stale, or contradictory."""


def receipt_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes({key: value for key, value in payload.items() if key != "receipt_sha256"})
    ).hexdigest()


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_pass_receipt(payload: object, expected_gate: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProjectStatusError("receipt must be a JSON object")
    required = {
        "authority",
        "evidence_sha256s",
        "gate",
        "receipt_sha256",
        "schema_version",
        "source_sha",
        "source_tree",
        "status",
    }
    if set(payload) != required:
        raise ProjectStatusError("receipt field set is not exact")
    if (
        payload["schema_version"] != "pre-p3-gate-receipt-v1"
        or payload["gate"] != expected_gate
        or payload["status"] != "PASS"
    ):
        raise ProjectStatusError("receipt gate identity or status is invalid")
    if not _is_lower_hex(payload["source_sha"], 40):
        raise ProjectStatusError("receipt source SHA is invalid")
    if not _is_lower_hex(payload["source_tree"], 40):
        raise ProjectStatusError("receipt source tree is invalid")
    evidence = payload["evidence_sha256s"]
    if (
        not isinstance(evidence, list)
        or not evidence
        or evidence != sorted(set(evidence))
        or any(not _is_lower_hex(item, 64) for item in evidence)
    ):
        raise ProjectStatusError("receipt evidence digests are invalid")
    authority = payload["authority"]
    if authority != {
        "broker": False,
        "live": False,
        "network": False,
        "production": False,
    }:
        raise ProjectStatusError("receipt authority must remain fail-closed")
    if payload["receipt_sha256"] != receipt_sha256(payload):
        raise ProjectStatusError("receipt self-digest is invalid")
    return payload


def make_pass_receipt(
    gate: str,
    *,
    source_sha: str,
    source_tree: str,
    evidence_sha256s: tuple[str, ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "authority": {"broker": False, "live": False, "network": False, "production": False},
        "evidence_sha256s": sorted(set(evidence_sha256s)),
        "gate": gate,
        "schema_version": "pre-p3-gate-receipt-v1",
        "source_sha": source_sha,
        "source_tree": source_tree,
        "status": "PASS",
    }
    payload["receipt_sha256"] = receipt_sha256(payload)
    return validate_pass_receipt(payload, gate)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise ProjectStatusError("git source authority is unavailable")
    return completed.stdout.strip()


def _source_is_current(root: Path, source_sha: str, source_tree: str) -> bool:
    try:
        dirty = [line[3:] for line in _git(root, "status", "--porcelain").splitlines()]
        if any(not path.startswith(_RECEIPT_ONLY_PATHS) for path in dirty):
            return False
        if _git(root, "rev-parse", f"{source_sha}^{{tree}}") != source_tree:
            return False
        if _git(root, "merge-base", source_sha, "HEAD") != source_sha:
            return False
        changed = _git(root, "diff", "--name-only", f"{source_sha}..HEAD").splitlines()
        return all(path.startswith(_RECEIPT_ONLY_PATHS) for path in changed)
    except ProjectStatusError:
        return False


def _source_is_ancestor(root: Path, source_sha: str, source_tree: str) -> bool:
    try:
        return (
            _git(root, "rev-parse", f"{source_sha}^{{tree}}") == source_tree
            and _git(root, "merge-base", source_sha, "HEAD") == source_sha
        )
    except ProjectStatusError:
        return False


def derive_project_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    policy = json.loads(
        (root / "docs/implementation/p1-real-nautilus/lts/p1-engine-lts-policy-v2.json").read_text()
    )
    gates: dict[str, str] = {}
    latest: dict[str, dict[str, str]] = {}
    blockers: list[str] = []
    p0_path = root / RECEIPT_DIR / P0_RECEIPT
    try:
        p0_raw = p0_path.read_bytes()
        p0_receipt = validate_pass_receipt(
            json.loads(p0_raw), "P0_SOURCE_COMPLETE"
        )
        if not _source_is_ancestor(
            root, p0_receipt["source_sha"], p0_receipt["source_tree"]
        ):
            raise ProjectStatusError("P0 source is not an ancestor")
        p0_status = "P0_SOURCE_COMPLETE"
        latest["P0"] = {
            "path": str(RECEIPT_DIR / P0_RECEIPT),
            "sha256": hashlib.sha256(p0_raw).hexdigest(),
        }
    except (OSError, ValueError, json.JSONDecodeError, ProjectStatusError):
        p0_status = "HELD"
        blockers.append("P0: P0_SOURCE_COMPLETE absent")
    for gate, name in RECEIPTS.items():
        path = root / RECEIPT_DIR / name
        try:
            raw = path.read_bytes()
            receipt = validate_pass_receipt(json.loads(raw), gate)
            if not _source_is_current(root, receipt["source_sha"], receipt["source_tree"]):
                raise ProjectStatusError("receipt source is stale")
            gates[gate] = "PASS"
            latest[gate] = {
                "path": str(RECEIPT_DIR / name),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        except (OSError, ValueError, json.JSONDecodeError, ProjectStatusError):
            gates[gate] = "HELD"
            blockers.append(f"{gate}: valid current receipt absent")

    p1_complete_path = root / policy["bindings"]["p1_complete_receipt"]["path"]
    p1_complete = (
        hashlib.sha256(p1_complete_path.read_bytes()).hexdigest()
        == policy["bindings"]["p1_complete_receipt"]["sha256"]
        and "Status: `P1_COMPLETE`" in p1_complete_path.read_text()
    )
    p3_allowed = (
        p0_status == "P0_SOURCE_COMPLETE"
        and all(status == "PASS" for status in gates.values())
        and p1_complete
    )
    active = next(item for item in policy["engine_registry"] if item["lifecycle"] == "ACTIVE")
    rollback = next(item for item in policy["engine_registry"] if item["lifecycle"] == "ROLLBACK")
    return {
        "authority": {"broker": False, "live": False, "network": False, "production": False},
        "blockers": sorted(blockers),
        "current_phase": "P3_ALPHA_DEVELOPMENT" if p3_allowed else "PRE_P3_CLOSURE",
        "data_api_epoch": 2,
        "execution_scope": policy["execution_scope"],
        "engine": {
            "active": active,
            "event_api_epoch": policy["event_api_epoch"],
            "rollback": rollback,
        },
        "gates": {
            "P0": p0_status,
            "P1_COMPLETE": "PASS" if p1_complete else "HELD",
            **gates,
            "PROJECT_STATUS_AUTHORITY": "PASS",
        },
        "latest_receipts": latest,
        "live_eligible": False,
        "live_enabled": False,
        "migration_head": "0019_p2_security_master",
        "p2_source_status": "IMPLEMENTED",
        "p3_alpha_development_allowed": p3_allowed,
        "schema_version": "trading-agent-project-status-v1",
    }


__all__ = [
    "ProjectStatusError",
    "RECEIPTS",
    "derive_project_status",
    "make_pass_receipt",
    "receipt_sha256",
    "validate_pass_receipt",
]
