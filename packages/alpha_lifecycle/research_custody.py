"""Compact structural data bindings; protected producer/admission is separate."""
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from packages.alpha_lifecycle.replica_store import ArtifactStore, _read
from packages.alpha_lifecycle.contracts.authority import LocalRunIdentity
from packages.alpha_lifecycle.contracts.base import DigestModel, SafeAuthority, Sha256, SourceIdentity
from packages.alpha_lifecycle.contracts.data import DatasetEvidence
from packages.alpha_lifecycle.pit_evidence import _ReadBudget, _reference
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1, PITQueryMode, PITQueryV1
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes


class _ResearchReceipt(DigestModel):
    """Producer-neutral research facts; each lane adds its own producer metadata."""
    source: SourceIdentity
    authority: SafeAuthority


class _BackupReceipt(_ResearchReceipt):
    """A declaration until protected destination readback authenticates it."""
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


class P3ResearchBackupReceipt(_BackupReceipt):
    """GITHUB v1 lane; field set and canonical bytes are frozen."""
    schema_version: Literal['p3-research-backup-receipt-v1']
    producer_repository: Literal['nam176hermes/Trading-Agent']
    producer_workflow_ref: Literal['nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main']
    producer_run_id: Annotated[int, Field(ge=1)]
    producer_attempt: Annotated[int, Field(ge=1)]


# The GITHUB producer is the protected p3-authority.yml operation `p3-research-inputs-v1`.
AUTHORITY_WORKFLOW_REF='nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main'


class P3ResearchBackupReceiptV2(_BackupReceipt):
    """GITHUB v2 lane: produced inside an authenticated p3-authority.yml run."""
    schema_version: Literal['p3-research-backup-receipt-v2']
    producer_repository: Literal['nam176hermes/Trading-Agent']
    producer_workflow_ref: Literal['nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main']
    producer_run_id: Annotated[int, Field(ge=1)]
    producer_attempt: Annotated[int, Field(ge=1)]


class P3LocalResearchBackupReceipt(_BackupReceipt):
    """LOCAL lane; carries LocalRunIdentity and never GitHub producer provenance."""
    schema_version: Literal['p3-local-research-backup-receipt-v1']
    identity: LocalRunIdentity


class _BatchCommitment(_ResearchReceipt):
    """No recursive raw inventory or predecessor edge; this is not admission."""
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


class P3ResearchBatchCommitment(_BatchCommitment):
    """GITHUB v1 lane; field set and canonical bytes are frozen."""
    schema_version: Literal['p3-research-batch-commitment-v1']
    producer_repository: Literal['nam176hermes/Trading-Agent']
    producer_workflow_ref: Literal['nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main']
    producer_run_id: Annotated[int, Field(ge=1)]
    producer_attempt: Annotated[int, Field(ge=1)]


class P3ResearchBatchCommitmentV2(_BatchCommitment):
    """GITHUB v2 lane: produced inside an authenticated p3-authority.yml run."""
    schema_version: Literal['p3-research-batch-commitment-v2']
    producer_repository: Literal['nam176hermes/Trading-Agent']
    producer_workflow_ref: Literal['nam176hermes/Trading-Agent/.github/workflows/p3-authority.yml@refs/heads/main']
    producer_run_id: Annotated[int, Field(ge=1)]
    producer_attempt: Annotated[int, Field(ge=1)]


class P3LocalResearchBatchCommitment(_BatchCommitment):
    """LOCAL lane; carries LocalRunIdentity and never GitHub producer provenance."""
    schema_version: Literal['p3-local-research-batch-commitment-v1']
    identity: LocalRunIdentity


_BACKUP_LANES={'p3-research-backup-receipt-v1':P3ResearchBackupReceipt,
    'p3-research-backup-receipt-v2':P3ResearchBackupReceiptV2,
    'p3-local-research-backup-receipt-v1':P3LocalResearchBackupReceipt}
_COMMITMENT_LANES={'p3-research-batch-commitment-v1':P3ResearchBatchCommitment,
    'p3-research-batch-commitment-v2':P3ResearchBatchCommitmentV2,
    'p3-local-research-batch-commitment-v1':P3LocalResearchBatchCommitment}
# Each GITHUB commitment schema consumes only the backup schema of the same producer workflow.
_GITHUB_BACKUP={P3ResearchBatchCommitment:P3ResearchBackupReceipt,
    P3ResearchBatchCommitmentV2:P3ResearchBackupReceiptV2}


def _parse_lane(raw: bytes, lanes: Mapping[str, type[_ResearchReceipt]]):
    """Dispatch on schema_version only; neither lane may absorb the other's shape."""
    try:
        declared=json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('research receipt is not canonical JSON') from error
    if type(declared) is not dict:
        raise ValueError('research receipt must be a JSON object')
    version=declared.get('schema_version')
    model=lanes.get(version) if isinstance(version,str) else None
    if model is None:
        raise ValueError('research receipt schema_version is not an accepted lane')
    return model.model_validate_json(raw)


def parse_research_backup_receipt(raw: bytes):
    """Resolve a backup receipt to its exact lane; structure only, never admission."""
    return _parse_lane(raw,_BACKUP_LANES)


def parse_research_batch_commitment(raw: bytes):
    """Resolve a batch commitment to its exact lane; structure only, never admission."""
    return _parse_lane(raw,_COMMITMENT_LANES)


def validate_research_batch_commitment(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, dataset_ref: ArtifactRefV1, dataset: DatasetEvidence,
    store: ArtifactStore) -> P3ResearchBatchCommitment | P3ResearchBatchCommitmentV2 | P3LocalResearchBatchCommitment:
    """Verify retained structure only; never infer authentic custody from hashes."""
    _reference(ref,65536)
    _reference(dataset_ref,2097152)
    reader=_ReadBudget(store)
    commitment=_read(reader,ref,TypeAdapter(Annotated[
        P3ResearchBatchCommitment | P3ResearchBatchCommitmentV2 | P3LocalResearchBatchCommitment,
        Field(discriminator='schema_version')]))
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
    backup=_read(reader,commitment.backup_receipt_ref,TypeAdapter(Annotated[
        P3ResearchBackupReceipt | P3ResearchBackupReceiptV2 | P3LocalResearchBackupReceipt,
        Field(discriminator='schema_version')]))
    if isinstance(commitment,P3LocalResearchBatchCommitment):
        if not isinstance(backup,P3LocalResearchBackupReceipt) or backup.identity!=commitment.identity:
            raise ValueError('research backup lane or LOCAL producer differs')
    elif (type(backup) is not _GITHUB_BACKUP[type(commitment)]
        or backup.producer_run_id!=commitment.producer_run_id
        or backup.producer_attempt!=commitment.producer_attempt):
        raise ValueError('research backup lane or GITHUB producer differs')
    if (backup.source!=commitment.source
        or backup.inventory_content_sha256!=commitment.inventory_content_sha256
        or backup.dataset_content_sha256!=commitment.dataset_content_sha256
        or backup.snapshot_content_sha256!=commitment.snapshot_content_sha256
        or backup.object_count<max(commitment.inventory_entry_count,len(known))
        or backup.total_bytes<sum(known.values())
        or not commitment.completed_at<=backup.verified_at<=commitment.issued_at):
        raise ValueError('research backup subject, producer or timing differs')
    return commitment


def validate_input_commitment(input_set_ref: ArtifactRefV1, *, store: ArtifactStore):
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
    if inputs.synthetic_generation_ref is not None:
        from packages.alpha_lifecycle.synthetic_data import read_synthetic_commitment_metadata
        if pit.vintage_class != 'SYNTHETIC_REHEARSAL' or pit.limitations != ('synthetic.rehearsal_only',):
            raise ValueError('synthetic InputSet purpose cannot admit market evidence')
        return read_synthetic_commitment_metadata(pit.revision_proof_ref,
            generation_ref=inputs.synthetic_generation_ref, dataset_ref=inputs.dataset_evidence_ref,
            source=inputs.source, policy_digest=inputs.policy_digest, store=reader)
    return validate_research_batch_commitment(pit.revision_proof_ref,source=inputs.source,
        policy_digest=inputs.policy_digest,dataset_ref=inputs.dataset_evidence_ref,dataset=dataset,store=reader)


def _reconstruct_and_backup(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, query: PITQueryV1, dataset_ref: ArtifactRefV1,
    store: LocalArtifactStore, backup: LocalArtifactStore):
    """Reconstruct, copy and read back every object; producer metadata is the caller's."""
    from packages.alpha_lifecycle.acquisition import _retain_json
    from packages.alpha_lifecycle.pit_evidence import validate_revision_inventory

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
    facts=dict(source=source,
        authority=dict(broker=False,live=False,network=False,production=False),
        inventory_content_sha256=ref.content_sha256,dataset_content_sha256=dataset_ref.content_sha256,
        snapshot_content_sha256=dataset.snapshot_ref.content_sha256,
        object_inventory_content_sha256=inventory_ref.content_sha256,
        object_inventory_size_bytes=inventory_ref.size_bytes,
        destination_namespace='p3.research.backup',object_version=inventory_ref.content_sha256,
        object_count=len(sizes),total_bytes=total_bytes,
        verified_at=datetime.now(UTC).isoformat().replace('+00:00','Z'),status='READBACK_VERIFIED')
    return facts,inventory_ref


def _seal(payload: Mapping[str, object], model):
    value=dict(payload,digest=hashlib.sha256(canonical_json_bytes(payload)).hexdigest())
    return model.model_validate_json(canonical_json_bytes(value))


def backup_revision_inventory(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, query: PITQueryV1, dataset_ref: ArtifactRefV1,
    store: LocalArtifactStore, backup: LocalArtifactStore,
    producer_run_id: int, producer_attempt: int,
    version: Literal[1, 2]=1) -> tuple[P3ResearchBackupReceipt | P3ResearchBackupReceiptV2, ArtifactRefV1]:
    """GITHUB lane; copy and read back before any receipt is emitted. v2 names p3-authority.yml."""
    if (type(producer_run_id) is not int or producer_run_id<1
        or type(producer_attempt) is not int or producer_attempt<1):
        raise ValueError('backup producer run and attempt must be positive integers')
    if version not in (1,2):
        raise ValueError('GITHUB backup receipt version is 1 or 2')
    facts,inventory_ref=_reconstruct_and_backup(ref,source=source,policy_digest=policy_digest,
        query=query,dataset_ref=dataset_ref,store=store,backup=backup)
    model=P3ResearchBackupReceiptV2 if version==2 else P3ResearchBackupReceipt
    payload=dict(schema_version=f'p3-research-backup-receipt-v{version}',
        producer_repository='nam176hermes/Trading-Agent',
        producer_workflow_ref=AUTHORITY_WORKFLOW_REF if version==2 else
            'nam176hermes/Trading-Agent/.github/workflows/p3-research-inputs.yml@refs/heads/main',
        producer_run_id=producer_run_id,producer_attempt=producer_attempt,**facts)
    return _seal(payload,model),inventory_ref


def backup_revision_inventory_local(ref: ArtifactRefV1, *, source: SourceIdentity,
    policy_digest: str, query: PITQueryV1, dataset_ref: ArtifactRefV1,
    store: LocalArtifactStore, backup: LocalArtifactStore,
    identity: LocalRunIdentity) -> tuple[P3LocalResearchBackupReceipt, ArtifactRefV1]:
    """LOCAL lane; identical reconstruction, copy and readback, distinct provenance."""
    if not isinstance(identity,LocalRunIdentity):
        identity=LocalRunIdentity.model_validate_json(canonical_json_bytes(identity))
    facts,inventory_ref=_reconstruct_and_backup(ref,source=source,policy_digest=policy_digest,
        query=query,dataset_ref=dataset_ref,store=store,backup=backup)
    payload=dict(schema_version='p3-local-research-backup-receipt-v1',
        identity=json.loads(identity.model_dump_json()),**facts)
    return _seal(payload,P3LocalResearchBackupReceipt),inventory_ref
