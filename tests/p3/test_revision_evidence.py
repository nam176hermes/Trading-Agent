"""Synthetic standalone reconstruction; never protected C01 qualification."""
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from packages.alpha_lifecycle import pit_evidence
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.data_contracts import ArtifactRefV1, DatasetPartitionManifestV3, PITQueryV1, PITQueryMode
from packages.engine_contracts.serialization import canonical_json_bytes


SOURCE=SourceIdentity(commit_sha='1'*40,tree_sha='2'*40,closure_schema_version='fixture',
    closure_policy_sha256='3'*64,closure_sha256='4'*64)
POLICY='5'*64


@pytest.mark.parametrize('fault',['locator','media','size'])
def test_revision_inventory_rejects_invalid_top_reference_before_read(fault):
    class NoRead:
        def read_bytes(self,ref):
            raise AssertionError('unbounded reference reached storage')
    fields=dict(content_sha256='a'*64,size_bytes=1,media_type='application/json',locator='a'*64+'.blob')
    if fault=='locator':
        fields['locator']='b'*64+'.blob'
    elif fault=='media':
        fields['media_type']='text/plain'
    else:
        fields['size_bytes']=64*1024*1024+1
    ref=ArtifactRefV1.model_validate(fields)
    query=PITQueryV1(mode=PITQueryMode.SYSTEM_OBSERVED,
        valid_at=datetime(2025,9,1,tzinfo=UTC),cutoff=datetime(2026,9,11,tzinfo=UTC))
    with pytest.raises(ValueError):
        pit_evidence.validate_revision_inventory(ref,source=SOURCE,policy_digest=POLICY,
            query=query,dataset_ref=ArtifactRefV1(content_sha256='c'*64,size_bytes=1,
                media_type='application/json',locator='c'*64+'.blob'),store=NoRead())


@pytest.fixture(scope='module')
def complete_revision_inputs(tmp_path_factory):
    from packages.alpha_lifecycle import acquisition
    from packages.alpha_lifecycle.data_view import seal_research_dataset
    from packages.data_catalog.artifact_store import LocalArtifactStore
    from packages.data_catalog.v3 import materialize_arrow_partition_v3
    from tests.p3.test_acquisition import FixtureTransport, _archive
    root=tmp_path_factory.mktemp('standalone-revision-store')
    root.chmod(0o700)
    store=LocalArtifactStore(root)
    entries=[]
    for offset in range(2800):
        day=date(2018,1,1)+timedelta(days=offset)
        filename,zipped=_archive(day)
        base='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/'
        checksum=f'{hashlib.sha256(zipped).hexdigest()}  {filename}\n'.encode()
        acquired=acquisition.acquire_day_receipt(day,FixtureTransport({base+filename:zipped,base+filename+'.CHECKSUM':checksum}),store)
        normalized=store.put_bytes(canonical_json_bytes(acquisition.normalize_daily_acquisition(acquired.artifact_ref,store)),media_type='application/json')
        provider=acquisition.retain_normalization_receipt(acquired.artifact_ref,store)
        quality=acquisition.retain_daily_quality_receipt(acquired.artifact_ref,store)
        schema,table=acquisition.daily_arrow_table(acquired.artifact_ref,store)
        series=uuid5(NAMESPACE_URL,f'p3.research.daily/BTCUSDT.BINANCE/{day.isoformat()}')
        identity=canonical_json_bytes(dict(archive_sha256=acquired.archive_ref.content_sha256,
            checksum_sha256=acquired.checksum_ref.content_sha256,revision_ordinal=1)).decode()
        partition=materialize_arrow_partition_v3(table,schema=schema,store=store,
            partition_id=uuid5(series,identity),dataset='p3.research.daily',
            partition_key=('BTCUSDT.BINANCE',day.isoformat()),partition_spec_version='p3.utc-day.v1',
            source_available_at=acquired.system_observed_at,system_observed_at=acquired.system_observed_at,
            ingested_at=datetime.now(UTC),raw_evidence_sha256s=(acquired.archive_ref.content_sha256,acquired.checksum_ref.content_sha256),
            transform_receipt_sha256=provider.content_sha256,quality_receipt_sha256=quality.content_sha256,
            revision_series_id=series,revision_ordinal=1)
        entries.append(dict(day=day.isoformat(),acquisition_ref=acquired.artifact_ref,
            normalized_ref=normalized,provider_ref=provider,quality_ref=quality,partition=partition.manifest))
    query=PITQueryV1(mode=PITQueryMode.SYSTEM_OBSERVED,valid_at=datetime(2025,9,1,tzinfo=UTC),cutoff=datetime.now(UTC))
    dataset=seal_research_dataset(tuple(entry['partition'] for entry in entries),query,schema,store)
    dataset_ref=store.put_bytes(canonical_json_bytes(dataset),media_type='application/json')
    value=dict(schema_version='p3-research-revision-inventory-v1',source=SOURCE,policy_digest=POLICY,entries=entries)
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    ref=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    return store,ref,query,dataset_ref,dataset


def test_complete_revision_reconstruction_is_read_only(complete_revision_inputs,monkeypatch):
    store,ref,query,dataset_ref,dataset=complete_revision_inputs
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('reconstruction wrote an artifact')))
    assert pit_evidence.validate_revision_inventory(ref,source=SOURCE,policy_digest=POLICY,
        query=query,dataset_ref=dataset_ref,store=store)==dataset


@pytest.mark.parametrize('fault',[
    'source','policy','missing_day','duplicate','namespace','partition_key',
    'ordinal','normalized','provider','quality','nested_locator','parquet_size',
    'partition_id','revision_series_id','duplicate_raw_revision',
])
def test_revision_reconstruction_rejects_tampering_without_writes(
    complete_revision_inputs,monkeypatch,fault,
):
    store,ref,query,dataset_ref,_=complete_revision_inputs
    value=json.loads(store.read_bytes(ref))
    entry=value['entries'][0]
    if fault=='source':
        value['source']['commit_sha']='6'*40
    elif fault=='policy':
        value['policy_digest']='6'*64
    elif fault=='missing_day':
        value['entries'].pop()
    elif fault=='duplicate':
        value['entries'].insert(0,entry.copy())
    elif fault=='namespace':
        entry['partition']['dataset']='another.dataset'
    elif fault=='partition_key':
        entry['partition']['partition_key'][1]='2018-01-02'
    elif fault=='ordinal':
        entry['partition']['revision_ordinal']=2
    elif fault in ('normalized','provider','quality'):
        entry[fault+'_ref']=value['entries'][1][fault+'_ref']
        # Quality receipts may be identical for identical synthetic prices.
        if fault=='quality':
            entry['quality_ref']=entry['normalized_ref']
    elif fault=='nested_locator':
        entry['acquisition_ref']['locator']='6'*64+'.blob'
    elif fault=='parquet_size':
        entry['partition']['parquet_size_bytes']=1048577
    elif fault in ('partition_id','revision_series_id'):
        entry['partition'][fault]=str(uuid5(NAMESPACE_URL,'wrong-p3-identity'))
    else:
        second=json.loads(json.dumps(entry))
        prior=DatasetPartitionManifestV3.model_validate_json(canonical_json_bytes(entry['partition']))
        acquired=json.loads(store.read_bytes(ArtifactRefV1.model_validate(entry['acquisition_ref'])))
        identity=canonical_json_bytes(dict(archive_sha256=acquired['archive_ref']['content_sha256'],
            checksum_sha256=acquired['checksum_ref']['content_sha256'],revision_ordinal=2)).decode()
        second['partition'].update(revision_ordinal=2,
            partition_id=str(uuid5(UUID(entry['partition']['revision_series_id']),identity)),
            supersedes_partition_id=str(prior.partition_id),supersedes_manifest_sha256=prior.digest)
        value['entries'].insert(1,second)
    value.pop('digest')
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    altered_ref=store.put_bytes(canonical_json_bytes(value),media_type='application/json')
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('reconstruction wrote an artifact')))
    reason='identical current raw pair' if fault=='duplicate_raw_revision' else None
    with pytest.raises(ValueError,match=reason):
        pit_evidence.validate_revision_inventory(altered_ref,source=SOURCE,policy_digest=POLICY,
            query=query,dataset_ref=dataset_ref,store=store)


def test_revision_read_budget_counts_repeated_reads_and_rejects_before_io():
    class Store:
        reads=0
        def read_bytes(self,ref):
            self.reads+=1
            return b'x'
    store=Store()
    digest=hashlib.sha256(b'x').hexdigest()
    ref=ArtifactRefV1(content_sha256=digest,size_bytes=1,media_type='application/json',locator=digest+'.blob')
    reader=pit_evidence._ReadBudget(store)
    reader.remaining=2
    assert reader.read_bytes(ref)==reader.read_bytes(ref)==b'x'
    with pytest.raises(ValueError,match='read budget'):
        reader.read_bytes(ref)
    assert store.reads==2
