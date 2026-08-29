from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import services.job_worker.p1_nautilus_closure as closure_module
from engines.nautilus.runtime_v1.profile import P1_REAL_BACKTEST_PROFILE
from packages.engine_contracts import RunBacktest
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY
from services.job_worker.p1_nautilus_closure import _load_policy
from services.job_worker.engine_spawn_interface import EngineSpawnError


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "engines/nautilus/p1-runtime-closure-policy.json"


def test_p1_profile_is_one_code_owned_closed_policy() -> None:
    policy = P1_REAL_BACKTEST_POLICY
    assert policy.profile == P1_REAL_BACKTEST_PROFILE
    assert policy.semantic_profile == "nautilus-p1-real-backtest-v1"
    assert policy.command_type == "RunBacktest"
    assert policy.manifest_schema_version == 8
    assert policy.runtime_family == "cython-v1"
    assert policy.engine_version == "1.231.0"
    assert policy.engine_upstream_commit == "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
    assert policy.entrypoint == "/engine/bin/nautilus-entry-guard"
    assert policy.argv_prefix == (
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "/engine/runtime_v1/main.py",
        "--profile",
        P1_REAL_BACKTEST_PROFILE,
    )
    assert policy.required_artifact_names == (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
    )
    assert policy.request_protocol_version == "1.0.0"
    assert policy.event_schema == "nautilus-p1-event-stream-v1"
    assert policy.result_validator_id == "nautilus-p1-event-stream-v1"
    assert policy.timeout_seconds == 120
    assert policy.closure_sha256 == (
        "74b4e8864d8c9a2cc8ba9e5944340f013739e496933fa2f5dc9817bfcb7bced1"
    )
    assert policy.sandbox_profile_sha256 == (
        "742d3d2cf313a0dc5832fd88d277da1d00e07c6e4abcc4ca51bf0ebcd7c3936e"
    )
    assert policy.runtime_inventory_sha256 == (
        "039e9c40c15270c816382870165ade5721edb11bcb1b4b8cb2f6af11b194a8f1"
    )
    assert "profile" not in RunBacktest.model_fields


def test_p1_policy_binds_the_complete_runtime_inventory_and_legacy_bytes() -> None:
    document = json.loads(POLICY_PATH.read_bytes())
    assert _load_policy() == document
    assert document["schema"] == "trading-agent-p1-runtime-closure-policy/v1"
    assert document["profile_manifest_schema_version"] == 8
    assert document["profile"] == P1_REAL_BACKTEST_PROFILE
    assert document["candidate_generation_id"] == "NT1231-U04-G1"
    assert document["candidate_generation_sha256"] == (
        "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"
    )
    assert document["candidate_closure_sha256"] == (
        "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
    )
    assert document["engine_wheel"] == {
        "mode": "0400",
        "sha256": "ecc461d0f634c25db17e0fb79136c3bf0d513edd323d4f9adaaf84346e68b2fb",
        "size": 183626605,
        "target": "/engine/wheels/nautilus_trader-1.231.0-cp312-cp312-manylinux_2_39_x86_64.whl",
    }
    baseline = ROOT / "docs/implementation/p1-real-nautilus/upgrade/p1-engine-baseline-receipt.json"
    assert document["p1_baseline_receipt_sha256"] == hashlib.sha256(
        baseline.read_bytes()
    ).hexdigest()
    assert document["p1_baseline_status"] == "P1_BASELINE_APPROVED"
    assert document["p1_baseline_scope"] == "P1_A_AND_P1_B_ONLY"
    assert (
        document["sandbox_profile_sha256"]
        == P1_REAL_BACKTEST_POLICY.sandbox_profile_sha256
    )
    inventory = document["runtime_inventory"]
    expected_sources = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "engines/nautilus/runtime_v1").glob("*.py")
    }
    assert {record["source"] for record in inventory} == expected_sources
    for record in inventory:
        raw = (ROOT / record["source"]).read_bytes()
        assert record == {
            "mode": "0400",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "source": record["source"],
            "target": "/engine/runtime_v1/" + Path(record["source"]).name,
        }

    assert hashlib.sha256(
        (ROOT / "services/job_worker/nautilus_closure.py").read_bytes()
    ).hexdigest() == "d7a67c023a96344ce53b1d4ed001822eaccf70fba4ba554b2885570f6758df89"
    assert hashlib.sha256(
        (ROOT / "engines/nautilus/runtime-closure-policy.json").read_bytes()
    ).hexdigest() == "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2"
    assert hashlib.sha256(
        (ROOT / "engines/nautilus/paper-compatibility-runtime-closure-policy.json").read_bytes()
    ).hexdigest() == "ab04b77042fb351a541764054e2bac7259097c749f6ff930c3fc68ef631d592c"
    assert hashlib.sha256(
        (ROOT / "engines/nautilus/native_entry_guard/src/main.rs").read_bytes()
    ).hexdigest() == "a25053355abcfece9b7d5c524f4a3d3c06ce727aec8224012ef9b683240fd880"


def test_p1_policy_rejects_artifact_manifest_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = json.loads(POLICY_PATH.read_bytes())
    document["artifact_manifest_sha256"] = "0" * 64
    changed_policy = tmp_path / "p1-runtime-closure-policy.json"
    changed_policy.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(closure_module, "_POLICY_PATH", changed_policy)

    with pytest.raises(EngineSpawnError, match="artifact manifest|policy"):
        _load_policy()


@pytest.mark.parametrize("mutation", ("stale-source", "missing", "digest"))
def test_p1_policy_rejects_runtime_inventory_mutation(
    mutation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = json.loads(POLICY_PATH.read_bytes())
    inventory = document["runtime_inventory"]
    assert isinstance(inventory, list)
    if mutation == "stale-source":
        inventory[0]["sha256"] = "0" * 64
    elif mutation == "missing":
        inventory.pop()
    else:
        document["runtime_inventory_sha256"] = "0" * 64
    changed_policy = tmp_path / "p1-runtime-closure-policy.json"
    changed_policy.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(closure_module, "_POLICY_PATH", changed_policy)

    with pytest.raises(EngineSpawnError, match="runtime inventory|closure policy"):
        _load_policy()
