from __future__ import annotations

import hashlib
import io
from datetime import UTC, date, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from packages.alpha_lifecycle.acquisition import AcquisitionError, acquire_day, parse_daily_archive
from packages.data_catalog.artifact_store import LocalArtifactStore, ArtifactIntegrityError


class FixtureTransport:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, *, timeout_seconds: int, max_bytes: int) -> bytes:
        self.urls.append(url)
        value = self.responses[url]
        assert timeout_seconds == 20
        assert len(value) <= max_bytes
        return value


def _archive(day: date, *, member: str | None = None, column: tuple[int,str] | None = None) -> tuple[str, bytes]:
    stem = f"BTCUSDT-1d-{day.isoformat()}"
    unit = 1_000_000 if day.year >= 2025 else 1_000
    opened = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * unit
    closed = opened + 86_400 * unit - 1
    columns=f"{opened},100,110,90,105,1,{closed},100,1,0,0,0".split(',')
    if column is not None:
        columns[column[0]]=column[1]
    row = (','.join(columns)+'\n').encode()
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(member or f"{stem}.csv", row)
    return f"{stem}.zip", output.getvalue()


def _store(path: Path) -> LocalArtifactStore:
    path.mkdir(mode=0o700)
    return LocalArtifactStore(path)


def test_acquire_day_validates_checksum_archive_and_timestamp_unit(tmp_path: Path) -> None:
    day = date(2025, 1, 2)
    filename, zipped = _archive(day)
    base = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"
    transport = FixtureTransport({
        base + filename: zipped,
        base + filename + ".CHECKSUM": f"{hashlib.sha256(zipped).hexdigest()}  {filename}\n".encode(),
    })

    store = _store(tmp_path / "artifacts")
    ref = acquire_day(day, transport, store)
    parsed = parse_daily_archive(day, store.read_bytes(ref))

    assert transport.urls == [base + filename + ".CHECKSUM", base + filename]
    assert parsed.raw_timestamp_unit == "MICROSECONDS"
    assert parsed.closed_at_exclusive.date() == date(2025, 1, 3)
    assert ref.media_type == "application/zip"


@pytest.mark.parametrize("corruption", ("checksum", "traversal"))
def test_acquire_day_quarantines_ambiguous_or_tampered_bytes(
    tmp_path: Path, corruption: str
) -> None:
    day = date(2024, 1, 2)
    filename, zipped = _archive(day, member="../escape.csv" if corruption == "traversal" else None)
    digest = "0" * 64 if corruption == "checksum" else hashlib.sha256(zipped).hexdigest()
    base = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"
    transport = FixtureTransport({
        base + filename: zipped,
        base + filename + ".CHECKSUM": f"{digest}  {filename}\n".encode(),
    })

    with pytest.raises(AcquisitionError):
        acquire_day(day, transport, _store(tmp_path / "artifacts"))


def test_acquisition_receipt_retains_and_revalidates_zip_and_checksum(tmp_path):
    from packages.alpha_lifecycle import acquisition
    from packages.engine_contracts.serialization import canonical_json_bytes
    day=date(2025,1,2)
    filename,zipped=_archive(day)
    base='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/'
    checksum=f'{hashlib.sha256(zipped).hexdigest()}  {filename}\n'.encode()
    store=_store(tmp_path/'artifacts')
    transport=FixtureTransport({base+filename:zipped,base+filename+'.CHECKSUM':checksum})
    before=datetime.now(UTC)
    receipt=acquisition.acquire_day_receipt(day,transport,store)
    after=datetime.now(UTC)
    assert before<=receipt.system_observed_at<=receipt.fetched_at<=after
    assert receipt.provider_published_at is None
    assert receipt.vintage_class=='RETROSPECTIVE_CURRENT_ARCHIVE'
    assert store.read_bytes(receipt.archive_ref)==zipped
    assert store.read_bytes(receipt.checksum_ref)==checksum
    ref=receipt.artifact_ref
    assert store.read_bytes(ref)==canonical_json_bytes(receipt)
    assert acquisition.validate_acquisition_receipt(ref,store)==receipt
    (store._root/receipt.checksum_ref.locator).unlink()
    with pytest.raises(ArtifactIntegrityError):
        acquisition.validate_acquisition_receipt(ref,store)


def test_acquisition_refuses_uncompleted_day_before_retaining_bytes(tmp_path):
    from datetime import timedelta
    from packages.alpha_lifecycle import acquisition
    day=datetime.now(UTC).date()+timedelta(days=2)
    filename,zipped=_archive(day)
    base='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/'
    checksum=f'{hashlib.sha256(zipped).hexdigest()}  {filename}\n'.encode()
    store=_store(tmp_path/'artifacts')
    with pytest.raises(AcquisitionError):
        acquisition.acquire_day_receipt(day,FixtureTransport({base+filename:zipped,base+filename+'.CHECKSUM':checksum}),store)
    assert list(store._root.iterdir())==[]


@pytest.mark.parametrize('fault',['day','archive_media','checksum_media','archive_size','checksum_size','observation','checksum_bytes','archive_bytes','noncanonical'])
def test_acquisition_consumer_rejects_substitution_without_writing(tmp_path,monkeypatch,fault):
    from packages.alpha_lifecycle import acquisition
    from packages.engine_contracts.serialization import canonical_json_bytes
    day=date(2025,1,2)
    filename,zipped=_archive(day)
    base='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/'
    checksum=f'{hashlib.sha256(zipped).hexdigest()}  {filename}\n'.encode()
    store=_store(tmp_path/'artifacts')
    receipt=acquisition.acquire_day_receipt(day,FixtureTransport({base+filename:zipped,base+filename+'.CHECKSUM':checksum}),store)
    value=receipt.model_dump(mode='json',exclude={'digest'})
    if fault=='day':
        value['day']='2025-01-03'
    elif fault.endswith('_media'):
        value[fault.removesuffix('_media')+'_ref']['media_type']='application/octet-stream'
    elif fault.endswith('_size'):
        value[fault.removesuffix('_size')+'_ref']['size_bytes']=2**40
    elif fault=='observation':
        value['system_observed_at']='2025-01-02T00:00:00Z'
    elif fault=='checksum_bytes':
        value['checksum_ref']=store.put_bytes(b'0  wrong.zip\n',media_type='text/plain').model_dump(mode='json')
    elif fault=='archive_bytes':
        value['archive_ref']=store.put_bytes(_archive(date(2025,1,3))[1],media_type='application/zip').model_dump(mode='json')
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    ref=store.put_bytes(canonical_json_bytes(value)+(b' ' if fault=='noncanonical' else b''),media_type='application/json')
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('consumer wrote an artifact')))
    with pytest.raises(ValueError):
        acquisition.validate_acquisition_receipt(ref,store)


def test_acquisition_consumer_rejects_wrong_locator_before_read():
    from packages.alpha_lifecycle.acquisition import validate_acquisition_receipt
    from packages.data_contracts import ArtifactRefV1
    class NoRead:
        def read_bytes(self,ref):
            raise AssertionError('invalid reference reached storage')
    ref=ArtifactRefV1(content_sha256='a'*64,size_bytes=100,
        media_type='application/json',locator='b'*64+'.blob')
    with pytest.raises(AcquisitionError):
        validate_acquisition_receipt(ref,NoRead())


@pytest.mark.parametrize('column',[(0,'9'*129),(0,'-1'),(6,'9223372036854775808')])
def test_archive_rejects_timestamp_representation_before_conversion(column):
    with pytest.raises(AcquisitionError):
        parse_daily_archive(date(2025,1,2),_archive(date(2025,1,2),column=column)[1])


@pytest.mark.parametrize('column', [None,(1,'100.0000'),(2,'99'),(7,'-1'),(9,'NaN'),(10,'1e3'),(11,'1'),(8,'1.5'),(7,'9'*129)])
def test_normalization_binds_retained_daily_values_without_writing(tmp_path,monkeypatch,column):
    from packages.alpha_lifecycle import acquisition
    day=date(2025,1,2)
    filename,zipped=_archive(day,column=column)
    base='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/'
    checksum=f'{hashlib.sha256(zipped).hexdigest()}  {filename}\n'.encode()
    store=_store(tmp_path/'artifacts')
    receipt=acquisition.acquire_day_receipt(day,FixtureTransport({base+filename:zipped,base+filename+'.CHECKSUM':checksum}),store)
    monkeypatch.setattr(store,'put_bytes',lambda *a,**k:(_ for _ in ()).throw(AssertionError('normalizer wrote an artifact')))
    if column is not None and column[0]!=1:
        with pytest.raises(AcquisitionError):
            acquisition.normalize_daily_acquisition(receipt.artifact_ref,store)
    else:
        result=acquisition.normalize_daily_acquisition(receipt.artifact_ref,store)
        assert result==dict(schema_version='p3-normalized-daily-row-v1',row=dict(
            ts_event='2025-01-03T00:00:00Z',date='2025-01-02',instrument='BTCUSDT.BINANCE',
            opened_at='2025-01-02T00:00:00Z',closed_at_exclusive='2025-01-03T00:00:00Z',
            raw_close_time=1735862399999999,raw_timestamp_unit='MICROSECONDS',
            open='100',high='110',low='90',close='105',base_volume='1',quote_volume='100',
            trade_count=1,provider_published_at=None))
