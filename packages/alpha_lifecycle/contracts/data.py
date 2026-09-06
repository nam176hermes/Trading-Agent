"""P3 dataset, fold, and point-in-time evidence contracts."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator

from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime

from .base import DecimalText, DigestModel, Sha256, StrictModel, Token


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a JSON array")
    return tuple(value)


Refs = Annotated[
    tuple[ArtifactRefV1, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=5000)
]
Limitations = Annotated[
    tuple[Token, ...], BeforeValidator(_tuple), Field(max_length=16)
]
Vintage = Literal[
    "RETROSPECTIVE_CURRENT_ARCHIVE",
    "HISTORICAL_VINTAGE_VERIFIED",
    "LIVE_OBSERVED_APPEND_ONLY",
]


def _day(value: object) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("day must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("day must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError("day must be canonical ISO date text")
    return parsed


Day = Annotated[date, BeforeValidator(_day)]


class DateRange(StrictModel):
    start: Day
    end: Day

    @model_validator(mode="after")
    def _ordered(self) -> "DateRange":
        if self.start > self.end:
            raise ValueError("date range must be ordered")
        return self


class DailyBar(DigestModel):
    schema_version: Literal["p3-daily-bar-v1"]
    date: Day
    instrument: Literal["BTCUSDT.BINANCE"]
    opened_at: CanonicalUtcDateTime
    closed_at_exclusive: CanonicalUtcDateTime
    raw_close_time: Annotated[int, Field(ge=0)]
    raw_timestamp_unit: Literal["MILLISECONDS", "MICROSECONDS"]
    open: DecimalText
    high: DecimalText
    low: DecimalText
    close: DecimalText
    base_volume: DecimalText
    quote_volume: DecimalText
    trade_count: Annotated[int, Field(ge=0)]
    provider_published_at: CanonicalUtcDateTime | None
    system_observed_at: CanonicalUtcDateTime
    ingested_at: CanonicalUtcDateTime
    partition_ref: ArtifactRefV1
    row_ordinal: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _bar(self) -> "DailyBar":
        opening, high, low, closing = map(
            Decimal, (self.open, self.high, self.low, self.close)
        )
        if min(opening, high, low, closing) <= 0 or not (
            low <= opening <= high and low <= closing <= high
        ):
            raise ValueError("OHLC values are invalid")
        if self.closed_at_exclusive - self.opened_at != timedelta(days=1):
            raise ValueError("daily bar duration must be exactly one day")
        if not (
            self.system_observed_at >= self.closed_at_exclusive
            and self.ingested_at >= self.system_observed_at
        ):
            raise ValueError("observation and ingestion times are invalid")
        return self


class DatasetEvidence(DigestModel):
    schema_version: Literal["p3-dataset-evidence-v1"]
    snapshot_ref: ArtifactRefV1
    query_digest: Sha256
    segment: Literal["RESEARCH", "HOLDOUT", "BUFFER"]
    date_range: DateRange
    usable_rows: Annotated[int, Field(ge=0)]
    row_refs: Refs
    ordered_rows_digest: Sha256
    vintage_class: Vintage
    observed_cutoff: CanonicalUtcDateTime
    limitations: Limitations

    @model_validator(mode="after")
    def _row_count(self) -> "DatasetEvidence":
        expected = {"HOLDOUT": 365, "BUFFER": 1}.get(self.segment)
        if self.segment == "RESEARCH" and self.usable_rows < 750:
            raise ValueError("research dataset requires at least 750 usable rows")
        if expected is not None and self.usable_rows != expected:
            raise ValueError(f"{self.segment} dataset row count is invalid")
        if len(self.row_refs) != self.usable_rows:
            raise ValueError("dataset row references do not match usable rows")
        return self


class Fold(DigestModel):
    schema_version: Literal["p3-fold-v1"]
    fold_id: Literal["F1", "F2", "F3", "H1"]
    return_end_range: DateRange
    context_start: Day
    decision_start: Day
    decision_end: Day
    return_count: Annotated[int, Field(ge=0)]
    decision_row_refs: Refs
    return_row_refs: Refs
    snapshot_ref: ArtifactRefV1

    @model_validator(mode="after")
    def _timing(self) -> "Fold":
        if (
            self.decision_start != self.return_end_range.start - timedelta(days=1)
            or self.decision_end != self.return_end_range.end - timedelta(days=1)
            or len(self.decision_row_refs) != self.return_count
            or len(self.return_row_refs) != self.return_count
        ):
            raise ValueError("fold decision and return rows are inconsistent")
        return self


class FoldManifest(DigestModel):
    schema_version: Literal["p3-fold-manifest-v1"]
    mode: Literal["OOS", "HOLDOUT"]
    folds: Annotated[
        tuple[Fold, ...], BeforeValidator(_tuple), Field(min_length=1, max_length=3)
    ]
    static_policy_digest: Sha256
    dataset_evidence_ref: ArtifactRefV1

    @model_validator(mode="after")
    def _folds(self) -> "FoldManifest":
        ids = tuple(item.fold_id for item in self.folds)
        if ids != (("F1", "F2", "F3") if self.mode == "OOS" else ("H1",)):
            raise ValueError("fold manifest IDs do not match mode")
        ranges = tuple(item.return_end_range for item in self.folds)
        if any(left.end >= right.start for left, right in zip(ranges, ranges[1:])):
            raise ValueError("fold return ranges overlap")
        return self


class PITProof(DigestModel):
    schema_version: Literal["p3-p-i-t-proof-v1"]
    dataset_ref: ArtifactRefV1
    fold_manifest_ref: ArtifactRefV1
    vintage_class: Vintage
    historical_vintage_verified: bool
    revision_proof_ref: ArtifactRefV1
    no_future_suite_ref: ArtifactRefV1
    limitations: Limitations

    @model_validator(mode="after")
    def _vintage(self) -> "PITProof":
        if self.vintage_class == "RETROSPECTIVE_CURRENT_ARCHIVE" and self.historical_vintage_verified:
            raise ValueError("retrospective data cannot claim historical-vintage proof")
        return self


class BufferOpen(DigestModel):
    schema_version: Literal["p3-buffer-open-v1"]
    date: Day
    instrument: Literal["BTCUSDT.BINANCE"]
    opened_at: CanonicalUtcDateTime
    open: DecimalText
    source_evidence_ref: ArtifactRefV1
    observed_at: CanonicalUtcDateTime


__all__ = [
    "BufferOpen", "DailyBar", "DatasetEvidence", "DateRange", "Fold",
    "FoldManifest", "PITProof",
]
