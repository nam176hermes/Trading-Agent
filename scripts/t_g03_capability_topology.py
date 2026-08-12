#!/usr/bin/env python3
"""Fail-closed capability-topology receipts for the locked hosted inventory."""

from __future__ import annotations

import hashlib
import json
import csv
import argparse
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


LOCKED_INVENTORY_SHA256 = "99e2e9f0ea91c65fd841a0b81b8948eb6d3967203627d0911c151794737a8bfe"
RECEIPT_SCHEMA = "t-g03a-capability-receipt/v1"
RECEIPT_KEYS = frozenset({
    "schema_version", "foundation_run_id", "foundation_head_sha", "inventory_sha256",
    "lane", "capability_or_authority_code", "expected_node_ids", "collected_node_ids",
    "completeness_sha256", "preflight_state", "redacted_fact_class", "outcome",
    "receipt_sha256",
})


class TopologyError(RuntimeError):
    """A topology binding or receipt is invalid."""


INVENTORY_COLUMNS = (
    "test_node_id", "source_file", "primary_invariant", "failure_before_primary_assertion",
    "classification", "capability_or_authority_code", "source_fix_required",
    "dedicated_gate", "evidence_command", "owner", "security_critical", "reason",
)
CLASSIFICATION_LANE = {
    "PORTABLE_SOURCE_DEFECT": "portable-source",
    "NATIVE_CAPABILITY_REQUIRED": "native-capabilities",
    "EXTERNAL_AUTHORITY_REQUIRED": "external-authorities",
}
CODE_CLASSIFICATION = {
    "SRC-PHASE4B-FAKEROOT-IDENTITY": "PORTABLE_SOURCE_DEFECT",
    "SRC-SEALEDUV-BWRAP-PREFLIGHT": "PORTABLE_SOURCE_DEFECT",
    "SRC-SEMANTIC-FIXTURE-IDENTITY": "PORTABLE_SOURCE_DEFECT",
    "NATIVE-BWRAP-OS-SANDBOX": "NATIVE_CAPABILITY_REQUIRED",
    "NATIVE-USERNS-ROOT-PROVISION": "NATIVE_CAPABILITY_REQUIRED",
    "EXT-PHASE3B-CORPUS": "EXTERNAL_AUTHORITY_REQUIRED",
    "EXT-LEGACY-UV-AUTHORITY": "EXTERNAL_AUTHORITY_REQUIRED",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^(0|[1-9][0-9]*)$")
ASCII = re.compile(r"^[\x21-\x7e]+$")


@dataclass(frozen=True)
class InventoryRow:
    node_id: str
    classification: str
    code: str


def load_inventory(path: Path) -> tuple[InventoryRow, ...]:
    """Read only the locked tracked inventory; its hash also locks all mappings."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != LOCKED_INVENTORY_SHA256:
        raise TopologyError("locked inventory hash drift")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TopologyError("inventory is not UTF-8") from exc
    if text.startswith("\ufeff") or not text.endswith("\n") or "\n\n" in text:
        raise TopologyError("inventory has noncanonical rows")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    if tuple(reader.fieldnames or ()) != INVENTORY_COLUMNS:
        raise TopologyError("inventory schema drift")
    rows: list[InventoryRow] = []
    seen: set[str] = set()
    for index, row in enumerate(reader, start=2):
        if row is None or set(row) != set(INVENTORY_COLUMNS) or any(
            not isinstance(value, str) or not value for value in row.values()
        ):
            raise TopologyError(f"inventory row {index} is blank or malformed")
        node_id = row["test_node_id"]
        classification = row["classification"]
        code = row["capability_or_authority_code"]
        if node_id in seen or not ASCII.fullmatch(node_id):
            raise TopologyError(f"inventory row {index} has duplicate or invalid node")
        if classification not in CLASSIFICATION_LANE or CODE_CLASSIFICATION.get(code) != classification:
            raise TopologyError(f"inventory row {index} has unknown classification or code")
        seen.add(node_id)
        rows.append(InventoryRow(node_id, classification, code))
    if len(rows) != 62:
        raise TopologyError("inventory row count drift")
    return tuple(rows)


def install_inventory(source: Path, evidence_root: Path) -> Path:
    """Copy verified bytes once into private evidence; never overwrite a prior run."""
    load_inventory(source)
    evidence_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(evidence_root, 0o700)
    destination = evidence_root / "t-g03a-hosted-failure-inventory.tsv"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    if destination.read_bytes() != source.read_bytes():
        raise TopologyError("installed inventory byte comparison failed")
    load_inventory(destination)
    return destination


def canonical_json_bytes(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _sha256(document: object) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def completeness_sha256(receipt: dict[str, object]) -> str:
    return _sha256({field: receipt[field] for field in (
        "lane", "capability_or_authority_code", "expected_node_ids", "collected_node_ids",
    )})


def payload_sha256(receipt: dict[str, object]) -> str:
    return _sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})


def parse_receipt(raw: bytes) -> dict[str, object]:
    try:
        decoded = raw.decode("utf-8")
        value: Any = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError("receipt is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != RECEIPT_KEYS:
        raise TopologyError("receipt has invalid schema keys")
    if canonical_json_bytes(value) != raw:
        raise TopologyError("receipt is not canonical")
    if value.get("schema_version") != RECEIPT_SCHEMA:
        raise TopologyError("receipt has invalid schema version")
    _validate_receipt_shape(value)
    if value.get("completeness_sha256") != completeness_sha256(value):
        raise TopologyError("receipt completeness hash mismatch")
    if value.get("receipt_sha256") != payload_sha256(value):
        raise TopologyError("receipt self-hash mismatch")
    return value


def _validate_receipt_shape(receipt: dict[str, object]) -> None:
    if not isinstance(receipt["foundation_run_id"], str) or not RUN_ID.fullmatch(receipt["foundation_run_id"]):
        raise TopologyError("receipt has invalid foundation run")
    if not isinstance(receipt["foundation_head_sha"], str) or not HEAD_SHA.fullmatch(receipt["foundation_head_sha"]):
        raise TopologyError("receipt has invalid head")
    for field in ("inventory_sha256", "completeness_sha256", "receipt_sha256"):
        if not isinstance(receipt[field], str) or not HEX64.fullmatch(receipt[field]):
            raise TopologyError(f"receipt has invalid {field}")
    for field in ("lane", "capability_or_authority_code", "preflight_state", "redacted_fact_class", "outcome"):
        if not isinstance(receipt[field], str) or not ASCII.fullmatch(receipt[field]):
            raise TopologyError(f"receipt has invalid {field}")
    if receipt["outcome"] not in {"PASS", "DEFERRED", "FAIL"}:
        raise TopologyError("receipt has invalid outcome")
    for field in ("expected_node_ids", "collected_node_ids"):
        values = receipt[field]
        if not isinstance(values, list) or any(not isinstance(item, str) or not ASCII.fullmatch(item) for item in values):
            raise TopologyError(f"receipt has invalid {field}")
        if values != sorted(set(values)):
            raise TopologyError(f"receipt has duplicate or unordered {field}")


def _expected_rows(rows: tuple[InventoryRow, ...], code: str) -> tuple[str, tuple[str, ...]]:
    expected = tuple(sorted(row.node_id for row in rows if row.code == code))
    if not expected:
        raise TopologyError("receipt uses unknown code")
    classification = CODE_CLASSIFICATION.get(code)
    if classification is None:
        raise TopologyError("receipt uses unknown code")
    return CLASSIFICATION_LANE[classification], expected


def validate_receipt(
    raw: bytes, *, rows: tuple[InventoryRow, ...], foundation_run_id: str, foundation_head_sha: str,
) -> dict[str, object]:
    receipt = parse_receipt(raw)
    if receipt["foundation_run_id"] != foundation_run_id or receipt["foundation_head_sha"] != foundation_head_sha:
        raise TopologyError("receipt is stale for this Foundation run/head")
    if receipt["inventory_sha256"] != LOCKED_INVENTORY_SHA256:
        raise TopologyError("receipt inventory binding drift")
    lane, expected = _expected_rows(rows, str(receipt["capability_or_authority_code"]))
    if receipt["lane"] != lane or tuple(receipt["expected_node_ids"]) != expected:
        raise TopologyError("receipt lane/code/node mapping drift")
    state = str(receipt["preflight_state"])
    outcome = str(receipt["outcome"])
    allowed = {
        "portable-source": {("AVAILABLE", "PASS")},
        "native-capabilities": {("AVAILABLE", "PASS"), ("UNAVAILABLE", "DEFERRED")},
        "external-authorities": {("VALID", "PASS"), ("ABSENT", "DEFERRED")},
    }[lane]
    if (state, outcome) not in allowed:
        raise TopologyError("receipt has forbidden state-to-lane mapping")
    collected = tuple(receipt["collected_node_ids"])
    if outcome == "PASS" and collected != expected:
        raise TopologyError("PASS receipt did not execute every expected node")
    if outcome == "DEFERRED" and collected:
        raise TopologyError("DEFERRED receipt selected a node")
    return receipt


def aggregate_receipts(
    paths: list[Path], *, rows: tuple[InventoryRow, ...], foundation_run_id: str, foundation_head_sha: str,
) -> dict[str, object]:
    expected_codes = set(CODE_CLASSIFICATION)
    receipts = [validate_receipt(path.read_bytes(), rows=rows, foundation_run_id=foundation_run_id, foundation_head_sha=foundation_head_sha) for path in paths]
    codes = [str(receipt["capability_or_authority_code"]) for receipt in receipts]
    if len(codes) != len(set(codes)) or set(codes) != expected_codes:
        raise TopologyError("receipt set is missing, duplicate, or unknown")
    statuses = {lane: "PASS" for lane in CLASSIFICATION_LANE.values()}
    for receipt in receipts:
        lane = str(receipt["lane"])
        if receipt["outcome"] == "DEFERRED":
            statuses[lane] = "DEFERRED"
    if statuses["portable-source"] != "PASS":
        raise TopologyError("portable source lane did not pass")
    return {
        "portable_source_status": statuses["portable-source"],
        "native_capabilities_status": statuses["native-capabilities"],
        "external_authorities_status": statuses["external-authorities"],
        "runtime_proof": "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS" if "DEFERRED" in statuses.values() else "COMPLETE",
    }


def make_receipt(*, run_id: str, head_sha: str, lane: str, code: str, expected: tuple[str, ...], collected: tuple[str, ...], state: str, fact: str, outcome: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA, "foundation_run_id": run_id,
        "foundation_head_sha": head_sha, "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "lane": lane, "capability_or_authority_code": code,
        "expected_node_ids": list(expected), "collected_node_ids": list(collected),
        "completeness_sha256": "", "preflight_state": state,
        "redacted_fact_class": fact, "outcome": outcome, "receipt_sha256": "",
    }
    receipt["completeness_sha256"] = completeness_sha256(receipt)
    receipt["receipt_sha256"] = payload_sha256(receipt)
    return receipt


def publish_receipt(receipt: dict[str, object], evidence_root: Path) -> Path:
    """Publish a receipt once. Existing evidence is a hard failure, never clobbered."""
    code = str(receipt["capability_or_authority_code"])
    destination = evidence_root / "capability-topology" / f"{code}.json"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_json_bytes(receipt))
        stream.flush()
        os.fsync(stream.fileno())
    return destination


def _native_preflight(code: str) -> tuple[str, str]:
    if code == "NATIVE-BWRAP-OS-SANDBOX":
        policy = Path("engines/nautilus/sealed-uv-exec-policy.json")
        sandbox = Path("/usr/bin/bwrap")
        if not sandbox.exists():
            return "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT"
        if not sandbox.is_file() or sandbox.is_symlink() or not policy.is_file():
            return "BROKEN", "NATIVE_IDENTITY_INVALID"
        try:
            binding = json.loads(policy.read_text(encoding="utf-8"))
            observed = sandbox.stat()
            version = subprocess.run([str(sandbox), "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
            help_output = subprocess.run([str(sandbox), "--help"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError):
            return "BROKEN", "NATIVE_IDENTITY_INVALID"
        required = binding.get("sandbox_capabilities")
        valid = (version.returncode == 0 and help_output.returncode == 0 and isinstance(required, list)
                 and hashlib.sha256(sandbox.read_bytes()).hexdigest() == binding.get("sandbox_sha256")
                 and observed.st_uid == binding.get("sandbox_uid") and observed.st_gid == binding.get("sandbox_gid")
                 and f"{observed.st_mode & 0o7777:04o}" == binding.get("sandbox_mode")
                 and version.stdout.strip() == binding.get("sandbox_version")
                 and all(isinstance(option, str) and option in help_output.stdout for option in required))
        return ("AVAILABLE", "NATIVE_CAPABILITY_VALIDATED") if valid else ("BROKEN", "NATIVE_IDENTITY_INVALID")
    if code == "NATIVE-USERNS-ROOT-PROVISION":
        unshare = shutil.which("unshare")
        if unshare is None:
            return "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT"
        try:
            result = subprocess.run([unshare, "--user", "--map-root-user", "true"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return "BROKEN", "NATIVE_PROBE_INVALID"
        if result.returncode == 0:
            return "AVAILABLE", "NATIVE_CAPABILITY_VALIDATED"
        if "Operation not permitted" in result.stderr:
            return "UNAVAILABLE", "RUNNER_POLICY_DISALLOWS_USERNS"
        return "BROKEN", "NATIVE_PROBE_INVALID"
    raise TopologyError("unknown native capability")


def _external_preflight(code: str) -> tuple[str, str]:
    if code == "EXT-PHASE3B-CORPUS":
        root = Path("/home/thenam176/.hermes/crypto-research")
        return ("ABSENT", "AUTHORITY_ROOT_ABSENT") if not root.exists() else ("INVALID", "AUTHORITY_PRESENT_REQUIRES_REVIEWED_VALIDATION")
    if code == "EXT-LEGACY-UV-AUTHORITY":
        authority = Path("/home/thenam176/.local/bin/uv")
        return ("ABSENT", "AUTHORITY_EXECUTABLE_ABSENT") if not authority.exists() else ("INVALID", "AUTHORITY_PRESENT_REQUIRES_REVIEWED_VALIDATION")
    raise TopologyError("unknown external authority")


def _run_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(report.parent, 0o700)
    environment = dict(os.environ, TEST_GOVERNANCE_REPORT=str(report), TEST_GOVERNANCE_COMPONENT="root")
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "scripts.test_governance_pytest", *nodes], stdin=subprocess.DEVNULL, env=environment, check=False)
    if completed.returncode != 0 or not report.is_file():
        raise TopologyError("selected pytest collection or execution failed")
    document = json.loads(report.read_text(encoding="utf-8"))
    observed = document.get("tests")
    if not isinstance(observed, list):
        raise TopologyError("governance report is malformed")
    passed = tuple(sorted(str(item.get("test_node_id")) for item in observed if isinstance(item, dict) and item.get("outcome") == "passed"))
    if passed != nodes or len(observed) != len(nodes):
        raise TopologyError("exact node collection/execution proof failed")
    return passed


def run_lane(*, lane: str, inventory: Path, evidence_root: Path, run_id: str, head_sha: str) -> list[Path]:
    rows = load_inventory(inventory)
    installed = evidence_root / "t-g03a-hosted-failure-inventory.tsv"
    if installed.exists():
        if installed.read_bytes() != inventory.read_bytes():
            raise TopologyError("installed inventory binding drift")
        load_inventory(installed)
    else:
        installed = install_inventory(inventory, evidence_root)
    rows = load_inventory(installed)
    publications: list[Path] = []
    for code in sorted(CODE_CLASSIFICATION):
        expected_lane, expected = _expected_rows(rows, code)
        if expected_lane != lane:
            continue
        state, fact = ("AVAILABLE", "SOURCE_TEST_EXECUTED") if lane == "portable-source" else (_native_preflight(code) if lane == "native-capabilities" else _external_preflight(code))
        if state in {"BROKEN", "PARTIAL", "INVALID"}:
            raise TopologyError(f"{code} preflight is {state}")
        if state in {"UNAVAILABLE", "ABSENT"}:
            receipt = make_receipt(run_id=run_id, head_sha=head_sha, lane=lane, code=code, expected=expected, collected=(), state=state, fact=fact, outcome="DEFERRED")
        else:
            selected = _run_exact(expected, evidence_root / "capability-topology" / f"{code}.governance.json")
            receipt = make_receipt(run_id=run_id, head_sha=head_sha, lane=lane, code=code, expected=expected, collected=selected, state=state, fact=fact, outcome="PASS")
        publications.append(publish_receipt(receipt, evidence_root))
    return publications


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run-lane", "aggregate"))
    parser.add_argument("--lane", choices=tuple(CLASSIFICATION_LANE.values()))
    parser.add_argument("--inventory", type=Path, default=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--foundation-run-id", required=True)
    parser.add_argument("--foundation-head-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "run-lane":
            if args.lane is None:
                raise TopologyError("run-lane requires --lane")
            run_lane(lane=args.lane, inventory=args.inventory, evidence_root=args.evidence_root, run_id=args.foundation_run_id, head_sha=args.foundation_head_sha)
        else:
            rows = load_inventory(args.inventory)
            paths = sorted((args.evidence_root / "capability-topology").glob("*.json"))
            print(canonical_json_bytes(aggregate_receipts(paths, rows=rows, foundation_run_id=args.foundation_run_id, foundation_head_sha=args.foundation_head_sha)).decode("utf-8"))
    except (TopologyError, OSError, ValueError) as exc:
        print(f"t-g03 capability topology: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
