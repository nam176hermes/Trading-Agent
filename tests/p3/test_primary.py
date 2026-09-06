from decimal import Decimal

from packages.alpha_lifecycle.primary_selection import ranking_key


def test_primary_ranking_uses_frozen_order() -> None:
    better_median = ranking_key("a1",(Decimal("0.03"),Decimal("0.03"),Decimal("0.01")),Decimal("0.02"),Decimal("0.2"))
    better_drawdown = ranking_key("a0",(Decimal("0.02"),)*3,Decimal("0.02"),Decimal("0.1"))
    worse_drawdown = ranking_key("a1",(Decimal("0.02"),)*3,Decimal("0.02"),Decimal("0.2"))
    assert better_median < better_drawdown < worse_drawdown
