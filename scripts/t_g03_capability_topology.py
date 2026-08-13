#!/usr/bin/env python3
"""Fail-closed capability-topology receipts for the locked hosted inventory."""

from __future__ import annotations

import hashlib
import json
import csv
import argparse
from collections.abc import Callable
from contextlib import contextmanager
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Any


LOCKED_INVENTORY_SHA256 = "86c157c8394f16e381d1e53a6884b6c3d93af5520ea9bdd6b3abd9efbc588a93"
LOCKED_CLOSURE_SHA256 = "4feaed5b9e73f60ab192938a8b8b51b873f61e139a66b4926e7429c80144154a"
LOCKED_GOVERNED_NODE_IDS_SHA256 = "aedeffcf5b9ad3d7704b3f6a15822f9862d9b84b279cc8a66b193b331262f7f0"
RECEIPT_SCHEMA = "t-g03a-capability-receipt/v1"
PORTABLE_CLOSURE_PROOF_SCHEMA = "t-g03a-portable-closure-proof/v2"
CLOSED_NODE_PROOF_SCHEMA = "t-g03a-closed-node-proof/v2"
FOUNDATION_CONTEXT_SCHEMA = "t-g03a-foundation-context/v1"
RESERVATION_SCHEMA = "t-g03a-topology-reservation/v2"
BASELINE_SCHEMA = "t-g03a-portable-root-baseline/v4"
REMAINDER_SCHEMA = "t-g03a-portable-root-remainder/v2"
FAILURE_DIAGNOSTIC_SCHEMA = "t-g03a-portable-root-failure-diagnostic/v3"
POLICY_NONACCEPTANCE_SCHEMA = "t-g03a-policy-validation-nonacceptance/v1"
UNSAFE_RAW_REASON_NONACCEPTANCE_SCHEMA = "t-g03a-unsafe-raw-reason-nonacceptance/v1"
POLICY_SNAPSHOT_SCHEMA = "t-g03a-portable-root-policy-snapshot/v1"
POLICY_ENTRY_SCHEMA = "t-g03a-skip-policy-entry/v1"
REASON_COMMITMENT_SCHEMA = "t-g03a-policy-reason-commitment/v1"
RECEIPT_KEYS = frozenset({
    "schema_version", "foundation_run_id", "foundation_head_sha", "inventory_sha256",
    "lane", "capability_or_authority_code", "expected_node_ids", "collected_node_ids",
    "completeness_sha256", "preflight_state", "redacted_fact_class", "outcome",
    "receipt_sha256",
})
FAILURE_DIAGNOSTIC_KEYS = frozenset({
    "schema_version", "diagnostic_only", "foundation_run_id", "foundation_head_sha",
    "foundation_validation_date", "foundation_context_sha256", "inventory_sha256", "baseline_candidate_ids_sha256", "baseline_node_list_sha256",
    "remainder_candidate_ids_sha256", "remainder_node_list_sha256", "custody_policy_sha256",
    "custody_postcheck_status", "pytest_exit_status", "policy_snapshot",
    "policy_snapshot_sha256", "observations", "diagnostic_sha256",
})
POLICY_NONACCEPTANCE_KEYS = frozenset({
    "schema_version", "diagnostic_only", "foundation_run_id", "foundation_head_sha",
    "foundation_validation_date", "foundation_context_sha256", "inventory_sha256",
    "baseline_sha256", "baseline_candidate_ids_sha256", "baseline_node_list_sha256",
    "remainder_sha256", "remainder_candidate_ids_sha256", "remainder_node_list_sha256",
    "custody_policy_sha256", "custody_status", "policy_validation_stage",
    "policy_validation_class", "policy_source_hash_status", "policy_source_sha256",
    "nonacceptance_sha256",
})
UNSAFE_RAW_REASON_NONACCEPTANCE_KEYS = frozenset({
    "schema_version", "diagnostic_only", "foundation_run_id", "foundation_head_sha",
    "foundation_validation_date", "foundation_context_sha256", "inventory_sha256",
    "baseline_sha256", "baseline_candidate_ids_sha256", "baseline_node_list_sha256",
    "remainder_sha256", "remainder_candidate_ids_sha256", "remainder_node_list_sha256",
    "custody_policy_sha256", "custody_postcheck_status", "pytest_exit_status",
    "raw_reason_nonacceptance_state", "nonacceptance_sha256",
})
POLICY_VALIDATION_STAGES = frozenset({
    "SOURCE_ACQUISITION_HEAD_BINDING", "SHARED_VALIDATOR_IMPORT", "STRICT_JSON_PARSE",
    "SHARED_ALLOWLIST_VALIDATION", "ROOT_PROJECTION_REASON_NORMALIZATION",
    "POST_CUSTODY_REREAD_COMPARISON",
})
POLICY_STAGE_CLASSES = {
    "SOURCE_ACQUISITION_HEAD_BINDING": frozenset({"POLICY_SOURCE_DRIFT", "POLICY_VALIDATION_INVALID"}),
    "SHARED_VALIDATOR_IMPORT": frozenset({"POLICY_VALIDATION_INVALID"}),
    "STRICT_JSON_PARSE": frozenset({"POLICY_SCHEMA_INVALID", "POLICY_VALIDATION_INVALID"}),
    "SHARED_ALLOWLIST_VALIDATION": frozenset({
        "POLICY_SCHEMA_INVALID", "POLICY_FIELD_TYPE_INVALID", "POLICY_REVIEW_DATE_INVALID",
        "POLICY_REVIEW_DATE_EXPIRED", "POLICY_REASON_NORMALIZATION_INVALID",
        "POLICY_DUPLICATE_ENTRY", "POLICY_VALIDATION_INVALID",
    }),
    "ROOT_PROJECTION_REASON_NORMALIZATION": frozenset({
        "POLICY_FIELD_TYPE_INVALID", "POLICY_REASON_NORMALIZATION_INVALID", "POLICY_VALIDATION_INVALID",
    }),
    "POST_CUSTODY_REREAD_COMPARISON": frozenset({
        "POLICY_SOURCE_DRIFT", "POLICY_SCHEMA_INVALID", "POLICY_FIELD_TYPE_INVALID",
        "POLICY_REVIEW_DATE_INVALID", "POLICY_REVIEW_DATE_EXPIRED",
        "POLICY_REASON_NORMALIZATION_INVALID", "POLICY_DUPLICATE_ENTRY", "POLICY_VALIDATION_INVALID",
    }),
}
POLICY_SNAPSHOT_KEYS = frozenset({
    "snapshot_schema_version", "allowlist_schema_version", "allowlist_source_sha256",
    "policy_entry_schema_version", "entries",
})
POLICY_SNAPSHOT_ENTRY_KEYS = frozenset({
    "component", "test_node_id", "outcome", "allowed_in_ci", "reason_class",
    "normalized_reason_commitment_sha256", "policy_entry_sha256",
})
DIAGNOSTIC_OBSERVATION_KEYS = frozenset({
    "test_node_id", "component", "outcome", "phase", "xfail_state", "reason_class",
    "reason_provenance", "normalized_reason_commitment_sha256", "policy_match_result",
    "existing_policy_entry_sha256",
})
V1_WHITE_SPACE = frozenset({
    *range(0x0009, 0x000E), 0x0020, 0x0085, 0x00A0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
})


class TopologyError(RuntimeError):
    """A topology binding or receipt is invalid."""


@dataclass(frozen=True)
class _PolicyStageFailure:
    stage: str
    public_class: str
    source_hash_status: str
    source_sha256: str


class _PolicyStageError(TopologyError):
    def __init__(self, failure: _PolicyStageFailure) -> None:
        self.failure = failure
        super().__init__(f"policy validation failed: {failure.public_class}")


def _prepare_private_evidence_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = path.lstat()
    except OSError as exc:
        raise TopologyError("evidence directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid():
        raise TopologyError("evidence directory is unsafe")
    os.chmod(path, 0o700)
    current = path.lstat()
    if current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o700:
        raise TopologyError("evidence directory is not private")


def _artifact_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_descriptor_bytes(descriptor: int) -> bytes:
    """Read a descriptor without reopening its pathname."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _read_private_regular_file(path: Path, *, label: str) -> bytes:
    """Read one publisher-owned 0600 artifact with no-follow identity checks."""
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise TopologyError(f"{label} is not a private regular file")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except TopologyError:
        raise
    except OSError as exc:
        raise TopologyError(f"{label} is not a private regular file") from exc
    try:
        opened = os.fstat(descriptor)
        if _artifact_identity(opened) != _artifact_identity(before):
            raise TopologyError(f"{label} identity changed before read")
        raw = _read_descriptor_bytes(descriptor)
        after_descriptor = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            _artifact_identity(after_descriptor) != _artifact_identity(opened)
            or _artifact_identity(after_path) != _artifact_identity(opened)
        ):
            raise TopologyError(f"{label} identity changed during read")
        return raw
    except TopologyError:
        raise
    except OSError as exc:
        raise TopologyError(f"{label} identity changed during read") from exc
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class _RetainedClosureArtifacts:
    directory_path: Path
    directory_descriptor: int
    directory_identity: tuple[int, ...]
    proof_descriptor: int
    proof_identity: tuple[int, ...]
    proof_raw: bytes
    governance_descriptor: int
    governance_identity: tuple[int, ...]
    governance_raw: bytes


def _open_private_artifact_leaf(
    directory_descriptor: int, name: str, *, label: str,
) -> tuple[int, tuple[int, ...]]:
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
        ):
            raise TopologyError(f"{label} is not a private regular file")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
        opened = os.fstat(descriptor)
    except TopologyError:
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise TopologyError(f"{label} is not a private regular file") from exc
    if _artifact_identity(opened) != _artifact_identity(before):
        os.close(descriptor)
        raise TopologyError(f"{label} identity changed before read")
    return descriptor, _artifact_identity(opened)


@contextmanager
def _retained_private_closure_artifacts(path: Path):
    """Retain the private directory and both closure leaves as one artifact set."""
    if path.name != "portable-defect-closure-proof.json":
        raise TopologyError("portable closure proof path is malformed")
    directory_path = path.parent
    directory_descriptor = -1
    proof_descriptor = -1
    governance_descriptor = -1
    try:
        try:
            before = directory_path.lstat()
            if (
                not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o700
            ):
                raise TopologyError("portable closure private artifact directory is unsafe")
            directory_descriptor = os.open(
                directory_path,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            opened_directory = os.fstat(directory_descriptor)
        except TopologyError:
            raise
        except OSError as exc:
            raise TopologyError("portable closure private artifact directory is unsafe") from exc
        directory_identity = _artifact_identity(opened_directory)
        if directory_identity != _artifact_identity(before):
            raise TopologyError("portable closure artifact directory identity changed")
        proof_descriptor, proof_identity = _open_private_artifact_leaf(
            directory_descriptor, path.name, label="portable closure proof",
        )
        governance_descriptor, governance_identity = _open_private_artifact_leaf(
            directory_descriptor,
            "portable-defect-closure.governance.json",
            label="portable closure governance report",
        )
        proof_raw = _read_descriptor_bytes(proof_descriptor)
        governance_raw = _read_descriptor_bytes(governance_descriptor)
        yield _RetainedClosureArtifacts(
            directory_path=directory_path,
            directory_descriptor=directory_descriptor,
            directory_identity=directory_identity,
            proof_descriptor=proof_descriptor,
            proof_identity=proof_identity,
            proof_raw=proof_raw,
            governance_descriptor=governance_descriptor,
            governance_identity=governance_identity,
            governance_raw=governance_raw,
        )
    finally:
        for descriptor in (governance_descriptor, proof_descriptor, directory_descriptor):
            if descriptor >= 0:
                os.close(descriptor)


def _postcheck_private_closure_artifacts(artifacts: _RetainedClosureArtifacts) -> None:
    """Reject any directory or leaf replacement before accepting cross-artifact PASS."""
    try:
        named_directory = artifacts.directory_path.lstat()
        held_directory = os.fstat(artifacts.directory_descriptor)
        named_proof = os.stat(
            "portable-defect-closure-proof.json",
            dir_fd=artifacts.directory_descriptor,
            follow_symlinks=False,
        )
        named_governance = os.stat(
            "portable-defect-closure.governance.json",
            dir_fd=artifacts.directory_descriptor,
            follow_symlinks=False,
        )
        held_proof = os.fstat(artifacts.proof_descriptor)
        held_governance = os.fstat(artifacts.governance_descriptor)
    except OSError as exc:
        raise TopologyError("portable closure artifact identity changed during validation") from exc
    if (
        _artifact_identity(named_directory) != artifacts.directory_identity
        or _artifact_identity(held_directory) != artifacts.directory_identity
        or _artifact_identity(named_proof) != artifacts.proof_identity
        or _artifact_identity(held_proof) != artifacts.proof_identity
        or _artifact_identity(named_governance) != artifacts.governance_identity
        or _artifact_identity(held_governance) != artifacts.governance_identity
    ):
        raise TopologyError("portable closure artifact identity changed during validation")


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
    "NATIVE-BWRAP-OS-SANDBOX": "NATIVE_CAPABILITY_REQUIRED",
    "NATIVE-USERNS-ROOT-PROVISION": "NATIVE_CAPABILITY_REQUIRED",
    "EXT-PHASE3B-CORPUS": "EXTERNAL_AUTHORITY_REQUIRED",
    "EXT-LEGACY-UV-AUTHORITY": "EXTERNAL_AUTHORITY_REQUIRED",
}
CLOSED_CODE_CLASSIFICATION = {
    "SRC-PHASE4B-FAKEROOT-IDENTITY": "PORTABLE_SOURCE_DEFECT",
    "SRC-SEALEDUV-BWRAP-PREFLIGHT": "PORTABLE_SOURCE_DEFECT",
    "SRC-SEMANTIC-FIXTURE-IDENTITY": "PORTABLE_SOURCE_DEFECT",
}
CLOSED_CODE_COUNTS = {
    "SRC-PHASE4B-FAKEROOT-IDENTITY": 2,
    "SRC-SEALEDUV-BWRAP-PREFLIGHT": 27,
    "SRC-SEMANTIC-FIXTURE-IDENTITY": 3,
}
CLOSED_SOURCE_CODE = {
    "tests/foundation/test_nautilus_sealed_uv_exec.py": "SRC-SEALEDUV-BWRAP-PREFLIGHT",
    "tests/runtime_release/test_provision_script.py": "SRC-PHASE4B-FAKEROOT-IDENTITY",
    "tests/runtime_release/test_semantic.py": "SRC-SEMANTIC-FIXTURE-IDENTITY",
}
CLOSURE_COLUMNS = (
    "test_node_id", "source_file", "former_capability_code", "fix_commit",
    "proof_command", "proof_result_digest", "closed_at_foundation_date",
)
CLOSURE_PROOF_COMMAND = "PYTEST_EXACT_NODE_V1"
CLOSURE_RELATIVE_PATH = Path("docs/implementation/foundation-portable-defect-closure.tsv")
CLOSURE_PROOF_KEYS = frozenset({
    "schema_version", "foundation_run_id", "foundation_head_sha",
    "foundation_validation_date", "foundation_context_sha256",
    "inventory_sha256", "closure_sha256", "closure_node_ids",
    "closure_node_ids_sha256", "proof_command", "proof_result_digests",
    "custody_policy", "custody_policy_sha256", "governance_report_sha256",
    "outcome", "closure_proof_sha256",
})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^(0|[1-9][0-9]*)$")
FOUNDATION_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
ASCII = re.compile(r"^[\x21-\x7e]+$")
REDACTED_FACT_CLASSES = frozenset({
    "SOURCE_TEST_EXECUTED", "NATIVE_COMPONENT_ABSENT", "NATIVE_IDENTITY_INVALID",
    "NATIVE_CAPABILITY_VALIDATED", "RUNNER_POLICY_DISALLOWS_USERNS", "NATIVE_PROBE_INVALID",
    "AUTHORITY_ROOT_ABSENT", "AUTHORITY_EXECUTABLE_ABSENT", "AUTHORITY_COMPLETE_VALIDATED",
    "AUTHORITY_PARTIAL", "AUTHORITY_INVALID",
})
TRUSTED_UNSHARE = Path("/usr/bin/unshare")
PHASE3B_ROOT = Path("/home/thenam176/.hermes/crypto-research")
LEGACY_UV = Path("/home/thenam176/.local/bin/uv")
LEGACY_UV_SHA256 = "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4"
LEGACY_UV_VERSION = "uv 0.11.7 (x86_64-unknown-linux-gnu)"
ROOT = Path(__file__).resolve().parents[1]
CLOSURE_PATH = ROOT / CLOSURE_RELATIVE_PATH
PHASE3B_REQUIRED_ENTRIES = (
    ("asset_registry.py", False),
    ("memory/decisions.jsonl", False),
    ("memory/trading.db", False),
    (".dexter/scratchpad", True),
    ("reports", True),
    ("decisions", True),
)
LEGACY_CLOSURE_ENTRIES = (
    (".venv/bin/python", False),
    (".venv/pyvenv.cfg", False),
    ("pyproject.toml", False),
    ("uv.lock", False),
    ("nautilus_parity_adapter.py", False),
)
PORTABLE_ROOT_MARKER = "not runtime_postgres and not host_coupled"
PORTABLE_ROOT_POLICY = {
    "governance_plugin": "scripts.test_governance_pytest",
    "marker_expression": PORTABLE_ROOT_MARKER,
    "portable_argument": "--portable-embedded-proof",
    "root_selector": "tests",
}


@dataclass(frozen=True)
class InventoryRow:
    node_id: str
    classification: str
    code: str


@dataclass(frozen=True)
class ClosureRow:
    node_id: str
    source_file: str
    former_code: str
    fix_commit: str
    proof_command: str
    proof_result_digest: str
    closed_at_foundation_date: str


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
    if len(rows) != 30:
        raise TopologyError("inventory row count drift")
    counts = {code: sum(row.code == code for row in rows) for code in CODE_CLASSIFICATION}
    if counts != {
        "NATIVE-BWRAP-OS-SANDBOX": 16,
        "NATIVE-USERNS-ROOT-PROVISION": 8,
        "EXT-PHASE3B-CORPUS": 3,
        "EXT-LEGACY-UV-AUTHORITY": 3,
    }:
        raise TopologyError("inventory native or external mapping drift")
    return tuple(rows)


def _canonical_passing_observation(node_id: str) -> dict[str, object]:
    return {
        "test_node_id": node_id,
        "component": "root",
        "outcome": "passed",
        "reason": "",
        "phase": "call",
    }


def _closed_node_proof_payload(
    row: ClosureRow, observation: dict[str, object] | None = None,
) -> dict[str, object]:
    """Canonical row proof reproduced only after its exact pytest node passes."""
    return {
        "schema_version": CLOSED_NODE_PROOF_SCHEMA,
        "test_node_id": row.node_id,
        "source_file": row.source_file,
        "former_capability_code": row.former_code,
        "fix_commit": row.fix_commit,
        "proof_command": row.proof_command,
        "observed_governance_record": (
            _canonical_passing_observation(row.node_id)
            if observation is None else observation
        ),
    }


def closed_node_proof_digest(
    row: ClosureRow, observation: dict[str, object] | None = None,
) -> str:
    return _sha256(_closed_node_proof_payload(row, observation))


def _commit_touches_source(commit: str, source_file: str, *, head_sha: str) -> bool:
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True, timeout=10,
        )
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, head_sha], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True, timeout=10,
        )
        changed = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit, "--", source_file],
            cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return False
    return source_file in changed


def parse_portable_defect_closure(raw: bytes, *, head_sha: str) -> tuple[ClosureRow, ...]:
    """Validate canonical closure bytes, historical commit evidence, and proof digests."""
    if not HEAD_SHA.fullmatch(head_sha):
        raise TopologyError("closure head is malformed")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TopologyError("closure ledger is not UTF-8") from exc
    if (
        text.startswith("\ufeff")
        or not text.endswith("\n")
        or "\n\n" in text
        or "\r" in text
        or '"' in text
    ):
        raise TopologyError("closure ledger has nonliteral or noncanonical rows")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    if tuple(reader.fieldnames or ()) != CLOSURE_COLUMNS:
        raise TopologyError("closure ledger schema drift")
    if any(line.count("\t") != len(CLOSURE_COLUMNS) - 1 for line in text.splitlines()):
        raise TopologyError("closure ledger has nonliteral or noncanonical rows")
    rows: list[ClosureRow] = []
    seen: set[str] = set()
    commit_bindings: set[tuple[str, str]] = set()
    for index, item in enumerate(reader, start=2):
        if item is None or set(item) != set(CLOSURE_COLUMNS) or any(
            not isinstance(value, str) or not value for value in item.values()
        ):
            raise TopologyError(f"closure row {index} is blank or malformed")
        row = ClosureRow(
            item["test_node_id"], item["source_file"], item["former_capability_code"],
            item["fix_commit"], item["proof_command"], item["proof_result_digest"],
            item["closed_at_foundation_date"],
        )
        if row.node_id in seen or not ASCII.fullmatch(row.node_id):
            raise TopologyError(f"closure row {index} has duplicate or invalid node")
        if not row.node_id.startswith(f"{row.source_file}::") or not _is_portable_root_pytest_node_id(row.node_id):
            raise TopologyError(f"closure row {index} has wrong source file")
        if row.former_code not in CLOSED_CODE_CLASSIFICATION:
            raise TopologyError(f"closure row {index} has unknown former code")
        if CLOSED_SOURCE_CODE.get(row.source_file) != row.former_code:
            raise TopologyError(f"closure row {index} has wrong former code for source")
        if row.proof_command != CLOSURE_PROOF_COMMAND:
            raise TopologyError(f"closure row {index} has unknown proof command")
        if not HEAD_SHA.fullmatch(row.fix_commit):
            raise TopologyError(f"closure row {index} has malformed fix commit")
        if not HEX64.fullmatch(row.proof_result_digest):
            raise TopologyError(f"closure row {index} has malformed proof digest")
        if parse_foundation_validation_date(row.closed_at_foundation_date) != date(2026, 8, 13):
            raise TopologyError(f"closure row {index} has wrong Foundation date")
        if row.proof_result_digest != closed_node_proof_digest(row):
            raise TopologyError(f"closure row {index} has mismatched proof digest")
        seen.add(row.node_id)
        commit_bindings.add((row.fix_commit, row.source_file))
        rows.append(row)
    if len(rows) != 32:
        raise TopologyError("closure row count drift")
    counts = {code: sum(row.former_code == code for row in rows) for code in CLOSED_CODE_COUNTS}
    if counts != CLOSED_CODE_COUNTS:
        raise TopologyError("closure former-code mapping drift")
    for commit, source_file in sorted(commit_bindings):
        if not _commit_touches_source(commit, source_file, head_sha=head_sha):
            raise TopologyError("closure fix commit is absent, nonancestor, or does not touch source")
    return tuple(rows)


def load_portable_defect_closure(
    path: Path = CLOSURE_PATH, *, head_sha: str | None = None,
) -> tuple[ClosureRow, ...]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != LOCKED_CLOSURE_SHA256:
        raise TopologyError("locked closure hash drift")
    if head_sha is None:
        try:
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, check=True, timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise TopologyError("closure head is unavailable") from exc
    return parse_portable_defect_closure(raw, head_sha=head_sha)


def load_governance_state(
    inventory: Path, closure: Path = CLOSURE_PATH, *, head_sha: str,
) -> tuple[tuple[InventoryRow, ...], tuple[ClosureRow, ...]]:
    active = load_inventory(inventory)
    closed = load_portable_defect_closure(closure, head_sha=head_sha)
    overlap = {row.node_id for row in active} & {row.node_id for row in closed}
    if overlap:
        raise TopologyError("active inventory overlaps portable closure")
    governed = tuple(sorted({row.node_id for row in active} | {row.node_id for row in closed}))
    if len(governed) != 62 or _ids_sha256(governed) != LOCKED_GOVERNED_NODE_IDS_SHA256:
        raise TopologyError("active and closure governed-node set drift")
    return active, closed


def install_inventory(source: Path, evidence_root: Path) -> Path:
    """Copy verified bytes once into private evidence; never overwrite a prior run."""
    load_inventory(source)
    _prepare_private_evidence_directory(evidence_root)
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


def install_portable_defect_closure(
    source: Path, evidence_root: Path, *, head_sha: str,
) -> Path:
    """Install the validated closure bytes once beside the active inventory."""
    load_portable_defect_closure(source, head_sha=head_sha)
    _prepare_private_evidence_directory(evidence_root)
    destination = evidence_root / CLOSURE_RELATIVE_PATH.name
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
        raise TopologyError("installed closure byte comparison failed")
    load_portable_defect_closure(destination, head_sha=head_sha)
    return destination


def reserve_topology_evidence(
    evidence_root: Path, *, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> None:
    """Seal an empty topology namespace before collection can mutate observations."""
    _prepare_private_evidence_directory(evidence_root)
    topology_root = evidence_root / "capability-topology"
    _prepare_private_evidence_directory(topology_root)
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    context: dict[str, object] | None = None
    if foundation_context_path is not None:
        context = load_foundation_context(
            foundation_context_path, run_id=run_id, head_sha=head_sha,
        )
    targets = [
        evidence_root / "t-g03a-hosted-failure-inventory.tsv",
        evidence_root / CLOSURE_RELATIVE_PATH.name,
        topology_root / ".reservation",
        topology_root / "portable-root-baseline.json",
        topology_root / "portable-root-candidates.txt",
        topology_root / "portable-root-collection.governance.json",
        topology_root / "portable-root-remainder.json",
        topology_root / "portable-root-remainder.txt",
        topology_root / "portable-root-remainder.governance.json",
        topology_root / "portable-root-remainder.failure-diagnostic.json",
        topology_root / "portable-root-remainder.unsafe-raw-reason-nonacceptance.json",
        topology_root / "policy-validation-nonacceptance.json",
        topology_root / "portable-defect-closure.governance.json",
        topology_root / "portable-defect-closure-proof.json",
    ]
    for code in CODE_CLASSIFICATION:
        targets.extend((topology_root / f"{code}.json", topology_root / f"{code}.governance.json"))
    for code in CLOSED_CODE_CLASSIFICATION:
        targets.extend((topology_root / f"{code}.json", topology_root / f"{code}.governance.json"))
    if any(os.path.lexists(path) for path in targets):
        raise TopologyError("topology evidence namespace is already reserved or populated")
    reservation_document: dict[str, object] = {
        "foundation_head_sha": head_sha,
        "foundation_run_id": run_id,
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "closure_sha256": LOCKED_CLOSURE_SHA256,
    }
    if context is not None:
        reservation_document = {
            "schema_version": RESERVATION_SCHEMA,
            **reservation_document,
            "foundation_context_sha256": context["foundation_context_sha256"],
        }
    reservation = canonical_json_bytes(reservation_document)
    descriptor = os.open(topology_root / ".reservation", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(reservation)
        stream.flush()
        os.fsync(stream.fileno())


def _require_topology_reservation(
    evidence_root: Path, run_id: str, head_sha: str,
    foundation_context: dict[str, object] | None = None,
) -> None:
    path = evidence_root / "capability-topology/.reservation"
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError("topology evidence reservation is missing") from exc
    expected: dict[str, object] = {
        "foundation_head_sha": head_sha,
        "foundation_run_id": run_id,
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "closure_sha256": LOCKED_CLOSURE_SHA256,
    }
    if foundation_context is not None:
        expected = {
            "schema_version": RESERVATION_SCHEMA,
            **expected,
            "foundation_context_sha256": foundation_context["foundation_context_sha256"],
        }
    if canonical_json_bytes(document) != raw or document != expected:
        raise TopologyError("topology evidence reservation binding drift")


def canonical_json_bytes(document: object) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def parse_foundation_validation_date(value: object) -> date:
    """Parse the sole canonical Foundation policy-validation date spelling."""
    if not isinstance(value, str) or not FOUNDATION_DATE.fullmatch(value):
        raise TopologyError("Foundation validation date is malformed")
    try:
        encoded = value.encode("ascii")
        parsed = date.fromisoformat(value)
    except (UnicodeEncodeError, ValueError) as exc:
        raise TopologyError("Foundation validation date is malformed") from exc
    if encoded.decode("ascii") != value or parsed.isoformat() != value:
        raise TopologyError("Foundation validation date is malformed")
    return parsed


def _reject_validation_date_environment() -> None:
    if "FOUNDATION_VALIDATION_DATE" in os.environ:
        raise TopologyError("Foundation validation date environment is forbidden")


def _active_foundation_identity() -> tuple[str, str]:
    current_run = os.environ.get("GITHUB_RUN_ID")
    if not current_run or not RUN_ID.fullmatch(current_run) or current_run == "0":
        raise TopologyError("authoritative GitHub run context is required")
    try:
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise TopologyError("checked-out Foundation head is unavailable") from exc
    if not HEAD_SHA.fullmatch(current_head):
        raise TopologyError("checked-out Foundation head is malformed")
    return current_run, current_head


def _foundation_context_path(evidence_root: Path) -> Path:
    return evidence_root / "capability-topology" / "foundation-context.json"


def _capture_foundation_context(
    evidence_root: Path, *, clock: Callable[[], datetime] | None = None,
) -> Path:
    """Capture the one UTC date before the portable Foundation wrapper exists.

    ``clock`` is intentionally private and exists only for unit tests; no CLI,
    Make variable, or environment value can provide a date.
    """
    _reject_validation_date_environment()
    run_id, head_sha = _active_foundation_identity()
    now = datetime.now(timezone.utc) if clock is None else clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise TopologyError("Foundation clock is malformed")
    validation_date = now.astimezone(timezone.utc).date().isoformat()
    parse_foundation_validation_date(validation_date)
    _prepare_private_evidence_directory(evidence_root)
    topology_root = evidence_root / "capability-topology"
    _prepare_private_evidence_directory(topology_root)
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    context_path = _foundation_context_path(evidence_root)
    acceptance_paths = [
        topology_root / ".reservation",
        topology_root / "portable-root-baseline.json",
        topology_root / "portable-root-remainder.governance.json",
        topology_root / "portable-root-remainder.failure-diagnostic.json",
        topology_root / "portable-root-remainder.unsafe-raw-reason-nonacceptance.json",
        topology_root / "policy-validation-nonacceptance.json",
        topology_root / "portable-defect-closure.governance.json",
        topology_root / "portable-defect-closure-proof.json",
    ]
    acceptance_paths.extend(topology_root / f"{code}{suffix}" for code in CODE_CLASSIFICATION for suffix in (".json", ".governance.json"))
    acceptance_paths.extend(topology_root / f"{code}{suffix}" for code in CLOSED_CODE_CLASSIFICATION for suffix in (".json", ".governance.json"))
    if os.path.lexists(context_path) or any(os.path.lexists(path) for path in acceptance_paths):
        raise TopologyError("Foundation context reuse is rejected")
    context: dict[str, object] = {
        "schema_version": FOUNDATION_CONTEXT_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "foundation_validation_date": validation_date,
        "foundation_context_sha256": "",
    }
    context["foundation_context_sha256"] = _sha256({
        key: value for key, value in context.items() if key != "foundation_context_sha256"
    })
    _publish_no_clobber(context_path, canonical_json_bytes(context))
    if context_path.read_bytes() != canonical_json_bytes(context):
        raise TopologyError("Foundation context post-write reread failed")
    load_foundation_context(context_path, run_id=run_id, head_sha=head_sha)
    return context_path


def _validated_foundation_context(
    path: Path, *, run_id: str, head_sha: str,
) -> dict[str, object]:
    """Validate canonical context bytes against an already-established identity."""
    try:
        raw = path.read_bytes()
        document = _strict_json(raw, label="Foundation context")
    except OSError as exc:
        raise TopologyError("Foundation context is absent") from exc
    required = {
        "schema_version", "foundation_run_id", "foundation_head_sha",
        "foundation_validation_date", "foundation_context_sha256",
    }
    if not isinstance(document, dict) or set(document) != required or canonical_json_bytes(document) != raw:
        raise TopologyError("Foundation context is malformed")
    if (
        document["schema_version"] != FOUNDATION_CONTEXT_SCHEMA
        or document["foundation_run_id"] != run_id
        or document["foundation_head_sha"] != head_sha
        or not isinstance(document["foundation_context_sha256"], str)
        or not HEX64.fullmatch(document["foundation_context_sha256"])
    ):
        raise TopologyError("Foundation context binding mismatch")
    parse_foundation_validation_date(document["foundation_validation_date"])
    if document["foundation_context_sha256"] != _sha256({
        key: value for key, value in document.items() if key != "foundation_context_sha256"
    }):
        raise TopologyError("Foundation context self-hash mismatch")
    return document


def _foundation_context_is_valid_for_diagnostics(
    path: Path, *, run_id: str, head_sha: str,
) -> bool:
    """Return only whether context bytes pass full v1 validation for this identity."""
    try:
        _validated_foundation_context(path, run_id=run_id, head_sha=head_sha)
    except (TopologyError, OSError, UnicodeError, ValueError):
        return False
    return True


def load_foundation_context(path: Path, *, run_id: str, head_sha: str) -> dict[str, object]:
    """Reopen canonical context bytes; this proves consistency, never writer identity."""
    _reject_validation_date_environment()
    current_run, current_head = _active_foundation_identity()
    if run_id != current_run or head_sha != current_head:
        raise TopologyError("Foundation context binding mismatch")
    return _validated_foundation_context(path, run_id=run_id, head_sha=head_sha)


def _publish_no_clobber(path: Path, content: bytes) -> None:
    _prepare_private_evidence_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_failure_diagnostic(path: Path, content: bytes) -> None:
    """Atomically install complete diagnostic bytes without replacing prior evidence."""
    _prepare_private_evidence_directory(path.parent)
    staging = path.with_name(f".{path.name}.staging")
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass


def _reject_failure_diagnostic_coexistence(topology_root: Path) -> None:
    """A failure-only record cannot be installed beside any accepting topology evidence."""
    accepted = [topology_root / "portable-root-remainder.governance.json"]
    for code in CODE_CLASSIFICATION:
        accepted.extend((topology_root / f"{code}.json", topology_root / f"{code}.governance.json"))
    accepted.extend((
        topology_root / "portable-defect-closure.governance.json",
        topology_root / "portable-defect-closure-proof.json",
    ))
    rejected = [
        *accepted,
        topology_root / "portable-root-remainder.failure-diagnostic.json",
        topology_root / "policy-validation-nonacceptance.json",
    ]
    if any(os.path.lexists(path) for path in rejected) or _unsafe_raw_reason_nonacceptance_instances(topology_root):
        raise TopologyError("failure diagnostic conflicts with existing topology acceptance artifact")


def _policy_nonacceptance_path(topology_root: Path) -> Path:
    return topology_root / "policy-validation-nonacceptance.json"


def _unsafe_raw_reason_nonacceptance_path(topology_root: Path) -> Path:
    return topology_root / "portable-root-remainder.unsafe-raw-reason-nonacceptance.json"


def _unsafe_raw_reason_nonacceptance_instances(topology_root: Path) -> tuple[Path, ...]:
    """Find every reserved unsafe-record spelling without opening untrusted bytes."""
    try:
        return tuple(sorted(
            (
                path for path in topology_root.iterdir()
                if path.name.endswith(".unsafe-raw-reason-nonacceptance.json") and os.path.lexists(path)
            ),
            key=lambda path: os.fsencode(path.name),
        ))
    except OSError as exc:
        raise TopologyError("unsafe raw reason nonacceptance directory is unavailable") from exc


def _reject_unsafe_raw_reason_nonacceptance_presence(topology_root: Path) -> None:
    if _unsafe_raw_reason_nonacceptance_instances(topology_root):
        raise TopologyError("unsafe raw reason nonacceptance is present; topology acceptance is forbidden")


def _reject_policy_nonacceptance_presence(topology_root: Path) -> None:
    if os.path.lexists(_policy_nonacceptance_path(topology_root)):
        raise TopologyError("policy validation nonacceptance is present; topology acceptance is forbidden")


def _reject_closed_source_artifacts(topology_root: Path) -> None:
    """Closed `SRC-*` code artifacts are stale acceptance evidence in P0-06."""
    try:
        stale = [
            path for path in topology_root.iterdir()
            if path.name.startswith("SRC-")
            and (path.name.endswith(".json") or path.name.endswith(".governance.json"))
            and os.path.lexists(path)
        ]
    except FileNotFoundError:
        return
    except OSError as exc:
        raise TopologyError("topology artifact directory is unavailable") from exc
    if stale:
        raise TopologyError("stale closed-code receipt or governance artifact is present")


def _candidate_file_bytes(node_ids: tuple[str, ...]) -> bytes:
    return ("\n".join(node_ids) + ("\n" if node_ids else "")).encode("utf-8")


def _collection_record_path(evidence_root: Path) -> Path:
    return evidence_root / "capability-topology" / "portable-root-collection.governance.json"


def _validate_collection_record(path: Path, candidates: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError("portable root collection report is malformed") from exc
    if (
        not isinstance(document, dict)
        or document.get("collection_only") is not True
        or document.get("component") != "root"
        or document.get("pytest_exit_status") != 0
        or not isinstance(document.get("tests"), list)
    ):
        raise TopologyError("portable root collection report has invalid policy")
    collected: list[str] = []
    deselected: list[dict[str, object]] = []
    for item in document["tests"]:
        if (
            not isinstance(item, dict)
            or item.get("component") != "root"
            or not isinstance(item.get("test_node_id"), str)
            or item.get("outcome") not in {"collected", "deselected"}
        ):
            raise TopologyError("portable root collection reported an execution outcome")
        if item["outcome"] == "collected":
            collected.append(str(item["test_node_id"]))
        else:
            deselected.append(dict(item))
    if tuple(sorted(collected)) != candidates or len(collected) != len(candidates):
        raise TopologyError("portable root collection report drifted from candidate list")
    deselected_ids = [str(item["test_node_id"]) for item in deselected]
    if (
        len(deselected_ids) != len(set(deselected_ids))
        or set(deselected_ids) & set(candidates)
    ):
        raise TopologyError("portable root collection has duplicate or overlapping deselection")
    return tuple(deselected)


def _validate_root_candidates(node_ids: tuple[str, ...]) -> None:
    if node_ids != tuple(sorted(set(node_ids))):
        raise TopologyError("portable root candidates are duplicate or unordered")
    if any(
        # Candidate sealing and diagnostics share this portable-root predicate.
        not _is_portable_root_pytest_node_id(
            node_id
        )
        for node_id in node_ids
    ):
        raise TopologyError("portable root candidate is outside the root test tree")


def _custody_artifact_identity(info: os.stat_result) -> str:
    if not stat.S_ISREG(info.st_mode):
        raise TopologyError("portable root custody extension is unsafe")
    return ":".join(
        str(value)
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            f"{stat.S_IMODE(info.st_mode):o}",
            info.st_nlink,
        )
    )


def _custody_policy_from_artifact(info: os.stat_result, digest: str) -> dict[str, str]:
    if not HEX64.fullmatch(digest):
        raise TopologyError("portable root custody extension is unsafe")
    return {
        **PORTABLE_ROOT_POLICY,
        "native_custody_extension_identity": _custody_artifact_identity(info),
        "native_custody_extension_sha256": digest,
    }


def _require_named_custody_matches_descriptor(path: Path, descriptor: int) -> None:
    try:
        named = path.stat(follow_symlinks=False)
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise TopologyError("portable root custody extension changed during execution") from exc
    if _custody_artifact_identity(named) != _custody_artifact_identity(opened):
        raise TopologyError("portable root custody extension changed during execution")


@contextmanager
def _retained_native_custody():
    """Hold one no-follow extension descriptor through a root exact execution."""
    raw_path = os.environ.get("PACKAGE6_FD_CUSTODY_EXTENSION_PATH")
    expected = os.environ.get("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256")
    if not raw_path or not expected or not HEX64.fullmatch(expected):
        raise TopologyError("portable root collection requires native custody identity")
    path = Path(raw_path)
    if not path.is_absolute():
        raise TopologyError("portable root custody extension is unsafe")
    descriptor = -1
    try:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            policy = _custody_policy_from_artifact(os.fstat(descriptor), _digest_fd(descriptor))
            _require_named_custody_matches_descriptor(path, descriptor)
        except OSError as exc:
            raise TopologyError("portable root custody extension is unsafe") from exc
        if policy["native_custody_extension_sha256"] != expected:
            raise TopologyError("portable root custody extension digest drift")
        yield policy, descriptor
        _require_named_custody_matches_descriptor(path, descriptor)
        if _custody_policy_from_artifact(
            os.fstat(descriptor), _digest_fd(descriptor),
        ) != policy:
            raise TopologyError("portable root custody extension changed during execution")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _native_custody_policy() -> dict[str, str]:
    with _retained_native_custody() as (policy, _descriptor):
        return policy


def _validate_custody_policy(policy: object) -> dict[str, str]:
    if not isinstance(policy, dict) or set(policy) != {
        *PORTABLE_ROOT_POLICY,
        "native_custody_extension_identity",
        "native_custody_extension_sha256",
    }:
        raise TopologyError("portable root collector policy drift")
    if any(policy.get(key) != value for key, value in PORTABLE_ROOT_POLICY.items()):
        raise TopologyError("portable root collector policy drift")
    digest = policy.get("native_custody_extension_sha256")
    identity = policy.get("native_custody_extension_identity")
    if (
        not isinstance(digest, str)
        or not HEX64.fullmatch(digest)
        or not isinstance(identity, str)
        or not re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+:[0-7]+:[0-9]+", identity)
    ):
        raise TopologyError("portable root collector policy drift")
    return {key: str(value) for key, value in policy.items()}


@contextmanager
def _retained_sealed_custody(baseline: dict[str, object]):
    sealed = _validate_custody_policy(baseline["collector_policy"])
    with _retained_native_custody() as (current, descriptor):
        if current != sealed:
            raise TopologyError("portable root custody identity drift")
        yield sealed, descriptor


@contextmanager
def _governance_custody_policy(policy: dict[str, str], descriptor: int):
    values = {
        "TEST_GOVERNANCE_CUSTODY_POLICY": canonical_json_bytes(policy).decode("utf-8"),
        "TEST_GOVERNANCE_CUSTODY_FD": str(descriptor),
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, prior in previous.items():
            if prior is None:
                del os.environ[key]
            else:
                os.environ[key] = prior


def _baseline_payload_sha256(document: dict[str, object]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "baseline_sha256"})


def _remainder_payload_sha256(document: dict[str, object]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "remainder_sha256"})


def _installed_inventory_rows(inventory: Path, evidence_root: Path) -> tuple[InventoryRow, ...]:
    rows = load_inventory(inventory)
    installed = evidence_root / "t-g03a-hosted-failure-inventory.tsv"
    if installed.exists():
        if installed.read_bytes() != inventory.read_bytes():
            raise TopologyError("installed inventory binding drift")
    else:
        install_inventory(inventory, evidence_root)
    installed_rows = load_inventory(installed)
    if rows != installed_rows:
        raise TopologyError("installed inventory row mapping drift")
    return installed_rows


def _installed_governance_state(
    inventory: Path, evidence_root: Path, *, head_sha: str,
) -> tuple[tuple[InventoryRow, ...], tuple[ClosureRow, ...]]:
    active = _installed_inventory_rows(inventory, evidence_root)
    installed = evidence_root / CLOSURE_RELATIVE_PATH.name
    if installed.exists():
        if installed.read_bytes() != CLOSURE_PATH.read_bytes():
            raise TopologyError("installed closure binding drift")
    else:
        install_portable_defect_closure(CLOSURE_PATH, evidence_root, head_sha=head_sha)
    closed = load_portable_defect_closure(installed, head_sha=head_sha)
    overlap = {row.node_id for row in active} & {row.node_id for row in closed}
    if overlap:
        raise TopologyError("active inventory overlaps portable closure")
    governed = tuple(sorted({row.node_id for row in active} | {row.node_id for row in closed}))
    if len(governed) != 62 or _ids_sha256(governed) != LOCKED_GOVERNED_NODE_IDS_SHA256:
        raise TopologyError("installed active and closure governed-node set drift")
    return active, closed


def _validate_closure_date(
    closure: tuple[ClosureRow, ...], context: dict[str, object],
) -> None:
    sealed = parse_foundation_validation_date(context["foundation_validation_date"])
    if any(parse_foundation_validation_date(row.closed_at_foundation_date) > sealed for row in closure):
        raise TopologyError("closure date is later than sealed Foundation date")


def _optional_foundation_context(
    foundation_context_path: Path | None, *, run_id: str, head_sha: str,
) -> dict[str, object] | None:
    if foundation_context_path is None:
        return None
    return load_foundation_context(foundation_context_path, run_id=run_id, head_sha=head_sha)


def collect_portable_root_baseline(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    collector: Callable[[], tuple[str, ...]] | None = None,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Seal the dynamically collected portable root candidate universe."""
    require_foundation_context(run_id, head_sha)
    context = _optional_foundation_context(foundation_context_path, run_id=run_id, head_sha=head_sha)
    _require_topology_reservation(evidence_root, run_id, head_sha, context)
    topology_root = evidence_root / "capability-topology"
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    _reject_closed_source_artifacts(topology_root)
    rows, closure = _installed_governance_state(inventory, evidence_root, head_sha=head_sha)
    if context is not None:
        _validate_closure_date(closure, context)
    policy = _native_custody_policy()
    candidates = _collect_portable_root_candidates(evidence_root) if collector is None else collector()
    _validate_root_candidates(candidates)
    governed_ids = {row.node_id for row in rows} | {row.node_id for row in closure}
    if not governed_ids <= set(candidates):
        raise TopologyError("portable root baseline omitted a governed node")
    collection_report = _collection_record_path(evidence_root)
    if collector is not None:
        _publish_no_clobber(
            collection_report,
            json.dumps(
                {
                    "schema_version": 1,
                    "component": "root",
                    "collection_only": True,
                    "pytest_exit_status": 0,
                    "tests": [
                        {
                            "test_node_id": node,
                            "component": "root",
                            "outcome": "collected",
                            "reason": "",
                            "phase": "collection",
                        }
                        for node in candidates
                    ],
                },
                sort_keys=True,
            ).encode("utf-8"),
        )
    _validate_collection_record(collection_report, candidates)
    collection_digest = hashlib.sha256(collection_report.read_bytes()).hexdigest()
    candidate_bytes = _candidate_file_bytes(candidates)
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
    baseline: dict[str, object] = {
        "schema_version": BASELINE_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "closure_sha256": LOCKED_CLOSURE_SHA256,
        "collector_policy": policy,
        "candidate_node_ids": list(candidates),
        "candidate_file_sha256": candidate_digest,
        "collection_report_sha256": collection_digest,
        "baseline_sha256": "",
    }
    if context is not None:
        baseline["foundation_validation_date"] = context["foundation_validation_date"]
        baseline["foundation_context_sha256"] = context["foundation_context_sha256"]
    baseline["baseline_sha256"] = _baseline_payload_sha256(baseline)
    _publish_no_clobber(topology_root / "portable-root-candidates.txt", candidate_bytes)
    _publish_no_clobber(
        topology_root / "portable-root-baseline.json", canonical_json_bytes(baseline),
    )
    return baseline


def load_portable_root_baseline(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Reopen and verify the sealed baseline and candidate file before execution."""
    context = _optional_foundation_context(foundation_context_path, run_id=run_id, head_sha=head_sha)
    _require_topology_reservation(evidence_root, run_id, head_sha, context)
    rows, closure = _installed_governance_state(inventory, evidence_root, head_sha=head_sha)
    if context is not None:
        _validate_closure_date(closure, context)
    topology_root = evidence_root / "capability-topology"
    try:
        raw = (topology_root / "portable-root-baseline.json").read_bytes()
        baseline = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError("portable root baseline is missing") from exc
    required = {
        "schema_version", "foundation_run_id", "foundation_head_sha", "inventory_sha256", "closure_sha256",
        "collector_policy", "candidate_node_ids", "candidate_file_sha256", "collection_report_sha256", "baseline_sha256",
    }
    if context is not None:
        required |= {"foundation_validation_date", "foundation_context_sha256"}
    if not isinstance(baseline, dict) or set(baseline) != required or canonical_json_bytes(baseline) != raw:
        raise TopologyError("portable root baseline is noncanonical or malformed")
    if (
        baseline["schema_version"] != BASELINE_SCHEMA
        or baseline["foundation_run_id"] != run_id
        or baseline["foundation_head_sha"] != head_sha
        or baseline["inventory_sha256"] != LOCKED_INVENTORY_SHA256
        or baseline["closure_sha256"] != LOCKED_CLOSURE_SHA256
        or baseline["baseline_sha256"] != _baseline_payload_sha256(baseline)
        or not isinstance(baseline["collector_policy"], dict)
        or (context is not None and (
            baseline["foundation_validation_date"] != context["foundation_validation_date"]
            or baseline["foundation_context_sha256"] != context["foundation_context_sha256"]
            or parse_foundation_validation_date(baseline["foundation_validation_date"]) is None
        ))
    ):
        raise TopologyError("portable root baseline binding drift")
    _validate_custody_policy(baseline["collector_policy"])
    values = baseline["candidate_node_ids"]
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise TopologyError("portable root baseline candidates are malformed")
    candidates = tuple(values)
    _validate_root_candidates(candidates)
    candidate_bytes = _candidate_file_bytes(candidates)
    if (topology_root / "portable-root-candidates.txt").read_bytes() != candidate_bytes:
        raise TopologyError("portable root candidate file drift")
    if baseline["candidate_file_sha256"] != hashlib.sha256(candidate_bytes).hexdigest():
        raise TopologyError("portable root candidate digest drift")
    if (
        not isinstance(baseline["collection_report_sha256"], str)
        or not HEX64.fullmatch(baseline["collection_report_sha256"])
        or baseline["collection_report_sha256"]
        != hashlib.sha256(_collection_record_path(evidence_root).read_bytes()).hexdigest()
    ):
        raise TopologyError("portable root collection report digest drift")
    _validate_collection_record(_collection_record_path(evidence_root), candidates)
    if not ({row.node_id for row in rows} | {row.node_id for row in closure}) <= set(candidates):
        raise TopologyError("portable root baseline omitted a governed node")
    return baseline


def prepare_portable_root_remainder(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Generate the exact ordinary-root list from the verified dynamic baseline."""
    require_foundation_context(run_id, head_sha)
    context = _optional_foundation_context(foundation_context_path, run_id=run_id, head_sha=head_sha)
    _require_topology_reservation(evidence_root, run_id, head_sha, context)
    topology_root = evidence_root / "capability-topology"
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    rows, closure = _installed_governance_state(inventory, evidence_root, head_sha=head_sha)
    candidates = tuple(baseline["candidate_node_ids"])
    governed = {row.node_id for row in rows} | {row.node_id for row in closure}
    remainder = tuple(sorted(set(candidates) - governed))
    _validate_root_candidates(remainder)
    remainder_bytes = _candidate_file_bytes(remainder)
    document: dict[str, object] = {
        "schema_version": REMAINDER_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "closure_sha256": LOCKED_CLOSURE_SHA256,
        "baseline_sha256": baseline["baseline_sha256"],
        "remainder_node_ids": list(remainder),
        "remainder_file_sha256": hashlib.sha256(remainder_bytes).hexdigest(),
        "remainder_sha256": "",
    }
    document["remainder_sha256"] = _remainder_payload_sha256(document)
    _publish_no_clobber(topology_root / "portable-root-remainder.txt", remainder_bytes)
    _publish_no_clobber(
        topology_root / "portable-root-remainder.json", canonical_json_bytes(document),
    )
    return document


def _collect_portable_root_candidates(evidence_root: Path) -> tuple[str, ...]:
    """Run the one permitted broad root selector in collection-only mode."""
    report = evidence_root / "capability-topology" / "portable-root-collection.governance.json"
    environment = dict(
        os.environ,
        TEST_GOVERNANCE_REPORT=str(report),
        TEST_GOVERNANCE_COMPONENT="root",
        TEST_GOVERNANCE_NO_CLOBBER="1",
        TEST_GOVERNANCE_COLLECTION_ONLY="1",
    )
    command = [
        sys.executable, "-m", "pytest", "-q", "--collect-only",
        "--portable-embedded-proof", "-m", PORTABLE_ROOT_MARKER,
        "-p", "scripts.test_governance_pytest", "tests",
    ]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, env=environment, check=False)
    if completed.returncode != 0 or not report.is_file():
        raise TopologyError("portable root baseline collection failed")
    document = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("tests"), list):
        raise TopologyError("portable root collection report is malformed")
    result = tuple(sorted(
        str(item["test_node_id"])
        for item in document["tests"]
        if isinstance(item, dict) and item.get("outcome") == "collected" and isinstance(item.get("test_node_id"), str)
    ))
    _validate_root_candidates(result)
    _validate_collection_record(report, result)
    return result


def _load_portable_root_remainder(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    topology_root = evidence_root / "capability-topology"
    try:
        raw = (topology_root / "portable-root-remainder.json").read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError("portable root remainder is missing") from exc
    required = {
        "schema_version", "foundation_run_id", "foundation_head_sha", "inventory_sha256", "closure_sha256",
        "baseline_sha256", "remainder_node_ids", "remainder_file_sha256", "remainder_sha256",
    }
    if not isinstance(document, dict) or set(document) != required or canonical_json_bytes(document) != raw:
        raise TopologyError("portable root remainder is noncanonical or malformed")
    if (
        document["schema_version"] != REMAINDER_SCHEMA
        or document["foundation_run_id"] != run_id
        or document["foundation_head_sha"] != head_sha
        or document["inventory_sha256"] != LOCKED_INVENTORY_SHA256
        or document["closure_sha256"] != LOCKED_CLOSURE_SHA256
        or document["baseline_sha256"] != baseline["baseline_sha256"]
        or document["remainder_sha256"] != _remainder_payload_sha256(document)
        or not isinstance(document["remainder_node_ids"], list)
        or any(not isinstance(node, str) for node in document["remainder_node_ids"])
    ):
        raise TopologyError("portable root remainder binding drift")
    remainder = tuple(document["remainder_node_ids"])
    _validate_root_candidates(remainder)
    active, closure = _installed_governance_state(inventory, evidence_root, head_sha=head_sha)
    expected = tuple(sorted(
        set(baseline["candidate_node_ids"])
        - ({row.node_id for row in active} | {row.node_id for row in closure})
    ))
    if remainder != expected:
        raise TopologyError("portable root remainder is not baseline minus inventory")
    contents = _candidate_file_bytes(remainder)
    if (topology_root / "portable-root-remainder.txt").read_bytes() != contents:
        raise TopologyError("portable root remainder file drift")
    if document["remainder_file_sha256"] != hashlib.sha256(contents).hexdigest():
        raise TopologyError("portable root remainder digest drift")
    return document, remainder


def _validate_exact_governance_bytes(
    raw: bytes, expected: tuple[str, ...], custody_policy: dict[str, str],
) -> tuple[dict[str, object], ...]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError("exact governance record is malformed") from exc
    if (
        not isinstance(document, dict)
        or document.get("component") != "root"
        or document.get("pytest_exit_status") != 0
        or document.get("custody_policy") != custody_policy
        or not isinstance(document.get("tests"), list)
    ):
        raise TopologyError("exact governance record is not a passing root report")
    records = document["tests"]
    observed: dict[str, dict[str, object]] = {}
    for item in records:
        if (
            not isinstance(item, dict)
            or set(item) != {"test_node_id", "component", "outcome", "reason", "phase"}
            or item.get("component") != "root"
            or item.get("outcome") != "passed"
            or not isinstance(item.get("test_node_id"), str)
            or not isinstance(item.get("reason"), str)
            or not isinstance(item.get("phase"), str)
        ):
            raise TopologyError("exact governance record has a non-passing outcome")
        node_id = str(item["test_node_id"])
        if node_id in observed:
            raise TopologyError("exact governance record has duplicate nodes")
        observed[node_id] = {
            "test_node_id": node_id,
            "component": item["component"],
            "outcome": item["outcome"],
            "reason": item["reason"],
            "phase": item["phase"],
        }
    result = tuple(sorted(observed))
    if result != expected or len(records) != len(expected):
        raise TopologyError("exact governance record does not match selected nodes")
    return tuple(observed[node] for node in expected)


def _validate_exact_governance_record(
    path: Path, expected: tuple[str, ...], custody_policy: dict[str, str],
) -> tuple[str, ...]:
    try:
        records = _validate_exact_governance_bytes(path.read_bytes(), expected, custody_policy)
    except OSError as exc:
        raise TopologyError("exact governance record is malformed") from exc
    return tuple(str(item["test_node_id"]) for item in records)


def _observed_closure_digests(
    closure: tuple[ClosureRow, ...], governance_raw: bytes, custody_policy: dict[str, str],
) -> list[str]:
    ordered = tuple(sorted(closure, key=lambda row: row.node_id))
    records = _validate_exact_governance_bytes(
        governance_raw, tuple(row.node_id for row in ordered), custody_policy,
    )
    return [
        closed_node_proof_digest(row, record)
        for row, record in zip(ordered, records, strict=True)
    ]


def _closure_proof_payload_sha256(document: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in document.items() if key != "closure_proof_sha256"
    })


def validate_portable_closure_proof(
    path: Path, *, foundation_run_id: str, foundation_head_sha: str,
    foundation_context: dict[str, object], sealed_custody: dict[str, str],
    closure_path: Path = CLOSURE_PATH,
) -> dict[str, object]:
    """Validate the one exact-execution proof that replaces all closed SRC receipts."""
    _reject_closed_source_artifacts(path.parent)
    with _retained_private_closure_artifacts(path) as artifacts:
        raw = artifacts.proof_raw
        governance_raw = artifacts.governance_raw
        document = _strict_json(raw, label="portable closure proof")
        if (
            not isinstance(document, dict)
            or set(document) != CLOSURE_PROOF_KEYS
            or canonical_json_bytes(document) != raw
        ):
            raise TopologyError("portable closure proof is noncanonical or malformed")
        closure = load_portable_defect_closure(closure_path, head_sha=foundation_head_sha)
        _validate_closure_date(closure, foundation_context)
        nodes = tuple(sorted(row.node_id for row in closure))
        ledger_digests = [
            row.proof_result_digest for row in sorted(closure, key=lambda row: row.node_id)
        ]
        custody = document["custody_policy"]
        if not isinstance(custody, dict):
            raise TopologyError("portable closure proof custody is malformed")
        custody = _validate_custody_policy(custody)
        expected_custody = _validate_custody_policy(sealed_custody)
        if custody != expected_custody:
            raise TopologyError("portable closure proof does not match sealed custody")
        observed_digests = _observed_closure_digests(closure, governance_raw, custody)
        if (
            observed_digests != ledger_digests
            or document["proof_result_digests"] != observed_digests
        ):
            raise TopologyError("portable closure observed proof digest drift")
        if (
            document["schema_version"] != PORTABLE_CLOSURE_PROOF_SCHEMA
            or document["foundation_run_id"] != foundation_run_id
            or document["foundation_head_sha"] != foundation_head_sha
            or document["foundation_validation_date"] != foundation_context["foundation_validation_date"]
            or document["foundation_context_sha256"] != foundation_context["foundation_context_sha256"]
            or document["inventory_sha256"] != LOCKED_INVENTORY_SHA256
            or document["closure_sha256"] != LOCKED_CLOSURE_SHA256
            or tuple(document["closure_node_ids"]) != nodes
            or document["closure_node_ids_sha256"] != _ids_sha256(nodes)
            or document["proof_command"] != CLOSURE_PROOF_COMMAND
            or document["custody_policy_sha256"] != _sha256(custody)
            or document["governance_report_sha256"] != hashlib.sha256(governance_raw).hexdigest()
            or document["outcome"] != "PASS"
            or document["closure_proof_sha256"] != _closure_proof_payload_sha256(document)
        ):
            raise TopologyError("portable closure proof binding drift")
        _postcheck_private_closure_artifacts(artifacts)
        return document


def execute_portable_defect_closure(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path,
    exact_runner: Callable[[tuple[str, ...], Path], tuple[str, ...]] | None = None,
) -> Path:
    """Execute all 32 closed nodes exactly once and publish one no-clobber proof."""
    require_foundation_context(run_id, head_sha)
    context = load_foundation_context(
        foundation_context_path, run_id=run_id, head_sha=head_sha,
    )
    _require_topology_reservation(evidence_root, run_id, head_sha, context)
    topology_root = evidence_root / "capability-topology"
    _reject_policy_nonacceptance_presence(topology_root)
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    _reject_closed_source_artifacts(topology_root)
    active, closure = _installed_governance_state(inventory, evidence_root, head_sha=head_sha)
    del active
    _validate_closure_date(closure, context)
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id,
        head_sha=head_sha, foundation_context_path=foundation_context_path,
    )
    nodes = tuple(sorted(row.node_id for row in closure))
    report = topology_root / "portable-defect-closure.governance.json"
    selected = _execute_exact_with_retained_custody(
        baseline=baseline, nodes=nodes, report=report,
        runner=_run_exact if exact_runner is None else exact_runner,
    )
    if selected != nodes:
        raise TopologyError("portable closure proof did not execute all closed nodes")
    custody = _validate_custody_policy(baseline["collector_policy"])
    governance_raw = _read_private_regular_file(
        report, label="portable closure governance report",
    )
    observed_digests = _observed_closure_digests(closure, governance_raw, custody)
    ledger_digests = [
        row.proof_result_digest for row in sorted(closure, key=lambda row: row.node_id)
    ]
    if observed_digests != ledger_digests:
        raise TopologyError("portable closure observed proof digest drift")
    proof: dict[str, object] = {
        "schema_version": PORTABLE_CLOSURE_PROOF_SCHEMA,
        "foundation_run_id": run_id,
        "foundation_head_sha": head_sha,
        "foundation_validation_date": context["foundation_validation_date"],
        "foundation_context_sha256": context["foundation_context_sha256"],
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "closure_sha256": LOCKED_CLOSURE_SHA256,
        "closure_node_ids": list(nodes),
        "closure_node_ids_sha256": _ids_sha256(nodes),
        "proof_command": CLOSURE_PROOF_COMMAND,
        "proof_result_digests": observed_digests,
        "custody_policy": custody,
        "custody_policy_sha256": _sha256(custody),
        "governance_report_sha256": hashlib.sha256(governance_raw).hexdigest(),
        "outcome": "PASS",
        "closure_proof_sha256": "",
    }
    proof["closure_proof_sha256"] = _closure_proof_payload_sha256(proof)
    destination = topology_root / "portable-defect-closure-proof.json"
    _publish_no_clobber(destination, canonical_json_bytes(proof))
    if validate_portable_closure_proof(
        destination, foundation_run_id=run_id, foundation_head_sha=head_sha,
        foundation_context=context, sealed_custody=custody,
    ) != proof:
        raise TopologyError("portable closure proof post-write reread failed")
    return destination


def _execute_exact_with_retained_custody(
    *, baseline: dict[str, object], nodes: tuple[str, ...], report: Path,
    runner: Callable[[tuple[str, ...], Path], tuple[str, ...]],
    portable_root_remainder: bool = False, remainder_document: dict[str, object] | None = None,
    foundation_context_verified: bool = False,
) -> tuple[str, ...]:
    """Publish PASS evidence only for all-pass raw execution; retain complete non-pass diagnostics."""
    provisional = report.with_name(f".{report.name}.executing")
    diagnostic = report.with_name("portable-root-remainder.failure-diagnostic.json")
    nonacceptance = _policy_nonacceptance_path(report.parent)
    unsafe_nonacceptance = _unsafe_raw_reason_nonacceptance_path(report.parent)
    _reject_unsafe_raw_reason_nonacceptance_presence(report.parent)
    if (
        os.path.lexists(provisional)
        or os.path.lexists(diagnostic)
        or os.path.lexists(nonacceptance)
        or os.path.lexists(unsafe_nonacceptance)
    ):
        raise TopologyError("exact governance staging record already exists")
    validation_date = parse_foundation_validation_date(
        baseline["foundation_validation_date"],
    ) if "foundation_validation_date" in baseline else None
    nonacceptance_mode = foundation_context_verified and portable_root_remainder and {
        "foundation_validation_date", "foundation_context_sha256",
    }.issubset(baseline)
    if nonacceptance_mode:
        if remainder_document is None:
            raise TopologyError("portable root remainder requires a verified remainder record")
        _reject_failure_diagnostic_coexistence(report.parent)
        custody = _validate_custody_policy(baseline["collector_policy"])
        try:
            policy_snapshot, policy_source = _policy_snapshot_for_nonacceptance(
                str(baseline["foundation_head_sha"]), validation_date,
            )
        except _PolicyStageError as exc:
            payload = _policy_nonacceptance_payload(
                baseline=baseline, remainder=remainder_document, nodes=nodes, custody=custody,
                failure=exc.failure, custody_status="PRE_EXECUTION_VALIDATED",
            )
            _publish_policy_nonacceptance(nonacceptance, payload)
            raise TopologyError(str(exc)) from None
    else:
        policy_snapshot, policy_source = _validated_policy_snapshot(
            str(baseline["foundation_head_sha"]), validation_date,
        )
    try:
        with _retained_sealed_custody(baseline) as (sealed_custody, descriptor):
            with _governance_custody_policy(sealed_custody, descriptor):
                selected = runner(nodes, provisional)
            if selected != nodes:
                raise TopologyError("exact runner changed the generated node list")
        # Leaving retained custody is the postcheck; only now may either record be considered.
        if nonacceptance_mode:
            try:
                reread_snapshot, reread_source = _policy_snapshot_for_nonacceptance(
                    str(baseline["foundation_head_sha"]), validation_date,
                )
            except _PolicyStageError as exc:
                payload = _policy_nonacceptance_payload(
                    baseline=baseline, remainder=remainder_document, nodes=nodes, custody=sealed_custody,
                    failure=_PolicyStageFailure(
                        "POST_CUSTODY_REREAD_COMPARISON", exc.failure.public_class,
                        "PRE_EXECUTION_SNAPSHOT", hashlib.sha256(policy_source).hexdigest(),
                    ), custody_status="POST_CUSTODY_POSTCHECK_PASS",
                )
                _publish_policy_nonacceptance(nonacceptance, payload)
                raise TopologyError(str(exc)) from None
        else:
            reread_snapshot, reread_source = _validated_policy_snapshot(
                str(baseline["foundation_head_sha"]), validation_date,
            )
        if reread_source != policy_source or reread_snapshot != policy_snapshot:
            if nonacceptance_mode:
                payload = _policy_nonacceptance_payload(
                    baseline=baseline, remainder=remainder_document, nodes=nodes, custody=sealed_custody,
                    failure=_PolicyStageFailure(
                        "POST_CUSTODY_REREAD_COMPARISON", "POLICY_SOURCE_DRIFT",
                        "PRE_EXECUTION_SNAPSHOT", hashlib.sha256(policy_source).hexdigest(),
                    ), custody_status="POST_CUSTODY_POSTCHECK_PASS",
                )
                _publish_policy_nonacceptance(nonacceptance, payload)
                raise TopologyError("policy validation failed: POLICY_SOURCE_DRIFT")
            raise _policy_validation_error(TopologyError("allowlist source drifted during exact execution"))
        raw_report = _strict_json(provisional.read_bytes(), label="raw exact report")
        if raw_report.get("custody_policy") != sealed_custody:
            raise TopologyError("raw diagnostic report has custody policy drift")
        raw_observations, raw_exit_status = _structurally_valid_raw_observations(raw_report, nodes)
        if _has_unsafe_raw_reason(raw_observations):
            if not nonacceptance_mode:
                raise TopologyError("raw diagnostic observation has unsafe reason")
            assert remainder_document is not None
            _reject_failure_diagnostic_coexistence(report.parent)
            payload = _unsafe_raw_reason_nonacceptance_payload(
                baseline=baseline, remainder=remainder_document, nodes=nodes, custody=sealed_custody,
                pytest_exit_status=raw_exit_status,
            )
            _publish_unsafe_raw_reason_nonacceptance(unsafe_nonacceptance, payload)
            raise TopologyError("UNSAFE_RAW_REASON_NONACCEPTANCE")
        observations, raw_exit_status = _diagnostic_observations(raw_report, nodes, policy_snapshot)
        is_nonpass = raw_exit_status != "0" or any(item["outcome"] != "passed" for item in observations)
        if is_nonpass:
            _reject_failure_diagnostic_coexistence(report.parent)
            payload = _failure_diagnostic_payload(
                baseline=baseline,
                remainder={
                    "remainder_file_sha256": hashlib.sha256(_candidate_file_bytes(nodes)).hexdigest(),
                },
                nodes=nodes,
                run_id=str(baseline["foundation_run_id"]),
                head_sha=str(baseline["foundation_head_sha"]),
                custody=sealed_custody,
                # Pytest returns zero for a skipped collection; the exact lane's own
                # non-pass status is nevertheless required to be nonzero on disk.
                pytest_exit_status=raw_exit_status if raw_exit_status != "0" else "1",
                snapshot=policy_snapshot,
                observations=observations,
            )
            payload["diagnostic_sha256"] = _sha256(payload)
            encoded = canonical_json_bytes(payload)
            _publish_failure_diagnostic(diagnostic, encoded)
            if diagnostic.read_bytes() != encoded or parse_failure_diagnostic(diagnostic.read_bytes()) != payload:
                raise TopologyError("failure diagnostic post-write reread failed")
            raise TopologyError("EXACT_EXECUTION_NONPASS")
        _validate_exact_governance_record(provisional, nodes, sealed_custody)
        _publish_no_clobber(report, provisional.read_bytes())
        return _validate_exact_governance_record(report, nodes, sealed_custody)
    finally:
        try:
            provisional.unlink()
        except FileNotFoundError:
            pass


def _write_empty_exact_governance_report(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    if nodes:
        raise TopologyError("empty governance report received selected nodes")
    try:
        custody_policy = json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise TopologyError("empty governance report lacks scoped custody policy") from exc
    _publish_no_clobber(
        report,
        json.dumps(
            {
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 0,
                "custody_policy": custody_policy,
                "summary": {},
                "tests": [],
            },
            sort_keys=True,
        ).encode("utf-8"),
    )
    return ()


def execute_portable_root_remainder(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    exact_runner: Callable[[tuple[str, ...], Path], tuple[str, ...]] | None = None,
    foundation_context_path: Path | None = None,
) -> tuple[str, ...]:
    """Execute the sealed ordinary-root list exactly once, including an empty list."""
    require_foundation_context(run_id, head_sha)
    topology_root = evidence_root / "capability-topology"
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    _reject_closed_source_artifacts(topology_root)
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    remainder_document, remainder = _load_portable_root_remainder(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    report = evidence_root / "capability-topology" / "portable-root-remainder.governance.json"
    if remainder:
        runner = _run_exact_observations if exact_runner is None else exact_runner
        return _execute_exact_with_retained_custody(
            baseline=baseline, nodes=remainder, report=report, runner=runner,
            portable_root_remainder=True, remainder_document=remainder_document,
            foundation_context_verified=foundation_context_path is not None,
        )
    return _execute_exact_with_retained_custody(
        baseline=baseline,
        nodes=remainder,
        report=report,
        runner=_write_empty_exact_governance_report,
        portable_root_remainder=True, remainder_document=remainder_document,
        foundation_context_verified=foundation_context_path is not None,
    )


def _sha256(document: object) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _strict_json(raw: bytes, *, label: str) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TopologyError(f"{label} is not strict UTF-8 JSON") from exc


def _normalize_v1_reason(reason: str) -> str:
    return " ".join("".join(group) for group in re.split(
        "[" + "".join(chr(codepoint) for codepoint in V1_WHITE_SPACE) + "]+", reason,
    ) if group)


def _is_v1_normalized_reason(reason: str) -> bool:
    """Check v1 shape without producing a normalized copy of untrusted evidence."""
    return (
        not reason.startswith(" ")
        and not reason.endswith(" ")
        and "  " not in reason
        and all(ord(character) not in V1_WHITE_SPACE or character == " " for character in reason)
    )


def reason_commitment_sha256(reason: str) -> str:
    normalized = _normalize_v1_reason(reason)
    return _sha256({"schema_version": REASON_COMMITMENT_SCHEMA, "normalized_reason": normalized})


def _reason_is_safe(reason: str) -> bool:
    return (
        "/" not in reason
        and "\\" not in reason
        and not any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in reason)
        and re.search(r"(?i)[a-z][a-z0-9+.-]{1,31}://", reason) is None
        and re.search(r"(?i)\b(?:token|secret|password|authorization|bearer)\b", reason) is None
        and re.search(r"[A-Za-z0-9+/_=-]{20,}", reason) is None
    )


def _allowlist_bytes_at_head(head_sha: str) -> bytes:
    source = ROOT / "tests/skip-allowlist.yaml"
    try:
        raw = source.read_bytes()
        tracked = subprocess.run(
            ["git", "show", f"{head_sha}:tests/skip-allowlist.yaml"], cwd=ROOT,
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise TopologyError("tracked allowlist is unavailable") from exc
    if raw != tracked:
        raise TopologyError("allowlist source drifted from Foundation head")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TopologyError("allowlist is not strict UTF-8") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise TopologyError("allowlist has a UTF-8 BOM")
    return raw


def _policy_entry_payload(entry: dict[str, object]) -> dict[str, object]:
    fields = (
        "approval_record_type", "allowed_in_ci", "component", "outcome", "owner", "reason",
        "reason_category", "required_binary_or_service", "review_by", "security_critical",
        "target_phase", "test_node_id",
    )
    payload = {field: entry[field] for field in fields}
    payload["reason"] = _normalize_v1_reason(str(payload["reason"]))
    return payload


def _policy_validation_error(exc: BaseException) -> TopologyError:
    message = str(exc).lower()
    if any(token in message for token in (
        "allowlist source", "tracked allowlist", "utf-8 bom", "strict utf-8",
    )):
        code = "POLICY_SOURCE_DRIFT"
    elif "expired" in message:
        code = "POLICY_REVIEW_DATE_EXPIRED"
    elif "schema" in message:
        code = "POLICY_SCHEMA_INVALID"
    elif "review_by" in message:
        code = "POLICY_REVIEW_DATE_INVALID"
    elif "non-normalized" in message:
        code = "POLICY_REASON_NORMALIZATION_INVALID"
    elif "duplicate" in message:
        code = "POLICY_DUPLICATE_ENTRY"
    elif "invalid" in message or "unapproved" in message:
        code = "POLICY_FIELD_TYPE_INVALID"
    else:
        code = "POLICY_VALIDATION_INVALID"
    return TopologyError(f"policy validation failed: {code}")


def _validated_policy_snapshot(
    head_sha: str, validation_date: date,
) -> tuple[dict[str, object], bytes]:
    """Build the complete redacted root-policy projection from tracked allowlist bytes."""
    try:
        raw = _allowlist_bytes_at_head(head_sha)
        from scripts import check_test_governance as governance
        document = _strict_json(raw, label="allowlist")
        entries = governance.validate_allowlist_document(document, today=validation_date)
        snapshot_entries: list[dict[str, object]] = []
        for entry in entries:
            if entry["component"] != "root":
                continue
            outcome = entry["outcome"]
            normalized_reason = _normalize_v1_reason(str(entry["reason"]))
            if str(entry["reason"]) != normalized_reason:
                raise TopologyError("allowlist policy reason is not v1-normalized")
            snapshot_entries.append({
                "component": "root",
                "test_node_id": entry["test_node_id"],
                "outcome": outcome,
                "allowed_in_ci": entry["allowed_in_ci"],
                "reason_class": "POLICY_SKIP_REASON" if outcome == "skipped" else "POLICY_DESELECT_REASON",
                "normalized_reason_commitment_sha256": reason_commitment_sha256(normalized_reason),
                "policy_entry_sha256": _sha256(_policy_entry_payload(entry)),
            })
        snapshot_entries.sort(key=lambda item: (str(item["component"]).encode(), str(item["test_node_id"]).encode()))
        snapshot: dict[str, object] = {
            "snapshot_schema_version": POLICY_SNAPSHOT_SCHEMA,
            "allowlist_schema_version": "1",
            "allowlist_source_sha256": hashlib.sha256(raw).hexdigest(),
            "policy_entry_schema_version": POLICY_ENTRY_SCHEMA,
            "entries": snapshot_entries,
        }
        return snapshot, raw
    except Exception as exc:
        raise _policy_validation_error(exc) from exc


def _stage_failure(
    stage: str, *, source: bytes | None, source_hash_status: str | None = None,
    public_class: str = "POLICY_VALIDATION_INVALID",
) -> _PolicyStageError:
    """Construct the record-only redacted outcome without inspecting exception details."""
    if source is None:
        status, digest = "UNAVAILABLE", ""
    else:
        status, digest = source_hash_status or "CURRENT_STAGE_BYTES", hashlib.sha256(source).hexdigest()
    return _PolicyStageError(_PolicyStageFailure(stage, public_class, status, digest))


def _policy_snapshot_for_nonacceptance(
    head_sha: str, validation_date: date,
) -> tuple[dict[str, object], bytes]:
    """Run snapshot operations at their named boundaries for a diagnostic-only writer."""
    try:
        raw = _allowlist_bytes_at_head(head_sha)
    except Exception:
        raise _stage_failure("SOURCE_ACQUISITION_HEAD_BINDING", source=None) from None
    try:
        from scripts import check_test_governance as governance
    except Exception:
        raise _stage_failure("SHARED_VALIDATOR_IMPORT", source=raw) from None
    try:
        document = _strict_json(raw, label="allowlist")
    except Exception:
        raise _stage_failure("STRICT_JSON_PARSE", source=raw) from None
    try:
        entries = governance.validate_allowlist_document(document, today=validation_date)
    except governance.AllowlistValidationError as exc:
        raise _stage_failure(
            "SHARED_ALLOWLIST_VALIDATION", source=raw, public_class=exc.policy_class,
        ) from None
    except Exception:
        raise _stage_failure("SHARED_ALLOWLIST_VALIDATION", source=raw) from None
    try:
        snapshot_entries: list[dict[str, object]] = []
        for entry in entries:
            if entry["component"] != "root":
                continue
            outcome = entry["outcome"]
            normalized_reason = _normalize_v1_reason(str(entry["reason"]))
            if str(entry["reason"]) != normalized_reason:
                raise TopologyError("reason normalization failed")
            snapshot_entries.append({
                "component": "root", "test_node_id": entry["test_node_id"], "outcome": outcome,
                "allowed_in_ci": entry["allowed_in_ci"],
                "reason_class": "POLICY_SKIP_REASON" if outcome == "skipped" else "POLICY_DESELECT_REASON",
                "normalized_reason_commitment_sha256": reason_commitment_sha256(normalized_reason),
                "policy_entry_sha256": _sha256(_policy_entry_payload(entry)),
            })
        snapshot_entries.sort(key=lambda item: (str(item["component"]).encode(), str(item["test_node_id"]).encode()))
        return {
            "snapshot_schema_version": POLICY_SNAPSHOT_SCHEMA,
            "allowlist_schema_version": "1",
            "allowlist_source_sha256": hashlib.sha256(raw).hexdigest(),
            "policy_entry_schema_version": POLICY_ENTRY_SCHEMA,
            "entries": snapshot_entries,
        }, raw
    except Exception:
        raise _stage_failure("ROOT_PROJECTION_REASON_NORMALIZATION", source=raw) from None


def _policy_nonacceptance_payload(
    *, baseline: dict[str, object], remainder: dict[str, object], nodes: tuple[str, ...],
    custody: dict[str, str], failure: _PolicyStageFailure, custody_status: str,
) -> dict[str, object]:
    return {
        "schema_version": POLICY_NONACCEPTANCE_SCHEMA, "diagnostic_only": True,
        "foundation_run_id": baseline["foundation_run_id"], "foundation_head_sha": baseline["foundation_head_sha"],
        "foundation_validation_date": baseline["foundation_validation_date"],
        "foundation_context_sha256": baseline["foundation_context_sha256"],
        "inventory_sha256": LOCKED_INVENTORY_SHA256, "baseline_sha256": baseline["baseline_sha256"],
        "baseline_candidate_ids_sha256": _ids_sha256(tuple(baseline["candidate_node_ids"])),
        "baseline_node_list_sha256": baseline["candidate_file_sha256"],
        "remainder_sha256": remainder["remainder_sha256"],
        "remainder_candidate_ids_sha256": _ids_sha256(nodes),
        "remainder_node_list_sha256": remainder["remainder_file_sha256"],
        "custody_policy_sha256": _sha256(custody), "custody_status": custody_status,
        "policy_validation_stage": failure.stage, "policy_validation_class": failure.public_class,
        "policy_source_hash_status": failure.source_hash_status,
        "policy_source_sha256": failure.source_sha256,
    }


def parse_policy_validation_nonacceptance(raw: bytes) -> dict[str, object]:
    document = _strict_json(raw, label="policy validation nonacceptance")
    if not isinstance(document, dict) or set(document) != POLICY_NONACCEPTANCE_KEYS:
        raise TopologyError("policy validation nonacceptance has invalid schema keys")
    if canonical_json_bytes(document) != raw or document["schema_version"] != POLICY_NONACCEPTANCE_SCHEMA or document["diagnostic_only"] is not True:
        raise TopologyError("policy validation nonacceptance is invalid")
    stage = document["policy_validation_stage"]
    public_class = document["policy_validation_class"]
    custody_status = document["custody_status"]
    source_status = document["policy_source_hash_status"]
    source_digest = document["policy_source_sha256"]
    if not all(isinstance(value, str) for value in (stage, public_class, custody_status, source_status, source_digest)):
        raise TopologyError("policy validation nonacceptance typed fields are invalid")
    if stage not in POLICY_VALIDATION_STAGES or public_class not in POLICY_STAGE_CLASSES[stage]:
        raise TopologyError("policy validation nonacceptance stage/class is invalid")
    if custody_status not in {"PRE_EXECUTION_VALIDATED", "POST_CUSTODY_POSTCHECK_PASS"}:
        raise TopologyError("policy validation nonacceptance custody status is invalid")
    if stage == "POST_CUSTODY_REREAD_COMPARISON":
        if custody_status != "POST_CUSTODY_POSTCHECK_PASS":
            raise TopologyError("policy validation nonacceptance custody binding is invalid")
    elif custody_status != "PRE_EXECUTION_VALIDATED":
        raise TopologyError("policy validation nonacceptance custody binding is invalid")
    for field in ("foundation_run_id", "foundation_head_sha", "foundation_validation_date"):
        if not isinstance(document[field], str):
            raise TopologyError("policy validation nonacceptance binding is invalid")
    if not RUN_ID.fullmatch(document["foundation_run_id"]) or not HEAD_SHA.fullmatch(document["foundation_head_sha"]):
        raise TopologyError("policy validation nonacceptance binding is invalid")
    parse_foundation_validation_date(document["foundation_validation_date"])
    for field in (
        "foundation_context_sha256", "inventory_sha256", "baseline_sha256", "baseline_candidate_ids_sha256",
        "baseline_node_list_sha256", "remainder_sha256", "remainder_candidate_ids_sha256",
        "remainder_node_list_sha256", "custody_policy_sha256", "nonacceptance_sha256",
    ):
        if not isinstance(document[field], str) or not HEX64.fullmatch(document[field]):
            raise TopologyError("policy validation nonacceptance hash is invalid")
    status = source_status
    digest = source_digest
    if stage == "SOURCE_ACQUISITION_HEAD_BINDING":
        source_valid = status == "UNAVAILABLE" and digest == ""
    elif stage == "POST_CUSTODY_REREAD_COMPARISON":
        source_valid = status == "PRE_EXECUTION_SNAPSHOT" and isinstance(digest, str) and HEX64.fullmatch(digest)
    else:
        source_valid = status == "CURRENT_STAGE_BYTES" and isinstance(digest, str) and HEX64.fullmatch(digest)
    if not source_valid:
        raise TopologyError("policy validation nonacceptance source binding is invalid")
    if document["nonacceptance_sha256"] != _sha256({key: value for key, value in document.items() if key != "nonacceptance_sha256"}):
        raise TopologyError("policy validation nonacceptance self-hash mismatch")
    return document


def _publish_policy_nonacceptance(path: Path, payload: dict[str, object]) -> None:
    payload["nonacceptance_sha256"] = _sha256(payload)
    encoded = canonical_json_bytes(payload)
    _publish_failure_diagnostic(path, encoded)
    if path.read_bytes() != encoded or parse_policy_validation_nonacceptance(path.read_bytes()) != payload:
        raise TopologyError("policy validation nonacceptance post-write reread failed")


def read_policy_validation_nonacceptance(
    path: Path, *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Read diagnostic-only policy evidence without parsing or approving its policy source."""
    _reject_unsafe_raw_reason_nonacceptance_presence(path.parent)
    try:
        document = parse_policy_validation_nonacceptance(path.read_bytes())
    except OSError as exc:
        raise TopologyError("policy validation nonacceptance is missing") from exc
    if document["policy_source_hash_status"] != "UNAVAILABLE":
        try:
            source = _allowlist_bytes_at_head(head_sha)
        except Exception:
            raise TopologyError("policy validation nonacceptance source binding drift") from None
        if hashlib.sha256(source).hexdigest() != document["policy_source_sha256"]:
            raise TopologyError("policy validation nonacceptance source binding drift")
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    remainder, nodes = _load_portable_root_remainder(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    custody = _validate_custody_policy(baseline["collector_policy"])
    expected = _policy_nonacceptance_payload(
        baseline=baseline, remainder=remainder, nodes=nodes, custody=custody,
        failure=_PolicyStageFailure(
            str(document["policy_validation_stage"]), str(document["policy_validation_class"]),
            str(document["policy_source_hash_status"]), str(document["policy_source_sha256"]),
        ), custody_status=str(document["custody_status"]),
    )
    for field, value in expected.items():
        if document[field] != value:
            raise TopologyError("policy validation nonacceptance binding drift")
    return document


def _unsafe_raw_reason_nonacceptance_payload(
    *, baseline: dict[str, object], remainder: dict[str, object], nodes: tuple[str, ...],
    custody: dict[str, str], pytest_exit_status: str,
) -> dict[str, object]:
    return {
        "schema_version": UNSAFE_RAW_REASON_NONACCEPTANCE_SCHEMA, "diagnostic_only": True,
        "foundation_run_id": baseline["foundation_run_id"], "foundation_head_sha": baseline["foundation_head_sha"],
        "foundation_validation_date": baseline["foundation_validation_date"],
        "foundation_context_sha256": baseline["foundation_context_sha256"],
        "inventory_sha256": LOCKED_INVENTORY_SHA256, "baseline_sha256": baseline["baseline_sha256"],
        "baseline_candidate_ids_sha256": _ids_sha256(tuple(baseline["candidate_node_ids"])),
        "baseline_node_list_sha256": baseline["candidate_file_sha256"],
        "remainder_sha256": remainder["remainder_sha256"],
        "remainder_candidate_ids_sha256": _ids_sha256(nodes),
        "remainder_node_list_sha256": remainder["remainder_file_sha256"],
        "custody_policy_sha256": _sha256(custody), "custody_postcheck_status": "PASS",
        "pytest_exit_status": pytest_exit_status,
        "raw_reason_nonacceptance_state": "UNSAFE_RAW_REASON_OBSERVED",
    }


def parse_unsafe_raw_reason_nonacceptance(raw: bytes) -> dict[str, object]:
    document = _strict_json(raw, label="unsafe raw reason nonacceptance")
    if not isinstance(document, dict) or set(document) != UNSAFE_RAW_REASON_NONACCEPTANCE_KEYS:
        raise TopologyError("unsafe raw reason nonacceptance has invalid schema keys")
    if (
        canonical_json_bytes(document) != raw
        or document["schema_version"] != UNSAFE_RAW_REASON_NONACCEPTANCE_SCHEMA
        or document["diagnostic_only"] is not True
        or document["custody_postcheck_status"] != "PASS"
        or document["raw_reason_nonacceptance_state"] != "UNSAFE_RAW_REASON_OBSERVED"
    ):
        raise TopologyError("unsafe raw reason nonacceptance is invalid")
    for field in ("foundation_run_id", "foundation_head_sha", "foundation_validation_date", "pytest_exit_status"):
        if not isinstance(document[field], str):
            raise TopologyError("unsafe raw reason nonacceptance binding is invalid")
    if (
        not RUN_ID.fullmatch(document["foundation_run_id"])
        or document["foundation_run_id"] == "0"
        or not HEAD_SHA.fullmatch(document["foundation_head_sha"])
        or not re.fullmatch(r"(?:0|[1-9][0-9]*)", document["pytest_exit_status"])
    ):
        raise TopologyError("unsafe raw reason nonacceptance binding is invalid")
    parse_foundation_validation_date(document["foundation_validation_date"])
    for field in (
        "foundation_context_sha256", "inventory_sha256", "baseline_sha256", "baseline_candidate_ids_sha256",
        "baseline_node_list_sha256", "remainder_sha256", "remainder_candidate_ids_sha256",
        "remainder_node_list_sha256", "custody_policy_sha256", "nonacceptance_sha256",
    ):
        if not isinstance(document[field], str) or not HEX64.fullmatch(document[field]):
            raise TopologyError("unsafe raw reason nonacceptance hash is invalid")
    if document["nonacceptance_sha256"] != _sha256({
        key: value for key, value in document.items() if key != "nonacceptance_sha256"
    }):
        raise TopologyError("unsafe raw reason nonacceptance self-hash mismatch")
    return document


def _publish_unsafe_raw_reason_nonacceptance(path: Path, payload: dict[str, object]) -> None:
    if path != _unsafe_raw_reason_nonacceptance_path(path.parent):
        raise TopologyError("unsafe raw reason nonacceptance writer requires the canonical path")
    payload["nonacceptance_sha256"] = _sha256(payload)
    encoded = canonical_json_bytes(payload)
    _publish_failure_diagnostic(path, encoded)
    if path.read_bytes() != encoded or parse_unsafe_raw_reason_nonacceptance(path.read_bytes()) != payload:
        raise TopologyError("unsafe raw reason nonacceptance post-write reread failed")


def read_unsafe_raw_reason_nonacceptance(
    path: Path, *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Read fixed diagnostic-only evidence; it cannot approve, aggregate, or publish anything."""
    topology_root = evidence_root / "capability-topology"
    canonical = _unsafe_raw_reason_nonacceptance_path(topology_root)
    if path != canonical:
        raise TopologyError("unsafe raw reason nonacceptance reader requires the canonical path")
    instances = _unsafe_raw_reason_nonacceptance_instances(topology_root)
    if instances != (canonical,):
        raise TopologyError("unsafe raw reason nonacceptance has a second unsafe record")
    conflicts = [
        topology_root / "portable-root-remainder.failure-diagnostic.json",
        _policy_nonacceptance_path(topology_root),
        topology_root / "portable-root-remainder.governance.json",
    ]
    for code in CODE_CLASSIFICATION:
        conflicts.extend((topology_root / f"{code}.json", topology_root / f"{code}.governance.json"))
    if any(os.path.lexists(candidate) for candidate in conflicts):
        raise TopologyError("unsafe raw reason nonacceptance conflicts with topology terminal evidence")
    try:
        document = parse_unsafe_raw_reason_nonacceptance(path.read_bytes())
    except OSError as exc:
        raise TopologyError("unsafe raw reason nonacceptance is missing") from exc
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    remainder, nodes = _load_portable_root_remainder(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    custody = _validate_custody_policy(baseline["collector_policy"])
    expected = _unsafe_raw_reason_nonacceptance_payload(
        baseline=baseline, remainder=remainder, nodes=nodes, custody=custody,
        pytest_exit_status=str(document["pytest_exit_status"]),
    )
    for field, value in expected.items():
        if document[field] != value:
            raise TopologyError("unsafe raw reason nonacceptance binding drift")
    return document


def _ids_sha256(nodes: tuple[str, ...]) -> str:
    return _sha256(list(nodes))


def compare_failure_policy_link(
    snapshot: dict[str, object], *, component: str, test_node_id: str, outcome: str,
    normalized_reason_commitment_sha256: str,
) -> tuple[str, str]:
    """Apply the closed v1 policy-link precedence without approving an outcome."""
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        raise TopologyError("failure diagnostic policy snapshot is malformed")
    policy: dict[str, object] | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            raise TopologyError("failure diagnostic policy snapshot is malformed")
        if entry.get("component") == component and entry.get("test_node_id") == test_node_id:
            if policy is not None:
                raise TopologyError("failure diagnostic policy snapshot has duplicate entry")
            policy = entry
    if policy is None:
        return "NO_POLICY_ENTRY", ""
    allowed = policy.get("allowed_in_ci")
    if type(allowed) is not bool:
        raise TopologyError("failure diagnostic policy has non-boolean allowed_in_ci")
    policy_hash = policy.get("policy_entry_sha256")
    if not isinstance(policy_hash, str) or not HEX64.fullmatch(policy_hash):
        raise TopologyError("failure diagnostic policy has invalid entry hash")
    if policy.get("outcome") != outcome:
        return "OUTCOME_MISMATCH", policy_hash
    if policy.get("normalized_reason_commitment_sha256") != normalized_reason_commitment_sha256:
        return "REASON_MISMATCH", policy_hash
    if allowed is not True:
        return "CI_DISALLOWED", policy_hash
    return "EXACT_POLICY_MATCH", policy_hash


def _raw_observation_domain(item: dict[str, object]) -> tuple[str, str, str, str, str]:
    outcome = item.get("outcome")
    phase = item.get("phase")
    has_wasxfail = "wasxfail" in item
    wasxfail = item.get("wasxfail")
    if has_wasxfail and type(wasxfail) is not bool:
        raise TopologyError("raw diagnostic observation has invalid wasxfail")
    if outcome in {"xfailed", "xpassed"} and has_wasxfail and wasxfail is not True:
        raise TopologyError("raw diagnostic observation has invalid wasxfail")
    if wasxfail or outcome in {"xfailed", "xpassed"}:
        if outcome in {"passed", "xpassed"} and phase == "call":
            return "xpassed", "call", "WAS_XFAIL", "PYTEST_XPASS_MARKER", "PYTEST_WASXFAIL"
        if outcome in {"skipped", "xfailed"} and phase in {"setup", "call", "teardown"}:
            return "xfailed", str(phase), "WAS_XFAIL", "PYTEST_XFAIL_MARKER", "PYTEST_WASXFAIL"
    mapping = {
        ("passed", "call"): ("passed", "NOT_WAS_XFAIL", "NONE", "NONE"),
        ("skipped", "setup"): ("skipped", "NOT_WAS_XFAIL", "PYTEST_SKIP_REASON", "PYTEST_REPORT"),
        ("skipped", "call"): ("skipped", "NOT_WAS_XFAIL", "PYTEST_SKIP_REASON", "PYTEST_REPORT"),
        ("skipped", "teardown"): ("skipped", "NOT_WAS_XFAIL", "PYTEST_SKIP_REASON", "PYTEST_REPORT"),
        ("deselected", "collection"): ("deselected", "NOT_WAS_XFAIL", "MARKER_DESELECT_REASON", "PYTEST_DESELECT_HOOK"),
        ("failed", "setup"): ("failed", "NOT_WAS_XFAIL", "PYTEST_FAILURE_REASON", "PYTEST_REPORT"),
        ("failed", "call"): ("failed", "NOT_WAS_XFAIL", "PYTEST_FAILURE_REASON", "PYTEST_REPORT"),
        ("failed", "teardown"): ("failed", "NOT_WAS_XFAIL", "PYTEST_FAILURE_REASON", "PYTEST_REPORT"),
        ("failed", "collection"): ("failed", "NOT_WAS_XFAIL", "GOVERNANCE_COLLECTION_FAILURE", "GOVERNANCE_COLLECTION_HOOK"),
        ("error", "setup"): ("error", "NOT_WAS_XFAIL", "PYTEST_ERROR_REASON", "PYTEST_REPORT"),
        ("error", "teardown"): ("error", "NOT_WAS_XFAIL", "PYTEST_ERROR_REASON", "PYTEST_REPORT"),
        ("error", "collection"): ("error", "NOT_WAS_XFAIL", "PYTEST_COLLECTION_ERROR", "PYTEST_COLLECTOR"),
        ("not_run", "session"): ("not_run", "NOT_WAS_XFAIL", "MISSING_FINAL_REPORT", "GOVERNANCE_SESSION"),
    }
    try:
        canonical_outcome, xfail_state, reason_class, provenance = mapping[(outcome, phase)]
    except KeyError as exc:
        raise TopologyError("raw diagnostic observation has an invalid closed domain") from exc
    return canonical_outcome, str(phase), xfail_state, reason_class, provenance


def _structurally_valid_raw_observations(document: object, nodes: tuple[str, ...]) -> tuple[list[dict[str, object]], str]:
    """Validate complete private raw evidence before inspecting any untrusted reason."""
    if not isinstance(document, dict) or document.get("component") != "root" or not isinstance(document.get("tests"), list):
        raise TopologyError("raw diagnostic report is malformed")
    exit_status = document.get("pytest_exit_status")
    if isinstance(exit_status, bool) or not isinstance(exit_status, int) or exit_status < 0:
        raise TopologyError("raw diagnostic report has invalid pytest exit status")
    observations: list[dict[str, object]] = []
    for raw in document["tests"]:
        if not isinstance(raw, dict) or raw.get("component") != "root" or not _is_portable_root_pytest_node_id(raw.get("test_node_id")):
            raise TopologyError("raw diagnostic observation is malformed")
        _raw_observation_domain(raw)
        observations.append(raw)
    observations.sort(key=lambda item: str(item["test_node_id"]).encode())
    if tuple(item["test_node_id"] for item in observations) != nodes or len(observations) != len(nodes):
        raise TopologyError("raw diagnostic observations do not exactly match selected nodes")
    if not observations:
        raise TopologyError("failure diagnostic has no observations")
    return observations, str(exit_status)


def _has_unsafe_raw_reason(observations: list[dict[str, object]]) -> bool:
    """Return one private boolean; rejected values are never normalized, committed, or compared."""
    for raw in observations:
        reason = raw.get("reason")
        if not isinstance(reason, str) or not _is_v1_normalized_reason(reason) or not _reason_is_safe(reason):
            return True
    return False


def _diagnostic_observations(document: object, nodes: tuple[str, ...], snapshot: dict[str, object]) -> tuple[list[dict[str, object]], str]:
    raw_observations, exit_status = _structurally_valid_raw_observations(document, nodes)
    if _has_unsafe_raw_reason(raw_observations):
        raise TopologyError("raw diagnostic observation has unsafe reason")
    observations: list[dict[str, object]] = []
    for raw in raw_observations:
        outcome, phase, xfail_state, reason_class, provenance = _raw_observation_domain(raw)
        reason = raw.get("reason", "")
        assert isinstance(reason, str)  # Proven by _has_unsafe_raw_reason above.
        commitment = "" if reason_class == "NONE" else reason_commitment_sha256(reason)
        policy_match, policy_hash = "NOT_APPLICABLE", ""
        if outcome in {"skipped", "deselected"}:
            policy_match, policy_hash = compare_failure_policy_link(
                snapshot, component="root", test_node_id=raw["test_node_id"], outcome=outcome,
                normalized_reason_commitment_sha256=commitment,
            )
        observations.append({
            "test_node_id": raw["test_node_id"], "component": "root", "outcome": outcome, "phase": phase,
            "xfail_state": xfail_state, "reason_class": reason_class, "reason_provenance": provenance,
            "normalized_reason_commitment_sha256": commitment, "policy_match_result": policy_match,
            "existing_policy_entry_sha256": policy_hash,
        })
    return observations, exit_status


def _failure_diagnostic_payload(
    *, baseline: dict[str, object], remainder: dict[str, object], nodes: tuple[str, ...], run_id: str,
    head_sha: str, custody: dict[str, str], pytest_exit_status: str, snapshot: dict[str, object],
    observations: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": FAILURE_DIAGNOSTIC_SCHEMA, "diagnostic_only": True,
        "foundation_run_id": run_id, "foundation_head_sha": head_sha,
        # Direct unit helpers may exercise an intentionally pre-context fixture;
        # the CLI/Foundation route cannot reach this fallback because it requires
        # a verified context and v3 baseline binding before execution.
        "foundation_validation_date": baseline.get("foundation_validation_date", "1970-01-01"),
        "foundation_context_sha256": baseline.get("foundation_context_sha256", "0" * 64),
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "baseline_candidate_ids_sha256": _ids_sha256(tuple(baseline["candidate_node_ids"])),
        "baseline_node_list_sha256": baseline["candidate_file_sha256"],
        "remainder_candidate_ids_sha256": _ids_sha256(nodes),
        "remainder_node_list_sha256": remainder["remainder_file_sha256"],
        "custody_policy_sha256": _sha256(custody), "custody_postcheck_status": "PASS",
        "pytest_exit_status": pytest_exit_status, "policy_snapshot": snapshot,
        "policy_snapshot_sha256": _sha256(snapshot), "observations": observations,
    }


def _validate_failure_diagnostic_shape(document: object) -> dict[str, object]:
    if not isinstance(document, dict) or set(document) != FAILURE_DIAGNOSTIC_KEYS:
        raise TopologyError("failure diagnostic has invalid schema keys")
    if document["schema_version"] != FAILURE_DIAGNOSTIC_SCHEMA or document["diagnostic_only"] is not True:
        raise TopologyError("failure diagnostic has invalid schema")
    if document["custody_postcheck_status"] != "PASS":
        raise TopologyError("failure diagnostic has no custody postcheck")
    if not isinstance(document["foundation_run_id"], str) or not RUN_ID.fullmatch(document["foundation_run_id"]) or document["foundation_run_id"] == "0":
        raise TopologyError("failure diagnostic has invalid Foundation run")
    if not isinstance(document["foundation_head_sha"], str) or not HEAD_SHA.fullmatch(document["foundation_head_sha"]):
        raise TopologyError("failure diagnostic has invalid Foundation head")
    parse_foundation_validation_date(document["foundation_validation_date"])
    if not isinstance(document["foundation_context_sha256"], str) or not HEX64.fullmatch(document["foundation_context_sha256"]):
        raise TopologyError("failure diagnostic has invalid Foundation context")
    if not isinstance(document["pytest_exit_status"], str) or not re.fullmatch(r"[1-9][0-9]*", document["pytest_exit_status"]):
        raise TopologyError("failure diagnostic has no non-pass proof")
    for field in (
        "inventory_sha256", "baseline_candidate_ids_sha256", "baseline_node_list_sha256",
        "remainder_candidate_ids_sha256", "remainder_node_list_sha256", "custody_policy_sha256",
        "policy_snapshot_sha256", "diagnostic_sha256",
    ):
        if not isinstance(document[field], str) or not HEX64.fullmatch(document[field]):
            raise TopologyError(f"failure diagnostic has invalid {field}")
    snapshot = document["policy_snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != POLICY_SNAPSHOT_KEYS:
        raise TopologyError("failure diagnostic has malformed policy snapshot")
    if (
        snapshot["snapshot_schema_version"] != POLICY_SNAPSHOT_SCHEMA
        or snapshot["allowlist_schema_version"] != "1"
        or snapshot["policy_entry_schema_version"] != POLICY_ENTRY_SCHEMA
        or not isinstance(snapshot["entries"], list)
        or not isinstance(snapshot["allowlist_source_sha256"], str)
        or not HEX64.fullmatch(snapshot["allowlist_source_sha256"])
    ):
        raise TopologyError("failure diagnostic has invalid policy snapshot")
    entries = snapshot["entries"]
    previous: tuple[bytes, bytes] | None = None
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != POLICY_SNAPSHOT_ENTRY_KEYS:
            raise TopologyError("failure diagnostic has malformed policy entry")
        key = (entry.get("component"), entry.get("test_node_id"))
        if not all(isinstance(value, str) and value for value in key) or key in seen:
            raise TopologyError("failure diagnostic has duplicate policy entry")
        current = (key[0].encode(), key[1].encode())
        if previous is not None and current <= previous:
            raise TopologyError("failure diagnostic policy entries are unordered")
        previous, seen = current, seen | {key}
        if entry["component"] != "root" or entry["outcome"] not in {"skipped", "deselected"}:
            raise TopologyError("failure diagnostic has invalid policy entry")
        if entry["allowed_in_ci"] is not True and entry["allowed_in_ci"] is not False:
            raise TopologyError("failure diagnostic has non-boolean allowed_in_ci")
        if entry["reason_class"] != ("POLICY_SKIP_REASON" if entry["outcome"] == "skipped" else "POLICY_DESELECT_REASON"):
            raise TopologyError("failure diagnostic has invalid policy reason class")
        for field in ("normalized_reason_commitment_sha256", "policy_entry_sha256"):
            if not isinstance(entry[field], str) or not HEX64.fullmatch(entry[field]):
                raise TopologyError("failure diagnostic has invalid policy hash")
    observations = document["observations"]
    if not isinstance(observations, list) or not observations:
        raise TopologyError("failure diagnostic has no observations")
    previous_node: bytes | None = None
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != DIAGNOSTIC_OBSERVATION_KEYS:
            raise TopologyError("failure diagnostic has malformed observation")
        node = observation["test_node_id"]
        if not _is_portable_root_pytest_node_id(node) or (previous_node is not None and node.encode() <= previous_node):
            raise TopologyError("failure diagnostic observations are duplicate or unordered")
        previous_node = node.encode()
        if observation["component"] != "root":
            raise TopologyError("failure diagnostic observation component is invalid")
        outcome = observation["outcome"]
        phase = observation["phase"]
        xfail_state = observation["xfail_state"]
        reason_class = observation["reason_class"]
        provenance = observation["reason_provenance"]
        allowed = {
            ("passed", "call", "NOT_WAS_XFAIL", "NONE", "NONE"),
            ("skipped", "setup", "NOT_WAS_XFAIL", "PYTEST_SKIP_REASON", "PYTEST_REPORT"),
            ("skipped", "call", "NOT_WAS_XFAIL", "PYTEST_SKIP_REASON", "PYTEST_REPORT"),
            ("skipped", "teardown", "NOT_WAS_XFAIL", "PYTEST_SKIP_REASON", "PYTEST_REPORT"),
            ("xfailed", "setup", "WAS_XFAIL", "PYTEST_XFAIL_MARKER", "PYTEST_WASXFAIL"),
            ("xfailed", "call", "WAS_XFAIL", "PYTEST_XFAIL_MARKER", "PYTEST_WASXFAIL"),
            ("xfailed", "teardown", "WAS_XFAIL", "PYTEST_XFAIL_MARKER", "PYTEST_WASXFAIL"),
            ("xpassed", "call", "WAS_XFAIL", "PYTEST_XPASS_MARKER", "PYTEST_WASXFAIL"),
            ("deselected", "collection", "NOT_WAS_XFAIL", "MARKER_DESELECT_REASON", "PYTEST_DESELECT_HOOK"),
            ("failed", "setup", "NOT_WAS_XFAIL", "PYTEST_FAILURE_REASON", "PYTEST_REPORT"),
            ("failed", "call", "NOT_WAS_XFAIL", "PYTEST_FAILURE_REASON", "PYTEST_REPORT"),
            ("failed", "teardown", "NOT_WAS_XFAIL", "PYTEST_FAILURE_REASON", "PYTEST_REPORT"),
            ("failed", "collection", "NOT_WAS_XFAIL", "GOVERNANCE_COLLECTION_FAILURE", "GOVERNANCE_COLLECTION_HOOK"),
            ("error", "setup", "NOT_WAS_XFAIL", "PYTEST_ERROR_REASON", "PYTEST_REPORT"),
            ("error", "teardown", "NOT_WAS_XFAIL", "PYTEST_ERROR_REASON", "PYTEST_REPORT"),
            ("error", "collection", "NOT_WAS_XFAIL", "PYTEST_COLLECTION_ERROR", "PYTEST_COLLECTOR"),
            ("not_run", "session", "NOT_WAS_XFAIL", "MISSING_FINAL_REPORT", "GOVERNANCE_SESSION"),
        }
        if (outcome, phase, xfail_state, reason_class, provenance) not in allowed:
            raise TopologyError("failure diagnostic observation domain is invalid")
        commitment = observation["normalized_reason_commitment_sha256"]
        existing = observation["existing_policy_entry_sha256"]
        match = observation["policy_match_result"]
        if outcome in {"skipped", "deselected"}:
            if match not in {"NO_POLICY_ENTRY", "OUTCOME_MISMATCH", "REASON_MISMATCH", "CI_DISALLOWED", "EXACT_POLICY_MATCH"}:
                raise TopologyError("failure diagnostic policy match is invalid")
            if match == "NO_POLICY_ENTRY":
                if not isinstance(commitment, str) or not HEX64.fullmatch(commitment) or existing != "":
                    raise TopologyError("failure diagnostic no-policy binding is invalid")
            elif not (isinstance(commitment, str) and HEX64.fullmatch(commitment) and isinstance(existing, str) and HEX64.fullmatch(existing)):
                raise TopologyError("failure diagnostic policy binding is invalid")
        elif outcome == "passed":
            if match != "NOT_APPLICABLE" or existing != "" or commitment != "":
                raise TopologyError("failure diagnostic non-policy binding is invalid")
        elif (
            match != "NOT_APPLICABLE"
            or existing != ""
            or not isinstance(commitment, str)
            or not HEX64.fullmatch(commitment)
        ):
            raise TopologyError("failure diagnostic non-policy binding is invalid")
    return document


def parse_failure_diagnostic(raw: bytes) -> dict[str, object]:
    document = _strict_json(raw, label="failure diagnostic")
    if canonical_json_bytes(document) != raw:
        raise TopologyError("failure diagnostic is not canonical")
    parsed = _validate_failure_diagnostic_shape(document)
    if parsed["policy_snapshot_sha256"] != _sha256(parsed["policy_snapshot"]):
        raise TopologyError("failure diagnostic policy snapshot hash mismatch")
    if parsed["diagnostic_sha256"] != _sha256({key: value for key, value in parsed.items() if key != "diagnostic_sha256"}):
        raise TopologyError("failure diagnostic self-hash mismatch")
    return parsed


def read_failure_diagnostic(
    path: Path, *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Read and bind a failure record; this reader cannot publish receipts or change policy."""
    _reject_policy_nonacceptance_presence(path.parent)
    _reject_unsafe_raw_reason_nonacceptance_presence(path.parent)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TopologyError("failure diagnostic is missing") from exc
    document = parse_failure_diagnostic(raw)
    baseline = load_portable_root_baseline(inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha, foundation_context_path=foundation_context_path)
    remainder_document, nodes = _load_portable_root_remainder(inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha, foundation_context_path=foundation_context_path)
    custody = _validate_custody_policy(baseline["collector_policy"])
    validation_date = parse_foundation_validation_date(baseline["foundation_validation_date"]) if "foundation_validation_date" in baseline else None
    snapshot, _ = _validated_policy_snapshot(head_sha, validation_date)
    expected = _failure_diagnostic_payload(
        baseline=baseline, remainder=remainder_document, nodes=nodes, run_id=run_id, head_sha=head_sha,
        custody=custody, pytest_exit_status=str(document["pytest_exit_status"]), snapshot=snapshot,
        observations=list(document["observations"]),
    )
    for field, value in expected.items():
        if field != "observations" and document[field] != value:
            raise TopologyError("failure diagnostic binding drift")
    if tuple(item["test_node_id"] for item in document["observations"]) != nodes:
        raise TopologyError("failure diagnostic observations are foreign or incomplete")
    for observation in document["observations"]:
        outcome = observation["outcome"]
        if outcome not in {"skipped", "deselected"}:
            continue
        expected_match, expected_hash = compare_failure_policy_link(
            snapshot, component="root", test_node_id=observation["test_node_id"], outcome=outcome,
            normalized_reason_commitment_sha256=observation["normalized_reason_commitment_sha256"],
        )
        if observation["policy_match_result"] != expected_match or observation["existing_policy_entry_sha256"] != expected_hash:
            raise TopologyError("failure diagnostic policy-link binding drift")
    return document


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
    if receipt["redacted_fact_class"] not in REDACTED_FACT_CLASSES:
        raise TopologyError("receipt has unredacted fact class")
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
    paths: list[Path], *, rows: tuple[InventoryRow, ...], foundation_run_id: str,
    foundation_head_sha: str, foundation_context: dict[str, object] | None = None,
    closure_proof_path: Path | None = None, sealed_custody: dict[str, str] | None = None,
) -> dict[str, object]:
    if not paths or any(path.parent != paths[0].parent for path in paths):
        raise TopologyError("receipt aggregation has an invalid topology root")
    _reject_unsafe_raw_reason_nonacceptance_presence(paths[0].parent)
    _reject_closed_source_artifacts(paths[0].parent)
    if foundation_context is None or closure_proof_path is None or sealed_custody is None:
        raise TopologyError("portable closure proof is required for aggregation")
    validate_portable_closure_proof(
        closure_proof_path, foundation_run_id=foundation_run_id,
        foundation_head_sha=foundation_head_sha, foundation_context=foundation_context,
        sealed_custody=sealed_custody,
    )
    expected_codes = set(CODE_CLASSIFICATION)
    try:
        receipts = [
            validate_receipt(
                path.read_bytes(), rows=rows, foundation_run_id=foundation_run_id,
                foundation_head_sha=foundation_head_sha,
            )
            for path in paths
        ]
    except OSError as exc:
        raise TopologyError("receipt set is missing or unreadable") from exc
    codes = [str(receipt["capability_or_authority_code"]) for receipt in receipts]
    if len(codes) != len(set(codes)) or set(codes) != expected_codes:
        raise TopologyError("receipt set is missing, duplicate, or unknown")
    statuses = {"portable-source": "PASS", "native-capabilities": "PASS", "external-authorities": "PASS"}
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


def reconcile_portable_root_accounting(
    *, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> dict[str, object]:
    """Require the dynamic baseline to be a closed one-execution-or-deferred union."""
    _reject_unsafe_raw_reason_nonacceptance_presence(evidence_root / "capability-topology")
    _reject_closed_source_artifacts(evidence_root / "capability-topology")
    diagnostic = evidence_root / "capability-topology" / "portable-root-remainder.failure-diagnostic.json"
    if os.path.lexists(diagnostic):
        raise TopologyError("failure diagnostic is present; topology aggregation is forbidden")
    _reject_policy_nonacceptance_presence(evidence_root / "capability-topology")
    baseline = load_portable_root_baseline(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    sealed_custody = _validate_custody_policy(baseline["collector_policy"])
    _, remainder = _load_portable_root_remainder(
        inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )
    executed_remainder = _validate_exact_governance_record(
        evidence_root / "capability-topology" / "portable-root-remainder.governance.json",
        remainder,
        sealed_custody,
    )
    rows, closure = _installed_governance_state(inventory, evidence_root, head_sha=head_sha)
    topology_root = evidence_root / "capability-topology"
    receipt_paths = [topology_root / f"{code}.json" for code in sorted(CODE_CLASSIFICATION)]
    context = load_foundation_context(
        foundation_context_path, run_id=run_id, head_sha=head_sha,
    ) if foundation_context_path is not None else None
    if context is None:
        raise TopologyError("portable closure proof requires sealed Foundation context")
    closure_proof_path = topology_root / "portable-defect-closure-proof.json"
    disclosure = aggregate_receipts(
        receipt_paths, rows=rows, foundation_run_id=run_id, foundation_head_sha=head_sha,
        foundation_context=context, closure_proof_path=closure_proof_path,
        sealed_custody=sealed_custody,
    )
    proof = validate_portable_closure_proof(
        closure_proof_path, foundation_run_id=run_id, foundation_head_sha=head_sha,
        foundation_context=context, sealed_custody=sealed_custody,
    )
    accounted: list[str] = [*executed_remainder, *proof["closure_node_ids"]]
    for path in receipt_paths:
        receipt = validate_receipt(
            path.read_bytes(), rows=rows, foundation_run_id=run_id, foundation_head_sha=head_sha,
        )
        expected = tuple(receipt["expected_node_ids"])
        code = str(receipt["capability_or_authority_code"])
        governance = topology_root / f"{code}.governance.json"
        if receipt["outcome"] == "PASS":
            accounted.extend(_validate_exact_governance_record(governance, expected, sealed_custody))
        else:
            if os.path.lexists(governance):
                raise TopologyError("deferred receipt has an execution record")
            accounted.extend(expected)
    candidates = tuple(baseline["candidate_node_ids"])
    if len(accounted) != len(set(accounted)):
        raise TopologyError("portable root accounting has duplicate execution or deferral")
    if tuple(sorted(accounted)) != candidates:
        raise TopologyError("portable root accounting union does not equal baseline")
    return {**disclosure, "portable_root_remainder_status": "PASS", "baseline_candidate_count": str(len(candidates))}


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
    _prepare_private_evidence_directory(evidence_root)
    destination = evidence_root / "capability-topology" / f"{code}.json"
    _prepare_private_evidence_directory(destination.parent)
    _reject_policy_nonacceptance_presence(destination.parent)
    _reject_unsafe_raw_reason_nonacceptance_presence(destination.parent)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_json_bytes(receipt))
        stream.flush()
        os.fsync(stream.fileno())
    return destination


def require_foundation_context(run_id: str, head_sha: str) -> None:
    """Accept only the authoritative GitHub run and the checkout actually executing."""
    current_run = os.environ.get("GITHUB_RUN_ID")
    if not current_run or not RUN_ID.fullmatch(current_run) or current_run == "0":
        raise TopologyError("authoritative GitHub run context is required")
    if run_id != current_run:
        raise TopologyError("Foundation run does not match GitHub run context")
    try:
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise TopologyError("checked-out Foundation head is unavailable") from exc
    if not HEAD_SHA.fullmatch(head_sha) or head_sha != current_head:
        raise TopologyError("Foundation head does not match checked-out HEAD")


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
        unshare = TRUSTED_UNSHARE
        if not os.path.lexists(unshare):
            return "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT"
        try:
            identity = unshare.lstat()
        except OSError:
            return "BROKEN", "NATIVE_IDENTITY_INVALID"
        if (unshare.is_symlink() or not stat.S_ISREG(identity.st_mode)
                or identity.st_uid != 0 or identity.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or not os.access(unshare, os.X_OK)):
            return "BROKEN", "NATIVE_IDENTITY_INVALID"
        try:
            result = subprocess.run([str(unshare), "--user", "--map-root-user", "true"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return "BROKEN", "NATIVE_PROBE_INVALID"
        if result.returncode == 0:
            return "AVAILABLE", "NATIVE_CAPABILITY_VALIDATED"
        if "Operation not permitted" in result.stderr:
            return "UNAVAILABLE", "RUNNER_POLICY_DISALLOWS_USERNS"
        return "BROKEN", "NATIVE_PROBE_INVALID"
    raise TopologyError("unknown native capability")


def _safe_authority_entry(path: Path, *, directory: bool) -> str | None:
    path_state = _authority_path_state(path, directory=directory)
    if path_state is not None:
        return path_state
    try:
        info = path.lstat()
    except OSError:
        return "INVALID"
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected_type:
        return "INVALID"
    if info.st_uid != os.geteuid() or info.st_gid != os.getegid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "INVALID"
    return None


def _authority_path_state(path: Path, *, directory: bool) -> str | None:
    """Inspect every absolute path component with lstat before trusting authority data.

    A parent link would otherwise be followed by normal existence checks before
    the authority leaf is inspected.  Each component is therefore verified as
    a non-link directory before the next component is even addressed.
    """
    if not path.is_absolute():
        return "INVALID"
    parts = path.parts
    if not parts or parts[0] != path.anchor:
        return "INVALID"
    current = Path(path.anchor)
    try:
        root_info = current.lstat()
    except OSError:
        return "INVALID"
    if not stat.S_ISDIR(root_info.st_mode):
        return "INVALID"
    for index, part in enumerate(parts[1:]):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return "ABSENT"
        except OSError:
            return "INVALID"
        if stat.S_ISLNK(info.st_mode):
            return "INVALID"
        final = index == len(parts) - 2
        if not final and not stat.S_ISDIR(info.st_mode):
            return "INVALID"
        if final and not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            return "INVALID"
    return None


def _validate_direct_entries(root: Path, entries: tuple[tuple[str, bool], ...]) -> str | None:
    root_state = _safe_authority_entry(root, directory=True)
    if root_state is not None:
        return "PARTIAL" if root_state == "ABSENT" else "INVALID"
    root_info = root.lstat()
    for relative, directory in entries:
        parts = Path(relative).parts
        current = root
        for index, part in enumerate(parts):
            current = current / part
            state = _safe_authority_entry(current, directory=index < len(parts) - 1 or directory)
            if state is not None:
                return "PARTIAL" if state == "ABSENT" else "INVALID"
            info = current.lstat()
            if info.st_uid != root_info.st_uid or info.st_gid != root_info.st_gid:
                return "INVALID"
    return None


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _digest_fd(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _default_phase3b_validator(root: Path) -> object:
    from trading_control.phase3b_sources import analyze_phase3b_sources
    return analyze_phase3b_sources(root)


def _phase3b_valid(analysis: object) -> bool:
    return (
        getattr(analysis, "inventory_hash", None) == "dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce"
        and getattr(analysis, "decision_total", None) == 16517
        and getattr(analysis, "cost_sessions", None) == 20
        and getattr(analysis, "asset_count", None) == 17
        and getattr(analysis, "asset_source_files", None) == 2209
    )


def _external_preflight(
    code: str, *, corpus_root: Path = PHASE3B_ROOT, uv_path: Path = LEGACY_UV,
    legacy_root: Path = ROOT / "legacy/research-backend",
    corpus_validator: Callable[[Path], object] = _default_phase3b_validator,
    expected_uv_sha256: str = LEGACY_UV_SHA256,
    expected_uv_version: str = LEGACY_UV_VERSION,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str, str]:
    if code == "EXT-PHASE3B-CORPUS":
        root_state = _safe_authority_entry(corpus_root, directory=True)
        if root_state == "ABSENT":
            return "ABSENT", "AUTHORITY_ROOT_ABSENT"
        if root_state is not None:
            return "INVALID", "AUTHORITY_INVALID"
        direct = _validate_direct_entries(corpus_root, PHASE3B_REQUIRED_ENTRIES)
        if direct is not None:
            return direct, "AUTHORITY_PARTIAL" if direct == "PARTIAL" else "AUTHORITY_INVALID"
        try:
            analysis = corpus_validator(corpus_root)
        except FileNotFoundError:
            return "PARTIAL", "AUTHORITY_PARTIAL"
        except Exception:
            return "INVALID", "AUTHORITY_INVALID"
        return ("VALID", "AUTHORITY_COMPLETE_VALIDATED") if _phase3b_valid(analysis) else ("INVALID", "AUTHORITY_INVALID")
    if code == "EXT-LEGACY-UV-AUTHORITY":
        uv_state = _safe_authority_entry(uv_path, directory=False)
        legacy_state = _safe_authority_entry(legacy_root, directory=True)
        if uv_state == legacy_state == "ABSENT":
            return "ABSENT", "AUTHORITY_EXECUTABLE_ABSENT"
        if uv_state == "INVALID" or legacy_state == "INVALID":
            return "INVALID", "AUTHORITY_INVALID"
        if uv_state == "ABSENT" or legacy_state == "ABSENT":
            return "PARTIAL", "AUTHORITY_PARTIAL"
        if uv_state is not None or legacy_state is not None:
            return "INVALID", "AUTHORITY_INVALID"
        direct = _validate_direct_entries(legacy_root, LEGACY_CLOSURE_ENTRIES)
        if direct is not None:
            return direct, "AUTHORITY_PARTIAL" if direct == "PARTIAL" else "AUTHORITY_INVALID"
        descriptor = -1
        try:
            named_before = uv_path.lstat()
            descriptor = os.open(uv_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
            opened = os.fstat(descriptor)
            digest = _digest_fd(descriptor)
            executable = f"/proc/self/fd/{descriptor}"
            version = runner([executable, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False, pass_fds=(descriptor,))
            sync = runner(
                [executable, "sync", "--frozen", "--extra", "test"],
                cwd=legacy_root, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=120, check=False,
                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1", "UV_OFFLINE": "1"}, pass_fds=(descriptor,),
            )
            named_after = uv_path.lstat()
            stable = _identity(named_before) == _identity(opened) == _identity(named_after) and _digest_fd(descriptor) == digest
        except (OSError, subprocess.SubprocessError):
            return "INVALID", "AUTHORITY_INVALID"
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (stat.S_IMODE(opened.st_mode) != 0o755 or not stable or digest != expected_uv_sha256
                or version.returncode != 0 or version.stdout.strip() != expected_uv_version
                or sync.returncode != 0):
            return "INVALID", "AUTHORITY_INVALID"
        return "VALID", "AUTHORITY_COMPLETE_VALIDATED"
    raise TopologyError("unknown external authority")


def _run_exact_observations(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(report.parent, 0o700)
    environment = dict(os.environ, TEST_GOVERNANCE_REPORT=str(report), TEST_GOVERNANCE_COMPONENT="root", TEST_GOVERNANCE_NO_CLOBBER="1")
    raw_descriptor = environment.get("TEST_GOVERNANCE_CUSTODY_FD")
    pass_fds: tuple[int, ...] = ()
    if raw_descriptor is not None:
        if not raw_descriptor.isdecimal():
            raise TopologyError("retained custody descriptor is malformed")
        descriptor = int(raw_descriptor)
        try:
            os.fstat(descriptor)
        except OSError as exc:
            raise TopologyError("retained custody descriptor is unavailable") from exc
        pass_fds = (descriptor,)
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "--portable-embedded-proof",
            "-m", PORTABLE_ROOT_MARKER, "-p", "scripts.test_governance_pytest", *nodes,
        ],
        stdin=subprocess.DEVNULL,
        env=environment,
        pass_fds=pass_fds,
        check=False,
    )
    if not report.is_file():
        raise TopologyError("selected pytest collection or execution failed")
    document = _strict_json(report.read_bytes(), label="raw exact report")
    if not isinstance(document, dict):
        raise TopologyError("governance report is malformed")
    observed = document.get("tests")
    if not isinstance(observed, list):
        raise TopologyError("governance report is malformed")
    # The caller validates the complete report after the retained-custody postcheck,
    # so a non-pass can become a failure-only record rather than being discarded here.
    return nodes


def _run_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    """Legacy strict public runner retained for exact all-pass lane callers."""
    if _run_exact_observations((*nodes,), report) != nodes:
        raise TopologyError("exact runner changed the generated node list")
    document = _strict_json(report.read_bytes(), label="raw exact report")
    if not isinstance(document, dict) or not isinstance(document.get("tests"), list):
        raise TopologyError("governance report is malformed")
    observed = document["tests"]
    if any(
        isinstance(item, dict)
        and (item.get("outcome") in {"xfailed", "xpassed"} or item.get("wasxfail"))
        for item in observed
    ):
        raise TopologyError("xfail or XPASS observed in exact selection")
    passed = tuple(sorted(str(item.get("test_node_id")) for item in observed if isinstance(item, dict) and item.get("outcome") == "passed"))
    if passed != nodes or len(observed) != len(nodes):
        raise TopologyError("exact node collection/execution proof failed")
    return passed


def run_lane(
    *, lane: str, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    external_preflight: Callable[[str], tuple[str, str]] = _external_preflight,
    exact_runner: Callable[[tuple[str, ...], Path], tuple[str, ...]] = _run_exact,
    foundation_context_path: Path | None = None,
) -> list[Path]:
    require_foundation_context(run_id, head_sha)
    context = _optional_foundation_context(foundation_context_path, run_id=run_id, head_sha=head_sha)
    _require_topology_reservation(evidence_root, run_id, head_sha, context)
    _reject_policy_nonacceptance_presence(evidence_root / "capability-topology")
    _reject_unsafe_raw_reason_nonacceptance_presence(evidence_root / "capability-topology")
    if os.path.lexists(evidence_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"):
        raise TopologyError("failure diagnostic is present; lane publication is forbidden")
    rows = _installed_inventory_rows(inventory, evidence_root)
    if lane == "portable-source":
        if foundation_context_path is None:
            raise TopologyError("portable closure proof requires sealed Foundation context")
        execute_portable_defect_closure(
            inventory=inventory, evidence_root=evidence_root, run_id=run_id,
            head_sha=head_sha, foundation_context_path=foundation_context_path,
            exact_runner=exact_runner,
        )
        return []
    publications: list[Path] = []
    for code in sorted(CODE_CLASSIFICATION):
        expected_lane, expected = _expected_rows(rows, code)
        if expected_lane != lane:
            continue
        state, fact = ("AVAILABLE", "SOURCE_TEST_EXECUTED") if lane == "portable-source" else (_native_preflight(code) if lane == "native-capabilities" else external_preflight(code))
        if state in {"BROKEN", "PARTIAL", "INVALID"}:
            raise TopologyError(f"{code} preflight is {state}")
        if state in {"UNAVAILABLE", "ABSENT"}:
            receipt = make_receipt(run_id=run_id, head_sha=head_sha, lane=lane, code=code, expected=expected, collected=(), state=state, fact=fact, outcome="DEFERRED")
        else:
            baseline = load_portable_root_baseline(
                inventory=inventory, evidence_root=evidence_root, run_id=run_id, head_sha=head_sha,
                foundation_context_path=foundation_context_path,
            )
            governance = evidence_root / "capability-topology" / f"{code}.governance.json"
            selected = _execute_exact_with_retained_custody(
                baseline=baseline, nodes=expected, report=governance, runner=exact_runner,
            )
            receipt = make_receipt(run_id=run_id, head_sha=head_sha, lane=lane, code=code, expected=expected, collected=selected, state=state, fact=fact, outcome="PASS")
        publications.append(publish_receipt(receipt, evidence_root))
    return publications


def _is_portable_root_pytest_node_id(node_id: object) -> bool:
    return (
        isinstance(node_id, str)
        and bool(node_id)
        and node_id.startswith("tests/")
        and all(0x20 <= ord(character) <= 0x7E for character in node_id)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "reserve", "collect-baseline", "prepare-remainder", "run-remainder", "run-lane",
        "check-closure", "aggregate",
    ))
    parser.add_argument("--lane", choices=tuple(CLASSIFICATION_LANE.values()))
    parser.add_argument("--inventory", type=Path, default=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--foundation-context-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run_id, head_sha = _active_foundation_identity()
        load_foundation_context(
            args.foundation_context_path,
            run_id=run_id,
            head_sha=head_sha,
        )
        if args.action == "reserve":
            reserve_topology_evidence(args.evidence_root, run_id=run_id, head_sha=head_sha, foundation_context_path=args.foundation_context_path)
        elif args.action == "collect-baseline":
            collect_portable_root_baseline(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )
        elif args.action == "prepare-remainder":
            prepare_portable_root_remainder(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )
        elif args.action == "run-remainder":
            execute_portable_root_remainder(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )
        elif args.action == "check-closure":
            execute_portable_defect_closure(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )
        elif args.action == "run-lane":
            if args.lane is None:
                raise TopologyError("run-lane requires --lane")
            run_lane(lane=args.lane, inventory=args.inventory, evidence_root=args.evidence_root, run_id=run_id, head_sha=head_sha, foundation_context_path=args.foundation_context_path)
        else:
            print(canonical_json_bytes(reconcile_portable_root_accounting(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )).decode("utf-8"))
    except (TopologyError, OSError, ValueError) as exc:
        print(f"t-g03 capability topology: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
