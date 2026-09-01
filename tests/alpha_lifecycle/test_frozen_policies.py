from __future__ import annotations

import json
from pathlib import Path

from packages.alpha_lifecycle.baselines import BaselineId


ROOT = Path(__file__).parents[2]
POLICIES = ROOT / "docs/implementation/pre-p3"


def test_baseline_policy_is_frozen_and_matches_the_closed_implementation_set() -> None:
    policy = json.loads((POLICIES / "p3-baseline-suite-v1.json").read_text())

    assert policy["schema_version"] == "p3-baseline-suite-v1"
    assert policy["status"] == "FROZEN"
    assert tuple(item["baseline_id"] for item in policy["baselines"]) == tuple(
        item.value for item in BaselineId
    )
    assert policy["asset_universe"] == ["BTCUSDT.BINANCE"]
    assert policy["risk_constraints"] == {
        "allow_leverage": False,
        "allow_short": False,
        "long_only": True,
    }
    assert policy["qualification_fixture"]["minimum_daily_closes_for_real_campaign"] == 750


def test_evaluation_policy_is_frozen_before_candidate_results_exist() -> None:
    policy = json.loads((POLICIES / "p3-evaluation-protocol-v1.json").read_text())

    assert policy["schema_version"] == "p3-evaluation-protocol-v1"
    assert policy["status"] == "FROZEN"
    assert tuple(item["criterion_id"] for item in policy["criteria"]) == tuple(
        f"C{index:02d}" for index in range(1, 17)
    )
    assert len({item["failure_code"] for item in policy["criteria"]}) == 16
    assert policy["undefined_metric_policy"] == "null-with-stable-failure-code"
