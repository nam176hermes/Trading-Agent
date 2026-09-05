"""Exposure-aware P3 research epoch rules."""

from __future__ import annotations

import hashlib
from typing import Literal

from packages.alpha_lifecycle.contracts.data import DateRange
from packages.engine_contracts.serialization import canonical_json_bytes


PeriodClass = Literal["UNSEEN", "DEVELOPMENT", "EXPOSED"]


def classify_period(
    proposed: DateRange,
    *,
    exposed_ranges: tuple[DateRange, ...],
    result_guided: bool,
) -> PeriodClass:
    overlaps = any(
        proposed.start <= exposed.end and exposed.start <= proposed.end
        for exposed in exposed_ranges
    )
    if not overlaps:
        return "UNSEEN"
    return "DEVELOPMENT" if result_guided else "EXPOSED"


def logical_trial_id(epoch_id: str, alpha_id: str, scenario: str) -> str:
    """Return an attempt-independent logical trial token."""
    digest = hashlib.sha256(
        canonical_json_bytes([epoch_id, alpha_id, scenario])
    ).hexdigest()
    return f"trial.{digest}"


__all__ = ["PeriodClass", "classify_period", "logical_trial_id"]
