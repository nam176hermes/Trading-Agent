"""Deterministic Arrow/Parquet partition materialization and snapshot selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import io
import re
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq

from packages.data_contracts import (
    ArrowFieldV1,
    ArrowSchemaV1,
    ArtifactRefV1,
    DatasetPartitionManifestV2,
    DatasetSnapshotV2,
    PITQueryV1,
)
from packages.domain import require_utc

from .artifact_store import LocalArtifactStore


class DatasetMaterializationError(ValueError):
    """A table cannot be represented by the declared canonical schema."""


@dataclass(frozen=True, slots=True)
class MaterializedPartitionV2:
    manifest: DatasetPartitionManifestV2
    artifact: ArtifactRefV1


_DECIMAL = re.compile(r"^decimal128\((\d+),(\d+)\)$", re.ASCII)


def _arrow_type(field: ArrowFieldV1) -> pa.DataType:
    if field.data_type == "timestamp[ns,UTC]":
        return pa.timestamp("ns", tz="UTC")
    if field.data_type == "string":
        return pa.string()
    if field.data_type == "int64":
        return pa.int64()
    if field.data_type == "bool":
        return pa.bool_()
    match = _DECIMAL.fullmatch(field.data_type)
    if match is not None:
        return pa.decimal128(int(match.group(1)), int(match.group(2)))
    raise DatasetMaterializationError(f"unsupported canonical Arrow type: {field.data_type}")


def _expected_schema(schema: ArrowSchemaV1) -> pa.Schema:
    return pa.schema(
        [
            pa.field(field.name, _arrow_type(field), nullable=field.nullable)
            for field in schema.fields
        ]
    )


def _canonical_table(table: pa.Table, expected: pa.Schema) -> pa.Table:
    if table.column_names != expected.names or any(
        actual.type != declared.type
        for actual, declared in zip(table.schema, expected, strict=True)
    ):
        raise DatasetMaterializationError("Arrow table does not match the canonical schema")
    for column, declared in zip(table.columns, expected, strict=True):
        if not declared.nullable and column.null_count:
            raise DatasetMaterializationError("non-nullable Arrow field contains nulls")
    return pa.Table.from_arrays(table.columns, schema=expected)


def _canonical_ipc_bytes(table: pa.Table) -> bytes:
    output = io.BytesIO()
    with pa.ipc.new_stream(output, table.schema) as writer:
        writer.write_table(table)
    return output.getvalue()


def _parquet_bytes(table: pa.Table) -> bytes:
    output = io.BytesIO()
    pq.write_table(
        table,
        output,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        version="2.6",
        data_page_version="2.0",
    )
    return output.getvalue()


def _event_bounds(table: pa.Table) -> tuple[datetime, datetime]:
    if "ts_event" not in table.column_names or table.num_rows == 0:
        raise DatasetMaterializationError("partition requires non-empty ts_event values")
    values = table.column("ts_event").to_pylist()
    if any(not isinstance(value, datetime) for value in values):
        raise DatasetMaterializationError("ts_event must contain UTC timestamps")
    try:
        canonical = tuple(require_utc(value).astimezone(UTC) for value in values)
    except ValueError as exc:
        raise DatasetMaterializationError("ts_event must contain UTC timestamps") from exc
    if canonical != tuple(sorted(canonical)) or len(canonical) != len(set(canonical)):
        raise DatasetMaterializationError("ts_event values must be strictly increasing")
    return canonical[0], canonical[-1]


def materialize_arrow_partition(
    table: pa.Table,
    *,
    schema: ArrowSchemaV1,
    store: LocalArtifactStore,
    partition_id: UUID,
    dataset: str,
    partition_key: tuple[str, ...],
    partition_spec_version: str,
    source_available_at: datetime,
    system_observed_at: datetime,
    ingested_at: datetime,
    raw_evidence_sha256s: tuple[str, ...],
    transform_receipt_sha256: str,
    quality_receipt_sha256: str,
) -> MaterializedPartitionV2:
    if not isinstance(table, pa.Table):
        raise DatasetMaterializationError("partition input must be a PyArrow table")
    canonical_schema = ArrowSchemaV1.model_validate(schema)
    expected = _expected_schema(canonical_schema)
    table = _canonical_table(table, expected)
    if table.num_rows > 10_000_000:
        raise DatasetMaterializationError("partition row count exceeds the local bound")
    first_event_at, last_event_at = _event_bounds(table)
    parquet = _parquet_bytes(table)
    artifact = store.put_bytes(parquet, media_type="application/vnd.apache.parquet")
    manifest = DatasetPartitionManifestV2(
        partition_id=partition_id,
        dataset=dataset,
        partition_key=partition_key,
        schema_id=canonical_schema.schema_id,
        schema_fingerprint=canonical_schema.fingerprint,
        data_api_epoch=canonical_schema.data_api_epoch,
        partition_spec_version=partition_spec_version,
        first_event_at=first_event_at,
        last_event_at=last_event_at,
        source_available_at=require_utc(source_available_at),
        system_observed_at=require_utc(system_observed_at),
        ingested_at=require_utc(ingested_at),
        row_count=table.num_rows,
        parquet_sha256=artifact.content_sha256,
        parquet_size_bytes=artifact.size_bytes,
        canonical_rows_sha256=hashlib.sha256(_canonical_ipc_bytes(table)).hexdigest(),
        raw_evidence_sha256s=raw_evidence_sha256s,
        transform_receipt_sha256=transform_receipt_sha256,
        quality_receipt_sha256=quality_receipt_sha256,
    )
    return MaterializedPartitionV2(manifest=manifest, artifact=artifact)


def build_snapshot(
    *,
    dataset: str,
    query: PITQueryV1,
    schema: ArrowSchemaV1,
    partitions: tuple[DatasetPartitionManifestV2, ...],
) -> DatasetSnapshotV2:
    canonical_query = PITQueryV1.model_validate(query)
    canonical_schema = ArrowSchemaV1.model_validate(schema)
    visibility_field = canonical_query.visibility_field
    selected = tuple(
        partition
        for partition in partitions
        if partition.last_event_at <= canonical_query.valid_at
        and getattr(partition, visibility_field) <= canonical_query.cutoff
    )
    return DatasetSnapshotV2(
        dataset=dataset,
        query=canonical_query,
        schema_id=canonical_schema.schema_id,
        schema_fingerprint=canonical_schema.fingerprint,
        data_api_epoch=canonical_schema.data_api_epoch,
        partitions=selected,
    )


__all__ = [
    "DatasetMaterializationError",
    "MaterializedPartitionV2",
    "build_snapshot",
    "materialize_arrow_partition",
]
