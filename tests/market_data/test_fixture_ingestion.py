"""RED/green proof for the injected, provider-free P10 candle fixture."""

from __future__ import annotations

import hashlib
import socket
import urllib.request
from datetime import UTC, datetime

import pytest

from packages.job_contracts import JobType, SnapshotPayload, parse_payload
from services.market_data.fixture import DeterministicProviderFreeFixture, ProviderObservation
from services.market_data.ingestion import MarketDataIngestionError, MarketDataIngestor
from services.market_data.repository import MarketDataPersistenceOutcome


_REQUESTED_AT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
_EVIDENCE = (
    b'{"candles":[{"close":"64210.5","high":"64220","low":"64190",'
    b'"open":"64200","open_time":"2026-08-04T11:58:00Z","volume":"12.5"},'
    b'{"close":"64230","high":"64240","low":"64200","open":"64210.5",'
    b'"open_time":"2026-08-04T11:59:00Z","volume":"8.25"}],'
    b'"instrument":"crypto_spot:FIXTURE:BTC",'
    b'"provider":"deterministic-provider-free-fixture-v1","timeframe":"1m"}'
)


def _payload() -> SnapshotPayload:
    parsed = parse_payload(
        JobType.SNAPSHOT,
        {
            "scope": "default",
            "requested_as_of": None,
            "market_data": {
                "provider": "deterministic-provider-free-fixture-v1",
                "instrument": "crypto_spot:FIXTURE:BTC",
                "timeframe": "1m",
                "interval_seconds": 60,
                "requested_at": "2026-08-04T12:00:00Z",
                "provider_retry_limit": 1,
            },
        },
    )
    assert isinstance(parsed, SnapshotPayload)
    return parsed


def test_core_owned_fixture_returns_canonical_candles_and_provenance_evidence() -> None:
    request = _payload().market_data
    assert request is not None

    observation = DeterministicProviderFreeFixture().fetch(request)

    assert observation.raw_evidence == _EVIDENCE
    assert observation.snapshot.instrument.canonical == "crypto_spot:FIXTURE:BTC"
    assert observation.snapshot.timeframe.value == "1m"
    assert observation.snapshot.provenance.provider == "deterministic-provider-free-fixture-v1"
    assert observation.snapshot.provenance.raw_evidence_sha256 == hashlib.sha256(_EVIDENCE).hexdigest()
    assert observation.snapshot.provenance.observed_at == _REQUESTED_AT
    assert observation.snapshot.provenance.fetched_at == _REQUESTED_AT
    assert observation.snapshot.known_at == _REQUESTED_AT
    assert [candle.open_time for candle in observation.snapshot.candles] == [
        datetime(2026, 8, 4, 11, 58, tzinfo=UTC),
        datetime(2026, 8, 4, 11, 59, tzinfo=UTC),
    ]
    assert observation.snapshot.continuity.is_continuous is True


def test_core_owned_fixture_never_uses_network(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _payload().market_data
    assert request is not None

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the deterministic fixture must not use network I/O")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    assert DeterministicProviderFreeFixture().fetch(request).snapshot.digest


class _Repository:
    def __init__(self, insertions: list[bool]) -> None:
        self._insertions = insertions
        self.persisted = []

    def persist(self, snapshot):
        self.persisted.append(snapshot)
        return MarketDataPersistenceOutcome(snapshot.digest, self._insertions.pop(0))


def test_ingestor_validates_evidence_before_calling_postgres_function_boundary() -> None:
    fixture = DeterministicProviderFreeFixture()
    repository = _Repository([True])

    outcome = MarketDataIngestor(fixture, repository).ingest(_payload())

    assert outcome == MarketDataPersistenceOutcome(repository.persisted[0].digest, True)
    assert repository.persisted[0].canonical_payload_bytes


def test_ingestor_fails_closed_without_persisting_tampered_provider_evidence() -> None:
    request = _payload().market_data
    assert request is not None
    snapshot = DeterministicProviderFreeFixture().fetch(request).snapshot

    class TamperedProvider:
        def fetch(self, _request):
            return ProviderObservation(snapshot=snapshot, raw_evidence=b"tampered")

    repository = _Repository([True])

    with pytest.raises(MarketDataIngestionError, match="evidence digest"):
        MarketDataIngestor(TamperedProvider(), repository).ingest(_payload())

    assert repository.persisted == []


def test_ingestor_fails_closed_when_digest_matches_incomplete_provider_evidence() -> None:
    request = _payload().market_data
    assert request is not None
    snapshot = DeterministicProviderFreeFixture().fetch(request).snapshot
    raw_evidence = b"{}"
    incomplete_snapshot = snapshot.model_copy(
        update={
            "provenance": snapshot.provenance.model_copy(
                update={"raw_evidence_sha256": hashlib.sha256(raw_evidence).hexdigest()}
            )
        }
    )

    class IncompleteEvidenceProvider:
        def fetch(self, _request):
            return ProviderObservation(snapshot=incomplete_snapshot, raw_evidence=raw_evidence)

    repository = _Repository([True])

    with pytest.raises(MarketDataIngestionError, match="evidence is not canonical"):
        MarketDataIngestor(IncompleteEvidenceProvider(), repository).ingest(_payload())

    assert repository.persisted == []


def test_retry_preserves_one_canonical_snapshot_identity() -> None:
    repository = _Repository([True, False])
    ingestor = MarketDataIngestor(DeterministicProviderFreeFixture(), repository)

    first = ingestor.ingest(_payload())
    second = ingestor.ingest(_payload())

    assert first.inserted is True
    assert second.inserted is False
    assert len({snapshot.digest for snapshot in repository.persisted}) == 1
