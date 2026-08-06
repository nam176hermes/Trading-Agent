"""Strict immutable metadata binding one normalized market dataset."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from packages.domain import InstrumentId, MarketSnapshot, MarketTimeframe, require_utc


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class CatalogManifestError(ValueError):
    """A dataset cannot be represented by a complete immutable manifest."""


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class MarketDatasetManifestV1(_CatalogModel):
    """Content-addressed metadata for one local normalized candle dataset."""

    schema_version: Literal["market-dataset-manifest-v1"] = "market-dataset-manifest-v1"
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    instrument: InstrumentId
    timeframe: MarketTimeframe
    first_event_at: datetime
    last_event_at: datetime
    row_count: StrictInt = Field(ge=1, le=4096)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_rows_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parquet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gap_report: tuple[datetime, ...] = Field(max_length=4096)
    duplicate_report: tuple[datetime, ...] = Field(max_length=4096)
    importer_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")

    @field_validator("first_event_at", "last_event_at")
    @classmethod
    def _utc_event_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @field_validator("gap_report", "duplicate_report")
    @classmethod
    def _utc_report_times(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(require_utc(value) for value in values)

    @model_validator(mode="after")
    def _complete_and_canonical(self) -> "MarketDatasetManifestV1":
        if self.last_event_at < self.first_event_at:
            raise ValueError("last_event_at must not be before first_event_at")
        for digest in (
            self.content_digest,
            self.raw_evidence_sha256,
            self.canonical_rows_sha256,
            self.parquet_sha256,
        ):
            if _SHA256.fullmatch(digest) is None:
                raise ValueError("catalog digests must be lowercase sha256 hex")
        if _SAFE_VERSION.fullmatch(self.importer_version) is None:
            raise ValueError("importer_version must be a safe bounded version")
        interval = self.timeframe.interval_seconds
        for field_name in ("gap_report", "duplicate_report"):
            values = getattr(self, field_name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must be sorted and unique")
            for value in values:
                delta = value - _EPOCH
                if value.microsecond or (delta.days * 86_400 + delta.seconds) % interval:
                    raise ValueError(f"{field_name} values must align to the timeframe")
        return self

    @classmethod
    def from_snapshot(
        cls,
        snapshot: MarketSnapshot,
        *,
        raw_evidence_sha256: str,
        canonical_rows_sha256: str,
        parquet_sha256: str,
        importer_version: str,
    ) -> "MarketDatasetManifestV1":
        if not isinstance(snapshot, MarketSnapshot):
            raise CatalogManifestError("snapshot must be a validated MarketSnapshot")
        if raw_evidence_sha256 != snapshot.provenance.raw_evidence_sha256:
            raise CatalogManifestError("raw evidence digest does not match snapshot provenance")
        continuity = snapshot.continuity
        return cls(
            provider=snapshot.provenance.provider,
            instrument=snapshot.instrument,
            timeframe=snapshot.timeframe,
            first_event_at=snapshot.candles[0].open_time,
            last_event_at=snapshot.candles[-1].open_time,
            row_count=len(snapshot.candles),
            content_digest=snapshot.digest,
            raw_evidence_sha256=raw_evidence_sha256,
            canonical_rows_sha256=canonical_rows_sha256,
            parquet_sha256=parquet_sha256,
            gap_report=continuity.missing_open_times,
            duplicate_report=continuity.duplicate_open_times,
            importer_version=importer_version,
        )
