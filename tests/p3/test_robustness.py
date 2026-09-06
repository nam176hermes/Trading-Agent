from __future__ import annotations

from decimal import Decimal

import pytest

from packages.alpha_lifecycle.capacity import CapacityError, participation_samples
from packages.alpha_lifecycle.robustness import delayed_weights, regime_excess
from packages.alpha_lifecycle.trials import deterministic_trial_keys


def test_delayed_scenario_shifts_precomputed_targets() -> None:
    assert delayed_weights((1, 1, 0, 1)) == (0, 1, 1, 0)


def test_capacity_uses_only_nonzero_transitions_and_rejects_bad_volume() -> None:
    assert participation_samples(
        (Decimal("1"), Decimal("0"), Decimal("0.5")),
        (Decimal("1000000"), Decimal("1"), Decimal("500000")),
    ) == (Decimal("0.1"), Decimal("0.1"))
    with pytest.raises(CapacityError, match="E_VOLUME"):
        participation_samples((Decimal("1"),), (Decimal("0"),))


def test_regime_excess_compounds_same_labeled_samples() -> None:
    labels = ("BULL_LOW_VOL", "BEAR_LOW_VOL", "HIGH_VOL")
    result = regime_excess(
        (Decimal("0.1"), Decimal("-0.1"), Decimal("0.2")),
        (Decimal("0"), Decimal("0"), Decimal("0.1")),
        labels,
    )
    assert result["HIGH_VOL"] == Decimal("0.1")


def test_trial_keys_cover_only_seven_preregistered_paths() -> None:
    keys = deterministic_trial_keys("p3-btc-d1-e1", "a0.donchian-20-10-close-confirm")
    assert len(keys) == len(set(keys)) == 7
    assert all("attempt" not in key for key in keys)
