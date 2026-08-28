"""Exact semantic comparison for the accepted rollback and G1 runtimes."""

from __future__ import annotations

from copy import deepcopy

import pytest

from scripts import compare_nautilus_runtime_versions as comparison


def _event() -> dict[str, object]:
    return {
        "attempt_id": "attempt-a",
        "run_uuid": "run-a",
        "custody_timestamp": "2026-08-28T00:00:00Z",
        "staging_token": "stage-a",
        "side": "BUY",
        "error_class": "NONE",
        "events": [
            {"kind": "order", "price": "100", "quantity": "2"},
            {"kind": "fill", "price": "101", "quantity": "2"},
        ],
        "account": {
            "fees": "0.2",
            "cash": "1001.8",
            "balance": "1001.8",
            "position": "2",
            "realized_pnl": "0",
            "unrealized_pnl": "2",
            "count": 2,
        },
    }


def test_only_allowlisted_custody_fields_are_normalized() -> None:
    left = _event()
    right = deepcopy(left)
    right.update(
        {
            "attempt_id": "attempt-b",
            "run_uuid": "run-b",
            "custody_timestamp": "2026-08-29T00:00:00Z",
            "staging_token": "stage-b",
        }
    )
    assert comparison.semantic_digest(left) == comparison.semantic_digest(right)
    assert comparison.classify_semantic_drift(left, right, []) == "NONE"


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("side",), "SELL"),
        (("events", 0, "price"), "100.1"),
        (("events", 0, "quantity"), "1"),
        (("account", "fees"), "0.21"),
        (("account", "cash"), "1001.7"),
        (("account", "balance"), "1001.7"),
        (("account", "position"), "1"),
        (("account", "realized_pnl"), "1"),
        (("account", "unrealized_pnl"), "1"),
        (("account", "count"), 3),
        (("error_class",), "PANIC"),
    ),
)
def test_business_and_financial_mutations_are_unexplained_blockers(
    path: tuple[str | int, ...], value: object
) -> None:
    left = _event()
    right = deepcopy(left)
    target: object = right
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    assert comparison.classify_semantic_drift(
        left, right, []
    ) == "UNEXPLAINED_BLOCKER"


def test_event_order_is_not_normalized() -> None:
    left = _event()
    right = deepcopy(left)
    right["events"].reverse()  # type: ignore[union-attr]
    assert comparison.classify_semantic_drift(
        left, right, []
    ) == "UNEXPLAINED_BLOCKER"


def test_each_runtime_must_be_internally_deterministic() -> None:
    event = _event()
    assert comparison.require_deterministic_repeats([event, event, event]) == (
        comparison.semantic_digest(event)
    )
    changed = deepcopy(event)
    changed["account"]["fees"] = "0.3"  # type: ignore[index]
    with pytest.raises(comparison.RuntimeComparisonError, match="deterministic"):
        comparison.require_deterministic_repeats([event, changed, event])


def test_only_exact_approved_drift_pairs_are_accepted() -> None:
    left = _event()
    right = deepcopy(left)
    right["account"]["fees"] = "0.3"  # type: ignore[index]
    approval = {
        "rollback_semantic_sha256": comparison.semantic_digest(left),
        "candidate_semantic_sha256": comparison.semantic_digest(right),
        "classification": "EXPECTED_UPSTREAM_FIX",
    }
    assert comparison.classify_semantic_drift(left, right, [approval]) == (
        "EXPECTED_UPSTREAM_FIX"
    )
    approval["candidate_semantic_sha256"] = "f" * 64
    assert comparison.classify_semantic_drift(
        left, right, [approval]
    ) == "UNEXPLAINED_BLOCKER"
