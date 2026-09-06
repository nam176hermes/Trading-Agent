"""Seal revision-aware P2 daily partitions into P3 research evidence."""

from __future__ import annotations

import hashlib
import io
from datetime import date, timedelta
from decimal import Decimal

import pyarrow.parquet as pq

from packages.alpha_lifecycle.baselines import DailyCloseV1
from packages.alpha_lifecycle.contracts.data import DailyBar, DatasetEvidence
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_catalog.v3 import build_snapshot_v3
from packages.data_contracts import (
    ArrowSchemaV1,
    ArtifactRefV1,
    DatasetPartitionManifestV3,
    PITQueryV1,
)
from packages.engine_contracts.serialization import canonical_json_bytes


class DatasetSealError(ValueError):
    """P2 partitions cannot form one complete P3 daily dataset."""


_RANGES = {
    "RESEARCH": (date(2018, 1, 1), date(2025, 8, 31)),
    "HOLDOUT": (date(2025, 9, 1), date(2026, 8, 31)),
    "BUFFER": (date(2026, 9, 1), date(2026, 9, 1)),
}


def validate_dates(segment: str, days: tuple[date, ...]) -> tuple[date, ...]:
    if len(days) < 750 and segment in {"GENERIC_RESEARCH", "RESEARCH"}:
        raise DatasetSealError("research dataset requires at least 750 usable closes")
    if days != tuple(sorted(set(days))):
        raise DatasetSealError("dataset dates must be unique and ordered")
    if any(right != left + timedelta(days=1) for left, right in zip(days, days[1:])):
        raise DatasetSealError("dataset date coverage contains a gap")
    if segment in _RANGES and (not days or (days[0], days[-1]) != _RANGES[segment]):
        raise DatasetSealError("dataset does not match exact campaign coverage")
    return days


def to_daily_close(bar: DailyBar) -> DailyCloseV1:
    return DailyCloseV1(
        instrument=bar.instrument,
        closed_at=bar.closed_at_exclusive - timedelta(microseconds=1),
        close=Decimal(bar.close),
    )


def _partition_ref(partition: DatasetPartitionManifestV3) -> ArtifactRefV1:
    return ArtifactRefV1(
        content_sha256=partition.parquet_sha256,
        size_bytes=partition.parquet_size_bytes,
        media_type="application/vnd.apache.parquet",
        locator=f"{partition.parquet_sha256}.blob",
    )


def _daily_bar(
    partition: DatasetPartitionManifestV3, store: LocalArtifactStore
) -> DailyBar:
    partition_ref = _partition_ref(partition)
    table = pq.read_table(io.BytesIO(store.read_bytes(partition_ref)))
    if table.num_rows != 1:
        raise DatasetSealError("each P3 daily partition must contain exactly one row")
    row = table.to_pylist()[0]
    required = {
        "date", "instrument", "opened_at", "closed_at_exclusive", "raw_close_time",
        "raw_timestamp_unit", "open", "high", "low", "close", "base_volume",
        "quote_volume", "trade_count",
    }
    if not required <= row.keys():
        raise DatasetSealError("daily partition is missing required P3 fields")
    payload = {
        "schema_version": "p3-daily-bar-v1",
        **{name: row[name] for name in required},
        "provider_published_at": row.get("provider_published_at"),
        "system_observed_at": partition.system_observed_at,
        "ingested_at": partition.ingested_at,
        "partition_ref": partition_ref,
        "row_ordinal": 0,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return DailyBar.model_validate(payload)


def seal_research_dataset(
    partitions: tuple[DatasetPartitionManifestV3, ...],
    query: PITQueryV1,
    schema: ArrowSchemaV1,
    store: LocalArtifactStore,
) -> DatasetEvidence:
    if not partitions or len({item.dataset for item in partitions}) != 1:
        raise DatasetSealError("one non-empty dataset namespace is required")
    namespace = partitions[0].dataset
    segment = {
        "p3.research.daily": "RESEARCH",
        "p3.holdout.daily": "HOLDOUT",
        "p3.buffer.daily": "BUFFER",
    }.get(namespace)
    if segment is None:
        raise DatasetSealError("dataset namespace is outside the P3 campaign")
    snapshot = build_snapshot_v3(
        dataset=namespace, query=query, schema=schema, partitions=partitions
    )
    bars = tuple(_daily_bar(item, store) for item in snapshot.partitions)
    validate_dates(segment, tuple(item.date for item in bars))
    row_refs = tuple(
        store.put_bytes(canonical_json_bytes(item), media_type="application/json")
        for item in bars
    )
    snapshot_ref = store.put_bytes(
        canonical_json_bytes(snapshot), media_type="application/json"
    )
    payload = {
        "schema_version": "p3-dataset-evidence-v1",
        "snapshot_ref": snapshot_ref,
        "query_digest": query.canonical_digest,
        "segment": segment,
        "date_range": {"start": bars[0].date, "end": bars[-1].date},
        "usable_rows": len(bars),
        "row_refs": row_refs,
        "ordered_rows_digest": hashlib.sha256(
            canonical_json_bytes([item.content_sha256 for item in row_refs])
        ).hexdigest(),
        "vintage_class": "RETROSPECTIVE_CURRENT_ARCHIVE",
        "observed_cutoff": query.cutoff,
        "limitations": ("retrospective.current_archive",),
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    evidence = DatasetEvidence.model_validate(payload)
    store.read_bytes(snapshot_ref)
    for ref in row_refs:
        store.read_bytes(ref)
    return evidence


__all__ = ["DatasetSealError", "seal_research_dataset", "to_daily_close", "validate_dates"]
