from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.alpha_lifecycle.candidates import run_candidate
from packages.alpha_lifecycle.contracts.data import DailyBar, DateRange, Fold
from packages.alpha_lifecycle.contracts.policy import CandidateSpec
from scripts.generate_p3_specs import _candidate_specs


def _ref(value: str = "a") -> dict[str, object]:
    digest = value * 64
    return {"content_sha256": digest, "size_bytes": 1, "media_type": "application/json", "locator": f"{digest}.blob"}


def _bar(day: date, close: Decimal, *, high: Decimal | None = None, low: Decimal | None = None) -> DailyBar:
    opened = datetime(day.year, day.month, day.day, tzinfo=UTC)
    high, low = high or close, low or close
    payload: dict[str, object] = {
        "schema_version": "p3-daily-bar-v1", "date": day.isoformat(),
        "instrument": "BTCUSDT.BINANCE", "opened_at": opened.isoformat().replace("+00:00", "Z"),
        "closed_at_exclusive": (opened + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "raw_close_time": int((opened + timedelta(days=1)).timestamp() * 1_000_000) - 1,
        "raw_timestamp_unit": "MICROSECONDS", "open": str(close), "high": str(high),
        "low": str(low), "close": str(close), "base_volume": "1", "quote_volume": "1",
        "trade_count": 1, "provider_published_at": None,
        "system_observed_at": "2026-09-05T00:00:00Z",
        "ingested_at": "2026-09-05T00:00:01Z",
        "partition_ref": _ref(), "row_ordinal": 0,
    }
    from packages.engine_contracts.serialization import canonical_json_bytes
    payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return DailyBar.model_validate(payload)


def _candidate(index: int) -> CandidateSpec:
    policy = json.loads(Path("docs/implementation/p3/specs/p3-policy-set-v21.json").read_bytes())
    return CandidateSpec.model_validate(_candidate_specs(policy)[index])


def test_donchian_excludes_current_bar_and_suppresses_terminal_signal() -> None:
    start = date(2025, 1, 1)
    bars = tuple(
        _bar(start + timedelta(days=i), Decimal("11") if i >= 20 else Decimal("10"))
        for i in range(22)
    )
    fold = Fold.model_construct(
        decision_start=start + timedelta(days=20),
        decision_end=start + timedelta(days=20),
        return_end_range=DateRange(start=start + timedelta(days=21), end=start + timedelta(days=21)),
        return_count=1,
    )
    assert run_candidate(_candidate(0), bars, fold) == (1, 1)


def test_zscore_zero_variance_forces_flat_even_from_long() -> None:
    start = date(2025, 1, 1)
    closes = [Decimal("100")] * 20 + [Decimal("90")] + [Decimal("90")] * 20
    bars = tuple(_bar(start + timedelta(days=i), value) for i, value in enumerate(closes))
    fold = Fold.model_construct(
        decision_start=start + timedelta(days=19),
        decision_end=start + timedelta(days=39),
        return_end_range=DateRange(start=start + timedelta(days=20), end=start + timedelta(days=40)),
        return_count=21,
    )
    weights = run_candidate(_candidate(2), bars, fold)
    assert 1 in weights
    assert weights[-2:] == (0, 0)


@pytest.mark.parametrize("candidate_index", range(4))
def test_all_frozen_perturbations_are_runnable_and_future_invariant(candidate_index: int) -> None:
    start = date(2024, 1, 1)
    bars = tuple(
        _bar(start + timedelta(days=i), Decimal(100 + (i % 17)))
        for i in range(260)
    )
    fold = Fold.model_construct(
        decision_start=start + timedelta(days=250),
        decision_end=start + timedelta(days=257),
        return_end_range=DateRange(start=start + timedelta(days=251), end=start + timedelta(days=258)),
        return_count=8,
    )
    spec = _candidate(candidate_index)
    for perturbation in spec.perturbations:
        expected = run_candidate(spec, bars[:259], fold, perturbation_id=perturbation.perturbation_id)
        assert run_candidate(spec, bars, fold, perturbation_id=perturbation.perturbation_id) == expected
    with pytest.raises(ValueError, match="outside"):
        run_candidate(spec, bars, fold, perturbation_id="p05")
