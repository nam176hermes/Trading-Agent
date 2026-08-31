from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pytest

from packages.data_catalog.artifact_store import ArtifactIntegrityError, LocalArtifactStore
from packages.data_catalog.v2 import build_snapshot, materialize_arrow_partition
from packages.data_contracts import ArrowFieldV1, ArrowSchemaV1, PITQueryMode, PITQueryV1
from packages.data_query import query_snapshot_parity


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def canonical_schema() -> ArrowSchemaV1:
    return ArrowSchemaV1(
        schema_id="canonical-bars",
        data_api_epoch=1,
        fields=(
            ArrowFieldV1(field_id=1, name="ts_event", data_type="timestamp[ns,UTC]", nullable=False),
            ArrowFieldV1(field_id=2, name="close", data_type="decimal128(18,8)", nullable=False),
        ),
    )


def table() -> pa.Table:
    return pa.table(
        {
            "ts_event": pa.array(
                (T0, T0 + timedelta(minutes=1)),
                type=pa.timestamp("ns", tz="UTC"),
            ),
            "close": pa.array(
                (Decimal("100.00000000"), Decimal("101.25000000")),
                type=pa.decimal128(18, 8),
            ),
        }
    )


def materialize(store: LocalArtifactStore):
    return materialize_arrow_partition(
        table(),
        schema=canonical_schema(),
        store=store,
        partition_id=UUID("40000000-0000-4000-8000-000000000001"),
        dataset="bars",
        partition_key=("BTC-USD", "2026-01-01"),
        partition_spec_version="day-v1",
        source_available_at=T0 + timedelta(minutes=2),
        system_observed_at=T0 + timedelta(minutes=3),
        ingested_at=T0 + timedelta(minutes=4),
        raw_evidence_sha256s=("a" * 64,),
        transform_receipt_sha256="b" * 64,
        quality_receipt_sha256="c" * 64,
    )


def test_local_artifact_store_is_content_addressed_idempotent_and_tamper_evident(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)

    first = store.put_bytes(b"canonical", media_type="application/octet-stream")
    second = store.put_bytes(b"canonical", media_type="application/octet-stream")

    assert first == second
    assert store.read_bytes(first) == b"canonical"
    (tmp_path / first.locator).write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError, match="invalid"):
        store.read_bytes(first)


def test_local_artifact_store_rejects_shared_or_symlinked_roots(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    with pytest.raises(ArtifactIntegrityError, match="private"):
        LocalArtifactStore(shared)

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(ArtifactIntegrityError, match="artifact root"):
        LocalArtifactStore(link)


def test_arrow_partition_and_snapshot_are_deterministic(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)

    first = materialize(store)
    second = materialize(store)
    query = PITQueryV1(
        mode=PITQueryMode.AS_INGESTED,
        valid_at=T0 + timedelta(minutes=1),
        cutoff=T0 + timedelta(minutes=5),
    )
    snapshot = build_snapshot(
        dataset="bars",
        query=query,
        schema=canonical_schema(),
        partitions=(first.manifest,),
    )

    assert first == second
    assert first.manifest.parquet_sha256 == first.artifact.content_sha256
    assert snapshot.partitions == (first.manifest,)
    assert snapshot.row_count == 2


def test_snapshot_builder_excludes_future_visibility_and_partial_event_partitions(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    value = materialize(store)

    too_early = build_snapshot(
        dataset="bars",
        query=PITQueryV1(
            mode=PITQueryMode.AS_INGESTED,
            valid_at=T0 + timedelta(seconds=30),
            cutoff=T0 + timedelta(minutes=5),
        ),
        schema=canonical_schema(),
        partitions=(value.manifest,),
    )
    not_ingested = build_snapshot(
        dataset="bars",
        query=PITQueryV1(
            mode=PITQueryMode.AS_INGESTED,
            valid_at=T0 + timedelta(minutes=1),
            cutoff=T0 + timedelta(minutes=3),
        ),
        schema=canonical_schema(),
        partitions=(value.manifest,),
    )

    assert too_early.partitions == ()
    assert not_ingested.partitions == ()


def test_duckdb_polars_and_pyarrow_return_identical_canonical_rows(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    value = materialize(store)
    snapshot = build_snapshot(
        dataset="bars",
        query=PITQueryV1(
            mode=PITQueryMode.AS_INGESTED,
            valid_at=T0 + timedelta(minutes=1),
            cutoff=T0 + timedelta(minutes=5),
        ),
        schema=canonical_schema(),
        partitions=(value.manifest,),
    )

    result = query_snapshot_parity(snapshot, store=store, order_by=("ts_event",))

    assert result.row_count == 2
    assert result.pyarrow_sha256 == result.polars_sha256 == result.duckdb_sha256
    assert result.rows[1]["close"] == "101.25000000"
