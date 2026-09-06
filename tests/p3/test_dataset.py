from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest

from packages.alpha_lifecycle.contracts.data import DailyBar
from packages.alpha_lifecycle.data_view import DatasetSealError, to_daily_close, validate_dates


def _ref(digest: str) -> dict[str, object]:
    return {
        "content_sha256": digest,
        "size_bytes": 1,
        "media_type": "application/json",
        "locator": f"{digest}.blob",
    }


def _bar(day: date) -> DailyBar:
    opened = datetime(day.year, day.month, day.day, tzinfo=UTC)
    payload: dict[str, object] = {
        "schema_version": "p3-daily-bar-v1",
        "date": day.isoformat(),
        "instrument": "BTCUSDT.BINANCE",
        "opened_at": opened.isoformat().replace("+00:00", "Z"),
        "closed_at_exclusive": (opened + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "raw_close_time": int((opened + timedelta(days=1)).timestamp() * 1_000_000) - 1,
        "raw_timestamp_unit": "MICROSECONDS",
        "open": "100", "high": "110", "low": "90", "close": "105",
        "base_volume": "1", "quote_volume": "105", "trade_count": 1,
        "provider_published_at": None,
        "system_observed_at": "2026-09-05T12:00:00Z",
        "ingested_at": "2026-09-05T12:00:01Z",
        "partition_ref": _ref("a" * 64),
        "row_ordinal": 0,
    }
    payload["digest"] = hashlib.sha256(
        __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DailyBar.model_validate(payload)


def test_generic_dataset_minimum_and_actual_campaign_coverage() -> None:
    start = date(2018, 1, 1)
    assert len(validate_dates("GENERIC_RESEARCH", tuple(start + timedelta(days=i) for i in range(750)))) == 750
    with pytest.raises(DatasetSealError, match="750"):
        validate_dates("GENERIC_RESEARCH", tuple(start + timedelta(days=i) for i in range(749)))
    with pytest.raises(DatasetSealError, match="coverage"):
        validate_dates("RESEARCH", tuple(start + timedelta(days=i) for i in range(750)))


def test_daily_close_uses_end_exclusive_minus_one_microsecond() -> None:
    bar = _bar(date(2025, 1, 6))
    close = to_daily_close(bar)
    assert close.closed_at == bar.closed_at_exclusive - timedelta(microseconds=1)
    assert close.closed_at.date() == bar.date
