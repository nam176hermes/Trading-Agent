#!/usr/bin/env python3
"""Emit HWC portable evidence only from the exact protected Foundation run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.engine_contracts.serialization import canonical_json_bytes
from packages.hwc_status import receipt_sha256, validate_hwc_portable_receipt
from packages.pre_p3_provenance import canonical_source_identity
from scripts.check_hwc_boundaries import evaluate_hwc_boundaries
from scripts.qualify_hwc_headless import validate_receipt as validate_headless_receipt
from scripts.t_g03_capability_topology import _publish_no_clobber


class HwcPortableRecordingError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_identity(environment: Mapping[str, str], source_sha: str) -> dict[str, str]:
    run = {
        "repository": environment.get("GITHUB_REPOSITORY", ""),
        "workflow": environment.get("GITHUB_WORKFLOW", ""),
        "workflow_ref": environment.get("GITHUB_WORKFLOW_REF", ""),
        "event": environment.get("GITHUB_EVENT_NAME", ""),
        "ref": environment.get("GITHUB_REF", ""),
        "sha": environment.get("GITHUB_SHA", ""),
        "workflow_sha": environment.get("GITHUB_WORKFLOW_SHA", ""),
        "run_id": environment.get("GITHUB_RUN_ID", ""),
        "run_attempt": environment.get("GITHUB_RUN_ATTEMPT", ""),
    }
    expected_ref = (
        "nam176hermes/Trading-Agent/.github/workflows/foundation.yml@refs/heads/main"
    )
    if (
        run["repository"] != "nam176hermes/Trading-Agent"
        or run["workflow"] != "Foundation"
        or run["workflow_ref"] != expected_ref
        or run["event"] != "push"
        or run["ref"] != "refs/heads/main"
        or run["sha"] != source_sha
        or run["workflow_sha"] != source_sha
        or not run["run_id"].isdigit()
        or run["run_id"].startswith("0")
        or not run["run_attempt"].isdigit()
        or run["run_attempt"].startswith("0")
    ):
        raise HwcPortableRecordingError("protected Foundation identity is required")
    return run


def _regular_bytes(path: Path, label: str) -> bytes:
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_nlink != 1:
            raise OSError
        return path.read_bytes()
    except OSError as exc:
        raise HwcPortableRecordingError(f"{label} is unavailable") from exc


def _boundary_report(root: Path) -> bytes:
    report = evaluate_hwc_boundaries(root, final=True)
    payload = {
        "schema_version": "hwc-boundary-report-v1",
        "grandfathered_debt": report.grandfathered_debt,
        "status": "PASS" if report.passed else "FAIL",
        "violations": [
            {"code": item.code, "detail": item.detail, "path": item.path}
            for item in report.violations
        ],
    }
    if payload["status"] != "PASS" or payload["grandfathered_debt"] != 0:
        raise HwcPortableRecordingError("final HWC boundary report is not PASS")
    return canonical_json_bytes(payload) + b"\n"


def _contract_report(root: Path) -> bytes:
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/generate_contracts.py"), "--check"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise HwcPortableRecordingError("generated contracts are not current")
    return (
        canonical_json_bytes(
            {
                "schema_version": "hwc-generated-contract-report-v1",
                "command": "scripts/generate_contracts.py --check",
                "status": "PASS",
                "stdout_sha256": _sha(completed.stdout),
                "stderr_sha256": _sha(completed.stderr),
            }
        )
        + b"\n"
    )


def make_receipt(
    *,
    root: Path,
    headless_raw: bytes,
    boundary_raw: bytes,
    contract_raw: bytes,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    try:
        headless = json.loads(headless_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HwcPortableRecordingError("headless evidence is invalid") from exc
    if headless_raw != canonical_json_bytes(headless) + b"\n":
        raise HwcPortableRecordingError("headless evidence bytes are not canonical")
    try:
        validate_headless_receipt(headless, root=root)
    except (OSError, ValueError) as exc:
        raise HwcPortableRecordingError("headless evidence validation failed") from exc
    source = canonical_source_identity(root)
    if headless["source"] != source:
        raise HwcPortableRecordingError("headless evidence source is stale")
    for raw, schema in (
        (boundary_raw, "hwc-boundary-report-v1"),
        (contract_raw, "hwc-generated-contract-report-v1"),
    ):
        try:
            report = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HwcPortableRecordingError("portable report is invalid") from exc
        if (
            raw != canonical_json_bytes(report) + b"\n"
            or report.get("schema_version") != schema
            or report.get("status") != "PASS"
        ):
            raise HwcPortableRecordingError(
                "portable report is not canonical PASS evidence"
            )
    payload: dict[str, Any] = {
        "schema_version": "hwc-portable-qualified-receipt-v1",
        "status": "PASS",
        "source": source,
        "run": _run_identity(environment, source["commit_sha"]),
        "evidence": {
            "headless_receipt_sha256": _sha(headless_raw),
            "recovery_campaign_sha256": headless["evidence"][
                "recovery_campaign_sha256"
            ],
            "hwc_boundary_report_sha256": _sha(boundary_raw),
            "generated_contract_report_sha256": _sha(contract_raw),
        },
        "authority": {
            "broker": False,
            "live": False,
            "network": False,
            "production": False,
        },
    }
    payload["receipt_sha256"] = receipt_sha256(payload)
    return validate_hwc_portable_receipt(payload)


def record(
    *, root: Path, headless_path: Path, output: Path, environment: Mapping[str, str]
) -> dict[str, Any]:
    if subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, stdout=subprocess.PIPE, check=True
    ).stdout:
        raise HwcPortableRecordingError(
            "portable recording requires a clean source tree"
        )
    headless_raw = _regular_bytes(headless_path, "headless evidence")
    boundary_raw = _boundary_report(root)
    contract_raw = _contract_report(root)
    receipt = make_receipt(
        root=root,
        headless_raw=headless_raw,
        boundary_raw=boundary_raw,
        contract_raw=contract_raw,
        environment=environment,
    )
    _publish_no_clobber(output.parent / "hwc-boundary-report-v1.json", boundary_raw)
    _publish_no_clobber(
        output.parent / "hwc-generated-contract-report-v1.json", contract_raw
    )
    _publish_no_clobber(output, canonical_json_bytes(receipt) + b"\n")
    return receipt


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        record(
            root=ROOT,
            headless_path=options.headless,
            output=options.output,
            environment=os.environ,
        )
    except (
        HwcPortableRecordingError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"HWC portable recording: FAIL ({exc})", file=sys.stderr)
        return 1
    print(f"HWC portable recording: PASS ({options.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
