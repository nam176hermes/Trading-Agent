"""RED/green HTTP contract for canonical read-only P10 market data."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from control_api.app import create_app
from control_api.config import Settings
from control_api.contracts import DataFreshness, FreshnessStatus
from control_api.repositories.market_data import CanonicalMarketDataResult

from test_market_data_repository import canonical_snapshot


class _Repository:
    def __init__(self) -> None:
        snapshot = canonical_snapshot()
        self.result = CanonicalMarketDataResult(
            snapshot=snapshot,
            snapshot_digest=snapshot.digest,
            freshness=DataFreshness(
                status=FreshnessStatus.FRESH,
                as_of=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
                age_seconds=0,
                stale_after_seconds=60,
            ),
        )

    def latest(self, *, instrument: str, timeframe: str) -> CanonicalMarketDataResult:
        if instrument == "crypto_spot:FIXTURE:BTC" and timeframe == "1m":
            return self.result
        return CanonicalMarketDataResult(
            snapshot=None,
            snapshot_digest=None,
            freshness=DataFreshness(
                status=FreshnessStatus.NO_DATA,
                as_of=None,
                age_seconds=None,
                stale_after_seconds=60,
            ),
        )

    def get(self, snapshot_digest: str) -> CanonicalMarketDataResult | None:
        return self.result if snapshot_digest == self.result.snapshot_digest else None


def _client(repository: _Repository) -> TestClient:
    return TestClient(
        create_app(
            Settings(data_root=Path("/tmp")),
            env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
            canonical_market_data_repository=repository,
        )
    )


def test_latest_canonical_market_data_returns_provenance_candles_and_freshness() -> None:
    response = _client(_Repository()).get(
        "/v1/market-data/latest?instrument=crypto_spot:FIXTURE:BTC&timeframe=1m"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["snapshot"]["snapshot"]["instrument"] == {
        "symbol": "BTC",
        "venue": "FIXTURE",
        "product_type": "crypto_spot",
    }
    assert payload["data"]["snapshot"]["snapshot"]["candles"][0]["open"] == "64200"
    assert payload["data"]["snapshot"]["snapshot"]["provenance"]["raw_evidence_sha256"] == "a" * 64
    assert payload["freshness"] == {
        "status": "FRESH",
        "as_of": "2026-08-04T12:00:00Z",
        "age_seconds": 0,
        "stale_after_seconds": 60,
    }


def test_canonical_market_data_rejects_unknown_vocabulary_and_mutation() -> None:
    client = _client(_Repository())

    invalid = client.get(
        "/v1/market-data/latest?instrument=crypto_spot:FIXTURE:ETH&timeframe=1m"
    )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_QUERY"
    assert client.post("/v1/market-data/latest").status_code == 405


def test_snapshot_lookup_returns_not_found_without_legacy_fallback() -> None:
    response = _client(_Repository()).get("/v1/market-data/snapshots/" + "b" * 64)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MARKET_DATA_SNAPSHOT_NOT_FOUND"


def test_snapshot_lookup_rejects_non_digest_path_value() -> None:
    response = _client(_Repository()).get("/v1/market-data/snapshots/not-a-digest")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_legacy_mode_reports_canonical_market_data_unavailable_without_fallback() -> None:
    client = TestClient(
        create_app(
            Settings(data_root=Path("/tmp")),
            env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
        )
    )

    response = client.get(
        "/v1/market-data/latest?instrument=crypto_spot:FIXTURE:BTC&timeframe=1m"
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CANONICAL_MARKET_DATA_UNAVAILABLE"
