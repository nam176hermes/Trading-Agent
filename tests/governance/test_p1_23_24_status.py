"""Canonical P1-23/P1-24 acceptance must remain internally consistent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
P1_23 = ROOT / "docs/implementation/p1-real-nautilus/p1-23-adversarial-qualification-receipt.json"
P1_24 = ROOT / "docs/implementation/p1-real-nautilus/p1-24-release-readiness-receipt.json"
LEDGER = ROOT / "docs/implementation/p1-real-nautilus/task-ledger.md"
_SAFE = {
    "live_authorized": False,
    "network_trading_authorized": False,
    "production_authorized": False,
}


def _load(path: Path) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            assert key not in value
            value[key] = item
        return value

    value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
    assert isinstance(value, dict)
    return value


def test_p1_23_and_p1_24_are_accepted_on_one_exact_source() -> None:
    adversarial = _load(P1_23)
    release = _load(P1_24)

    assert adversarial["schema"] == "trading-agent-p1-23-acceptance/v1"
    assert adversarial["status"] == "P1_23_ACCEPTED"
    assert adversarial["verdict"] == "PASS"
    assert adversarial["scenario_count"] == 8
    assert adversarial["test_count"] == 205
    assert adversarial["skipped_count"] == 0
    assert adversarial["authority_limits"] == _SAFE
    assert release["schema"] == "trading-agent-p1-24-acceptance/v1"
    assert release["status"] == "P1_24_ACCEPTED"
    assert release["verdict"] == "PASS"
    assert release["authority_limits"] == _SAFE
    assert release["p1_23_acceptance_sha256"] == hashlib.sha256(
        P1_23.read_bytes()
    ).hexdigest()
    assert (
        release["qualification_source_commit"]
        == adversarial["qualification_source_commit"]
    )
    assert (
        release["qualification_source_tree"]
        == adversarial["qualification_source_tree"]
    )
    assert all(review["verdict"] == "PASS" for review in adversarial["reviews"])
    assert all(review["verdict"] == "PASS" for review in release["reviews"])

    ledger = LEDGER.read_text(encoding="utf-8")
    assert "| P1-23 | P1-22 | ACCEPTED |" in ledger
    assert "| P1-24 | P1-22 | ACCEPTED |" in ledger
    assert "| P1-25 | P1-23, P1-24 | READY |" in ledger
