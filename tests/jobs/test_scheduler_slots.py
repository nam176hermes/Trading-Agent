from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.job_scheduler.slots import slot_for_tick


@pytest.mark.parametrize(
    ("minute", "expected"),
    ((0, "2026-07-12T12:00Z"), (30, "2026-07-12T12:30Z")),
)
def test_half_hour_utc_ticks_produce_exact_snapshot_slot_keys(
    minute: int, expected: str
) -> None:
    slot = slot_for_tick(
        datetime(2026, 7, 12, 12, minute, 47, 123456, tzinfo=timezone.utc)
    )

    assert slot is not None
    assert slot.value == expected
    assert slot.at == datetime(2026, 7, 12, 12, minute, tzinfo=timezone.utc)
    assert slot.idempotency_key == f"schedule:snapshot:{expected}"


@pytest.mark.parametrize("minute", (1, 29, 31))
def test_non_slot_minutes_are_skipped_without_rounding_or_catch_up(minute: int) -> None:
    assert (
        slot_for_tick(datetime(2026, 7, 12, 12, minute, tzinfo=timezone.utc))
        is None
    )


def test_aware_host_time_is_normalized_to_utc_before_slot_selection() -> None:
    eastern = timezone(timedelta(hours=-4))

    slot = slot_for_tick(datetime(2026, 7, 12, 8, 30, tzinfo=eastern))

    assert slot is not None
    assert slot.value == "2026-07-12T12:30Z"


def test_naive_tick_is_rejected_instead_of_assuming_host_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        slot_for_tick(datetime(2026, 7, 12, 12, 0))
