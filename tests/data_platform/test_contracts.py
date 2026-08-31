from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.data_contracts import (
    ArrowFieldV1,
    ArrowSchemaV1,
    DatasetPartitionManifestV2,
    DatasetSnapshotV2,
    PITQueryMode,
    PITQueryV1,
    ProviderCapabilityV1,
    ProviderReceiptV1,
    RawEvidenceArtifactV1,
)
from packages.data_catalog.schema_registry import SchemaCompatibilityError, SchemaRegistry


T0 = datetime(2026, 1, 1, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def field(field_id: int, name: str, data_type: str) -> ArrowFieldV1:
    return ArrowFieldV1(
        field_id=field_id,
        name=name,
        data_type=data_type,
        nullable=False,
    )


def schema() -> ArrowSchemaV1:
    return ArrowSchemaV1(
        schema_id="canonical-bars",
        data_api_epoch=1,
        fields=(
            field(1, "ts_event", "timestamp[ns,UTC]"),
            field(2, "close", "decimal128(18,8)"),
        ),
    )


def partition(*, suffix: int = 1, start: datetime = T0) -> DatasetPartitionManifestV2:
    return DatasetPartitionManifestV2(
        partition_id=UUID(f"10000000-0000-4000-8000-{suffix:012d}"),
        dataset="bars",
        partition_key=("BTC-USD", "2026-01-01"),
        schema_id="canonical-bars",
        schema_fingerprint=schema().fingerprint,
        data_api_epoch=1,
        partition_spec_version="day-v1",
        first_event_at=start,
        last_event_at=start + timedelta(minutes=1),
        source_available_at=start + timedelta(minutes=2),
        system_observed_at=start + timedelta(minutes=3),
        ingested_at=start + timedelta(minutes=4),
        row_count=2,
        parquet_sha256=SHA_A,
        parquet_size_bytes=128,
        canonical_rows_sha256=SHA_B,
        raw_evidence_sha256s=(SHA_C,),
        transform_receipt_sha256=SHA_A,
        quality_receipt_sha256=SHA_B,
    )


@pytest.mark.parametrize(
    ("mode", "visibility_field"),
    (
        (PITQueryMode.MARKET_AVAILABLE, "source_available_at"),
        (PITQueryMode.SYSTEM_OBSERVED, "system_observed_at"),
        (PITQueryMode.AS_INGESTED, "ingested_at"),
    ),
)
def test_pit_query_requires_an_explicit_mode_and_exposes_its_visibility_field(
    mode: PITQueryMode,
    visibility_field: str,
) -> None:
    query = PITQueryV1(mode=mode, valid_at=T0, cutoff=T0 + timedelta(days=1))

    assert query.visibility_field == visibility_field
    assert query.canonical_digest == PITQueryV1.model_validate_json(
        query.canonical_bytes
    ).canonical_digest


def test_pit_query_rejects_non_utc_or_reverse_cutoffs() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        PITQueryV1(
            mode=PITQueryMode.AS_INGESTED,
            valid_at=T0.replace(tzinfo=None),
            cutoff=T0,
        )
    with pytest.raises(ValidationError, match="cutoff"):
        PITQueryV1(
            mode=PITQueryMode.AS_INGESTED,
            valid_at=T0 + timedelta(seconds=1),
            cutoff=T0,
        )


def test_arrow_schema_has_stable_unique_field_ids_and_a_semantic_fingerprint() -> None:
    value = schema()

    assert value.fingerprint == "3ec2a408ed1e9f55a047b006cda4cd02d9607d6fe37ce837b314b15ba0a93ad0"
    assert value.fields == tuple(sorted(value.fields, key=lambda item: item.field_id))
    with pytest.raises(ValidationError, match="field_id"):
        ArrowSchemaV1(
            schema_id="canonical-bars",
            data_api_epoch=1,
            fields=(field(1, "ts_event", "timestamp[ns,UTC]"), field(1, "close", "decimal128(18,8)")),
        )


def test_provider_receipt_binds_capability_query_evidence_and_outputs() -> None:
    evidence = RawEvidenceArtifactV1(
        evidence_id=UUID("20000000-0000-4000-8000-000000000001"),
        provider="fixture",
        media_type="application/json",
        byte_length=12,
        content_sha256=SHA_A,
        source_available_at=T0,
        system_observed_at=T0 + timedelta(seconds=1),
        fetched_at=T0 + timedelta(seconds=2),
    )
    receipt = ProviderReceiptV1(
        provider="fixture",
        capability=ProviderCapabilityV1.MARKET_BARS,
        query_sha256=SHA_B,
        evidence=(evidence,),
        normalization_version="bars-v1",
        output_sha256s=(SHA_C,),
    )

    assert receipt.evidence == (evidence,)
    assert receipt.digest == ProviderReceiptV1.model_validate_json(
        receipt.canonical_bytes
    ).digest
    with pytest.raises(ValidationError, match="evidence"):
        ProviderReceiptV1(
            provider="fixture",
            capability=ProviderCapabilityV1.MARKET_BARS,
            query_sha256=SHA_B,
            evidence=(evidence, evidence),
            normalization_version="bars-v1",
            output_sha256s=(SHA_C,),
        )


def test_dataset_snapshot_is_order_independent_and_rejects_mixed_contracts() -> None:
    first = partition(suffix=1)
    second = partition(suffix=2, start=T0 + timedelta(days=1))
    query = PITQueryV1(
        mode=PITQueryMode.AS_INGESTED,
        valid_at=T0 + timedelta(days=1, minutes=1),
        cutoff=T0 + timedelta(days=3),
    )

    left = DatasetSnapshotV2(
        dataset="bars",
        query=query,
        schema_id="canonical-bars",
        schema_fingerprint=schema().fingerprint,
        data_api_epoch=1,
        partitions=(second, first),
    )
    right = DatasetSnapshotV2(
        dataset="bars",
        query=query,
        schema_id="canonical-bars",
        schema_fingerprint=schema().fingerprint,
        data_api_epoch=1,
        partitions=(first, second),
    )

    assert left.partitions == (first, second)
    assert left.row_count == 4
    assert left.snapshot_digest == right.snapshot_digest
    with pytest.raises(ValidationError, match="schema"):
        DatasetSnapshotV2(
            dataset="bars",
            query=query,
            schema_id="other",
            schema_fingerprint=schema().fingerprint,
            data_api_epoch=1,
            partitions=(first,),
        )


def test_dataset_snapshot_contract_rejects_future_event_partition() -> None:
    value = partition()
    with pytest.raises(ValidationError, match="event window"):
        DatasetSnapshotV2(
            dataset="bars",
            query=PITQueryV1(
                mode=PITQueryMode.AS_INGESTED,
                valid_at=value.last_event_at - timedelta(seconds=1),
                cutoff=value.ingested_at,
            ),
            schema_id="canonical-bars",
            schema_fingerprint=schema().fingerprint,
            data_api_epoch=1,
            partitions=(value,),
        )


def test_schema_registry_allows_only_additive_stable_field_evolution() -> None:
    registry = SchemaRegistry()
    original = schema()
    additive = ArrowSchemaV1(
        schema_id="canonical-bars",
        data_api_epoch=2,
        fields=original.fields + (ArrowFieldV1(field_id=3, name="volume", data_type="decimal128(18,8)", nullable=True),),
    )

    assert registry.register(original) == original.fingerprint
    assert registry.register(original) == original.fingerprint
    assert registry.register(additive) == additive.fingerprint
    assert registry.require("canonical-bars", 2) == additive

    mutated = ArrowSchemaV1(
        schema_id="canonical-bars",
        data_api_epoch=3,
        fields=(field(1, "ts_event", "timestamp[ns,UTC]"), field(2, "close", "float64")),
    )
    with pytest.raises(SchemaCompatibilityError, match="field"):
        registry.register(mutated)

    non_nullable_addition = ArrowSchemaV1(
        schema_id="canonical-bars",
        data_api_epoch=3,
        fields=additive.fields
        + (ArrowFieldV1(field_id=4, name="venue", data_type="string", nullable=False),),
    )
    with pytest.raises(SchemaCompatibilityError, match="nullable"):
        registry.register(non_nullable_addition)
