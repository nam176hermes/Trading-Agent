#!/usr/bin/env python3
"""Issue fail-closed Pre-P3 receipts from clean source and disposable proofs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.project_status import (
    derive_project_status,
    make_pass_receipt,
    receipt_sha256,
    validate_pass_receipt,
)
from scripts.qualify_p1_engine_lts import SAFE_AUTHORITY_LIMITS as P1_SAFE_AUTHORITY_LIMITS


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = {"broker": False, "live": False, "network": False, "production": False}


class QualificationError(ValueError):
    pass


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments), cwd=ROOT, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise QualificationError("git source identity is unavailable")
    return result.stdout.strip()


def _source() -> tuple[str, str]:
    if _git("status", "--porcelain"):
        raise QualificationError("qualification requires a clean source tree")
    return _git("rev-parse", "HEAD"), _git("rev-parse", "HEAD^{tree}")


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise QualificationError(f"required evidence is unavailable: {path}") from exc


def _run(*command: str) -> str:
    result = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )
    if result.returncode:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise QualificationError(f"qualification command failed: {' '.join(command)}")
    return result.stdout


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def _load_generic(path: Path, gate: str) -> dict[str, Any]:
    try:
        return validate_pass_receipt(json.loads(path.read_bytes()), gate)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise QualificationError(f"invalid {gate} receipt") from exc


def _same_source(receipts: tuple[dict[str, Any], ...]) -> tuple[str, str]:
    identities = {(item["source_sha"], item["source_tree"]) for item in receipts}
    if len(identities) != 1:
        raise QualificationError("qualification receipts do not bind one source")
    return next(iter(identities))


def p2_source(output: Path) -> None:
    source_sha, source_tree = _source()
    _run(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/data_platform",
        "tests/security_master",
        "--ignore=tests/security_master/test_postgres_runtime.py",
    )
    replay_outputs = tuple(
        _run(sys.executable, "scripts/certify_p2_data_platform.py")
        for _ in range(3)
    )
    if len(set(replay_outputs)) != 1:
        raise QualificationError("P2 subprocess replay is not deterministic")
    certification = json.loads(replay_outputs[0])
    if (
        certification.get("schema_version") != "p2-data-platform-certification-v2"
        or certification.get("repetitions") != 3
        or certification.get("query_parity") is not True
        or certification.get("pit_leakage_closed") is not True
        or certification.get("data_api_epoch") != 2
        or certification.get("migration_head") != "0019_p2_security_master"
    ):
        raise QualificationError("P2 deterministic certification did not pass")
    evidence = (
        certification["receipt_sha256"],
        _sha(ROOT / "alembic/versions/0019_p2_security_master.py"),
        _sha(ROOT / "packages/data_contracts/models.py"),
        _sha(ROOT / "packages/data_catalog/v3.py"),
        _sha(ROOT / "packages/security_master/projector.py"),
        _sha(ROOT / "docs/implementation/pre-p3/p2-pit-adversarial-suite-v1.json"),
        _sha(ROOT / "docs/operations/p2-data-platform-runbook.md"),
    )
    _write(
        output,
        make_pass_receipt(
            "P2_SOURCE_COMPLETE",
            source_sha=source_sha,
            source_tree=source_tree,
            evidence_sha256s=evidence,
        ),
    )


def p2_runtime(output: Path) -> None:
    source_sha, source_tree = _source()
    _run("make", "test-p2-runtime-postgres")
    version = _run("/usr/lib/postgresql/16/bin/postgres", "--version").strip()
    if not version.startswith("postgres (PostgreSQL) 16."):
        raise QualificationError("runtime qualification did not use PostgreSQL 16")
    evidence = (
        hashlib.sha256(version.encode()).hexdigest(),
        _sha(ROOT / "tests/security_master/test_postgres_runtime.py"),
        _sha(ROOT / "alembic/versions/0019_p2_security_master.py"),
    )
    _write(
        output,
        make_pass_receipt(
            "P2_RUNTIME_QUALIFIED",
            source_sha=source_sha,
            source_tree=source_tree,
            evidence_sha256s=evidence,
        ),
    )


def p2_final(source_path: Path, runtime_path: Path, output: Path) -> None:
    source = _load_generic(source_path, "P2_SOURCE_COMPLETE")
    runtime = _load_generic(runtime_path, "P2_RUNTIME_QUALIFIED")
    source_sha, source_tree = _same_source((source, runtime))
    _write(
        output,
        make_pass_receipt(
            "P2_QUALIFIED",
            source_sha=source_sha,
            source_tree=source_tree,
            evidence_sha256s=(source["receipt_sha256"], runtime["receipt_sha256"]),
        ),
    )


def p1_bridge(p1_lts_path: Path, p1_h_output: Path, p1_lts_output: Path) -> None:
    try:
        native = json.loads(p1_lts_path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise QualificationError("P1 LTS receipt is invalid") from exc
    nested = native.get("p1_h_complete")
    source = native.get("source")
    if (
        native.get("schema") != "trading-agent-p1-lts-ready/v1"
        or native.get("status") != "P1_LTS_READY"
        or native.get("result") != "PASS"
        or native.get("authority_limits") != P1_SAFE_AUTHORITY_LIMITS
        or native.get("execution_scope") != "PAPER_LOCAL_ONLY"
        or not isinstance(nested, dict)
        or nested.get("schema") != "trading-agent-p1-h-complete/v1"
        or nested.get("status") != "P1_H_COMPLETE"
        or nested.get("result") != "PASS"
        or nested.get("authority_limits") != P1_SAFE_AUTHORITY_LIMITS
        or nested.get("execution_scope") != "PAPER_LOCAL_ONLY"
        or nested.get("source") != source
        or native.get("p1_h_complete_sha256") != nested.get("receipt_sha256")
    ):
        raise QualificationError("P1 LTS receipt did not pass")
    native_digest = native.get("receipt_sha256")
    nested_digest = nested.get("receipt_sha256")
    if native_digest != hashlib.sha256(
        canonical_json_bytes({key: value for key, value in native.items() if key != "receipt_sha256"})
    ).hexdigest() or nested_digest != hashlib.sha256(
        canonical_json_bytes({key: value for key, value in nested.items() if key != "receipt_sha256"})
    ).hexdigest():
        raise QualificationError("P1 LTS receipt digest is invalid")
    if not isinstance(source, dict) or source.get("clean") is not True:
        raise QualificationError("P1 LTS source is not clean")
    p1_h = make_pass_receipt(
        "P1_H_COMPLETE",
        source_sha=source["commit"],
        source_tree=source["tree"],
        evidence_sha256s=(nested_digest,),
    )
    p1_lts = make_pass_receipt(
        "P1_LTS_READY",
        source_sha=source["commit"],
        source_tree=source["tree"],
        evidence_sha256s=(native_digest, p1_h["receipt_sha256"]),
    )
    _write(p1_h_output, p1_h)
    _write(p1_lts_output, p1_lts)


def p3_foundation(output_dir: Path) -> None:
    source_sha, source_tree = _source()
    _run(sys.executable, "-m", "pytest", "-q", "tests/alpha_lifecycle")
    specifications = {
        "P3_BASELINES_FROZEN": (
            ROOT / "docs/implementation/pre-p3/p3-baseline-suite-v1.json",
            ROOT / "packages/alpha_lifecycle/baselines.py",
            ROOT / "packages/alpha_lifecycle/metrics.py",
        ),
        "P3_EVALUATION_PROTOCOL_FROZEN": (
            ROOT / "docs/implementation/pre-p3/p3-evaluation-protocol-v1.json",
            ROOT / "packages/alpha_lifecycle/protocol.py",
        ),
        "ALPHA_REGISTRY_FOUNDATION": (
            ROOT / "packages/alpha_lifecycle/registry.py",
            ROOT / "tests/alpha_lifecycle/test_alpha_registry.py",
        ),
    }
    names = {
        "P3_BASELINES_FROZEN": "p3-baselines-frozen-v1.json",
        "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v1.json",
        "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v1.json",
    }
    for gate, paths in specifications.items():
        _write(
            output_dir / names[gate],
            make_pass_receipt(
                gate,
                source_sha=source_sha,
                source_tree=source_tree,
                evidence_sha256s=tuple(_sha(path) for path in paths),
            ),
        )


def pre_p3_final(
    receipt_dir: Path,
    output: Path,
    *,
    status_payload: dict[str, Any] | None = None,
) -> None:
    names = {
        "P1_H_COMPLETE": "p1-h-complete-v1.json",
        "P1_LTS_READY": "p1-lts-ready-v1.json",
        "P2_SOURCE_COMPLETE": "p2-source-complete-v1.json",
        "P2_RUNTIME_QUALIFIED": "p2-runtime-qualified-v1.json",
        "P2_QUALIFIED": "p2-qualified-v1.json",
        "P3_BASELINES_FROZEN": "p3-baselines-frozen-v1.json",
        "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v1.json",
        "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v1.json",
    }
    receipts = tuple(
        _load_generic(receipt_dir / name, gate) for gate, name in names.items()
    )
    source_sha, source_tree = _same_source(receipts)
    status = status_payload or derive_project_status(ROOT)
    required_status = {
        "P1_COMPLETE",
        "P1_H_COMPLETE",
        "P1_LTS_READY",
        "P2_QUALIFIED",
        "PROJECT_STATUS_AUTHORITY",
        "P3_BASELINES_FROZEN",
        "P3_EVALUATION_PROTOCOL_FROZEN",
        "ALPHA_REGISTRY_FOUNDATION",
    }
    if any(status["gates"].get(gate) != "PASS" for gate in required_status):
        raise QualificationError("canonical project status has not passed every final gate")
    if status["gates"].get("P0") != "P0_SOURCE_COMPLETE":
        raise QualificationError("canonical project status has not passed P0")
    if (
        status.get("live_eligible") is not False
        or status.get("live_enabled") is not False
        or set(status.get("authority", {}).values()) != {False}
    ):
        raise QualificationError("canonical project status grants forbidden authority")
    receipt_by_gate = {item["gate"]: item for item in receipts}
    payload: dict[str, Any] = {
        "authority": AUTHORITY,
        "bindings": {
            "ALPHA_REGISTRY_FOUNDATION": receipt_by_gate["ALPHA_REGISTRY_FOUNDATION"]["receipt_sha256"],
            "P1_COMPLETE": _sha(ROOT / "docs/implementation/p1-real-nautilus/P1-FINAL-CERTIFICATION.md"),
            "P1_H_COMPLETE": receipt_by_gate["P1_H_COMPLETE"]["receipt_sha256"],
            "P1_LTS_READY": receipt_by_gate["P1_LTS_READY"]["receipt_sha256"],
            "P2_QUALIFIED": receipt_by_gate["P2_QUALIFIED"]["receipt_sha256"],
            "P3_BASELINES_FROZEN": receipt_by_gate["P3_BASELINES_FROZEN"]["receipt_sha256"],
            "P3_EVALUATION_PROTOCOL_FROZEN": receipt_by_gate["P3_EVALUATION_PROTOCOL_FROZEN"]["receipt_sha256"],
            "PROJECT_STATUS_AUTHORITY": hashlib.sha256(canonical_json_bytes(status)).hexdigest(),
        },
        "live_eligible": False,
        "live_enabled": False,
        "p3_alpha_development_allowed": True,
        "schema_version": "pre-p3-certification-v1",
        "source_sha": source_sha,
        "source_tree": source_tree,
        "status": "PRE_P3_READY",
    }
    payload["receipt_sha256"] = receipt_sha256(payload)
    _write(output, payload)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("p2-source", "p2-runtime"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    final = commands.add_parser("p2-final")
    final.add_argument("--source", type=Path, required=True)
    final.add_argument("--runtime", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    bridge = commands.add_parser("p1-bridge")
    bridge.add_argument("--p1-lts", type=Path, required=True)
    bridge.add_argument("--p1-h-output", type=Path, required=True)
    bridge.add_argument("--p1-lts-output", type=Path, required=True)
    p3 = commands.add_parser("p3-foundation")
    p3.add_argument("--output-dir", type=Path, required=True)
    pre = commands.add_parser("final")
    pre.add_argument("--receipt-dir", type=Path, required=True)
    pre.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        if args.command == "p2-source":
            p2_source(args.output)
        elif args.command == "p2-runtime":
            p2_runtime(args.output)
        elif args.command == "p2-final":
            p2_final(args.source, args.runtime, args.output)
        elif args.command == "p1-bridge":
            p1_bridge(args.p1_lts, args.p1_h_output, args.p1_lts_output)
        elif args.command == "p3-foundation":
            p3_foundation(args.output_dir)
        else:
            pre_p3_final(args.receipt_dir, args.output)
    except QualificationError as exc:
        print(f"HELD: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
