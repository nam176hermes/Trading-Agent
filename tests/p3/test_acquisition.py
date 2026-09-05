from __future__ import annotations

import hashlib
import io
from datetime import UTC, date, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from packages.alpha_lifecycle.acquisition import AcquisitionError, acquire_day, parse_daily_archive
from packages.data_catalog.artifact_store import LocalArtifactStore


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


def _archive(day: date, *, member: str | None = None) -> tuple[str, bytes]:
    stem = f"BTCUSDT-1d-{day.isoformat()}"
    unit = 1_000_000 if day.year >= 2025 else 1_000
    opened = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * unit
    closed = opened + 86_400 * unit - 1
    row = f"{opened},100,110,90,105,1,{closed},100,1,0,0,0\n".encode()
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
