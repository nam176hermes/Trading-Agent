#!/usr/bin/env python3
"""Emit read-only, source-scoped P1-H and P1 LTS qualification receipts."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import cast


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.nautilus_upgrade_authority import (
    CandidateGenerationError,
    EngineLifecycle,
    P1LtsPolicyError,
    P1LtsPolicyV2,
    load_candidate_generation,
    load_p1_lts_policy,
    validate_p1_lts_identity,
)
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY


POLICY_PATH = ROOT / "docs/implementation/p1-real-nautilus/lts/p1-engine-lts-policy-v2.json"
SAFE_AUTHORITY_LIMITS = {
    "broker_access_authorized": False,
    "database_runtime_authorized": False,
    "live_authorized": False,
    "network_authorized": False,
    "production_authorized": False,
}
_EXTERNAL_SPECS = {
    "foundation_receipt": ("trading-agent-p1-lts-foundation-proof/v1", "PASS"),
    "native_receipt": ("trading-agent-p1-lts-native-proof/v1", "PASS"),
    "operator_receipt": ("trading-agent-p1-lts-operator-acceptance/v1", "ACCEPT"),
}
_EXTERNAL_KEYS = {
    "authority_limits", "evidence_sha256s", "execution_scope", "schema", "source_commit", "source_tree", "verdict",
}
_LOCAL_GATES = {
    "P1S_SOURCE": (
        "tests/engine_contracts/test_session.py",
        "tests/nautilus_runtime_contracts",
        "tests/nautilus_upgrade/test_lts_policy.py",
        "tests/nautilus_upgrade/test_p1_h_impact_cli.py",
        "tests/nautilus_upgrade/test_p1_h_lifecycle.py",
        "tests/paper_runtime/test_engine_session_port.py",
    ),
    "P1S_RECOVERY": ("tests/execution_sandbox/test_recovery_checkpoint_load.py",),
    "P1S_GOLDEN": ("tests/nautilus_backtest/test_scenarios.py", "tests/nautilus_backtest/test_paper_compat.py"),
}


class P1EngineLtsQualificationError(ValueError):
    """Static or external P1 LTS evidence is invalid."""


def canonical_receipt_bytes(value: object) -> bytes:
    return canonical_json_bytes(value)


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise P1EngineLtsQualificationError("bound evidence is unavailable") from exc


def _repo_path(relative: str) -> Path:
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in relative:
        raise P1EngineLtsQualificationError("bound evidence path is invalid")
    return ROOT / relative


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P1EngineLtsQualificationError("bound evidence JSON is invalid") from exc
    if not isinstance(value, dict):
        raise P1EngineLtsQualificationError("bound evidence JSON shape is invalid")
    return cast(dict[str, object], value)


def _source_identity() -> tuple[str, str]:
    try:
        completed = subprocess.run(
            ("git", "-C", str(ROOT), "rev-parse", "HEAD", "HEAD^{tree}"),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise P1EngineLtsQualificationError("source identity is unavailable") from exc
    lines = completed.stdout.splitlines()
    if len(lines) != 2:
        raise P1EngineLtsQualificationError("source identity is invalid")
    return lines[0], lines[1]


def _source_clean() -> bool:
    try:
        completed = subprocess.run(
            ("git", "-C", str(ROOT), "status", "--porcelain=v1", "--untracked-files=all"),
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise P1EngineLtsQualificationError("source cleanliness is unavailable") from exc
    return not completed.stdout


def _source_changed(expected: tuple[str, str]) -> bool:
    return _source_identity() != expected or not _source_clean()


def _run_local_gates() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for node_id, nodeids in _LOCAL_GATES.items():
        completed = subprocess.run(
            (sys.executable, "-m", "pytest", "-q", "-W", "error", *nodeids),
            cwd=ROOT,
            check=False,
            timeout=180,
        )
        results[node_id] = completed.returncode == 0
    return results


def _bindings(policy: P1LtsPolicyV2) -> tuple[object, ...]:
    return tuple(
        getattr(policy.bindings, name)
        for name in policy.bindings.__class__.model_fields
    )


def _validate_static() -> tuple[P1LtsPolicyV2, dict[str, str]]:
    try:
        policy = load_p1_lts_policy(POLICY_PATH)
        validate_p1_lts_identity(policy, P1_REAL_BACKTEST_POLICY)
        generation = load_candidate_generation(_repo_path(policy.bindings.candidate_generation.path))
    except (CandidateGenerationError, P1LtsPolicyError) as exc:
        raise P1EngineLtsQualificationError(str(exc)) from exc
    bindings = _bindings(policy)
    hashes = {binding.path: _sha256(_repo_path(binding.path)) for binding in bindings}
    if any(hashes[binding.path] != binding.sha256 for binding in bindings):
        raise P1EngineLtsQualificationError("bound evidence SHA-256 is invalid")

    active = next(item for item in policy.engine_registry if item.lifecycle is EngineLifecycle.ACTIVE)
    rollback = next(item for item in policy.engine_registry if item.lifecycle is EngineLifecycle.ROLLBACK)
    if (
        generation.generation_id != "NT1231-U04-G1"
        or generation.engine_identity.version != active.engine_version
        or generation.engine_identity.upstream_commit != active.engine_upstream_commit
        or generation.rollback.version != rollback.engine_version
        or generation.rollback.upstream_commit != rollback.engine_upstream_commit
        or generation.rollback.schema_version != rollback.closure_schema_version
        or generation.rollback.closure_sha256 != rollback.closure_sha256
    ):
        raise P1EngineLtsQualificationError("candidate generation does not match the LTS registry")

    golden = policy.golden_registry
    scenarios = getattr(importlib.import_module(golden.module), golden.object_name, None)
    if (
        not isinstance(scenarios, tuple)
        or set(scenarios) != set(golden.scenarios)
        or _sha256(_repo_path(golden.source_path)) != golden.source_sha256
    ):
        raise P1EngineLtsQualificationError("canonical P1 scenario registry is invalid")
    matrix = _load_json(_repo_path(policy.bindings.release_regression_matrix.path))
    u06 = _load_json(_repo_path(policy.bindings.u06_regression.path))
    u07 = _load_json(_repo_path(policy.bindings.u07_dual_runtime.path))
    u06_evidence = u06.get("evidence")
    u07_evidence = u07.get("evidence")
    if not isinstance(u06_evidence, dict) or not isinstance(u07_evidence, dict):
        raise P1EngineLtsQualificationError("P1 regression receipt shape is invalid")
    u06_scenarios = u06_evidence.get("scenarios")
    u07_scenarios = u07_evidence.get("scenarios")
    if (
        matrix.get("scenario_ids") != sorted(scenarios)
        or not isinstance(u06_scenarios, dict)
        or not isinstance(u07_scenarios, dict)
        or u06.get("verdict") != "PASS"
        or u07.get("verdict") != "PASS"
    ):
        raise P1EngineLtsQualificationError("P1 regression evidence is not closed")
    for scenario_id, expected in golden.scenarios.items():
        left = u06_scenarios.get(scenario_id)
        right = u07_scenarios.get(scenario_id)
        if not isinstance(left, dict) or not isinstance(right, dict) or (
            left.get("result_sha256"), left.get("event_sha256"), left.get("oracle_sha256"),
            right.get("candidate_semantic_sha256"), right.get("rollback_semantic_sha256"),
        ) != (
            expected.result_sha256, expected.event_sha256, expected.oracle_sha256,
            expected.candidate_semantic_sha256, expected.rollback_semantic_sha256,
        ):
            raise P1EngineLtsQualificationError("P1 golden scenario binding is invalid")
    hashes["packages/nautilus_upgrade_authority/lts.py"] = _sha256(
        ROOT / "packages/nautilus_upgrade_authority/lts.py"
    )
    return policy, hashes


def _loads_external_exact(raw: bytes) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise P1EngineLtsQualificationError("external receipt contains a duplicate key")
            result[key] = value
        return result

    def reject_float(_value: str) -> object:
        raise P1EngineLtsQualificationError("external receipt float input is forbidden")

    try:
        value = json.loads(raw, object_pairs_hook=no_duplicates, parse_float=reject_float, parse_constant=reject_float)
    except P1EngineLtsQualificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P1EngineLtsQualificationError("external receipt JSON is invalid") from exc
    if not isinstance(value, dict) or canonical_receipt_bytes(value) != raw:
        raise P1EngineLtsQualificationError("external receipt is not canonical")
    return cast(dict[str, object], value)


def load_external_receipt(path: Path, *, schema: str, verdict: str, source_identity: tuple[str, str]) -> dict[str, object]:
    try:
        value = _loads_external_exact(path.read_bytes())
    except OSError as exc:
        raise P1EngineLtsQualificationError("external receipt is unavailable") from exc
    if set(value) != _EXTERNAL_KEYS:
        raise P1EngineLtsQualificationError("external receipt shape is invalid")
    if value.get("authority_limits") != SAFE_AUTHORITY_LIMITS:
        raise P1EngineLtsQualificationError("external receipt grants invalid authority")
    evidence = value.get("evidence_sha256s")
    if (
        not isinstance(evidence, list)
        or not evidence
        or evidence != sorted(set(evidence))
        or any(
            not isinstance(item, str)
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
            for item in evidence
        )
    ):
        raise P1EngineLtsQualificationError("external receipt evidence is invalid")
    if (
        value.get("execution_scope") != "PAPER_LOCAL_ONLY"
        or value.get("schema") != schema
        or value.get("verdict") != verdict
        or (value.get("source_commit"), value.get("source_tree")) != source_identity
    ):
        raise P1EngineLtsQualificationError("external receipt identity is invalid")
    return value


def _node_result(
    node_id: str,
    passed: bool,
    source: tuple[str, str],
    evidence_sha256s: tuple[str, ...],
) -> dict[str, object]:
    return {
        "evidence_sha256s": sorted(evidence_sha256s),
        "failure_codes": [] if passed else ["E_QUALIFICATION_FAILED"],
        "node_id": node_id,
        "source_commit": source[0],
        "source_tree": source[1],
        "status": "PASS" if passed else "HELD",
    }


def _base_receipt(
    policy: P1LtsPolicyV2,
    hashes: dict[str, str],
    source: tuple[str, str],
    clean: bool,
) -> dict[str, object]:
    golden_sha256 = hashlib.sha256(
        canonical_receipt_bytes(policy.golden_registry.model_dump(mode="json"))
    ).hexdigest()
    return {
        "authority_limits": SAFE_AUTHORITY_LIMITS,
        "checkpoint_policy": policy.checkpoint_policy.model_dump(mode="json"),
        "engine_registry": [item.model_dump(mode="json") for item in policy.engine_registry],
        "event_api_epoch": policy.event_api_epoch.model_dump(mode="json"),
        "execution_scope": policy.execution_scope,
        "golden_registry_sha256": golden_sha256,
        "lts_policy_sha256": policy.record_sha256,
        "qualification_dag": [item.model_dump(mode="json") for item in policy.qualification_dag],
        "source": {"clean": clean, "commit": source[0], "tree": source[1]},
        "static_evidence_sha256s": hashes,
    }


def _complete_node_results(
    base: dict[str, object],
    source: tuple[str, str],
    external_hashes: dict[str, str],
) -> list[dict[str, object]]:
    static = cast(dict[str, str], base["static_evidence_sha256s"])
    golden = cast(str, base["golden_registry_sha256"])
    return [
        _node_result("P1S_SOURCE", True, source, tuple(static.values())),
        _node_result("P1S_RECOVERY", True, source, (static[next(path for path in static if path.endswith("u04-rollback-isolation-receipt.json"))],)),
        _node_result("P1S_GOLDEN", True, source, (golden,)),
        _node_result("P1N_G1", True, source, (external_hashes["native_receipt"],)),
        _node_result("P1N_E2E", True, source, (external_hashes["native_receipt"],)),
        _node_result("P1N_PAPER", True, source, (external_hashes["native_receipt"],)),
        _node_result("P1H_FOUNDATION", True, source, (external_hashes["foundation_receipt"],)),
        _node_result("P1O_ACCEPT", True, source, (external_hashes["operator_receipt"],)),
    ]


def qualify(
    *,
    mode: str,
    native_receipt: Path | None = None,
    foundation_receipt: Path | None = None,
    operator_receipt: Path | None = None,
    source_identity: tuple[str, str] | None = None,
    source_clean: bool | None = None,
    local_gate_results: dict[str, bool] | None = None,
) -> tuple[dict[str, object], int]:
    policy, evidence_hashes = _validate_static()
    injected_source = source_identity is not None
    source = source_identity or _source_identity()
    clean = _source_clean() if source_clean is None else source_clean
    base = _base_receipt(policy, evidence_hashes, source, clean)

    if mode == "local":
        gates = local_gate_results if local_gate_results is not None else _run_local_gates()
        if set(gates) != set(_LOCAL_GATES) or any(type(value) is not bool for value in gates.values()):
            raise P1EngineLtsQualificationError("local gate results are invalid")
        node_results = [
            _node_result(node_id, passed, source, (policy.record_sha256,))
            for node_id, passed in gates.items()
        ]
        local = {**base, "local_gates": gates, "node_results": node_results}
        if not all(gates.values()):
            return {**local, "status": "HELD_LOCAL_GATE_FAILURE"}, 2
        if not clean:
            return {**local, "status": "HELD_DIRTY_SOURCE"}, 2
        if not injected_source and _source_changed(source):
            return {**local, "status": "HELD_SOURCE_CHANGED_DURING_QUALIFICATION"}, 2
        return {**local, "external_evidence": "NOT_ASSESSED", "status": "P1_H_LOCAL_SOURCE_QUALIFIED"}, 0

    if mode not in {"report", "source-ready", "final"}:
        raise P1EngineLtsQualificationError("qualification mode is invalid")
    supplied = {
        "foundation_receipt": foundation_receipt,
        "native_receipt": native_receipt,
        "operator_receipt": operator_receipt,
    }
    missing = [name for name, path in supplied.items() if path is None]
    if missing:
        status = "DEFERRED_EXTERNAL" if mode == "report" else (
            "HELD_MISSING_EXTERNAL_EVIDENCE" if len(missing) == len(supplied) else "HELD_PARTIAL_EXTERNAL_EVIDENCE"
        )
        return {**base, "missing_external_evidence": missing, "status": status}, 0 if mode == "report" else 2
    external_hashes: dict[str, str] = {}
    for name, path in supplied.items():
        assert path is not None
        schema, verdict = _EXTERNAL_SPECS[name]
        load_external_receipt(path, schema=schema, verdict=verdict, source_identity=source)
        external_hashes[name] = _sha256(path)
    if not clean:
        return {**base, "status": "HELD_DIRTY_SOURCE"}, 2
    if not injected_source and _source_changed(source):
        return {**base, "status": "HELD_SOURCE_CHANGED_DURING_QUALIFICATION"}, 2

    p1_h = {
        **base,
        "external_receipt_sha256s": external_hashes,
        "node_results": _complete_node_results(base, source, external_hashes),
        "result": "PASS",
        "schema": "trading-agent-p1-h-complete/v1",
        "source_qualification": "PASS",
        "status": "P1_H_COMPLETE",
    }
    p1_h_sha256 = hashlib.sha256(canonical_receipt_bytes(p1_h)).hexdigest()
    p1_h["receipt_sha256"] = p1_h_sha256
    if mode != "final":
        return p1_h, 0

    receipt = {
        **base,
        "bindings": {
            "p1_complete": policy.bindings.p1_complete_receipt.sha256,
            "p1_h_complete": p1_h_sha256,
        },
        "p1_h_complete": p1_h,
        "p1_h_complete_sha256": p1_h_sha256,
        "result": "PASS",
        "schema": "trading-agent-p1-lts-ready/v1",
        "source_qualification": "PASS",
        "status": "P1_LTS_READY",
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest()
    return receipt, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "report", "source-ready", "final"), required=True)
    parser.add_argument("--native-receipt", type=Path)
    parser.add_argument("--foundation-receipt", type=Path)
    parser.add_argument("--operator-receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt, exit_code = qualify(
            mode=args.mode,
            native_receipt=args.native_receipt,
            foundation_receipt=args.foundation_receipt,
            operator_receipt=args.operator_receipt,
        )
    except P1EngineLtsQualificationError as exc:
        receipt = {
            "authority_limits": SAFE_AUTHORITY_LIMITS,
            "error": str(exc),
            "execution_scope": "PAPER_LOCAL_ONLY",
            "schema": "trading-agent-p1-engine-lts-qualification/v2",
            "status": "HELD_INVALID_EVIDENCE",
        }
        exit_code = 2
    sys.stdout.buffer.write(canonical_receipt_bytes(receipt) + b"\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
