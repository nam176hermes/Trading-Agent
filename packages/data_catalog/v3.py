"""Revision-aware Data API epoch 2 partition materialization and PIT selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import pyarrow as pa

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_catalog.v2 import materialize_arrow_partition
from packages.data_contracts import (
    ArrowSchemaV1,
    ArtifactRefV1,
    DatasetPartitionManifestV3,
    DatasetSnapshotV3,
    PITQueryV1,
)


class DatasetRevisionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class MaterializedPartitionV3:
    manifest: DatasetPartitionManifestV3
    artifact: ArtifactRefV1


def materialize_arrow_partition_v3(
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
    revision_series_id: UUID,
    revision_ordinal: int,
    supersedes_partition_id: UUID | None = None,
    supersedes_manifest_sha256: str | None = None,
) -> MaterializedPartitionV3:
    if schema.data_api_epoch != 2:
        raise DatasetRevisionError("E_MIXED_DATA_EPOCH", "V3 materialization requires Data API epoch 2")
    materialized = materialize_arrow_partition(
        table,
        schema=schema,
        store=store,
        partition_id=partition_id,
        dataset=dataset,
        partition_key=partition_key,
        partition_spec_version=partition_spec_version,
        source_available_at=source_available_at,
        system_observed_at=system_observed_at,
        ingested_at=ingested_at,
        raw_evidence_sha256s=raw_evidence_sha256s,
        transform_receipt_sha256=transform_receipt_sha256,
        quality_receipt_sha256=quality_receipt_sha256,
    )
    manifest = DatasetPartitionManifestV3(
        **materialized.manifest.model_dump(exclude={"schema_version"}),
        revision_series_id=revision_series_id,
        revision_ordinal=revision_ordinal,
        supersedes_partition_id=supersedes_partition_id,
        supersedes_manifest_sha256=supersedes_manifest_sha256,
    )
    return MaterializedPartitionV3(manifest=manifest, artifact=materialized.artifact)


def _validated_chains(
    dataset: str,
    schema: ArrowSchemaV1,
    partitions: tuple[DatasetPartitionManifestV3, ...],
) -> tuple[tuple[DatasetPartitionManifestV3, ...], ...]:
    if schema.data_api_epoch != 2 or any(
        type(partition) is not DatasetPartitionManifestV3
        or partition.data_api_epoch != 2
        for partition in partitions
    ):
        raise DatasetRevisionError("E_MIXED_DATA_EPOCH", "V3 inputs must use Data API epoch 2")
    grouped: dict[
        tuple[str, tuple[str, ...], str], list[DatasetPartitionManifestV3]
    ] = {}
    for partition in partitions:
        if (
            partition.dataset != dataset
            or partition.schema_id != schema.schema_id
            or partition.schema_fingerprint != schema.fingerprint
        ):
            raise DatasetRevisionError("E_REVISION_SERIES", "partition contract does not match its series")
        grouped.setdefault(
            (partition.dataset, partition.partition_key, partition.schema_id), []
        ).append(partition)

    chains: list[tuple[DatasetPartitionManifestV3, ...]] = []
    for values in grouped.values():
        if len({value.revision_series_id for value in values}) != 1:
            raise DatasetRevisionError("E_REVISION_SERIES", "one logical partition has multiple series")
        ordinals = [value.revision_ordinal for value in values]
        if len(ordinals) != len(set(ordinals)):
            raise DatasetRevisionError("E_REVISION_FORK", "a revision ordinal has multiple manifests")
        ordered = tuple(sorted(values, key=lambda value: value.revision_ordinal))
        if tuple(value.revision_ordinal for value in ordered) != tuple(range(1, len(ordered) + 1)):
            raise DatasetRevisionError("E_REVISION_GAP", "revision ordinals are not contiguous")
        for predecessor, current in zip(ordered, ordered[1:]):
            if (
                current.supersedes_partition_id != predecessor.partition_id
                or current.supersedes_manifest_sha256 != predecessor.digest
            ):
                raise DatasetRevisionError("E_REVISION_PREDECESSOR", "revision predecessor binding is invalid")
        chains.append(ordered)
    return tuple(chains)


def build_snapshot_v3(
    *,
    dataset: str,
    query: PITQueryV1,
    schema: ArrowSchemaV1,
    partitions: tuple[DatasetPartitionManifestV3, ...],
) -> DatasetSnapshotV3:
    canonical_query = PITQueryV1.model_validate(query)
    canonical_schema = ArrowSchemaV1.model_validate(schema)
    chains = _validated_chains(dataset, canonical_schema, partitions)
    visibility = canonical_query.visibility_field
    selected: list[DatasetPartitionManifestV3] = []
    for chain in chains:
        visible = tuple(
            partition
            for partition in chain
            if partition.last_event_at <= canonical_query.valid_at
            and getattr(partition, visibility) <= canonical_query.cutoff
        )
        if not visible:
            continue
        if tuple(value.revision_ordinal for value in visible) != tuple(
            range(1, visible[-1].revision_ordinal + 1)
        ):
            raise DatasetRevisionError("E_REVISION_HEAD", "visible revisions do not form a closed prefix")
        selected.append(visible[-1])
    return DatasetSnapshotV3(
        dataset=dataset,
        query=canonical_query,
        schema_id=canonical_schema.schema_id,
        schema_fingerprint=canonical_schema.fingerprint,
        data_api_epoch=canonical_schema.data_api_epoch,
        partitions=tuple(selected),
    )


__all__ = [
    "DatasetRevisionError",
    "MaterializedPartitionV3",
    "build_snapshot_v3",
    "materialize_arrow_partition_v3",
]
