from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.data_catalog import MarketDatasetContinuityV1, MarketDatasetManifestV1
from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)


OPEN = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def snapshot() -> MarketSnapshot:
    instrument = InstrumentId("BTC-USD", ProductType.CRYPTO_SPOT, "ALPACA")
    return MarketSnapshot(
        instrument=instrument,
        timeframe=MarketTimeframe.ONE_MINUTE,
        candles=(
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=OPEN,
                open=Decimal("100"),
                high=Decimal("102"),
                low=Decimal("99"),
                close=Decimal("101"),
                volume=Decimal("12.5"),
            ),
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
                open=Decimal("101"),
                high=Decimal("103"),
                low=Decimal("100"),
                close=Decimal("102"),
                volume=Decimal("8.5"),
            ),
        ),
        provenance=MarketDataProvenance(
            provider="deterministic-fixture-v1",
            observed_at=datetime(2026, 8, 6, 12, 3, tzinfo=UTC),
            fetched_at=datetime(2026, 8, 6, 12, 3, tzinfo=UTC),
            raw_evidence_sha256="a" * 64,
            schema_version="market-data-v1",
            normalization_version="market-normalization-v1",
        ),
        known_at=datetime(2026, 8, 6, 12, 3, tzinfo=UTC),
        schema_version="market-snapshot-v1",
        normalization_version="market-normalization-v1",
    )


def test_manifest_binds_snapshot_identity_digests_and_gap_report() -> None:
    source = snapshot()

    manifest = MarketDatasetManifestV1.from_snapshot(
        source,
        raw_evidence_sha256="a" * 64,
        canonical_rows_sha256="b" * 64,
        parquet_sha256="c" * 64,
        importer_version="fixture-catalog-v1",
    )

    assert manifest.provider == "deterministic-fixture-v1"
    assert manifest.instrument == source.instrument
    assert manifest.timeframe is MarketTimeframe.ONE_MINUTE
    assert manifest.first_event_at == OPEN
    assert manifest.last_event_at == datetime(2026, 8, 6, 12, 2, tzinfo=UTC)
    assert manifest.row_count == 2
    assert manifest.content_digest == source.digest
    assert manifest.raw_evidence_sha256 == "a" * 64
    assert manifest.canonical_rows_sha256 == "b" * 64
    assert manifest.parquet_sha256 == "c" * 64
    assert manifest.gap_report == (datetime(2026, 8, 6, 12, 1, tzinfo=UTC),)
    assert manifest.duplicate_report == ()


def test_manifest_rejects_unsorted_or_unknown_integrity_metadata() -> None:
    source = snapshot()
    values = MarketDatasetManifestV1.from_snapshot(
        source,
        raw_evidence_sha256="a" * 64,
        canonical_rows_sha256="b" * 64,
        parquet_sha256="c" * 64,
        importer_version="fixture-catalog-v1",
    ).model_dump()
    values["instrument"] = source.instrument
    values["continuity"] = MarketDatasetContinuityV1.model_construct(
        timeframe=MarketTimeframe.ONE_MINUTE,
        gap_report=(
            datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
            datetime(2026, 8, 6, 12, 1, tzinfo=UTC),
        ),
        duplicate_report=(),
    )

    with pytest.raises(ValidationError, match="sorted and unique"):
        MarketDatasetManifestV1(**values)

    with pytest.raises(ValidationError):
        MarketDatasetManifestV1(**{**values, "unexpected": "forbidden"})


def test_dataset_continuity_is_a_strict_public_value_object() -> None:
    continuity = MarketDatasetContinuityV1(
        timeframe=MarketTimeframe.ONE_MINUTE,
        gap_report=(datetime(2026, 8, 6, 12, 1, tzinfo=UTC),),
        duplicate_report=(),
    )

    assert continuity.gap_report == (datetime(2026, 8, 6, 12, 1, tzinfo=UTC),)
    with pytest.raises(ValidationError, match="sorted and unique"):
        MarketDatasetContinuityV1(
            timeframe=MarketTimeframe.ONE_MINUTE,
            gap_report=(
                datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
                datetime(2026, 8, 6, 12, 1, tzinfo=UTC),
            ),
            duplicate_report=(),
        )
