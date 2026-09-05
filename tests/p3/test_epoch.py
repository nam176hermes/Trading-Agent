from __future__ import annotations

from datetime import date

from packages.alpha_lifecycle.contracts.data import DateRange
from packages.alpha_lifecycle.epoch import classify_period, logical_trial_id


def test_result_guided_family_treats_prior_oos_as_development() -> None:
    """Break caught: an already observed OOS interval is relabeled unseen."""
    old_oos = DateRange(start="2024-09-01", end="2025-08-31")
    assert classify_period(
        old_oos,
        exposed_ranges=(old_oos,),
        result_guided=True,
    ) == "DEVELOPMENT"
    assert classify_period(
        DateRange(start="2026-09-01", end="2027-08-31"),
        exposed_ranges=(old_oos,),
        result_guided=True,
    ) == "UNSEEN"


def test_technical_retry_keeps_the_same_logical_trial() -> None:
    """Break caught: retry attempts become new economic selection trials."""
    first = logical_trial_id("p3-btc-d1-e1", "a0.donchian-20-10-close-confirm", "BASE")
    retry = logical_trial_id("p3-btc-d1-e1", "a0.donchian-20-10-close-confirm", "BASE")
    assert first == retry
    assert "attempt" not in first


def test_partial_overlap_is_also_development() -> None:
    exposed = DateRange(start=date(2024, 1, 1), end=date(2024, 12, 31))
    proposed = DateRange(start=date(2024, 12, 31), end=date(2025, 12, 31))
    assert classify_period(proposed, exposed_ranges=(exposed,), result_guided=True) == "DEVELOPMENT"
