"""Provider-free local Parquet materialization for validated market snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

import pyarrow as pa
import pyarrow.parquet as pq

from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)

from .manifests import CatalogManifestError, MarketDatasetManifestV1


class CatalogMaterializationError(ValueError):
    """The local catalog artifact is unsafe, malformed, or has drifted."""


@dataclass(frozen=True, slots=True)
class MaterializedMarketDatasetV1:
    manifest: MarketDatasetManifestV1
    parquet_path: Path
    manifest_path: Path


_ROW_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _utc_text(value: datetime) -> str:
    if value.tzinfo is not UTC:
        raise CatalogMaterializationError("catalog timestamps must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _canonical_rows(snapshot: MarketSnapshot) -> tuple[list[dict[str, str]], bytes]:
    rows = [
        {
            "close": str(candle.close),
            "high": str(candle.high),
            "low": str(candle.low),
            "open": str(candle.open),
            "open_time": _utc_text(candle.open_time),
            "volume": str(candle.volume),
        }
        for candle in snapshot.candles
    ]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rows, encoded


def _validated_destination(destination: Path) -> Path:
    if not isinstance(destination, Path) or destination.is_symlink() or not destination.is_dir():
        raise CatalogMaterializationError("destination must be an existing non-symlink directory")
    resolved = destination.resolve(strict=True)
    if resolved.is_symlink():
        raise CatalogMaterializationError("destination must not resolve through a symlink")
    return resolved


def _new_artifact_paths(destination: Path, content_digest: str) -> tuple[Path, Path]:
    parquet_path = destination / f"market-{content_digest}.parquet"
    manifest_path = destination / f"market-{content_digest}.manifest.json"
    if parquet_path.exists() or manifest_path.exists():
        raise CatalogMaterializationError("catalog artifact already exists")
    return parquet_path, manifest_path


def _write_new_parquet(path: Path, rows: list[dict[str, str]]) -> None:
    table = pa.Table.from_pylist(rows, schema=pa.schema([pa.field(column, pa.string(), nullable=False) for column in _ROW_COLUMNS]))
    with NamedTemporaryFile(dir=path.parent, prefix=".catalog-", suffix=".parquet", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        pq.write_table(table, temporary_path, compression="NONE", use_dictionary=False, write_statistics=False)
        os.link(temporary_path, path)
    except FileExistsError as exc:
        raise CatalogMaterializationError("catalog artifact already exists") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_new_manifest(path: Path, manifest: MarketDatasetManifestV1) -> None:
    encoded = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        with open(path, "xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise CatalogMaterializationError("catalog artifact already exists") from exc


def materialize_fixture_catalog(
    snapshot: MarketSnapshot,
    raw_evidence: bytes,
    *,
    destination: Path,
    importer_version: str,
) -> MaterializedMarketDatasetV1:
    """Write a new hash-bound local catalog artifact from one validated fixture."""

    if not isinstance(snapshot, MarketSnapshot):
        raise CatalogMaterializationError("snapshot must be a validated MarketSnapshot")
    if not isinstance(raw_evidence, bytes) or not raw_evidence:
        raise CatalogMaterializationError("raw evidence must be non-empty bytes")
    raw_evidence_sha256 = _sha256_bytes(raw_evidence)
    if raw_evidence_sha256 != snapshot.provenance.raw_evidence_sha256:
        raise CatalogMaterializationError("raw evidence digest does not match snapshot provenance")
    root = _validated_destination(destination)
    rows, canonical_rows = _canonical_rows(snapshot)
    parquet_path, manifest_path = _new_artifact_paths(root, snapshot.digest)
    _write_new_parquet(parquet_path, rows)
    try:
        manifest = MarketDatasetManifestV1.from_snapshot(
            snapshot,
            raw_evidence_sha256=raw_evidence_sha256,
            canonical_rows_sha256=_sha256_bytes(canonical_rows),
            parquet_sha256=_sha256_file(parquet_path),
            importer_version=importer_version,
        )
        _write_new_manifest(manifest_path, manifest)
        artifact = MaterializedMarketDatasetV1(manifest, parquet_path, manifest_path)
        if verify_materialized_catalog(artifact) != snapshot:
            raise CatalogMaterializationError("materialized catalog does not round-trip")
        return artifact
    except Exception:
        parquet_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise


def _read_manifest(path: Path) -> MarketDatasetManifestV1:
    if path.is_symlink() or not path.is_file():
        raise CatalogMaterializationError("manifest path must be a regular file")
    try:
        return MarketDatasetManifestV1.model_validate_json(path.read_bytes())
    except Exception as exc:
        raise CatalogMaterializationError("manifest is invalid") from exc


def _read_rows(path: Path) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise CatalogMaterializationError("parquet path must be a regular file")
    try:
        table = pq.read_table(path)
    except Exception as exc:
        raise CatalogMaterializationError("parquet cannot be read") from exc
    if tuple(table.column_names) != _ROW_COLUMNS or any(field.type != pa.string() or field.nullable for field in table.schema):
        raise CatalogMaterializationError("parquet schema is not canonical")
    rows = table.to_pylist()
    if not all(isinstance(row, dict) and set(row) == set(_ROW_COLUMNS) and all(isinstance(value, str) for value in row.values()) for row in rows):
        raise CatalogMaterializationError("parquet rows are not canonical strings")
    return rows


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise CatalogMaterializationError("catalog timestamp must use Z UTC spelling")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise CatalogMaterializationError("catalog timestamp is invalid") from exc
    if parsed.tzinfo is not UTC:
        raise CatalogMaterializationError("catalog timestamp must be UTC")
    return parsed


def _snapshot_from_rows(rows: list[dict[str, str]], manifest: MarketDatasetManifestV1) -> MarketSnapshot:
    try:
        instrument = InstrumentId(manifest.instrument.symbol, manifest.instrument.product_type, manifest.instrument.venue)
        candles = tuple(
            MarketCandle(
                instrument=instrument,
                timeframe=manifest.timeframe,
                open_time=_parse_utc(row["open_time"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            for row in rows
        )
        return MarketSnapshot(
            instrument=instrument,
            timeframe=manifest.timeframe,
            candles=candles,
            provenance=MarketDataProvenance(
                provider=manifest.provider,
                observed_at=manifest.observed_at,
                fetched_at=manifest.fetched_at,
                raw_evidence_sha256=manifest.raw_evidence_sha256,
                schema_version=manifest.provenance_schema_version,
                normalization_version=manifest.normalization_version,
            ),
            known_at=manifest.known_at,
            schema_version=manifest.snapshot_schema_version,
            normalization_version=manifest.normalization_version,
        )
    except Exception as exc:
        raise CatalogMaterializationError("parquet rows cannot reconstruct a MarketSnapshot") from exc


def verify_materialized_catalog(artifact: MaterializedMarketDatasetV1) -> MarketSnapshot:
    """Re-read every artifact byte and reject any manifest, schema, or row drift."""

    if not isinstance(artifact, MaterializedMarketDatasetV1):
        raise CatalogMaterializationError("artifact has invalid type")
    manifest = _read_manifest(artifact.manifest_path)
    if manifest != artifact.manifest:
        raise CatalogMaterializationError("manifest file does not match artifact identity")
    if _sha256_file(artifact.parquet_path) != manifest.parquet_sha256:
        raise CatalogMaterializationError("parquet digest does not match manifest")
    rows = _read_rows(artifact.parquet_path)
    canonical_rows = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if _sha256_bytes(canonical_rows) != manifest.canonical_rows_sha256:
        raise CatalogMaterializationError("canonical rows digest does not match manifest")
    snapshot = _snapshot_from_rows(rows, manifest)
    try:
        expected = MarketDatasetManifestV1.from_snapshot(
            snapshot,
            raw_evidence_sha256=manifest.raw_evidence_sha256,
            canonical_rows_sha256=manifest.canonical_rows_sha256,
            parquet_sha256=manifest.parquet_sha256,
            importer_version=manifest.importer_version,
        )
    except CatalogManifestError as exc:
        raise CatalogMaterializationError("reconstructed snapshot cannot bind a manifest") from exc
    if expected != manifest:
        raise CatalogMaterializationError("manifest does not bind reconstructed snapshot")
    return snapshot


__all__ = [
    "CatalogMaterializationError",
    "MaterializedMarketDatasetV1",
    "materialize_fixture_catalog",
    "verify_materialized_catalog",
]
