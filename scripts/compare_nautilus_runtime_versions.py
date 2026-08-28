#!/usr/bin/env python3
"""Compare exact 1.227 rollback and G1 semantics in isolated processes."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.nautilus_backtest import SCENARIO_IDS
from packages.nautilus_upgrade_authority import load_candidate_generation
from scripts import qualify_nautilus_v1231_api as _u05
from scripts import qualify_nautilus_v1231_regressions as _u06
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


GENERATION = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/candidate-generations"
    / "NT1231-U04-G1.json"
)
ROLLBACK_RECEIPT = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/u04-rollback-isolation-receipt.json"
)
U06_RECEIPT = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/u06-regression-qualification-receipt.json"
)
DRIFT_LEDGER = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/approved-drift-ledger.json"
)
_NORMALIZED_FIELDS = frozenset(
    {"attempt_id", "run_uuid", "custody_timestamp", "staging_token"}
)
_DRIFT_CLASSES = {
    "EXPECTED_UPSTREAM_FIX",
    "APPROVED_CONTRACT_CHANGE",
}


class RuntimeComparisonError(ValueError):
    """The two-runtime semantic authority is invalid or incomplete."""


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


def _semantic_value(value: object) -> object:
    if isinstance(value, float):
        raise RuntimeComparisonError("floating-point semantic evidence is forbidden")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise RuntimeComparisonError("semantic object keys must be strings")
        return {
            key: _semantic_value(item)
            for key, item in value.items()
            if key not in _NORMALIZED_FIELDS
        }
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise RuntimeComparisonError("semantic evidence is not canonical JSON")


def semantic_digest(document: dict[str, object]) -> str:
    return hashlib.sha256(_canonical(_semantic_value(document))).hexdigest()


def classify_semantic_drift(
    rollback: dict[str, object],
    candidate: dict[str, object],
    approvals: list[dict[str, object]],
) -> str:
    rollback_sha = semantic_digest(rollback)
    candidate_sha = semantic_digest(candidate)
    if rollback_sha == candidate_sha:
        return "NONE"
    for approval in approvals:
        if (
            approval.get("rollback_semantic_sha256") == rollback_sha
            and approval.get("candidate_semantic_sha256") == candidate_sha
            and approval.get("classification") in _DRIFT_CLASSES
        ):
            return str(approval["classification"])
    return "UNEXPLAINED_BLOCKER"


def require_deterministic_repeats(runs: list[dict[str, object]]) -> str:
    if len(runs) != 3:
        raise RuntimeComparisonError("exactly three runtime repeats are required")
    digests = {semantic_digest(run) for run in runs}
    if len(digests) != 1:
        raise RuntimeComparisonError("runtime is not internally deterministic")
    return next(iter(digests))


def _snapshot_runtime(root: Path, manifest_sha256: str) -> tuple[dict[str, object], set[tuple[int, int]]]:
    manifest = root / "closure-manifest.json"
    try:
        root_stat = root.lstat()
        manifest_raw = manifest.read_bytes()
    except OSError as exc:
        raise RuntimeComparisonError("runtime closure is unavailable") from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or root_stat.st_uid != os.geteuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o500
        or hashlib.sha256(manifest_raw).hexdigest() != manifest_sha256
    ):
        raise RuntimeComparisonError("runtime closure identity is invalid")
    document = _u05._closed_json(manifest)
    records = document.get("files")
    if not isinstance(records, list) or not records:
        raise RuntimeComparisonError("runtime closure inventory is invalid")
    snapshot: list[dict[str, object]] = []
    inodes: set[tuple[int, int]] = set()
    expected_paths = {"closure-manifest.json"}
    writable = 0
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeComparisonError("runtime file record is invalid")
        relative = record.get("path")
        mode = record.get("mode")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(mode, str)
        ):
            raise RuntimeComparisonError("runtime file record is invalid")
        path = root / relative
        try:
            raw = path.read_bytes()
            observed = path.lstat()
        except OSError as exc:
            raise RuntimeComparisonError("runtime file is unavailable") from exc
        observed_mode = stat.S_IMODE(observed.st_mode)
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or f"{observed_mode:04o}" != mode
            or len(raw) != record.get("size")
            or hashlib.sha256(raw).hexdigest() != record.get("sha256")
        ):
            raise RuntimeComparisonError("runtime file bytes or mode changed")
        writable += int(bool(observed_mode & 0o222))
        inodes.add((observed.st_dev, observed.st_ino))
        expected_paths.add(relative)
        snapshot.append(
            {"mode": mode, "path": relative, "sha256": record["sha256"], "size": record["size"]}
        )
    observed_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if observed_paths != expected_paths or writable:
        raise RuntimeComparisonError("runtime inventory or writable state changed")
    return (
        {
            "closure_manifest_sha256": manifest_sha256,
            "file_count": len(snapshot),
            "file_snapshot_sha256": hashlib.sha256(_canonical(snapshot)).hexdigest(),
            "writable_regular_files": writable,
        },
        inodes,
    )


def _validate_ledger(ledger: dict[str, object]) -> list[dict[str, object]]:
    entries = ledger.get("entries")
    if (
        set(ledger) != {"candidate_generation_id", "entries", "normalized_fields", "schema"}
        or ledger.get("schema") != "trading-agent-nautilus-approved-drift-ledger/v1"
        or ledger.get("candidate_generation_id") != "NT1231-U04-G1"
        or ledger.get("normalized_fields") != sorted(_NORMALIZED_FIELDS)
        or not isinstance(entries, list)
        or not all(isinstance(entry, dict) for entry in entries)
    ):
        raise RuntimeComparisonError("approved drift ledger is invalid")
    for entry in entries:
        if (
            set(entry)
            != {
                "candidate_semantic_sha256",
                "classification",
                "rationale",
                "rollback_semantic_sha256",
                "scenario_id",
            }
            or entry["classification"] not in _DRIFT_CLASSES
            or entry["scenario_id"] not in SCENARIO_IDS
            or not isinstance(entry["rationale"], str)
            or not entry["rationale"].strip()
        ):
            raise RuntimeComparisonError("approved drift entry is invalid")
    return entries


def qualify(commit: str, tree: str, rollback_version: str, generation_id: str) -> dict[str, object]:
    _u05._git_identity(commit, tree)
    generation = load_candidate_generation(GENERATION)
    u06_receipt = _u05._closed_json(U06_RECEIPT)
    rollback_receipt = _u05._closed_json(ROLLBACK_RECEIPT)
    ledger = _u05._closed_json(DRIFT_LEDGER)
    approvals = _validate_ledger(ledger)
    rollback = rollback_receipt.get("rollback_authority")
    if (
        generation_id != generation.generation_id
        or rollback_version != "1.227.0"
        or u06_receipt.get("verdict") != "PASS"
        or u06_receipt.get("candidate_generation_sha256") != generation.record_sha256
        or u06_receipt.get("candidate_closure_sha256") != generation.closure.manifest_sha256
        or not isinstance(rollback, dict)
        or rollback.get("engine_version") != rollback_version
        or rollback.get("schema_version") != 6
    ):
        raise RuntimeComparisonError("U06, G1, or rollback authority is mixed")
    policy = _u05._closed_json(_u05.POLICY)
    isolation = policy.get("external_cache_isolation")
    roots = isolation.get("external_roots") if isinstance(isolation, dict) else None
    candidate_path = roots.get("candidate_runtime_root") if isinstance(roots, dict) else None
    rollback_generation = rollback.get("generation")
    artifact_generation = rollback.get("artifact_generation")
    if not all(isinstance(value, str) for value in (candidate_path, rollback_generation, artifact_generation)):
        raise RuntimeComparisonError("runtime roots cannot be resolved from exact authority")
    candidate_root = Path(candidate_path)
    cache_root = candidate_root.parent / "nautilus"
    rollback_root = cache_root / str(rollback_generation)
    artifact_root = cache_root / "artifacts" / str(artifact_generation)
    if candidate_root.resolve() == rollback_root.resolve():
        raise RuntimeComparisonError("candidate and rollback roots are not disjoint")
    rollback_attestation = attest_nautilus_backtest_closure(
        NautilusClosureConfig(rollback_root, artifact_root, _u05.BWRAP),
        expected_profile="execution-simulation",
    )
    if rollback_attestation.closure_sha256 != rollback.get("closure_sha256"):
        raise RuntimeComparisonError("rollback closure digest is not accepted")
    candidate_before = _u05.snapshot_candidate_closure(
        candidate_root, generation.closure.manifest_sha256
    )
    rollback_before, rollback_inodes = _snapshot_runtime(
        rollback_root, str(rollback["closure_manifest_sha256"])
    )
    candidate_inodes = {
        (path.lstat().st_dev, path.lstat().st_ino)
        for path in candidate_root.rglob("*")
        if path.is_file()
    }
    shared_inodes = candidate_inodes & rollback_inodes
    if shared_inodes:
        raise RuntimeComparisonError("runtime roots share regular file state")
    runs: dict[str, dict[str, list[dict[str, object]]]] = {
        "rollback": {scenario: [] for scenario in SCENARIO_IDS},
        "candidate": {scenario: [] for scenario in SCENARIO_IDS},
    }
    for _attempt in range(3):
        for scenario_id in SCENARIO_IDS:
            _fixture, _request, oracle = _u06._oracle(scenario_id)
            expected = _u06._expected(oracle)
            for runtime_name, runtime_root in (
                ("rollback", rollback_root),
                ("candidate", candidate_root),
            ):
                document, _stderr = _u05._run_scenario(runtime_root, scenario_id)
                _u06.validate_candidate_outcome(document, expected)
                runs[runtime_name][scenario_id].append(document)
    comparisons: dict[str, object] = {}
    rollback_attempts: list[dict[str, str]] = [{}, {}, {}]
    candidate_attempts: list[dict[str, str]] = [{}, {}, {}]
    for scenario_id in SCENARIO_IDS:
        rollback_digest = require_deterministic_repeats(runs["rollback"][scenario_id])
        candidate_digest = require_deterministic_repeats(runs["candidate"][scenario_id])
        scenario_approvals = [entry for entry in approvals if entry["scenario_id"] == scenario_id]
        classification = classify_semantic_drift(
            runs["rollback"][scenario_id][0],
            runs["candidate"][scenario_id][0],
            scenario_approvals,
        )
        comparisons[scenario_id] = {
            "candidate_semantic_sha256": candidate_digest,
            "classification": classification,
            "rollback_semantic_sha256": rollback_digest,
        }
        for attempt in range(3):
            rollback_attempts[attempt][scenario_id] = semantic_digest(
                runs["rollback"][scenario_id][attempt]
            )
            candidate_attempts[attempt][scenario_id] = semantic_digest(
                runs["candidate"][scenario_id][attempt]
            )
    rollback_attempt_digests = [hashlib.sha256(_canonical(value)).hexdigest() for value in rollback_attempts]
    candidate_attempt_digests = [hashlib.sha256(_canonical(value)).hexdigest() for value in candidate_attempts]
    if len(set(rollback_attempt_digests)) != 1 or len(set(candidate_attempt_digests)) != 1:
        raise RuntimeComparisonError("runtime campaign is not internally deterministic")
    classifications = Counter(item["classification"] for item in comparisons.values())  # type: ignore[union-attr]
    if classifications["UNEXPLAINED_BLOCKER"]:
        raise RuntimeComparisonError("dual-runtime campaign has unexplained semantic drift")
    candidate_after = _u05.snapshot_candidate_closure(
        candidate_root, generation.closure.manifest_sha256
    )
    rollback_after, rollback_after_inodes = _snapshot_runtime(
        rollback_root, str(rollback["closure_manifest_sha256"])
    )
    if (
        candidate_before != candidate_after
        or rollback_before != rollback_after
        or rollback_inodes != rollback_after_inodes
    ):
        raise RuntimeComparisonError("runtime closure changed during comparison")
    return {
        "schema": "trading-agent-nautilus-dual-runtime-evidence/v1",
        "verdict": "PASS",
        "candidate_generation_id": generation.generation_id,
        "candidate_generation_sha256": generation.record_sha256,
        "candidate_closure_sha256": generation.closure.manifest_sha256,
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "input_receipt_sha256s": {
            "approved_drift_ledger": _sha(DRIFT_LEDGER),
            "rollback_isolation": _sha(ROLLBACK_RECEIPT),
            "u06_qualification": _sha(U06_RECEIPT),
        },
        "normalization": sorted(_NORMALIZED_FIELDS),
        "attempts_per_runtime": 3,
        "isolated_process_count": 6 * len(SCENARIO_IDS),
        "runtime_semantic_sha256s": {
            "rollback_1_227": rollback_attempt_digests[0],
            "candidate_1_231": candidate_attempt_digests[0],
        },
        "internal_semantic_digest_counts": {"rollback_1_227": 1, "candidate_1_231": 1},
        "drift_counts": {
            name: classifications[name]
            for name in ("NONE", "EXPECTED_UPSTREAM_FIX", "APPROVED_CONTRACT_CHANGE", "UNEXPLAINED_BLOCKER")
        },
        "scenarios": comparisons,
        "isolation": {
            "candidate_snapshot_before": candidate_before,
            "candidate_snapshot_after": candidate_after,
            "rollback_snapshot_before": rollback_before,
            "rollback_snapshot_after": rollback_after,
            "roots_pairwise_disjoint": True,
            "shared_regular_file_inodes": 0,
            "shared_writable_state": 0,
        },
        "authority_limits": {
            "candidate_active": False,
            "candidate_promoted": False,
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--qualification-source-commit", required=True)
    parser.add_argument("--qualification-source-tree", required=True)
    return parser.parse_args()


def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    args = _parse_args()
    try:
        evidence = qualify(
            args.qualification_source_commit,
            args.qualification_source_tree,
            args.rollback,
            args.generation,
        )
    except (RuntimeComparisonError, OSError, ValueError) as exc:
        _die(str(exc))
    sys.stdout.buffer.write(_pretty(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
