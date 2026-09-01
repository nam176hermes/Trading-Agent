"""Canonical P2 point-in-time data contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from packages.engine_contracts.serialization import (
    CanonicalUtcDateTime,
    Sha256Hex,
    canonical_json_bytes,
)


_Token = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
]
_Version = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


class PITQueryMode(str, Enum):
    MARKET_AVAILABLE = "MARKET_AVAILABLE"
    SYSTEM_OBSERVED = "SYSTEM_OBSERVED"
    AS_INGESTED = "AS_INGESTED"


class PITQueryV1(_FrozenModel):
    mode: PITQueryMode
    valid_at: CanonicalUtcDateTime
    cutoff: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _ordered(self) -> "PITQueryV1":
        if self.cutoff < self.valid_at:
            raise ValueError("cutoff must not precede valid_at")
        return self

    @property
    def visibility_field(self) -> str:
        return {
            PITQueryMode.MARKET_AVAILABLE: "source_available_at",
            PITQueryMode.SYSTEM_OBSERVED: "system_observed_at",
            PITQueryMode.AS_INGESTED: "ingested_at",
        }[self.mode]

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @property
    def canonical_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class ArrowFieldV1(_FrozenModel):
    field_id: Annotated[int, Field(ge=1, le=65535)]
    name: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
    ]
    data_type: Annotated[str, Field(min_length=1, max_length=128)]
    nullable: bool


class ArrowSchemaV1(_FrozenModel):
    schema_id: _Token
    data_api_epoch: Annotated[int, Field(ge=1, le=65535)]
    fields: Annotated[tuple[ArrowFieldV1, ...], Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def _canonical_fields(self) -> "ArrowSchemaV1":
        ordered = tuple(sorted(self.fields, key=lambda value: value.field_id))
        ids = tuple(value.field_id for value in ordered)
        names = tuple(value.name for value in ordered)
        if len(ids) != len(set(ids)):
            raise ValueError("field_id values must be unique")
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        object.__setattr__(self, "fields", ordered)
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


class ProviderCapabilityV1(str, Enum):
    MARKET_BARS = "MARKET_BARS"
    QUOTES = "QUOTES"
    TRADES = "TRADES"
    FUNDING = "FUNDING"
    SECURITY_MASTER = "SECURITY_MASTER"
    CORPORATE_ACTIONS = "CORPORATE_ACTIONS"
    DOCUMENT = "DOCUMENT"


class ArtifactRefV1(_FrozenModel):
    content_sha256: Sha256Hex
    size_bytes: Annotated[int, Field(ge=0, le=1 << 40)]
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    locator: Annotated[
        str,
        Field(min_length=69, max_length=69, pattern=r"^[0-9a-f]{64}\.blob$"),
    ]


class RawEvidenceArtifactV1(_FrozenModel):
    evidence_id: UUID
    provider: _Token
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    byte_length: Annotated[int, Field(ge=0, le=1 << 40)]
    content_sha256: Sha256Hex
    source_available_at: CanonicalUtcDateTime
    system_observed_at: CanonicalUtcDateTime
    fetched_at: CanonicalUtcDateTime

    @model_validator(mode="after")
    def _ordered(self) -> "RawEvidenceArtifactV1":
        if not self.source_available_at <= self.system_observed_at <= self.fetched_at:
            raise ValueError(
                "evidence requires source_available_at <= system_observed_at <= fetched_at"
            )
        return self


class ProviderReceiptV1(_FrozenModel):
    provider: _Token
    capability: ProviderCapabilityV1
    query_sha256: Sha256Hex
    evidence: Annotated[tuple[RawEvidenceArtifactV1, ...], Field(min_length=1, max_length=128)]
    normalization_version: _Version
    output_sha256s: Annotated[tuple[Sha256Hex, ...], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _canonical_sets(self) -> "ProviderReceiptV1":
        evidence = tuple(sorted(self.evidence, key=lambda value: value.evidence_id.bytes))
        evidence_ids = tuple(value.evidence_id for value in evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence identifiers must be unique")
        outputs = tuple(sorted(self.output_sha256s))
        if len(outputs) != len(set(outputs)):
            raise ValueError("output digests must be unique")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "output_sha256s", outputs)
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


class DatasetPartitionManifestV2(_FrozenModel):
    partition_id: UUID
    dataset: _Token
    partition_key: Annotated[tuple[_Token, ...], Field(min_length=1, max_length=16)]
    schema_id: _Token
    schema_fingerprint: Sha256Hex
    data_api_epoch: Annotated[int, Field(ge=1, le=65535)]
    partition_spec_version: _Version
    first_event_at: CanonicalUtcDateTime
    last_event_at: CanonicalUtcDateTime
    source_available_at: CanonicalUtcDateTime
    system_observed_at: CanonicalUtcDateTime
    ingested_at: CanonicalUtcDateTime
    row_count: Annotated[int, Field(ge=1, le=1_000_000_000)]
    parquet_sha256: Sha256Hex
    parquet_size_bytes: Annotated[int, Field(ge=1, le=1 << 30)]
    canonical_rows_sha256: Sha256Hex
    raw_evidence_sha256s: Annotated[tuple[Sha256Hex, ...], Field(min_length=1, max_length=4096)]
    transform_receipt_sha256: Sha256Hex
    quality_receipt_sha256: Sha256Hex

    @model_validator(mode="after")
    def _canonical_partition(self) -> "DatasetPartitionManifestV2":
        if not (
            self.first_event_at
            <= self.last_event_at
            <= self.source_available_at
            <= self.system_observed_at
            <= self.ingested_at
        ):
            raise ValueError("partition event and visibility times are not ordered")
        evidence = tuple(sorted(self.raw_evidence_sha256s))
        if len(evidence) != len(set(evidence)):
            raise ValueError("raw evidence digests must be unique")
        object.__setattr__(self, "raw_evidence_sha256s", evidence)
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


class DatasetSnapshotV2(_FrozenModel):
    dataset: _Token
    query: PITQueryV1
    schema_id: _Token
    schema_fingerprint: Sha256Hex
    data_api_epoch: Annotated[int, Field(ge=1, le=65535)]
    partitions: Annotated[
        tuple[DatasetPartitionManifestV2, ...], Field(max_length=100_000)
    ]

    @model_validator(mode="after")
    def _closed_snapshot(self) -> "DatasetSnapshotV2":
        ordered = tuple(
            sorted(
                self.partitions,
                key=lambda value: (
                    value.partition_key,
                    value.first_event_at,
                    value.partition_id.bytes,
                ),
            )
        )
        partition_ids = tuple(value.partition_id for value in ordered)
        if len(partition_ids) != len(set(partition_ids)):
            raise ValueError("partition identifiers must be unique")
        visibility = self.query.visibility_field
        for partition in ordered:
            if (
                partition.dataset != self.dataset
                or partition.schema_id != self.schema_id
                or partition.schema_fingerprint != self.schema_fingerprint
                or partition.data_api_epoch != self.data_api_epoch
            ):
                raise ValueError("partition schema or dataset contract does not match snapshot")
            if getattr(partition, visibility) > self.query.cutoff:
                raise ValueError("partition is not visible at the requested PIT cutoff")
            if partition.last_event_at > self.query.valid_at:
                raise ValueError("partition event window exceeds the requested valid_at")
        object.__setattr__(self, "partitions", ordered)
        return self

    @computed_field
    @property
    def row_count(self) -> int:
        return sum(value.row_count for value in self.partitions)

    @property
    def snapshot_digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


class DatasetPartitionManifestV3(DatasetPartitionManifestV2):
    schema_version: Literal["dataset-partition-manifest-v3"] = (
        "dataset-partition-manifest-v3"
    )
    revision_series_id: UUID
    revision_ordinal: Annotated[int, Field(ge=1, le=1_000_000)]
    supersedes_partition_id: UUID | None
    supersedes_manifest_sha256: Sha256Hex | None

    @model_validator(mode="after")
    def _revision_identity(self) -> "DatasetPartitionManifestV3":
        if self.data_api_epoch != 2:
            raise ValueError("E_MIXED_DATA_EPOCH: V3 partitions require Data API epoch 2")
        predecessors = (
            self.supersedes_partition_id,
            self.supersedes_manifest_sha256,
        )
        if self.revision_ordinal == 1 and any(value is not None for value in predecessors):
            raise ValueError("E_REVISION_ROOT: revision roots cannot supersede a partition")
        if self.revision_ordinal > 1 and any(value is None for value in predecessors):
            raise ValueError("E_REVISION_PREDECESSOR: non-root revisions require exact predecessors")
        return self


class DatasetSnapshotV3(_FrozenModel):
    schema_version: Literal["dataset-snapshot-v3"] = "dataset-snapshot-v3"
    dataset: _Token
    query: PITQueryV1
    schema_id: _Token
    schema_fingerprint: Sha256Hex
    data_api_epoch: Annotated[int, Field(ge=1, le=65535)]
    partitions: Annotated[
        tuple[DatasetPartitionManifestV3, ...], Field(max_length=100_000)
    ]

    @model_validator(mode="after")
    def _closed_snapshot(self) -> "DatasetSnapshotV3":
        if self.data_api_epoch != 2:
            raise ValueError("E_MIXED_DATA_EPOCH: V3 snapshots require Data API epoch 2")
        ordered = tuple(
            sorted(
                self.partitions,
                key=lambda value: (
                    value.partition_key,
                    value.first_event_at,
                    value.partition_id.bytes,
                ),
            )
        )
        if len({value.partition_id for value in ordered}) != len(ordered):
            raise ValueError("partition identifiers must be unique")
        visibility = self.query.visibility_field
        for partition in ordered:
            if (
                partition.dataset != self.dataset
                or partition.schema_id != self.schema_id
                or partition.schema_fingerprint != self.schema_fingerprint
                or partition.data_api_epoch != self.data_api_epoch
            ):
                raise ValueError("partition schema or dataset contract does not match snapshot")
            if getattr(partition, visibility) > self.query.cutoff:
                raise ValueError("partition is not visible at the requested PIT cutoff")
            if partition.last_event_at > self.query.valid_at:
                raise ValueError("partition event window exceeds the requested valid_at")
        object.__setattr__(self, "partitions", ordered)
        return self

    @computed_field
    @property
    def row_count(self) -> int:
        return sum(value.row_count for value in self.partitions)

    @property
    def snapshot_digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


__all__ = [
    "ArtifactRefV1",
    "ArrowFieldV1",
    "ArrowSchemaV1",
    "DatasetPartitionManifestV2",
    "DatasetPartitionManifestV3",
    "DatasetSnapshotV2",
    "DatasetSnapshotV3",
    "PITQueryMode",
    "PITQueryV1",
    "ProviderCapabilityV1",
    "ProviderReceiptV1",
    "RawEvidenceArtifactV1",
]
