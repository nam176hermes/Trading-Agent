from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)
from services.market_data.repository import (
    MarketDataIntegrityError,
    MarketDataSnapshotIdentity,
    PostgresMarketDataRepository,
    PostgresMarketDataSql,
)


def snapshot(*, candles: tuple[MarketCandle, ...] | None = None) -> MarketSnapshot:
    instrument = InstrumentId(
        symbol="BTCUSDT", venue="BINANCE", product_type=ProductType.CRYPTO_SPOT
    )
    observed = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    if candles is None:
        candles = (
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                open=Decimal("100"), high=Decimal("102"), low=Decimal("99"),
                close=Decimal("101"), volume=Decimal("12.50"),
            ),
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
                open=Decimal("101"), high=Decimal("103"), low=Decimal("100"),
                close=Decimal("102"), volume=Decimal("0"),
            ),
        )
    return MarketSnapshot(
        instrument=instrument,
        timeframe=MarketTimeframe.ONE_MINUTE,
        candles=candles,
        provenance=MarketDataProvenance(
            provider="public-feed",
            observed_at=observed,
            fetched_at=observed + timedelta(seconds=1),
            raw_evidence_sha256="a" * 64,
            schema_version="market-v1",
            normalization_version="market-normalization-v1",
        ),
        known_at=observed + timedelta(seconds=2),
        schema_version="market-v1",
        normalization_version="market-normalization-v1",
    )


class Cursor:
    def __init__(self, row: object) -> None:
        self.row = row

    def fetchone(self) -> object:
        return self.row


class Connection:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.transactions = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except BaseException:
            self.rollbacks += 1
            raise
        finally:
            self.transactions -= 1

    def execute(self, statement: str, params: dict[str, object]) -> Cursor:
        self.calls.append((statement, params))
        row = self.rows.pop(0)
        if isinstance(row, BaseException):
            raise row
        return Cursor(row)


class Pool:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @contextmanager
    def connection(self):
        yield self._connection


def test_persist_sends_exact_canonical_text_to_one_database_owned_function() -> None:
    document = snapshot()
    connection = Connection([{"inserted": True}])

    result = PostgresMarketDataRepository(Pool(connection)).persist(document)

    assert result.inserted is True
    assert result.snapshot_digest == document.digest
    statement, params = connection.calls[0]
    assert statement == PostgresMarketDataSql.SAVE_SNAPSHOT
    assert params == {"canonical_snapshot_text": document.canonical_payload_bytes.decode("utf-8")}
    assert connection.transactions == 0


def test_retry_with_same_digest_returns_the_database_idempotent_outcome() -> None:
    document = snapshot()
    connection = Connection([{"inserted": False}])

    result = PostgresMarketDataRepository(Pool(connection)).persist(document)

    assert result.inserted is False
    assert result.snapshot_digest == document.digest


def test_database_conflicting_natural_identity_rolls_back_without_a_client_side_write() -> None:
    document = snapshot()
    connection = Connection([RuntimeError("conflicting market snapshot identity")])
    repository = PostgresMarketDataRepository(Pool(connection))

    with pytest.raises(RuntimeError, match="conflicting market snapshot identity"):
        repository.persist(document)

    assert len(connection.calls) == 1
    assert connection.rollbacks == 1
    assert "INSERT" not in connection.calls[0][0]


def test_load_reconstructs_and_revalidates_exact_canonical_bytes_and_digest() -> None:
    document = snapshot()
    connection = Connection(
        [{"canonical_snapshot_text": document.canonical_payload_bytes.decode("utf-8"), "snapshot_digest": document.digest}]
    )

    loaded = PostgresMarketDataRepository(Pool(connection)).load_by_digest(document.digest)

    assert loaded == document
    assert connection.calls[0] == (
        PostgresMarketDataSql.LOAD_BY_DIGEST,
        {"snapshot_digest": document.digest},
    )


def test_load_by_natural_identity_uses_utc_provenance_and_timeframe_parameters() -> None:
    document = snapshot()
    identity = MarketDataSnapshotIdentity.from_snapshot(document)
    connection = Connection(
        [{"canonical_snapshot_text": document.canonical_payload_bytes.decode("utf-8"), "snapshot_digest": document.digest}]
    )

    loaded = PostgresMarketDataRepository(Pool(connection)).load_by_identity(identity)

    assert loaded == document
    statement, params = connection.calls[0]
    assert statement == PostgresMarketDataSql.LOAD_BY_IDENTITY
    assert params["timeframe"] == "1m"
    assert params["range_start"].tzinfo is UTC
    assert params["observed_at"].tzinfo is UTC
    assert params["provenance_schema_version"] == "market-v1"


def test_load_by_identity_rejects_a_valid_row_for_a_different_identity() -> None:
    document = snapshot()
    requested = MarketDataSnapshotIdentity.from_snapshot(document)
    different = document.model_copy(update={"schema_version": "market-v2"})
    connection = Connection(
        [
            {
                "canonical_snapshot_text": different.canonical_payload_bytes.decode(
                    "utf-8"
                ),
                "snapshot_digest": different.digest,
            }
        ]
    )

    with pytest.raises(
        MarketDataIntegrityError,
        match="stored market-data identity does not match lookup",
    ):
        PostgresMarketDataRepository(Pool(connection)).load_by_identity(requested)


@pytest.mark.parametrize(
    ("canonical_text", "digest"),
    (
        ('{"candles":[]}', "0" * 64),
        (snapshot().canonical_payload_bytes.decode("utf-8"), "0" * 64),
    ),
)
def test_load_fails_closed_for_tampered_canonical_storage(
    canonical_text: str, digest: str
) -> None:
    connection = Connection([{"canonical_snapshot_text": canonical_text, "snapshot_digest": digest}])

    with pytest.raises(MarketDataIntegrityError):
        PostgresMarketDataRepository(Pool(connection)).load_by_digest("a" * 64)


def test_sql_contract_uses_parameterized_atomic_write_and_identity_query() -> None:
    assert PostgresMarketDataSql.SAVE_SNAPSHOT.strip().startswith(
        "SELECT public.save_market_data_snapshot("
    )
    assert PostgresMarketDataSql.SAVE_SNAPSHOT.count(";") == 1
    assert "INSERT" not in PostgresMarketDataSql.SAVE_SNAPSHOT
    assert "%(canonical_snapshot_text)s" in PostgresMarketDataSql.SAVE_SNAPSHOT
    assert "WHERE snapshot_digest = %(snapshot_digest)s" in PostgresMarketDataSql.LOAD_BY_DIGEST
    assert "ORDER BY created_at, snapshot_digest" in PostgresMarketDataSql.LOAD_BY_IDENTITY


def test_domain_bounds_remain_the_repository_boundary() -> None:
    document = snapshot()
    with pytest.raises(Exception):
        snapshot(candles=())
    assert len(document.candles) == 2
    canonical = document.canonical_payload_bytes.decode("utf-8")
    assert '"timeframe":"1m"' in canonical
    assert '"volume":"12.5"' in canonical
    assert '"open_time":"2026-01-01T00:00:00Z"' in canonical


def test_repository_accepts_the_domain_maximum_candle_count_without_narrowing_it() -> None:
    base = snapshot()
    start = base.candles[0].open_time
    candles = tuple(
        base.candles[0].model_copy(update={"open_time": start + timedelta(minutes=index)})
        for index in range(4096)
    )
    observed = start + timedelta(minutes=4096)
    maximum = MarketSnapshot(
        instrument=base.instrument,
        timeframe=base.timeframe,
        candles=candles,
        provenance=base.provenance.model_copy(
            update={"observed_at": observed, "fetched_at": observed + timedelta(seconds=1)}
        ),
        known_at=observed + timedelta(seconds=2),
        schema_version=base.schema_version,
        normalization_version=base.normalization_version,
    )

    assert len(maximum.candles) == 4096
