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
from pathlib import PurePosixPath
from typing import Literal, Protocol
from zipfile import BadZipFile, ZipFile

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
    try:
        opened_raw, closed_raw = int(rows[0][0]), int(rows[0][6])
    except ValueError as error:
        raise AcquisitionError("daily timestamps must be integers") from error
    unit: Literal["MILLISECONDS", "MICROSECONDS"] = (
        "MICROSECONDS" if day.year >= 2025 else "MILLISECONDS"
    )
    scale = 1_000_000 if unit == "MICROSECONDS" else 1_000
    opened = datetime.fromtimestamp(opened_raw / scale, tz=UTC)
    closed_exclusive = datetime.fromtimestamp((closed_raw + 1) / scale, tz=UTC)
    expected_open = datetime(day.year, day.month, day.day, tzinfo=UTC)
    if opened != expected_open or closed_exclusive != expected_open + timedelta(days=1):
        raise AcquisitionError("daily timestamps do not match the requested UTC day")
    return AcquiredDailyRow(
        opened, closed_exclusive, opened_raw, closed_raw, unit, tuple(rows[0])
    )


def acquire_day(
    day: date, transport: ApprovedHttpTransport, store: LocalArtifactStore
) -> ArtifactRefV1:
    if not isinstance(day, date):
        raise AcquisitionError("day must be a date")
    filename = f"BTCUSDT-1d-{day.isoformat()}.zip"
    checksum = _get(transport, _BASE + filename + ".CHECKSUM", _CHECKSUM_MAX)
    zipped = _get(transport, _BASE + filename, _ZIP_MAX)
    match = re.fullmatch(rb"([0-9a-f]{64})  ([A-Za-z0-9._-]+)\n?", checksum)
    if not match or match.group(2).decode() != filename:
        raise AcquisitionError("checksum filename or format is invalid")
    if hashlib.sha256(zipped).hexdigest() != match.group(1).decode():
        raise AcquisitionError("archive checksum is invalid")
    parse_daily_archive(day, zipped)
    return store.put_bytes(zipped, media_type="application/zip")


__all__ = [
    "AcquiredDailyRow", "AcquisitionError", "ApprovedHttpTransport",
    "RetryableTransportError", "acquire_day", "parse_daily_archive",
]
