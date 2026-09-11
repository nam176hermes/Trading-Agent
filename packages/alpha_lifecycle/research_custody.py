"""Compact structural data bindings; protected producer/admission is separate."""
import hashlib
import os
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.alpha_lifecycle.pit_evidence import _ReadBudget, _reference
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1, PITQueryMode, PITQueryV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes


class _ProducerReceipt(DigestModel):
    source: SourceIdentity
    producer_repository: Literal['nam176hermes/Trading-Agent']
    producer_workflow_ref: Literal['nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main']
    producer_run_id: Annotated[int, Field(ge=1)]
    producer_attempt: Annotated[int, Field(ge=1)]
    authority: SafeAuthority


class P3ResearchBackupReceipt(_ProducerReceipt):
    """A declaration until protected destination readback authenticates it."""
    schema_version: Literal['p3-research-backup-receipt-v1']
    inventory_content_sha256: Sha256
    dataset_content_sha256: Sha256
    snapshot_content_sha256: Sha256
    object_inventory_content_sha256: Sha256
    object_inventory_size_bytes: Annotated[int, Field(ge=1, le=67108864)]
    destination_namespace: Literal['p3.research.backup']
    object_version: Sha256
    object_count: Annotated[int, Field(ge=1, le=1000000)]
    total_bytes: Annotated[int, Field(ge=1, le=1073741824)]
    verified_at: CanonicalUtcDateTime
    status: Literal['READBACK_VERIFIED']

    @model_validator(mode='after')
    def _version(self):
        if self.object_version!=self.object_inventory_content_sha256 or self.total_bytes<self.object_count:
            raise ValueError('backup version or positive object-byte accounting differs')
        return self


class P3ResearchBatchCommitment(_ProducerReceipt):
    """No recursive raw inventory or predecessor edge; this is not admission."""
    schema_version: Literal['p3-research-batch-commitment-v1']
    policy_digest: Sha256
    segment: Literal['RESEARCH']
    query: PITQueryV1
    inventory_content_sha256: Sha256
    inventory_size_bytes: Annotated[int, Field(ge=1, le=67108864)]
    inventory_entry_count: Annotated[int, Field(ge=2800, le=100000)]
    dataset_content_sha256: Sha256
    dataset_digest: Sha256
    snapshot_content_sha256: Sha256
    batch_ordinal: Annotated[int, Field(ge=1)]
    predecessor_commitment_content_sha256: Sha256 | None
    frozen_at: CanonicalUtcDateTime
    completed_at: CanonicalUtcDateTime
    issued_at: CanonicalUtcDateTime
    backup_receipt_ref: ArtifactRefV1
    status: Literal['PASS']

    @model_validator(mode='after')
    def _bindings(self):
        _reference(self.backup_receipt_ref,65536)
        if (self.batch_ordinal==1)!=(self.predecessor_commitment_content_sha256 is None):
            raise ValueError('FIRST batch iff predecessor is absent')
        if (self.query.mode is not PITQueryMode.SYSTEM_OBSERVED
            or self.query.valid_at!=datetime(2025,9,1,tzinfo=UTC)
            or self.query.cutoff!=self.completed_at
            or not self.frozen_at<=self.completed_at<=self.issued_at):
            raise ValueError('research commitment query or inventory/publication timing differs')
        return self


def validate_research_batch_commitment(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, dataset_ref: ArtifactRefV1, dataset: DatasetEvidence,
    store: ArtifactStore) -> P3ResearchBatchCommitment:
    """Verify retained structure only; never infer authentic custody from hashes."""
    _reference(ref,65536)
    _reference(dataset_ref,2097152)
    reader=_ReadBudget(store)
    commitment=_read(reader,ref,P3ResearchBatchCommitment)
    dataset=DatasetEvidence.model_validate(dataset)
    if _read(reader,dataset_ref,DatasetEvidence)!=dataset:
        raise ValueError('research commitment dataset bytes differ')
    _reference(dataset.snapshot_ref,67108864)
    for row_ref in dataset.row_refs:
        _reference(row_ref,65536)
    if len({row.content_sha256 for row in dataset.row_refs})!=len(dataset.row_refs):
        raise ValueError('fixed daily research rows must be distinct')
    if dataset.ordered_rows_digest!=hashlib.sha256(canonical_json_bytes(
        [row.content_sha256 for row in dataset.row_refs])).hexdigest():
        raise ValueError('research ordered row digest differs')
    known={commitment.inventory_content_sha256:commitment.inventory_size_bytes}
    for known_ref in (dataset_ref,dataset.snapshot_ref,*dataset.row_refs):
        if known.setdefault(known_ref.content_sha256,known_ref.size_bytes)!=known_ref.size_bytes:
            raise ValueError('one known object digest declares inconsistent sizes')
    if (commitment.source!=SourceIdentity.model_validate(source) or commitment.policy_digest!=policy_digest
        or commitment.dataset_content_sha256!=dataset_ref.content_sha256
        or commitment.dataset_digest!=dataset.digest
        or commitment.snapshot_content_sha256!=dataset.snapshot_ref.content_sha256
        or dataset.segment!='RESEARCH' or dataset.usable_rows!=2800
        or dataset.date_range.start!=date(2018,1,1) or dataset.date_range.end!=date(2025,8,31)
        or dataset.vintage_class!='RETROSPECTIVE_CURRENT_ARCHIVE'
        or dataset.limitations!=('retrospective.current_archive',)
        or dataset.query_digest!=commitment.query.canonical_digest
        or dataset.observed_cutoff!=commitment.completed_at):
        raise ValueError('research commitment source, policy or fixed dataset differs')
    backup=_read(reader,commitment.backup_receipt_ref,P3ResearchBackupReceipt)
    if (backup.source!=commitment.source
        or backup.inventory_content_sha256!=commitment.inventory_content_sha256
        or backup.dataset_content_sha256!=commitment.dataset_content_sha256
        or backup.snapshot_content_sha256!=commitment.snapshot_content_sha256
        or backup.producer_run_id!=commitment.producer_run_id
        or backup.producer_attempt!=commitment.producer_attempt
        or backup.object_count<max(commitment.inventory_entry_count,len(known))
        or backup.total_bytes<sum(known.values())
        or not commitment.completed_at<=backup.verified_at<=commitment.issued_at):
        raise ValueError('research backup subject, producer or timing differs')
    return commitment


def validate_input_commitment(input_set_ref: ArtifactRefV1, *, store: ArtifactStore) -> P3ResearchBatchCommitment:
    """Resolve the exact structural commitment; producer and reviewer admission is separate."""
    from packages.alpha_lifecycle.contracts.execution import InputSet
    from packages.alpha_lifecycle.contracts.data import PITProof

    _reference(input_set_ref,65536)
    reader=_ReadBudget(store)
    inputs=_read(reader,input_set_ref,InputSet)
    _reference(inputs.pit_proof_ref,65536)
    _reference(inputs.dataset_evidence_ref,2097152)
    pit=_read(reader,inputs.pit_proof_ref,PITProof)
    dataset=_read(reader,inputs.dataset_evidence_ref,DatasetEvidence)
    if (pit.dataset_ref!=inputs.dataset_evidence_ref or pit.fold_manifest_ref!=inputs.fold_manifest_ref
        or pit.vintage_class!=dataset.vintage_class or pit.limitations!=dataset.limitations
        or pit.historical_vintage_verified):
        raise ValueError('InputSet PIT proof does not bind the current-archive research dataset')
    return validate_research_batch_commitment(pit.revision_proof_ref,source=inputs.source,
        policy_digest=inputs.policy_digest,dataset_ref=inputs.dataset_evidence_ref,dataset=dataset,store=reader)


def backup_revision_inventory(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, query: PITQueryV1, dataset_ref: ArtifactRefV1,
    store: LocalArtifactStore, backup: LocalArtifactStore,
    producer_run_id: int, producer_attempt: int) -> tuple[P3ResearchBackupReceipt, ArtifactRefV1]:
    """Copy reconstructed objects and read back; host custody is checked separately."""
    from packages.alpha_lifecycle.acquisition import _retain_json
    from packages.alpha_lifecycle.pit_evidence import validate_revision_inventory

    if (type(producer_run_id) is not int or producer_run_id<1
        or type(producer_attempt) is not int or producer_attempt<1):
        raise ValueError('backup producer run and attempt must be positive integers')
    query=PITQueryV1.model_validate(query)
    if query.cutoff>datetime.now(UTC):
        raise ValueError('backup cannot verify a future inventory cutoff')
    if (not isinstance(store,LocalArtifactStore) or not isinstance(backup,LocalArtifactStore)
        or os.path.samefile(store._root,backup._root)
        or store._root in backup._root.parents or backup._root in store._root.parents):
        raise ValueError('backup needs distinct private source and destination stores')
    objects={}
    sizes={}

    class RecordingReader:
        def read_bytes(self, item: ArtifactRefV1) -> bytes:
            key=(item.content_sha256,item.size_bytes,item.media_type,item.locator)
            if len(objects)>=1000000 and key not in objects:
                raise ValueError('backup object count exceeds its bound')
            if sizes.setdefault(item.content_sha256,item.size_bytes)!=item.size_bytes:
                raise ValueError('backup object declares inconsistent sizes')
            objects[key]=item
            return store.read_bytes(item)

    dataset=validate_revision_inventory(ref,source=source,policy_digest=policy_digest,
        query=query,dataset_ref=dataset_ref,store=RecordingReader())
    total_bytes=sum(sizes.values())
    inventory=canonical_json_bytes([objects[key] for key in sorted(objects)])
    if total_bytes>1073741824 or len(inventory)>67108864:
        raise ValueError('backup bytes or object inventory exceeds its bound')
    reader=_ReadBudget(store)
    for key in sorted(objects):
        item=objects[key]
        raw=reader.read_bytes(item)
        if backup.put_bytes(raw,media_type=item.media_type)!=item or backup.read_bytes(item)!=raw:
            raise ValueError('backup destination identity or readback differs')
    inventory_ref=_retain_json(inventory,backup)
    payload=dict(schema_version='p3-research-backup-receipt-v1',source=source,
        producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref='nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main',
        producer_run_id=producer_run_id,producer_attempt=producer_attempt,
        authority=dict(broker=False,live=False,network=False,production=False),
        inventory_content_sha256=ref.content_sha256,dataset_content_sha256=dataset_ref.content_sha256,
        snapshot_content_sha256=dataset.snapshot_ref.content_sha256,
        object_inventory_content_sha256=inventory_ref.content_sha256,
        object_inventory_size_bytes=inventory_ref.size_bytes,
        destination_namespace='p3.research.backup',object_version=inventory_ref.content_sha256,
        object_count=len(sizes),total_bytes=total_bytes,
        verified_at=datetime.now(UTC).isoformat().replace('+00:00','Z'),status='READBACK_VERIFIED')
    payload['digest']=hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return P3ResearchBackupReceipt.model_validate_json(canonical_json_bytes(payload)),inventory_ref
