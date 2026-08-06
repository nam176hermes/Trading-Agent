from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from packages.data_catalog import (
    CatalogMaterializationError,
    materialize_fixture_catalog,
    verify_materialized_catalog,
)
from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)


OPEN = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
RAW_EVIDENCE = b'{"fixture":"market-catalog-v1"}'


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
                open=Decimal("100.00"),
                high=Decimal("102.00"),
                low=Decimal("99.00"),
                close=Decimal("101.00"),
                volume=Decimal("12.50"),
            ),
            MarketCandle(
                instrument=instrument,
                timeframe=MarketTimeframe.ONE_MINUTE,
                open_time=datetime(2026, 8, 6, 12, 1, tzinfo=UTC),
                open=Decimal("101.00"),
                high=Decimal("103.00"),
                low=Decimal("100.00"),
                close=Decimal("102.00"),
                volume=Decimal("8.50"),
            ),
        ),
        provenance=MarketDataProvenance(
            provider="deterministic-fixture-v1",
            observed_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
            fetched_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
            raw_evidence_sha256=hashlib.sha256(RAW_EVIDENCE).hexdigest(),
            schema_version="market-data-v1",
            normalization_version="market-normalization-v1",
        ),
        known_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
        schema_version="market-snapshot-v1",
        normalization_version="market-normalization-v1",
    )


def test_materialized_fixture_catalog_round_trips_and_is_hash_bound(tmp_path: Path) -> None:
    source = snapshot()

    artifact = materialize_fixture_catalog(
        source,
        RAW_EVIDENCE,
        destination=tmp_path,
        importer_version="fixture-catalog-v1",
    )

    assert artifact.parquet_path.is_file()
    assert artifact.manifest_path.is_file()
    assert verify_materialized_catalog(artifact) == source
    assert artifact.manifest.content_digest == source.digest


def test_catalog_verifier_rejects_tampered_parquet(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(),
        RAW_EVIDENCE,
        destination=tmp_path,
        importer_version="fixture-catalog-v1",
    )
    artifact.parquet_path.write_bytes(artifact.parquet_path.read_bytes() + b"tamper")

    with pytest.raises(CatalogMaterializationError, match="parquet digest"):
        verify_materialized_catalog(artifact)


def test_catalog_round_trip_preserves_source_schema_versions(tmp_path: Path) -> None:
    source = snapshot().model_copy(
        update={
            "provenance": snapshot().provenance.model_copy(
                update={"schema_version": "fixture-source-v7"}
            ),
            "schema_version": "fixture-snapshot-v5",
        }
    )

    artifact = materialize_fixture_catalog(
        source,
        RAW_EVIDENCE,
        destination=tmp_path,
        importer_version="fixture-catalog-v1",
    )

    assert verify_materialized_catalog(artifact) == source


def test_catalog_uses_a_utc_timestamp_parquet_column(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(), RAW_EVIDENCE, destination=tmp_path, importer_version="fixture-catalog-v1"
    )

    assert str(pq.read_schema(artifact.parquet_path).field("open_time").type) == "timestamp[us, tz=UTC]"


def test_catalog_accepts_zero_offset_zoneinfo_utc_snapshot(tmp_path: Path) -> None:
    source = snapshot().model_copy(
        update={
            "candles": tuple(
                candle.model_copy(update={"open_time": candle.open_time.replace(tzinfo=ZoneInfo("Etc/UTC"))})
                for candle in snapshot().candles
            ),
            "provenance": snapshot().provenance.model_copy(
                update={
                    "observed_at": snapshot().provenance.observed_at.replace(tzinfo=ZoneInfo("Etc/UTC")),
                    "fetched_at": snapshot().provenance.fetched_at.replace(tzinfo=ZoneInfo("Etc/UTC")),
                }
            ),
            "known_at": snapshot().known_at.replace(tzinfo=ZoneInfo("Etc/UTC")),
        }
    )

    artifact = materialize_fixture_catalog(
        source, RAW_EVIDENCE, destination=tmp_path, importer_version="fixture-catalog-v1"
    )

    assert verify_materialized_catalog(artifact) == source


def test_catalog_rejects_destination_with_an_intermediate_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    via = tmp_path / "via"
    via.symlink_to(outside, target_is_directory=True)
    requested = via / "nested"
    (outside / "nested").mkdir()

    with pytest.raises(CatalogMaterializationError, match="symlink"):
        materialize_fixture_catalog(
            snapshot(), RAW_EVIDENCE, destination=requested, importer_version="fixture-catalog-v1"
        )

    assert list(outside.joinpath("nested").iterdir()) == []


def test_catalog_verifier_rejects_oversized_artifact_before_parquet_loading(tmp_path: Path) -> None:
    artifact = materialize_fixture_catalog(
        snapshot(), RAW_EVIDENCE, destination=tmp_path, importer_version="fixture-catalog-v1"
    )
    artifact.parquet_path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))

    with pytest.raises(CatalogMaterializationError, match="bounded regular-file policy"):
        verify_materialized_catalog(artifact)


def test_data_catalog_source_has_no_runtime_or_provider_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "packages" / "data_catalog"
    forbidden = {
        "httpx",
        "nautilus_trader",
        "psycopg",
        "requests",
        "services.market_data",
        "socket",
        "sqlalchemy",
        "subprocess",
        "urllib",
    }
    imports: set[str] = set()

    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    assert not any(
        name == blocked or name.startswith(f"{blocked}.")
        for name in imports
        for blocked in forbidden
    )
