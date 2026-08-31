"""Derived, point-in-time normalization over immutable raw values."""

from __future__ import annotations

from datetime import datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from enum import Enum
import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.data_contracts import PITQueryV1
from packages.domain import require_utc
from packages.engine_contracts.serialization import CanonicalUtcDateTime


_POSITIVE_DECIMAL = re.compile(r"^(?:[1-9]\d*|(?:0|[1-9]\d*)\.\d*[1-9])$", re.ASCII)


class NormalizationError(ValueError):
    """Normalization inputs are ambiguous, non-canonical, or unsafe."""


class NormalizationMode(str, Enum):
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"
    TOTAL_RETURN = "TOTAL_RETURN"
    SCALED_RAW = "SCALED_RAW"


class AdjustmentFactorV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_id: UUID
    mode: NormalizationMode
    effective_at: CanonicalUtcDateTime
    source_available_at: CanonicalUtcDateTime
    system_observed_at: CanonicalUtcDateTime
    ingested_at: CanonicalUtcDateTime
    price_factor: Annotated[str, Field(min_length=1, max_length=128)]
    quantity_factor: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("price_factor", "quantity_factor")
    @classmethod
    def _positive_factor(cls, value: str) -> str:
        if _POSITIVE_DECIMAL.fullmatch(value) is None:
            raise ValueError("adjustment factor must be a canonical positive decimal")
        return value

    @model_validator(mode="after")
    def _not_raw(self) -> "AdjustmentFactorV1":
        if self.mode is NormalizationMode.RAW:
            raise ValueError("RAW normalization cannot have an adjustment factor")
        if not self.source_available_at <= self.system_observed_at <= self.ingested_at:
            raise ValueError("adjustment visibility times are not ordered")
        return self


def normalize_price_quantity(
    *,
    price: Decimal,
    quantity: Decimal,
    event_at: datetime,
    mode: NormalizationMode,
    query: PITQueryV1,
    factors: tuple[AdjustmentFactorV1, ...],
) -> tuple[Decimal, Decimal]:
    if type(price) is not Decimal or type(quantity) is not Decimal:
        raise NormalizationError("price and quantity must be Decimal")
    if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0:
        raise NormalizationError("price and quantity must be finite and positive")
    try:
        event = require_utc(event_at)
        canonical_query = PITQueryV1.model_validate(query)
        canonical_factors = tuple(AdjustmentFactorV1.model_validate(item) for item in factors)
    except Exception as exc:
        raise NormalizationError("normalization inputs are not canonical") from exc
    action_ids = tuple(item.action_id for item in canonical_factors)
    if len(action_ids) != len(set(action_ids)):
        raise NormalizationError("duplicate corporate-action factor")
    if mode is NormalizationMode.RAW:
        return price, quantity

    visible = tuple(
        sorted(
            (
                item
                for item in canonical_factors
                if item.mode is mode
                and event < item.effective_at
                and getattr(item, canonical_query.visibility_field) <= canonical_query.cutoff
            ),
            key=lambda item: (item.effective_at, item.action_id.bytes),
        )
    )
    try:
        with localcontext(Context(prec=80)):
            for item in visible:
                price *= Decimal(item.price_factor)
                quantity *= Decimal(item.quantity_factor)
    except (InvalidOperation, OverflowError) as exc:
        raise NormalizationError("normalization arithmetic failed") from exc
    if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0:
        raise NormalizationError("normalized values must remain finite and positive")
    return price, quantity


__all__ = [
    "AdjustmentFactorV1",
    "NormalizationError",
    "NormalizationMode",
    "normalize_price_quantity",
]
