"""Exact semantic comparison for the accepted rollback and G1 runtimes."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import compare_nautilus_runtime_versions as comparison


ROOT = Path(__file__).resolve().parents[2]
RECEIPT = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/"
    "u07-dual-runtime-qualification-receipt.json"
)


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


def test_committed_u07_receipt_is_closed_generation_bound_and_inactive() -> None:
    raw = RECEIPT.read_bytes()
    receipt = json.loads(raw)
    assert raw == comparison._pretty(receipt)
    assert receipt["schema"] == (
        "trading-agent-nautilus-u07-dual-runtime-qualification/v1"
    )
    assert receipt["verdict"] == "PASS"
    assert receipt["qualification_source_commit"] == (
        "fb07bce9aede8c68dfc5b11aa12023fbde3b0918"
    )
    assert receipt["qualification_source_tree"] == (
        "2a8d08d5ef0adec1113d4b9c2f2a17c81f0ddfbd"
    )
    assert receipt["candidate_generation_id"] == "NT1231-U04-G1"
    assert receipt["candidate_generation_sha256"] == (
        "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"
    )
    assert receipt["candidate_closure_sha256"] == (
        "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
    )
    assert set(receipt["authority_limits"].values()) == {False}
    evidence = receipt["evidence"]
    assert receipt["evidence_sha256"] == hashlib.sha256(
        comparison._canonical(evidence)
    ).hexdigest()
    assert evidence["attempts_per_runtime"] == 3
    assert evidence["isolated_process_count"] == 48
    assert evidence["internal_semantic_digest_counts"] == {
        "candidate_1_231": 1,
        "rollback_1_227": 1,
    }
    assert evidence["drift_counts"] == {
        "APPROVED_CONTRACT_CHANGE": 0,
        "EXPECTED_UPSTREAM_FIX": 0,
        "NONE": 8,
        "UNEXPLAINED_BLOCKER": 0,
    }
    assert evidence["isolation"]["shared_writable_state"] == 0
    assert evidence["isolation"]["candidate_snapshot_before"] == (
        evidence["isolation"]["candidate_snapshot_after"]
    )
    assert evidence["isolation"]["rollback_snapshot_before"] == (
        evidence["isolation"]["rollback_snapshot_after"]
    )


def test_u07_runner_reuses_existing_scenarios_without_build_paths() -> None:
    source = Path(comparison.__file__).read_text(encoding="utf-8")
    assert "SCENARIO_IDS" in source
    assert "_run_scenario" in source
    assert all(
        term not in source
        for term in (
            "build_nautilus_engine",
            "materialize_candidate_runtime_closure",
            "--materialize-candidate",
        )
    )
