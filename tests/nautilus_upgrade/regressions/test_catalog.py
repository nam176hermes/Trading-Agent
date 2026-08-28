"""Closed release catalog and exact U06 outcome validation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import qualify_nautilus_v1231_regressions as qualification


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json"
)
CATALOG = Path(__file__).with_name("v1.228-v1.231.json")
RECEIPT = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/"
    "u06-regression-qualification-receipt.json"
)
MATRIX = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/release-regression-matrix.json"
)


def _documents() -> tuple[dict[str, object], dict[str, object]]:
    contract = json.loads(CONTRACT.read_bytes())
    catalog = json.loads(CATALOG.read_bytes())
    assert isinstance(contract, dict) and isinstance(catalog, dict)
    return contract, catalog


def test_catalog_classifies_every_release_item_without_skip_or_tolerance() -> None:
    contract, catalog = _documents()
    qualification.validate_catalog(catalog, contract)

    release_ids = [item["id"] for item in contract["release_delta"]]
    items = catalog["items"]
    assert len(release_ids) == len(items) == 40
    assert [item["release_id"] for item in items] == release_ids
    serialized = json.dumps(catalog, sort_keys=True).lower()
    assert all(term not in serialized for term in ("skip", "xfail", "tolerance"))


@pytest.mark.parametrize(
    "mutation",
    ("duplicate", "missing", "unknown-scenario", "empty-proof", "wrong-generation"),
)
def test_catalog_mutations_fail_closed(mutation: str) -> None:
    contract, catalog = _documents()
    mutated = deepcopy(catalog)
    items = mutated["items"]
    if mutation == "duplicate":
        items[1]["release_id"] = items[0]["release_id"]
    elif mutation == "missing":
        items.pop()
    elif mutation == "unknown-scenario":
        item = next(entry for entry in items if entry["disposition"] == "SCENARIO")
        item["scenarios"] = ["not-a-scenario"]
    elif mutation == "empty-proof":
        items[0]["proof"] = ""
    else:
        mutated["candidate_generation_id"] = "NT1231-U04-G2"
    with pytest.raises(qualification.RegressionQualificationError):
        qualification.validate_catalog(mutated, contract)


def test_candidate_outcome_requires_exact_oracle_fields_and_terminal_evidence() -> None:
    expected = {
        "average_entry_price": "100",
        "event_digest": "a" * 64,
        "fees": "0.2",
        "filled_quantity": "2",
        "iterations": 1,
        "position_quantity": "2",
        "realized_pnl": "0",
        "remaining_quantity": "0",
        "scenario_digest": "b" * 64,
        "scenario_id": "long-accounting",
        "stop_take_profit_precedence": "stop-first",
        "total_events": 2,
        "total_fills": 1,
        "total_orders": 1,
        "total_positions": 1,
        "unrealized_pnl": "2",
    }
    observed = {
        "payload": {
            "event_type": "NautilusBacktestSimulationCompleted",
            "attributes": [
                {"name": key, "value": value} for key, value in expected.items()
            ]
            + [{"name": "input_artifacts_sha256", "value": "c" * 64}],
        }
    }
    qualification.validate_candidate_outcome(observed, expected)

    changed = deepcopy(observed)
    changed["payload"]["attributes"][2]["value"] = "0.2000001"
    with pytest.raises(qualification.RegressionQualificationError, match="oracle"):
        qualification.validate_candidate_outcome(changed, expected)
    changed = deepcopy(observed)
    changed["payload"]["event_type"] = "Started"
    with pytest.raises(qualification.RegressionQualificationError, match="terminal"):
        qualification.validate_candidate_outcome(changed, expected)


def test_u06_runner_reuses_source_scenarios_and_has_no_build_path() -> None:
    source = Path(qualification.__file__).read_text(encoding="utf-8")
    assert "build_canonical_simulation_fixture" in source
    assert "calculate_reference_outcome" in source
    assert "_run_scenario" in source
    assert all(
        term not in source
        for term in (
            "build_nautilus_engine",
            "materialize_candidate_runtime_closure",
            "--materialize-candidate",
        )
    )


def test_oracle_projection_uses_canonical_decimal_strings() -> None:
    _fixture, _request, oracle = qualification._oracle("long-accounting")
    expected = qualification._expected(oracle)
    assert expected["fees"] == "0.2"
    assert expected["realized_pnl"] == "0"


def test_committed_u06_receipt_is_closed_and_generation_bound() -> None:
    raw = RECEIPT.read_bytes()
    receipt = json.loads(raw)
    assert raw == (
        json.dumps(receipt, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")
    assert receipt["schema"] == (
        "trading-agent-nautilus-u06-regression-qualification/v1"
    )
    assert receipt["verdict"] == "PASS"
    assert receipt["qualification_source_commit"] == (
        "09ea4f2a7ae27bdafe070763fa460068254bebfb"
    )
    assert receipt["qualification_source_tree"] == (
        "b97bda3c00aef8467b636b25464a782acf1e48bb"
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
        qualification._canonical(evidence)
    ).hexdigest()
    assert evidence["candidate_snapshot_before"] == evidence["candidate_snapshot_after"]
    assert set(evidence["scenarios"]) == set(json.loads(CATALOG.read_bytes())["scenario_ids"])
    assert set(evidence["outcomes"].values()) == {0}
    assert evidence["catalog"] == {
        "disposition_counts": {
            "NOT_USED": 14,
            "SCENARIO": 15,
            "UPSTREAM_ONLY": 11,
        },
        "release_item_count": 40,
        "sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
    }
    matrix = json.loads(MATRIX.read_bytes())
    assert matrix["catalog_sha256"] == evidence["catalog"]["sha256"]
    assert receipt["input_receipt_sha256s"]["u05_qualification"] == hashlib.sha256(
        (
            ROOT
            / "docs/implementation/p1-real-nautilus/upgrade/"
            "u05-api-qualification-receipt.json"
        ).read_bytes()
    ).hexdigest()
    assert evidence["source_sha256s"]["runner"] == hashlib.sha256(
        Path(qualification.__file__).read_bytes()
    ).hexdigest()
