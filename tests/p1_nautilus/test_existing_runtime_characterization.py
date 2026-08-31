"""P1-01 reuses the accepted real Nautilus path and U05 API authority."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UPGRADE = ROOT / "docs/implementation/p1-real-nautilus/upgrade"


def test_existing_launcher_uses_real_backtest_engine_and_exact_g1_contract() -> None:
    launcher = (ROOT / "engines/nautilus/launcher/nautilus_backtest.py").read_text()
    contract = json.loads((UPGRADE / "direct-api-contract.json").read_bytes())
    receipt = json.loads((UPGRADE / "u05-api-qualification-receipt.json").read_bytes())

    assert "from nautilus_trader.backtest.engine import BacktestEngine" in launcher
    assert "engine = BacktestEngine(" in launcher
    assert "calculate_reference_outcome" not in launcher
    assert len(contract["api_surfaces"]) == 33
    assert len(contract["local_invocations"]) == 153
    assert receipt["candidate_generation_id"] == "NT1231-U04-G1"
    assert receipt["evidence"]["api_probe"]["api_surface_count"] == 33
    assert receipt["evidence"]["api_probe"]["local_invocation_count"] == 153
    assert receipt["evidence"]["callbacks_observed"] == [
        "on_bar",
        "on_order_filled",
        "on_start",
    ]
    assert receipt["evidence"]["callbacks_unobserved"] == ["on_order_rejected"]


def test_characterization_names_final_v1_and_schema8_seam_without_v2_import() -> None:
    characterization = (
        ROOT
        / "docs/implementation/p1-real-nautilus/current-runtime-characterization.md"
    ).read_text()
    api_map = (
        ROOT / "docs/implementation/p1-real-nautilus/upstream-api-map.md"
    ).read_text()
    assert "1.231.0" in characterization
    assert "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317" in characterization
    assert "schema 8" in characterization
    assert "not imported, installed or run" in characterization
    assert "Dynamic imports" in api_map
    assert "provider/network adapters" in api_map
