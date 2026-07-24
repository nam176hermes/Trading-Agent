from __future__ import annotations

from pathlib import Path

from trading_control.real_import import build_real_plan


REAL_ROOT = Path("/home/thenam176/.hermes/crypto-research")


def test_reviewed_real_source_builds_exact_approved_apply_plan() -> None:
    plan = build_real_plan(REAL_ROOT)

    assert plan.inventory_hash == (
        "dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce"
    )
    assert plan.planner_manifest_hash == (
        "06964c9ce162bf0fefa637c0a04d86eaea9b21deae0060ddec1555ba63f20892"
    )
    assert plan.domain_counts == {
        "assets": 17,
        "market_reports": 2186,
        "market_asset_snapshots": 23961,
        "decisions": 16517,
        "signals": 344,
        "capability_evidence": 9,
        "cost_summaries": 1,
        "cost_sessions": 20,
    }
    assert len(plan.invalid_reports) == 86
    assert len(plan.invalid_decisions) == 136
    assert sum(item.legacy_value == "WATCH" for item in plan.invalid_decisions) == 122
    assert sum(item.legacy_value == "WATCH FOR EXIT" for item in plan.invalid_decisions) == 14
    assert sum(len(report.assets) for report in plan.reports) == 23961
    assert sum(len(item.audit_codes) for item in plan.decisions) == 2349
    assert max(report.as_of for report in plan.reports).isoformat() == (
        "2026-06-25T04:54:37.766581+00:00"
    )
    latest = max(plan.reports, key=lambda item: (item.as_of, item.report_id))
    assert len(latest.assets) == 10
    assert plan.canonical_total == 43055
    assert plan.quarantine_total == 222
