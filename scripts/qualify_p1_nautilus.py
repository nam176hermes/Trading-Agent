#!/usr/bin/env python3
"""Run the closed P1 adversarial matrix and emit one evidence receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import NoReturn
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.job_worker.engine_profiles import P1_REAL_BACKTEST_POLICY


MATRIX = ROOT / "docs/implementation/p1-real-nautilus/adversarial-matrix.json"
MATRIX_SHA256 = "c05d8027e1d57d381247f8e7528588976336c774a7dac96b5e30748c087af263"
BASELINE = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/p1-engine-baseline-receipt.json"
)
PRODUCT_POLICY = ROOT / "engines/nautilus/p1-runtime-closure-policy.json"
P1_22_EVIDENCE = ROOT / "docs/implementation/p1-real-nautilus/e2e-evidence.md"
_BOUNDARIES = {
    "ACCOUNTING",
    "AUTHORITY",
    "CUSTODY",
    "EXECUTION",
    "INPUT_PROTOCOL",
    "PERSISTENCE",
    "REPLAY",
    "SEMANTIC_DRIFT",
}
_EXPECTED = {"EXPECTED_REJECTION_ASSERTED", "PASS"}
_CEILINGS = {
    "case_output_bytes",
    "case_peak_memory_kib",
    "case_runtime_seconds",
    "total_runtime_seconds",
}
_MATRIX_KEYS = {"ceilings", "scenarios", "schema"}
_SCENARIO_KEYS = {"boundary", "expected_class", "nodeids", "scenario"}
_PRODUCT_METRICS_SCENARIO = "three-run-custody-and-semantic-determinism"
_PRODUCT_METRIC_KEYS = {
    "closure_attestation_milliseconds",
    "native_execution_milliseconds",
    "product_peak_memory_kib",
    "replay_milliseconds",
    "runs",
    "schema",
}
_PRODUCT_RUN_KEYS = {
    "attempt_id",
    "batch_sha256",
    "event_count",
    "job_id",
    "output_bytes",
    "portfolio_state_sha256",
    "raw_portfolio_state_sha256",
    "request_sha256",
    "semantic_sha256",
}


class QualificationError(ValueError):
    """The adversarial matrix or observed evidence is incomplete."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise QualificationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_matrix(path: Path, *, root: Path = ROOT) -> dict[str, object]:
    if root == ROOT:
        try:
            status = path.lstat()
        except OSError as exc:
            raise QualificationError("canonical adversarial matrix is unavailable") from exc
        if (
            path.resolve() != MATRIX.resolve()
            or not stat.S_ISREG(status.st_mode)
            or stat.S_ISLNK(status.st_mode)
            or _sha(path) != MATRIX_SHA256
        ):
            raise QualificationError("canonical adversarial matrix identity is invalid")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError("adversarial matrix is unavailable or invalid") from exc
    if (
        type(document) is not dict
        or set(document) != _MATRIX_KEYS
        or document.get("schema") != "trading-agent-p1-adversarial-matrix/v1"
    ):
        raise QualificationError("adversarial matrix envelope is invalid")
    ceilings = document.get("ceilings")
    scenarios = document.get("scenarios")
    if (
        type(ceilings) is not dict
        or set(ceilings) != _CEILINGS
        or any(type(ceilings[name]) is not int or ceilings[name] <= 0 for name in _CEILINGS)
        or type(scenarios) is not list
        or not scenarios
    ):
        raise QualificationError("adversarial matrix limits or scenarios are invalid")
    names: set[str] = set()
    all_nodeids: set[str] = set()
    for scenario in scenarios:
        if type(scenario) is not dict or set(scenario) != _SCENARIO_KEYS:
            raise QualificationError("adversarial scenario shape is invalid")
        name = scenario.get("scenario")
        nodeids = scenario.get("nodeids")
        if type(nodeids) is list and any(nodeid in all_nodeids for nodeid in nodeids):
            raise QualificationError("adversarial pytest authority is duplicated")
        if (
            type(name) is not str
            or not name
            or name in names
            or scenario.get("boundary") not in _BOUNDARIES
            or scenario.get("expected_class") not in _EXPECTED
            or type(nodeids) is not list
            or not nodeids
            or len(nodeids) != len(set(nodeids))
            or any(type(nodeid) is not str or not nodeid for nodeid in nodeids)
        ):
            if name in names:
                raise QualificationError("adversarial scenario is duplicated")
            raise QualificationError("adversarial scenario authority is invalid")
        serialized = _canonical(scenario).decode("ascii").lower()
        if "skip" in serialized or "xfail" in serialized:
            raise QualificationError("skip or xfail authority is forbidden")
        for nodeid in nodeids:
            source_name = nodeid.split("::", 1)[0]
            source = Path(source_name)
            target = root / source
            try:
                source_status = target.lstat()
            except OSError as exc:
                raise QualificationError(
                    "adversarial pytest authority is unavailable"
                ) from exc
            if (
                source.is_absolute()
                or ".." in source.parts
                or not source_name.startswith("tests/")
                or not stat.S_ISREG(source_status.st_mode)
                or stat.S_ISLNK(source_status.st_mode)
                or not target.resolve().is_relative_to(root.resolve())
            ):
                raise QualificationError("adversarial pytest authority is invalid")
        names.add(name)
        all_nodeids.update(nodeids)
    if root == ROOT and {scenario["boundary"] for scenario in scenarios} != _BOUNDARIES:
        raise QualificationError("adversarial boundary coverage is incomplete")
    return document


def _counts(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise QualificationError("pytest evidence is unavailable") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise QualificationError("pytest evidence contains no suites")
    return {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("errors", "failures", "skipped", "tests")
    }


def _product_metrics(path: Path, ceilings: dict[str, object]) -> dict[str, object]:
    try:
        status = path.lstat()
        raw = path.read_bytes()
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError("P1 product metrics are unavailable or invalid") from exc
    runs = document.get("runs") if isinstance(document, dict) else None
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_ISLNK(status.st_mode)
        or len(raw) > 64 * 1024
        or type(document) is not dict
        or set(document) != _PRODUCT_METRIC_KEYS
        or document.get("schema") != "trading-agent-p1-product-metrics/v1"
        or type(runs) is not list
        or len(runs) != 3
        or any(type(run) is not dict or set(run) != _PRODUCT_RUN_KEYS for run in runs)
    ):
        raise QualificationError("P1 product metrics envelope is invalid")
    metric_values: dict[str, int] = {}
    for name in (
        "closure_attestation_milliseconds",
        "native_execution_milliseconds",
        "product_peak_memory_kib",
        "replay_milliseconds",
    ):
        value = document.get(name)
        if type(value) is not int or value < 0:
            raise QualificationError("P1 product metric value is invalid")
        metric_values[name] = value
    digest_fields = (
        "batch_sha256",
        "portfolio_state_sha256",
        "raw_portfolio_state_sha256",
        "request_sha256",
        "semantic_sha256",
    )
    if any(
        type(run["event_count"]) is not int
        or run["event_count"] <= 0
        or run["event_count"] > 4096
        or type(run["output_bytes"]) is not int
        or run["output_bytes"] <= 0
        or run["output_bytes"] > ceilings["case_output_bytes"]
        or type(run["job_id"]) is not str
        or not run["job_id"].startswith("job_")
        or type(run["attempt_id"]) is not str
        or not run["attempt_id"].startswith("attempt_")
        or any(
            type(run[field]) is not str
            or len(run[field]) != 64
            or any(character not in "0123456789abcdef" for character in run[field])
            for field in digest_fields
        )
        for run in runs
    ):
        raise QualificationError("P1 product run metric is invalid")
    for field in ("attempt_id", "batch_sha256", "job_id", "request_sha256"):
        if len({run[field] for run in runs}) != 3:
            raise QualificationError("P1 raw custody identities are not distinct")
    for field in ("event_count", "output_bytes", "portfolio_state_sha256", "semantic_sha256"):
        if len({run[field] for run in runs}) != 1:
            raise QualificationError("P1 semantic or portfolio result is not deterministic")
    case_peak_memory_kib = ceilings["case_peak_memory_kib"]
    case_runtime_seconds = ceilings["case_runtime_seconds"]
    if type(case_peak_memory_kib) is not int or type(case_runtime_seconds) is not int:
        raise QualificationError("P1 product metric ceiling is invalid")
    if metric_values["product_peak_memory_kib"] > case_peak_memory_kib:
        raise QualificationError("P1 product memory ceiling was exceeded")
    if sum(
        metric_values[name]
        for name in (
            "closure_attestation_milliseconds",
            "native_execution_milliseconds",
            "replay_milliseconds",
        )
    ) > case_runtime_seconds * 1000:
        raise QualificationError("P1 product runtime ceiling was exceeded")
    return document


def _source_identity(root: Path) -> tuple[str, str]:
    try:
        status = subprocess.check_output(
            (
                "/usr/bin/git",
                "status",
                "--porcelain",
                "--untracked-files=all",
            ),
            cwd=root,
            env={
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "PATH": "/usr/bin:/bin",
            },
            text=True,
        )
        values = tuple(
            subprocess.check_output(
                ("/usr/bin/git", "rev-parse", revision),
                cwd=root,
                env={
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_NO_REPLACE_OBJECTS": "1",
                    "PATH": "/usr/bin:/bin",
                },
                text=True,
            ).strip()
            for revision in ("HEAD", "HEAD^{tree}")
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise QualificationError("qualification source identity is unavailable") from exc
    if status:
        raise QualificationError("qualification source checkout is not clean")
    if any(len(value) != 40 or any(c not in "0123456789abcdef" for c in value) for value in values):
        raise QualificationError("qualification source identity is invalid")
    return values[0], values[1]


def _qualification_authority() -> dict[str, object]:
    try:
        baseline = json.loads(
            BASELINE.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
        policy = json.loads(
            PRODUCT_POLICY.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError("P1 baseline or product policy is unavailable") from exc
    limits = policy.get("authority_limits") if isinstance(policy, dict) else None
    baseline_limits = (
        baseline.get("authority_limits") if isinstance(baseline, dict) else None
    )
    legacy = (
        baseline.get("legacy_phase4_authority")
        if isinstance(baseline, dict)
        else None
    )
    legacy_digests = (
        "execution_simulation_policy_sha256",
        "job_worker_loader_sha256",
        "paper_compatibility_policy_sha256",
    )
    if (
        not isinstance(baseline, dict)
        or not isinstance(policy, dict)
        or baseline.get("schema")
        != "trading-agent-p1-engine-baseline-receipt/v1"
        or baseline.get("verdict") != "PASS"
        or baseline.get("status") != "P1_BASELINE_APPROVED"
        or baseline.get("scope") != "P1_A_AND_P1_B_ONLY"
        or baseline.get("operator_decision") != "PROMOTE_1_231_FOR_P1"
        or baseline.get("legacy_phase4_profiles_unchanged") is not True
        or baseline.get("p1_product_closure_schema") != 8
        or baseline_limits
        != {
            "candidate_active": False,
            "candidate_promoted": False,
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        }
        or not isinstance(legacy, dict)
        or legacy.get("active_policy_changes") != 0
        or legacy.get("engine_version") != "1.227.0"
        or legacy.get("schema_version") != 6
        or any(
            type(legacy.get(name)) is not str
            or len(legacy[name]) != 64
            or any(character not in "0123456789abcdef" for character in legacy[name])
            for name in legacy_digests
        )
        or policy.get("profile_manifest_schema_version") != 8
        or policy.get("p1_baseline_receipt_sha256") != _sha(BASELINE)
        or policy.get("candidate_generation_id")
        != baseline.get("candidate_generation_id")
        or policy.get("candidate_generation_sha256")
        != baseline.get("candidate_generation_sha256")
        or policy.get("candidate_closure_sha256")
        != baseline.get("candidate_closure_sha256")
        or P1_REAL_BACKTEST_POLICY.manifest_schema_version != 8
        or P1_REAL_BACKTEST_POLICY.p1_baseline_receipt_sha256 != _sha(BASELINE)
        or limits
        != {
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        }
    ):
        raise QualificationError("P1 baseline or product policy is mixed")
    return {
        "baseline_authority_limits": baseline_limits,
        "candidate_closure_sha256": baseline["candidate_closure_sha256"],
        "candidate_generation_id": baseline["candidate_generation_id"],
        "candidate_generation_sha256": baseline["candidate_generation_sha256"],
        "legacy_phase4_authority": legacy,
        "legacy_phase4_profiles_unchanged": True,
        "operator_decision": "PROMOTE_1_231_FOR_P1",
        "p1_baseline_receipt_sha256": _sha(BASELINE),
        "p1_product_closure_schema": 8,
        "p1_product_closure_sha256": P1_REAL_BACKTEST_POLICY.closure_sha256,
        "p1_product_policy_sha256": _sha(PRODUCT_POLICY),
        "p1_22_evidence_sha256": _sha(P1_22_EVIDENCE),
    }


def qualify(
    matrix_path: Path,
    *,
    root: Path = ROOT,
    environment: dict[str, str] | None = None,
    source_identity: tuple[str, str] | None = None,
) -> dict[str, object]:
    matrix = load_matrix(matrix_path, root=root)
    ceilings = matrix["ceilings"]
    scenarios = matrix["scenarios"]
    assert isinstance(ceilings, dict) and isinstance(scenarios, list)
    requires_native = any(
        "_native.py" in nodeid
        for scenario in scenarios
        for nodeid in scenario["nodeids"]
    )
    supplied = environment or {}
    if requires_native and not all(
        supplied.get(name)
        for name in ("P1_NAUTILUS_CLOSURE_MANIFEST", "P1_NAUTILUS_PYTHON")
    ):
        raise QualificationError("exact native G1 authority is required")
    commit, tree = source_identity or _source_identity(root)
    authority = _qualification_authority() if root == ROOT else {}
    started = time.monotonic()
    results: list[dict[str, object]] = []
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        nodeids = scenario["nodeids"]
        assert isinstance(nodeids, list)
        source_sha256s = {
            nodeid.split("::", 1)[0]: _sha(root / nodeid.split("::", 1)[0])
            for nodeid in nodeids
        }
        with tempfile.TemporaryDirectory(prefix="p1-qualification-", dir="/tmp") as temporary:
            junit = Path(temporary) / "pytest.xml"
            metrics_path = Path(temporary) / "product-metrics.json"
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--disable-warnings",
                "-o",
                "xfail_strict=true",
                f"--junitxml={junit}",
                *nodeids,
            ]
            case_started = time.monotonic()
            case_environment = dict(supplied)
            if scenario["scenario"] == _PRODUCT_METRICS_SCENARIO:
                case_environment["P1_QUALIFICATION_METRICS_PATH"] = str(metrics_path)
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=case_environment,
                    check=False,
                    capture_output=True,
                    timeout=ceilings["case_runtime_seconds"],
                )
            except subprocess.TimeoutExpired as exc:
                raise QualificationError(
                    f"scenario exceeded runtime ceiling: {scenario['scenario']}"
                ) from exc
            duration = time.monotonic() - case_started
            output_bytes = len(completed.stdout) + len(completed.stderr)
            peak_memory = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            counts = _counts(junit)
            if (
                completed.returncode != 0
                or counts["tests"] <= 0
                or counts["errors"]
                or counts["failures"]
                or counts["skipped"]
            ):
                raise QualificationError(
                    f"scenario did not pass exactly: {scenario['scenario']}"
                )
            if output_bytes > ceilings["case_output_bytes"]:
                raise QualificationError(
                    f"scenario exceeded output ceiling: {scenario['scenario']}"
                )
            if peak_memory > ceilings["case_peak_memory_kib"]:
                raise QualificationError(
                    f"scenario exceeded memory ceiling: {scenario['scenario']}"
                )
            if source_sha256s != {
                nodeid.split("::", 1)[0]: _sha(root / nodeid.split("::", 1)[0])
                for nodeid in nodeids
            }:
                raise QualificationError(
                    f"scenario source changed during execution: {scenario['scenario']}"
                )
            observed_code = (
                "PYTEST_PASS"
                if scenario["expected_class"] == "PASS"
                else "EXPECTED_REJECTION_CONFIRMED"
            )
            product_metrics = (
                _product_metrics(metrics_path, ceilings)
                if scenario["scenario"] == _PRODUCT_METRICS_SCENARIO
                else None
            )
            evidence = {
                "command": command,
                "duration_milliseconds": round(duration * 1000),
                "exit_status": completed.returncode,
                "output_bytes": output_bytes,
                "peak_memory_kib": peak_memory,
                "product_metrics": product_metrics,
                "source_sha256s": source_sha256s,
                "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
                "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                "test_counts": counts,
            }
            results.append(
                {
                    "boundary": scenario["boundary"],
                    "command": command,
                    "duration_milliseconds": round(duration * 1000),
                    "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
                    "exit_status": completed.returncode,
                    "expected_class": scenario["expected_class"],
                    "observed_code": observed_code,
                    "output_bytes": output_bytes,
                    "peak_memory_kib": peak_memory,
                    **(
                        {"product_metrics": product_metrics}
                        if product_metrics is not None
                        else {}
                    ),
                    "scenario": scenario["scenario"],
                    "source_sha256s": source_sha256s,
                    "stderr_sha256": evidence["stderr_sha256"],
                    "stdout_sha256": evidence["stdout_sha256"],
                    "test_counts": counts,
                }
            )
    total = time.monotonic() - started
    if total > ceilings["total_runtime_seconds"]:
        raise QualificationError("qualification exceeded total runtime ceiling")
    if source_identity is None and _source_identity(root) != (commit, tree):
        raise QualificationError("qualification source changed during execution")
    evidence = {
        "authority": authority,
        "ceilings": ceilings,
        "matrix_sha256": _sha(matrix_path),
        "scenario_count": len(results),
        "scenarios": results,
        "total_runtime_milliseconds": round(total * 1000),
    }
    return {
        "authority_limits": {
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
        **authority,
        "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        "matrix_sha256": _sha(matrix_path),
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "scenarios": results,
        "schema": "trading-agent-p1-adversarial-qualification/v1",
        "total_runtime_milliseconds": round(total * 1000),
        "verdict": "PASS",
    }


def _abort(message: str) -> NoReturn:
    print(f"P1 adversarial qualification failed: {message}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    arguments = parser.parse_args(argv)
    native_names = ("P1_NAUTILUS_CLOSURE_MANIFEST", "P1_NAUTILUS_PYTHON")
    native_values = tuple(os.environ.get(name) for name in native_names)
    if not any(native_values):
        print(
            json.dumps(
                {
                    "authority_limits": {
                        "live_authorized": False,
                        "network_trading_authorized": False,
                        "production_authorized": False,
                    },
                    "schema": "trading-agent-p1-adversarial-qualification/v1",
                    "verdict": "DEFERRED",
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if not all(native_values):
        _abort("native G1 authority is partial or invalid")
    environment = {
        name: value
        for name in (
            *native_names,
            "PYTHONHASHSEED",
        )
        if (value := os.environ.get(name)) is not None
    }
    uv = shutil.which("uv")
    if uv is None:
        _abort("uv executable is unavailable")
    environment["PATH"] = f"{Path(uv).parent}:/usr/bin:/bin"
    try:
        receipt = qualify(arguments.matrix, environment=environment)
    except QualificationError as exc:
        _abort(str(exc))
    print(json.dumps(receipt, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
