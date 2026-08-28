"""P1-00 starts only from the accepted P1-only engine baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
P1 = ROOT / "docs/implementation/p1-real-nautilus"


def test_p1_product_baseline_binds_u08_without_legacy_or_live_promotion() -> None:
    receipt_path = P1 / "upgrade/p1-engine-baseline-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    baseline = (P1 / "baseline.md").read_text(encoding="utf-8")

    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() in baseline
    assert receipt["status"] == "P1_BASELINE_APPROVED"
    assert receipt["scope"] == "P1_A_AND_P1_B_ONLY"
    assert receipt["p1_product_closure_schema"] == 8
    assert receipt["legacy_phase4_profiles_unchanged"] is True
    assert receipt["legacy_phase4_authority"]["engine_version"] == "1.227.0"
    assert all(value is False for value in receipt["authority_limits"].values())
    assert "242f5f1be3a28cbb4241caacb03f82abed073bea" in baseline
    assert "9f8ba02822d54d1b4d6ba605a41a9e3d903f1c48" in baseline
    assert "| P1-01 | P1-00 | ACCEPTED |" in (P1 / "task-ledger.md").read_text()
