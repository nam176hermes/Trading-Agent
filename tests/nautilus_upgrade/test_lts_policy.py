"""Source-only LTS authority tests for the accepted P1 Nautilus lane."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

from packages.nautilus_upgrade_authority.lts import (
    LineageRole,
    P1ChangeClass,
    P1ImpactDisposition,
    P1LtsPolicyError,
    SourceQualification,
    classify_p1_change,
    load_p1_lts_policy,
    validate_p1_lts_identity,
)
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = (
    ROOT
    / "docs/implementation/p1-real-nautilus/lts"
    / "p1-engine-lts-policy-v1.json"
)


def _canonical(document: object) -> bytes:
    return (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def test_loads_exact_frozen_policy_and_current_compatibility_tuple() -> None:
    policy = load_p1_lts_policy(POLICY_PATH)

    assert policy.schema == "trading-agent-p1-engine-lts-policy/v1"
    assert policy.lineage_role is LineageRole.BASELINE
    assert policy.source_qualification is SourceQualification.UNASSESSED
    assert policy.compatibility.runtime_family == "cython-v1"
    assert policy.compatibility.engine_version == "1.231.0"
    assert policy.compatibility.candidate_closure_schema_version == 7
    assert policy.compatibility.candidate_closure_sha256 == (
        "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
    )
    assert policy.compatibility.product_closure_schema_version == 8
    assert policy.compatibility.product_closure_sha256 == (
        "97185d4c0b6090353ba51c1aab25ed4ea4dfab08113b655fac623af9e7db2b80"
    )
    assert policy.compatibility.paper_schema == "nautilus-paper-session-v2"
    assert policy.compatibility.semantic_profile == "nautilus-p1-real-backtest-v1"
    assert policy.execution_scope == "PAPER_LOCAL_ONLY"
    assert all(value is False for value in asdict(policy.authority_limits).values())
    with pytest.raises(FrozenInstanceError):
        policy.source_qualification = SourceQualification.QUALIFIED

    validate_p1_lts_identity(policy, P1_REAL_BACKTEST_POLICY)


def test_policy_binds_existing_generation_scenarios_and_receipts() -> None:
    policy = load_p1_lts_policy(POLICY_PATH)

    assert policy.candidate_generation.path == (
        "docs/implementation/p1-real-nautilus/upgrade/candidate-generations/"
        "NT1231-U04-G1.json"
    )
    assert policy.candidate_generation.sha256 == (
        "2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c"
    )
    assert policy.scenarios.module == "packages.nautilus_backtest.fixtures"
    assert policy.scenarios.object_name == "SCENARIO_IDS"
    assert policy.scenarios.sha256 == (
        "95ec82b986113780dbef8b8b0cb3751533ba0cf2e28209d91c3ce07f2a4fc885"
    )
    for binding in (policy.candidate_generation, policy.baseline_receipt, *policy.evidence):
        assert hashlib.sha256((ROOT / binding.path).read_bytes()).hexdigest() == binding.sha256


def test_policy_qualification_nodes_are_static_and_acyclic() -> None:
    policy = load_p1_lts_policy(POLICY_PATH)
    nodes = {node.node_id: node.dependencies for node in policy.qualification_nodes}

    assert nodes == {
        "P1S_SOURCE": (),
        "P1S_RECOVERY": ("P1S_SOURCE",),
        "P1S_GOLDEN": ("P1S_SOURCE",),
        "P1N_G1": ("P1S_GOLDEN",),
        "P1N_E2E": ("P1N_G1",),
        "P1N_PAPER": ("P1N_E2E", "P1S_RECOVERY"),
        "P1H_FOUNDATION": ("P1S_SOURCE", "P1S_RECOVERY", "P1S_GOLDEN"),
        "P1O_ACCEPT": (
            "P1S_SOURCE",
            "P1S_RECOVERY",
            "P1S_GOLDEN",
            "P1N_G1",
            "P1N_E2E",
            "P1N_PAPER",
            "P1H_FOUNDATION",
        ),
    }
    visited: set[str] = set()
    for node_id, dependencies in nodes.items():
        assert set(dependencies) <= visited
        visited.add(node_id)


@pytest.mark.parametrize(
    ("changed_paths", "declared", "compatibility_changed", "expected_class", "disposition"),
    (
        (("docs/implementation/p1-real-nautilus/design.md",), "A", False, "A", "QUALIFIABLE"),
        (("services/job_worker/main.py",), "A", False, "B", "QUALIFIABLE"),
        (("uv.lock",), "A", False, "C", "QUALIFIABLE"),
        (("packages/engine_contracts/versions.py",), "A", False, "D", "HELD"),
        (("unowned/new_surface.py",), "A", False, "D", "HELD"),
        (("docs/implementation/p1-real-nautilus/design.md",), "C", False, "C", "QUALIFIABLE"),
        (("docs/implementation/p1-real-nautilus/design.md",), "A", True, "D", "HELD"),
        ((), "A", False, "D", "HELD"),
    ),
)
def test_change_classifier_escalates_and_unknown_fails_closed(
    changed_paths: tuple[str, ...],
    declared: str,
    compatibility_changed: bool,
    expected_class: str,
    disposition: str,
) -> None:
    decision = classify_p1_change(changed_paths, declared, compatibility_changed)

    assert decision.change_class is P1ChangeClass(expected_class)
    assert decision.disposition is P1ImpactDisposition(disposition)
    if disposition == "HELD":
        assert decision.required_node_ids == ()
        assert decision.reasons
    else:
        assert decision.required_node_ids


def test_class_b_requires_native_and_acceptance_nodes() -> None:
    decision = classify_p1_change(("engines/nautilus/runtime_v1/main.py",), "A", False)

    assert decision.required_node_ids == (
        "P1S_SOURCE",
        "P1S_RECOVERY",
        "P1S_GOLDEN",
        "P1N_G1",
        "P1N_E2E",
        "P1N_PAPER",
        "P1H_FOUNDATION",
        "P1O_ACCEPT",
    )


@pytest.mark.parametrize(
    "path",
    (
        "engines/nautilus/runtime-closure-policy.json",
        "engines/nautilus/candidates/1.231/toolchain-inputs.json",
        "engines/nautilus/runtime_v1/runtime-inventory.json",
    ),
)
def test_native_closure_and_toolchain_changes_are_class_c(path: str) -> None:
    decision = classify_p1_change((path,), "A", False)

    assert decision.change_class is P1ChangeClass.C
    assert "closure_rebuild_and_verify_required" in decision.reasons


def test_identity_validation_binds_baseline_receipt_and_python_abi() -> None:
    policy = load_p1_lts_policy(POLICY_PATH)

    with pytest.raises(P1LtsPolicyError, match="incompatible"):
        validate_p1_lts_identity(
            policy,
            replace(P1_REAL_BACKTEST_POLICY, p1_baseline_receipt_sha256="0" * 64),
        )
    with pytest.raises(P1LtsPolicyError, match="incompatible"):
        validate_p1_lts_identity(
            policy,
            replace(
                P1_REAL_BACKTEST_POLICY,
                argv_prefix=("/usr/bin/python3.11", *P1_REAL_BACKTEST_POLICY.argv_prefix[1:]),
            ),
        )


@pytest.mark.parametrize(
    "path",
    ("/absolute.py", "../escape.py", "docs\\windows.py", ""),
)
def test_invalid_change_path_is_held(path: str) -> None:
    decision = classify_p1_change((path,), P1ChangeClass.A, False)

    assert decision.change_class is P1ChangeClass.D
    assert decision.disposition is P1ImpactDisposition.HELD


def test_policy_rejects_any_byte_mutation(tmp_path: Path) -> None:
    document = json.loads(POLICY_PATH.read_bytes())
    compatibility = document["compatibility"]
    assert isinstance(compatibility, dict)
    compatibility["engine_version"] = "1.232.0"
    mutated = tmp_path / POLICY_PATH.name
    mutated.write_bytes(_canonical(document))

    with pytest.raises(P1LtsPolicyError):
        load_p1_lts_policy(mutated)


def test_policy_rejects_noncanonical_duplicate_float_and_unknown_key(tmp_path: Path) -> None:
    raw = POLICY_PATH.read_bytes()

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(raw + b"\n")
    with pytest.raises(P1LtsPolicyError, match="canonical"):
        load_p1_lts_policy(noncanonical)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(raw.replace(b"{", b'{"schema":"duplicate",', 1))
    with pytest.raises(P1LtsPolicyError, match="duplicate"):
        load_p1_lts_policy(duplicate)

    document = json.loads(raw)
    document["unknown"] = True
    unknown = tmp_path / "unknown.json"
    unknown.write_bytes(_canonical(document))
    with pytest.raises(P1LtsPolicyError):
        load_p1_lts_policy(unknown)

    float_value = tmp_path / "float.json"
    float_value.write_bytes(raw.replace(b'"engine_version":"1.231.0"', b'"engine_version":1.231'))
    with pytest.raises(P1LtsPolicyError, match="float"):
        load_p1_lts_policy(float_value)


def test_make_lts_target_runs_the_bounded_executable_local_gates() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "test-p1-h-source:" in makefile
    assert (
        "qualify-p1-h-source: test-p1-h-source\n"
        "\t$(PYTHON) scripts/qualify_p1_engine_lts.py --mode local"
    ) in makefile
    target = makefile.split("qualify-p1-h-source:", 1)[1].split("\n\n", 1)[0]
    assert "test-p1-nautilus-native" not in target
    assert "qualify-p1-nautilus" not in target
    assert "test-p1-nautilus-e2e" not in target
