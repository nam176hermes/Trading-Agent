from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.data_catalog.v3 import DatasetRevisionError, build_snapshot_v3
from packages.data_contracts import (
    ArrowFieldV1,
    ArrowSchemaV1,
    DatasetPartitionManifestV3,
    PITQueryMode,
    PITQueryV1,
)


T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = T1 + timedelta(days=1)
T3 = T1 + timedelta(days=2)
SERIES = UUID("10000000-0000-4000-8000-000000000001")


def schema(epoch: int = 2) -> ArrowSchemaV1:
    return ArrowSchemaV1(
        schema_id="canonical-bars-v3",
        data_api_epoch=epoch,
        fields=(
            ArrowFieldV1(field_id=1, name="ts_event", data_type="timestamp[ns,UTC]", nullable=False),
            ArrowFieldV1(field_id=2, name="close", data_type="decimal128(18,8)", nullable=False),
        ),
    )


def revision(
    ordinal: int,
    *,
    visible_at: datetime,
    partition_id: UUID | None = None,
    series_id: UUID = SERIES,
    predecessor: DatasetPartitionManifestV3 | None = None,
) -> DatasetPartitionManifestV3:
    return DatasetPartitionManifestV3(
        partition_id=partition_id or UUID(f"20000000-0000-4000-8000-{ordinal:012d}"),
        dataset="bars",
        partition_key=("BTCUSDT.BINANCE", "2026-01-01"),
        schema_id=schema().schema_id,
        schema_fingerprint=schema().fingerprint,
        data_api_epoch=2,
        partition_spec_version="day-v1",
        first_event_at=T1,
        last_event_at=T1,
        source_available_at=visible_at,
        system_observed_at=visible_at,
        ingested_at=visible_at,
        row_count=2,
        parquet_sha256=f"{ordinal:x}" * 64,
        parquet_size_bytes=128,
        canonical_rows_sha256="a" * 64,
        raw_evidence_sha256s=("b" * 64,),
        transform_receipt_sha256="c" * 64,
        quality_receipt_sha256="d" * 64,
        revision_series_id=series_id,
        revision_ordinal=ordinal,
        supersedes_partition_id=None if predecessor is None else predecessor.partition_id,
        supersedes_manifest_sha256=None if predecessor is None else predecessor.digest,
    )


def snapshot_at(cutoff: datetime, partitions: tuple[DatasetPartitionManifestV3, ...]):
    return build_snapshot_v3(
        dataset="bars",
        query=PITQueryV1(mode=PITQueryMode.AS_INGESTED, valid_at=T2, cutoff=cutoff),
        schema=schema(),
        partitions=partitions,
    )


def test_t2_snapshot_never_sees_correction_first_known_at_t3() -> None:
    original = revision(1, visible_at=T1)
    correction = revision(2, visible_at=T3, predecessor=original)

    at_t2 = snapshot_at(T2, (correction, original))
    at_t3 = snapshot_at(T3, (original, correction))

    assert at_t2.partitions == (original,)
    assert at_t3.partitions == (correction,)
    assert original.schema_version == "dataset-partition-manifest-v3"
    assert at_t2.schema_version == "dataset-snapshot-v3"
    assert at_t2.snapshot_digest != at_t3.snapshot_digest


def test_revision_root_requires_null_predecessors() -> None:
    with pytest.raises(ValidationError, match="E_REVISION_ROOT"):
        revision(1, visible_at=T1, predecessor=revision(1, visible_at=T1))


@pytest.mark.parametrize(
    ("code", "partitions"),
    (
        (
            "E_REVISION_GAP",
            lambda root: (root, revision(3, visible_at=T3, predecessor=root)),
        ),
        (
            "E_REVISION_FORK",
            lambda root: (
                root,
                revision(2, visible_at=T3, predecessor=root),
                revision(
                    2,
                    visible_at=T3,
                    partition_id=UUID("30000000-0000-4000-8000-000000000002"),
                    predecessor=root,
                ),
            ),
        ),
        (
            "E_REVISION_SERIES",
            lambda root: (
                root,
                revision(
                    1,
                    visible_at=T1,
                    partition_id=UUID("30000000-0000-4000-8000-000000000003"),
                    series_id=UUID("40000000-0000-4000-8000-000000000001"),
                ),
            ),
        ),
    ),
)
def test_invalid_revision_chains_fail_with_stable_codes(code: str, partitions: object) -> None:
    root = revision(1, visible_at=T1)
    with pytest.raises(DatasetRevisionError, match=code):
        snapshot_at(T3, partitions(root))  # type: ignore[operator]


def test_revision_predecessor_and_visible_head_must_be_exact() -> None:
    root = revision(1, visible_at=T1)
    wrong = revision(1, visible_at=T1, partition_id=UUID("50000000-0000-4000-8000-000000000001"))
    bad_predecessor = revision(2, visible_at=T3, predecessor=wrong)
    with pytest.raises(DatasetRevisionError, match="E_REVISION_PREDECESSOR"):
        snapshot_at(T3, (root, bad_predecessor))

    late_root = revision(1, visible_at=T3)
    early_correction = revision(2, visible_at=T2, predecessor=late_root)
    with pytest.raises(DatasetRevisionError, match="E_REVISION_HEAD"):
        snapshot_at(T2, (late_root, early_correction))


def test_v3_snapshot_rejects_a_mixed_data_api_epoch() -> None:
    root = revision(1, visible_at=T1)
    with pytest.raises(DatasetRevisionError, match="E_MIXED_DATA_EPOCH"):
        build_snapshot_v3(
            dataset="bars",
            query=PITQueryV1(mode=PITQueryMode.AS_INGESTED, valid_at=T2, cutoff=T2),
            schema=schema(1),
            partitions=(root,),
        )
