"""Fail-closed Pre-P3 source and promotion provenance."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
from typing import Any
import unicodedata

from packages.engine_contracts.serialization import canonical_json_bytes


SOURCE_CLOSURE_SCHEMA = "trading-agent-source-closure-v1"
PROJECT_STATUS_PATH = "docs/implementation/project-status.json"
RECEIPT_ROOT = "docs/implementation/pre-p3/receipts"
PROMOTION_ROOT = "docs/implementation/pre-p3/promotions"
_V1_RECEIPTS = frozenset(
    {
        "alpha-registry-foundation-v1.json",
        "p0-source-complete-v1.json",
        "p1-h-complete-v1.json",
        "p1-lts-ready-v1.json",
        "p2-qualified-v1.json",
        "p2-runtime-qualified-v1.json",
        "p2-source-complete-v1.json",
        "p3-baselines-frozen-v1.json",
        "p3-evaluation-protocol-frozen-v1.json",
    }
)
_V2_RECEIPTS = frozenset(
    name.replace("-v1.json", "-v2.json")
    for name in _V1_RECEIPTS
    if not name.startswith("p0-")
)
_CANDIDATE_NAME = "pre-p3-candidate-v2.json"
_PROMOTION_NAME = re.compile(r"[0-9a-f]{40}-v1\.json\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_AUTHORITY = {"broker": False, "live": False, "network": False, "production": False}
_V2_GATE_BY_NAME = {
    "alpha-registry-foundation-v2.json": "ALPHA_REGISTRY_FOUNDATION",
    "p1-h-complete-v2.json": "P1_H_COMPLETE",
    "p1-lts-ready-v2.json": "P1_LTS_READY",
    "p2-qualified-v2.json": "P2_QUALIFIED",
    "p2-runtime-qualified-v2.json": "P2_RUNTIME_QUALIFIED",
    "p2-source-complete-v2.json": "P2_SOURCE_COMPLETE",
    "p3-baselines-frozen-v2.json": "P3_BASELINES_FROZEN",
    "p3-evaluation-protocol-frozen-v2.json": "P3_EVALUATION_PROTOCOL_FROZEN",
}
_V2_NAME_BY_GATE = {gate: name for name, gate in _V2_GATE_BY_NAME.items()}
_V1_GATE_BY_NAME = {
    name.replace("-v2.json", "-v1.json"): gate
    for name, gate in _V2_GATE_BY_NAME.items()
}
_POLICY = {
    "excluded_exact_paths": [PROJECT_STATUS_PATH],
    "excluded_legacy_receipts": sorted(_V1_RECEIPTS),
    "excluded_v2_receipts": sorted(_V2_RECEIPTS | {_CANDIDATE_NAME}),
    "promotion_receipt_pattern": f"{PROMOTION_ROOT}/<promoted-commit-sha>-v1.json",
    "reject_modes": ["120000", "160000"],
    "schema_version": SOURCE_CLOSURE_SCHEMA,
}
SOURCE_CLOSURE_POLICY_SHA256 = hashlib.sha256(canonical_json_bytes(_POLICY)).hexdigest()


class ProvenanceError(ValueError):
    """Pre-P3 provenance is malformed, stale, or contradictory."""


def payload_sha256(payload: dict[str, Any]) -> str:
    """Hash a self-sealed payload without its receipt digest field."""
    return hashlib.sha256(
        canonical_json_bytes(
            {key: value for key, value in payload.items() if key != "receipt_sha256"}
        )
    ).hexdigest()


def _read_regular(path: Path, label: str) -> bytes:
    try:
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ProvenanceError(f"{label} is not a regular file")
        return path.read_bytes()
    except OSError as exc:
        raise ProvenanceError(f"{label} is unavailable") from exc


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=root,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError("git source authority is unavailable") from exc
    if result.returncode:
        raise ProvenanceError("git source authority is unavailable")
    return result.stdout


def _normalized_path(raw: bytes) -> str:
    try:
        path = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ProvenanceError("source path is not canonical UTF-8") from exc
    pure = PurePosixPath(path)
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "//" in path
        or pure.parts != tuple(part for part in path.split("/") if part)
        or any(part in {".", ".."} for part in pure.parts)
        or unicodedata.normalize("NFC", path) != path
    ):
        raise ProvenanceError("source path is not canonically normalized")
    return path


def _parse_tree(raw: bytes) -> list[tuple[str, str, bytes]]:
    entries: list[tuple[str, str, bytes]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_raw = record.split(b"\t", 1)
            mode_raw, kind, oid_raw = metadata.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            oid = oid_raw.decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise ProvenanceError("Git tree entry is malformed") from exc
        path = _normalized_path(path_raw)
        if mode == "120000":
            raise ProvenanceError(f"source closure rejects symlink: {path}")
        if mode == "160000" or kind != b"blob":
            raise ProvenanceError(f"source closure rejects gitlink: {path}")
        if mode not in {"100644", "100755"} or not re.fullmatch(r"[0-9a-f]{40,64}", oid):
            raise ProvenanceError("Git tree entry identity is invalid")
        entries.append((mode, oid, path_raw))
    if entries != sorted(entries, key=lambda item: item[2]) or len(
        {item[2] for item in entries}
    ) != len(entries):
        raise ProvenanceError("Git tree enumeration is not canonical")
    return entries


def _read_blobs(root: Path, entries: list[tuple[str, str, bytes]]) -> list[bytes]:
    raw = _git(
        root,
        "cat-file",
        "--batch",
        input_bytes=b"".join(oid.encode() + b"\n" for _, oid, _ in entries),
    )
    offset = 0
    blobs: list[bytes] = []
    for _, expected_oid, _ in entries:
        line_end = raw.find(b"\n", offset)
        if line_end < 0:
            raise ProvenanceError("Git blob batch output is truncated")
        header = raw[offset:line_end].split(b" ")
        if len(header) != 3:
            raise ProvenanceError("Git blob batch header is malformed")
        try:
            oid = header[0].decode("ascii")
            kind = header[1].decode("ascii")
            size = int(header[2])
        except (UnicodeError, ValueError) as exc:
            raise ProvenanceError("Git blob batch header is malformed") from exc
        start = line_end + 1
        end = start + size
        if (
            oid != expected_oid
            or kind != "blob"
            or size < 0
            or end >= len(raw)
            or raw[end : end + 1] != b"\n"
        ):
            raise ProvenanceError("Git blob batch binding is invalid")
        blobs.append(raw[start:end])
        offset = end + 1
    if offset != len(raw):
        raise ProvenanceError("Git blob batch output has trailing data")
    return blobs


def _valid_legacy_receipt(raw: bytes) -> bool:
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and set(payload)
        == {
            "authority",
            "evidence_sha256s",
            "gate",
            "receipt_sha256",
            "schema_version",
            "source_sha",
            "source_tree",
            "status",
        }
        and payload.get("schema_version") == "pre-p3-gate-receipt-v1"
        and payload.get("status") == "PASS"
        and payload.get("authority")
        == {"broker": False, "live": False, "network": False, "production": False}
        and payload.get("receipt_sha256") == payload_sha256(payload)
    )


def _excluded_output(path: str, mode: str, raw: bytes) -> bool:
    if path == PROJECT_STATUS_PATH:
        return mode == "100644"
    parent, _, name = path.rpartition("/")
    if parent == RECEIPT_ROOT and name in _V1_RECEIPTS:
        return mode == "100644" and _valid_legacy_receipt(raw)
    if parent == RECEIPT_ROOT and name in _V2_GATE_BY_NAME:
        try:
            validate_v2_gate_receipt(json.loads(raw), _V2_GATE_BY_NAME[name])
        except (UnicodeError, json.JSONDecodeError, ProvenanceError):
            return False
        return mode == "100644"
    if parent == RECEIPT_ROOT and name == _CANDIDATE_NAME:
        try:
            validate_candidate_certificate(json.loads(raw))
        except (UnicodeError, json.JSONDecodeError, ProvenanceError):
            return False
        return mode == "100644"
    if parent == PROMOTION_ROOT and _PROMOTION_NAME.fullmatch(name):
        try:
            payload = validate_promotion_receipt(json.loads(raw))
        except (UnicodeError, json.JSONDecodeError, ProvenanceError):
            return False
        return mode == "100644" and name == f'{payload["promoted_source"]["commit_sha"]}-v1.json'
    return False


def canonical_source_identity(root: Path, revision: str = "HEAD") -> dict[str, str]:
    """Return deterministic semantic identity for one committed source closure."""
    root = root.resolve()
    commit_sha = _git(root, "rev-parse", f"{revision}^{{commit}}").decode("ascii").strip()
    tree_sha = _git(root, "rev-parse", f"{revision}^{{tree}}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha) or not re.fullmatch(
        r"[0-9a-f]{40,64}", tree_sha
    ):
        raise ProvenanceError("Git revision identity is invalid")
    entries = _parse_tree(_git(root, "ls-tree", "-rz", "--full-tree", commit_sha))
    blobs = _read_blobs(root, entries)
    digest = hashlib.sha256()
    digest.update(b"trading-agent-source-closure/v1\0")
    digest.update(bytes.fromhex(SOURCE_CLOSURE_POLICY_SHA256))
    for (mode, _, path_raw), blob in zip(entries, blobs, strict=True):
        path = path_raw.decode("utf-8")
        if _excluded_output(path, mode, blob):
            continue
        digest.update(mode.encode("ascii") + b"\0")
        digest.update(struct.pack(">Q", len(path_raw)))
        digest.update(path_raw)
        digest.update(struct.pack(">Q", len(blob)))
        digest.update(blob)
    return {
        "closure_policy_sha256": SOURCE_CLOSURE_POLICY_SHA256,
        "closure_schema_version": SOURCE_CLOSURE_SCHEMA,
        "closure_sha256": digest.hexdigest(),
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
    }


def _validate_source(source: object) -> dict[str, str]:
    if not isinstance(source, dict) or set(source) != {
        "closure_policy_sha256",
        "closure_schema_version",
        "closure_sha256",
        "commit_sha",
        "tree_sha",
    }:
        raise ProvenanceError("receipt source field set is invalid")
    if (
        source.get("closure_schema_version") != SOURCE_CLOSURE_SCHEMA
        or source.get("closure_policy_sha256") != SOURCE_CLOSURE_POLICY_SHA256
        or not _HEX64.fullmatch(str(source.get("closure_sha256", "")))
        or not _HEX40.fullmatch(str(source.get("commit_sha", "")))
        or not _HEX40.fullmatch(str(source.get("tree_sha", "")))
    ):
        raise ProvenanceError("receipt source identity is invalid")
    return source


def _validate_qualification(qualification: object) -> dict[str, str]:
    if not isinstance(qualification, dict) or set(qualification) != {
        "completed_at_utc",
        "producer",
        "run_attempt",
        "run_id",
    }:
        raise ProvenanceError("receipt qualification field set is invalid")
    try:
        completed = datetime.fromisoformat(
            str(qualification["completed_at_utc"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ProvenanceError("receipt qualification timestamp is invalid") from exc
    if (
        not str(qualification["completed_at_utc"]).endswith("Z")
        or completed.utcoffset() is None
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", str(qualification["producer"]))
        or not re.fullmatch(r"[1-9][0-9]*", str(qualification["run_id"]))
        or not re.fullmatch(r"[1-9][0-9]*", str(qualification["run_attempt"]))
    ):
        raise ProvenanceError("receipt qualification identity is invalid")
    return qualification


def _validate_evidence(evidence: object) -> list[dict[str, str]]:
    if not isinstance(evidence, list) or not evidence:
        raise ProvenanceError("receipt evidence is absent")
    keys = ("kind", "locator", "name", "sha256")
    allowed_kinds = {
        "DERIVED_RECEIPT",
        "EXTERNAL_RECEIPT",
        "TOOL_IDENTITY",
        "TRACKED_BLOB",
    }
    for item in evidence:
        if not isinstance(item, dict) or set(item) != set(keys):
            raise ProvenanceError("receipt evidence field set is invalid")
        if (
            item.get("kind") not in allowed_kinds
            or not isinstance(item.get("locator"), str)
            or not item["locator"]
            or not isinstance(item.get("name"), str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", item["name"])
            or not _HEX64.fullmatch(str(item.get("sha256", "")))
        ):
            raise ProvenanceError("receipt evidence identity is invalid")
        if item["kind"] == "TRACKED_BLOB":
            _normalized_path(item["locator"].encode("utf-8"))
    ordered = sorted(evidence, key=lambda item: tuple(item[key] for key in keys))
    identities = {tuple(item[key] for key in keys) for item in evidence}
    if evidence != ordered or len(identities) != len(evidence):
        raise ProvenanceError("receipt evidence ordering is invalid")
    return evidence


def make_v2_gate_receipt(
    gate: str,
    *,
    source: dict[str, str],
    evidence: tuple[dict[str, str], ...],
    qualification: dict[str, str],
) -> dict[str, Any]:
    keys = ("kind", "locator", "name", "sha256")
    payload: dict[str, Any] = {
        "authority": dict(_SAFE_AUTHORITY),
        "evidence": sorted(evidence, key=lambda item: tuple(item[key] for key in keys)),
        "gate": gate,
        "qualification": qualification,
        "schema_version": "pre-p3-gate-receipt-v2",
        "source": source,
        "status": "PASS",
    }
    payload["receipt_sha256"] = payload_sha256(payload)
    return validate_v2_gate_receipt(payload, gate)


def _derived_receipt_digests(receipt: dict[str, Any]) -> dict[str, str]:
    return {
        item["locator"]: item["sha256"]
        for item in receipt["evidence"]
        if item["kind"] == "DERIVED_RECEIPT"
    }


def _validate_receipt_chain(receipts: dict[str, dict[str, Any]]) -> dict[str, str]:
    if set(receipts) != set(_V2_GATE_BY_NAME.values()):
        raise ProvenanceError("candidate gate receipt set is invalid")
    sources: list[dict[str, str]] = []
    for gate, receipt in receipts.items():
        sources.append(validate_v2_gate_receipt(receipt, gate)["source"])
    if any(source != sources[0] for source in sources[1:]):
        raise ProvenanceError("candidate receipts do not bind one source")
    expected = {
        "P1_LTS_READY": {"P1_H_COMPLETE": receipts["P1_H_COMPLETE"]["receipt_sha256"]},
        "P2_QUALIFIED": {
            "P2_RUNTIME_QUALIFIED": receipts["P2_RUNTIME_QUALIFIED"]["receipt_sha256"],
            "P2_SOURCE_COMPLETE": receipts["P2_SOURCE_COMPLETE"]["receipt_sha256"],
        },
    }
    for gate, bindings in expected.items():
        if _derived_receipt_digests(receipts[gate]) != bindings:
            raise ProvenanceError(f"candidate {gate} derived receipt binding is invalid")
    return sources[0]


def _validate_destination(destination: object) -> dict[str, str]:
    if not isinstance(destination, dict) or set(destination) != {
        "base_sha",
        "promotion_type",
        "ref",
        "repository",
    }:
        raise ProvenanceError("candidate destination field set is invalid")
    if (
        not _HEX40.fullmatch(str(destination.get("base_sha", "")))
        or destination.get("promotion_type")
        not in {"SQUASH", "REBASE", "CHERRY_PICK", "CONTROLLED_RELEASE"}
        or destination.get("ref") != "refs/heads/main"
        or destination.get("repository") != "nam176hermes/Trading-Agent"
    ):
        raise ProvenanceError("candidate destination identity is invalid")
    return destination


def make_candidate_certificate(
    *,
    receipts: dict[str, dict[str, Any]],
    legacy_receipts: dict[str, str],
    qualification: dict[str, str],
    destination: dict[str, str],
) -> dict[str, Any]:
    source = _validate_receipt_chain(receipts)
    payload: dict[str, Any] = {
        "authority": dict(_SAFE_AUTHORITY),
        "destination": destination,
        "gate_receipts": {
            gate: {
                "path": f"{RECEIPT_ROOT}/{_V2_NAME_BY_GATE[gate]}",
                "receipt_sha256": receipts[gate]["receipt_sha256"],
            }
            for gate in sorted(receipts)
        },
        "legacy_receipts": dict(sorted(legacy_receipts.items())),
        "live_eligible": False,
        "live_enabled": False,
        "p3_alpha_development_allowed": False,
        "qualification": qualification,
        "schema_version": "pre-p3-candidate-certification-v2",
        "source": source,
        "status": "PRE_P3_CANDIDATE_QUALIFIED",
    }
    payload["receipt_sha256"] = payload_sha256(payload)
    validate_candidate_certificate(payload)
    return payload


def _validate_run(
    run: object,
    *,
    destination: dict[str, str],
    promoted: dict[str, str],
) -> dict[str, str]:
    if not isinstance(run, dict) or set(run) != {
        "event",
        "ref",
        "repository",
        "run_attempt",
        "run_id",
        "sha",
        "workflow",
        "workflow_ref",
        "workflow_sha",
    }:
        raise ProvenanceError("promotion run field set is invalid")
    if (
        run.get("event") != "push"
        or run.get("ref") != destination["ref"]
        or run.get("repository") != destination["repository"]
        or run.get("sha") != promoted["commit_sha"]
        or run.get("workflow") != "Foundation"
        or run.get("workflow_ref")
        != (
            f'{destination["repository"]}/.github/workflows/'
            f'foundation.yml@{destination["ref"]}'
        )
        or run.get("workflow_sha") != promoted["commit_sha"]
        or not re.fullmatch(r"[1-9][0-9]*", str(run.get("run_id", "")))
        or not re.fullmatch(r"[1-9][0-9]*", str(run.get("run_attempt", "")))
    ):
        raise ProvenanceError("promotion run identity is invalid")
    return run


def make_promotion_receipt(
    *,
    root: Path,
    candidate_path: Path,
    promoted_revision: str,
    run: dict[str, str],
) -> dict[str, Any]:
    try:
        candidate_raw = _read_regular(candidate_path, "promotion candidate")
        candidate = json.loads(candidate_raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError("promotion candidate is unavailable") from exc
    candidate = validate_candidate_certificate(
        candidate, root=root, receipt_dir=candidate_path.parent
    )
    promoted_source = canonical_source_identity(root, promoted_revision)
    if not all(
        promoted_source[key] == candidate["source"][key]
        for key in ("closure_schema_version", "closure_policy_sha256", "closure_sha256")
    ):
        raise ProvenanceError("promoted source closure differs from qualified source closure")
    try:
        candidate_relative = candidate_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ProvenanceError("promotion candidate path escapes the repository") from exc
    payload: dict[str, Any] = {
        "authority": dict(_SAFE_AUTHORITY),
        "candidate": {
            "path": candidate_relative,
            "sha256": hashlib.sha256(candidate_raw).hexdigest(),
        },
        "destination": candidate["destination"],
        "promoted_source": promoted_source,
        "qualified_source": candidate["source"],
        "run": run,
        "schema_version": "pre-p3-promotion-receipt-v1",
        "status": "PRE_P3_PROMOTION_PROVENANCE_VALID",
    }
    payload["receipt_sha256"] = payload_sha256(payload)
    return validate_promotion_receipt(
        payload,
        root=root,
        candidate_path=candidate_path,
        current_revision=promoted_revision,
    )


def source_matches_current(root: Path, source: object, revision: str = "HEAD") -> bool:
    try:
        qualified = _validate_source(source)
        current = canonical_source_identity(root, revision)
    except ProvenanceError:
        return False
    return all(
        current[key] == qualified[key]
        for key in ("closure_schema_version", "closure_policy_sha256", "closure_sha256")
    )


def validate_v2_gate_receipt(
    payload: object,
    expected_gate: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "authority",
        "evidence",
        "gate",
        "qualification",
        "receipt_sha256",
        "schema_version",
        "source",
        "status",
    }:
        raise ProvenanceError("v2 receipt field set is invalid")
    if (
        payload.get("schema_version") != "pre-p3-gate-receipt-v2"
        or payload.get("gate") != expected_gate
    ):
        raise ProvenanceError("v2 receipt gate identity is invalid")
    if payload.get("status") != "PASS":
        raise ProvenanceError("v2 receipt status is invalid")
    if payload.get("authority") != _SAFE_AUTHORITY:
        raise ProvenanceError("v2 receipt authority is invalid")
    source = _validate_source(payload.get("source"))
    evidence = _validate_evidence(payload.get("evidence"))
    _validate_qualification(payload.get("qualification"))
    if payload.get("receipt_sha256") != payload_sha256(payload):
        raise ProvenanceError("v2 receipt self-digest is invalid")
    if root is not None:
        try:
            actual_source = canonical_source_identity(root, source["commit_sha"])
        except ProvenanceError as exc:
            raise ProvenanceError("v2 receipt source commit is unavailable") from exc
        if actual_source != source:
            raise ProvenanceError("v2 receipt source binding is invalid")
        for item in evidence:
            if item["kind"] != "TRACKED_BLOB":
                continue
            try:
                raw = _git(root, "show", f'{source["commit_sha"]}:{item["locator"]}')
            except ProvenanceError as exc:
                raise ProvenanceError("v2 receipt tracked evidence is unavailable") from exc
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ProvenanceError("v2 receipt tracked evidence digest is invalid")
    return payload


def validate_candidate_certificate(
    payload: object,
    *,
    root: Path | None = None,
    receipt_dir: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "authority",
        "destination",
        "gate_receipts",
        "legacy_receipts",
        "live_eligible",
        "live_enabled",
        "p3_alpha_development_allowed",
        "qualification",
        "receipt_sha256",
        "schema_version",
        "source",
        "status",
    }:
        raise ProvenanceError("candidate certificate field set is invalid")
    if (
        payload.get("schema_version") != "pre-p3-candidate-certification-v2"
        or payload.get("status") != "PRE_P3_CANDIDATE_QUALIFIED"
        or payload.get("authority") != _SAFE_AUTHORITY
        or payload.get("live_eligible") is not False
        or payload.get("live_enabled") is not False
        or payload.get("p3_alpha_development_allowed") is not False
    ):
        raise ProvenanceError("candidate certificate authority or status is invalid")
    source = _validate_source(payload.get("source"))
    destination = _validate_destination(payload.get("destination"))
    _validate_qualification(payload.get("qualification"))
    gate_receipts = payload.get("gate_receipts")
    legacy = payload.get("legacy_receipts")
    expected_gates = set(_V2_GATE_BY_NAME.values())
    if (
        not isinstance(gate_receipts, dict)
        or set(gate_receipts) != expected_gates
        or not isinstance(legacy, dict)
        or set(legacy) != expected_gates
        or any(not _HEX64.fullmatch(str(value)) for value in legacy.values())
    ):
        raise ProvenanceError("candidate receipt bindings are invalid")
    for gate, binding in gate_receipts.items():
        expected_name = _V2_NAME_BY_GATE[gate]
        if not isinstance(binding, dict) or binding != {
            "path": f"{RECEIPT_ROOT}/{expected_name}",
            "receipt_sha256": binding.get("receipt_sha256"),
        } or not _HEX64.fullmatch(str(binding.get("receipt_sha256", ""))):
            raise ProvenanceError("candidate gate receipt binding is invalid")
    if payload.get("receipt_sha256") != payload_sha256(payload):
        raise ProvenanceError("candidate certificate self-digest is invalid")
    if (root is None) != (receipt_dir is None):
        raise ProvenanceError("candidate repository validation inputs are incomplete")
    if root is not None and receipt_dir is not None:
        receipts: dict[str, dict[str, Any]] = {}
        for gate, binding in gate_receipts.items():
            path = root / binding["path"]
            if path.resolve().parent != receipt_dir.resolve():
                raise ProvenanceError("candidate receipt path is invalid")
            try:
                raw = _read_regular(path, "candidate gate receipt")
                receipt = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ProvenanceError("candidate gate receipt is unavailable") from exc
            receipt = validate_v2_gate_receipt(receipt, gate, root=root)
            if receipt["receipt_sha256"] != binding["receipt_sha256"]:
                raise ProvenanceError("candidate gate receipt digest is invalid")
            receipts[gate] = receipt
        if _validate_receipt_chain(receipts) != source:
            raise ProvenanceError("candidate source binding is invalid")
        for gate, expected_digest in legacy.items():
            name = next(
                name for name, item_gate in _V1_GATE_BY_NAME.items() if item_gate == gate
            )
            path = root / RECEIPT_ROOT / name
            try:
                raw = _read_regular(path, "candidate legacy receipt")
                legacy_payload = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ProvenanceError("candidate legacy receipt is unavailable") from exc
            if (
                not _valid_legacy_receipt(raw)
                or legacy_payload.get("gate") != gate
                or hashlib.sha256(raw).hexdigest() != expected_digest
            ):
                raise ProvenanceError("candidate legacy receipt binding is invalid")
        try:
            base = _git(
                root, "merge-base", destination["base_sha"], source["commit_sha"]
            ).decode().strip()
            if base != destination["base_sha"]:
                raise ProvenanceError("candidate base is not an ancestor of qualified source")
        except UnicodeError as exc:
            raise ProvenanceError("candidate ancestry output is invalid") from exc
    return payload


def validate_promotion_receipt(
    payload: object,
    *,
    root: Path | None = None,
    candidate_path: Path | None = None,
    current_revision: str = "HEAD",
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "authority",
        "candidate",
        "destination",
        "promoted_source",
        "qualified_source",
        "receipt_sha256",
        "run",
        "schema_version",
        "status",
    }:
        raise ProvenanceError("promotion receipt field set is invalid")
    if (
        payload.get("schema_version") != "pre-p3-promotion-receipt-v1"
        or payload.get("status") != "PRE_P3_PROMOTION_PROVENANCE_VALID"
        or payload.get("authority") != _SAFE_AUTHORITY
    ):
        raise ProvenanceError("promotion receipt authority or status is invalid")
    destination = _validate_destination(payload.get("destination"))
    qualified = _validate_source(payload.get("qualified_source"))
    promoted = _validate_source(payload.get("promoted_source"))
    _validate_run(payload.get("run"), destination=destination, promoted=promoted)
    candidate_binding = payload.get("candidate")
    if (
        not isinstance(candidate_binding, dict)
        or set(candidate_binding) != {"path", "sha256"}
        or candidate_binding.get("path") != f"{RECEIPT_ROOT}/{_CANDIDATE_NAME}"
        or not _HEX64.fullmatch(str(candidate_binding.get("sha256", "")))
    ):
        raise ProvenanceError("promotion candidate binding is invalid")
    if payload.get("receipt_sha256") != payload_sha256(payload):
        raise ProvenanceError("promotion receipt self-digest is invalid")
    if (root is None) != (candidate_path is None):
        raise ProvenanceError("promotion repository validation inputs are incomplete")
    if root is not None and candidate_path is not None:
        try:
            raw = _read_regular(candidate_path, "promotion candidate")
            candidate = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProvenanceError("promotion candidate is unavailable") from exc
        if hashlib.sha256(raw).hexdigest() != candidate_binding["sha256"]:
            raise ProvenanceError("promotion candidate digest is invalid")
        candidate = validate_candidate_certificate(
            candidate, root=root, receipt_dir=candidate_path.parent
        )
        if candidate["source"] != qualified or candidate["destination"] != destination:
            raise ProvenanceError("promotion candidate provenance is inconsistent")
        if canonical_source_identity(root, promoted["commit_sha"]) != promoted:
            raise ProvenanceError("promotion commit source binding is invalid")
        if not all(
            promoted[key] == qualified[key]
            for key in ("closure_schema_version", "closure_policy_sha256", "closure_sha256")
        ):
            raise ProvenanceError("promotion source closures differ")
        try:
            base = _git(
                root, "merge-base", destination["base_sha"], promoted["commit_sha"]
            ).decode().strip()
            if base != destination["base_sha"]:
                raise ProvenanceError("promotion base is not an ancestor of promoted source")
        except UnicodeError as exc:
            raise ProvenanceError("promotion ancestry output is invalid") from exc
        if not source_matches_current(root, promoted, current_revision):
            raise ProvenanceError("current source closure differs from promoted source closure")
    return payload


__all__ = [
    "PROJECT_STATUS_PATH",
    "PROMOTION_ROOT",
    "ProvenanceError",
    "RECEIPT_ROOT",
    "SOURCE_CLOSURE_POLICY_SHA256",
    "SOURCE_CLOSURE_SCHEMA",
    "canonical_source_identity",
    "make_candidate_certificate",
    "make_promotion_receipt",
    "make_v2_gate_receipt",
    "payload_sha256",
    "source_matches_current",
    "validate_candidate_certificate",
    "validate_promotion_receipt",
    "validate_v2_gate_receipt",
]
