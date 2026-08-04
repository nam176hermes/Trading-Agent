"""Core-owned, injected deterministic candle fixture for P10 source proofs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)
from packages.job_contracts import MarketDataSnapshotRequest


P10_PROVIDER = "deterministic-provider-free-fixture-v1"
P10_INSTRUMENT = "crypto_spot:FIXTURE:BTC"
P10_TIMEFRAME = "1m"
P10_INTERVAL_SECONDS = 60
_INSTRUMENT = InstrumentId(
    symbol="BTC", venue="FIXTURE", product_type=ProductType.CRYPTO_SPOT
)


class MarketDataProviderError(ValueError):
    """The injected provider could not produce a closed P10 observation."""


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """Provider output whose raw evidence is verified before persistence."""

    snapshot: MarketSnapshot
    raw_evidence: bytes


class MarketDataProvider(Protocol):
    def fetch(self, request: MarketDataSnapshotRequest) -> ProviderObservation: ...


def _canonical_evidence(candles: tuple[MarketCandle, ...]) -> bytes:
    return json.dumps(
        {
            "candles": [
                {
                    "close": str(candle.close),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "open": str(candle.open),
                    "open_time": candle.open_time.isoformat().replace("+00:00", "Z"),
                    "volume": str(candle.volume),
                }
                for candle in candles
            ],
            "instrument": P10_INSTRUMENT,
            "provider": P10_PROVIDER,
            "timeframe": P10_TIMEFRAME,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class DeterministicProviderFreeFixture:
    """Return the one core-owned P10 fixture without configuration or I/O."""

    def fetch(self, request: MarketDataSnapshotRequest) -> ProviderObservation:
        if not isinstance(request, MarketDataSnapshotRequest):
            raise MarketDataProviderError("market-data request is invalid")
        if (
            request.provider != P10_PROVIDER
            or request.instrument != P10_INSTRUMENT
            or request.timeframe != P10_TIMEFRAME
            or request.interval_seconds != P10_INTERVAL_SECONDS
            or request.provider_retry_limit != 1
            or request.requested_at.second != 0
            or request.requested_at.microsecond != 0
        ):
            raise MarketDataProviderError("market-data request is outside fixture authority")

        observed_at = request.requested_at
        candles = (
            MarketCandle(
                instrument=_INSTRUMENT,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=observed_at - timedelta(minutes=2),
                open=Decimal("64200"),
                high=Decimal("64220"),
                low=Decimal("64190"),
                close=Decimal("64210.5"),
                volume=Decimal("12.5"),
            ),
            MarketCandle(
                instrument=_INSTRUMENT,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=observed_at - timedelta(minutes=1),
                open=Decimal("64210.5"),
                high=Decimal("64240"),
                low=Decimal("64200"),
                close=Decimal("64230"),
                volume=Decimal("8.25"),
            ),
        )
        raw_evidence = _canonical_evidence(candles)
        return ProviderObservation(
            snapshot=MarketSnapshot(
                instrument=_INSTRUMENT,
                timeframe=MarketTimeframe.ONE_MINUTE,
                candles=candles,
                provenance=MarketDataProvenance(
                    provider=P10_PROVIDER,
                    observed_at=observed_at,
                    fetched_at=observed_at,
                    raw_evidence_sha256=hashlib.sha256(raw_evidence).hexdigest(),
                    schema_version="p10-provider-free-fixture-v1",
                    normalization_version="p10-fixture-normalization-v1",
                ),
                known_at=observed_at,
                schema_version="p10-market-data-v1",
                normalization_version="p10-fixture-normalization-v1",
            ),
            raw_evidence=raw_evidence,
        )


__all__ = [
    "DeterministicProviderFreeFixture",
    "MarketDataProvider",
    "MarketDataProviderError",
    "P10_INSTRUMENT",
    "P10_INTERVAL_SECONDS",
    "P10_PROVIDER",
    "P10_TIMEFRAME",
    "ProviderObservation",
]
