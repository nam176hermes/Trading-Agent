"""Frozen deterministic long/flat baseline implementations for P3."""

from __future__ import annotations

from decimal import Decimal, localcontext
from enum import Enum
import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.engine_contracts.serialization import (
    CanonicalUtcDateTime,
    Sha256Hex,
    canonical_json_bytes,
)


class BaselineId(str, Enum):
    CASH = "B0_CASH"
    BUY_AND_HOLD = "B1_BUY_AND_HOLD"
    EQUAL_WEIGHT = "B2_EQUAL_WEIGHT"
    SIMPLE_MOMENTUM = "B3_SIMPLE_MOMENTUM"
    SIMPLE_MEAN_REVERSION = "B4_SIMPLE_MEAN_REVERSION"


class DailyCloseV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    instrument: Annotated[
        str, Field(pattern=r"^[A-Z0-9][A-Z0-9._-]{0,63}$")
    ]
    closed_at: CanonicalUtcDateTime
    close: Annotated[Decimal, Field(gt=0)]

    @model_validator(mode="after")
    def _finite(self) -> "DailyCloseV1":
        if not self.close.is_finite():
            raise ValueError("daily close must be finite")
        return self


class BaselineResultV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    schema_version: Literal["baseline-result-v1"] = "baseline-result-v1"
    baseline_id: BaselineId
    baseline_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    dataset_snapshot_sha256: Sha256Hex
    cost_model_sha256: Sha256Hex
    metrics_sha256: Sha256Hex
    total_return: Annotated[Decimal, Field(ge=Decimal("-1"), le=Decimal("1000"))]
    result_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _bound(self) -> "BaselineResultV1":
        if not self.total_return.is_finite():
            raise ValueError("baseline total return must be finite")
        digest = hashlib.sha256(
            canonical_json_bytes(
                self.model_dump(mode="json", exclude={"result_sha256"})
            )
        ).hexdigest()
        if self.result_sha256 is not None and self.result_sha256 != digest:
            raise ValueError("baseline result digest is invalid")
        object.__setattr__(self, "result_sha256", digest)
        return self


def _validated(rows: tuple[DailyCloseV1, ...]) -> tuple[DailyCloseV1, ...]:
    values = tuple(DailyCloseV1.model_validate(row) for row in rows)
    if not values:
        raise ValueError("baseline requires daily closes")
    if len({row.instrument for row in values}) != 1:
        raise ValueError("baseline requires exactly one instrument")
    times = tuple(row.closed_at for row in values)
    if times != tuple(sorted(times)) or len(times) != len(set(times)):
        raise ValueError("daily closes must be strictly ordered")
    return values


def _momentum(rows: tuple[DailyCloseV1, ...]) -> tuple[Decimal, ...]:
    weight = Decimal(0)
    result: list[Decimal] = []
    month: tuple[int, int] | None = None
    for index, row in enumerate(rows):
        current_month = (row.closed_at.year, row.closed_at.month)
        if index >= 126 and current_month != month:
            weight = Decimal(1) if rows[index - 5].close > rows[index - 126].close else Decimal(0)
        result.append(weight)
        month = current_month
    return tuple(result)


def _mean_reversion(rows: tuple[DailyCloseV1, ...]) -> tuple[Decimal, ...]:
    weight = Decimal(0)
    result: list[Decimal] = []
    with localcontext() as context:
        context.prec = 50
        for index, row in enumerate(rows):
            if index >= 20 and row.closed_at.weekday() == 0:
                closes = tuple(item.close for item in rows[index - 20 : index])
                mean = sum(closes, Decimal(0)) / Decimal(len(closes))
                variance = sum((value - mean) ** 2 for value in closes) / Decimal(len(closes))
                deviation = variance.sqrt()
                z_score = Decimal(0) if deviation == 0 else (row.close - mean) / deviation
                if weight == 0 and z_score <= Decimal(-1):
                    weight = Decimal(1)
                elif weight == 1 and z_score >= 0:
                    weight = Decimal(0)
            result.append(weight)
    return tuple(result)


def baseline_weights(
    baseline_id: BaselineId,
    rows: tuple[DailyCloseV1, ...],
) -> tuple[Decimal, ...]:
    """Return one daily target weight per close; outputs are always long or flat."""

    baseline = BaselineId(baseline_id)
    values = _validated(rows)
    if baseline is BaselineId.CASH:
        return (Decimal(0),) * len(values)
    if baseline in {BaselineId.BUY_AND_HOLD, BaselineId.EQUAL_WEIGHT}:
        return (Decimal(1),) * len(values)
    if baseline is BaselineId.SIMPLE_MOMENTUM:
        return _momentum(values)
    return _mean_reversion(values)


__all__ = ["BaselineId", "BaselineResultV1", "DailyCloseV1", "baseline_weights"]
