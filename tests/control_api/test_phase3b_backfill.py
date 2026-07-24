from __future__ import annotations

from pathlib import Path

from trading_control.phase3b_backfill import (
    FieldAction,
    build_phase3b_backfill_plan,
    decide_field_action,
)
from trading_control.phase3b_sources import ProvenanceQuality, ReasonCode


REAL_ROOT = Path("/home/thenam176/.hermes/crypto-research")


def test_exact_replaces_unknown() -> None:
    result = decide_field_action(
        stored_value=None,
        stored_quality=ProvenanceQuality.UNKNOWN,
        incoming_value="123.45",
        incoming_quality=ProvenanceQuality.EXACT,
    )

    assert result.action is FieldAction.UPDATE
    assert result.reason_code is None


def test_derived_does_not_overwrite_exact() -> None:
    result = decide_field_action(
        stored_value="123.45",
        stored_quality=ProvenanceQuality.EXACT,
        incoming_value="120",
        incoming_quality=ProvenanceQuality.DERIVED,
    )

    assert result.action is FieldAction.IGNORE
    assert result.reason_code is ReasonCode.LOWER_QUALITY_SOURCE_IGNORED


def test_equal_quality_different_value_is_conflict() -> None:
    result = decide_field_action(
        stored_value="first",
        stored_quality=ProvenanceQuality.EXACT,
        incoming_value="second",
        incoming_quality=ProvenanceQuality.EXACT,
    )

    assert result.action is FieldAction.CONFLICT
    assert result.reason_code is ReasonCode.EQUAL_QUALITY_CONFLICT


def test_equal_quality_same_value_is_unchanged() -> None:
    result = decide_field_action(
        stored_value="same",
        stored_quality=ProvenanceQuality.EXACT,
        incoming_value="same",
        incoming_quality=ProvenanceQuality.EXACT,
    )

    assert result.action is FieldAction.UNCHANGED
    assert result.reason_code is None


def test_unknown_never_replaces_a_value() -> None:
    result = decide_field_action(
        stored_value="known",
        stored_quality=ProvenanceQuality.EXACT,
        incoming_value=None,
        incoming_quality=ProvenanceQuality.UNKNOWN,
    )

    assert result.action is FieldAction.IGNORE
    assert result.reason_code is ReasonCode.LOWER_QUALITY_SOURCE_IGNORED


def test_real_backfill_plan_has_only_approved_evidence() -> None:
    plan = build_phase3b_backfill_plan(REAL_ROOT)

    assert plan.inventory_hash == (
        "dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce"
    )
    assert len(plan.decision_prices) == 16517
    assert all(item.quality is ProvenanceQuality.EXACT for item in plan.decision_prices)
    assert len(plan.decision_snippets) == 16517
    assert sum(item.quality is ProvenanceQuality.EXACT for item in plan.decision_snippets) == 16516
    assert sum(item.quality is ProvenanceQuality.UNKNOWN for item in plan.decision_snippets) == 1
    assert len(plan.cost_symbols) == 20
    assert sum(len(item.symbols) for item in plan.cost_symbols) == 200
    assert len(plan.asset_lineage) == 41039
    assert {item.asset_id for item in plan.asset_lineage} == set(plan.asset_ids)
