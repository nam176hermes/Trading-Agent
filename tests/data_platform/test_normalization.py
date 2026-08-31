from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from packages.data_contracts import PITQueryMode, PITQueryV1
from packages.data_normalization import (
    AdjustmentFactorV1,
    NormalizationError,
    NormalizationMode,
    normalize_price_quantity,
)


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def factor(*, visible_at: datetime, suffix: int = 1) -> AdjustmentFactorV1:
    return AdjustmentFactorV1(
        action_id=UUID(f"30000000-0000-4000-8000-{suffix:012d}"),
        mode=NormalizationMode.SPLIT_ADJUSTED,
        effective_at=T0 + timedelta(days=2),
        source_available_at=visible_at,
        system_observed_at=visible_at + timedelta(minutes=1),
        ingested_at=visible_at + timedelta(minutes=2),
        price_factor="0.5",
        quantity_factor="2",
    )


def test_split_adjustment_is_decimal_pit_bounded_and_does_not_mutate_raw() -> None:
    query = PITQueryV1(
        mode=PITQueryMode.MARKET_AVAILABLE,
        valid_at=T0,
        cutoff=T0 + timedelta(days=1),
    )
    price = Decimal("100")
    quantity = Decimal("3")

    adjusted = normalize_price_quantity(
        price=price,
        quantity=quantity,
        event_at=T0,
        mode=NormalizationMode.SPLIT_ADJUSTED,
        query=query,
        factors=(factor(visible_at=T0 + timedelta(hours=1)),),
    )

    assert adjusted == (Decimal("50.0"), Decimal("6"))
    assert (price, quantity) == (Decimal("100"), Decimal("3"))


def test_raw_mode_never_applies_factors_and_future_knowledge_is_invisible() -> None:
    query = PITQueryV1(
        mode=PITQueryMode.SYSTEM_OBSERVED,
        valid_at=T0,
        cutoff=T0 + timedelta(hours=1),
    )
    future = factor(visible_at=T0 + timedelta(hours=2))

    assert normalize_price_quantity(
        price=Decimal("100"), quantity=Decimal("3"), event_at=T0,
        mode=NormalizationMode.RAW, query=query, factors=(future,),
    ) == (Decimal("100"), Decimal("3"))
    assert normalize_price_quantity(
        price=Decimal("100"), quantity=Decimal("3"), event_at=T0,
        mode=NormalizationMode.SPLIT_ADJUSTED, query=query, factors=(future,),
    ) == (Decimal("100"), Decimal("3"))


def test_normalization_uses_the_visibility_field_selected_by_pit_mode() -> None:
    item = factor(visible_at=T0)
    query = PITQueryV1(
        mode=PITQueryMode.SYSTEM_OBSERVED,
        valid_at=T0,
        cutoff=T0 + timedelta(seconds=30),
    )

    assert normalize_price_quantity(
        price=Decimal("100"), quantity=Decimal("3"), event_at=T0,
        mode=NormalizationMode.SPLIT_ADJUSTED, query=query, factors=(item,),
    ) == (Decimal("100"), Decimal("3"))


def test_normalization_rejects_duplicate_actions_or_nonpositive_values() -> None:
    query = PITQueryV1(
        mode=PITQueryMode.AS_INGESTED,
        valid_at=T0,
        cutoff=T0 + timedelta(days=1),
    )
    item = factor(visible_at=T0)

    with pytest.raises(NormalizationError, match="duplicate"):
        normalize_price_quantity(
            price=Decimal("100"), quantity=Decimal("3"), event_at=T0,
            mode=NormalizationMode.SPLIT_ADJUSTED, query=query, factors=(item, item),
        )
    with pytest.raises(NormalizationError, match="positive"):
        normalize_price_quantity(
            price=Decimal("0"), quantity=Decimal("3"), event_at=T0,
            mode=NormalizationMode.SPLIT_ADJUSTED, query=query, factors=(),
        )
