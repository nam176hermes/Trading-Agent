"""Cross-engine query parity for immutable P2 snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from typing import Any

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1, DatasetSnapshotV2
from packages.engine_contracts.serialization import canonical_json_bytes


class QueryParityError(ValueError):
    """A snapshot is unreadable or engines disagree on canonical rows."""


@dataclass(frozen=True, slots=True)
class QueryParityResultV1:
    rows: tuple[dict[str, str], ...]
    row_count: int
    pyarrow_sha256: str
    polars_sha256: str
    duckdb_sha256: str


def _reference(digest: str, size: int) -> ArtifactRefV1:
    return ArtifactRefV1(
        content_sha256=digest,
        size_bytes=size,
        media_type="application/vnd.apache.parquet",
        locator=f"{digest}.blob",
    )


def _text(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise QueryParityError("query engine returned a naive timestamp")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None or isinstance(value, (str, bool, int)):
        return str(value)
    raise QueryParityError(f"unsupported query result value: {type(value).__name__}")


def _rows(table: pa.Table, order_by: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    if any(column not in table.column_names for column in order_by):
        raise QueryParityError("order_by references an unknown column")
    rows = tuple(
        {name: _text(value) for name, value in row.items()}
        for row in table.to_pylist()
    )
    return tuple(sorted(rows, key=lambda row: tuple(row[column] for column in order_by)))


def _digest(rows: tuple[dict[str, str], ...]) -> str:
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def query_snapshot_parity(
    snapshot: DatasetSnapshotV2,
    *,
    store: LocalArtifactStore,
    order_by: tuple[str, ...],
) -> QueryParityResultV1:
    canonical = DatasetSnapshotV2.model_validate(snapshot)
    paths = [
        str(
            store.verified_path(
                _reference(partition.parquet_sha256, partition.parquet_size_bytes)
            )
        )
        for partition in canonical.partitions
    ]
    if not paths:
        empty: tuple[dict[str, str], ...] = ()
        digest = _digest(empty)
        return QueryParityResultV1(empty, 0, digest, digest, digest)

    arrow_tables = [pq.read_table(path) for path in paths]
    arrow_table = pa.concat_tables(arrow_tables)
    polars_table = pl.read_parquet(paths).to_arrow()
    connection = duckdb.connect(database=":memory:")
    try:
        reader = connection.read_parquet(paths).arrow()
        duckdb_table = reader.read_all()
    finally:
        connection.close()

    arrow_rows = _rows(arrow_table, order_by)
    polars_rows = _rows(polars_table, order_by)
    duckdb_rows = _rows(duckdb_table, order_by)
    arrow_digest = _digest(arrow_rows)
    polars_digest = _digest(polars_rows)
    duckdb_digest = _digest(duckdb_rows)
    if len({arrow_digest, polars_digest, duckdb_digest}) != 1:
        raise QueryParityError("DuckDB, Polars, and PyArrow query results diverged")
    return QueryParityResultV1(
        rows=arrow_rows,
        row_count=len(arrow_rows),
        pyarrow_sha256=arrow_digest,
        polars_sha256=polars_digest,
        duckdb_sha256=duckdb_digest,
    )


__all__ = ["QueryParityError", "QueryParityResultV1", "query_snapshot_parity"]
