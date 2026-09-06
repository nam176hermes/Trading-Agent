from __future__ import annotations

from decimal import Decimal

import pytest

from packages.alpha_lifecycle.regimes import (
    RegimeError,
    assign_regime,
    nearest_rank_threshold,
    require_regime_coverage,
)


def test_nearest_rank_threshold_uses_frozen_75_percent_rule() -> None:
    assert nearest_rank_threshold((Decimal("4"), Decimal("1"), Decimal("3"), Decimal("2"))) == Decimal("3")


def test_regime_boundary_equalities_are_frozen() -> None:
    threshold = Decimal("0.2")
    assert assign_regime(Decimal("0.2"), Decimal("1"), threshold) == "BULL_LOW_VOL"
    assert assign_regime(Decimal("0.2"), Decimal("0"), threshold) == "BEAR_LOW_VOL"
    assert assign_regime(Decimal("0.2000001"), Decimal("-1"), threshold) == "HIGH_VOL"


def test_missing_required_regime_blocks_evidence() -> None:
    with pytest.raises(RegimeError, match="E_REGIME_COVERAGE"):
        require_regime_coverage(("BULL_LOW_VOL", "HIGH_VOL"))
