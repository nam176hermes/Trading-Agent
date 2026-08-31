#!/usr/bin/env python3
"""Run the opt-in P1-U04 host-authority lane without accepting skips."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_nautilus_engine as builder
from scripts import run_required_runtime_pytest


HOST_TESTS = (
    ROOT / "tests/nautilus_upgrade/host_authority/p1_u04_host_authority.py"
)
SCHEMA = "p1-u04-host-authority-receipt-v1"
TOPOLOGY_SCHEMA = "p1-u04-host-topology-receipt-v1"
_P1_REQUIRED_EXTERNAL = frozenset(
    {
        "EXT-PHASE3B-CORPUS",
        "EXT-LEGACY-UV-AUTHORITY",
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
    }
)
_P1_FORBIDDEN_POSTGRES = frozenset(
    {
        "EXT-DISPOSABLE-PG-GREEN",
        "EXT-DISPOSABLE-PG-RED",
        "EXT-DISPOSABLE-PG-RED-EVIDENCE",
    }
)


def _emit(
    outcome: str,
    reason: str,
    *,
    schema: str = SCHEMA,
    lane: str = "HOST_EXTERNAL_AUTHORITY",
) -> int:
    print(
        json.dumps(
            {
                "lane": lane,
                "outcome": outcome,
                "reason": reason,
                "schema": schema,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return {"PASS": 0, "FAIL": 1, "DEFERRED": 3}[outcome]


def _load_engine_policy() -> dict[str, object]:
    return json.loads(builder._CANDIDATE_ENGINE_POLICY.read_text(encoding="ascii"))


def _validate_p1_external_outcomes(
    receipts: list[dict[str, object]],
) -> dict[str, str]:
    expected = _P1_REQUIRED_EXTERNAL | _P1_FORBIDDEN_POSTGRES
    by_code = {
        str(receipt.get("capability_or_authority_code")): receipt
        for receipt in receipts
    }
    if len(receipts) != len(by_code) or set(by_code) != expected:
        raise ValueError("P1_U04_EXTERNAL_AUTHORITY_INVALID")
    for code in _P1_REQUIRED_EXTERNAL:
        receipt = by_code[code]
        if (
            receipt.get("outcome") != "PASS"
            or receipt.get("preflight_state") != "VALID"
            or receipt.get("redacted_fact_class") != "AUTHORITY_COMPLETE_VALIDATED"
        ):
            raise ValueError("P1_U04_EXTERNAL_AUTHORITY_INVALID")
    for code in _P1_FORBIDDEN_POSTGRES:
        receipt = by_code[code]
        if (
            receipt.get("outcome") != "DEFERRED"
            or receipt.get("preflight_state") != "ABSENT"
            or receipt.get("redacted_fact_class") != "AUTHORITY_RECORD_ABSENT"
        ):
            raise ValueError("P1_U04_EXTERNAL_AUTHORITY_INVALID")
    return {code: str(by_code[code]["outcome"]) for code in sorted(by_code)}


def _validate_p1_topology(evidence_root: Path, foundation_context_path: Path) -> int:
    from scripts import t_g03_capability_topology as topology

    try:
        run_id, head_sha = topology._active_foundation_identity()
        context = topology.load_foundation_context(
            foundation_context_path, run_id=run_id, head_sha=head_sha,
        )
        inventory = ROOT / "tests/fixtures/t-g03a-hosted-failure-inventory.tsv"
        topology.validate_portable_defect_closure(
            inventory=inventory,
            evidence_root=evidence_root,
            run_id=run_id,
            head_sha=head_sha,
            foundation_context_path=foundation_context_path,
        )
        rows = topology._installed_inventory_rows(inventory, evidence_root)
        baseline = topology.load_portable_root_baseline(
            inventory=inventory,
            evidence_root=evidence_root,
            run_id=run_id,
            head_sha=head_sha,
            foundation_context_path=foundation_context_path,
        )
        custody = topology._validate_custody_policy(baseline["collector_policy"])
        topology_root = evidence_root / "capability-topology"
        native_status = topology.validate_native_artifacts(
            topology_root,
            rows=rows,
            foundation_context=context,
            sealed_custody=custody,
            require_pass=True,
        )
        receipts = [
            topology.validate_external_artifact_set(
                topology_root / f"{code}.json",
                rows=rows,
                foundation_context=context,
                sealed_custody=custody,
            )[0]
            for code in sorted(_P1_REQUIRED_EXTERNAL | _P1_FORBIDDEN_POSTGRES)
        ]
        external = _validate_p1_external_outcomes(receipts)
    except (OSError, RuntimeError, ValueError, topology.TopologyError):
        return _emit(
            "FAIL", "P1_TOPOLOGY_INVALID", schema=TOPOLOGY_SCHEMA,
            lane="P1_U04_HOST_TOPOLOGY",
        )
    print(
        json.dumps(
            {
                "external_outcomes": external,
                "foundation_head_sha": head_sha,
                "foundation_run_id": run_id,
                "lane": "P1_U04_HOST_TOPOLOGY",
                "native_status": native_status,
                "outcome": "PASS",
                "portable_closure_status": "PASS",
                "schema": TOPOLOGY_SCHEMA,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _cache_is_exact(cache: Path, policy: dict[str, object]) -> bool:
    try:
        info = cache.lstat()
    except OSError:
        return False
    isolation = policy["external_cache_isolation"]
    assert isinstance(isolation, dict)
    return (
        stat.S_ISDIR(info.st_mode)
        and not cache.is_symlink()
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == int(str(isolation["directory_mode"]), 8)
    )


def _missing_host_authority(policy: dict[str, object]) -> bool:
    isolation = policy["external_cache_isolation"]
    python = policy["python"]
    native = policy["native_build_authority"]
    assert isinstance(isolation, dict)
    assert isinstance(python, dict)
    assert isinstance(native, dict)
    roots = isolation["external_roots"]
    assert isinstance(roots, dict)
    required_roots = (
        "candidate_cargo_home_root",
        "candidate_input_root",
        "candidate_llvm_toolchain_root",
        "candidate_rust_toolchain_root",
        "candidate_toolchain_root",
        "candidate_vendor_root",
    )
    required = [Path(str(roots[name])) for name in required_roots]
    snapshot = native.get("snapshot")
    if not isinstance(snapshot, dict):
        return False
    required.extend(
        (Path(str(snapshot.get("root"))), Path(str(snapshot.get("receipt_path"))))
    )
    required.extend(
        (
            Path(str(python["executable"])),
            Path(str(python["stdlib_inventory"]["path"])),
            builder._CANDIDATE_SANDBOX,
        )
    )
    return any(not path.exists() for path in required)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-cache", type=Path)
    parser.add_argument("--topology-evidence-root", type=Path)
    parser.add_argument("--foundation-context-path", type=Path)
    arguments = parser.parse_args(argv)
    if (
        arguments.topology_evidence_root is None
        and arguments.foundation_context_path is not None
    ) or (
        arguments.topology_evidence_root is not None
        and arguments.foundation_context_path is None
    ):
        return _emit(
            "FAIL", "P1_TOPOLOGY_ARGUMENTS_INCOMPLETE", schema=TOPOLOGY_SCHEMA,
            lane="P1_U04_HOST_TOPOLOGY",
        )
    if arguments.topology_evidence_root is not None:
        if arguments.evidence_cache is not None:
            return _emit(
                "FAIL", "P1_TOPOLOGY_ARGUMENTS_MIXED", schema=TOPOLOGY_SCHEMA,
                lane="P1_U04_HOST_TOPOLOGY",
            )
        assert arguments.foundation_context_path is not None
        return _validate_p1_topology(
            arguments.topology_evidence_root,
            arguments.foundation_context_path,
        )
    policy = _load_engine_policy()
    roots = policy["external_cache_isolation"]["external_roots"]
    expected_cache = Path(str(roots["candidate_input_root"]))
    if arguments.evidence_cache is None:
        return _emit("DEFERRED", "EVIDENCE_CACHE_NOT_SUPPLIED")
    if arguments.evidence_cache != expected_cache:
        return _emit("FAIL", "EVIDENCE_CACHE_PATH_NOT_EXACT")
    if not _cache_is_exact(arguments.evidence_cache, policy):
        return _emit("FAIL", "EVIDENCE_CACHE_INVALID")
    if _missing_host_authority(policy):
        return _emit("DEFERRED", "HOST_AUTHORITY_NOT_AVAILABLE")
    try:
        builder._validate_sandbox(builder._CANDIDATE_SANDBOX)
    except (OSError, RuntimeError, ValueError):
        return _emit("FAIL", "BUBBLEWRAP_AUTHORITY_INVALID")
    try:
        with builder._verified_candidate_native_snapshot(policy):
            pass
    except (OSError, RuntimeError, ValueError):
        return _emit("FAIL", "HOST_AUTHORITY_INVALID")

    os.environ["P1_U03_TOOLCHAIN_CACHE"] = str(expected_cache)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = run_required_runtime_pytest.main([str(HOST_TESTS)])
    if result != 0:
        sys.stderr.write(stdout.getvalue())
        sys.stderr.write(stderr.getvalue())
        return _emit("FAIL", "HOST_TESTS_FAILED")
    return _emit("PASS", "HOST_TESTS_PASSED")


if __name__ == "__main__":
    raise SystemExit(main())
