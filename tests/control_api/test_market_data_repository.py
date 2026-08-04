"""RED/green repository proof for canonical P10 market-data reads."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from control_api.contracts import FreshnessStatus
from control_api.repositories.market_data import (
    CanonicalMarketDataReadError,
    PostgresCanonicalMarketDataRepository,
)
from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)
from trading_control.db import DatabaseSettings


def canonical_snapshot() -> MarketSnapshot:
    instrument = InstrumentId(
        symbol="BTC", venue="FIXTURE", product_type=ProductType.CRYPTO_SPOT
    )
    observed_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    return MarketSnapshot(
        instrument=instrument,
        timeframe=MarketTimeframe.ONE_MINUTE,
        candles=(
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=observed_at - timedelta(minutes=2),
                open=Decimal("64200"),
                high=Decimal("64220"),
                low=Decimal("64190"),
                close=Decimal("64210.5"),
                volume=Decimal("12.5"),
            ),
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=observed_at - timedelta(minutes=1),
                open=Decimal("64210.5"),
                high=Decimal("64240"),
                low=Decimal("64200"),
                close=Decimal("64230"),
                volume=Decimal("8.25"),
            ),
        ),
        provenance=MarketDataProvenance(
            provider="deterministic-provider-free-fixture-v1",
            observed_at=observed_at,
            fetched_at=observed_at,
            raw_evidence_sha256="a" * 64,
            schema_version="p10-provider-free-fixture-v1",
            normalization_version="p10-fixture-normalization-v1",
        ),
        known_at=observed_at,
        schema_version="p10-market-data-v1",
        normalization_version="p10-fixture-normalization-v1",
    )


class _Cursor:
    def __init__(self, row: object) -> None:
        self._row = row

    def fetchone(self) -> object:
        return self._row


class _Connection:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters: tuple[object, ...]) -> _Cursor:
        self.calls.append((statement, parameters))
        return _Cursor(self._rows.pop(0))


def _settings() -> DatabaseSettings:
    fixed_value = "test-only"
    return DatabaseSettings(
        host="127.0.0.1",
        port=5432,
        database="trading",
        user="trading_reader",
        password=fixed_value,
    )


def _repository(monkeypatch: pytest.MonkeyPatch, row: object):
    connection = _Connection([row])

    @contextmanager
    def connect(_settings: DatabaseSettings, *, read_only: bool):
        assert read_only is True
        yield connection

    monkeypatch.setattr("control_api.repositories.market_data.connect", connect)
    return (
        PostgresCanonicalMarketDataRepository(
            _settings(),
            stale_after_seconds=60,
            clock=lambda: datetime(2026, 8, 4, 12, 0, 30, tzinfo=UTC),
        ),
        connection,
    )


def test_latest_reads_one_canonical_snapshot_with_read_only_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = canonical_snapshot()
    repository, connection = _repository(
        monkeypatch,
        {
            "canonical_snapshot_text": snapshot.canonical_payload_bytes.decode("utf-8"),
            "snapshot_digest": snapshot.digest,
        },
    )

    result = repository.latest(
        instrument="crypto_spot:FIXTURE:BTC", timeframe="1m"
    )

    assert result.snapshot == snapshot
    assert result.snapshot_digest == snapshot.digest
    assert result.freshness.status is FreshnessStatus.FRESH
    assert result.freshness.age_seconds == 30
    statement, parameters = connection.calls[0]
    assert statement.lstrip().startswith("SELECT canonical_snapshot_text, snapshot_digest")
    assert parameters == ("BTC", "FIXTURE", "crypto_spot", "1m")
    assert all(keyword not in statement.upper() for keyword in ("INSERT", "UPDATE", "DELETE"))


def test_repository_rejects_tampered_canonical_row_before_returning_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = canonical_snapshot()
    repository, _ = _repository(
        monkeypatch,
        {
            "canonical_snapshot_text": snapshot.canonical_payload_bytes.decode("utf-8"),
            "snapshot_digest": "b" * 64,
        },
    )

    with pytest.raises(CanonicalMarketDataReadError, match="digest"):
        repository.latest(instrument="crypto_spot:FIXTURE:BTC", timeframe="1m")


def test_repository_rejects_non_p10_query_before_opening_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, connection = _repository(monkeypatch, None)

    with pytest.raises(CanonicalMarketDataReadError, match="closed P10 vocabulary"):
        repository.latest(instrument="crypto_spot:FIXTURE:ETH", timeframe="1m")

    assert connection.calls == []


def test_repository_fails_closed_when_p10_table_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingTableConnection:
        def execute(self, statement: str, parameters: tuple[object, ...]):
            raise psycopg.OperationalError("relation market_data_snapshots does not exist")

    @contextmanager
    def connect(_settings: DatabaseSettings, *, read_only: bool):
        assert read_only is True
        yield MissingTableConnection()

    monkeypatch.setattr("control_api.repositories.market_data.connect", connect)
    repository = PostgresCanonicalMarketDataRepository(_settings(), stale_after_seconds=60)

    with pytest.raises(CanonicalMarketDataReadError, match="unavailable"):
        repository.latest(instrument="crypto_spot:FIXTURE:BTC", timeframe="1m")
