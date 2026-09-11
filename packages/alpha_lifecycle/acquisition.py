"""Bounded acquisition of one Binance public daily archive."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import stat
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Literal, Protocol
from zipfile import BadZipFile, ZipFile

from pydantic import model_validator
from packages.alpha_lifecycle.contracts.base import DigestModel, Sha256
from packages.alpha_lifecycle.contracts.data import Day
from packages.engine_contracts.serialization import CanonicalUtcDateTime, canonical_json_bytes
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1


_BASE = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"
_ZIP_MAX = 1_048_576
_MEMBER_MAX = 8_388_608
_CHECKSUM_MAX = 4_096
_RATIO_MAX = 100


class AcquisitionError(ValueError):
    """Provider bytes violate the accepted acquisition policy."""


class RetryableTransportError(OSError):
    """A bounded timeout, 429, or 5xx may be retried."""


class ApprovedHttpTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: int, max_bytes: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class AcquiredDailyRow:
    opened_at: datetime
    closed_at_exclusive: datetime
    raw_open_time: int
    raw_close_time: int
    raw_timestamp_unit: Literal["MILLISECONDS", "MICROSECONDS"]
    columns: tuple[str, ...]


def _integer(raw: str) -> int:
    if len(raw)>128 or re.fullmatch(r'[0-9]+',raw,re.ASCII) is None:
        raise AcquisitionError('daily integer is outside the representation bound')
    value=int(raw)
    if value>2**63-1:
        raise AcquisitionError('daily integer exceeds signed int64')
    return value


def _get(transport: ApprovedHttpTransport, url: str, max_bytes: int) -> bytes:
    for attempt, delay in enumerate((1, 2, 4), start=1):
        try:
            value = transport.get(url, timeout_seconds=20, max_bytes=max_bytes)
            if not isinstance(value, bytes) or len(value) > max_bytes:
                raise AcquisitionError("transport returned invalid or oversized bytes")
            return value
        except RetryableTransportError:
            if attempt == 3:
                raise AcquisitionError("bounded acquisition retries exhausted") from None
            time.sleep(delay)
    raise AssertionError("unreachable")


def _member(day: date, zipped: bytes) -> tuple[str, bytes]:
    expected = f"BTCUSDT-1d-{day.isoformat()}.csv"
    try:
        with ZipFile(io.BytesIO(zipped)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise AcquisitionError("archive must contain exactly one member")
            info = members[0]
            path = PurePosixPath(info.filename)
            unix_mode = info.external_attr >> 16
            if (
                info.filename != expected
                or path.is_absolute()
                or ".." in path.parts
                or info.is_dir()
                or stat.S_IFMT(unix_mode) not in (0, stat.S_IFREG)
                or info.file_size > _MEMBER_MAX
                or info.flag_bits & 1
                or info.file_size > max(1, info.compress_size) * _RATIO_MAX
            ):
                raise AcquisitionError("archive member is unsafe or outside policy")
            value = archive.read(info)
    except (BadZipFile, RuntimeError) as error:
        raise AcquisitionError("archive is invalid") from error
    if len(value) != info.file_size:
        raise AcquisitionError("archive member size changed while reading")
    return expected, value


def parse_daily_archive(day: date, zipped: bytes) -> AcquiredDailyRow:
    _, raw = _member(day, zipped)
    try:
        rows = tuple(csv.reader(io.StringIO(raw.decode("ascii"), newline="")))
    except (UnicodeDecodeError, csv.Error) as error:
        raise AcquisitionError("daily CSV is invalid") from error
    if len(rows) != 1 or len(rows[0]) != 12:
        raise AcquisitionError("daily CSV must contain one 12-column row")
    opened_raw, closed_raw = _integer(rows[0][0]), _integer(rows[0][6])
    unit: Literal["MILLISECONDS", "MICROSECONDS"] = (
        "MICROSECONDS" if day.year >= 2025 else "MILLISECONDS"
    )
    scale = 1_000_000 if unit == "MICROSECONDS" else 1_000
    expected_open = datetime(day.year, day.month, day.day, tzinfo=UTC)
    expected_raw=(expected_open-datetime(1970,1,1,tzinfo=UTC)).days*86400*scale
    if opened_raw!=expected_raw or closed_raw!=expected_raw+86400*scale-1:
        raise AcquisitionError("daily timestamps do not match the requested UTC day")
    try:
        closed_exclusive=expected_open+timedelta(days=1)
    except OverflowError as error:
        raise AcquisitionError('daily closing boundary is out of range') from error
    return AcquiredDailyRow(
        expected_open, closed_exclusive, opened_raw, closed_raw, unit, tuple(rows[0])
    )


class DailyAcquisitionReceipt(DigestModel):
    """Private retained-input evidence; producer custody authenticates observation."""
    schema_version: Literal['p3-daily-acquisition-v1']
    digest: Sha256
    day: Day
    archive_ref: ArtifactRefV1
    checksum_ref: ArtifactRefV1
    system_observed_at: CanonicalUtcDateTime
    fetched_at: CanonicalUtcDateTime
    provider_published_at: None
    vintage_class: Literal['RETROSPECTIVE_CURRENT_ARCHIVE']

    @property
    def artifact_ref(self) -> ArtifactRefV1:
        raw = canonical_json_bytes(self)
        digest = hashlib.sha256(raw).hexdigest()
        return ArtifactRefV1(content_sha256=digest, size_bytes=len(raw),
            media_type='application/json', locator=f'{digest}.blob')

    @model_validator(mode='after')
    def _scope(self):
        if (self.archive_ref.media_type!='application/zip' or not 0<self.archive_ref.size_bytes<=_ZIP_MAX
            or self.checksum_ref.media_type!='text/plain' or not 0<self.checksum_ref.size_bytes<=_CHECKSUM_MAX
            or self.system_observed_at>self.fetched_at
            or self.system_observed_at<datetime(self.day.year,self.day.month,self.day.day,tzinfo=UTC)+timedelta(days=1)):
            raise ValueError('acquisition references or observation times differ')
        return self


def _checked_archive(day: date, checksum: bytes, zipped: bytes) -> AcquiredDailyRow:
    filename=f"BTCUSDT-1d-{day.isoformat()}.zip"
    if len(checksum)>_CHECKSUM_MAX or len(zipped)>_ZIP_MAX:
        raise AcquisitionError('archive or checksum exceeds the accepted bound')
    match=re.fullmatch(rb"([0-9a-f]{64})  ([A-Za-z0-9._-]+)\n?",checksum)
    if not match or match.group(2).decode()!=filename:
        raise AcquisitionError('checksum filename or format is invalid')
    if hashlib.sha256(zipped).hexdigest()!=match.group(1).decode():
        raise AcquisitionError('archive checksum is invalid')
    return parse_daily_archive(day,zipped)


def validate_acquisition_receipt(ref: ArtifactRefV1, store) -> DailyAcquisitionReceipt:
    ref=ArtifactRefV1.model_validate(ref)
    if (ref.media_type!='application/json' or not 0<ref.size_bytes<=65536
        or ref.locator!=ref.content_sha256+'.blob'):
        raise AcquisitionError('acquisition receipt reference exceeds its bound')
    raw=store.read_bytes(ref)
    receipt=DailyAcquisitionReceipt.model_validate_json(raw)
    if canonical_json_bytes(receipt)!=raw:
        raise AcquisitionError('acquisition receipt is not canonical')
    _checked_archive(receipt.day,store.read_bytes(receipt.checksum_ref),store.read_bytes(receipt.archive_ref))
    return receipt


def acquire_day_receipt(day: date, transport: ApprovedHttpTransport, store) -> DailyAcquisitionReceipt:
    if type(day) is not date:
        raise AcquisitionError('day must be a date')
    filename=f"BTCUSDT-1d-{day.isoformat()}.zip"
    checksum=_get(transport,_BASE+filename+'.CHECKSUM',_CHECKSUM_MAX)
    zipped=_get(transport,_BASE+filename,_ZIP_MAX)
    observed=datetime.now(UTC)
    parsed=_checked_archive(day,checksum,zipped)
    if parsed.closed_at_exclusive>observed:
        raise AcquisitionError('daily archive is not yet complete at observation')
    value=dict(schema_version='p3-daily-acquisition-v1',day=day.isoformat(),
        archive_ref=store.put_bytes(zipped,media_type='application/zip'),
        checksum_ref=store.put_bytes(checksum,media_type='text/plain'),
        system_observed_at=observed.isoformat().replace('+00:00','Z'),
        fetched_at=datetime.now(UTC).isoformat().replace('+00:00','Z'),provider_published_at=None,
        vintage_class='RETROSPECTIVE_CURRENT_ARCHIVE')
    value['digest']=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    receipt=DailyAcquisitionReceipt.model_validate_json(canonical_json_bytes(value))
    ref=store.put_bytes(canonical_json_bytes(receipt),media_type='application/json')
    return validate_acquisition_receipt(ref,store)


def acquire_day(day: date, transport: ApprovedHttpTransport, store: LocalArtifactStore) -> ArtifactRefV1:
    return acquire_day_receipt(day,transport,store).archive_ref


def _decimal_number(raw: str) -> str:
    if len(raw)>128 or re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',raw,re.ASCII) is None:
        raise AcquisitionError('daily decimal is outside the representation bound')
    text=format(Decimal(raw),'f')
    if '.' in text:
        text=text.rstrip('0').rstrip('.')
    return '0' if Decimal(text)==0 else text


def normalize_daily_acquisition(ref: ArtifactRefV1, store) -> dict[str, object]:
    """Revalidate retained raw input and construct the fixed noncyclic row document."""
    from packages.data_quality import DataQualityError, validate_bar_rows
    receipt=validate_acquisition_receipt(ref,store)
    parsed=parse_daily_archive(receipt.day,store.read_bytes(receipt.archive_ref))
    columns=parsed.columns
    numbers={index:_decimal_number(columns[index]) for index in (1,2,3,4,5,7,9,10)}
    trades=_integer(columns[8])
    if columns[11]!='0':
        raise AcquisitionError('daily ignored field differs from the fixed format')
    try:
        validate_bar_rows((dict(ts_event=parsed.closed_at_exclusive,open=numbers[1],
            high=numbers[2],low=numbers[3],close=numbers[4],volume=numbers[5]),),
            dataset='p3.research.daily')
    except DataQualityError as error:
        raise AcquisitionError('daily OHLC or volume quality failed') from error
    closed=parsed.closed_at_exclusive.isoformat().replace('+00:00','Z')
    return dict(schema_version='p3-normalized-daily-row-v1',row=dict(
        ts_event=closed,date=receipt.day.isoformat(),instrument='BTCUSDT.BINANCE',
        opened_at=parsed.opened_at.isoformat().replace('+00:00','Z'),
        closed_at_exclusive=closed,raw_close_time=parsed.raw_close_time,
        raw_timestamp_unit=parsed.raw_timestamp_unit,open=numbers[1],high=numbers[2],
        low=numbers[3],close=numbers[4],base_volume=numbers[5],quote_volume=numbers[7],
        trade_count=trades,provider_published_at=None))


def retain_normalization_receipt(ref: ArtifactRefV1, store) -> ArtifactRefV1:
    """Retain the existing P2 provider identity after complete raw validation."""
    from uuid import NAMESPACE_URL, uuid5
    from packages.data_contracts import ProviderCapabilityV1, ProviderReceiptV1, RawEvidenceArtifactV1
    acquired=validate_acquisition_receipt(ref,store)
    normalized=normalize_daily_acquisition(ref,store)
    archive_url=_BASE+f'BTCUSDT-1d-{acquired.day.isoformat()}.zip'
    checksum_url=archive_url+'.CHECKSUM'
    query=dict(schema_version='p3-daily-acquisition-query-v1',provider='binance.public-archive',
        day=acquired.day.isoformat(),instrument='BTCUSDT.BINANCE',interval='1d',
        archive_url=archive_url,checksum_url=checksum_url)
    evidence=tuple(RawEvidenceArtifactV1(
        evidence_id=uuid5(NAMESPACE_URL,url+'#sha256='+raw.content_sha256),
        provider='binance.public-archive',media_type=raw.media_type,byte_length=raw.size_bytes,
        content_sha256=raw.content_sha256,source_available_at=acquired.system_observed_at,
        system_observed_at=acquired.system_observed_at,fetched_at=acquired.fetched_at)
        for url,raw in ((archive_url,acquired.archive_ref),(checksum_url,acquired.checksum_ref)))
    document=canonical_json_bytes(normalized)
    receipt=ProviderReceiptV1(provider='binance.public-archive',
        capability=ProviderCapabilityV1.MARKET_BARS,query_sha256=hashlib.sha256(canonical_json_bytes(query)).hexdigest(),
        evidence=evidence,normalization_version='p3.binance.12-column.daily.v1',
        output_sha256s=(hashlib.sha256(document).hexdigest(),))
    _retain_json(document,store)
    return _retain_json(canonical_json_bytes(receipt),store)


def _retain_json(raw: bytes, store) -> ArtifactRefV1:
    digest=hashlib.sha256(raw).hexdigest()
    expected=ArtifactRefV1(content_sha256=digest,size_bytes=len(raw),
        media_type='application/json',locator=digest+'.blob')
    if store.put_bytes(raw,media_type='application/json')!=expected or store.read_bytes(expected)!=raw:
        raise AcquisitionError('retained JSON artifact identity or bytes differ')
    return expected


def daily_arrow_table(ref: ArtifactRefV1, store):
    """Adapt the fixed daily representation to the existing P2 materializer."""
    import pyarrow as pa
    from packages.data_contracts import ArrowFieldV1, ArrowSchemaV1
    normalized=normalize_daily_acquisition(ref,store)
    row=normalized['row']
    assert isinstance(row,dict)
    fields=(('ts_event','timestamp[ns,UTC]'),('date','string'),('instrument','string'),
        ('opened_at','timestamp[ns,UTC]'),('closed_at_exclusive','timestamp[ns,UTC]'),
        ('raw_close_time','int64'),('raw_timestamp_unit','string'),('open','string'),
        ('high','string'),('low','string'),('close','string'),('base_volume','string'),
        ('quote_volume','string'),('trade_count','int64'),('provider_published_at','timestamp[ns,UTC]'))
    schema=ArrowSchemaV1(schema_id='p3.binance.daily.v1',data_api_epoch=2,
        fields=tuple(ArrowFieldV1(field_id=index,name=name,data_type=kind,
            nullable=name=='provider_published_at') for index,(name,kind) in enumerate(fields,1)))
    types={'timestamp[ns,UTC]':pa.timestamp('ns',tz='UTC'),'string':pa.string(),'int64':pa.int64()}
    arrow_schema=pa.schema([pa.field(field.name,types[field.data_type],nullable=field.nullable)
        for field in schema.fields])
    values=dict(row)
    for field in schema.fields:
        if field.data_type=='timestamp[ns,UTC]' and values[field.name] is not None:
            values[field.name]=datetime.fromisoformat(values[field.name].replace('Z','+00:00'))
    return schema,pa.Table.from_pylist([values],schema=arrow_schema)


def retain_daily_quality_receipt(ref: ArtifactRefV1, store) -> ArtifactRefV1:
    """Retain exactly the existing P2 quality receipt's four-field digest input."""
    from dataclasses import asdict
    from packages.data_quality import validate_bar_rows
    row=normalize_daily_acquisition(ref,store)['row']
    assert isinstance(row,dict)
    receipt=validate_bar_rows((dict(ts_event=datetime.fromisoformat(row['ts_event'].replace('Z','+00:00')),
        open=row['open'],high=row['high'],low=row['low'],close=row['close'],volume=row['base_volume']),),
        dataset='p3.research.daily')
    raw=canonical_json_bytes(asdict(receipt))
    return _retain_json(raw,store)


__all__ = [
    "AcquiredDailyRow", "AcquisitionError", "ApprovedHttpTransport", "DailyAcquisitionReceipt",
    "RetryableTransportError", "acquire_day", "acquire_day_receipt", "parse_daily_archive", "validate_acquisition_receipt",
    "normalize_daily_acquisition",
    "retain_normalization_receipt",
    "daily_arrow_table",
    "retain_daily_quality_receipt",
]
