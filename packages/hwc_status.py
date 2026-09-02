"""Derived HWC source status and protected portable-receipt validation."""

from __future__ import annotations

import hashlib
from importlib import import_module
import json
import re
import stat
import tomllib
from pathlib import Path
from typing import Any

from packages.engine_contracts.serialization import canonical_json_bytes
from scripts.check_hwc_boundaries import evaluate_hwc_boundaries


HWC_STATUS_PATH = "docs/implementation/hwc/hwc-source-status.json"
HWC_PORTABLE_RECEIPT_PATH = (
    "docs/implementation/hwc/receipts/hwc-portable-qualified-v1.json"
)
HWC_GATES = (
    "HWC_ARCHITECTURE_FROZEN",
    "HWC_BOUNDARIES_ENFORCED",
    "HWC_OPERATOR_CONTRACTS",
    "HWC_OPERATOR_JOURNAL",
    "HWC_OPERATOR_SERVICE",
    "HWC_CREDENTIAL_BOUNDARY",
    "HWC_OPERATOR_API",
    "ARCH_CONTRACT_READY",
    "HWC_DASHBOARD_AUTHORITY_REMOVED",
    "HWC_OPERATOR_CLI",
    "HWC_PORTABLE_HEADLESS_PROOF",
    "HWC_COMMAND_RECOVERY_PROOF",
    "HWC_SOURCE_COMPLETE",
    "HWC_PORTABLE_QUALIFIED",
    "HWC_SOURCE_READY",
)
_AUTHORITY = {
    "broker": False,
    "live": False,
    "network": False,
    "production": False,
}
_DEPLOYMENT = {
    "host_qualified": "HELD",
    "release_v2_integrated": "HELD",
    "runtime_active": "HELD",
}
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_GATES = (
    "ARCH_CONTRACT_READY",
    "HWC_DASHBOARD_AUTHORITY_REMOVED",
    "HWC_OPERATOR_CLI",
    "HWC_PORTABLE_HEADLESS_PROOF",
    "HWC_COMMAND_RECOVERY_PROOF",
)
_REQUIRED_FILES = {
    "HWC_ARCHITECTURE_FROZEN": (
        "docs/adr/ADR-HWC-HEADLESS-OPERATOR-BOUNDARIES.md",
        "docs/implementation/hwc/hwc-authority-inventory-v1.json",
        "docs/implementation/hwc/hwc-boundary-policy-v1.json",
        "docs/implementation/hwc/hwc-closure-matrix-v1.json",
        "tests/hwc/test_hwc_policy.py",
    ),
    "HWC_BOUNDARIES_ENFORCED": (
        "scripts/check_hwc_boundaries.py",
        "tests/hwc/test_hwc_boundaries.py",
    ),
    "HWC_OPERATOR_CONTRACTS": (
        "packages/operator_control/contracts.py",
        "packages/operator_control/hashing.py",
        "packages/operator_control/policy.py",
        "tests/hwc/test_operator_contracts.py",
        "tests/hwc/test_operator_policy.py",
    ),
    "HWC_OPERATOR_JOURNAL": (
        "services/operator_control/protected_fs.py",
        "services/operator_control/state_store.py",
        "services/operator_control/journal.py",
        "tests/hwc/test_operator_protected_fs.py",
        "tests/hwc/test_operator_journal.py",
        "tests/hwc/test_operator_recovery.py",
    ),
    "HWC_OPERATOR_SERVICE": (
        "services/operator_control/service.py",
        "services/operator_control/composition.py",
        "services/operator_control/safety_adapter.py",
        "tests/hwc/test_operator_service.py",
    ),
    "HWC_CREDENTIAL_BOUNDARY": (
        "apps/operator_api/auth.py",
        "apps/operator_api/config.py",
        "apps/operator_api/errors.py",
        "tests/hwc/test_operator_api_auth.py",
    ),
    "HWC_OPERATOR_API": (
        "apps/operator_api/app.py",
        "apps/operator_api/contracts.py",
        "apps/operator_api/main.py",
        "apps/operator_api/middleware.py",
        "generated/operator-api/openapi/openapi.json",
        "apps/dashboard/src/generated/operator-api-types.ts",
        "tests/hwc/test_operator_api.py",
        "tests/hwc/test_operator_contract_generation.py",
    ),
    "HWC_OPERATOR_CLI": (
        "apps/operator_cli/http.py",
        "apps/operator_cli/cli.py",
        "tests/hwc/test_operator_cli.py",
        "tests/hwc/test_operator_cli_http.py",
    ),
    "HWC_PORTABLE_HEADLESS_PROOF": (
        "scripts/qualify_hwc_headless.py",
        "tests/hwc/test_headless_portable.py",
    ),
    "HWC_COMMAND_RECOVERY_PROOF": (
        "scripts/qualify_hwc_headless.py",
        "tests/hwc/test_operator_recovery_subprocess.py",
    ),
}


class HwcStatusError(ValueError):
    """HWC status or portable evidence is malformed or stale."""


def status_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {key: value for key, value in payload.items() if key != "status_sha256"}
        )
    ).hexdigest()


def receipt_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {key: value for key, value in payload.items() if key != "receipt_sha256"}
        )
    ).hexdigest()


def _regular(root: Path, relative: str) -> bool:
    path = root / relative
    try:
        return stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink()
    except OSError:
        return False


def _files_present(root: Path, gate: str) -> bool:
    return all(_regular(root, relative) for relative in _REQUIRED_FILES[gate])


def _architecture_valid(root: Path) -> bool:
    try:
        inventory = json.loads(
            (root / "docs/implementation/hwc/hwc-authority-inventory-v1.json").read_bytes()
        )
        policy = json.loads(
            (root / "docs/implementation/hwc/hwc-boundary-policy-v1.json").read_bytes()
        )
        closure = json.loads(
            (root / "docs/implementation/hwc/hwc-closure-matrix-v1.json").read_bytes()
        )
        return (
            inventory.get("schema_version") == "hwc-authority-inventory-v1"
            and inventory.get("authority_limits") == _AUTHORITY
            and policy.get("schema_version") == "hwc-boundary-policy-v1"
            and policy.get("route_inventory")
            == "docs/implementation/hwc/hwc-authority-inventory-v1.json"
            and closure.get("schema_version") == "hwc-closure-matrix-v1"
            and closure.get("authority") == _AUTHORITY
            and [item.get("gate") for item in closure.get("gates", ())] == list(HWC_GATES)
        )
    except (OSError, AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _api_contract_valid(root: Path) -> bool:
    try:
        document = json.loads(
            (root / "generated/operator-api/openapi/openapi.json").read_bytes()
        )
        paths = document["paths"]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        included = project["tool"]["hatch"]["build"]["targets"]["wheel"][
            "force-include"
        ]
        return (
            set(paths)
            == {"/health/live", "/health/ready", "/v1/state", "/v1/commands"}
            and paths["/v1/state"]["get"].get("x-operator-interfaces") == ["CLI"]
            and paths["/v1/commands"]["post"].get("x-operator-interfaces")
            == ["WEB", "CLI"]
            and included.get("apps/operator_api") == "apps/operator_api"
        )
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ):
        return False


def derive_hwc_source_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    report = evaluate_hwc_boundaries(root)
    final_report = evaluate_hwc_boundaries(root, final=True)
    gates = {gate: "HELD" for gate in HWC_GATES}
    gates["HWC_ARCHITECTURE_FROZEN"] = (
        "PASS"
        if _files_present(root, "HWC_ARCHITECTURE_FROZEN")
        and _architecture_valid(root)
        else "HELD"
    )
    gates["HWC_BOUNDARIES_ENFORCED"] = (
        "PASS"
        if _files_present(root, "HWC_BOUNDARIES_ENFORCED") and report.passed
        else "HELD"
    )
    for gate in (
        "HWC_OPERATOR_CONTRACTS",
        "HWC_OPERATOR_JOURNAL",
        "HWC_OPERATOR_SERVICE",
        "HWC_CREDENTIAL_BOUNDARY",
        "HWC_OPERATOR_API",
    ):
        gates[gate] = "PASS" if _files_present(root, gate) else "HELD"
    if gates["HWC_OPERATOR_API"] == "PASS" and not _api_contract_valid(root):
        gates["HWC_OPERATOR_API"] = "HELD"
    architecture_gates = HWC_GATES[: HWC_GATES.index("ARCH_CONTRACT_READY")]
    gates["ARCH_CONTRACT_READY"] = (
        "PASS" if all(gates[gate] == "PASS" for gate in architecture_gates) else "HELD"
    )
    gates["HWC_DASHBOARD_AUTHORITY_REMOVED"] = (
        "PASS" if final_report.passed and final_report.grandfathered_debt == 0 else "HELD"
    )
    for gate in (
        "HWC_OPERATOR_CLI",
        "HWC_PORTABLE_HEADLESS_PROOF",
        "HWC_COMMAND_RECOVERY_PROOF",
    ):
        gates[gate] = "PASS" if _files_present(root, gate) else "HELD"
    gates["HWC_SOURCE_COMPLETE"] = (
        "PASS" if all(gates[gate] == "PASS" for gate in _SOURCE_GATES) else "HELD"
    )
    receipt_path = root / HWC_PORTABLE_RECEIPT_PATH
    try:
        if gates["HWC_SOURCE_COMPLETE"] != "PASS":
            raise HwcStatusError("HWC source is incomplete")
        if not _regular(root, HWC_PORTABLE_RECEIPT_PATH):
            raise HwcStatusError("portable receipt is absent")
        validate_hwc_portable_receipt(json.loads(receipt_path.read_bytes()), root=root)
        gates["HWC_PORTABLE_QUALIFIED"] = "PASS"
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        gates["HWC_PORTABLE_QUALIFIED"] = "HELD"
    gates["HWC_SOURCE_READY"] = (
        "PASS"
        if gates["HWC_SOURCE_COMPLETE"] == gates["HWC_PORTABLE_QUALIFIED"] == "PASS"
        else "HELD"
    )
    payload: dict[str, Any] = {
        "schema_version": "trading-agent-hwc-status-v1",
        "gates": gates,
        "authority": dict(_AUTHORITY),
        "deployment": dict(_DEPLOYMENT),
        "blockers": sorted(
            f"{gate}: HELD" for gate, value in gates.items() if value == "HELD"
        ),
    }
    payload["status_sha256"] = status_sha256(payload)
    return validate_hwc_source_status(payload)


def validate_hwc_source_status(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "gates",
        "authority",
        "deployment",
        "blockers",
        "status_sha256",
    }:
        raise HwcStatusError("HWC status field set is invalid")
    if payload.get("schema_version") != "trading-agent-hwc-status-v1":
        raise HwcStatusError("HWC status schema is invalid")
    gates = payload.get("gates")
    if (
        not isinstance(gates, dict)
        or set(gates) != set(HWC_GATES)
        or any(value not in {"PASS", "HELD"} for value in gates.values())
    ):
        raise HwcStatusError("HWC status gate set is invalid")
    architecture_gates = HWC_GATES[: HWC_GATES.index("ARCH_CONTRACT_READY")]
    expected_arch = all(gates[gate] == "PASS" for gate in architecture_gates)
    expected_complete = all(gates[gate] == "PASS" for gate in _SOURCE_GATES)
    expected_ready = (
        gates["HWC_SOURCE_COMPLETE"] == gates["HWC_PORTABLE_QUALIFIED"] == "PASS"
    )
    if (
        (gates["ARCH_CONTRACT_READY"] == "PASS") != expected_arch
        or (gates["HWC_SOURCE_COMPLETE"] == "PASS") != expected_complete
        or (gates["HWC_SOURCE_READY"] == "PASS") != expected_ready
        or (gates["HWC_PORTABLE_QUALIFIED"] == "PASS" and not expected_complete)
    ):
        raise HwcStatusError("HWC status gate logic is invalid")
    if payload.get("authority") != _AUTHORITY:
        raise HwcStatusError("HWC status authority is invalid")
    if payload.get("deployment") != _DEPLOYMENT:
        raise HwcStatusError("HWC status deployment is invalid")
    blockers = payload.get("blockers")
    expected_blockers = sorted(
        f"{gate}: HELD" for gate, value in gates.items() if value == "HELD"
    )
    if blockers != expected_blockers:
        raise HwcStatusError("HWC status blockers are invalid")
    if payload.get("status_sha256") != status_sha256(payload):
        raise HwcStatusError("HWC status digest is invalid")
    return payload


def validate_hwc_portable_receipt(
    payload: object, *, root: Path | None = None
) -> dict[str, Any]:
    provenance = import_module("packages.pre_p3_provenance")

    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "status",
        "source",
        "run",
        "evidence",
        "authority",
        "receipt_sha256",
    }:
        raise HwcStatusError("HWC portable receipt field set is invalid")
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) != {
        "closure_policy_sha256",
        "closure_schema_version",
        "closure_sha256",
        "commit_sha",
        "tree_sha",
    }:
        raise HwcStatusError("HWC portable receipt source is invalid")
    if (
        payload.get("schema_version") != "hwc-portable-qualified-receipt-v1"
        or payload.get("status") != "PASS"
        or payload.get("authority") != _AUTHORITY
        or source.get("closure_schema_version") != provenance.SOURCE_CLOSURE_SCHEMA
        or source.get("closure_policy_sha256")
        != provenance.SOURCE_CLOSURE_POLICY_SHA256
        or not _HEX64.fullmatch(str(source.get("closure_sha256", "")))
        or not _HEX40.fullmatch(str(source.get("commit_sha", "")))
        or not _HEX40.fullmatch(str(source.get("tree_sha", "")))
    ):
        raise HwcStatusError("HWC portable receipt authority or source is invalid")
    run = payload.get("run")
    if not isinstance(run, dict) or set(run) != {
        "repository",
        "workflow",
        "event",
        "ref",
        "sha",
        "workflow_sha",
        "run_id",
        "run_attempt",
    }:
        raise HwcStatusError("HWC portable receipt run field set is invalid")
    if (
        run.get("repository") != "nam176hermes/Trading-Agent"
        or run.get("workflow") != "Foundation"
        or run.get("event") != "push"
        or run.get("ref") != "refs/heads/main"
        or run.get("sha") != source["commit_sha"]
        or run.get("workflow_sha") != source["commit_sha"]
        or not re.fullmatch(r"[1-9][0-9]*", str(run.get("run_id", "")))
        or not re.fullmatch(r"[1-9][0-9]*", str(run.get("run_attempt", "")))
    ):
        raise HwcStatusError("HWC portable receipt protected run is invalid")
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != {
        "headless_receipt_sha256",
        "recovery_campaign_sha256",
        "hwc_boundary_report_sha256",
        "generated_contract_report_sha256",
    } or any(not _HEX64.fullmatch(str(value)) for value in evidence.values()):
        raise HwcStatusError("HWC portable receipt evidence is invalid")
    if payload.get("receipt_sha256") != receipt_sha256(payload):
        raise HwcStatusError("HWC portable receipt digest is invalid")
    if root is not None:
        try:
            actual = provenance.canonical_source_identity(root, source["commit_sha"])
        except ValueError as exc:
            raise HwcStatusError("HWC portable receipt source is unavailable") from exc
        if actual != source or not provenance.source_matches_current(root, source):
            raise HwcStatusError("HWC portable receipt source is stale")
    return payload


__all__ = [
    "HWC_GATES",
    "HWC_PORTABLE_RECEIPT_PATH",
    "HWC_STATUS_PATH",
    "HwcStatusError",
    "derive_hwc_source_status",
    "receipt_sha256",
    "status_sha256",
    "validate_hwc_portable_receipt",
    "validate_hwc_source_status",
]
