"""Generation-bound U05 runner and receipt boundary."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import qualify_nautilus_v1231_api as qualification


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json"
)
GOLDEN = ROOT / "tests/fixtures/nautilus_upgrade/v1.231-api-probe.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _probe_document() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_bytes())
    golden = json.loads(GOLDEN.read_bytes())
    return {
        "api_surface_count": len(contract["api_surfaces"]),
        "engine_version": "1.231.0",
        "lifecycle": {
            "dispose_called": True,
            "reset_called": True,
            "reset_retained_instrument": True,
            "reset_retained_strategy": True,
        },
        "local_invocation_count": len(contract["local_invocations"]),
        "local_invocation_ids": sorted(
            item["id"] for item in contract["local_invocations"]
        ),
        "schema": "trading-agent-nautilus-v1231-api-probe/v1",
        "status": "PASS",
        "surface_cases": [
            {
                "case": (
                    "RESULT_INSTANCE"
                    if item["id"] == "API-BACKTEST-RESULT"
                    else "STRATEGY_SUBCLASS"
                    if item["id"] == "API-STRATEGY"
                    else "IMPORTED_SYMBOL"
                ),
                "id": item["id"],
                "members": item["required_members"],
                "module": item["import_module"],
                "symbol": item["import_symbol"],
            }
            for item in contract["api_surfaces"]
        ],
        "surface_ids_sha256": golden["surface_ids_sha256"],
    }


def _event(scenario_id: str) -> dict[str, object]:
    expected = json.loads(GOLDEN.read_bytes())["scenarios"][scenario_id]
    return {
        "payload": {
            "event_type": "NautilusBacktestSimulationCompleted",
            "attributes": [
                {"name": key, "value": value}
                for key, value in expected.items()
            ],
        }
    }


def test_probe_and_execution_evidence_must_match_complete_golden() -> None:
    contract = json.loads(CONTRACT.read_bytes())
    golden = json.loads(GOLDEN.read_bytes())
    probe = _probe_document()

    qualification.validate_probe_result(probe, contract=contract, golden=golden)
    for scenario_id in ("long-accounting", "same-bar-stop-take-profit"):
        attributes = qualification.validate_scenario_event(
            _event(scenario_id), scenario_id=scenario_id, golden=golden
        )
        assert attributes.items() >= golden["scenarios"][scenario_id].items()

    mutated = deepcopy(probe)
    mutated["surface_cases"] = mutated["surface_cases"][:-1]
    with pytest.raises(qualification.ApiQualificationError, match="surface"):
        qualification.validate_probe_result(mutated, contract=contract, golden=golden)

    mutated = deepcopy(probe)
    mutated["engine_version"] = "1.227.0"
    with pytest.raises(qualification.ApiQualificationError, match="version"):
        qualification.validate_probe_result(mutated, contract=contract, golden=golden)


def test_closure_snapshot_detects_candidate_mutation(tmp_path: Path) -> None:
    payload = b"candidate-byte"
    file_path = tmp_path / "files/engine/wheels/example.whl"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(payload)
    file_path.chmod(0o400)
    manifest = {
        "activation_status": "CANDIDATE_ONLY_NOT_ACTIVATED",
        "files": [
            {
                "mode": "0400",
                "path": "files/engine/wheels/example.whl",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "target": "/engine/wheels/example.whl",
            }
        ],
        "schema_version": 7,
    }
    manifest_path = tmp_path / "closure-manifest.json"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    manifest_path.chmod(0o400)
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    tmp_path.chmod(0o500)

    try:
        before = qualification.snapshot_candidate_closure(tmp_path, digest)
        file_path.chmod(0o600)
        file_path.write_bytes(b"mutated")
        with pytest.raises(qualification.ApiQualificationError, match="candidate file"):
            qualification.snapshot_candidate_closure(tmp_path, digest)
        assert before["closure_manifest_sha256"] == digest
    finally:
        tmp_path.chmod(0o700)


def test_u05_runner_contains_no_build_or_materialization_path() -> None:
    source = Path(qualification.__file__).read_text(encoding="utf-8")
    forbidden = (
        "build_nautilus_engine",
        "materialize_candidate_runtime_closure",
        "materialize_nautilus_runtime_closure",
        "--materialize-candidate",
    )
    assert all(term not in source for term in forbidden)
