from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_pit_adversarial_manifest_covers_every_required_failure_class() -> None:
    manifest = json.loads(
        (
            ROOT
            / "docs/implementation/pre-p3/p2-pit-adversarial-suite-v1.json"
        ).read_text()
    )
    cases = {item["case_id"]: item["node_id"] for item in manifest["cases"]}

    assert set(cases) == {
        "CORPORATE_ACTION",
        "LATE_ARRIVING_DATA",
        "LISTING_DELISTING",
        "PRICE_BAR_REVISION",
        "PROVIDER_CONFLICT",
        "SECURITY_METADATA_CORRECTION",
        "SYMBOL_MAPPING_CHANGE",
    }
    assert all((ROOT / node.split("::", 1)[0]).is_file() for node in cases.values())
    assert manifest["required_cutoff_rule"] == (
        "actual_at_T1_correction_known_at_T3_query_cutoff_T2_must_select_only_T1"
    )
