"""UTC-only clocks for deterministic replay-safe domain code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def require_utc(value: datetime) -> datetime:
    """Return *value* only when it is timezone-aware and has a UTC offset."""
    if not isinstance(value, datetime):
        raise ValueError("value must be a UTC datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("value must be a timezone-aware UTC datetime")
    return value


@dataclass(frozen=True, slots=True)
class SystemUtcClock:
    """Clock backed by the system's current UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FixedUtcClock:
    """Clock that always returns one validated UTC instant."""

    value: datetime

    def __post_init__(self) -> None:
        require_utc(self.value)

    def now(self) -> datetime:
        return self.value
