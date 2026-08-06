"""Descriptor-bound, provider-free local Parquet market catalog."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    require_utc,
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
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_PARQUET_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 4096
_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: datetime) -> datetime:
    try:
        return require_utc(value).astimezone(UTC)
    except ValueError as exc:
        raise CatalogMaterializationError("catalog timestamp must be UTC") from exc


def _utc_text(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _canonical_rows(snapshot: MarketSnapshot) -> tuple[list[dict[str, str]], bytes]:
    rows = [
        {
            "close": str(candle.close), "high": str(candle.high), "low": str(candle.low),
            "open": str(candle.open), "open_time": _utc_text(candle.open_time),
            "volume": str(candle.volume),
        }
        for candle in snapshot.candles
    ]
    return rows, json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _component_names(destination: Path) -> tuple[str, ...]:
    if not isinstance(destination, Path) or not destination.is_absolute():
        raise CatalogMaterializationError("destination must be an absolute directory path")
    names = destination.parts[1:]
    if not names or any(name in {"", ".", ".."} for name in names):
        raise CatalogMaterializationError("destination path contains an unsafe component")
    return names


@contextmanager
def _opened_catalog_directory(destination: Path) -> Iterator[tuple[int, Path]]:
    """Open every destination component without following a symlink."""

    fd = os.open("/", _OPEN_FLAGS)
    try:
        for name in _component_names(destination):
            next_fd = os.open(name, _OPEN_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise CatalogMaterializationError("destination must be a caller-owned directory")
        yield fd, destination
    except OSError as exc:
        raise CatalogMaterializationError("destination contains a non-directory or symlink") from exc
    finally:
        os.close(fd)


def _regular_fd_at(directory_fd: int, name: str, *, maximum_bytes: int) -> int:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
    except OSError as exc:
        raise CatalogMaterializationError("catalog artifact must be a regular non-symlink file") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum_bytes:
        os.close(fd)
        raise CatalogMaterializationError("catalog artifact exceeds its bounded regular-file policy")
    return fd


def _sha256_at(directory_fd: int, name: str, *, maximum_bytes: int) -> str:
    fd = _regular_fd_at(directory_fd, name, maximum_bytes=maximum_bytes)
    try:
        digest = hashlib.sha256()
        while block := os.read(fd, 64 * 1024):
            digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _artifact_names(content_digest: str) -> tuple[str, str]:
    return (f"market-{content_digest}.parquet", f"market-{content_digest}.manifest.json")


def _write_new_parquet(directory_fd: int, name: str, rows: list[dict[str, str]]) -> None:
    temporary = f".catalog-{uuid.uuid4().hex}.parquet"
    temporary_fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd
    )
    os.close(temporary_fd)
    try:
        schema = pa.schema([
            pa.field("open_time", pa.timestamp("us", tz="UTC"), nullable=False),
            *(pa.field(column, pa.string(), nullable=False) for column in _ROW_COLUMNS[1:]),
        ])
        parquet_rows = [{**row, "open_time": _parse_utc(row["open_time"])} for row in rows]
        pq.write_table(
            pa.Table.from_pylist(parquet_rows, schema=schema),
            f"/proc/self/fd/{directory_fd}/{temporary}",
            compression="NONE", use_dictionary=False, write_statistics=False,
        )
        source = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(source.st_mode) or source.st_nlink != 1:
            raise CatalogMaterializationError("temporary parquet artifact changed unexpectedly")
        os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
    except FileExistsError as exc:
        raise CatalogMaterializationError("catalog artifact already exists") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _write_new_manifest(directory_fd: int, name: str, manifest: MarketDatasetManifestV1) -> None:
    encoded = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise CatalogMaterializationError("manifest exceeds its bounded size")
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    except FileExistsError as exc:
        raise CatalogMaterializationError("catalog artifact already exists") from exc
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def materialize_fixture_catalog(snapshot: MarketSnapshot, raw_evidence: bytes, *, destination: Path, importer_version: str) -> MaterializedMarketDatasetV1:
    """Create one new private catalog artifact and verify it before returning."""

    if not isinstance(snapshot, MarketSnapshot):
        raise CatalogMaterializationError("snapshot must be a validated MarketSnapshot")
    if not isinstance(raw_evidence, bytes) or not raw_evidence:
        raise CatalogMaterializationError("raw evidence must be non-empty bytes")
    raw_evidence_sha256 = _sha256_bytes(raw_evidence)
    if raw_evidence_sha256 != snapshot.provenance.raw_evidence_sha256:
        raise CatalogMaterializationError("raw evidence digest does not match snapshot provenance")
    rows, canonical_rows = _canonical_rows(snapshot)
    parquet_name, manifest_name = _artifact_names(snapshot.digest)
    with _opened_catalog_directory(destination) as (directory_fd, root):
        _write_new_parquet(directory_fd, parquet_name, rows)
        try:
            manifest = MarketDatasetManifestV1.from_snapshot(
                snapshot, raw_evidence_sha256=raw_evidence_sha256,
                canonical_rows_sha256=_sha256_bytes(canonical_rows),
                parquet_sha256=_sha256_at(directory_fd, parquet_name, maximum_bytes=_MAX_PARQUET_BYTES),
                importer_version=importer_version,
            )
            _write_new_manifest(directory_fd, manifest_name, manifest)
            artifact = MaterializedMarketDatasetV1(manifest, root / parquet_name, root / manifest_name)
            if verify_materialized_catalog(artifact) != snapshot:
                raise CatalogMaterializationError("materialized catalog does not round-trip")
            return artifact
        except Exception:
            for name in (parquet_name, manifest_name):
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            raise


def _read_manifest(directory_fd: int, name: str) -> MarketDatasetManifestV1:
    fd = _regular_fd_at(directory_fd, name, maximum_bytes=_MAX_MANIFEST_BYTES)
    try:
        encoded = b"".join(iter(lambda: os.read(fd, 64 * 1024), b""))
    finally:
        os.close(fd)
    try:
        return MarketDatasetManifestV1.model_validate_json(encoded)
    except Exception as exc:
        raise CatalogMaterializationError("manifest is invalid") from exc


def _read_rows(directory_fd: int, name: str) -> list[dict[str, object]]:
    fd = _regular_fd_at(directory_fd, name, maximum_bytes=_MAX_PARQUET_BYTES)
    os.close(fd)
    try:
        reader = pq.ParquetFile(f"/proc/self/fd/{directory_fd}/{name}")
        if reader.metadata.num_rows > _MAX_ROWS or reader.metadata.num_row_groups > _MAX_ROWS:
            raise CatalogMaterializationError("parquet exceeds the catalog row bound")
        table = reader.read()
    except CatalogMaterializationError:
        raise
    except Exception as exc:
        raise CatalogMaterializationError("parquet cannot be read") from exc
    expected_schema = pa.schema([
        pa.field("open_time", pa.timestamp("us", tz="UTC"), nullable=False),
        *(pa.field(column, pa.string(), nullable=False) for column in _ROW_COLUMNS[1:]),
    ])
    if table.schema != expected_schema:
        raise CatalogMaterializationError("parquet schema is not canonical")
    rows = table.to_pylist()
    if len(rows) > _MAX_ROWS or not all(isinstance(row, dict) and set(row) == set(_ROW_COLUMNS) for row in rows):
        raise CatalogMaterializationError("parquet rows are not canonical")
    return rows


def _parse_utc(value: object) -> datetime:
    if isinstance(value, str):
        if not value.endswith("Z"):
            raise CatalogMaterializationError("catalog timestamp must use Z UTC spelling")
        try:
            value = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise CatalogMaterializationError("catalog timestamp is invalid") from exc
    if not isinstance(value, datetime):
        raise CatalogMaterializationError("catalog timestamp is invalid")
    return _utc(value)


def _canonical_rows_from_parquet(rows: list[dict[str, object]]) -> bytes:
    try:
        canonical = [
            {"open_time": _utc_text(_parse_utc(row["open_time"])), **{column: row[column] for column in _ROW_COLUMNS[1:]}}
            for row in rows
        ]
        if not all(all(isinstance(value, str) for key, value in row.items() if key != "open_time") for row in canonical):
            raise ValueError
    except Exception as exc:
        raise CatalogMaterializationError("parquet rows are not canonical strings") from exc
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _snapshot_from_rows(rows: list[dict[str, object]], manifest: MarketDatasetManifestV1) -> MarketSnapshot:
    try:
        instrument = InstrumentId(manifest.instrument.symbol, manifest.instrument.product_type, manifest.instrument.venue)
        candles = tuple(MarketCandle(instrument=instrument, timeframe=manifest.timeframe, open_time=_parse_utc(row["open_time"]), open=Decimal(str(row["open"])), high=Decimal(str(row["high"])), low=Decimal(str(row["low"])), close=Decimal(str(row["close"])), volume=Decimal(str(row["volume"]))) for row in rows)
        return MarketSnapshot(
            instrument=instrument, timeframe=manifest.timeframe, candles=candles,
            provenance=MarketDataProvenance(provider=manifest.provider, observed_at=manifest.observed_at, fetched_at=manifest.fetched_at, raw_evidence_sha256=manifest.raw_evidence_sha256, schema_version=manifest.provenance_schema_version, normalization_version=manifest.normalization_version),
            known_at=manifest.known_at, schema_version=manifest.snapshot_schema_version, normalization_version=manifest.normalization_version,
        )
    except Exception as exc:
        raise CatalogMaterializationError("parquet rows cannot reconstruct a MarketSnapshot") from exc


def verify_materialized_catalog(artifact: MaterializedMarketDatasetV1) -> MarketSnapshot:
    """Re-open exact direct children through a held directory descriptor and verify all bindings."""

    if not isinstance(artifact, MaterializedMarketDatasetV1) or artifact.parquet_path.parent != artifact.manifest_path.parent:
        raise CatalogMaterializationError("artifact has invalid paths")
    with _opened_catalog_directory(artifact.parquet_path.parent) as (directory_fd, _):
        manifest_name = artifact.manifest_path.name
        manifest = _read_manifest(directory_fd, manifest_name)
        parquet_name, expected_manifest_name = _artifact_names(manifest.content_digest)
        if manifest_name != expected_manifest_name or artifact.parquet_path.name != parquet_name or manifest != artifact.manifest:
            raise CatalogMaterializationError("artifact paths do not match manifest identity")
        if _sha256_at(directory_fd, parquet_name, maximum_bytes=_MAX_PARQUET_BYTES) != manifest.parquet_sha256:
            raise CatalogMaterializationError("parquet digest does not match manifest")
        rows = _read_rows(directory_fd, parquet_name)
        if _sha256_bytes(_canonical_rows_from_parquet(rows)) != manifest.canonical_rows_sha256:
            raise CatalogMaterializationError("canonical rows digest does not match manifest")
        snapshot = _snapshot_from_rows(rows, manifest)
        try:
            expected = MarketDatasetManifestV1.from_snapshot(snapshot, raw_evidence_sha256=manifest.raw_evidence_sha256, canonical_rows_sha256=manifest.canonical_rows_sha256, parquet_sha256=manifest.parquet_sha256, importer_version=manifest.importer_version)
        except CatalogManifestError as exc:
            raise CatalogMaterializationError("reconstructed snapshot cannot bind a manifest") from exc
        if expected != manifest:
            raise CatalogMaterializationError("manifest does not bind reconstructed snapshot")
        return snapshot


__all__ = ["CatalogMaterializationError", "MaterializedMarketDatasetV1", "materialize_fixture_catalog", "verify_materialized_catalog"]
