"""Pure UTC half-hour slot selection for the durable scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class Slot:
    at: datetime
    value: str

    @property
    def slot_at(self) -> datetime:
        return self.at

    @property
    def slot_key(self) -> str:
        return self.value

    @property
    def idempotency_key(self) -> str:
        return f"schedule:snapshot:{self.value}"


def slot_for_tick(now_utc: datetime) -> Slot | None:
    """Return only the current UTC half-hour slot for an injected aware tick."""

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("scheduler tick must be timezone-aware")
    tick = now_utc.astimezone(timezone.utc)
    if tick.minute not in (0, 30):
        return None
    slot_at = tick.replace(second=0, microsecond=0)
    return Slot(at=slot_at, value=slot_at.strftime("%Y-%m-%dT%H:%MZ"))
