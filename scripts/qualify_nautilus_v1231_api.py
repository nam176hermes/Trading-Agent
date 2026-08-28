#!/usr/bin/env python3
"""Qualify all U01 API surfaces and critical callbacks on exact G1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts import canonical_json_bytes
from packages.nautilus_backtest import (
    build_canonical_simulation_fixture,
    build_simulation_envelope,
)
from packages.nautilus_upgrade_authority import load_candidate_generation


GENERATION = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/candidate-generations"
    / "NT1231-U04-G1.json"
)
U04_ACCEPTANCE = (
    ROOT
    / "docs/implementation/p1-real-nautilus/upgrade/u04-final-acceptance-receipt.json"
)
CONTRACT = (
    ROOT / "docs/implementation/p1-real-nautilus/upgrade/direct-api-contract.json"
)
GOLDEN = ROOT / "tests/fixtures/nautilus_upgrade/v1.231-api-probe.json"
POLICY = ROOT / "engines/nautilus/candidates/v1.231/engine-build-policy.json"
PROBE = ROOT / "engines/nautilus/launcher/nautilus_v1231_probe.py"
LAUNCHER = ROOT / "engines/nautilus/launcher/nautilus_backtest.py"
STRATEGY = ROOT / "engines/nautilus/launcher/target_portfolio_strategy.py"
BWRAP = Path("/usr/bin/bwrap")
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SCENARIOS = ("long-accounting", "same-bar-stop-take-profit")


class ApiQualificationError(ValueError):
    """The G1 API/callback qualification evidence is invalid or incomplete."""


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


def _closed_json(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ApiQualificationError(f"duplicate JSON key in {path.name}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiQualificationError(f"{path.name} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ApiQualificationError(f"{path.name} must contain one object")
    return value


def snapshot_candidate_closure(root: Path, expected_manifest_sha256: str) -> dict[str, object]:
    """Verify and snapshot every manifest-bound candidate byte without mutation."""

    if _SHA256.fullmatch(expected_manifest_sha256) is None:
        raise ApiQualificationError("candidate closure digest is invalid")
    try:
        root_stat = root.lstat()
        manifest_path = root / "closure-manifest.json"
        manifest_raw = manifest_path.read_bytes()
        manifest_stat = manifest_path.lstat()
    except OSError as exc:
        raise ApiQualificationError("candidate closure is unavailable") from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or stat.S_IMODE(root_stat.st_mode) != 0o500
        or root_stat.st_uid != os.geteuid()
        or not stat.S_ISREG(manifest_stat.st_mode)
        or stat.S_IMODE(manifest_stat.st_mode) != 0o400
        or hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256
    ):
        raise ApiQualificationError("candidate closure identity is invalid")
    manifest = _closed_json(manifest_path)
    files = manifest.get("files")
    if (
        manifest.get("schema_version") != 7
        or manifest.get("activation_status") != "CANDIDATE_ONLY_NOT_ACTIVATED"
        or not isinstance(files, list)
        or not files
    ):
        raise ApiQualificationError("candidate closure manifest is invalid")
    snapshot: list[dict[str, object]] = []
    expected_paths = {"closure-manifest.json"}
    for record in files:
        if not isinstance(record, dict) or set(record) != {
            "mode",
            "path",
            "sha256",
            "size",
            "target",
        }:
            raise ApiQualificationError("candidate file record is invalid")
        relative = record["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ApiQualificationError("candidate file path is invalid")
        path = root / relative
        try:
            raw = path.read_bytes()
            observed = path.lstat()
        except OSError as exc:
            raise ApiQualificationError("candidate file is unavailable") from exc
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or f"{stat.S_IMODE(observed.st_mode):04o}" != record["mode"]
            or len(raw) != record["size"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ApiQualificationError("candidate file bytes or mode changed")
        expected_paths.add(relative)
        snapshot.append(
            {
                "mode": record["mode"],
                "path": relative,
                "sha256": record["sha256"],
                "size": record["size"],
            }
        )
    observed_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if observed_paths != expected_paths:
        raise ApiQualificationError("candidate file inventory changed")
    return {
        "closure_manifest_sha256": expected_manifest_sha256,
        "file_count": len(snapshot),
        "file_snapshot_sha256": hashlib.sha256(_canonical(snapshot)).hexdigest(),
    }


def validate_probe_result(
    document: dict[str, object],
    *,
    contract: dict[str, object],
    golden: dict[str, object],
) -> None:
    surfaces = contract.get("api_surfaces")
    invocations = contract.get("local_invocations")
    cases = document.get("surface_cases")
    ids = document.get("local_invocation_ids")
    if not all(isinstance(value, list) for value in (surfaces, invocations, cases, ids)):
        raise ApiQualificationError("probe coverage shape is invalid")
    expected_surface_ids = [item["id"] for item in surfaces if isinstance(item, dict)]
    observed_surface_ids = [item.get("id") for item in cases if isinstance(item, dict)]
    expected_invocation_ids = sorted(
        item["id"] for item in invocations if isinstance(item, dict)
    )
    if (
        document.get("schema") != "trading-agent-nautilus-v1231-api-probe/v1"
        or document.get("status") != "PASS"
        or document.get("engine_version") != golden.get("engine_version")
    ):
        raise ApiQualificationError("probe version or verdict is invalid")
    if (
        document.get("api_surface_count") != golden.get("api_surface_count")
        or observed_surface_ids != expected_surface_ids
        or document.get("surface_ids_sha256") != golden.get("surface_ids_sha256")
    ):
        raise ApiQualificationError("probe surface coverage is incomplete")
    if (
        document.get("local_invocation_count") != golden.get("local_invocation_count")
        or ids != expected_invocation_ids
        or hashlib.sha256(_canonical(ids)).hexdigest()
        != golden.get("local_invocation_ids_sha256")
    ):
        raise ApiQualificationError("probe local invocation coverage is incomplete")
    if document.get("lifecycle") != {
        "dispose_called": True,
        "reset_called": True,
        "reset_retained_instrument": True,
        "reset_retained_strategy": True,
    }:
        raise ApiQualificationError("probe lifecycle evidence is incomplete")


def validate_scenario_event(
    document: dict[str, object], *, scenario_id: str, golden: dict[str, object]
) -> dict[str, object]:
    payload = document.get("payload")
    if not isinstance(payload, dict) or payload.get("event_type") != (
        "NautilusBacktestSimulationCompleted"
    ):
        raise ApiQualificationError("scenario terminal event is invalid")
    records = payload.get("attributes")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ApiQualificationError("scenario attributes are invalid")
    attributes = {
        str(item.get("name")): item.get("value")
        for item in records
        if set(item) == {"name", "value"} and isinstance(item.get("name"), str)
    }
    if len(attributes) != len(records) or any(
        isinstance(value, float) for value in attributes.values()
    ):
        raise ApiQualificationError("scenario attributes are duplicated or inexact")
    scenarios = golden.get("scenarios")
    expected = scenarios.get(scenario_id) if isinstance(scenarios, dict) else None
    if not isinstance(expected, dict) or not attributes.items() >= expected.items():
        raise ApiQualificationError("scenario result does not match the exact golden")
    return attributes


def validate_scenario_stderr(stderr: bytes) -> None:
    warning_lines = tuple(line for line in stderr.decode("utf-8").splitlines() if line)
    expected = (
        "/engine/launcher/nautilus_backtest.py:2283: Pandas4Warning: "
        "Timestamp.utcnow is deprecated and will be removed in a future version. "
        "Use Timestamp.now('UTC') instead.",
        "  engine.run()",
    )
    if warning_lines not in {(), expected}:
        raise ApiQualificationError(
            "candidate scenario emitted unexpected stderr: "
            + " | ".join(warning_lines)
        )


def _base_sandbox(root: Path) -> list[str]:
    return [
        str(BWRAP),
        "--die-with-parent",
        "--unshare-all",
        "--new-session",
        "--tmpfs",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--dir",
        "/engine",
        "--ro-bind",
        str(root / "files/engine/wheels"),
        "/engine/wheels",
        "--ro-bind",
        str(root / "files/usr"),
        "/usr",
        "--ro-bind",
        str(root / "files/lib"),
        "/lib",
        "--ro-bind",
        str(root / "files/lib64"),
        "/lib64",
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
    ]


def _run(command: list[str], *, label: str) -> tuple[bytes, bytes]:
    try:
        result = subprocess.run(
            command,
            cwd=Path("/"),
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApiQualificationError(f"{label} could not execute") from exc
    if result.returncode != 0:
        raise ApiQualificationError(
            f"{label} failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout, result.stderr


def _run_probe(root: Path) -> tuple[dict[str, object], bytes]:
    command = [
        *_base_sandbox(root),
        "--dir",
        "/qualification",
        "--ro-bind",
        str(PROBE),
        "/qualification/nautilus_v1231_probe.py",
        "--ro-bind",
        str(CONTRACT),
        "/qualification/direct-api-contract.json",
        "--ro-bind",
        str(STRATEGY),
        "/qualification/target_portfolio_strategy.py",
        "--",
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "/qualification/nautilus_v1231_probe.py",
        "--contract",
        "/qualification/direct-api-contract.json",
        "--strategy",
        "/qualification/target_portfolio_strategy.py",
        "--wheel-directory",
        "/engine/wheels",
    ]
    stdout, stderr = _run(command, label="sealed API probe")
    if stderr:
        raise ApiQualificationError("sealed API probe emitted stderr")
    try:
        document = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiQualificationError("sealed API probe output is invalid") from exc
    if not isinstance(document, dict) or stdout != _canonical(document) + b"\n":
        raise ApiQualificationError("sealed API probe output is not canonical")
    return document, stdout


def _simulation_packet(parent: Path, scenario_id: str) -> tuple[Path, Path, Path, Path]:
    fixture = build_canonical_simulation_fixture(scenario_id)  # type: ignore[arg-type]
    envelope = build_simulation_envelope(fixture)
    artifacts = parent / "artifacts"
    artifacts.mkdir(mode=0o700)
    names = (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
        "simulation_scenario",
    )
    payload = envelope.payload
    for name, raw in zip(names, fixture.artifacts, strict=True):
        reference = getattr(payload, name)
        extension = ".jsonl" if reference.media_type == "application/jsonl" else ".json"
        path = artifacts / f"{name}-{reference.sha256}{extension}"
        path.write_bytes(raw)
        path.chmod(0o400)
    request = parent / "request.json"
    request_raw = canonical_json_bytes(envelope)
    request.write_bytes(request_raw)
    request.chmod(0o400)
    sidecar = parent / "request.sha256"
    sidecar.write_text(hashlib.sha256(request_raw).hexdigest() + "\n", encoding="ascii")
    sidecar.chmod(0o400)
    strategy = parent / "target_portfolio_strategy.py"
    strategy_raw = STRATEGY.read_bytes()
    strategy.write_bytes(strategy_raw)
    strategy.chmod(0o400)
    manifest = parent / "closure-manifest.json"
    manifest.write_bytes(
        _canonical(
            {
                "files": [
                    {
                        "mode": "0400",
                        "path": "files/engine/launcher/target_portfolio_strategy.py",
                        "sha256": hashlib.sha256(strategy_raw).hexdigest(),
                        "size": len(strategy_raw),
                        "target": "/engine/launcher/target_portfolio_strategy.py",
                    }
                ]
            }
        )
        + b"\n"
    )
    manifest.chmod(0o400)
    return artifacts, request, sidecar, manifest


def _run_scenario(root: Path, scenario_id: str) -> tuple[dict[str, object], bytes]:
    with tempfile.TemporaryDirectory(prefix="p1-u05-scenario-", dir="/tmp") as raw_temp:
        packet = Path(raw_temp)
        packet.chmod(0o700)
        artifacts, request, sidecar, manifest = _simulation_packet(packet, scenario_id)
        command = [
            *_base_sandbox(root),
            "--dir",
            "/engine/launcher",
            "--dir",
            "/inputs",
            "--ro-bind",
            str(LAUNCHER),
            "/engine/launcher/nautilus_backtest.py",
            "--ro-bind",
            str(packet / "target_portfolio_strategy.py"),
            "/engine/launcher/target_portfolio_strategy.py",
            "--ro-bind",
            str(manifest),
            "/engine/closure-manifest.json",
            "--ro-bind",
            str(artifacts),
            "/inputs/artifacts",
            "--ro-bind",
            str(request),
            "/inputs/request.json",
            "--ro-bind",
            str(sidecar),
            "/inputs/request.sha256",
            "--",
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/launcher/nautilus_backtest.py",
            "--profile",
            "execution-simulation",
            "/inputs/request.json",
            "/inputs/request.sha256",
        ]
        stdout, stderr = _run(command, label=f"candidate scenario {scenario_id}")
    validate_scenario_stderr(stderr)
    try:
        document = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiQualificationError("candidate scenario output is invalid") from exc
    if not isinstance(document, dict) or stdout != canonical_json_bytes(document) + b"\n":
        raise ApiQualificationError("candidate scenario output is not canonical")
    return document, stderr


def _git_identity(commit: str, tree: str) -> None:
    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None:
        raise ApiQualificationError("qualification source identity is invalid")
    for argument, expected in (("HEAD", commit), ("HEAD^{tree}", tree)):
        result = subprocess.run(
            ["git", "rev-parse", argument],
            cwd=ROOT,
            env={},
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != expected:
            raise ApiQualificationError("qualification checkout identity changed")


def qualify(commit: str, tree: str, generation_id: str) -> dict[str, object]:
    _git_identity(commit, tree)
    generation = load_candidate_generation(GENERATION)
    if generation_id != generation.generation_id:
        raise ApiQualificationError("requested candidate generation is not accepted")
    policy = _closed_json(POLICY)
    isolation = policy.get("external_cache_isolation")
    roots = isolation.get("external_roots") if isinstance(isolation, dict) else None
    runtime = roots.get("candidate_runtime_root") if isinstance(roots, dict) else None
    if not isinstance(runtime, str):
        raise ApiQualificationError("candidate runtime root is unavailable")
    runtime_root = Path(runtime)
    before = snapshot_candidate_closure(
        runtime_root, generation.closure.manifest_sha256
    )
    contract = _closed_json(CONTRACT)
    golden = _closed_json(GOLDEN)
    probe, probe_raw = _run_probe(runtime_root)
    validate_probe_result(probe, contract=contract, golden=golden)
    scenarios: dict[str, object] = {}
    for scenario_id in _SCENARIOS:
        event, stderr = _run_scenario(runtime_root, scenario_id)
        attributes = validate_scenario_event(
            event, scenario_id=scenario_id, golden=golden
        )
        scenarios[scenario_id] = {
            "attributes": attributes,
            "event_sha256": hashlib.sha256(canonical_json_bytes(event)).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        }
    after = snapshot_candidate_closure(runtime_root, generation.closure.manifest_sha256)
    if before != after:
        raise ApiQualificationError("candidate closure changed during qualification")
    evidence: dict[str, object] = {
        "api_probe": {
            "api_surface_count": probe["api_surface_count"],
            "local_invocation_count": probe["local_invocation_count"],
            "output_sha256": hashlib.sha256(probe_raw).hexdigest(),
            "surface_ids_sha256": probe["surface_ids_sha256"],
        },
        "callbacks_observed": golden["callbacks_observed"],
        "callbacks_unobserved": golden["callbacks_unobserved"],
        "candidate_snapshot_after": after,
        "candidate_snapshot_before": before,
        "scenarios": scenarios,
        "source_sha256s": {
            "direct_api_contract": _sha(CONTRACT),
            "golden": _sha(GOLDEN),
            "launcher": _sha(LAUNCHER),
            "probe": _sha(PROBE),
            "runner": _sha(Path(__file__)),
            "strategy": _sha(STRATEGY),
        },
    }
    receipt: dict[str, object] = {
        "authority_limits": {
            "candidate_active": False,
            "candidate_promoted": False,
            "live_authorized": False,
            "network_trading_authorized": False,
            "production_authorized": False,
        },
        "candidate_closure_sha256": generation.closure.manifest_sha256,
        "candidate_generation_id": generation.generation_id,
        "candidate_generation_sha256": generation.record_sha256,
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
        "input_receipt_sha256s": {
            "direct_api_contract": _sha(CONTRACT),
            "u04_final_acceptance": _sha(U04_ACCEPTANCE),
        },
        "qualification_source_commit": commit,
        "qualification_source_tree": tree,
        "schema": "trading-agent-nautilus-u05-qualification/v1",
        "verdict": "PASS",
    }
    return receipt


def _abort(message: str) -> NoReturn:
    print(f"U05 API qualification failed: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--qualification-source-commit", required=True)
    parser.add_argument("--qualification-source-tree", required=True)
    arguments = parser.parse_args()
    try:
        receipt = qualify(
            arguments.qualification_source_commit,
            arguments.qualification_source_tree,
            arguments.generation,
        )
    except ApiQualificationError as exc:
        _abort(str(exc))
    sys.stdout.buffer.write(_pretty(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
