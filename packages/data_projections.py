"""Deterministic source projection for Qlib's CSV-to-bin ingestion boundary."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re
from typing import Mapping

from packages.engine_contracts.serialization import canonical_json_bytes


class ProjectionError(ValueError):
    """Canonical rows cannot be represented by the target projection."""


@dataclass(frozen=True, slots=True)
class QlibCsvProjectionV1:
    calendar: bytes
    instruments: bytes
    features: bytes
    manifest_sha256: str


_INSTRUMENT = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,63}$", re.ASCII)
_FIELDS = ("instrument", "date", "$open", "$high", "$low", "$close", "$volume")


def _qlib_time(timestamp: datetime) -> str:
    utc = timestamp.astimezone(UTC)
    if not any((utc.hour, utc.minute, utc.second, utc.microsecond)):
        return utc.date().isoformat()
    return utc.isoformat().replace("+00:00", "Z")


def _qlib_decimal(row: Mapping[str, object], field: str) -> str:
    try:
        value = Decimal(str(row[field]))
    except (KeyError, InvalidOperation, ValueError):
        raise ProjectionError(f"Qlib {field} must be a finite decimal") from None
    if not value.is_finite():
        raise ProjectionError(f"Qlib {field} must be a finite decimal")
    return format(value, "f")


def project_qlib_csv(rows: tuple[Mapping[str, object], ...]) -> QlibCsvProjectionV1:
    if not rows:
        raise ProjectionError("Qlib projection requires rows")
    projected: list[tuple[str, str, str, str, str, str, str]] = []
    for row in rows:
        instrument = row.get("instrument")
        timestamp = row.get("ts_event")
        if (
            not isinstance(instrument, str)
            or _INSTRUMENT.fullmatch(instrument) is None
            or not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
        ):
            raise ProjectionError("Qlib row has an invalid instrument or timestamp")
        projected.append(
            (
                instrument,
                _qlib_time(timestamp),
                _qlib_decimal(row, "open"),
                _qlib_decimal(row, "high"),
                _qlib_decimal(row, "low"),
                _qlib_decimal(row, "close"),
                _qlib_decimal(row, "volume"),
            )
        )
    projected.sort(key=lambda value: (value[1], value[0]))
    if len({(value[0], value[1]) for value in projected}) != len(projected):
        raise ProjectionError("Qlib projection contains duplicate instrument dates")

    calendars = sorted({value[1] for value in projected})
    ranges: dict[str, tuple[str, str]] = {}
    for instrument in sorted({value[0] for value in projected}):
        dates = [value[1] for value in projected if value[0] == instrument]
        ranges[instrument] = (min(dates), max(dates))
    calendar = "".join(f"{value}\n" for value in calendars).encode()
    instruments = "".join(
        f"{instrument}\t{bounds[0]}\t{bounds[1]}\n"
        for instrument, bounds in ranges.items()
    ).encode()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(_FIELDS)
    writer.writerows(projected)
    features = output.getvalue().encode()
    manifest_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "calendar_sha256": hashlib.sha256(calendar).hexdigest(),
                "features_sha256": hashlib.sha256(features).hexdigest(),
                "instruments_sha256": hashlib.sha256(instruments).hexdigest(),
                "schema_version": "qlib-csv-projection-v1",
            }
        )
    ).hexdigest()
    return QlibCsvProjectionV1(calendar, instruments, features, manifest_sha256)


__all__ = ["ProjectionError", "QlibCsvProjectionV1", "project_qlib_csv"]
