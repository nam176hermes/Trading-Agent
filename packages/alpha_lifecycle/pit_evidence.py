"""Construct P3 PIT proof only after dataset and fold evidence exist."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Annotated, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, model_validator

from packages.alpha_lifecycle.contracts.base import DigestModel, Sha256, SourceIdentity, StrictModel
from packages.alpha_lifecycle.contracts.data import DatasetEvidence, Day, FoldManifest, PITProof
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1, DatasetPartitionManifestV3, PITQueryMode, PITQueryV1
from packages.engine_contracts.serialization import canonical_json_bytes


def _reference(ref: ArtifactRefV1, maximum: int, media: str = 'application/json') -> None:
    ref=ArtifactRefV1.model_validate(ref)
    if (ref.media_type!=media or not 0<ref.size_bytes<=maximum
        or ref.locator!=ref.content_sha256+'.blob'):
        raise ValueError('revision artifact reference is outside its exact bound')


class P3RevisionEntry(StrictModel):
    day: Day
    acquisition_ref: ArtifactRefV1
    normalized_ref: ArtifactRefV1
    provider_ref: ArtifactRefV1
    quality_ref: ArtifactRefV1
    partition: DatasetPartitionManifestV3

    @model_validator(mode='after')
    def _bounds(self):
        for ref in (self.acquisition_ref,self.normalized_ref,self.provider_ref,self.quality_ref):
            _reference(ref,65536)
        if not 0<self.partition.parquet_size_bytes<=1048576:
            raise ValueError('daily Parquet exceeds its exact bound')
        return self


class P3ResearchRevisionInventory(DigestModel):
    """Declared standalone universe; this model confers no protected custody."""
    schema_version: Literal['p3-research-revision-inventory-v1']
    digest: Sha256
    source: SourceIdentity
    policy_digest: Sha256
    entries: Annotated[tuple[P3RevisionEntry,...],Field(min_length=2800,max_length=100000)]

    @model_validator(mode='after')
    def _ordered(self):
        keys=tuple((entry.day,entry.partition.revision_ordinal) for entry in self.entries)
        if keys!=tuple(sorted(set(keys))):
            raise ValueError('revision inventory must contain ordered unique day/ordinal pairs')
        return self


class _ReadBudget:
    def __init__(self, store):
        self.store=store
        self.remaining=1073741824

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        ref=ArtifactRefV1.model_validate(ref)
        if (ref.locator!=ref.content_sha256+'.blob' or not 0<ref.size_bytes<=self.remaining):
            raise ValueError('revision reconstruction exceeds its read budget')
        self.remaining-=ref.size_bytes
        raw=self.store.read_bytes(ref)
        if len(raw)!=ref.size_bytes or hashlib.sha256(raw).hexdigest()!=ref.content_sha256:
            raise ValueError('revision retained bytes differ from their reference')
        return raw

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        raise ValueError('revision reconstruction cannot write an artifact')


def validate_revision_inventory(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, query: PITQueryV1, dataset_ref: ArtifactRefV1, store) -> DatasetEvidence:
    """Reconstruct retained inputs only; protected provenance remains separate."""
    from packages.alpha_lifecycle.acquisition import (
        daily_arrow_table, normalize_daily_acquisition, retain_daily_quality_receipt,
        retain_normalization_receipt, validate_acquisition_receipt,
    )
    from packages.alpha_lifecycle.baseline_campaign import ReadbackStore, _read
    from packages.alpha_lifecycle.data_view import _partition_ref, seal_research_dataset, validate_dates
    from packages.data_catalog.v3 import build_snapshot_v3, materialize_arrow_partition_v3

    _reference(ref,67108864)
    _reference(dataset_ref,2097152)
    source=SourceIdentity.model_validate(source)
    query=PITQueryV1.model_validate(query)
    if query.mode is not PITQueryMode.SYSTEM_OBSERVED or query.valid_at!=datetime(2025,9,1,tzinfo=UTC):
        raise ValueError('revision query differs from the fixed research query')
    budget=_ReadBudget(store)
    reader=ReadbackStore(budget,budget)
    inventory=_read(reader,ref,P3ResearchRevisionInventory)
    if inventory.source!=source or inventory.policy_digest!=policy_digest:
        raise ValueError('revision inventory source or policy differs')
    days=tuple(sorted({entry.day for entry in inventory.entries}))
    validate_dates('RESEARCH',days)
    dataset=_read(reader,dataset_ref,DatasetEvidence)
    if (dataset.segment!='RESEARCH' or dataset.usable_rows!=2800
        or dataset.date_range.start!=date(2018,1,1) or dataset.date_range.end!=date(2025,8,31)
        or dataset.observed_cutoff!=query.cutoff or dataset.query_digest!=query.canonical_digest):
        raise ValueError('revision dataset or cutoff differs')
    _reference(dataset.snapshot_ref,67108864)
    for row_ref in dataset.row_refs:
        _reference(row_ref,65536)
    for entry in inventory.entries:
        if (entry.partition.dataset!='p3.research.daily'
            or entry.partition.partition_key!=('BTCUSDT.BINANCE',entry.day.isoformat())
            or entry.partition.partition_spec_version!='p3.utc-day.v1'
            or entry.partition.ingested_at>query.cutoff):
            raise ValueError('revision partition namespace, key, spec or cutoff differs')
    schema,_=daily_arrow_table(inventory.entries[0].acquisition_ref,reader)
    partitions=tuple(entry.partition for entry in inventory.entries)
    build_snapshot_v3(dataset='p3.research.daily',query=query,schema=schema,partitions=partitions)
    previous={}
    # P2 names its concrete store in annotations; readback exposes those same
    # read/put methods, with put implemented as comparison against retained bytes.
    p2_store=cast(LocalArtifactStore,cast(object,reader))
    for entry in inventory.entries:
        acquired=validate_acquisition_receipt(entry.acquisition_ref,reader)
        if acquired.day!=entry.day or acquired.fetched_at>entry.partition.ingested_at:
            raise ValueError('revision acquisition day or retention time differs')
        normalized=canonical_json_bytes(normalize_daily_acquisition(entry.acquisition_ref,reader))
        if reader.put_bytes(normalized,media_type='application/json')!=entry.normalized_ref:
            raise ValueError('revision normalized document differs')
        if (retain_normalization_receipt(entry.acquisition_ref,reader)!=entry.provider_ref
            or retain_daily_quality_receipt(entry.acquisition_ref,reader)!=entry.quality_ref):
            raise ValueError('revision provider or quality receipt differs')
        actual_schema,table=daily_arrow_table(entry.acquisition_ref,reader)
        if actual_schema!=schema:
            raise ValueError('revision Arrow schema differs')
        pair=(acquired.archive_ref.content_sha256,acquired.checksum_ref.content_sha256)
        prior=previous.get(entry.day)
        if prior is not None and prior[1]==pair:
            raise ValueError('identical current raw pair cannot create another revision')
        series=uuid5(NAMESPACE_URL,f'p3.research.daily/BTCUSDT.BINANCE/{entry.day.isoformat()}')
        identity=canonical_json_bytes(dict(archive_sha256=pair[0],checksum_sha256=pair[1],
            revision_ordinal=entry.partition.revision_ordinal)).decode()
        actual=materialize_arrow_partition_v3(table,schema=schema,store=p2_store,
            partition_id=uuid5(series,identity),dataset='p3.research.daily',
            partition_key=('BTCUSDT.BINANCE',entry.day.isoformat()),partition_spec_version='p3.utc-day.v1',
            source_available_at=acquired.system_observed_at,system_observed_at=acquired.system_observed_at,
            ingested_at=entry.partition.ingested_at,raw_evidence_sha256s=pair,
            transform_receipt_sha256=entry.provider_ref.content_sha256,
            quality_receipt_sha256=entry.quality_ref.content_sha256,
            revision_series_id=series,revision_ordinal=entry.partition.revision_ordinal,
            supersedes_partition_id=None if prior is None else prior[0].partition_id,
            supersedes_manifest_sha256=None if prior is None else prior[0].digest)
        if actual.manifest!=entry.partition or actual.artifact!=_partition_ref(entry.partition):
            raise ValueError('revision Parquet or manifest differs from P2 reconstruction')
        previous[entry.day]=(entry.partition,pair)
    actual_dataset=seal_research_dataset(partitions,query,schema,p2_store)
    if actual_dataset!=dataset:
        raise ValueError('revision snapshot or dataset differs from P3 reconstruction')
    return dataset


def build_pit_proof(
    dataset: DatasetEvidence,
    fold_manifest: FoldManifest,
    revision_proof_ref: ArtifactRefV1,
    no_future_suite_ref: ArtifactRefV1,
    store: LocalArtifactStore,
) -> PITProof:
    dataset_ref = store.put_bytes(canonical_json_bytes(dataset), media_type="application/json")
    fold_ref = store.put_bytes(canonical_json_bytes(fold_manifest), media_type="application/json")
    payload = {
        "schema_version": "p3-p-i-t-proof-v1",
        "dataset_ref": dataset_ref,
        "fold_manifest_ref": fold_ref,
        "vintage_class": dataset.vintage_class,
        "historical_vintage_verified": dataset.vintage_class == "HISTORICAL_VINTAGE_VERIFIED",
        "revision_proof_ref": revision_proof_ref,
        "no_future_suite_ref": no_future_suite_ref,
        "limitations": dataset.limitations,
    }
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PITProof.model_validate(payload)


__all__ = ["build_pit_proof", "P3RevisionEntry", "P3ResearchRevisionInventory", "validate_revision_inventory"]
