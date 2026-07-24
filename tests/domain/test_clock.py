from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.domain import FixedUtcClock, SystemUtcClock, require_utc


def test_require_utc_returns_the_same_utc_datetime() -> None:
    value = datetime(2026, 7, 20, 12, 30, tzinfo=UTC)

    assert require_utc(value) is value
    assert require_utc(value.replace(tzinfo=timezone(timedelta(0)))) is not None


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 20, 12, 30),
        datetime(2026, 7, 20, 12, 30, tzinfo=timezone(timedelta(hours=-4))),
    ],
)
def test_require_utc_rejects_naive_and_non_utc_datetimes(value: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        require_utc(value)


def test_system_utc_clock_returns_an_aware_utc_datetime() -> None:
    value = SystemUtcClock().now()

    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)


def test_fixed_utc_clock_validates_and_returns_its_value() -> None:
    value = datetime(2026, 7, 20, 12, 30, tzinfo=UTC)
    clock = FixedUtcClock(value)

    assert clock.now() is value
    with pytest.raises(FrozenInstanceError):
        clock.value = datetime(2026, 7, 20, 12, 31, tzinfo=UTC)  # type: ignore[misc]


def test_fixed_utc_clock_rejects_non_utc_value() -> None:
    with pytest.raises(ValueError, match="UTC"):
        FixedUtcClock(datetime(2026, 7, 20, 12, 30))
