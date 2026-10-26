"""Truthful deterministic rehearsal evidence; no acquisition or execution authority.

P2 persists simulated partitions under a separate namespace. A daily bar's
partition_ref resolves to a typed synthetic provenance record, including the
P2 manifest and generation receipt. BufferOpen refers to separate noncyclic
dataset evidence for its simulated source bar. Official sealers are untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import hashlib
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pyarrow as pa

from packages.alpha_lifecycle import synthetic_prices
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity
from packages.alpha_lifecycle.contracts.data import BufferOpen, DailyBar, DatasetEvidence
from packages.alpha_lifecycle.data_view import _daily_bar, validate_dates
from packages.alpha_lifecycle.pit_evidence import _ReadBudget, _reference
from packages.alpha_lifecycle.replica_store import ArtifactStore, ReadbackStore, _read
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_catalog.v3 import build_snapshot_v3, materialize_arrow_partition_v3
from packages.data_contracts import ArrowFieldV1, ArrowSchemaV1, ArtifactRefV1, DatasetPartitionManifestV3, PITQueryV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes


class SyntheticGeneration(DigestModel):
    schema_version: Literal['p3-synthetic-generation-v1']
    purpose: Literal['SYNTHETIC_REHEARSAL_ONLY']
    source: SourceIdentity
    policy_digest: Sha256
    generation_id: UUID
    generator: Literal['p3.synthetic.seed9.v1']
    generator_sha256: Sha256
    seed: Literal[9]
    rows_sha256: Literal['54b4f688654789cec4668e3644f4b94a408534d5e86101883be9a27793bf23f7']
    row_count: Literal[3166]
    coordinates: Literal['SIMULATED_2018_01_01_TO_2026_09_01_NOT_MARKET_OBSERVATIONS']
    scenario_origin: Literal['HISTORICALLY_SCREENED_SYNTHETIC_FIXTURE_NO_ECONOMIC_EVIDENCE']
    generated_at: CanonicalUtcDateTime
    authority: SafeAuthority


class SyntheticDayReceipt(DigestModel):
    schema_version: Literal['p3-synthetic-day-v1']
    generation_ref: ArtifactRefV1
    provider: Literal['p3.synthetic.seed9']
    parser: Literal['p3.synthetic.seed9.v1']
    vintage_class: Literal['SYNTHETIC_REHEARSAL']
    normalized_row_sha256: Sha256


class SyntheticPartition(DigestModel):
    schema_version: Literal['p3-synthetic-partition-v1']
    generation_ref: ArtifactRefV1
    day_receipt_ref: ArtifactRefV1
    partition: DatasetPartitionManifestV3


class SyntheticBackup(DigestModel):
    schema_version: Literal['p3-synthetic-backup-v1']
    generation_ref: ArtifactRefV1
    inventory_ref: ArtifactRefV1
    verified_at: CanonicalUtcDateTime
    status: Literal['READBACK_VERIFIED']
    authority: SafeAuthority


class SyntheticCommitment(DigestModel):
    schema_version: Literal['p3-synthetic-commitment-v1']
    purpose: Literal['SYNTHETIC_REHEARSAL_ONLY']
    source: SourceIdentity
    policy_digest: Sha256
    generation_ref: ArtifactRefV1
    research_dataset_ref: ArtifactRefV1
    holdout_dataset_ref: ArtifactRefV1
    buffer_dataset_ref: ArtifactRefV1
    backup_receipt_ref: ArtifactRefV1
    issued_at: CanonicalUtcDateTime
    authority: SafeAuthority


@dataclass(frozen=True)
class SyntheticDatasets:
    generation: SyntheticGeneration
    generation_ref: ArtifactRefV1
    datasets: dict[str, ArtifactRefV1]


def _sealed(model, body):
    return model.model_validate_json(canonical_json_bytes({
        **body, 'digest': hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
    }))


def _retain(store: ArtifactStore, value) -> ArtifactRefV1:
    return store.put_bytes(canonical_json_bytes(value), media_type='application/json')


def _generator_hash() -> str:
    return hashlib.sha256(Path(synthetic_prices.__file__).read_bytes()).hexdigest()


def _utc(value: datetime) -> str:
    return value.isoformat().replace('+00:00', 'Z')


def _schema() -> tuple[ArrowSchemaV1, pa.Schema]:
    fields = (('ts_event', 'timestamp[ns,UTC]'), ('date', 'string'), ('instrument', 'string'),
        ('opened_at', 'timestamp[ns,UTC]'), ('closed_at_exclusive', 'timestamp[ns,UTC]'),
        ('raw_close_time', 'int64'), ('raw_timestamp_unit', 'string'), ('open', 'string'),
        ('high', 'string'), ('low', 'string'), ('close', 'string'), ('base_volume', 'string'),
        ('quote_volume', 'string'), ('trade_count', 'int64'), ('provider_published_at', 'timestamp[ns,UTC]'),
        ('synthetic_generation_sha256', 'string'), ('provider', 'string'), ('vintage_class', 'string'))
    schema = ArrowSchemaV1(schema_id='p3.synthetic.daily.v1', data_api_epoch=2,
        fields=tuple(ArrowFieldV1(field_id=i, name=name, data_type=kind,
            nullable=name == 'provider_published_at') for i, (name, kind) in enumerate(fields, 1)))
    types = {'timestamp[ns,UTC]': pa.timestamp('ns', tz='UTC'), 'string': pa.string(), 'int64': pa.int64()}
    return schema, pa.schema([pa.field(field.name, types[field.data_type], nullable=field.nullable)
                              for field in schema.fields])


def _segment(generation: SyntheticGeneration, generation_ref: ArtifactRefV1,
             segment: str, store: ArtifactStore) -> DatasetEvidence:
    slices = {'RESEARCH': (0, 2800), 'HOLDOUT': (2800, 3165), 'BUFFER': (3165, 3166)}
    if segment not in slices:
        raise ValueError('unknown synthetic segment')
    all_rows = synthetic_prices.daily_rows()
    if hashlib.sha256(canonical_json_bytes(all_rows)).hexdigest() != generation.rows_sha256:
        raise ValueError('synthetic generator no longer produces the frozen seed-9 scenario')
    start, stop = slices[segment]
    rows = all_rows[start:stop]
    validate_dates(segment, tuple(date.fromisoformat(row[0]) for row in rows))
    namespace = f'p3.synthetic.{segment.lower()}.daily'
    schema, arrow_schema = _schema()
    partitions, bars = [], []
    p2_store = cast(LocalArtifactStore, cast(object, store))
    for day_text, opened, high, low, close, base, quote in rows:
        day = date.fromisoformat(day_text)
        boundary = datetime(day.year, day.month, day.day, tzinfo=UTC)
        end = boundary + timedelta(days=1)
        row = dict(ts_event=end, date=day_text, instrument='BTCUSDT.BINANCE',
            opened_at=boundary, closed_at_exclusive=end,
            raw_close_time=int(end.timestamp()) * 1000 - 1, raw_timestamp_unit='MILLISECONDS',
            open=opened, high=high, low=low, close=close, base_volume=base, quote_volume=quote,
            trade_count=1000, provider_published_at=None,
            synthetic_generation_sha256=generation_ref.content_sha256,
            provider='p3.synthetic.seed9', vintage_class='SYNTHETIC_REHEARSAL')
        raw_ref = _retain(store, {key: _utc(value) if isinstance(value, datetime) else value
                                  for key, value in row.items()})
        receipt = _sealed(SyntheticDayReceipt, dict(schema_version='p3-synthetic-day-v1',
            generation_ref=generation_ref, provider='p3.synthetic.seed9', parser='p3.synthetic.seed9.v1',
            vintage_class='SYNTHETIC_REHEARSAL', normalized_row_sha256=raw_ref.content_sha256))
        receipt_ref = _retain(store, receipt)
        series = uuid5(NAMESPACE_URL, f'{namespace}/{generation.digest}/{day_text}')
        partition = materialize_arrow_partition_v3(pa.Table.from_pylist([row], schema=arrow_schema),
            schema=schema, store=p2_store, partition_id=uuid5(series, raw_ref.content_sha256),
            dataset=namespace, partition_key=('p3.synthetic.seed9', day_text),
            partition_spec_version='p3.synthetic.utc-day.v1',
            source_available_at=generation.generated_at, system_observed_at=generation.generated_at,
            ingested_at=generation.generated_at, raw_evidence_sha256s=(raw_ref.content_sha256,),
            transform_receipt_sha256=receipt_ref.content_sha256, quality_receipt_sha256=receipt_ref.content_sha256,
            revision_series_id=series, revision_ordinal=1).manifest
        proof = _sealed(SyntheticPartition, dict(schema_version='p3-synthetic-partition-v1',
            generation_ref=generation_ref, day_receipt_ref=receipt_ref, partition=partition))
        bar = _daily_bar(partition, p2_store)
        bar = _sealed(DailyBar, {**bar.model_dump(mode='json', exclude={'digest'}),
                                 'partition_ref': _retain(store, proof)})
        partitions.append(partition)
        bars.append(bar)
    query = PITQueryV1.model_validate_json(canonical_json_bytes(dict(mode='SYSTEM_OBSERVED',
        valid_at=_utc(bars[-1].closed_at_exclusive), cutoff=_utc(generation.generated_at))))
    snapshot = build_snapshot_v3(dataset=namespace, query=query, schema=schema, partitions=tuple(partitions))
    snapshot_ref = _retain(store, snapshot)
    row_refs = tuple(_retain(store, bar) for bar in bars)

    def evidence(refs):
        return _sealed(DatasetEvidence, dict(schema_version='p3-dataset-evidence-v1',
            snapshot_ref=snapshot_ref, query_digest=query.canonical_digest, segment=segment,
            date_range=dict(start=str(bars[0].date), end=str(bars[-1].date)), usable_rows=len(refs),
            row_refs=refs, ordered_rows_digest=hashlib.sha256(canonical_json_bytes(
                [ref.content_sha256 for ref in refs])).hexdigest(), vintage_class='SYNTHETIC_REHEARSAL',
            observed_cutoff=_utc(generation.generated_at), limitations=('synthetic.rehearsal_only',)))

    if segment == 'BUFFER':
        # The source-bar dataset contains DailyBar, not this derivative: no hash cycle.
        source_evidence_ref = _retain(store, evidence(row_refs))
        bar = bars[0]
        opening = _sealed(BufferOpen, dict(schema_version='p3-buffer-open-v1', date=str(bar.date),
            instrument=bar.instrument, opened_at=_utc(bar.opened_at), open=bar.open,
            source_evidence_ref=source_evidence_ref, observed_at=_utc(generation.generated_at)))
        row_refs = (_retain(store, opening),)
    return evidence(row_refs)


def generate_synthetic_datasets(*, store: LocalArtifactStore, source: SourceIdentity,
                                policy_digest: str) -> SyntheticDatasets:
    """Produce all fixed segments before outcomes; caller supplies a new private CAS."""
    if not isinstance(store, LocalArtifactStore) or any(store._root.iterdir()):
        raise ValueError('synthetic generation requires a new empty private CAS')
    now = datetime.now(UTC)
    if now <= datetime(2026, 9, 2, tzinfo=UTC):
        raise ValueError('generation time precedes the fixed simulated coordinates')
    generation = _sealed(SyntheticGeneration, dict(schema_version='p3-synthetic-generation-v1',
        purpose='SYNTHETIC_REHEARSAL_ONLY', source=source, policy_digest=policy_digest,
        generation_id=str(uuid4()), generator='p3.synthetic.seed9.v1', generator_sha256=_generator_hash(),
        seed=9, rows_sha256=synthetic_prices.ROWS_SHA256, row_count=3166,
        coordinates='SIMULATED_2018_01_01_TO_2026_09_01_NOT_MARKET_OBSERVATIONS',
        scenario_origin='HISTORICALLY_SCREENED_SYNTHETIC_FIXTURE_NO_ECONOMIC_EVIDENCE',
        generated_at=_utc(now), authority=dict(broker=False, live=False, network=False, production=False)))
    ref = _retain(store, generation)
    datasets = {segment: _retain(store, _segment(generation, ref, segment, store))
                for segment in ('RESEARCH', 'HOLDOUT', 'BUFFER')}
    return SyntheticDatasets(generation, ref, datasets)


def validate_synthetic_dataset(dataset_ref: ArtifactRefV1, *, generation_ref: ArtifactRefV1,
                               source: SourceIdentity, policy_digest: str, segment: str,
                               store: ArtifactStore) -> DatasetEvidence:
    """Explicit synthetic-only reconstruction. Never called by official validators.

    Reads the selected segment's rows. A protected HOLDOUT consumer may call this
    only after authorizing disclosure; metadata admission must use its commitment.
    """
    _reference(generation_ref, 65536)
    _reference(dataset_ref, 2097152)
    reader = _ReadBudget(store)
    generation = _read(reader, generation_ref, SyntheticGeneration)
    if (generation.source != source or generation.policy_digest != policy_digest
            or generation.generator_sha256 != _generator_hash()
            or not datetime(2026, 9, 2, tzinfo=UTC) < generation.generated_at <= datetime.now(UTC)):
        raise ValueError('synthetic generation source, generator, policy or time differs')
    expected = _segment(generation, generation_ref, segment, ReadbackStore(reader, reader))
    actual = _read(reader, dataset_ref, DatasetEvidence)
    if actual != expected:
        raise ValueError('synthetic dataset differs from its complete generation-bound reconstruction')
    return actual


def backup_synthetic_generation(result: SyntheticDatasets, *, store: LocalArtifactStore,
                                backup: LocalArtifactStore) -> ArtifactRefV1:
    """Reconstruct and read back all generated objects in a separate private CAS.

    This is local synthetic retention, never a market backup or producer receipt.
    The runtime's custodian must still establish protected ownership independently.
    """
    import os
    if (not isinstance(store, LocalArtifactStore) or not isinstance(backup, LocalArtifactStore)
            or os.path.samefile(store._root, backup._root)
            or store._root in backup._root.parents or backup._root in store._root.parents
            or any(backup._root.iterdir())):
        raise ValueError('synthetic backup requires a distinct empty private CAS')
    generation = _read(store, result.generation_ref, SyntheticGeneration)
    if generation != result.generation or generation.generator_sha256 != _generator_hash():
        raise ValueError('synthetic backup generation differs')
    inventory = {}

    class CopyReadback(ReadbackStore):
        def put_bytes(self, value, *, media_type):
            ref = super().put_bytes(value, media_type=media_type)
            if backup.put_bytes(value, media_type=media_type) != ref or backup.read_bytes(ref) != value:
                raise ValueError('synthetic backup readback differs')
            inventory[ref.content_sha256] = ref
            return ref

    reader = CopyReadback(_ReadBudget(store), store)
    _retain(reader, generation)
    for segment in ('RESEARCH', 'HOLDOUT', 'BUFFER'):
        dataset = _segment(generation, result.generation_ref, segment, reader)
        if _retain(reader, dataset) != result.datasets[segment]:
            raise ValueError('synthetic backup dataset differs')
    entries = [inventory[key] for key in sorted(inventory)]
    inventory_ref = _retain(store, entries)
    inventory_raw = store.read_bytes(inventory_ref)
    if backup.put_bytes(inventory_raw, media_type='application/json') != inventory_ref:
        raise ValueError('synthetic backup inventory differs')
    if backup.read_bytes(inventory_ref) != inventory_raw:
        raise ValueError('synthetic backup inventory readback differs')
    receipt = _sealed(SyntheticBackup, dict(schema_version='p3-synthetic-backup-v1',
        generation_ref=result.generation_ref, inventory_ref=inventory_ref,
        verified_at=_utc(datetime.now(UTC)), status='READBACK_VERIFIED', authority=generation.authority))
    commitment = _sealed(SyntheticCommitment, dict(schema_version='p3-synthetic-commitment-v1',
        purpose='SYNTHETIC_REHEARSAL_ONLY', source=generation.source, policy_digest=generation.policy_digest,
        generation_ref=result.generation_ref, research_dataset_ref=result.datasets['RESEARCH'],
        holdout_dataset_ref=result.datasets['HOLDOUT'], buffer_dataset_ref=result.datasets['BUFFER'],
        backup_receipt_ref=_retain(store, receipt), issued_at=_utc(datetime.now(UTC)), authority=generation.authority))
    return _retain(store, commitment)


def read_synthetic_commitment_metadata(ref: ArtifactRefV1, *, generation_ref: ArtifactRefV1,
                                     dataset_ref: ArtifactRefV1, source: SourceIdentity,
                                     policy_digest: str, store: ArtifactStore) -> SyntheticCommitment:
    """Compact structural binding, like official commitments; never custody proof.

    Full producer/backup reconstruction uses validate_synthetic_commitment. The
    protected reviewer binds those exact bytes before any live admission. Runtime
    fences re-read this compact graph; they never open the sealed HOLDOUT rows.
    """
    for item in (ref, generation_ref):
        _reference(item, 65536)
    _reference(dataset_ref, 2097152)
    reader = _ReadBudget(store)
    commitment = _read(reader, ref, SyntheticCommitment)
    generation = _read(reader, generation_ref, SyntheticGeneration)
    if (commitment.source != source or generation.source != source
            or commitment.policy_digest != policy_digest or generation.policy_digest != policy_digest
            or commitment.generation_ref != generation_ref or commitment.research_dataset_ref != dataset_ref
            or generation.generator_sha256 != _generator_hash()
            or not datetime(2026, 9, 2, tzinfo=UTC) < generation.generated_at <= datetime.now(UTC)):
        raise ValueError('synthetic commitment source, policy, generation or research differs')
    _reference(commitment.backup_receipt_ref, 65536)
    backup = _read(reader, commitment.backup_receipt_ref, SyntheticBackup)
    if (backup.generation_ref != generation_ref
            or not generation.generated_at <= backup.verified_at <= commitment.issued_at <= datetime.now(UTC)):
        raise ValueError('synthetic backup generation or timing differs')
    _reference(backup.inventory_ref, 67108864)
    from pydantic import TypeAdapter
    inventory_raw = reader.read_bytes(backup.inventory_ref)
    entries = TypeAdapter(list[ArtifactRefV1]).validate_json(inventory_raw)
    hashes = [entry.content_sha256 for entry in entries]
    if (canonical_json_bytes(entries) != inventory_raw
            or hashes != sorted(set(hashes)) or not 3166 <= len(entries) <= 30000):
        raise ValueError('synthetic backup inventory is not a bounded unique inventory')
    known = {entry.content_sha256: entry for entry in entries}
    for item in (generation_ref, commitment.research_dataset_ref,
                 commitment.holdout_dataset_ref, commitment.buffer_dataset_ref):
        _reference(item, 2097152)
        if known.get(item.content_sha256) != item:
            raise ValueError('synthetic backup inventory omits a bound dataset or generation')
    dataset = _read(reader, dataset_ref, DatasetEvidence)
    if (dataset.segment != 'RESEARCH' or dataset.vintage_class != 'SYNTHETIC_REHEARSAL'
            or dataset.limitations != ('synthetic.rehearsal_only',) or dataset.usable_rows != 2800
            or (str(dataset.date_range.start), str(dataset.date_range.end)) != ('2018-01-01', '2025-08-31')
            or dataset.observed_cutoff != generation.generated_at
            or dataset.ordered_rows_digest != hashlib.sha256(canonical_json_bytes(
                [row.content_sha256 for row in dataset.row_refs])).hexdigest()):
        raise ValueError('synthetic commitment research metadata differs')
    for item in (dataset.snapshot_ref, *dataset.row_refs):
        if known.get(item.content_sha256) != item:
            raise ValueError('synthetic backup inventory omits research objects')
    return commitment


def validate_synthetic_commitment(ref: ArtifactRefV1, *, generation_ref: ArtifactRefV1,
                                  dataset_ref: ArtifactRefV1, source: SourceIdentity,
                                  policy_digest: str, store: ArtifactStore) -> SyntheticCommitment:
    """Full research reconstruction; no HOLDOUT row reads and no admission."""
    reader = _ReadBudget(store)
    commitment = read_synthetic_commitment_metadata(ref, generation_ref=generation_ref,
        dataset_ref=dataset_ref, source=source, policy_digest=policy_digest, store=reader)
    validate_synthetic_dataset(dataset_ref, generation_ref=generation_ref, source=source,
        policy_digest=policy_digest, segment='RESEARCH', store=reader)
    return commitment
