#!/usr/bin/env python3
"""Issue fail-closed Pre-P3 receipts from clean source and disposable proofs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.pre_p3_provenance import (
    canonical_source_identity,
    make_candidate_certificate,
    make_promotion_receipt,
    make_v2_gate_receipt,
    source_matches_current,
    validate_v2_gate_receipt,
)
from packages.project_status import (
    LEGACY_RECEIPTS,
    RECEIPTS,
    derive_project_status,
    make_pass_receipt,
    receipt_sha256,
    validate_pass_receipt,
)
from scripts.qualify_p1_engine_lts import SAFE_AUTHORITY_LIMITS as P1_SAFE_AUTHORITY_LIMITS


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = {"broker": False, "live": False, "network": False, "production": False}
P1_QUALIFICATION_OPERATION = "p2-security-master-runtime-green-v1"
_P1_EXTERNAL_OUTCOMES = {
    "EXT-DISPOSABLE-PG-GREEN": "DEFERRED",
    "EXT-DISPOSABLE-PG-RED": "DEFERRED",
    "EXT-DISPOSABLE-PG-RED-EVIDENCE": "DEFERRED",
    "EXT-LEGACY-UV-AUTHORITY": "PASS",
    "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": "PASS",
    "EXT-PHASE3B-CORPUS": "PASS",
}


class QualificationError(ValueError):
    pass


def _git(*arguments: str) -> str:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
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


def _write(
    path: Path, payload: dict[str, Any], *, trailing_newline: bool = True
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(canonical_json_bytes(payload) + (b"\n" if trailing_newline else b""))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise QualificationError(f"receipt output already exists: {path}") from exc
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            path.unlink(missing_ok=True)
        raise QualificationError(f"receipt output is unavailable: {path}") from exc


def _read_receipt(path: Path) -> bytes:
    try:
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise QualificationError(f"receipt input is not a regular file: {path}")
        return path.read_bytes()
    except OSError as exc:
        raise QualificationError(f"receipt input is unavailable: {path}") from exc


def _load_generic(path: Path, gate: str) -> dict[str, Any]:
    try:
        return validate_pass_receipt(json.loads(_read_receipt(path)), gate)
    except QualificationError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise QualificationError(f"invalid {gate} receipt") from exc


def _same_source(receipts: tuple[dict[str, Any], ...]) -> tuple[str, str]:
    identities = {(item["source_sha"], item["source_tree"]) for item in receipts}
    if len(identities) != 1:
        raise QualificationError("qualification receipts do not bind one source")
    return next(iter(identities))


def qualification_metadata() -> dict[str, str]:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isdigit() or run_id.startswith("0") or not run_attempt.isdigit() or run_attempt.startswith("0"):
        raise QualificationError("v2 qualification requires a real CI run identity")
    return {
        "completed_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "producer": "scripts/qualify_pre_p3.py",
        "run_attempt": run_attempt,
        "run_id": run_id,
    }


def _source_v2() -> dict[str, str]:
    if _git("status", "--porcelain"):
        raise QualificationError("qualification requires a clean source tree")
    try:
        return canonical_source_identity(ROOT, "HEAD")
    except ValueError as exc:
        raise QualificationError("canonical source identity is unavailable") from exc


def p1_external_v1(
    topology_path: Path,
    output_dir: Path,
    *,
    operation: str,
) -> None:
    source = _source_v2()
    qualification = qualification_metadata()
    try:
        topology_raw = _read_receipt(topology_path)
        topology = json.loads(topology_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError("P1 topology receipt is invalid") from exc
    if (
        not isinstance(topology, dict)
        or topology_raw != canonical_json_bytes(topology) + b"\n"
        or set(topology)
        != {
            "external_outcomes",
            "foundation_head_sha",
            "foundation_run_id",
            "lane",
            "native_status",
            "outcome",
            "portable_closure_status",
            "schema",
        }
        or topology.get("external_outcomes") != _P1_EXTERNAL_OUTCOMES
        or topology.get("foundation_head_sha") != source["commit_sha"]
        or topology.get("foundation_run_id") != qualification["run_id"]
        or topology.get("lane") != "P1_U04_HOST_TOPOLOGY"
        or topology.get("native_status") != "PASS"
        or topology.get("outcome") != "PASS"
        or topology.get("portable_closure_status") != "PASS"
        or topology.get("schema") != "p1-u04-host-topology-receipt-v1"
    ):
        raise QualificationError("P1 topology receipt did not pass")

    expected_context = {
        "GITHUB_ACTOR": "nam176hermes",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "nam176hermes/Trading-Agent",
        "GITHUB_SHA": source["commit_sha"],
        "GITHUB_WORKFLOW": "Host Authority",
        "GITHUB_WORKFLOW_REF": (
            "nam176hermes/Trading-Agent/.github/workflows/"
            "host-authority.yml@refs/heads/main"
        ),
        "GITHUB_WORKFLOW_SHA": source["commit_sha"],
    }
    if operation != P1_QUALIFICATION_OPERATION or any(
        os.environ.get(name) != value for name, value in expected_context.items()
    ):
        raise QualificationError("P1 operator run context is invalid")

    decision = {
        "actor": expected_context["GITHUB_ACTOR"],
        "authority": dict(AUTHORITY),
        "event": expected_context["GITHUB_EVENT_NAME"],
        "operation": operation,
        "ref": expected_context["GITHUB_REF"],
        "repository": expected_context["GITHUB_REPOSITORY"],
        "run_attempt": qualification["run_attempt"],
        "run_id": qualification["run_id"],
        "schema_version": "pre-p3-operator-decision-v1",
        "sha": source["commit_sha"],
        "workflow": expected_context["GITHUB_WORKFLOW"],
        "workflow_ref": expected_context["GITHUB_WORKFLOW_REF"],
        "workflow_sha": expected_context["GITHUB_WORKFLOW_SHA"],
    }
    topology_sha256 = hashlib.sha256(topology_raw).hexdigest()
    decision_sha256 = hashlib.sha256(canonical_json_bytes(decision) + b"\n").hexdigest()
    specifications = {
        "p1-foundation-proof-v1.json": (
            "trading-agent-p1-lts-foundation-proof/v1",
            "PASS",
            topology_sha256,
        ),
        "p1-native-proof-v1.json": (
            "trading-agent-p1-lts-native-proof/v1",
            "PASS",
            topology_sha256,
        ),
        "p1-operator-acceptance-v1.json": (
            "trading-agent-p1-lts-operator-acceptance/v1",
            "ACCEPT",
            decision_sha256,
        ),
    }
    outputs = (output_dir / "p1-operator-decision-v1.json",) + tuple(
        output_dir / name for name in specifications
    )
    if any(os.path.lexists(path) for path in outputs):
        raise QualificationError("P1 external receipt output already exists")
    _write(outputs[0], decision)
    for name, (schema, verdict, evidence_sha256) in specifications.items():
        _write(
            output_dir / name,
            {
                "authority_limits": dict(P1_SAFE_AUTHORITY_LIMITS),
                "evidence_sha256s": [evidence_sha256],
                "execution_scope": "PAPER_LOCAL_ONLY",
                "schema": schema,
                "source_commit": source["commit_sha"],
                "source_tree": source["tree_sha"],
                "verdict": verdict,
            },
            trailing_newline=False,
        )


def _tracked_evidence(path: Path, name: str) -> dict[str, str]:
    try:
        locator = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise QualificationError("tracked evidence path escapes the repository") from exc
    return {
        "kind": "TRACKED_BLOB",
        "locator": locator,
        "name": name,
        "sha256": _sha(path),
    }


def _load_v2(path: Path, gate: str) -> dict[str, Any]:
    try:
        return validate_v2_gate_receipt(json.loads(_read_receipt(path)), gate, root=ROOT)
    except QualificationError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise QualificationError(f"invalid v2 {gate} receipt") from exc


def p2_final_v2(
    source: dict[str, Any],
    runtime: dict[str, Any],
    *,
    qualification: dict[str, str],
) -> dict[str, Any]:
    validate_v2_gate_receipt(source, "P2_SOURCE_COMPLETE")
    validate_v2_gate_receipt(runtime, "P2_RUNTIME_QUALIFIED")
    if source["source"] != runtime["source"]:
        raise QualificationError("v2 P2 receipts do not bind one source")
    return make_v2_gate_receipt(
        "P2_QUALIFIED",
        source=source["source"],
        evidence=tuple(
            {
                "kind": "DERIVED_RECEIPT",
                "locator": gate,
                "name": gate.lower().replace("_", "-"),
                "sha256": receipt["receipt_sha256"],
            }
            for gate, receipt in (
                ("P2_SOURCE_COMPLETE", source),
                ("P2_RUNTIME_QUALIFIED", runtime),
            )
        ),
        qualification=qualification,
    )


def p2_source_v2(output: Path, *, qualification: dict[str, str]) -> None:
    source = _source_v2()
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
        _run(sys.executable, "scripts/certify_p2_data_platform.py") for _ in range(3)
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
    evidence = [
        {
            "kind": "DERIVED_RECEIPT",
            "locator": "scripts/certify_p2_data_platform.py",
            "name": "p2-data-platform-certification",
            "sha256": certification["receipt_sha256"],
        }
    ]
    evidence.extend(
        _tracked_evidence(path, name)
        for path, name in (
            (ROOT / "alembic/versions/0019_p2_security_master.py", "p2-migration"),
            (ROOT / "packages/data_contracts/models.py", "data-contracts"),
            (ROOT / "packages/data_catalog/v3.py", "data-catalog"),
            (ROOT / "packages/security_master/projector.py", "security-master"),
            (
                ROOT / "docs/implementation/pre-p3/p2-pit-adversarial-suite-v1.json",
                "p2-pit-adversarial-suite",
            ),
            (ROOT / "docs/operations/p2-data-platform-runbook.md", "p2-runbook"),
        )
    )
    _write(
        output,
        make_v2_gate_receipt(
            "P2_SOURCE_COMPLETE",
            source=source,
            evidence=tuple(evidence),
            qualification=qualification,
        ),
    )


def p2_runtime_v2(output: Path, *, qualification: dict[str, str]) -> None:
    source = _source_v2()
    _run("make", "test-p2-runtime-postgres")
    version = _run("/usr/lib/postgresql/16/bin/postgres", "--version").strip()
    if not version.startswith("postgres (PostgreSQL) 16."):
        raise QualificationError("runtime qualification did not use PostgreSQL 16")
    evidence = (
        {
            "kind": "TOOL_IDENTITY",
            "locator": "/usr/lib/postgresql/16/bin/postgres --version",
            "name": "postgres-16",
            "sha256": hashlib.sha256(version.encode()).hexdigest(),
        },
        _tracked_evidence(
            ROOT / "tests/security_master/test_postgres_runtime.py", "p2-runtime-test"
        ),
        _tracked_evidence(
            ROOT / "alembic/versions/0019_p2_security_master.py", "p2-migration"
        ),
    )
    _write(
        output,
        make_v2_gate_receipt(
            "P2_RUNTIME_QUALIFIED",
            source=source,
            evidence=evidence,
            qualification=qualification,
        ),
    )


def p3_foundation_v2(output_dir: Path, *, qualification: dict[str, str]) -> None:
    source = _source_v2()
    _run(sys.executable, "-m", "pytest", "-q", "tests/alpha_lifecycle")
    specifications = {
        "P3_BASELINES_FROZEN": (
            (ROOT / "docs/implementation/pre-p3/p3-baseline-suite-v1.json", "p3-baselines"),
            (ROOT / "packages/alpha_lifecycle/baselines.py", "alpha-baselines"),
            (ROOT / "packages/alpha_lifecycle/metrics.py", "alpha-metrics"),
        ),
        "P3_EVALUATION_PROTOCOL_FROZEN": (
            (
                ROOT / "docs/implementation/pre-p3/p3-evaluation-protocol-v1.json",
                "p3-evaluation-protocol",
            ),
            (ROOT / "packages/alpha_lifecycle/protocol.py", "alpha-protocol"),
        ),
        "ALPHA_REGISTRY_FOUNDATION": (
            (ROOT / "packages/alpha_lifecycle/registry.py", "alpha-registry"),
            (ROOT / "tests/alpha_lifecycle/test_alpha_registry.py", "alpha-registry-test"),
        ),
    }
    names = {
        "P3_BASELINES_FROZEN": "p3-baselines-frozen-v2.json",
        "P3_EVALUATION_PROTOCOL_FROZEN": "p3-evaluation-protocol-frozen-v2.json",
        "ALPHA_REGISTRY_FOUNDATION": "alpha-registry-foundation-v2.json",
    }
    for gate, paths in specifications.items():
        _write(
            output_dir / names[gate],
            make_v2_gate_receipt(
                gate,
                source=source,
                evidence=tuple(_tracked_evidence(path, name) for path, name in paths),
                qualification=qualification,
            ),
        )


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
    _native, _nested, source, native_digest, nested_digest = _load_p1_native(
        p1_lts_path
    )
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


def _load_p1_native(
    p1_lts_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str]:
    try:
        native = json.loads(_read_receipt(p1_lts_path))
    except QualificationError:
        raise
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
    return native, nested, source, native_digest, nested_digest


def p1_bridge_v2(
    p1_lts_path: Path,
    p1_h_output: Path,
    p1_lts_output: Path,
    *,
    qualification: dict[str, str],
) -> None:
    _native, _nested, native_source, native_digest, nested_digest = _load_p1_native(
        p1_lts_path
    )
    try:
        source = canonical_source_identity(ROOT, native_source["commit"])
    except ValueError as exc:
        raise QualificationError("P1 native source commit is unavailable") from exc
    if source["tree_sha"] != native_source["tree"]:
        raise QualificationError("P1 native source tree binding is invalid")
    p1_h = make_v2_gate_receipt(
        "P1_H_COMPLETE",
        source=source,
        evidence=(
            {
                "kind": "EXTERNAL_RECEIPT",
                "locator": "p1-h-complete-native-v1",
                "name": "p1-h-native",
                "sha256": nested_digest,
            },
        ),
        qualification=qualification,
    )
    p1_lts = make_v2_gate_receipt(
        "P1_LTS_READY",
        source=source,
        evidence=(
            {
                "kind": "EXTERNAL_RECEIPT",
                "locator": "p1-lts-ready-native-v1",
                "name": "p1-lts-native",
                "sha256": native_digest,
            },
            {
                "kind": "DERIVED_RECEIPT",
                "locator": "P1_H_COMPLETE",
                "name": "p1-h-complete",
                "sha256": p1_h["receipt_sha256"],
            },
        ),
        qualification=qualification,
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


def candidate_v2(
    receipt_dir: Path,
    legacy_receipt_dir: Path,
    output: Path,
    *,
    base_sha: str,
    promotion_type: str,
    qualification: dict[str, str],
) -> None:
    receipts = {
        gate: _load_v2(receipt_dir / name, gate) for gate, name in RECEIPTS.items()
    }
    sources = {canonical_json_bytes(receipt["source"]) for receipt in receipts.values()}
    if len(sources) != 1:
        raise QualificationError("v2 candidate receipts do not bind one source")
    source = next(iter(receipts.values()))["source"]
    if not source_matches_current(ROOT, source):
        raise QualificationError("v2 candidate source closure is not current")
    legacy: dict[str, str] = {}
    for gate, name in LEGACY_RECEIPTS.items():
        path = legacy_receipt_dir / name
        receipt = _load_generic(path, gate)
        legacy[gate] = hashlib.sha256(_read_receipt(path)).hexdigest()
    candidate = make_candidate_certificate(
        receipts=receipts,
        legacy_receipts=legacy,
        qualification=qualification,
        destination={
            "base_sha": base_sha,
            "promotion_type": promotion_type,
            "ref": "refs/heads/main",
            "repository": "nam176hermes/Trading-Agent",
        },
    )
    if _git("merge-base", base_sha, source["commit_sha"]) != base_sha:
        raise QualificationError("candidate base is not an ancestor of qualified source")
    _write(output, candidate)


def promotion_v1(
    candidate_path: Path,
    output: Path,
    *,
    promoted_revision: str,
) -> None:
    if _git("status", "--porcelain"):
        raise QualificationError("promotion requires a clean source tree")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isdigit() or run_id.startswith("0") or not run_attempt.isdigit() or run_attempt.startswith("0"):
        raise QualificationError("promotion requires a real CI run identity")
    try:
        payload = make_promotion_receipt(
            root=ROOT,
            candidate_path=candidate_path,
            promoted_revision=promoted_revision,
            run={
                "event": os.environ.get("GITHUB_EVENT_NAME", ""),
                "ref": os.environ.get("GITHUB_REF", ""),
                "repository": os.environ.get("GITHUB_REPOSITORY", ""),
                "run_attempt": run_attempt,
                "run_id": run_id,
                "sha": os.environ.get("GITHUB_SHA", ""),
                "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
                "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF", ""),
                "workflow_sha": os.environ.get("GITHUB_WORKFLOW_SHA", ""),
            },
        )
    except ValueError as exc:
        raise QualificationError("promotion provenance validation failed") from exc
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
    p1_external = commands.add_parser("p1-external-v1")
    p1_external.add_argument("--topology", type=Path, required=True)
    p1_external.add_argument("--output-dir", type=Path, required=True)
    p1_external.add_argument("--operation", required=True)
    for name in ("p2-source-v2", "p2-runtime-v2"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    final_v2 = commands.add_parser("p2-final-v2")
    final_v2.add_argument("--source", type=Path, required=True)
    final_v2.add_argument("--runtime", type=Path, required=True)
    final_v2.add_argument("--output", type=Path, required=True)
    bridge_v2 = commands.add_parser("p1-bridge-v2")
    bridge_v2.add_argument("--p1-lts", type=Path, required=True)
    bridge_v2.add_argument("--p1-h-output", type=Path, required=True)
    bridge_v2.add_argument("--p1-lts-output", type=Path, required=True)
    p3_v2 = commands.add_parser("p3-foundation-v2")
    p3_v2.add_argument("--output-dir", type=Path, required=True)
    candidate = commands.add_parser("candidate-v2")
    candidate.add_argument("--receipt-dir", type=Path, required=True)
    candidate.add_argument("--legacy-receipt-dir", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--base-sha", required=True)
    candidate.add_argument(
        "--promotion-type",
        choices=("SQUASH", "REBASE", "CHERRY_PICK", "CONTROLLED_RELEASE"),
        required=True,
    )
    promotion = commands.add_parser("promotion-v1")
    promotion.add_argument("--candidate", type=Path, required=True)
    promotion.add_argument("--output", type=Path, required=True)
    promotion.add_argument("--promoted-revision", default="HEAD")
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
        elif args.command == "final":
            pre_p3_final(args.receipt_dir, args.output)
        elif args.command == "p1-external-v1":
            p1_external_v1(
                args.topology,
                args.output_dir,
                operation=args.operation,
            )
        elif args.command == "p2-source-v2":
            p2_source_v2(args.output, qualification=qualification_metadata())
        elif args.command == "p2-runtime-v2":
            p2_runtime_v2(args.output, qualification=qualification_metadata())
        elif args.command == "p2-final-v2":
            p2_final_receipt = p2_final_v2(
                _load_v2(args.source, "P2_SOURCE_COMPLETE"),
                _load_v2(args.runtime, "P2_RUNTIME_QUALIFIED"),
                qualification=qualification_metadata(),
            )
            _write(args.output, p2_final_receipt)
        elif args.command == "p1-bridge-v2":
            p1_bridge_v2(
                args.p1_lts,
                args.p1_h_output,
                args.p1_lts_output,
                qualification=qualification_metadata(),
            )
        elif args.command == "p3-foundation-v2":
            p3_foundation_v2(args.output_dir, qualification=qualification_metadata())
        elif args.command == "candidate-v2":
            candidate_v2(
                args.receipt_dir,
                args.legacy_receipt_dir,
                args.output,
                base_sha=args.base_sha,
                promotion_type=args.promotion_type,
                qualification=qualification_metadata(),
            )
        else:
            promotion_v1(
                args.candidate,
                args.output,
                promoted_revision=args.promoted_revision,
            )
    except QualificationError as exc:
        print(f"HELD: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
