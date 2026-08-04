"""Fail-closed canonical market-data ingestion through the approved repository."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol

from packages.domain import MarketSnapshot
from packages.job_contracts import MarketDataSnapshotRequest, SnapshotPayload

from .fixture import (
    MarketDataProvider,
    P10_INSTRUMENT,
    P10_INTERVAL_SECONDS,
    P10_PROVIDER,
    P10_TIMEFRAME,
    ProviderObservation,
)
from .repository import MarketDataPersistenceOutcome


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_EVIDENCE_BYTES = 1024 * 1024


class MarketDataIngestionError(RuntimeError):
    """Provider data is unsafe or persistence did not preserve its identity."""


class MarketDataSnapshotRepository(Protocol):
    def persist(self, snapshot: MarketSnapshot) -> MarketDataPersistenceOutcome: ...


class MarketDataIngestor:
    """Validate one injected P10 observation before database-owned persistence."""

    def __init__(
        self,
        provider: MarketDataProvider,
        repository: MarketDataSnapshotRepository,
    ) -> None:
        self._provider = provider
        self._repository = repository

    def ingest(self, payload: SnapshotPayload) -> MarketDataPersistenceOutcome:
        if not isinstance(payload, SnapshotPayload) or payload.market_data is None:
            raise MarketDataIngestionError("market-data snapshot payload is required")
        request = payload.market_data
        try:
            observation = self._provider.fetch(request)
        except Exception as exc:
            raise MarketDataIngestionError("provider observation is unavailable") from exc
        snapshot = self._validated_snapshot(observation, request)
        try:
            outcome = self._repository.persist(snapshot)
        except Exception as exc:
            raise MarketDataIngestionError("canonical snapshot persistence failed") from exc
        if (
            not isinstance(outcome, MarketDataPersistenceOutcome)
            or outcome.snapshot_digest != snapshot.digest
        ):
            raise MarketDataIngestionError("persistence did not preserve snapshot identity")
        return outcome

    @staticmethod
    def _validated_snapshot(
        observation: object, request: MarketDataSnapshotRequest
    ) -> MarketSnapshot:
        if not isinstance(observation, ProviderObservation):
            raise MarketDataIngestionError("provider observation has invalid type")
        if (
            not isinstance(observation.raw_evidence, bytes)
            or not observation.raw_evidence
            or len(observation.raw_evidence) > _MAX_EVIDENCE_BYTES
        ):
            raise MarketDataIngestionError("provider evidence is invalid")
        try:
            snapshot = MarketSnapshot.model_validate(observation.snapshot)
        except Exception as exc:
            raise MarketDataIngestionError("provider snapshot is invalid") from exc
        if snapshot.provenance.raw_evidence_sha256 != hashlib.sha256(
            observation.raw_evidence
        ).hexdigest():
            raise MarketDataIngestionError("provider evidence digest does not match")
        if observation.raw_evidence != MarketDataIngestor._canonical_evidence(snapshot):
            raise MarketDataIngestionError("provider evidence is not canonical")
        if (
            request.provider != P10_PROVIDER
            or request.instrument != P10_INSTRUMENT
            or request.timeframe != P10_TIMEFRAME
            or request.interval_seconds != P10_INTERVAL_SECONDS
            or snapshot.instrument.canonical != request.instrument
            or snapshot.timeframe.value != request.timeframe
            or snapshot.provenance.provider != request.provider
            or snapshot.provenance.observed_at != request.requested_at
            or snapshot.provenance.fetched_at != request.requested_at
            or snapshot.known_at != request.requested_at
            or not _SHA256.fullmatch(snapshot.provenance.raw_evidence_sha256)
            or not snapshot.continuity.is_continuous
        ):
            raise MarketDataIngestionError("provider snapshot is outside fixture authority")
        return snapshot

    @staticmethod
    def _canonical_evidence(snapshot: MarketSnapshot) -> bytes:
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
                    for candle in snapshot.candles
                ],
                "instrument": snapshot.instrument.canonical,
                "provider": snapshot.provenance.provider,
                "timeframe": snapshot.timeframe.value,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


__all__ = [
    "MarketDataIngestionError",
    "MarketDataIngestor",
    "MarketDataSnapshotRepository",
]
