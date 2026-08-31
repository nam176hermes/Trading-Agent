#!/usr/bin/env python3
"""Emit read-only, source-scoped P1 engine LTS qualification receipts."""

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
    EngineRegistryEntry,
    EventApiEpoch,
    P1LtsPolicyV1,
    P1LtsPolicyError,
    load_candidate_generation,
    load_p1_lts_policy,
    golden_registry_sha256,
    validate_p1_lts_identity,
    validate_engine_registry,
)
from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY


POLICY_PATH = (
    ROOT
    / "docs/implementation/p1-real-nautilus/lts"
    / "p1-engine-lts-policy-v1.json"
)
SAFE_AUTHORITY_LIMITS = {
    "broker_access_authorized": False,
    "database_runtime_authorized": False,
    "live_authorized": False,
    "network_authorized": False,
    "production_authorized": False,
}
_EXTERNAL_SPECS = {
    "foundation_receipt": (
        "trading-agent-p1-lts-foundation-proof/v1",
        "PASS",
    ),
    "native_receipt": (
        "trading-agent-p1-lts-native-proof/v1",
        "PASS",
    ),
    "operator_receipt": (
        "trading-agent-p1-lts-operator-acceptance/v1",
        "ACCEPT",
    ),
}
_EXTERNAL_KEYS = {
    "authority_limits",
    "execution_scope",
    "schema",
    "source_commit",
    "source_tree",
    "verdict",
}
_LOCAL_GATES = {
    "P1S_SOURCE": (
        "tests/nautilus_runtime_contracts",
        "tests/nautilus_upgrade/test_lts_policy.py",
    ),
    "P1S_RECOVERY": ("tests/execution_sandbox/test_recovery_checkpoint_load.py",),
    "P1S_GOLDEN": (
        "tests/nautilus_backtest/test_scenarios.py",
        "tests/nautilus_backtest/test_paper_compat.py",
    ),
}


class P1EngineLtsQualificationError(ValueError):
    """Static or external P1 LTS evidence is invalid."""


def canonical_receipt_bytes(value: object) -> bytes:
    """Return the one canonical wire representation used by H9 receipts."""

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
            (
                "git",
                "-C",
                str(ROOT),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
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


def _validate_static() -> tuple[P1LtsPolicyV1, dict[str, str]]:
    try:
        policy = load_p1_lts_policy(POLICY_PATH)
        validate_p1_lts_identity(policy, P1_REAL_BACKTEST_POLICY)
        generation = load_candidate_generation(_repo_path(policy.candidate_generation.path))
    except (CandidateGenerationError, P1LtsPolicyError) as exc:
        raise P1EngineLtsQualificationError(str(exc)) from exc
    bindings = (policy.candidate_generation, policy.baseline_receipt, *policy.evidence)
    hashes = {binding.path: _sha256(_repo_path(binding.path)) for binding in bindings}
    if any(hashes[binding.path] != binding.sha256 for binding in bindings):
        raise P1EngineLtsQualificationError("bound evidence SHA-256 is invalid")
    compatibility = policy.compatibility
    if (
        generation.generation_id != "NT1231-U04-G1"
        or generation.engine_identity.version != compatibility.engine_version
        or generation.engine_identity.upstream_commit != compatibility.engine_upstream_commit
        or generation.closure.schema_version != compatibility.candidate_closure_schema_version
        or generation.closure.manifest_sha256 != compatibility.candidate_closure_sha256
        or generation.rollback.version != compatibility.rollback_version
        or generation.rollback.upstream_commit != compatibility.rollback_upstream_commit
        or generation.rollback.schema_version != compatibility.rollback_closure_schema_version
        or generation.rollback.closure_sha256 != compatibility.rollback_closure_sha256
    ):
        raise P1EngineLtsQualificationError("candidate generation does not match the LTS tuple")
    scenario_module = importlib.import_module(policy.scenarios.module)
    scenarios = getattr(scenario_module, policy.scenarios.object_name, None)
    if (
        not isinstance(scenarios, tuple)
        or len(scenarios) != 8
        or any(not isinstance(item, str) for item in scenarios)
        or _sha256(ROOT / "packages/nautilus_backtest/fixtures.py") != policy.scenarios.sha256
    ):
        raise P1EngineLtsQualificationError("canonical P1 scenario registry is invalid")
    scenario_ids = set(scenarios)
    evidence_by_name = {Path(binding.path).name: _load_json(_repo_path(binding.path)) for binding in policy.evidence}
    matrix = evidence_by_name["release-regression-matrix.json"]
    u06 = evidence_by_name["u06-regression-qualification-receipt.json"]
    u07 = evidence_by_name["u07-dual-runtime-qualification-receipt.json"]
    u06_evidence = u06.get("evidence")
    u07_evidence = u07.get("evidence")
    if not isinstance(u06_evidence, dict) or not isinstance(u07_evidence, dict):
        raise P1EngineLtsQualificationError("P1 regression receipt shape is invalid")
    u06_scenarios = u06_evidence.get("scenarios")
    u07_scenarios = u07_evidence.get("scenarios")
    if (
        matrix.get("scenario_ids") != sorted(scenario_ids)
        or not isinstance(u06_scenarios, dict)
        or not isinstance(u07_scenarios, dict)
        or set(u06_scenarios) != scenario_ids
        or set(u07_scenarios) != scenario_ids
        or u06.get("verdict") != "PASS"
        or u07.get("verdict") != "PASS"
    ):
        raise P1EngineLtsQualificationError("P1 regression evidence is not closed")
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
        value = json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_float=reject_float,
            parse_constant=reject_float,
        )
    except P1EngineLtsQualificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P1EngineLtsQualificationError("external receipt JSON is invalid") from exc
    if not isinstance(value, dict) or canonical_receipt_bytes(value) != raw:
        raise P1EngineLtsQualificationError("external receipt is not canonical")
    return cast(dict[str, object], value)


def load_external_receipt(
    path: Path,
    *,
    schema: str,
    verdict: str,
    source_identity: tuple[str, str],
) -> dict[str, object]:
    """Load an exact, current, paper-only external proof."""

    try:
        value = _loads_external_exact(path.read_bytes())
    except OSError as exc:
        raise P1EngineLtsQualificationError("external receipt is unavailable") from exc
    if set(value) != _EXTERNAL_KEYS:
        raise P1EngineLtsQualificationError("external receipt shape is invalid")
    if value.get("authority_limits") != SAFE_AUTHORITY_LIMITS:
        raise P1EngineLtsQualificationError("external receipt grants invalid authority")
    if (
        value.get("execution_scope") != "PAPER_LOCAL_ONLY"
        or value.get("schema") != schema
        or value.get("verdict") != verdict
        or (value.get("source_commit"), value.get("source_tree")) != source_identity
    ):
        raise P1EngineLtsQualificationError("external receipt identity is invalid")
    return value


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
    """Evaluate local or externally certified source readiness without mutation."""

    policy, evidence_hashes = _validate_static()
    api_epoch = EventApiEpoch(
        request_protocol=policy.compatibility.request_protocol_version,
        event_schema=policy.compatibility.event_schema,
        paper_schema=policy.compatibility.paper_schema,
        result_validator=P1_REAL_BACKTEST_POLICY.result_validator_id,
        manifest_schema=P1_REAL_BACKTEST_POLICY.manifest_schema_version,
    )
    registry = validate_engine_registry(
        (
            EngineRegistryEntry(
                policy.compatibility.runtime_family,
                policy.compatibility.engine_version,
                EngineLifecycle.ACTIVE,
            ),
            EngineRegistryEntry(
                policy.compatibility.runtime_family,
                policy.compatibility.rollback_version,
                EngineLifecycle.ROLLBACK,
            ),
        )
    )
    injected_source = source_identity is not None
    commit, tree = source_identity or _source_identity()
    clean = _source_clean() if source_clean is None else source_clean
    base: dict[str, object] = {
        "authority_limits": SAFE_AUTHORITY_LIMITS,
        "candidate_generation_sha256": policy.candidate_generation.sha256,
        "checkpoint_schema": "sandbox-recovery-checkpoint-v2",
        "engine_registry": [
            {
                "lifecycle": entry.lifecycle.value,
                "runtime_family": entry.runtime_family,
                "version": entry.engine_version,
            }
            for entry in registry
        ],
        "event_api_epoch_sha256": api_epoch.sha256,
        "execution_scope": "PAPER_LOCAL_ONLY",
        "lts_policy_sha256": policy.record_sha256,
        "golden_registry_sha256": golden_registry_sha256(
            tuple(
                getattr(
                    importlib.import_module(policy.scenarios.module),
                    policy.scenarios.object_name,
                )
            ),
            policy.scenarios.sha256,
        ),
        "schema": "trading-agent-p1-engine-lts-qualification/v1",
        "source": {"clean": clean, "commit": commit, "tree": tree},
        "static_evidence_sha256s": evidence_hashes,
    }
    if mode == "local":
        gates = local_gate_results if local_gate_results is not None else _run_local_gates()
        if set(gates) != set(_LOCAL_GATES) or any(type(value) is not bool for value in gates.values()):
            raise P1EngineLtsQualificationError("local gate results are invalid")
        base["local_gates"] = gates
        if not all(gates.values()):
            return {**base, "status": "HELD_LOCAL_GATE_FAILURE"}, 2
        if not injected_source and _source_changed((commit, tree)):
            return {
                **base,
                "status": "HELD_SOURCE_CHANGED_DURING_QUALIFICATION",
            }, 2
        if not clean:
            return {**base, "status": "HELD_DIRTY_SOURCE"}, 2
        return {
            **base,
            "external_evidence": "NOT_ASSESSED",
            "status": "P1_H_LOCAL_SOURCE_QUALIFIED",
        }, 0
    if mode != "source-ready":
        raise P1EngineLtsQualificationError("qualification mode is invalid")
    supplied = {
        "foundation_receipt": foundation_receipt,
        "native_receipt": native_receipt,
        "operator_receipt": operator_receipt,
    }
    missing = [name for name, path in supplied.items() if path is None]
    if len(missing) == len(supplied):
        return {**base, "missing_external_evidence": missing, "status": "DEFERRED_EXTERNAL"}, 0
    if missing:
        return {
            **base,
            "missing_external_evidence": missing,
            "status": "HELD_PARTIAL_EXTERNAL_EVIDENCE",
        }, 2
    external_hashes: dict[str, str] = {}
    for name, path in supplied.items():
        assert path is not None
        schema, verdict = _EXTERNAL_SPECS[name]
        load_external_receipt(
            path,
            schema=schema,
            verdict=verdict,
            source_identity=(commit, tree),
        )
        external_hashes[name] = _sha256(path)
    if not injected_source and _source_changed((commit, tree)):
        return {
            **base,
            "status": "HELD_SOURCE_CHANGED_DURING_QUALIFICATION",
        }, 2
    if not clean:
        return {**base, "status": "HELD_DIRTY_SOURCE"}, 2
    return {
        **base,
        "external_receipt_sha256s": external_hashes,
        "status": "P1_ENGINE_LTS_SOURCE_READY",
    }, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "source-ready"), required=True)
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
            "schema": "trading-agent-p1-engine-lts-qualification/v1",
            "status": "HELD_INVALID_EVIDENCE",
        }
        exit_code = 2
    sys.stdout.buffer.write(canonical_receipt_bytes(receipt) + b"\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
