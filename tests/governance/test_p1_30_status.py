from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
P1 = ROOT / "docs/implementation/p1-real-nautilus"
RECEIPT = P1 / "p1-30-local-paper-qualification-receipt.json"
SAFE = {
    "live_authorized": False,
    "network_trading_authorized": False,
    "production_authorized": False,
}


def test_p1_30_closes_remote_source_without_live_overclaim() -> None:
    receipt = json.loads(RECEIPT.read_bytes())
    assert receipt["schema"] == "trading-agent-p1-paper-qualification/v1"
    assert receipt["status"] == "P1_LOCAL_SOURCE_CERTIFIED"
    assert receipt["verdict"] == "PASS"
    assert receipt["authority_limits"] == SAFE
    assert receipt["engine_version"] == "1.231.0"
    assert receipt["paper_protocol"] == "nautilus-paper-session-v2"
    assert receipt["p1_product_closure_schema"] == 8
    assert receipt["p1_product_closure_sha256"] == (
        "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
    )
    assert receipt["qualification_source_commit"] == (
        "fc34bc52d5af5312303abac417502d7419247ba7"
    )
    assert receipt["qualification_source_tree"] == (
        "cc4f3a628b4e7b0faacac47b6ea41ef3065192b3"
    )
    assert receipt["test_count"] == 69
    assert receipt["skipped_count"] == 0

    ledger = (P1 / "task-ledger.md").read_text(encoding="utf-8")
    assert "| P1-29 | P1-27, P1-28 | ACCEPTED |" in ledger
    assert "| P1-30 | P1-29 | ACCEPTED |" in ledger

    certification = (P1 / "P1-FINAL-CERTIFICATION.md").read_text(encoding="utf-8")
    assert "Status: `P1_COMPLETE`" in certification
    assert "9444a0089a46916811cfddb83fc49eb3d26ae216" in certification
    assert "a083bf4612ec63fd5ac5d6eb29e60670046c548b" in certification
    assert "Foundation run `33348201903`" in certification
    assert "remains pending" not in certification
    assert "PAPER_LOCAL_ONLY" in certification
    assert "NETWORK_DISABLED" in certification
    assert "LIVE_NOT_AUTHORIZED" in certification
    assert "PRODUCTION_NOT_AUTHORIZED" in certification
