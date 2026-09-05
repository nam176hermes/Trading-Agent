from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P3_ROOT = ROOT / "docs" / "implementation" / "p3"


def _load(name: str) -> dict[str, object]:
    return json.loads((P3_ROOT / name).read_text(encoding="utf-8"))


def test_p3_v21_decision_binds_the_verified_pack_and_clean_baseline() -> None:
    """Break caught: M1 starts from an unverified source or policy choice."""
    integration = _load("source-integration-map-v21.json")
    decision = _load("decision-record-v21.json")

    assert integration["source_commit"] == "9b625aa766688a087b26cd581e07545a1dfd5009"
    assert integration["source_tree"] == "b7f6c964989966a6875ff16633fbbfbb441d7c2b"
    assert decision["policy_file_sha256"] == (
        "75a9030d017df1de47c5c42aeb95b612f50fd34630c99f82ffd8d200da506336"
    )
    assert decision["operator_decision"] == "ACCEPTED_IMPLEMENT_M1"
    assert decision["authority"] == {
        "broker": False,
        "live": False,
        "network": False,
        "production": False,
    }
    assert integration["baseline_migration_head"] == "0019_p2_security_master"


def test_p3_v21_integration_map_keeps_the_existing_owners() -> None:
    """Break caught: a packet silently replaces an established owner."""
    integration = _load("source-integration-map-v21.json")
    entries = {entry["capability"]: entry for entry in integration["entries"]}

    assert entries["data_snapshot"]["symbol"] == "build_snapshot_v3"
    assert entries["metrics"]["symbol"] == "calculate_performance_metrics"
    assert entries["qualification"]["symbol"] == "evaluate_alpha_qualification"
    assert entries["registry"]["symbol"] == "AlphaRegistry.append"
    assert entries["event_append"]["symbol"].endswith("append_domain_event")
    assert integration["pack_file_sha256"] == (
        "4ba951a2ac6a155f30b3990344e87a39eae53519474b513fa7ea96a78a09f0b7"
    )
