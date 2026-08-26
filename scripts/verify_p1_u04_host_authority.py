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


def _emit(outcome: str, reason: str) -> int:
    print(
        json.dumps(
            {
                "lane": "HOST_EXTERNAL_AUTHORITY",
                "outcome": outcome,
                "reason": reason,
                "schema": SCHEMA,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return {"PASS": 0, "FAIL": 1, "DEFERRED": 3}[outcome]


def _load_engine_policy() -> dict[str, object]:
    return json.loads(builder._CANDIDATE_ENGINE_POLICY.read_text(encoding="ascii"))


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
    arguments = parser.parse_args(argv)
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
