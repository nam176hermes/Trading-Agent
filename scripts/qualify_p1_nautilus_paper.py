#!/usr/bin/env python3
"""Qualify the closed P1 backtest/local-paper evidence chain."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import NoReturn, TypedDict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qualify_p1_nautilus import (
    QualificationError,
    _canonical,
    _counts,
    _qualification_authority,
    _sha,
    _source_identity,
)


P1 = ROOT / "docs/implementation/p1-real-nautilus"
LEDGER = P1 / "task-ledger.md"
P1_23 = P1 / "p1-23-adversarial-qualification-receipt.json"
P1_24 = P1 / "p1-24-release-readiness-receipt.json"
P1_A_REVIEW = P1 / "P1-A-FINAL-REVIEW.md"
P1_29_COMMIT = "e7e125ba5b77433c68ae103c5b19cc995dbc58f7"
P1_29_TREE = "140fa5f21dd9501ea1c6cd055f9df807ab601772"
_P1_29_LEDGER_ROW = "| P1-29 | P1-27, P1-28 | ACCEPTED |"
_NATIVE_ENV = (
    "P1_NAUTILUS_CLOSURE_MANIFEST",
    "P1_NAUTILUS_PRODUCT_LINEAGE",
    "P1_NAUTILUS_PYTHON",
)
_SAFE = {
    "live_authorized": False,
    "network_trading_authorized": False,
    "production_authorized": False,
}


class _SuiteSpec(TypedDict):
    id: str
    nodeids: tuple[str, ...]


class _SuiteResult(TypedDict):
    suite: str
    duration_milliseconds: int
    evidence_sha256: str
    source_sha256: str
    test_counts: dict[str, int]


_SUITES: tuple[_SuiteSpec, ...] = (
    {"id": "paper-recovery", "nodeids": ("tests/paper_runtime",)},
    {
        "id": "exact-g1-backtest-paper-parity",
        "nodeids": ("tests/p1_nautilus/test_paper_runtime_native.py",),
    },
)


def _suite_source_sha256(nodeids: tuple[str, ...]) -> str:
    paths: dict[str, str] = {}
    for nodeid in nodeids:
        path = ROOT / nodeid.split("::", 1)[0]
        files = tuple(path.rglob("*.py")) if path.is_dir() else (path,)
        for source in files:
            paths[str(source.relative_to(ROOT))] = _sha(source)
    if not paths:
        raise QualificationError("paper qualification source is unavailable")
    return hashlib.sha256(_canonical(paths)).hexdigest()


def _run_suite(
    suite: _SuiteSpec, environment: dict[str, str]
) -> _SuiteResult:
    nodeids = suite["nodeids"]
    assert isinstance(nodeids, tuple)
    source_sha256 = _suite_source_sha256(nodeids)
    with tempfile.TemporaryDirectory(prefix="p1-paper-qualification-", dir="/tmp") as temporary:
        junit = Path(temporary) / "pytest.xml"
        command = (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--disable-warnings",
            "-o",
            "xfail_strict=true",
            f"--junitxml={junit}",
            *nodeids,
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            raise QualificationError(f"paper suite timed out: {suite['id']}") from exc
        counts = _counts(junit)
        if (
            completed.returncode
            or counts["tests"] <= 0
            or counts["errors"]
            or counts["failures"]
            or counts["skipped"]
        ):
            raise QualificationError(f"paper suite did not pass exactly: {suite['id']}")
        if _suite_source_sha256(nodeids) != source_sha256:
            raise QualificationError(f"paper suite source changed: {suite['id']}")
        evidence = {
            "command": command,
            "source_sha256": source_sha256,
            "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
            "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
            "test_counts": counts,
        }
        return {
            "suite": suite["id"],
            "duration_milliseconds": round((time.monotonic() - started) * 1000),
            "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
            "source_sha256": source_sha256,
            "test_counts": counts,
        }


def qualify(
    *,
    environment: dict[str, str],
    source_identity: tuple[str, str] | None = None,
) -> dict[str, object]:
    if _P1_29_LEDGER_ROW not in LEDGER.read_text(encoding="utf-8"):
        raise QualificationError("P1-29 acceptance authority is unavailable")
    commit, tree = source_identity or _source_identity(ROOT)
    authority = _qualification_authority()
    results: list[_SuiteResult] = [
        _run_suite(suite, environment) for suite in _SUITES
    ]
    if any(
        result["test_counts"][name]
        for result in results
        for name in ("errors", "failures", "skipped")
    ):
        raise QualificationError("paper suites did not pass exactly")
    if source_identity is None and _source_identity(ROOT) != (commit, tree):
        raise QualificationError("qualification source changed during execution")
    test_count = sum(result["test_counts"]["tests"] for result in results)
    evidence = {
        "p1_29_ledger_sha256": _sha(LEDGER),
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "suites": results,
    }
    return {
        "authority_limits": _SAFE,
        **authority,
        "engine_version": "1.231.0",
        "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        "p1_23_acceptance_sha256": _sha(P1_23),
        "p1_24_acceptance_sha256": _sha(P1_24),
        "p1_29_ledger_sha256": _sha(LEDGER),
        "p1_29_source_commit": P1_29_COMMIT,
        "p1_29_source_tree": P1_29_TREE,
        "p1_a_final_review_sha256": _sha(P1_A_REVIEW),
        "paper_protocol": "nautilus-paper-session-v2",
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "schema": "trading-agent-p1-paper-qualification/v1",
        "skipped_count": 0,
        "status": "P1_LOCAL_SOURCE_CERTIFIED",
        "suites": results,
        "test_count": test_count,
        "verdict": "PASS",
    }


def _abort(message: str) -> NoReturn:
    print(f"P1 paper qualification failed: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    supplied = tuple(os.environ.get(name) for name in _NATIVE_ENV)
    if not any(supplied):
        print(
            json.dumps(
                {
                    "authority_limits": _SAFE,
                    "schema": "trading-agent-p1-paper-qualification/v1",
                    "verdict": "DEFERRED",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if not all(supplied):
        _abort("native G1 authority is partial or invalid")
    uv = shutil.which("uv")
    if uv is None:
        _abort("uv executable is unavailable")
    environment = {
        name: value
        for name in (*_NATIVE_ENV, "PYTHONHASHSEED")
        if (value := os.environ.get(name)) is not None
    }
    environment["PATH"] = f"{Path(uv).parent}:/usr/bin:/bin"
    try:
        receipt = qualify(environment=environment)
    except (OSError, QualificationError) as exc:
        _abort(str(exc))
    print(json.dumps(receipt, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
