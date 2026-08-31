#!/usr/bin/env python3
"""Run the closed v1.228-v1.231 regression catalog on exact G1."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts import EngineEventEnvelope, canonical_json_bytes
from packages.nautilus_backtest import (
    SCENARIO_IDS,
    BacktestScenarioV1,
    build_canonical_simulation_fixture,
    build_simulation_envelope,
    calculate_reference_outcome,
    validate_isolated_simulation_result,
)
from packages.nautilus_upgrade_authority import load_candidate_generation
from scripts import qualify_nautilus_v1231_api as _u05


CATALOG = ROOT / "tests/nautilus_upgrade/regressions/v1.228-v1.231.json"
CONTRACT = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json"
)
GENERATION = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/candidate-generations"
    / "NT1231-U04-G1.json"
)
U05_RECEIPT = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/u05-api-qualification-receipt.json"
)
_DISPOSITIONS = {"NOT_USED", "SCENARIO", "UPSTREAM_ONLY"}


class RegressionQualificationError(ValueError):
    """The release catalog or a candidate regression result is invalid."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _pretty(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_catalog(catalog: dict[str, object], contract: dict[str, object]) -> None:
    release_delta = contract.get("release_delta")
    items = catalog.get("items")
    scenarios = catalog.get("scenario_ids")
    if (
        catalog.get("schema")
        != "trading-agent-nautilus-release-regression-catalog/v1"
        or catalog.get("candidate_generation_id") != "NT1231-U04-G1"
        or catalog.get("direct_api_contract_sha256") != _sha(CONTRACT)
        or not isinstance(release_delta, list)
        or not all(isinstance(item, dict) for item in release_delta)
        or not isinstance(items, list)
        or not all(isinstance(item, dict) for item in items)
        or scenarios != sorted(SCENARIO_IDS)
    ):
        raise RegressionQualificationError("regression catalog envelope is invalid")
    expected_ids = [item.get("id") for item in release_delta]
    observed_ids = [item.get("release_id") for item in items]
    if (
        expected_ids != observed_ids
        or len(observed_ids) != len(set(observed_ids))
        or len(observed_ids) != 40
    ):
        raise RegressionQualificationError("release mapping is incomplete or duplicated")
    covered_scenarios: set[str] = set()
    for item in items:
        if set(item) != {"disposition", "proof", "release_id", "scenarios"}:
            raise RegressionQualificationError("release mapping shape is invalid")
        disposition = item["disposition"]
        proof = item["proof"]
        mapped = item["scenarios"]
        if (
            disposition not in _DISPOSITIONS
            or not isinstance(proof, str)
            or not proof.strip()
            or not isinstance(mapped, list)
            or any(scenario not in SCENARIO_IDS for scenario in mapped)
            or len(mapped) != len(set(mapped))
            or (disposition == "SCENARIO") != bool(mapped)
        ):
            raise RegressionQualificationError("release mapping evidence is invalid")
        covered_scenarios.update(mapped)
    if covered_scenarios != set(SCENARIO_IDS):
        raise RegressionQualificationError("scenario coverage is incomplete")
    serialized = json.dumps(catalog, sort_keys=True).lower()
    if any(term in serialized for term in ("skip", "xfail", "tolerance")):
        raise RegressionQualificationError("weak regression acceptance is forbidden")


def validate_candidate_outcome(
    document: dict[str, object], expected: dict[str, object]
) -> dict[str, object]:
    payload = document.get("payload")
    if (
        not isinstance(payload, dict)
        or payload.get("event_type") != "NautilusBacktestSimulationCompleted"
    ):
        raise RegressionQualificationError("terminal regression evidence is missing")
    records = payload.get("attributes")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise RegressionQualificationError("regression attributes are invalid")
    attributes: dict[str, object] = {}
    for record in records:
        if set(record) != {"name", "value"} or not isinstance(record["name"], str):
            raise RegressionQualificationError("regression attribute shape is invalid")
        if record["name"] in attributes or isinstance(record["value"], float):
            raise RegressionQualificationError("regression attributes are duplicated or inexact")
        attributes[record["name"]] = record["value"]
    if set(attributes) != {*expected, "input_artifacts_sha256"} or any(
        attributes.get(name) != value for name, value in expected.items()
    ):
        raise RegressionQualificationError("candidate result differs from the exact oracle")
    return attributes


def _oracle(scenario_id: str):  # type: ignore[no-untyped-def]
    fixture = build_canonical_simulation_fixture(scenario_id)  # type: ignore[arg-type]
    request = build_simulation_envelope(fixture)
    payload = request.payload
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=fixture.simulation_scenario,
        catalog_bytes=fixture.instrument_catalog,
        strategy_bytes=fixture.strategy_configuration,
        market_data_bytes=fixture.market_data,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    return fixture, request, calculate_reference_outcome(scenario)


def _expected(outcome: object) -> dict[str, object]:
    dump = getattr(outcome, "model_dump", None)
    if not callable(dump):
        raise RegressionQualificationError("reference oracle result is invalid")
    value = dump(mode="json")
    if not isinstance(value, dict):
        raise RegressionQualificationError("reference oracle result is invalid")
    for name in (
        "average_entry_price",
        "fees",
        "filled_quantity",
        "position_quantity",
        "realized_pnl",
        "remaining_quantity",
        "unrealized_pnl",
    ):
        try:
            parsed = Decimal(str(value[name]))
        except (InvalidOperation, KeyError, ValueError) as exc:
            raise RegressionQualificationError(
                "reference oracle Decimal is invalid"
            ) from exc
        if not parsed.is_finite():
            raise RegressionQualificationError("reference oracle Decimal is invalid")
        rendered = format(parsed, "f")
        value[name] = (
            "0"
            if parsed.is_zero()
            else rendered.rstrip("0").rstrip(".")
            if "." in rendered
            else rendered
        )
    return value


def qualify(commit: str, tree: str, generation_id: str) -> dict[str, object]:
    try:
        _u05._git_identity(commit, tree)
        generation = load_candidate_generation(GENERATION)
        contract = _u05._closed_json(CONTRACT)
        catalog = _u05._closed_json(CATALOG)
        u05_receipt = _u05._closed_json(U05_RECEIPT)
    except Exception as exc:
        raise RegressionQualificationError("U06 input authority is invalid") from exc
    if (
        generation_id != generation.generation_id
        or u05_receipt.get("verdict") != "PASS"
        or u05_receipt.get("candidate_generation_sha256") != generation.record_sha256
        or u05_receipt.get("candidate_closure_sha256")
        != generation.closure.manifest_sha256
    ):
        raise RegressionQualificationError("U05/G1 authority is mixed or incomplete")
    validate_catalog(catalog, contract)
    policy = _u05._closed_json(_u05.POLICY)
    isolation = policy.get("external_cache_isolation")
    roots = isolation.get("external_roots") if isinstance(isolation, dict) else None
    runtime = roots.get("candidate_runtime_root") if isinstance(roots, dict) else None
    if not isinstance(runtime, str):
        raise RegressionQualificationError("candidate runtime root is unavailable")
    runtime_root = Path(runtime)
    before = _u05.snapshot_candidate_closure(
        runtime_root, generation.closure.manifest_sha256
    )
    cases: dict[str, object] = {}
    for scenario_id in SCENARIO_IDS:
        fixture, request, oracle = _oracle(scenario_id)
        del fixture
        try:
            event_document, stderr = _u05._run_scenario(runtime_root, scenario_id)
            attributes = validate_candidate_outcome(
                event_document, _expected(oracle)
            )
            event = EngineEventEnvelope.model_validate_json(
                canonical_json_bytes(event_document)
            )
            result = validate_isolated_simulation_result(request, event, oracle)
        except Exception as exc:
            raise RegressionQualificationError(
                f"candidate regression failed: {scenario_id}"
            ) from exc
        cases[scenario_id] = {
            "event_sha256": hashlib.sha256(
                canonical_json_bytes(event_document)
            ).hexdigest(),
            "oracle_sha256": hashlib.sha256(_canonical(_expected(oracle))).hexdigest(),
            "result_sha256": result.result_sha256,
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "terminal_counts": {
                "iterations": attributes["iterations"],
                "total_events": attributes["total_events"],
                "total_fills": attributes["total_fills"],
                "total_orders": attributes["total_orders"],
                "total_positions": attributes["total_positions"],
            },
        }
    after = _u05.snapshot_candidate_closure(
        runtime_root, generation.closure.manifest_sha256
    )
    if before != after:
        raise RegressionQualificationError("candidate closure changed during U06")
    items = catalog["items"]
    assert isinstance(items, list)
    counts = Counter(str(item["disposition"]) for item in items)
    evidence: dict[str, object] = {
        "candidate_snapshot_after": after,
        "candidate_snapshot_before": before,
        "catalog": {
            "disposition_counts": dict(sorted(counts.items())),
            "release_item_count": len(items),
            "sha256": _sha(CATALOG),
        },
        "outcomes": {
            "duplicate_accounting_facts": 0,
            "panics": 0,
            "unclassified_release_items": 0,
            "unexplained_pnl_drift": 0,
            "working_orders_after_process_exit": 0,
        },
        "scenarios": cases,
        "source_sha256s": {
            "fixtures": _sha(ROOT / "packages/nautilus_backtest/fixtures.py"),
            "launcher": _sha(_u05.LAUNCHER),
            "reference_oracle": _sha(ROOT / "packages/nautilus_backtest/reference.py"),
            "result_validator": _sha(ROOT / "packages/nautilus_backtest/result.py"),
            "runner": _sha(Path(__file__)),
            "u05_runner": _sha(Path(_u05.__file__)),
        },
    }
    return {
        "authority_limits": {
            "candidate_active": False,
            "candidate_promoted": False,
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
        "candidate_closure_sha256": generation.closure.manifest_sha256,
        "candidate_generation_id": generation.generation_id,
        "candidate_generation_sha256": generation.record_sha256,
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        "input_receipt_sha256s": {
            "direct_api_contract": _sha(CONTRACT),
            "u05_qualification": _sha(U05_RECEIPT),
        },
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "schema": "trading-agent-nautilus-u06-regression-qualification/v1",
        "verdict": "PASS",
    }


def _abort(message: str) -> NoReturn:
    print(f"U06 regression qualification failed: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--qualification-source-commit", required=True)
    parser.add_argument("--qualification-source-tree", required=True)
    arguments = parser.parse_args()
    try:
        receipt = qualify(
            arguments.qualification_source_commit,
            arguments.qualification_source_tree,
            arguments.generation,
        )
    except RegressionQualificationError as exc:
        _abort(str(exc))
    sys.stdout.buffer.write(_pretty(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
