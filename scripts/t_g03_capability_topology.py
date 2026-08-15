#!/usr/bin/env python3
"""Fail-closed capability-topology receipts for the locked hosted inventory."""

from __future__ import annotations

import hashlib
import json
import csv
import argparse
import ctypes
from collections.abc import Callable
from contextlib import contextmanager
import os
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Any, Sequence


LOCKED_INVENTORY_SHA256 = "44e6d1061b1a087935461edd265d80eda4580ecf23fbe6b3e1d2810e3383a7c1"
LOCKED_CLOSURE_SHA256 = "9b4b02af2972651f75d07b1758232a186041749598518e41ae40d6e34ca2aa88"
LOCKED_GOVERNED_NODE_IDS_SHA256 = "c6c3df6b28154836bacc882f1e0c1d2b652afd4f14dac19f761479dbf5d1242c"
RECEIPT_SCHEMA = "t-g03a-capability-receipt/v1"
NATIVE_RECEIPT_SCHEMA = "t-g03a-native-capability-receipt/v2"
NATIVE_MULTI_RECEIPT_SCHEMA = "t-g03a-native-multi-authority-receipt/v3"
NATIVE_ARTIFACT_MANIFEST_SCHEMA = "t-g03a-native-artifact-manifest/v1"
NATIVE_MULTI_ARTIFACT_MANIFEST_SCHEMA = (
    "t-g03a-native-multi-authority-artifact-manifest/v2"
)
EXTERNAL_RECEIPT_SCHEMA = "t-g03a-external-authority-receipt/v2"
EXTERNAL_ARTIFACT_MANIFEST_SCHEMA = "t-g03a-external-artifact-manifest/v1"
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
NATIVE_RECEIPT_KEYS = frozenset({
    "schema_version", "foundation_run_id", "foundation_head_sha",
    "foundation_validation_date", "foundation_context_sha256", "inventory_sha256",
    "lane", "capability_or_authority_code", "expected_node_ids", "collected_node_ids",
    "preflight_state", "redacted_fact_class", "probe", "selected_test_count",
    "passed", "failed", "unavailable", "completeness_sha256", "outcome",
    "receipt_sha256",
})
NATIVE_PROBE_KEYS = frozenset({
    "command_id", "exit_code", "stdout_sha256", "stderr_sha256",
    "executable_sha256",
})
NATIVE_MULTI_PROBE_KEYS = frozenset({
    "command_id", "exit_code", "stdout_sha256", "stderr_sha256",
})
NATIVE_MULTI_RECEIPT_KEYS = NATIVE_RECEIPT_KEYS | {"authority"}
EXTERNAL_RECEIPT_KEYS = frozenset({
    "schema_version", "foundation_run_id", "foundation_head_sha",
    "foundation_validation_date", "foundation_context_sha256", "inventory_sha256",
    "lane", "capability_or_authority_code", "expected_node_ids", "collected_node_ids",
    "preflight_state", "redacted_fact_class", "authority", "selected_test_count",
    "passed", "failed", "unavailable", "completeness_sha256", "outcome",
    "receipt_sha256",
})
PHASE3B_AUTHORITY_KEYS = frozenset({
    "authority_kind", "regular_directory_status", "expected_inventory_sha256",
    "observed_inventory_sha256", "required_entry_manifest_sha256",
    "required_entry_count", "expected_decision_total", "observed_decision_total",
    "expected_cost_sessions", "observed_cost_sessions", "expected_asset_count",
    "observed_asset_count", "expected_asset_source_files",
    "observed_asset_source_files",
})
LEGACY_UV_AUTHORITY_KEYS = frozenset({
    "authority_kind", "regular_file_status", "expected_uv_sha256",
    "observed_uv_sha256", "expected_uv_version", "observed_uv_version",
    "expected_uid", "observed_uid", "expected_gid", "observed_gid",
    "expected_mode", "observed_mode", "legacy_closure_manifest_sha256",
    "legacy_closure_entry_count", "sync_command_id", "sync_exit_code",
    "sync_stdout_sha256", "sync_stderr_sha256",
})
NAUTILUS_RUNTIME_AUTHORITY_KEYS = frozenset({
    "authority_kind", "base_root_status", "artifact_root_status",
    "runtime_policy_sha256", "base_manifest_sha256", "base_file_count",
    "base_file_inventory_sha256", "artifact_manifest_sha256",
    "artifact_wheel_sha256", "artifact_wheel_size",
})
NAUTILUS_TOOLCHAIN_AUTHORITY_KEYS = frozenset({
    "authority_kind", "rust_root_status", "llvm_root_status",
    "rust_policy_sha256", "llvm_policy_sha256", "rust_manifest_sha256",
    "rust_tree_sha256", "rust_file_count", "llvm_manifest_sha256",
    "llvm_tool_count", "llvm_resource_header_count",
})
NAUTILUS_COMPOSITE_AUTHORITY_KEYS = frozenset({
    "authority_kind", "toolchains", "sandbox",
})
NAUTILUS_SANDBOX_AUTHORITY_KEYS = frozenset({
    "regular_file_status", "policy_sha256", "expected_sha256",
    "observed_sha256", "expected_uid", "observed_uid", "expected_gid",
    "observed_gid", "expected_mode", "observed_mode",
})
NATIVE_ARTIFACT_MANIFEST_KEYS = frozenset({
    "schema_version", "capability_or_authority_code", "foundation_run_id",
    "foundation_head_sha", "foundation_validation_date",
    "foundation_context_sha256", "inventory_sha256", "receipt_filename",
    "receipt_bytes_sha256", "receipt_self_sha256", "governance_filename",
    "governance_present", "governance_sha256", "expected_node_ids",
    "expected_node_ids_sha256", "expected_node_count", "selected_test_count",
    "probe", "outcome", "manifest_sha256",
})
NATIVE_MULTI_ARTIFACT_MANIFEST_KEYS = NATIVE_ARTIFACT_MANIFEST_KEYS | {
    "authority",
}
EXTERNAL_ARTIFACT_MANIFEST_KEYS = frozenset({
    "schema_version", "capability_or_authority_code", "foundation_run_id",
    "foundation_head_sha", "foundation_validation_date",
    "foundation_context_sha256", "inventory_sha256", "receipt_filename",
    "receipt_bytes_sha256", "receipt_self_sha256", "governance_filename",
    "governance_present", "governance_sha256", "expected_node_ids",
    "expected_node_ids_sha256", "expected_node_count", "selected_test_count",
    "preflight_state", "authority", "outcome", "manifest_sha256",
})
NATIVE_BUNDLE_RECEIPT = "receipt.json"
NATIVE_BUNDLE_GOVERNANCE = "governance.json"
NATIVE_BUNDLE_MANIFEST = "manifest.json"
_RENAME_NOREPLACE = 1
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
            or before.st_gid != os.getegid()
            or before.st_nlink != 1
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
            or before.st_gid != os.getegid()
            or before.st_nlink != 1
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


@dataclass(frozen=True)
class _RetainedNativeArtifacts:
    directory_path: Path
    directory_descriptor: int
    directory_identity: tuple[int, ...]
    marker_name: str
    marker_descriptor: int
    marker_identity: tuple[int, ...]
    marker_raw: bytes
    bundle_name: str
    bundle_descriptor: int
    bundle_identity: tuple[int, ...]
    bundle_receipt_descriptor: int
    bundle_receipt_identity: tuple[int, ...]
    bundle_receipt_raw: bytes
    manifest_descriptor: int
    manifest_identity: tuple[int, ...]
    manifest_raw: bytes
    governance_descriptor: int
    governance_identity: tuple[int, ...] | None
    governance_raw: bytes | None


@contextmanager
def _retained_private_native_artifacts(receipt_path: Path):
    directory_path = receipt_path.parent
    marker_descriptor = -1
    bundle_descriptor = -1
    bundle_receipt_descriptor = -1
    manifest_descriptor = -1
    governance_descriptor = -1
    directory_descriptor = -1
    try:
        try:
            before = directory_path.lstat()
            if (
                not stat.S_ISDIR(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o700
            ):
                raise TopologyError("native artifact directory is unsafe")
            directory_descriptor = os.open(
                directory_path,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            )
            opened = os.fstat(directory_descriptor)
        except TopologyError:
            raise
        except OSError as exc:
            raise TopologyError("native artifact directory is unsafe") from exc
        directory_identity = _artifact_identity(opened)
        if directory_identity != _artifact_identity(before):
            raise TopologyError("native artifact directory identity changed")
        marker_descriptor, marker_identity = _open_private_artifact_leaf(
            directory_descriptor, receipt_path.name, label="native receipt",
        )
        marker_raw = _read_descriptor_bytes(marker_descriptor)
        legacy_governance_name = receipt_path.with_suffix(".governance.json").name
        try:
            os.stat(
                legacy_governance_name, dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise TopologyError("legacy flat native governance artifact is unsafe") from exc
        else:
            raise TopologyError("legacy flat native governance artifact is rejected")
        bundle_name = receipt_path.with_suffix(".artifacts").name
        try:
            before_bundle = os.stat(
                bundle_name, dir_fd=directory_descriptor, follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(before_bundle.st_mode)
                or before_bundle.st_uid != os.geteuid()
                or before_bundle.st_gid != os.getegid()
                or stat.S_IMODE(before_bundle.st_mode) != 0o700
            ):
                raise TopologyError("native artifact bundle is unsafe")
            bundle_descriptor = os.open(
                bundle_name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_descriptor,
            )
            opened_bundle = os.fstat(bundle_descriptor)
        except TopologyError:
            raise
        except OSError as exc:
            raise TopologyError("native artifact bundle is unsafe") from exc
        bundle_identity = _artifact_identity(opened_bundle)
        if bundle_identity != _artifact_identity(before_bundle):
            raise TopologyError("native artifact bundle identity changed")
        bundle_receipt_descriptor, bundle_receipt_identity = _open_private_artifact_leaf(
            bundle_descriptor, NATIVE_BUNDLE_RECEIPT, label="native bundled receipt",
        )
        manifest_descriptor, manifest_identity = _open_private_artifact_leaf(
            bundle_descriptor, NATIVE_BUNDLE_MANIFEST, label="native artifact manifest",
        )
        bundle_receipt_raw = _read_descriptor_bytes(bundle_receipt_descriptor)
        manifest_raw = _read_descriptor_bytes(manifest_descriptor)
        governance_identity: tuple[int, ...] | None = None
        governance_raw: bytes | None = None
        try:
            os.stat(
                NATIVE_BUNDLE_GOVERNANCE, dir_fd=bundle_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise TopologyError("native governance artifact is unsafe") from exc
        else:
            governance_descriptor, governance_identity = _open_private_artifact_leaf(
                bundle_descriptor, NATIVE_BUNDLE_GOVERNANCE,
                label="native governance artifact",
            )
            governance_raw = _read_descriptor_bytes(governance_descriptor)
        expected_entries = {
            NATIVE_BUNDLE_RECEIPT, NATIVE_BUNDLE_MANIFEST,
            *({NATIVE_BUNDLE_GOVERNANCE} if governance_raw is not None else set()),
        }
        try:
            entries = set(os.listdir(bundle_descriptor))
        except OSError as exc:
            raise TopologyError("native artifact bundle inventory is unreadable") from exc
        if entries != expected_entries:
            raise TopologyError("native artifact bundle inventory is not exact")
        artifacts = _RetainedNativeArtifacts(
            directory_path, directory_descriptor, directory_identity,
            receipt_path.name, marker_descriptor, marker_identity, marker_raw,
            bundle_name, bundle_descriptor, bundle_identity,
            bundle_receipt_descriptor, bundle_receipt_identity, bundle_receipt_raw,
            manifest_descriptor, manifest_identity, manifest_raw,
            governance_descriptor, governance_identity, governance_raw,
        )
        yield artifacts
    finally:
        for descriptor in (
            governance_descriptor, manifest_descriptor, bundle_receipt_descriptor,
            bundle_descriptor, marker_descriptor, directory_descriptor,
        ):
            if descriptor >= 0:
                os.close(descriptor)


def _postcheck_private_native_artifacts(artifacts: _RetainedNativeArtifacts) -> None:
    try:
        named_directory = artifacts.directory_path.lstat()
        held_directory = os.fstat(artifacts.directory_descriptor)
        named_marker = os.stat(
            artifacts.marker_name, dir_fd=artifacts.directory_descriptor,
            follow_symlinks=False,
        )
        held_marker = os.fstat(artifacts.marker_descriptor)
        named_bundle = os.stat(
            artifacts.bundle_name, dir_fd=artifacts.directory_descriptor,
            follow_symlinks=False,
        )
        held_bundle = os.fstat(artifacts.bundle_descriptor)
        named_receipt = os.stat(
            NATIVE_BUNDLE_RECEIPT, dir_fd=artifacts.bundle_descriptor,
            follow_symlinks=False,
        )
        held_receipt = os.fstat(artifacts.bundle_receipt_descriptor)
        named_manifest = os.stat(
            NATIVE_BUNDLE_MANIFEST, dir_fd=artifacts.bundle_descriptor,
            follow_symlinks=False,
        )
        held_manifest = os.fstat(artifacts.manifest_descriptor)
        if artifacts.governance_descriptor >= 0:
            named_governance = os.stat(
                NATIVE_BUNDLE_GOVERNANCE, dir_fd=artifacts.bundle_descriptor,
                follow_symlinks=False,
            )
            held_governance = os.fstat(artifacts.governance_descriptor)
        else:
            try:
                os.stat(
                    NATIVE_BUNDLE_GOVERNANCE, dir_fd=artifacts.bundle_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                named_governance = held_governance = None
            else:
                raise TopologyError("native governance artifact appeared during validation")
    except TopologyError:
        raise
    except OSError as exc:
        raise TopologyError("native artifact identity changed during validation") from exc
    if (
        _artifact_identity(named_directory) != artifacts.directory_identity
        or _artifact_identity(held_directory) != artifacts.directory_identity
        or _artifact_identity(named_marker) != artifacts.marker_identity
        or _artifact_identity(held_marker) != artifacts.marker_identity
        or _artifact_identity(named_bundle) != artifacts.bundle_identity
        or _artifact_identity(held_bundle) != artifacts.bundle_identity
        or _artifact_identity(named_receipt) != artifacts.bundle_receipt_identity
        or _artifact_identity(held_receipt) != artifacts.bundle_receipt_identity
        or _artifact_identity(named_manifest) != artifacts.manifest_identity
        or _artifact_identity(held_manifest) != artifacts.manifest_identity
        or (
            artifacts.governance_descriptor >= 0
            and (
                artifacts.governance_identity is None
                or _artifact_identity(named_governance) != artifacts.governance_identity
                or _artifact_identity(held_governance) != artifacts.governance_identity
            )
        )
    ):
        raise TopologyError("native artifact identity changed during validation")


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
    "NATIVE-NAUTILUS-SEALED-TOOLCHAINS": "NATIVE_CAPABILITY_REQUIRED",
    "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX": "NATIVE_CAPABILITY_REQUIRED",
    "EXT-PHASE3B-CORPUS": "EXTERNAL_AUTHORITY_REQUIRED",
    "EXT-LEGACY-UV-AUTHORITY": "EXTERNAL_AUTHORITY_REQUIRED",
    "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": "EXTERNAL_AUTHORITY_REQUIRED",
}
CLOSED_CODE_CLASSIFICATION = {
    "SRC-NAUTILUS-PREFLIGHT-FIXTURE-GATING": "PORTABLE_SOURCE_DEFECT",
    "SRC-PHASE4B-FAKEROOT-IDENTITY": "PORTABLE_SOURCE_DEFECT",
    "SRC-SEALEDUV-BWRAP-PREFLIGHT": "PORTABLE_SOURCE_DEFECT",
    "SRC-SEMANTIC-FIXTURE-IDENTITY": "PORTABLE_SOURCE_DEFECT",
}
CLOSED_CODE_COUNTS = {
    "SRC-NAUTILUS-PREFLIGHT-FIXTURE-GATING": 17,
    "SRC-PHASE4B-FAKEROOT-IDENTITY": 2,
    "SRC-SEALEDUV-BWRAP-PREFLIGHT": 27,
    "SRC-SEMANTIC-FIXTURE-IDENTITY": 3,
}
CLOSED_SOURCE_CODE = {
    "tests/foundation/test_nautilus_runtime_closure.py": "SRC-NAUTILUS-PREFLIGHT-FIXTURE-GATING",
    "tests/foundation/test_nautilus_sealed_uv_exec.py": "SRC-SEALEDUV-BWRAP-PREFLIGHT",
    "tests/jobs/test_nautilus_closure.py": "SRC-NAUTILUS-PREFLIGHT-FIXTURE-GATING",
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
NATIVE_FACT_CLASSES = frozenset({
    "NATIVE_COMPONENT_ABSENT", "NATIVE_IDENTITY_INVALID",
    "NATIVE_CAPABILITY_VALIDATED", "RUNNER_POLICY_DISALLOWS_USERNS",
    "NATIVE_PROBE_INVALID", "NATIVE_EXACT_TEST_FAILURE",
    "NATIVE_IDENTITY_REPLACED",
})
EXTERNAL_FACT_CLASSES = frozenset({
    "AUTHORITY_ROOT_ABSENT", "AUTHORITY_EXECUTABLE_ABSENT",
    "AUTHORITY_COMPLETE_VALIDATED", "AUTHORITY_PARTIAL", "AUTHORITY_INVALID",
    "AUTHORITY_DRIFTED", "EXTERNAL_EXACT_TEST_FAILURE",
})
TRUSTED_UNSHARE = Path("/usr/bin/unshare")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
NATIVE_PROBE_NOT_EXECUTED = -1
NATIVE_PROBE_TIMEOUT = -2
NATIVE_COMMAND_IDS = {
    "NATIVE-BWRAP-OS-SANDBOX": "BWRAP_USER_PID_NET_ISOLATION_V1",
    "NATIVE-USERNS-ROOT-PROVISION": "UNSHARE_MAP_ROOT_USER_V1",
    "NATIVE-NAUTILUS-SEALED-TOOLCHAINS": "NAUTILUS_SEALED_TOOLCHAINS_V1",
    "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX": "NAUTILUS_SEALED_BUILD_SANDBOX_V1",
}
NATIVE_MULTI_CODES = frozenset({
    "NATIVE-NAUTILUS-SEALED-TOOLCHAINS",
    "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX",
})


def _native_schema_for_code(code: object) -> str:
    return (
        NATIVE_MULTI_RECEIPT_SCHEMA
        if code in NATIVE_MULTI_CODES else NATIVE_RECEIPT_SCHEMA
    )
NATIVE_DENIAL_STDERR = {
    "NATIVE-BWRAP-OS-SANDBOX": frozenset({
        b"bwrap: Creating new namespace failed: Operation not permitted\n",
        b"bwrap: No permissions to create new namespace, likely because the kernel does not allow non-privileged user namespaces. See <https://deb.li/bubblewrap> or <file:///usr/share/doc/bubblewrap/README.Debian.gz>.\n",
    }),
    "NATIVE-USERNS-ROOT-PROVISION": frozenset({
        b"unshare: unshare failed: Operation not permitted\n",
        b"unshare: write failed /proc/self/uid_map: Operation not permitted\n",
        b"unshare: write failed /proc/self/uid_map: Permission denied\n",
    }),
}
PHASE3B_ROOT = Path("/home/thenam176/.hermes/crypto-research")
PHASE3B_EXPECTED_INVENTORY_SHA256 = "dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce"
PHASE3B_EXPECTED_DECISION_TOTAL = 16517
PHASE3B_EXPECTED_COST_SESSIONS = 20
PHASE3B_EXPECTED_ASSET_COUNT = 17
PHASE3B_EXPECTED_ASSET_SOURCE_FILES = 2209
LEGACY_UV = Path("/home/thenam176/.local/bin/uv")
LEGACY_UV_SHA256 = "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4"
LEGACY_UV_VERSION = "uv 0.11.7 (x86_64-unknown-linux-gnu)"
ROOT = Path(__file__).resolve().parents[1]
NAUTILUS_RUST_TOOLCHAIN = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/rust-1.95.0"
)
NAUTILUS_LLVM_TOOLCHAIN = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/llvm-22.1.3-resource-toolchain"
)
NAUTILUS_BASE_RUNTIME = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/runtime-closure-v3"
)
NAUTILUS_ARTIFACT_ROOT = Path(
    "/home/thenam176/.cache/trading-agent/nautilus/artifacts/"
    "nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c"
)
NAUTILUS_RUST_POLICY = ROOT / "engines/nautilus/toolchain-inputs.json"
NAUTILUS_LLVM_POLICY = ROOT / "engines/nautilus/llvm-toolchain-policy.json"
NAUTILUS_RUNTIME_POLICY = ROOT / "engines/nautilus/runtime-closure-policy.json"
REAL_LEGACY_ROOT = ROOT / "legacy/research-backend"
TRUSTED_BWRAP_POLICY = ROOT / "engines/nautilus/sealed-uv-exec-policy.json"
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
    if len(rows) != 66:
        raise TopologyError("inventory row count drift")
    counts = {code: sum(row.code == code for row in rows) for code in CODE_CLASSIFICATION}
    if counts != {
        "NATIVE-BWRAP-OS-SANDBOX": 18,
        "NATIVE-USERNS-ROOT-PROVISION": 8,
        "NATIVE-NAUTILUS-SEALED-TOOLCHAINS": 22,
        "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX": 10,
        "EXT-PHASE3B-CORPUS": 3,
        "EXT-LEGACY-UV-AUTHORITY": 3,
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": 2,
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
    if len(rows) != 49:
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
    if len(governed) != 115 or _ids_sha256(governed) != LOCKED_GOVERNED_NODE_IDS_SHA256:
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
        targets.extend((
            topology_root / f"{code}.json",
            topology_root / f"{code}.governance.json",
            topology_root / f"{code}.artifacts",
        ))
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
    acceptance_paths.extend(topology_root / f"{code}{suffix}" for code in CODE_CLASSIFICATION for suffix in (".json", ".governance.json", ".artifacts"))
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


def _stable_object_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


@contextmanager
def _retained_private_directory(path: Path, *, label: str):
    descriptor = -1
    try:
        try:
            before = path.lstat()
            if (
                not stat.S_ISDIR(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o700
            ):
                raise TopologyError(f"{label} is unsafe")
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            )
            opened = os.fstat(descriptor)
        except TopologyError:
            raise
        except OSError as exc:
            raise TopologyError(f"{label} is unsafe") from exc
        identity = _artifact_identity(opened)
        if identity != _artifact_identity(before):
            raise TopologyError(f"{label} identity changed")
        yield descriptor, identity
        try:
            named = path.lstat()
            held = os.fstat(descriptor)
        except OSError as exc:
            raise TopologyError(f"{label} identity changed") from exc
        stable_identity = _stable_object_identity(opened)
        if (
            _stable_object_identity(named) != stable_identity
            or _stable_object_identity(held) != stable_identity
        ):
            raise TopologyError(f"{label} identity changed")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_private_leaf(directory_descriptor: int, name: str, content: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != len(content)
        ):
            raise TopologyError("native staged artifact is not private and complete")
    except TopologyError:
        raise
    except OSError as exc:
        raise TopologyError("native staged artifact publication failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _renameat2_noreplace(
    old_directory_descriptor: int, old_name: str,
    new_directory_descriptor: int, new_name: str,
) -> None:
    """Linux atomic no-replace rename; no link/unlink fallback is permitted."""
    if (
        not old_name or not new_name
        or "/" in old_name or "/" in new_name
        or old_name in {".", ".."} or new_name in {".", ".."}
    ):
        raise TopologyError("native artifact rename name is malformed")
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise TopologyError("renameat2 RENAME_NOREPLACE is unavailable") from exc
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        old_directory_descriptor, os.fsencode(old_name),
        new_directory_descriptor, os.fsencode(new_name), _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), new_name)


def _native_artifact_manifest(
    receipt: dict[str, object], receipt_raw: bytes,
    governance_raw: bytes | None,
) -> dict[str, object]:
    expected = tuple(str(item) for item in receipt["expected_node_ids"])
    multi = receipt["schema_version"] == NATIVE_MULTI_RECEIPT_SCHEMA
    manifest: dict[str, object] = {
        "schema_version": (
            NATIVE_MULTI_ARTIFACT_MANIFEST_SCHEMA
            if multi else NATIVE_ARTIFACT_MANIFEST_SCHEMA
        ),
        "capability_or_authority_code": receipt["capability_or_authority_code"],
        "foundation_run_id": receipt["foundation_run_id"],
        "foundation_head_sha": receipt["foundation_head_sha"],
        "foundation_validation_date": receipt["foundation_validation_date"],
        "foundation_context_sha256": receipt["foundation_context_sha256"],
        "inventory_sha256": receipt["inventory_sha256"],
        "receipt_filename": NATIVE_BUNDLE_RECEIPT,
        "receipt_bytes_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "receipt_self_sha256": receipt["receipt_sha256"],
        "governance_filename": NATIVE_BUNDLE_GOVERNANCE if governance_raw is not None else "",
        "governance_present": governance_raw is not None,
        "governance_sha256": (
            hashlib.sha256(governance_raw).hexdigest()
            if governance_raw is not None else EMPTY_SHA256
        ),
        "expected_node_ids": list(expected),
        "expected_node_ids_sha256": _ids_sha256(expected),
        "expected_node_count": len(expected),
        "selected_test_count": receipt["selected_test_count"],
        "probe": receipt["probe"],
        "outcome": receipt["outcome"],
        "manifest_sha256": "",
    }
    if multi:
        manifest["authority"] = receipt["authority"]
    manifest["manifest_sha256"] = _sha256({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    return manifest


def _stage_native_candidate(
    topology_root: Path, receipt: dict[str, object], governance_raw: bytes | None,
) -> Path:
    """Fsync one private inert candidate. Failure deliberately leaves it inert."""
    _prepare_private_evidence_directory(topology_root)
    code = str(receipt["capability_or_authority_code"])
    name = f".native-candidate-{code}-{secrets.token_hex(16)}"
    receipt_raw = canonical_json_bytes(receipt)
    manifest_raw = canonical_json_bytes(
        _native_artifact_manifest(receipt, receipt_raw, governance_raw),
    )
    with _retained_private_directory(
        topology_root, label="native topology directory",
    ) as (parent_descriptor, _):
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            candidate_descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise TopologyError("native candidate staging failed") from exc
        try:
            candidate = os.fstat(candidate_descriptor)
            if (
                not stat.S_ISDIR(candidate.st_mode)
                or candidate.st_uid != os.geteuid()
                or candidate.st_gid != os.getegid()
                or stat.S_IMODE(candidate.st_mode) != 0o700
            ):
                raise TopologyError("native candidate directory is unsafe")
            _write_private_leaf(candidate_descriptor, NATIVE_BUNDLE_RECEIPT, receipt_raw)
            if governance_raw is not None:
                _write_private_leaf(
                    candidate_descriptor, NATIVE_BUNDLE_GOVERNANCE, governance_raw,
                )
            _write_private_leaf(candidate_descriptor, NATIVE_BUNDLE_MANIFEST, manifest_raw)
            os.fsync(candidate_descriptor)
            os.fsync(parent_descriptor)
        finally:
            os.close(candidate_descriptor)
    return topology_root / name


def _external_artifact_manifest(
    receipt: dict[str, object], receipt_raw: bytes,
    governance_raw: bytes | None,
) -> dict[str, object]:
    expected = tuple(str(item) for item in receipt["expected_node_ids"])
    manifest: dict[str, object] = {
        "schema_version": EXTERNAL_ARTIFACT_MANIFEST_SCHEMA,
        "capability_or_authority_code": receipt["capability_or_authority_code"],
        "foundation_run_id": receipt["foundation_run_id"],
        "foundation_head_sha": receipt["foundation_head_sha"],
        "foundation_validation_date": receipt["foundation_validation_date"],
        "foundation_context_sha256": receipt["foundation_context_sha256"],
        "inventory_sha256": receipt["inventory_sha256"],
        "receipt_filename": NATIVE_BUNDLE_RECEIPT,
        "receipt_bytes_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "receipt_self_sha256": receipt["receipt_sha256"],
        "governance_filename": NATIVE_BUNDLE_GOVERNANCE if governance_raw is not None else "",
        "governance_present": governance_raw is not None,
        "governance_sha256": (
            hashlib.sha256(governance_raw).hexdigest()
            if governance_raw is not None else EMPTY_SHA256
        ),
        "expected_node_ids": list(expected),
        "expected_node_ids_sha256": _ids_sha256(expected),
        "expected_node_count": len(expected),
        "selected_test_count": receipt["selected_test_count"],
        "preflight_state": receipt["preflight_state"],
        "authority": receipt["authority"],
        "outcome": receipt["outcome"],
        "manifest_sha256": "",
    }
    manifest["manifest_sha256"] = _sha256({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    return manifest


def _stage_external_candidate(
    topology_root: Path, receipt: dict[str, object], governance_raw: bytes | None,
) -> Path:
    """Fsync one private inert external candidate without a cleanup path."""
    _prepare_private_evidence_directory(topology_root)
    code = str(receipt["capability_or_authority_code"])
    name = f".external-candidate-{code}-{secrets.token_hex(16)}"
    receipt_raw = canonical_json_bytes(receipt)
    manifest_raw = canonical_json_bytes(
        _external_artifact_manifest(receipt, receipt_raw, governance_raw),
    )
    with _retained_private_directory(
        topology_root, label="external topology directory",
    ) as (parent_descriptor, _):
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            candidate_descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise TopologyError("external candidate staging failed") from exc
        try:
            candidate = os.fstat(candidate_descriptor)
            if (
                not stat.S_ISDIR(candidate.st_mode)
                or candidate.st_uid != os.geteuid()
                or candidate.st_gid != os.getegid()
                or stat.S_IMODE(candidate.st_mode) != 0o700
            ):
                raise TopologyError("external candidate directory is unsafe")
            _write_private_leaf(candidate_descriptor, NATIVE_BUNDLE_RECEIPT, receipt_raw)
            if governance_raw is not None:
                _write_private_leaf(
                    candidate_descriptor, NATIVE_BUNDLE_GOVERNANCE, governance_raw,
                )
            _write_private_leaf(candidate_descriptor, NATIVE_BUNDLE_MANIFEST, manifest_raw)
            os.fsync(candidate_descriptor)
            os.fsync(parent_descriptor)
        finally:
            os.close(candidate_descriptor)
    return topology_root / name


def _publish_external_candidate_bundle(candidate: Path, destination: Path) -> None:
    _publish_native_candidate_bundle(candidate, destination)


def _publish_external_acceptance_marker(path: Path, content: bytes) -> None:
    _publish_native_acceptance_marker(path, content)


def _publish_native_candidate_bundle(candidate: Path, destination: Path) -> None:
    if candidate.parent != destination.parent:
        raise TopologyError("native candidate and bundle roots differ")
    with _retained_private_directory(
        candidate.parent, label="native topology directory",
    ) as (parent_descriptor, _):
        candidate_descriptor = -1
        try:
            candidate_descriptor = os.open(
                candidate.name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            candidate_identity = _stable_object_identity(os.fstat(candidate_descriptor))
            try:
                _renameat2_noreplace(
                    parent_descriptor, candidate.name,
                    parent_descriptor, destination.name,
                )
            except OSError as exc:
                try:
                    destination_info = os.stat(
                        destination.name, dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    destination_info = None
                try:
                    os.stat(
                        candidate.name, dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    source_exists = False
                else:
                    source_exists = True
                if (
                    destination_info is not None
                    and not source_exists
                    and _stable_object_identity(destination_info) == candidate_identity
                ):
                        pass
                else:
                    raise TopologyError(
                        "native bundle publication failed without an exact resolved rename",
                    ) from exc
            os.fsync(parent_descriptor)
            destination_info = os.stat(
                destination.name, dir_fd=parent_descriptor, follow_symlinks=False,
            )
            if _stable_object_identity(destination_info) != candidate_identity:
                raise TopologyError("native bundle destination identity drifted")
        except TopologyError:
            raise
        except OSError as exc:
            raise TopologyError("native bundle publication failed") from exc
        finally:
            if candidate_descriptor >= 0:
                os.close(candidate_descriptor)


def _publish_native_acceptance_marker(path: Path, content: bytes) -> None:
    """Publish the sole acceptance point atomically and without replacement."""
    _prepare_private_evidence_directory(path.parent)
    staging_name = f".native-marker-{path.stem}-{secrets.token_hex(16)}"
    with _retained_private_directory(
        path.parent, label="native topology directory",
    ) as (parent_descriptor, _):
        _write_private_leaf(parent_descriptor, staging_name, content)
        _renameat2_noreplace(
            parent_descriptor, staging_name, parent_descriptor, path.name,
        )
        os.fsync(parent_descriptor)


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


def _publish_append_only_native_failure_diagnostic(
    path: Path, content: bytes,
) -> dict[str, object]:
    """Install one inert native diagnostic leaf without staging cleanup."""
    _prepare_private_evidence_directory(path.parent)
    with _retained_private_directory(
        path.parent, label="native execution directory",
    ) as (directory_descriptor, _):
        _write_private_leaf(directory_descriptor, path.name, content)
        os.fsync(directory_descriptor)
    raw = _read_private_regular_file(
        path, label="inert native failure diagnostic",
    )
    document = parse_failure_diagnostic(raw)
    if raw != content:
        raise TopologyError("native failure diagnostic post-write reread failed")
    return document


def _reject_failure_diagnostic_coexistence(topology_root: Path) -> None:
    """A failure-only record cannot be installed beside any accepting topology evidence."""
    accepted = [topology_root / "portable-root-remainder.governance.json"]
    for code in CODE_CLASSIFICATION:
        accepted.extend((
            topology_root / f"{code}.json",
            topology_root / f"{code}.governance.json",
            topology_root / f"{code}.artifacts",
        ))
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
    if len(governed) != 115 or _ids_sha256(governed) != LOCKED_GOVERNED_NODE_IDS_SHA256:
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
    """Execute all 49 closed nodes exactly once and publish one no-clobber proof."""
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
    retain_provisional: bool = False,
    append_only_native_diagnostic: bool = False,
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
            if append_only_native_diagnostic:
                if _publish_append_only_native_failure_diagnostic(
                    diagnostic, encoded,
                ) != payload:
                    raise TopologyError("failure diagnostic post-write reread failed")
            else:
                _publish_failure_diagnostic(diagnostic, encoded)
                if (
                    diagnostic.read_bytes() != encoded
                    or parse_failure_diagnostic(diagnostic.read_bytes()) != payload
                ):
                    raise TopologyError("failure diagnostic post-write reread failed")
            raise TopologyError("EXACT_EXECUTION_NONPASS")
        _validate_exact_governance_record(provisional, nodes, sealed_custody)
        _publish_no_clobber(report, provisional.read_bytes())
        return _validate_exact_governance_record(report, nodes, sealed_custody)
    finally:
        if not retain_provisional:
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
        conflicts.extend((
            topology_root / f"{code}.json",
            topology_root / f"{code}.governance.json",
            topology_root / f"{code}.artifacts",
        ))
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


def native_completeness_sha256(receipt: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in receipt.items()
        if key not in {"completeness_sha256", "receipt_sha256"}
    })


def external_completeness_sha256(receipt: dict[str, object]) -> str:
    return _sha256({
        key: value for key, value in receipt.items()
        if key not in {"completeness_sha256", "receipt_sha256"}
    })


def _parse_v1_receipt(value: dict[str, object], raw: bytes) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != RECEIPT_KEYS:
        raise TopologyError("receipt has invalid schema keys")
    if value.get("schema_version") != RECEIPT_SCHEMA:
        raise TopologyError("receipt has invalid schema version")
    _validate_receipt_shape(value)
    if value.get("completeness_sha256") != completeness_sha256(value):
        raise TopologyError("receipt completeness hash mismatch")
    if value.get("receipt_sha256") != payload_sha256(value):
        raise TopologyError("receipt self-hash mismatch")
    return value


def _validate_native_multi_authority(code: str, authority: object) -> None:
    if not isinstance(authority, dict):
        raise TopologyError("native multi-authority facts are invalid")
    toolchains = (
        authority
        if code == "NATIVE-NAUTILUS-SEALED-TOOLCHAINS"
        else authority.get("toolchains")
    )
    if (
        code == "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX"
        and set(authority) != NAUTILUS_COMPOSITE_AUTHORITY_KEYS
    ):
        raise TopologyError("native composite authority facts are invalid")
    if not isinstance(toolchains, dict) or set(toolchains) != NAUTILUS_TOOLCHAIN_AUTHORITY_KEYS:
        raise TopologyError("native toolchain authority facts are invalid")
    for field in (
        "rust_policy_sha256", "llvm_policy_sha256", "rust_manifest_sha256",
        "rust_tree_sha256", "llvm_manifest_sha256",
    ):
        if not isinstance(toolchains[field], str) or not HEX64.fullmatch(toolchains[field]):
            raise TopologyError("native toolchain authority digest is invalid")
    for field in ("rust_file_count", "llvm_tool_count", "llvm_resource_header_count"):
        if (
            not isinstance(toolchains[field], int)
            or isinstance(toolchains[field], bool) or toolchains[field] < 0
        ):
            raise TopologyError("native toolchain authority count is invalid")
    if code == "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX":
        sandbox = authority.get("sandbox")
        if not isinstance(sandbox, dict) or set(sandbox) != NAUTILUS_SANDBOX_AUTHORITY_KEYS:
            raise TopologyError("native sandbox authority facts are invalid")
        for field in ("policy_sha256", "expected_sha256", "observed_sha256"):
            if not isinstance(sandbox[field], str) or not HEX64.fullmatch(sandbox[field]):
                raise TopologyError("native sandbox authority digest is invalid")
        for field in (
            "expected_uid", "observed_uid", "expected_gid", "observed_gid",
            "expected_mode", "observed_mode",
        ):
            if not isinstance(sandbox[field], int) or isinstance(sandbox[field], bool):
                raise TopologyError("native sandbox authority identity is invalid")


def _nautilus_toolchain_authority_is_valid(
    toolchains: dict[str, object],
) -> bool:
    return not (
        toolchains["authority_kind"] != "NAUTILUS_SEALED_TOOLCHAINS_V1"
        or toolchains["rust_root_status"]
        != "PRIVATE_CURRENT_USER_SEALED_DIRECTORY"
        or toolchains["llvm_root_status"]
        != "PRIVATE_CURRENT_USER_SEALED_DIRECTORY"
        or toolchains["rust_policy_sha256"]
        != "bdd7a635f936a46414947e9ffcbb12bd3cf549326adda0ace184f93f0cfbafbe"
        or toolchains["llvm_policy_sha256"]
        != "7ce6888a582343edc823780485f942c7627f60ce9b37e497c7ce03f403e8d56f"
        or toolchains["rust_manifest_sha256"] == EMPTY_SHA256
        or toolchains["rust_tree_sha256"]
        != "29e25dea5701900ead25006933dc230879930f7e74577b8bb0de1f0bce3278e7"
        or toolchains["rust_file_count"] != 149
        or toolchains["llvm_manifest_sha256"] == EMPTY_SHA256
        or toolchains["llvm_tool_count"] != 3
        or toolchains["llvm_resource_header_count"] != 305
    )


def _nautilus_sandbox_authority_is_valid(
    sandbox: dict[str, object],
) -> bool:
    return (
        sandbox["regular_file_status"]
        == "ROOT_OWNED_POLICY_BOUND_EXECUTABLE"
        and sandbox["policy_sha256"]
        == "02366c24787531e112fe7ffe342065b499b07e586badc381e72f731e1467304e"
        and sandbox["expected_sha256"]
        == "52231e1caf55bcbc667b269f49c63599a6f7db4767ae6a039580d0ff853db712"
        and sandbox["observed_sha256"] == sandbox["expected_sha256"]
        and sandbox["expected_uid"] == sandbox["observed_uid"] == 0
        and sandbox["expected_gid"] == sandbox["observed_gid"] == 0
        and sandbox["expected_mode"] == sandbox["observed_mode"] == 0o755
    )


def _native_multi_authority_is_valid(
    code: str, authority: dict[str, object],
) -> bool:
    toolchains = (
        authority
        if code == "NATIVE-NAUTILUS-SEALED-TOOLCHAINS"
        else authority["toolchains"]
    )
    assert isinstance(toolchains, dict)
    if not _nautilus_toolchain_authority_is_valid(toolchains):
        return False
    if code == "NATIVE-NAUTILUS-SEALED-TOOLCHAINS":
        return True
    sandbox = authority["sandbox"]
    assert isinstance(sandbox, dict)
    return (
        authority["authority_kind"] == "NAUTILUS_SEALED_BUILD_SANDBOX_V1"
        and _nautilus_sandbox_authority_is_valid(sandbox)
    )


def _native_multi_authority_can_defer(
    code: str, authority: dict[str, object],
) -> bool:
    if code == "NATIVE-NAUTILUS-SEALED-TOOLCHAINS":
        return authority == _absent_nautilus_toolchain_authority()
    toolchains = authority["toolchains"]
    sandbox = authority["sandbox"]
    assert isinstance(toolchains, dict)
    assert isinstance(sandbox, dict)
    toolchains_absent = toolchains == _absent_nautilus_toolchain_authority()
    sandbox_absent = sandbox in (
        _absent_nautilus_sandbox_authority(),
        {
            "regular_file_status": "ABSENT",
            "policy_sha256": (
                "02366c24787531e112fe7ffe342065b499b07e586badc381e72f731e1467304e"
            ),
            "expected_sha256": (
                "52231e1caf55bcbc667b269f49c63599a6f7db4767ae6a039580d0ff853db712"
            ),
            "observed_sha256": EMPTY_SHA256,
            "expected_uid": 0,
            "observed_uid": -1,
            "expected_gid": 0,
            "observed_gid": -1,
            "expected_mode": 0o755,
            "observed_mode": -1,
        },
    )
    return (
        authority["authority_kind"] == "NAUTILUS_SEALED_BUILD_SANDBOX_V1"
        and (
            (
                toolchains_absent
                and (
                    sandbox_absent
                    or _nautilus_sandbox_authority_is_valid(sandbox)
                )
            )
            or (
                _nautilus_toolchain_authority_is_valid(toolchains)
                and sandbox_absent
            )
        )
    )


def _parse_native_receipt(value: dict[str, object], raw: bytes) -> dict[str, object]:
    schema = value.get("schema_version")
    multi = schema == NATIVE_MULTI_RECEIPT_SCHEMA
    expected_keys = NATIVE_MULTI_RECEIPT_KEYS if multi else NATIVE_RECEIPT_KEYS
    if set(value) != expected_keys:
        raise TopologyError("native receipt has invalid schema keys")
    if schema not in {NATIVE_RECEIPT_SCHEMA, NATIVE_MULTI_RECEIPT_SCHEMA}:
        raise TopologyError("native receipt has invalid schema version")
    for field, pattern, label in (
        ("foundation_run_id", RUN_ID, "foundation run"),
        ("foundation_head_sha", HEAD_SHA, "head"),
        ("foundation_validation_date", FOUNDATION_DATE, "Foundation date"),
        ("foundation_context_sha256", HEX64, "Foundation context hash"),
        ("inventory_sha256", HEX64, "inventory hash"),
        ("completeness_sha256", HEX64, "completeness hash"),
        ("receipt_sha256", HEX64, "self-hash"),
    ):
        item = value[field]
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise TopologyError(f"native receipt has invalid {label}")
    parse_foundation_validation_date(value["foundation_validation_date"])
    for field in (
        "lane", "capability_or_authority_code", "preflight_state",
        "redacted_fact_class", "outcome",
    ):
        item = value[field]
        if not isinstance(item, str) or not ASCII.fullmatch(item):
            raise TopologyError(f"native receipt has invalid {field}")
    if value["lane"] != "native-capabilities":
        raise TopologyError("native receipt has invalid lane")
    code = str(value["capability_or_authority_code"])
    expected_codes = (
        NATIVE_MULTI_CODES if multi else set(NATIVE_COMMAND_IDS) - NATIVE_MULTI_CODES
    )
    if code not in expected_codes:
        raise TopologyError("native receipt has invalid capability code")
    if value["redacted_fact_class"] not in NATIVE_FACT_CLASSES:
        raise TopologyError("native receipt has unredacted fact class")
    if value["outcome"] not in {"PASS", "DEFERRED", "FAIL"}:
        raise TopologyError("native receipt has invalid outcome")
    for field in ("expected_node_ids", "collected_node_ids"):
        items = value[field]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not ASCII.fullmatch(item) for item in items)
            or items != sorted(set(items))
        ):
            raise TopologyError(f"native receipt has invalid {field}")
    for field in ("selected_test_count", "passed", "failed", "unavailable"):
        item = value[field]
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise TopologyError(f"native receipt has invalid {field}")
    probe = value["probe"]
    probe_keys = NATIVE_MULTI_PROBE_KEYS if multi else NATIVE_PROBE_KEYS
    if not isinstance(probe, dict) or set(probe) != probe_keys:
        raise TopologyError("native receipt has invalid probe")
    if (
        not isinstance(probe["command_id"], str)
        or not ASCII.fullmatch(probe["command_id"])
        or not isinstance(probe["exit_code"], int)
        or isinstance(probe["exit_code"], bool)
    ):
        raise TopologyError("native receipt has invalid probe command result")
    digest_fields = ["stdout_sha256", "stderr_sha256"]
    if not multi:
        digest_fields.append("executable_sha256")
    for field in digest_fields:
        item = probe[field]
        if not isinstance(item, str) or not HEX64.fullmatch(item):
            raise TopologyError(f"native receipt has invalid probe {field}")
    if multi:
        _validate_native_multi_authority(code, value["authority"])
    if value["completeness_sha256"] != native_completeness_sha256(value):
        raise TopologyError("native receipt completeness hash mismatch")
    if value["receipt_sha256"] != payload_sha256(value):
        raise TopologyError("native receipt self-hash mismatch")
    return value


def _parse_external_receipt(value: dict[str, object], raw: bytes) -> dict[str, object]:
    del raw
    if set(value) != EXTERNAL_RECEIPT_KEYS:
        raise TopologyError("external receipt has invalid schema keys")
    if value.get("schema_version") != EXTERNAL_RECEIPT_SCHEMA:
        raise TopologyError("external receipt has invalid schema version")
    for field, pattern, label in (
        ("foundation_run_id", RUN_ID, "foundation run"),
        ("foundation_head_sha", HEAD_SHA, "head"),
        ("foundation_validation_date", FOUNDATION_DATE, "Foundation date"),
        ("foundation_context_sha256", HEX64, "Foundation context hash"),
        ("inventory_sha256", HEX64, "inventory hash"),
        ("completeness_sha256", HEX64, "completeness hash"),
        ("receipt_sha256", HEX64, "self-hash"),
    ):
        item = value[field]
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise TopologyError(f"external receipt has invalid {label}")
    parse_foundation_validation_date(value["foundation_validation_date"])
    for field in (
        "lane", "capability_or_authority_code", "preflight_state",
        "redacted_fact_class", "outcome",
    ):
        item = value[field]
        if not isinstance(item, str) or not ASCII.fullmatch(item):
            raise TopologyError(f"external receipt has invalid {field}")
    if value["lane"] != "external-authorities":
        raise TopologyError("external receipt has invalid lane")
    if value["capability_or_authority_code"] not in {
        "EXT-PHASE3B-CORPUS", "EXT-LEGACY-UV-AUTHORITY",
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS",
    }:
        raise TopologyError("external receipt has invalid authority code")
    if value["redacted_fact_class"] not in EXTERNAL_FACT_CLASSES:
        raise TopologyError("external receipt has unredacted fact class")
    if value["outcome"] not in {"PASS", "DEFERRED", "FAIL"}:
        raise TopologyError("external receipt has invalid outcome")
    for field in ("expected_node_ids", "collected_node_ids"):
        items = value[field]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not ASCII.fullmatch(item) for item in items)
            or items != sorted(set(items))
        ):
            raise TopologyError(f"external receipt has invalid {field}")
    for field in ("selected_test_count", "passed", "failed", "unavailable"):
        item = value[field]
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise TopologyError(f"external receipt has invalid {field}")
    authority = value["authority"]
    code = str(value["capability_or_authority_code"])
    expected_keys = {
        "EXT-PHASE3B-CORPUS": PHASE3B_AUTHORITY_KEYS,
        "EXT-LEGACY-UV-AUTHORITY": LEGACY_UV_AUTHORITY_KEYS,
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": NAUTILUS_RUNTIME_AUTHORITY_KEYS,
    }[code]
    if not isinstance(authority, dict) or set(authority) != expected_keys:
        raise TopologyError("external receipt has invalid authority facts")
    for key, item in authority.items():
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise TopologyError(f"external receipt has invalid authority field {key}")
        if isinstance(item, str) and item and any(ord(character) < 0x20 for character in item):
            raise TopologyError(f"external receipt has invalid authority field {key}")
        if isinstance(item, str) and item.startswith("/"):
            raise TopologyError("external receipt authority facts expose an absolute path")
    digest_fields = {
        "EXT-PHASE3B-CORPUS": (
            "expected_inventory_sha256", "observed_inventory_sha256",
            "required_entry_manifest_sha256",
        ),
        "EXT-LEGACY-UV-AUTHORITY": (
            "expected_uv_sha256", "observed_uv_sha256",
            "legacy_closure_manifest_sha256", "sync_stdout_sha256",
            "sync_stderr_sha256",
        ),
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": (
            "runtime_policy_sha256", "base_manifest_sha256",
            "base_file_inventory_sha256", "artifact_manifest_sha256",
            "artifact_wheel_sha256",
        ),
    }[code]
    for field in digest_fields:
        if not isinstance(authority[field], str) or not HEX64.fullmatch(authority[field]):
            raise TopologyError(f"external receipt has invalid authority digest {field}")
    if value["completeness_sha256"] != external_completeness_sha256(value):
        raise TopologyError("external receipt completeness hash mismatch")
    if value["receipt_sha256"] != payload_sha256(value):
        raise TopologyError("external receipt self-hash mismatch")
    return value


def parse_receipt(raw: bytes) -> dict[str, object]:
    value = _strict_json(raw, label="receipt")
    if not isinstance(value, dict):
        raise TopologyError("receipt has invalid schema keys")
    if canonical_json_bytes(value) != raw:
        raise TopologyError("receipt is not canonical")
    if value.get("schema_version") in {
        NATIVE_RECEIPT_SCHEMA, NATIVE_MULTI_RECEIPT_SCHEMA,
    }:
        return _parse_native_receipt(value, raw)
    if value.get("schema_version") == EXTERNAL_RECEIPT_SCHEMA:
        return _parse_external_receipt(value, raw)
    return _parse_v1_receipt(value, raw)


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
    foundation_context: dict[str, object] | None = None,
) -> dict[str, object]:
    receipt = parse_receipt(raw)
    if receipt["foundation_run_id"] != foundation_run_id or receipt["foundation_head_sha"] != foundation_head_sha:
        raise TopologyError("receipt is stale for this Foundation run/head")
    if receipt["inventory_sha256"] != LOCKED_INVENTORY_SHA256:
        raise TopologyError("receipt inventory binding drift")
    lane, expected = _expected_rows(rows, str(receipt["capability_or_authority_code"]))
    if receipt["lane"] != lane or tuple(receipt["expected_node_ids"]) != expected:
        raise TopologyError("receipt lane/code/node mapping drift")
    if lane == "native-capabilities":
        expected_native_schema = _native_schema_for_code(
            receipt["capability_or_authority_code"],
        )
        if receipt["schema_version"] == RECEIPT_SCHEMA:
            raise TopologyError("native v1 receipt is stale")
        if receipt["schema_version"] != expected_native_schema:
            raise TopologyError("native receipt schema is stale for its code")
        if foundation_context is None:
            raise TopologyError("native receipt requires sealed Foundation context")
        if (
            receipt["foundation_validation_date"] != foundation_context.get("foundation_validation_date")
            or receipt["foundation_context_sha256"] != foundation_context.get("foundation_context_sha256")
            or foundation_context.get("foundation_run_id") != foundation_run_id
            or foundation_context.get("foundation_head_sha") != foundation_head_sha
        ):
            raise TopologyError("native receipt Foundation context binding drift")
        _validate_native_outcome(receipt, expected)
        return receipt
    if lane == "external-authorities":
        if receipt["schema_version"] == RECEIPT_SCHEMA:
            raise TopologyError("external v1 receipt is stale")
        if receipt["schema_version"] != EXTERNAL_RECEIPT_SCHEMA:
            raise TopologyError("external receipt has invalid schema version")
        if foundation_context is None:
            raise TopologyError("external receipt requires sealed Foundation context")
        if (
            receipt["foundation_validation_date"]
            != foundation_context.get("foundation_validation_date")
            or receipt["foundation_context_sha256"]
            != foundation_context.get("foundation_context_sha256")
            or foundation_context.get("foundation_run_id") != foundation_run_id
            or foundation_context.get("foundation_head_sha") != foundation_head_sha
        ):
            raise TopologyError("external receipt Foundation context binding drift")
        _validate_external_outcome(receipt, expected)
        return receipt
    if receipt["schema_version"] != RECEIPT_SCHEMA:
        raise TopologyError("receipt has invalid schema version")
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


def _validate_external_outcome(
    receipt: dict[str, object], expected: tuple[str, ...],
) -> None:
    code = str(receipt["capability_or_authority_code"])
    state = str(receipt["preflight_state"])
    fact = str(receipt["redacted_fact_class"])
    outcome = str(receipt["outcome"])
    authority = receipt["authority"]
    assert isinstance(authority, dict)
    selected = int(receipt["selected_test_count"])
    passed = int(receipt["passed"])
    failed = int(receipt["failed"])
    unavailable = int(receipt["unavailable"])
    collected = tuple(receipt["collected_node_ids"])
    expected_count = len(expected)
    if outcome == "DEFERRED":
        expected_fact = {
            "EXT-PHASE3B-CORPUS": "AUTHORITY_ROOT_ABSENT",
            "EXT-LEGACY-UV-AUTHORITY": "AUTHORITY_EXECUTABLE_ABSENT",
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": "AUTHORITY_ROOT_ABSENT",
        }[code]
        expected_authority = {
            "EXT-PHASE3B-CORPUS": _phase3b_absent_authority,
            "EXT-LEGACY-UV-AUTHORITY": _legacy_absent_authority,
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS": (
                _absent_nautilus_external_authority
            ),
        }[code]()
        if (
            (state, fact) != ("ABSENT", expected_fact)
            or authority != expected_authority
            or collected
            or (selected, passed, failed, unavailable) != (0, 0, 0, expected_count)
        ):
            raise TopologyError("external DEFERRED receipt is not exact absence")
        return
    if outcome == "PASS":
        if (
            (state, fact) != ("VALID", "AUTHORITY_COMPLETE_VALIDATED")
            or collected != expected
            or (selected, passed, failed, unavailable)
            != (expected_count, expected_count, 0, 0)
        ):
            raise TopologyError("external PASS receipt lacks exact execution proof")
        if code == "EXT-PHASE3B-CORPUS":
            if (
                authority["authority_kind"] != "PHASE3B_REVIEWED_CORPUS_V1"
                or authority["regular_directory_status"]
                != "PRIVATE_CURRENT_USER_DIRECTORY"
                or authority["expected_inventory_sha256"]
                != PHASE3B_EXPECTED_INVENTORY_SHA256
                or authority["observed_inventory_sha256"]
                != PHASE3B_EXPECTED_INVENTORY_SHA256
                or authority["required_entry_manifest_sha256"] == EMPTY_SHA256
                or authority["required_entry_count"] != len(PHASE3B_REQUIRED_ENTRIES)
                or authority["expected_decision_total"]
                != PHASE3B_EXPECTED_DECISION_TOTAL
                or authority["observed_decision_total"]
                != PHASE3B_EXPECTED_DECISION_TOTAL
                or authority["expected_cost_sessions"]
                != PHASE3B_EXPECTED_COST_SESSIONS
                or authority["observed_cost_sessions"]
                != PHASE3B_EXPECTED_COST_SESSIONS
                or authority["expected_asset_count"]
                != PHASE3B_EXPECTED_ASSET_COUNT
                or authority["observed_asset_count"]
                != PHASE3B_EXPECTED_ASSET_COUNT
                or authority["expected_asset_source_files"]
                != PHASE3B_EXPECTED_ASSET_SOURCE_FILES
                or authority["observed_asset_source_files"]
                != PHASE3B_EXPECTED_ASSET_SOURCE_FILES
            ):
                raise TopologyError("external Phase-3B PASS authority facts drift")
            return
        if code == "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS":
            if (
                authority["authority_kind"]
                != "NAUTILUS_RUNTIME_CLOSURE_INPUTS_V1"
                or authority["base_root_status"]
                != "PRIVATE_CURRENT_USER_SEALED_DIRECTORY"
                or authority["artifact_root_status"]
                != "PRIVATE_CURRENT_USER_SEALED_DIRECTORY"
                or authority["runtime_policy_sha256"]
                != "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2"
                or authority["base_manifest_sha256"] == EMPTY_SHA256
                or authority["base_file_count"] != 88
                or authority["base_file_inventory_sha256"]
                != "894e8048d062877dda374f7968ee04c3e75febfc948f82669f23485ecc7f6126"
                or authority["artifact_manifest_sha256"] == EMPTY_SHA256
                or authority["artifact_wheel_sha256"] == EMPTY_SHA256
                or authority["artifact_wheel_size"] <= 0
            ):
                raise TopologyError("external Nautilus PASS authority facts drift")
            return
        if (
            authority["authority_kind"] != "LEGACY_UV_AND_CLOSURE_V1"
            or authority["regular_file_status"]
            != "PRIVATE_CURRENT_USER_EXECUTABLE"
            or authority["expected_uv_sha256"] != LEGACY_UV_SHA256
            or authority["observed_uv_sha256"] != LEGACY_UV_SHA256
            or authority["expected_uv_version"] != LEGACY_UV_VERSION
            or authority["observed_uv_version"] != LEGACY_UV_VERSION
            or authority["expected_uid"] != os.geteuid()
            or authority["observed_uid"] != os.geteuid()
            or authority["expected_gid"] != os.getegid()
            or authority["observed_gid"] != os.getegid()
            or authority["expected_mode"] != 0o755
            or authority["observed_mode"] != 0o755
            or authority["legacy_closure_manifest_sha256"] == EMPTY_SHA256
            or authority["legacy_closure_entry_count"] != len(LEGACY_CLOSURE_ENTRIES)
            or authority["sync_command_id"] != "LEGACY_UV_SYNC_FROZEN_OFFLINE_V1"
            or authority["sync_exit_code"] != 0
        ):
            raise TopologyError("external legacy UV PASS authority facts drift")
        return
    if (
        outcome != "FAIL"
        or state not in {"PARTIAL", "INVALID", "DRIFTED"}
        or fact not in EXTERNAL_FACT_CLASSES - {
            "AUTHORITY_ROOT_ABSENT", "AUTHORITY_EXECUTABLE_ABSENT",
            "AUTHORITY_COMPLETE_VALIDATED",
        }
        or unavailable != 0
        or passed != 0
        or selected not in {0, expected_count}
        or failed not in {0, expected_count}
        or collected
    ):
        raise TopologyError("external FAIL receipt has invalid state or counts")


def _validate_native_outcome(receipt: dict[str, object], expected: tuple[str, ...]) -> None:
    code = str(receipt["capability_or_authority_code"])
    state = str(receipt["preflight_state"])
    fact = str(receipt["redacted_fact_class"])
    outcome = str(receipt["outcome"])
    probe = receipt["probe"]
    assert isinstance(probe, dict)
    if probe["command_id"] != NATIVE_COMMAND_IDS[code]:
        raise TopologyError("native receipt probe command binding drift")
    selected = int(receipt["selected_test_count"])
    passed = int(receipt["passed"])
    failed = int(receipt["failed"])
    unavailable = int(receipt["unavailable"])
    collected = tuple(receipt["collected_node_ids"])
    expected_count = len(expected)
    multi = receipt["schema_version"] == NATIVE_MULTI_RECEIPT_SCHEMA
    if outcome == "PASS":
        if (
            (state, fact) != ("AVAILABLE", "NATIVE_CAPABILITY_VALIDATED")
            or probe["exit_code"] != 0
            or probe["stdout_sha256"] != EMPTY_SHA256
            or probe["stderr_sha256"] != EMPTY_SHA256
            or (not multi and probe["executable_sha256"] == EMPTY_SHA256)
            or collected != expected
            or (selected, passed, failed, unavailable)
            != (expected_count, expected_count, 0, 0)
            or (
                multi
                and not _native_multi_authority_is_valid(
                    code, receipt["authority"],
                )
            )
        ):
            raise TopologyError("native PASS receipt lacks exact probe or execution proof")
        return
    if outcome == "DEFERRED":
        if (
            state != "UNAVAILABLE"
            or fact not in {"NATIVE_COMPONENT_ABSENT", "RUNNER_POLICY_DISALLOWS_USERNS"}
            or collected
            or (selected, passed, failed, unavailable) != (0, 0, 0, expected_count)
        ):
            raise TopologyError("native DEFERRED receipt has invalid counts or state")
        if fact == "NATIVE_COMPONENT_ABSENT":
            if (
                probe["exit_code"] != NATIVE_PROBE_NOT_EXECUTED
                or probe["stdout_sha256"] != EMPTY_SHA256
                or probe["stderr_sha256"] != EMPTY_SHA256
                or (not multi and probe["executable_sha256"] != EMPTY_SHA256)
            ):
                raise TopologyError("native absent receipt falsely claims probe execution")
            if multi:
                authority = receipt["authority"]
                if not _native_multi_authority_can_defer(code, authority):
                    raise TopologyError("native DEFERRED authority facts are invalid")
            return
        allowed_stderr = {
            hashlib.sha256(value).hexdigest()
            for value in NATIVE_DENIAL_STDERR[
                "NATIVE-BWRAP-OS-SANDBOX" if multi else code
            ]
        }
        if (
            probe["exit_code"] != 1
            or probe["stdout_sha256"] != EMPTY_SHA256
            or probe["stderr_sha256"] not in allowed_stderr
            or (not multi and probe["executable_sha256"] == EMPTY_SHA256)
            or (
                multi
                and (
                    code != "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX"
                    or not _native_multi_authority_is_valid(
                        code, receipt["authority"],
                    )
                )
            )
        ):
            raise TopologyError("native namespace-policy deferral is not exact")
        return
    if (
        outcome != "FAIL"
        or state != "BROKEN"
        or fact not in NATIVE_FACT_CLASSES - {
            "NATIVE_COMPONENT_ABSENT", "NATIVE_CAPABILITY_VALIDATED",
            "RUNNER_POLICY_DISALLOWS_USERNS",
        }
        or unavailable != 0
        or passed != 0
        or failed not in {0, expected_count}
        or selected not in {0, expected_count}
        or collected
    ):
        raise TopologyError("native FAIL receipt has invalid state or counts")


def _reduce_native_artifact_status(
    receipts: list[dict[str, object]], *, require_pass: bool,
) -> str:
    if any(
        receipt.get("capability_or_authority_code") not in NATIVE_COMMAND_IDS
        or receipt.get("schema_version") != _native_schema_for_code(
            receipt.get("capability_or_authority_code"),
        )
        for receipt in receipts
    ):
        raise TopologyError("native artifact set contains a non-native receipt")
    codes = [str(receipt["capability_or_authority_code"]) for receipt in receipts]
    if len(codes) != len(set(codes)) or set(codes) != set(NATIVE_COMMAND_IDS):
        raise TopologyError("native receipt set is missing, duplicate, or unknown")
    if any(receipt["outcome"] == "FAIL" for receipt in receipts):
        raise TopologyError("native receipt set contains FAIL")
    status = "DEFERRED" if any(receipt["outcome"] == "DEFERRED" for receipt in receipts) else "PASS"
    if require_pass and status != "PASS":
        raise TopologyError("native host qualification requires PASS")
    return status


def _validate_native_manifest_bytes(
    raw: bytes, receipt: dict[str, object], receipt_raw: bytes,
    governance_raw: bytes | None,
) -> dict[str, object]:
    manifest = _strict_json(raw, label="native artifact manifest")
    expected_keys = (
        NATIVE_MULTI_ARTIFACT_MANIFEST_KEYS
        if receipt["schema_version"] == NATIVE_MULTI_RECEIPT_SCHEMA
        else NATIVE_ARTIFACT_MANIFEST_KEYS
    )
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise TopologyError("native artifact manifest has invalid schema keys")
    if canonical_json_bytes(manifest) != raw:
        raise TopologyError("native artifact manifest is not canonical")
    expected = _native_artifact_manifest(receipt, receipt_raw, governance_raw)
    if manifest != expected:
        raise TopologyError("native artifact manifest binding drift")
    return manifest


def validate_native_artifact_set(
    receipt_path: Path, *, rows: tuple[InventoryRow, ...],
    foundation_context: dict[str, object], sealed_custody: dict[str, str],
) -> tuple[dict[str, object], tuple[str, ...]]:
    if receipt_path.suffix != ".json":
        raise TopologyError("native receipt path is malformed")
    with _retained_private_native_artifacts(receipt_path) as artifacts:
        if artifacts.marker_raw != artifacts.bundle_receipt_raw:
            raise TopologyError("native marker does not equal bundled receipt bytes")
        receipt = validate_receipt(
            artifacts.bundle_receipt_raw, rows=rows,
            foundation_run_id=str(foundation_context.get("foundation_run_id", "")),
            foundation_head_sha=str(foundation_context.get("foundation_head_sha", "")),
            foundation_context=foundation_context,
        )
        code = str(receipt["capability_or_authority_code"])
        if (
            receipt.get("schema_version") != _native_schema_for_code(code)
            or code not in NATIVE_COMMAND_IDS
            or receipt_path.name != f"{code}.json"
        ):
            raise TopologyError("native receipt filename/code binding drift")
        expected = tuple(receipt["expected_node_ids"])
        if receipt["outcome"] == "PASS":
            if artifacts.governance_raw is None:
                raise TopologyError("native PASS receipt lacks governance artifact")
            records = _validate_exact_governance_bytes(
                artifacts.governance_raw, expected, _validate_custody_policy(sealed_custody),
            )
            executed = tuple(str(record["test_node_id"]) for record in records)
        elif receipt["outcome"] == "DEFERRED":
            if artifacts.governance_raw is not None:
                raise TopologyError("native DEFERRED receipt has governance artifact")
            executed = ()
        else:
            raise TopologyError("native FAIL receipt is never acceptable")
        _validate_native_manifest_bytes(
            artifacts.manifest_raw, receipt, artifacts.bundle_receipt_raw,
            artifacts.governance_raw,
        )
        _postcheck_private_native_artifacts(artifacts)
        return receipt, executed


def validate_native_artifacts(
    topology_root: Path, *, rows: tuple[InventoryRow, ...],
    foundation_context: dict[str, object], sealed_custody: dict[str, str],
    require_pass: bool,
) -> str:
    receipts = [
        validate_native_artifact_set(
            topology_root / f"{code}.json", rows=rows,
            foundation_context=foundation_context, sealed_custody=sealed_custody,
        )[0]
        for code in sorted(NATIVE_COMMAND_IDS)
    ]
    return _reduce_native_artifact_status(receipts, require_pass=require_pass)


def _validate_external_manifest_bytes(
    raw: bytes, receipt: dict[str, object], receipt_raw: bytes,
    governance_raw: bytes | None,
) -> dict[str, object]:
    manifest = _strict_json(raw, label="external artifact manifest")
    if not isinstance(manifest, dict) or set(manifest) != EXTERNAL_ARTIFACT_MANIFEST_KEYS:
        raise TopologyError("external artifact manifest has invalid schema keys")
    if canonical_json_bytes(manifest) != raw:
        raise TopologyError("external artifact manifest is not canonical")
    expected = _external_artifact_manifest(receipt, receipt_raw, governance_raw)
    if manifest != expected:
        raise TopologyError("external artifact manifest binding drift")
    return manifest


def validate_external_artifact_set(
    receipt_path: Path, *, rows: tuple[InventoryRow, ...],
    foundation_context: dict[str, object], sealed_custody: dict[str, str],
) -> tuple[dict[str, object], tuple[str, ...]]:
    if receipt_path.suffix != ".json":
        raise TopologyError("external receipt path is malformed")
    with _retained_private_native_artifacts(receipt_path) as artifacts:
        if artifacts.marker_raw != artifacts.bundle_receipt_raw:
            raise TopologyError("external marker does not equal bundled receipt bytes")
        receipt = validate_receipt(
            artifacts.bundle_receipt_raw, rows=rows,
            foundation_run_id=str(foundation_context.get("foundation_run_id", "")),
            foundation_head_sha=str(foundation_context.get("foundation_head_sha", "")),
            foundation_context=foundation_context,
        )
        code = str(receipt["capability_or_authority_code"])
        if (
            receipt.get("schema_version") != EXTERNAL_RECEIPT_SCHEMA
            or CODE_CLASSIFICATION.get(code) != "EXTERNAL_AUTHORITY_REQUIRED"
            or receipt_path.name != f"{code}.json"
        ):
            raise TopologyError("external receipt filename/code binding drift")
        expected = tuple(receipt["expected_node_ids"])
        if receipt["outcome"] == "PASS":
            if artifacts.governance_raw is None:
                raise TopologyError("external PASS receipt lacks governance artifact")
            records = _validate_exact_governance_bytes(
                artifacts.governance_raw, expected,
                _validate_custody_policy(sealed_custody),
            )
            executed = tuple(str(record["test_node_id"]) for record in records)
        elif receipt["outcome"] == "DEFERRED":
            if artifacts.governance_raw is not None:
                raise TopologyError("external DEFERRED receipt has governance artifact")
            executed = ()
        else:
            raise TopologyError("external FAIL receipt is never acceptable")
        _validate_external_manifest_bytes(
            artifacts.manifest_raw, receipt, artifacts.bundle_receipt_raw,
            artifacts.governance_raw,
        )
        _postcheck_private_native_artifacts(artifacts)
        return receipt, executed


def validate_external_artifacts(
    topology_root: Path, *, rows: tuple[InventoryRow, ...],
    foundation_context: dict[str, object], sealed_custody: dict[str, str],
    require_pass: bool,
) -> str:
    external_codes = sorted(
        code for code, classification in CODE_CLASSIFICATION.items()
        if classification == "EXTERNAL_AUTHORITY_REQUIRED"
    )
    receipts = [
        validate_external_artifact_set(
            topology_root / f"{code}.json", rows=rows,
            foundation_context=foundation_context, sealed_custody=sealed_custody,
        )[0]
        for code in external_codes
    ]
    if any(receipt["outcome"] == "FAIL" for receipt in receipts):
        raise TopologyError("external receipt set contains FAIL")
    status = (
        "DEFERRED"
        if any(receipt["outcome"] == "DEFERRED" for receipt in receipts)
        else "PASS"
    )
    if require_pass and status != "PASS":
        raise TopologyError("external host qualification requires PASS")
    return status


def _canonical_receipt_artifacts(paths: list[Path]) -> list[tuple[Path, str]]:
    if not paths or any(path.parent != paths[0].parent for path in paths):
        raise TopologyError("receipt aggregation has an invalid topology root")
    topology_root = paths[0].parent
    expected_names = {f"{code}.json" for code in CODE_CLASSIFICATION}
    received_names = [path.name for path in paths]
    if len(received_names) != len(expected_names) or set(received_names) != expected_names:
        raise TopologyError("receipt set is not the canonical receipt filename set")
    return [
        (topology_root / f"{code}.json", code)
        for code in sorted(CODE_CLASSIFICATION)
    ]


def _validate_bound_receipt_artifact(
    path: Path, expected_code: str, *, rows: tuple[InventoryRow, ...],
    foundation_run_id: str, foundation_head_sha: str,
    foundation_context: dict[str, object], sealed_custody: dict[str, str],
) -> tuple[dict[str, object], tuple[str, ...]]:
    classification = CODE_CLASSIFICATION[expected_code]
    if classification == "NATIVE_CAPABILITY_REQUIRED":
        receipt, executed = validate_native_artifact_set(
            path, rows=rows, foundation_context=foundation_context,
            sealed_custody=sealed_custody,
        )
    elif classification == "EXTERNAL_AUTHORITY_REQUIRED":
        receipt, executed = validate_external_artifact_set(
            path, rows=rows, foundation_context=foundation_context,
            sealed_custody=sealed_custody,
        )
    else:
        receipt = validate_receipt(
            path.read_bytes(), rows=rows, foundation_run_id=foundation_run_id,
            foundation_head_sha=foundation_head_sha,
            foundation_context=foundation_context,
        )
        executed = ()
    if receipt.get("capability_or_authority_code") != expected_code:
        raise TopologyError("receipt filename/code binding drift")
    return receipt, executed


def aggregate_receipts(
    paths: list[Path], *, rows: tuple[InventoryRow, ...], foundation_run_id: str,
    foundation_head_sha: str, foundation_context: dict[str, object] | None = None,
    closure_proof_path: Path | None = None, sealed_custody: dict[str, str] | None = None,
) -> dict[str, object]:
    artifacts = _canonical_receipt_artifacts(paths)
    topology_root = artifacts[0][0].parent
    _reject_unsafe_raw_reason_nonacceptance_presence(topology_root)
    _reject_closed_source_artifacts(topology_root)
    if foundation_context is None or closure_proof_path is None or sealed_custody is None:
        raise TopologyError("portable closure proof is required for aggregation")
    validate_portable_closure_proof(
        closure_proof_path, foundation_run_id=foundation_run_id,
        foundation_head_sha=foundation_head_sha, foundation_context=foundation_context,
        sealed_custody=sealed_custody,
    )
    expected_codes = set(CODE_CLASSIFICATION)
    receipts: list[dict[str, object]] = []
    try:
        for path, expected_code in artifacts:
            receipt, _ = _validate_bound_receipt_artifact(
                path, expected_code, rows=rows,
                foundation_run_id=foundation_run_id,
                foundation_head_sha=foundation_head_sha,
                foundation_context=foundation_context, sealed_custody=sealed_custody,
            )
            receipts.append(receipt)
    except OSError as exc:
        raise TopologyError("receipt set is missing or unreadable") from exc
    codes = [str(receipt["capability_or_authority_code"]) for receipt in receipts]
    if len(codes) != len(set(codes)) or set(codes) != expected_codes:
        raise TopologyError("receipt set is missing, duplicate, or unknown")
    statuses = {"portable-source": "PASS", "native-capabilities": "PASS", "external-authorities": "PASS"}
    for receipt in receipts:
        lane = str(receipt["lane"])
        if receipt["outcome"] == "FAIL":
            raise TopologyError("receipt set contains FAIL")
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
    for path, expected_code in _canonical_receipt_artifacts(receipt_paths):
        receipt, artifact_executed = _validate_bound_receipt_artifact(
            path, expected_code, rows=rows, foundation_run_id=run_id,
            foundation_head_sha=head_sha, foundation_context=context,
            sealed_custody=sealed_custody,
        )
        expected = tuple(receipt["expected_node_ids"])
        code = str(receipt["capability_or_authority_code"])
        governance = topology_root / f"{code}.governance.json"
        if receipt["outcome"] == "PASS":
            if CODE_CLASSIFICATION[expected_code] not in {
                "NATIVE_CAPABILITY_REQUIRED", "EXTERNAL_AUTHORITY_REQUIRED",
            }:
                raise TopologyError("receipt classification lacks an artifact execution boundary")
            accounted.extend(artifact_executed)
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


def build_final_semantic_projection(
    foundation_context: dict[str, object],
    baseline: dict[str, object],
    disclosure: dict[str, object],
    receipts: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Project validated topology meaning without per-attempt custody hashes."""
    head_sha = foundation_context.get("foundation_head_sha")
    validation_date = foundation_context.get("foundation_validation_date")
    inventory_sha256 = baseline.get("inventory_sha256")
    closure_sha256 = baseline.get("closure_sha256")
    collector_policy = baseline.get("collector_policy")
    if (
        not isinstance(head_sha, str) or not HEAD_SHA.fullmatch(head_sha)
        or not isinstance(validation_date, str)
        or not FOUNDATION_DATE.fullmatch(validation_date)
        or not isinstance(inventory_sha256, str) or not HEX64.fullmatch(inventory_sha256)
        or not isinstance(closure_sha256, str) or not HEX64.fullmatch(closure_sha256)
        or not isinstance(collector_policy, dict)
    ):
        raise TopologyError("final topology semantic inputs are malformed")
    status_keys = (
        "portable_source_status", "native_capabilities_status",
        "external_authorities_status", "portable_root_remainder_status",
        "runtime_proof", "baseline_candidate_count",
    )
    if any(key not in disclosure for key in status_keys):
        raise TopologyError("final topology disclosure is incomplete")
    receipt_results: list[dict[str, object]] = []
    for receipt in sorted(
        receipts, key=lambda item: str(item.get("capability_or_authority_code", "")),
    ):
        code = receipt.get("capability_or_authority_code")
        outcome = receipt.get("outcome")
        counts = {
            key: receipt.get(key)
            for key in ("selected_test_count", "passed", "failed", "unavailable")
        }
        if (
            not isinstance(code, str) or code not in CODE_CLASSIFICATION
            or outcome not in {"PASS", "DEFERRED"}
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values())
        ):
            raise TopologyError("final topology receipt meaning is malformed")
        receipt_results.append({
            "code": code,
            "outcome": outcome,
            "selected": counts["selected_test_count"],
            "passed": counts["passed"],
            "failed": counts["failed"],
            "unavailable": counts["unavailable"],
        })
    return {
        "foundation": {
            "head_sha": head_sha,
            "validation_date": validation_date,
        },
        "inventory_sha256": inventory_sha256,
        "closure_sha256": closure_sha256,
        "policy_sha256": _sha256(collector_policy),
        "statuses": {key: disclosure[key] for key in status_keys},
        "receipt_results": receipt_results,
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


def make_native_receipt(
    *, context: dict[str, object], code: str, expected: tuple[str, ...],
    collected: tuple[str, ...],
    session: NativeProbeSession | NativeMultiAuthoritySession, outcome: str,
    selected_test_count: int, passed: int, failed: int, unavailable: int,
    fact: str | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": (
            NATIVE_MULTI_RECEIPT_SCHEMA
            if isinstance(session, NativeMultiAuthoritySession)
            else NATIVE_RECEIPT_SCHEMA
        ),
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "foundation_validation_date": context["foundation_validation_date"],
        "foundation_context_sha256": context["foundation_context_sha256"],
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "lane": "native-capabilities",
        "capability_or_authority_code": code,
        "expected_node_ids": list(expected),
        "collected_node_ids": list(collected),
        "preflight_state": "BROKEN" if outcome == "FAIL" else session.state,
        "redacted_fact_class": session.fact if fact is None else fact,
        "probe": dict(session.probe),
        "selected_test_count": selected_test_count,
        "passed": passed,
        "failed": failed,
        "unavailable": unavailable,
        "completeness_sha256": "",
        "outcome": outcome,
        "receipt_sha256": "",
    }
    if isinstance(session, NativeMultiAuthoritySession):
        receipt["authority"] = dict(session.authority)
    receipt["completeness_sha256"] = native_completeness_sha256(receipt)
    receipt["receipt_sha256"] = payload_sha256(receipt)
    return receipt


def make_external_receipt(
    *, context: dict[str, object], code: str, expected: tuple[str, ...],
    collected: tuple[str, ...], session: ExternalAuthoritySession, outcome: str,
    selected_test_count: int, passed: int, failed: int, unavailable: int,
    fact: str | None = None, state: str | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": EXTERNAL_RECEIPT_SCHEMA,
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "foundation_validation_date": context["foundation_validation_date"],
        "foundation_context_sha256": context["foundation_context_sha256"],
        "inventory_sha256": LOCKED_INVENTORY_SHA256,
        "lane": "external-authorities",
        "capability_or_authority_code": code,
        "expected_node_ids": list(expected),
        "collected_node_ids": list(collected),
        "preflight_state": session.state if state is None else state,
        "redacted_fact_class": session.fact if fact is None else fact,
        "authority": dict(session.authority),
        "selected_test_count": selected_test_count,
        "passed": passed,
        "failed": failed,
        "unavailable": unavailable,
        "completeness_sha256": "",
        "outcome": outcome,
        "receipt_sha256": "",
    }
    receipt["completeness_sha256"] = external_completeness_sha256(receipt)
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
    _publish_no_clobber(destination, canonical_json_bytes(receipt))
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


@dataclass(frozen=True)
class NativeProbeSession:
    code: str
    state: str
    fact: str
    probe: dict[str, object]
    descriptor: int
    executable_path: Path | None
    named_identity: tuple[int, ...] | None
    descriptor_identity: tuple[int, ...] | None
    policy: dict[str, object] | None


@dataclass(frozen=True)
class NativeMultiAuthoritySession:
    code: str
    state: str
    fact: str
    probe: dict[str, object]
    authority: dict[str, object]
    descriptors: tuple[int, ...]
    postcheck: Callable[[], None]


def _native_multi_probe_record(
    code: str, *, exit_code: int, stdout: bytes = b"", stderr: bytes = b"",
) -> dict[str, object]:
    return {
        "command_id": NATIVE_COMMAND_IDS[code],
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }


def _absent_nautilus_toolchain_authority() -> dict[str, object]:
    return {
        "authority_kind": "NAUTILUS_SEALED_TOOLCHAINS_V1",
        "rust_root_status": "ABSENT",
        "llvm_root_status": "ABSENT",
        "rust_policy_sha256": EMPTY_SHA256,
        "llvm_policy_sha256": EMPTY_SHA256,
        "rust_manifest_sha256": EMPTY_SHA256,
        "rust_tree_sha256": EMPTY_SHA256,
        "rust_file_count": 0,
        "llvm_manifest_sha256": EMPTY_SHA256,
        "llvm_tool_count": 0,
        "llvm_resource_header_count": 0,
    }


def _invalid_nautilus_toolchain_authority() -> dict[str, object]:
    authority = _absent_nautilus_toolchain_authority()
    authority["rust_root_status"] = "INVALID"
    authority["llvm_root_status"] = "INVALID"
    return authority


def _absent_nautilus_sandbox_authority() -> dict[str, object]:
    return {
        "regular_file_status": "ABSENT",
        "policy_sha256": EMPTY_SHA256,
        "expected_sha256": EMPTY_SHA256,
        "observed_sha256": EMPTY_SHA256,
        "expected_uid": -1,
        "observed_uid": -1,
        "expected_gid": -1,
        "observed_gid": -1,
        "expected_mode": -1,
        "observed_mode": -1,
    }


def _nautilus_multi_authority(
    code: str, *, invalid: bool = False,
) -> dict[str, object]:
    toolchains = (
        _invalid_nautilus_toolchain_authority()
        if invalid else _absent_nautilus_toolchain_authority()
    )
    if code == "NATIVE-NAUTILUS-SEALED-TOOLCHAINS":
        return toolchains
    return {
        "authority_kind": "NAUTILUS_SEALED_BUILD_SANDBOX_V1",
        "toolchains": toolchains,
        "sandbox": _absent_nautilus_sandbox_authority(),
    }


def _path_absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _qualify_nautilus_toolchains(
    rust_toolchain: Path, llvm_toolchain: Path,
) -> dict[str, object]:
    from scripts import prepare_nautilus_llvm_toolchain as llvm
    from scripts import prepare_nautilus_toolchain as rust

    rust_policy_sha256 = _sha256_path(NAUTILUS_RUST_POLICY)
    llvm_policy_sha256 = _sha256_path(NAUTILUS_LLVM_POLICY)
    if (
        rust_policy_sha256
        != "bdd7a635f936a46414947e9ffcbb12bd3cf549326adda0ace184f93f0cfbafbe"
        or llvm_policy_sha256
        != "7ce6888a582343edc823780485f942c7627f60ce9b37e497c7ce03f403e8d56f"
    ):
        raise TopologyError("Nautilus toolchain policy identity drift")
    rust_policy = rust.load_manifest(NAUTILUS_RUST_POLICY)
    llvm_policy = llvm.load_policy(NAUTILUS_LLVM_POLICY)
    rust.verify_materialized_toolchain(rust_toolchain, rust_policy)
    llvm.verify_materialized(llvm_toolchain, llvm_policy)
    rust_materialized = rust_policy["materialized_toolchain"]
    llvm_tools = llvm_policy["tools"]
    llvm_resources = llvm_policy["resource_headers"]
    assert isinstance(rust_materialized, dict)
    assert isinstance(llvm_tools, dict)
    assert isinstance(llvm_resources, dict)
    resource_files = llvm_resources["files"]
    assert isinstance(resource_files, dict)
    return {
        "authority_kind": "NAUTILUS_SEALED_TOOLCHAINS_V1",
        "rust_root_status": "PRIVATE_CURRENT_USER_SEALED_DIRECTORY",
        "llvm_root_status": "PRIVATE_CURRENT_USER_SEALED_DIRECTORY",
        "rust_policy_sha256": rust_policy_sha256,
        "llvm_policy_sha256": llvm_policy_sha256,
        "rust_manifest_sha256": _sha256_path(
            rust_toolchain / "materialized-toolchain-manifest.json",
        ),
        "rust_tree_sha256": rust_materialized["tree_sha256"],
        "rust_file_count": rust_materialized["file_count"],
        "llvm_manifest_sha256": _sha256_path(
            llvm_toolchain / "llvm-toolchain-manifest.json",
        ),
        "llvm_tool_count": len(llvm_tools),
        "llvm_resource_header_count": len(resource_files),
    }


def _retain_nautilus_leaf(
    path: Path, *, expected_mode: int,
) -> tuple[int, tuple[int, ...], str]:
    named = path.lstat()
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or named.st_uid != os.geteuid()
        or named.st_gid != os.getegid()
        or stat.S_IMODE(named.st_mode) != expected_mode
    ):
        raise TopologyError("Nautilus retained toolchain leaf is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        held = os.fstat(descriptor)
        identity = _artifact_identity(named)
        if _artifact_identity(held) != identity:
            raise TopologyError("Nautilus retained toolchain leaf changed")
        return descriptor, identity, _digest_fd(descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _sandbox_authority_from_session(
    session: NativeProbeSession, *, bwrap_policy_path: Path,
) -> dict[str, object]:
    policy_sha256 = _sha256_path(bwrap_policy_path)
    if (
        bwrap_policy_path == TRUSTED_BWRAP_POLICY
        and policy_sha256
        != "02366c24787531e112fe7ffe342065b499b07e586badc381e72f731e1467304e"
    ):
        raise TopologyError("Nautilus sandbox policy identity drift")
    policy = session.policy or {}
    expected_sha256 = policy.get("sandbox_sha256", EMPTY_SHA256)
    expected_uid = policy.get("sandbox_uid", -1)
    expected_gid = policy.get("sandbox_gid", -1)
    expected_mode_text = policy.get("sandbox_mode", "")
    expected_mode = (
        int(expected_mode_text, 8)
        if isinstance(expected_mode_text, str)
        and re.fullmatch(r"0[0-7]{3}", expected_mode_text) else -1
    )
    present = session.descriptor >= 0
    named = (
        session.executable_path.lstat()
        if present and session.executable_path is not None else None
    )
    return {
        "regular_file_status": (
            "ROOT_OWNED_POLICY_BOUND_EXECUTABLE" if present else "ABSENT"
        ),
        "policy_sha256": policy_sha256,
        "expected_sha256": expected_sha256,
        "observed_sha256": (
            session.probe["executable_sha256"] if present else EMPTY_SHA256
        ),
        "expected_uid": expected_uid,
        "observed_uid": named.st_uid if named is not None else -1,
        "expected_gid": expected_gid,
        "observed_gid": named.st_gid if named is not None else -1,
        "expected_mode": expected_mode,
        "observed_mode": stat.S_IMODE(named.st_mode) if named is not None else -1,
    }


def _multi_probe_from_native(
    code: str, session: NativeProbeSession,
) -> dict[str, object]:
    return {
        "command_id": NATIVE_COMMAND_IDS[code],
        "exit_code": session.probe["exit_code"],
        "stdout_sha256": session.probe["stdout_sha256"],
        "stderr_sha256": session.probe["stderr_sha256"],
    }


def _open_nautilus_multi_session(
    code: str, *, rust_toolchain: Path, llvm_toolchain: Path,
    toolchain_qualifier: Callable[[Path, Path], dict[str, object]],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    bwrap_policy_path: Path,
) -> NativeMultiAuthoritySession:
    rust_ancestors = _external_parent_chain_snapshot(rust_toolchain)
    llvm_ancestors = _external_parent_chain_snapshot(llvm_toolchain)
    rust_absent = _path_absent(rust_toolchain)
    llvm_absent = _path_absent(llvm_toolchain)
    if (
        rust_absent and llvm_absent
        and rust_ancestors is not None and llvm_ancestors is not None
    ):
        def absent_postcheck() -> None:
            if (
                not _path_absent(rust_toolchain)
                or not _path_absent(llvm_toolchain)
                or _external_parent_chain_snapshot(rust_toolchain) != rust_ancestors
                or _external_parent_chain_snapshot(llvm_toolchain) != llvm_ancestors
            ):
                raise TopologyError("native multi-authority changed during qualification")

        return NativeMultiAuthoritySession(
            code, "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT",
            _native_multi_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            _nautilus_multi_authority(code), (), absent_postcheck,
        )
    if rust_absent or llvm_absent or rust_ancestors is None or llvm_ancestors is None:
        return NativeMultiAuthoritySession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_multi_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            _nautilus_multi_authority(code, invalid=True), (), lambda: None,
        )

    descriptors: list[int] = []
    retained: list[tuple[Path, int, tuple[int, ...], str | None]] = []
    bwrap_session: NativeProbeSession | None = None
    try:
        for root in (rust_toolchain, llvm_toolchain):
            state, descriptor, identity = _open_external_directory(
                root, exact_mode=0o500,
            )
            if state != "PRESENT" or identity is None:
                raise TopologyError("Nautilus toolchain root is unsafe")
            descriptors.append(descriptor)
            retained.append((root, descriptor, identity, None))
        for path, expected_mode in (
            (rust_toolchain / "materialized-toolchain-manifest.json", 0o400),
            (rust_toolchain / "bin/cargo", 0o500),
            (rust_toolchain / "bin/rustc", 0o500),
            (llvm_toolchain / "llvm-toolchain-manifest.json", 0o400),
            (llvm_toolchain / "bin/clang", 0o500),
            (llvm_toolchain / "bin/clang++", 0o500),
            (llvm_toolchain / "bin/ld.lld", 0o500),
        ):
            descriptor, identity, digest = _retain_nautilus_leaf(
                path, expected_mode=expected_mode,
            )
            descriptors.append(descriptor)
            retained.append((path, descriptor, identity, digest))
        authority = toolchain_qualifier(rust_toolchain, llvm_toolchain)
        if set(authority) != NAUTILUS_TOOLCHAIN_AUTHORITY_KEYS:
            raise TopologyError("Nautilus toolchain authority schema drift")
        if toolchain_qualifier(rust_toolchain, llvm_toolchain) != authority:
            raise TopologyError("Nautilus toolchain authority changed before probe")
        probe = _native_multi_probe_record(code, exit_code=0)
        state = "AVAILABLE"
        fact = "NATIVE_CAPABILITY_VALIDATED"
        if code == "NATIVE-NAUTILUS-SEALED-BUILD-SANDBOX":
            bwrap_session = _execute_native_probe(
                _open_bwrap_session(bwrap_policy_path), runner=runner,
            )
            if bwrap_session.descriptor >= 0:
                descriptors.append(bwrap_session.descriptor)
            sandbox = _sandbox_authority_from_session(
                bwrap_session, bwrap_policy_path=bwrap_policy_path,
            )
            combined = {
                "authority_kind": "NAUTILUS_SEALED_BUILD_SANDBOX_V1",
                "toolchains": authority,
                "sandbox": sandbox,
            }
            if bwrap_session.state == "UNAVAILABLE":
                state = "UNAVAILABLE"
                fact = bwrap_session.fact
                probe = _multi_probe_from_native(code, bwrap_session)
            elif bwrap_session.state != "AVAILABLE":
                raise TopologyError("Nautilus build sandbox probe failed")
            authority_value = combined
        else:
            authority_value = authority

        def postcheck() -> None:
            try:
                if (
                    _external_parent_chain_snapshot(rust_toolchain) != rust_ancestors
                    or _external_parent_chain_snapshot(llvm_toolchain) != llvm_ancestors
                    or toolchain_qualifier(rust_toolchain, llvm_toolchain) != authority
                ):
                    raise TopologyError(
                        "native multi-authority changed during qualification",
                    )
                for path, descriptor, identity, digest in retained:
                    named = path.lstat()
                    held = os.fstat(descriptor)
                    if (
                        _artifact_identity(named) != identity
                        or _artifact_identity(held) != identity
                        or (digest is not None and _digest_fd(descriptor) != digest)
                    ):
                        raise TopologyError(
                            "native multi-authority changed during qualification",
                        )
                if bwrap_session is not None:
                    _postcheck_native_probe(bwrap_session)
            except TopologyError:
                raise
            except Exception as exc:
                raise TopologyError(
                    "native multi-authority changed during qualification",
                ) from exc

        return NativeMultiAuthoritySession(
            code, state, fact, probe,
            authority_value, tuple(descriptors), postcheck,
        )
    except Exception:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return NativeMultiAuthoritySession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_multi_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            _nautilus_multi_authority(code, invalid=True), (), lambda: None,
        )


def _native_probe_record(
    code: str, *, exit_code: int, stdout: bytes = b"", stderr: bytes = b"",
    executable_sha256: str = EMPTY_SHA256,
) -> dict[str, object]:
    return {
        "command_id": NATIVE_COMMAND_IDS[code],
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "executable_sha256": executable_sha256,
    }


def _native_path_leaf(path: Path) -> tuple[str, os.stat_result | None]:
    """Classify one fixed root-owned executable without following any component link."""
    if not path.is_absolute():
        return "BROKEN", None
    current = Path(path.anchor)
    try:
        root_info = current.lstat()
    except OSError:
        return "BROKEN", None
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != 0
        or root_info.st_gid != 0
        or root_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        return "BROKEN", None
    for part in path.parts[1:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            return "BROKEN", None
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            return "BROKEN", None
    try:
        leaf = path.lstat()
    except FileNotFoundError:
        return "ABSENT", None
    except OSError:
        return "BROKEN", None
    if (
        stat.S_ISLNK(leaf.st_mode)
        or not stat.S_ISREG(leaf.st_mode)
        or leaf.st_nlink != 1
        or leaf.st_uid != 0
        or leaf.st_gid != 0
        or stat.S_IMODE(leaf.st_mode) != 0o755
        or not leaf.st_mode & stat.S_IXUSR
    ):
        return "BROKEN", None
    return "PRESENT", leaf


def _open_unshare_session(path: Path) -> NativeProbeSession:
    state, named = _native_path_leaf(path)
    if state == "ABSENT":
        return NativeProbeSession(
            "NATIVE-USERNS-ROOT-PROVISION", "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT",
            _native_probe_record(
                "NATIVE-USERNS-ROOT-PROVISION", exit_code=NATIVE_PROBE_NOT_EXECUTED,
            ), -1, None, None, None, None,
        )
    if state != "PRESENT" or named is None:
        return NativeProbeSession(
            "NATIVE-USERNS-ROOT-PROVISION", "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_probe_record(
                "NATIVE-USERNS-ROOT-PROVISION", exit_code=NATIVE_PROBE_NOT_EXECUTED,
            ), -1, path, None, None, None,
        )
    descriptor = -1
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if _artifact_identity(opened) != _artifact_identity(named):
            raise TopologyError("native executable identity changed before probe")
        digest = _digest_fd(descriptor)
    except (OSError, TopologyError):
        if descriptor >= 0:
            os.close(descriptor)
        return NativeProbeSession(
            "NATIVE-USERNS-ROOT-PROVISION", "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_probe_record(
                "NATIVE-USERNS-ROOT-PROVISION", exit_code=NATIVE_PROBE_NOT_EXECUTED,
            ), -1, path, None, None, None,
        )
    return NativeProbeSession(
        "NATIVE-USERNS-ROOT-PROVISION", "PROBE_PENDING", "NATIVE_PROBE_INVALID",
        _native_probe_record(
            "NATIVE-USERNS-ROOT-PROVISION", exit_code=NATIVE_PROBE_NOT_EXECUTED,
            executable_sha256=digest,
        ), descriptor, path, _artifact_identity(named), _artifact_identity(opened), None,
    )


def _open_bwrap_session(policy_path: Path) -> NativeProbeSession:
    code = "NATIVE-BWRAP-OS-SANDBOX"
    try:
        from scripts import materialize_sealed_uv_exec as sealed_uv
        policy = sealed_uv.load_policy(policy_path)
        path = Path(str(policy["sandbox_path"]))
    except (ImportError, OSError, ValueError, AttributeError, RuntimeError):
        return NativeProbeSession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            -1, None, None, None, None,
        )
    path_state, named = _native_path_leaf(path)
    if path_state == "ABSENT":
        return NativeProbeSession(
            code, "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT",
            _native_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            -1, None, None, None, policy,
        )
    if path_state != "PRESENT" or named is None:
        return NativeProbeSession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            -1, path, None, None, policy,
        )
    descriptor = -1
    try:
        descriptor = sealed_uv._verify_sandbox(policy)
        named_after = path.lstat()
        if _artifact_identity(named_after) != _artifact_identity(named):
            raise TopologyError("native executable identity changed before probe")
        opened = os.fstat(descriptor)
        digest = _digest_fd(descriptor)
        if digest != policy["sandbox_sha256"]:
            raise TopologyError("native executable policy digest drift")
    except (OSError, ValueError, AttributeError, TopologyError, RuntimeError):
        if descriptor >= 0:
            os.close(descriptor)
        return NativeProbeSession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            _native_probe_record(code, exit_code=NATIVE_PROBE_NOT_EXECUTED),
            -1, path, None, None, policy,
        )
    return NativeProbeSession(
        code, "PROBE_PENDING", "NATIVE_PROBE_INVALID",
        _native_probe_record(
            code, exit_code=NATIVE_PROBE_NOT_EXECUTED, executable_sha256=digest,
        ), descriptor, path, _artifact_identity(named), _artifact_identity(opened), policy,
    )


def _native_probe_argv(session: NativeProbeSession) -> list[str]:
    executable = f"/proc/self/fd/{session.descriptor}"
    if session.code == "NATIVE-BWRAP-OS-SANDBOX":
        return [
            executable, "--die-with-parent", "--unshare-user", "--unshare-pid",
            "--unshare-net", "--new-session", "--clearenv", "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp", "--", "/usr/bin/true",
        ]
    return [executable, "--user", "--map-root-user", "/usr/bin/true"]


def _execute_native_probe(
    session: NativeProbeSession, *, runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> NativeProbeSession:
    if session.state != "PROBE_PENDING":
        return session
    command = _native_probe_argv(session)
    try:
        result = runner(
            command, cwd=Path("/"), env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, pass_fds=(session.descriptor,),
        )
    except subprocess.TimeoutExpired:
        return NativeProbeSession(
            **{**session.__dict__, "state": "BROKEN", "fact": "NATIVE_PROBE_INVALID",
               "probe": _native_probe_record(
                   session.code, exit_code=NATIVE_PROBE_TIMEOUT,
                   executable_sha256=str(session.probe["executable_sha256"]),
               )},
        )
    except (OSError, subprocess.SubprocessError):
        return NativeProbeSession(
            **{**session.__dict__, "state": "BROKEN", "fact": "NATIVE_PROBE_INVALID"},
        )
    stdout = result.stdout if isinstance(result.stdout, bytes) else b""
    stderr = result.stderr if isinstance(result.stderr, bytes) else b""
    probe = _native_probe_record(
        session.code, exit_code=result.returncode, stdout=stdout, stderr=stderr,
        executable_sha256=str(session.probe["executable_sha256"]),
    )
    if result.returncode == 0 and stdout == b"" and stderr == b"":
        return NativeProbeSession(
            **{**session.__dict__, "state": "AVAILABLE",
               "fact": "NATIVE_CAPABILITY_VALIDATED", "probe": probe},
        )
    if result.returncode == 1 and stdout == b"" and stderr in NATIVE_DENIAL_STDERR[session.code]:
        return NativeProbeSession(
            **{**session.__dict__, "state": "UNAVAILABLE",
               "fact": "RUNNER_POLICY_DISALLOWS_USERNS", "probe": probe},
        )
    return NativeProbeSession(
        **{**session.__dict__, "state": "BROKEN", "fact": "NATIVE_PROBE_INVALID",
           "probe": probe},
    )


def _postcheck_native_probe(
    session: NativeProbeSession | NativeMultiAuthoritySession,
) -> None:
    if isinstance(session, NativeMultiAuthoritySession):
        try:
            session.postcheck()
        except TopologyError:
            raise
        except Exception as exc:
            raise TopologyError(
                "native multi-authority changed during qualification",
            ) from exc
        return
    if session.descriptor < 0:
        return
    if (
        session.executable_path is None
        or session.named_identity is None
        or session.descriptor_identity is None
    ):
        raise TopologyError("native retained executable state is incomplete")
    try:
        held = os.fstat(session.descriptor)
        named = session.executable_path.lstat()
        if (
            _artifact_identity(held) != session.descriptor_identity
            or _artifact_identity(named) != session.named_identity
            or _digest_fd(session.descriptor) != session.probe["executable_sha256"]
        ):
            raise TopologyError("native executable identity changed during execution")
        if session.code == "NATIVE-BWRAP-OS-SANDBOX":
            from scripts import materialize_sealed_uv_exec as sealed_uv
            if session.policy is None or hashlib.sha256(
                sealed_uv._read_bound_sandbox(session.policy),
            ).hexdigest() != session.probe["executable_sha256"]:
                raise TopologyError("native executable identity changed during execution")
    except (OSError, ValueError, AttributeError, ImportError, RuntimeError) as exc:
        raise TopologyError("native executable identity changed during execution") from exc


@contextmanager
def _retained_native_probe(
    code: str, *, runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    bwrap_policy_path: Path | None = None,
    unshare_path: Path | None = None,
    rust_toolchain: Path = NAUTILUS_RUST_TOOLCHAIN,
    llvm_toolchain: Path = NAUTILUS_LLVM_TOOLCHAIN,
    toolchain_qualifier: Callable[[Path, Path], dict[str, object]] = (
        _qualify_nautilus_toolchains
    ),
):
    bwrap_policy_path = TRUSTED_BWRAP_POLICY if bwrap_policy_path is None else bwrap_policy_path
    unshare_path = TRUSTED_UNSHARE if unshare_path is None else unshare_path
    if code == "NATIVE-BWRAP-OS-SANDBOX":
        session = _open_bwrap_session(bwrap_policy_path)
    elif code == "NATIVE-USERNS-ROOT-PROVISION":
        session = _open_unshare_session(unshare_path)
    elif code in NATIVE_MULTI_CODES:
        session = _open_nautilus_multi_session(
            code, rust_toolchain=rust_toolchain, llvm_toolchain=llvm_toolchain,
            toolchain_qualifier=toolchain_qualifier, runner=runner,
            bwrap_policy_path=bwrap_policy_path,
        )
    else:
        raise TopologyError("unknown native capability")
    session = _execute_native_probe(session, runner=runner)
    try:
        yield session
    finally:
        if isinstance(session, NativeMultiAuthoritySession):
            for descriptor in session.descriptors:
                os.close(descriptor)
        elif session.descriptor >= 0:
            os.close(session.descriptor)


def _native_preflight(code: str) -> tuple[str, str]:
    """Compatibility wrapper; production lanes retain the full probe session."""
    with _retained_native_probe(code) as session:
        _postcheck_native_probe(session)
        return session.state, session.fact


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
        getattr(analysis, "inventory_hash", None) == PHASE3B_EXPECTED_INVENTORY_SHA256
        and getattr(analysis, "decision_total", None) == PHASE3B_EXPECTED_DECISION_TOTAL
        and getattr(analysis, "cost_sessions", None) == PHASE3B_EXPECTED_COST_SESSIONS
        and getattr(analysis, "asset_count", None) == PHASE3B_EXPECTED_ASSET_COUNT
        and getattr(analysis, "asset_source_files", None)
        == PHASE3B_EXPECTED_ASSET_SOURCE_FILES
    )


def _phase3b_absent_authority() -> dict[str, object]:
    return {
        "authority_kind": "PHASE3B_REVIEWED_CORPUS_V1",
        "regular_directory_status": "ABSENT",
        "expected_inventory_sha256": PHASE3B_EXPECTED_INVENTORY_SHA256,
        "observed_inventory_sha256": EMPTY_SHA256,
        "required_entry_manifest_sha256": EMPTY_SHA256,
        "required_entry_count": 0,
        "expected_decision_total": PHASE3B_EXPECTED_DECISION_TOTAL,
        "observed_decision_total": 0,
        "expected_cost_sessions": PHASE3B_EXPECTED_COST_SESSIONS,
        "observed_cost_sessions": 0,
        "expected_asset_count": PHASE3B_EXPECTED_ASSET_COUNT,
        "observed_asset_count": 0,
        "expected_asset_source_files": PHASE3B_EXPECTED_ASSET_SOURCE_FILES,
        "observed_asset_source_files": 0,
    }


def _legacy_absent_authority() -> dict[str, object]:
    return {
        "authority_kind": "LEGACY_UV_AND_CLOSURE_V1",
        "regular_file_status": "ABSENT",
        "expected_uv_sha256": LEGACY_UV_SHA256,
        "observed_uv_sha256": EMPTY_SHA256,
        "expected_uv_version": LEGACY_UV_VERSION,
        "observed_uv_version": "",
        "expected_uid": os.geteuid(),
        "observed_uid": -1,
        "expected_gid": os.getegid(),
        "observed_gid": -1,
        "expected_mode": 0o755,
        "observed_mode": -1,
        "legacy_closure_manifest_sha256": EMPTY_SHA256,
        "legacy_closure_entry_count": 0,
        "sync_command_id": "LEGACY_UV_SYNC_FROZEN_OFFLINE_V1",
        "sync_exit_code": -1,
        "sync_stdout_sha256": EMPTY_SHA256,
        "sync_stderr_sha256": EMPTY_SHA256,
    }


def _invalid_external_authority(code: str) -> dict[str, object]:
    if code == "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS":
        authority = _absent_nautilus_external_authority()
        authority["base_root_status"] = "INVALID"
        authority["artifact_root_status"] = "INVALID"
        return authority
    authority = (
        _phase3b_absent_authority()
        if code == "EXT-PHASE3B-CORPUS" else _legacy_absent_authority()
    )
    status_key = (
        "regular_directory_status"
        if code == "EXT-PHASE3B-CORPUS" else "regular_file_status"
    )
    authority[status_key] = "INVALID"
    return authority


def _absent_nautilus_external_authority() -> dict[str, object]:
    return {
        "authority_kind": "NAUTILUS_RUNTIME_CLOSURE_INPUTS_V1",
        "base_root_status": "ABSENT",
        "artifact_root_status": "ABSENT",
        "runtime_policy_sha256": EMPTY_SHA256,
        "base_manifest_sha256": EMPTY_SHA256,
        "base_file_count": 0,
        "base_file_inventory_sha256": EMPTY_SHA256,
        "artifact_manifest_sha256": EMPTY_SHA256,
        "artifact_wheel_sha256": EMPTY_SHA256,
        "artifact_wheel_size": 0,
    }


class _ExternalStateError(TopologyError):
    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__("external authority state is not valid")


def _external_parent_chain_snapshot(
    path: Path, *, legacy_component_policy: bool = False,
) -> tuple[tuple[int, ...], ...] | None:
    if not path.is_absolute():
        return None
    if legacy_component_policy and path != REAL_LEGACY_ROOT:
        return None
    snapshot: list[tuple[int, ...]] = []
    current = Path(path.anchor)
    for part in (None, *path.parts[1:-1]):
        if part is not None:
            current /= part
        try:
            info = current.lstat()
        except OSError:
            return None
        current_identity_group_write = (
            legacy_component_policy
            and stat.S_IMODE(info.st_mode) == 0o775
            and info.st_uid == os.geteuid()
            and info.st_gid == os.getegid()
        )
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_gid not in {0, os.getegid()}
            or info.st_mode & stat.S_IWOTH
            or (
                info.st_mode & stat.S_IWGRP
                and not current_identity_group_write
            )
        ):
            return None
        snapshot.append((
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        ))
    return tuple(snapshot)


def _external_parent_chain_safe(
    path: Path, *, legacy_component_policy: bool = False,
) -> bool:
    return _external_parent_chain_snapshot(
        path, legacy_component_policy=legacy_component_policy,
    ) is not None


def _open_external_directory(
    path: Path, *, exact_mode: int | None,
    legacy_component_policy: bool = False,
) -> tuple[str, int, tuple[int, ...] | None]:
    if not _external_parent_chain_safe(
        path, legacy_component_policy=legacy_component_policy,
    ):
        return "INVALID", -1, None
    try:
        named = path.lstat()
    except FileNotFoundError:
        return "ABSENT", -1, None
    except OSError:
        return "INVALID", -1, None
    mode = stat.S_IMODE(named.st_mode)
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or named.st_uid != os.geteuid()
        or named.st_gid != os.getegid()
        or named.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (exact_mode is not None and mode != exact_mode)
    ):
        return "INVALID", -1, None
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        identity = _artifact_identity(opened)
        if identity != _artifact_identity(named):
            raise OSError("directory identity changed")
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        return "INVALID", -1, None
    return "PRESENT", descriptor, identity


def _open_external_regular_executable(
    path: Path,
) -> tuple[str, int, tuple[int, ...] | None]:
    if not _external_parent_chain_safe(path):
        return "INVALID", -1, None
    try:
        named = path.lstat()
    except FileNotFoundError:
        return "ABSENT", -1, None
    except OSError:
        return "INVALID", -1, None
    if (
        stat.S_ISLNK(named.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or named.st_uid != os.geteuid()
        or named.st_gid != os.getegid()
        or stat.S_IMODE(named.st_mode) != 0o755
    ):
        return "INVALID", -1, None
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        identity = _artifact_identity(opened)
        if identity != _artifact_identity(named):
            raise OSError("executable identity changed")
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        return "INVALID", -1, None
    return "PRESENT", descriptor, identity


def _required_entry_record(
    root_descriptor: int, relative: str, *, directory: bool,
    allow_final_symlink: bool,
) -> dict[str, object]:
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise _ExternalStateError("INVALID")
    current_descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            try:
                info = os.stat(part, dir_fd=current_descriptor, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise _ExternalStateError("PARTIAL") from exc
            except OSError as exc:
                raise _ExternalStateError("INVALID") from exc
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_gid != os.getegid()
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise _ExternalStateError("INVALID")
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=current_descriptor,
                )
            except OSError as exc:
                raise _ExternalStateError("INVALID") from exc
            os.close(current_descriptor)
            current_descriptor = child
        name = parts[-1]
        try:
            info = os.stat(name, dir_fd=current_descriptor, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise _ExternalStateError("PARTIAL") from exc
        except OSError as exc:
            raise _ExternalStateError("INVALID") from exc
        if info.st_uid != os.geteuid() or info.st_gid != os.getegid():
            raise _ExternalStateError("INVALID")
        identity = list(_artifact_identity(info))
        if directory:
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise _ExternalStateError("INVALID")
            kind = "directory"
            digest = EMPTY_SHA256
        elif stat.S_ISLNK(info.st_mode):
            if not allow_final_symlink:
                raise _ExternalStateError("INVALID")
            try:
                target = os.readlink(name, dir_fd=current_descriptor)
            except OSError as exc:
                raise _ExternalStateError("INVALID") from exc
            kind = "symlink"
            digest = hashlib.sha256(os.fsencode(target)).hexdigest()
        else:
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise _ExternalStateError("INVALID")
            descriptor = -1
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=current_descriptor,
                )
                opened = os.fstat(descriptor)
                if _artifact_identity(opened) != _artifact_identity(info):
                    raise OSError("required entry identity changed")
                digest = _digest_fd(descriptor)
                if _artifact_identity(os.fstat(descriptor)) != _artifact_identity(info):
                    raise OSError("required entry identity changed")
            except OSError as exc:
                raise _ExternalStateError("INVALID") from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            kind = "regular"
        return {
            "relative_name_sha256": hashlib.sha256(relative.encode("utf-8")).hexdigest(),
            "kind": kind,
            "identity": identity,
            "content_or_link_sha256": digest,
        }
    finally:
        os.close(current_descriptor)


def _required_entry_manifest(
    root_descriptor: int, entries: tuple[tuple[str, bool], ...], *,
    allow_symlink_names: frozenset[str] = frozenset(),
) -> str:
    records = [
        _required_entry_record(
            root_descriptor, relative, directory=directory,
            allow_final_symlink=relative in allow_symlink_names,
        )
        for relative, directory in entries
    ]
    return _sha256(records)


@dataclass(frozen=True)
class ExternalAuthoritySession:
    code: str
    state: str
    fact: str
    authority: dict[str, object]
    descriptors: tuple[int, ...]
    postcheck: Callable[[], None]


def _postcheck_external_authority(session: ExternalAuthoritySession) -> None:
    try:
        session.postcheck()
    except TopologyError:
        raise
    except Exception as exc:
        raise TopologyError("external authority changed during qualification") from exc


def _phase3b_authority_from_analysis(
    analysis: object, manifest_sha256: str,
) -> dict[str, object]:
    if not _phase3b_valid(analysis):
        raise _ExternalStateError("INVALID")
    return {
        "authority_kind": "PHASE3B_REVIEWED_CORPUS_V1",
        "regular_directory_status": "PRIVATE_CURRENT_USER_DIRECTORY",
        "expected_inventory_sha256": PHASE3B_EXPECTED_INVENTORY_SHA256,
        "observed_inventory_sha256": str(getattr(analysis, "inventory_hash")),
        "required_entry_manifest_sha256": manifest_sha256,
        "required_entry_count": len(PHASE3B_REQUIRED_ENTRIES),
        "expected_decision_total": PHASE3B_EXPECTED_DECISION_TOTAL,
        "observed_decision_total": int(getattr(analysis, "decision_total")),
        "expected_cost_sessions": PHASE3B_EXPECTED_COST_SESSIONS,
        "observed_cost_sessions": int(getattr(analysis, "cost_sessions")),
        "expected_asset_count": PHASE3B_EXPECTED_ASSET_COUNT,
        "observed_asset_count": int(getattr(analysis, "asset_count")),
        "expected_asset_source_files": PHASE3B_EXPECTED_ASSET_SOURCE_FILES,
        "observed_asset_source_files": int(getattr(analysis, "asset_source_files")),
    }


def _open_phase3b_external_session(
    *, corpus_root: Path, corpus_validator: Callable[[Path], object],
) -> ExternalAuthoritySession:
    ancestor_snapshot = _external_parent_chain_snapshot(corpus_root)
    state, descriptor, identity = _open_external_directory(corpus_root, exact_mode=0o700)
    if state == "ABSENT":
        def absent_postcheck() -> None:
            current_ancestors = _external_parent_chain_snapshot(corpus_root)
            current_state, current_descriptor, _ = _open_external_directory(
                corpus_root, exact_mode=0o700,
            )
            if current_descriptor >= 0:
                os.close(current_descriptor)
            if (
                current_state != "ABSENT"
                or current_ancestors != ancestor_snapshot
            ):
                raise TopologyError("external authority changed during qualification")

        return ExternalAuthoritySession(
            "EXT-PHASE3B-CORPUS", "ABSENT", "AUTHORITY_ROOT_ABSENT",
            _phase3b_absent_authority(), (), absent_postcheck,
        )
    if state != "PRESENT" or identity is None:
        return ExternalAuthoritySession(
            "EXT-PHASE3B-CORPUS", "INVALID", "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
        )
    if (
        ancestor_snapshot is None
        or _external_parent_chain_snapshot(corpus_root) != ancestor_snapshot
    ):
        os.close(descriptor)
        return ExternalAuthoritySession(
            "EXT-PHASE3B-CORPUS", "INVALID", "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
        )
    try:
        first_manifest = _required_entry_manifest(descriptor, PHASE3B_REQUIRED_ENTRIES)
        analysis = corpus_validator(corpus_root)
        second_manifest = _required_entry_manifest(descriptor, PHASE3B_REQUIRED_ENTRIES)
        if first_manifest != second_manifest:
            raise _ExternalStateError("INVALID")
        authority = _phase3b_authority_from_analysis(analysis, second_manifest)
    except FileNotFoundError:
        os.close(descriptor)
        return ExternalAuthoritySession(
            "EXT-PHASE3B-CORPUS", "PARTIAL", "AUTHORITY_PARTIAL",
            _invalid_external_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
        )
    except _ExternalStateError as exc:
        os.close(descriptor)
        fact = "AUTHORITY_PARTIAL" if exc.state == "PARTIAL" else "AUTHORITY_INVALID"
        return ExternalAuthoritySession(
            "EXT-PHASE3B-CORPUS", exc.state, fact,
            _invalid_external_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
        )
    except Exception:
        os.close(descriptor)
        return ExternalAuthoritySession(
            "EXT-PHASE3B-CORPUS", "INVALID", "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
        )

    def postcheck() -> None:
        try:
            current_ancestors = _external_parent_chain_snapshot(corpus_root)
            named = corpus_root.lstat()
            held = os.fstat(descriptor)
            current_manifest = _required_entry_manifest(
                descriptor, PHASE3B_REQUIRED_ENTRIES,
            )
            current_analysis = corpus_validator(corpus_root)
            current_authority = _phase3b_authority_from_analysis(
                current_analysis, current_manifest,
            )
        except Exception as exc:
            raise TopologyError("external authority changed during qualification") from exc
        if (
            current_ancestors != ancestor_snapshot
            or _artifact_identity(named) != identity
            or _artifact_identity(held) != identity
            or current_authority != authority
        ):
            raise TopologyError("external authority changed during qualification")

    return ExternalAuthoritySession(
        "EXT-PHASE3B-CORPUS", "VALID", "AUTHORITY_COMPLETE_VALIDATED",
        authority, (descriptor,), postcheck,
    )


def _open_legacy_external_session(
    *, uv_path: Path, legacy_root: Path, expected_uv_sha256: str,
    expected_uv_version: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> ExternalAuthoritySession:
    legacy_component_policy = legacy_root == REAL_LEGACY_ROOT
    uv_ancestor_snapshot = _external_parent_chain_snapshot(uv_path)
    root_ancestor_snapshot = _external_parent_chain_snapshot(
        legacy_root, legacy_component_policy=legacy_component_policy,
    )
    uv_state, uv_descriptor, uv_identity = _open_external_regular_executable(uv_path)
    root_state, root_descriptor, root_identity = _open_external_directory(
        legacy_root, exact_mode=None,
        legacy_component_policy=legacy_component_policy,
    )
    if uv_state == root_state == "ABSENT":
        def absent_postcheck() -> None:
            current_uv_ancestors = _external_parent_chain_snapshot(uv_path)
            current_root_ancestors = _external_parent_chain_snapshot(
                legacy_root, legacy_component_policy=legacy_component_policy,
            )
            current_uv, current_uv_descriptor, _ = _open_external_regular_executable(uv_path)
            current_root, current_root_descriptor, _ = _open_external_directory(
                legacy_root, exact_mode=None,
                legacy_component_policy=legacy_component_policy,
            )
            for retained in (current_uv_descriptor, current_root_descriptor):
                if retained >= 0:
                    os.close(retained)
            if (
                current_uv != "ABSENT"
                or current_root != "ABSENT"
                or current_uv_ancestors != uv_ancestor_snapshot
                or current_root_ancestors != root_ancestor_snapshot
            ):
                raise TopologyError("external authority changed during qualification")

        return ExternalAuthoritySession(
            "EXT-LEGACY-UV-AUTHORITY", "ABSENT", "AUTHORITY_EXECUTABLE_ABSENT",
            _legacy_absent_authority(), (), absent_postcheck,
        )
    if uv_state == "ABSENT" or root_state == "ABSENT":
        for retained in (uv_descriptor, root_descriptor):
            if retained >= 0:
                os.close(retained)
        return ExternalAuthoritySession(
            "EXT-LEGACY-UV-AUTHORITY", "PARTIAL", "AUTHORITY_PARTIAL",
            _invalid_external_authority("EXT-LEGACY-UV-AUTHORITY"), (), lambda: None,
        )
    if (
        uv_state != "PRESENT" or root_state != "PRESENT"
        or uv_identity is None or root_identity is None
    ):
        for retained in (uv_descriptor, root_descriptor):
            if retained >= 0:
                os.close(retained)
        return ExternalAuthoritySession(
            "EXT-LEGACY-UV-AUTHORITY", "INVALID", "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-LEGACY-UV-AUTHORITY"), (), lambda: None,
        )
    if (
        uv_ancestor_snapshot is None
        or root_ancestor_snapshot is None
        or _external_parent_chain_snapshot(uv_path) != uv_ancestor_snapshot
        or _external_parent_chain_snapshot(
            legacy_root, legacy_component_policy=legacy_component_policy,
        ) != root_ancestor_snapshot
    ):
        os.close(uv_descriptor)
        os.close(root_descriptor)
        return ExternalAuthoritySession(
            "EXT-LEGACY-UV-AUTHORITY", "INVALID", "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-LEGACY-UV-AUTHORITY"), (), lambda: None,
        )
    try:
        digest = _digest_fd(uv_descriptor)
        first_closure = _required_entry_manifest(
            root_descriptor, LEGACY_CLOSURE_ENTRIES,
            allow_symlink_names=frozenset({".venv/bin/python"}),
        )
        executable = f"/proc/self/fd/{uv_descriptor}"
        common = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "check": False,
            "pass_fds": (uv_descriptor,),
        }
        version = runner(
            [executable, "--version"], timeout=10,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            **common,
        )
        sync = runner(
            [executable, "sync", "--frozen", "--extra", "test"],
            cwd=legacy_root, timeout=120,
            env={
                "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                "PYTHONNOUSERSITE": "1", "UV_OFFLINE": "1",
                "UV_NO_PROGRESS": "1",
            },
            **common,
        )
        second_closure = _required_entry_manifest(
            root_descriptor, LEGACY_CLOSURE_ENTRIES,
            allow_symlink_names=frozenset({".venv/bin/python"}),
        )
        stdout = version.stdout if isinstance(version.stdout, bytes) else b""
        stderr = version.stderr if isinstance(version.stderr, bytes) else b""
        sync_stdout = sync.stdout if isinstance(sync.stdout, bytes) else b""
        sync_stderr = sync.stderr if isinstance(sync.stderr, bytes) else b""
        if (
            digest != expected_uv_sha256
            or version.returncode != 0
            or stdout != expected_uv_version.encode("utf-8") + b"\n"
            or stderr != b""
            or sync.returncode != 0
            or first_closure != second_closure
        ):
            raise _ExternalStateError("INVALID")
        uv_info = os.fstat(uv_descriptor)
        authority = {
            "authority_kind": "LEGACY_UV_AND_CLOSURE_V1",
            "regular_file_status": "PRIVATE_CURRENT_USER_EXECUTABLE",
            "expected_uv_sha256": expected_uv_sha256,
            "observed_uv_sha256": digest,
            "expected_uv_version": expected_uv_version,
            "observed_uv_version": stdout[:-1].decode("utf-8"),
            "expected_uid": os.geteuid(),
            "observed_uid": uv_info.st_uid,
            "expected_gid": os.getegid(),
            "observed_gid": uv_info.st_gid,
            "expected_mode": 0o755,
            "observed_mode": stat.S_IMODE(uv_info.st_mode),
            "legacy_closure_manifest_sha256": second_closure,
            "legacy_closure_entry_count": len(LEGACY_CLOSURE_ENTRIES),
            "sync_command_id": "LEGACY_UV_SYNC_FROZEN_OFFLINE_V1",
            "sync_exit_code": sync.returncode,
            "sync_stdout_sha256": hashlib.sha256(sync_stdout).hexdigest(),
            "sync_stderr_sha256": hashlib.sha256(sync_stderr).hexdigest(),
        }
    except (OSError, subprocess.SubprocessError, UnicodeError, _ExternalStateError):
        os.close(uv_descriptor)
        os.close(root_descriptor)
        return ExternalAuthoritySession(
            "EXT-LEGACY-UV-AUTHORITY", "INVALID", "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-LEGACY-UV-AUTHORITY"), (), lambda: None,
        )

    def postcheck() -> None:
        try:
            current_uv_ancestors = _external_parent_chain_snapshot(uv_path)
            current_root_ancestors = _external_parent_chain_snapshot(
                legacy_root, legacy_component_policy=legacy_component_policy,
            )
            named_uv = uv_path.lstat()
            held_uv = os.fstat(uv_descriptor)
            named_root = legacy_root.lstat()
            held_root = os.fstat(root_descriptor)
            closure = _required_entry_manifest(
                root_descriptor, LEGACY_CLOSURE_ENTRIES,
                allow_symlink_names=frozenset({".venv/bin/python"}),
            )
        except Exception as exc:
            raise TopologyError("external authority changed during qualification") from exc
        if (
            current_uv_ancestors != uv_ancestor_snapshot
            or current_root_ancestors != root_ancestor_snapshot
            or _artifact_identity(named_uv) != uv_identity
            or _artifact_identity(held_uv) != uv_identity
            or _digest_fd(uv_descriptor) != authority["observed_uv_sha256"]
            or _artifact_identity(named_root) != root_identity
            or _artifact_identity(held_root) != root_identity
            or closure != authority["legacy_closure_manifest_sha256"]
        ):
            raise TopologyError("external authority changed during qualification")

    return ExternalAuthoritySession(
        "EXT-LEGACY-UV-AUTHORITY", "VALID", "AUTHORITY_COMPLETE_VALIDATED",
        authority, (uv_descriptor, root_descriptor), postcheck,
    )


def _qualify_nautilus_external_inputs(
    base_runtime: Path, artifact_root: Path,
) -> dict[str, object]:
    from scripts import materialize_nautilus_runtime_closure as runtime

    policy_sha256 = _sha256_path(NAUTILUS_RUNTIME_POLICY)
    if (
        policy_sha256
        != "746df241937f6e791f30d66f2b70d50c88c451d6e6575fd903a46ea63e6c3ae2"
    ):
        raise TopologyError("Nautilus runtime policy identity drift")
    policy = runtime._load_policy(NAUTILUS_RUNTIME_POLICY)
    base_manifest, base_files = runtime._validate_base_runtime(
        base_runtime, policy,
    )
    artifact_manifest, wheel_path = runtime._validate_artifact(
        artifact_root, policy,
    )
    wheel = artifact_manifest["wheel"]
    assert isinstance(wheel, dict)
    return {
        "authority_kind": "NAUTILUS_RUNTIME_CLOSURE_INPUTS_V1",
        "base_root_status": "PRIVATE_CURRENT_USER_SEALED_DIRECTORY",
        "artifact_root_status": "PRIVATE_CURRENT_USER_SEALED_DIRECTORY",
        "runtime_policy_sha256": policy_sha256,
        "base_manifest_sha256": _sha256_path(
            base_runtime / "closure-manifest.json",
        ),
        "base_file_count": len(base_files),
        "base_file_inventory_sha256": str(policy["base_file_inventory_sha256"]),
        "artifact_manifest_sha256": _sha256_path(
            artifact_root / "artifact-manifest.json",
        ),
        "artifact_wheel_sha256": _sha256_path(wheel_path),
        "artifact_wheel_size": int(wheel["size"]),
    }


def _open_nautilus_external_session(
    *, base_runtime: Path, artifact_root: Path,
    qualifier: Callable[[Path, Path], dict[str, object]],
) -> ExternalAuthoritySession:
    base_ancestors = _external_parent_chain_snapshot(base_runtime)
    artifact_ancestors = _external_parent_chain_snapshot(artifact_root)
    base_state, base_descriptor, _ = _open_external_directory(
        base_runtime, exact_mode=0o500,
    )
    artifact_state, artifact_descriptor, _ = _open_external_directory(
        artifact_root, exact_mode=0o500,
    )
    if base_state == artifact_state == "ABSENT":
        def absent_postcheck() -> None:
            current_base, current_base_descriptor, _ = _open_external_directory(
                base_runtime, exact_mode=0o500,
            )
            current_artifact, current_artifact_descriptor, _ = _open_external_directory(
                artifact_root, exact_mode=0o500,
            )
            for retained in (current_base_descriptor, current_artifact_descriptor):
                if retained >= 0:
                    os.close(retained)
            if (
                current_base != "ABSENT"
                or current_artifact != "ABSENT"
                or _external_parent_chain_snapshot(base_runtime) != base_ancestors
                or _external_parent_chain_snapshot(artifact_root) != artifact_ancestors
            ):
                raise TopologyError("external authority changed during qualification")

        return ExternalAuthoritySession(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS", "ABSENT",
            "AUTHORITY_ROOT_ABSENT", _absent_nautilus_external_authority(), (),
            absent_postcheck,
        )
    if "ABSENT" in {base_state, artifact_state}:
        for descriptor in (base_descriptor, artifact_descriptor):
            if descriptor >= 0:
                os.close(descriptor)
        return ExternalAuthoritySession(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS", "PARTIAL",
            "AUTHORITY_PARTIAL",
            _invalid_external_authority("EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS"),
            (), lambda: None,
        )
    if (
        base_state != "PRESENT" or artifact_state != "PRESENT"
        or base_ancestors is None or artifact_ancestors is None
    ):
        for descriptor in (base_descriptor, artifact_descriptor):
            if descriptor >= 0:
                os.close(descriptor)
        return ExternalAuthoritySession(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS", "INVALID",
            "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS"),
            (), lambda: None,
        )
    try:
        base_identity = _artifact_identity(os.fstat(base_descriptor))
        artifact_identity = _artifact_identity(os.fstat(artifact_descriptor))
        authority = qualifier(base_runtime, artifact_root)
        if set(authority) != NAUTILUS_RUNTIME_AUTHORITY_KEYS:
            raise TopologyError("Nautilus external authority schema drift")
        if qualifier(base_runtime, artifact_root) != authority:
            raise TopologyError("Nautilus external authority changed before session")
    except Exception:
        os.close(base_descriptor)
        os.close(artifact_descriptor)
        return ExternalAuthoritySession(
            "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS", "INVALID",
            "AUTHORITY_INVALID",
            _invalid_external_authority("EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS"),
            (), lambda: None,
        )

    def postcheck() -> None:
        try:
            if (
                _external_parent_chain_snapshot(base_runtime) != base_ancestors
                or _external_parent_chain_snapshot(artifact_root) != artifact_ancestors
                or _artifact_identity(base_runtime.lstat()) != base_identity
                or _artifact_identity(os.fstat(base_descriptor)) != base_identity
                or _artifact_identity(artifact_root.lstat()) != artifact_identity
                or _artifact_identity(os.fstat(artifact_descriptor)) != artifact_identity
                or qualifier(base_runtime, artifact_root) != authority
            ):
                raise TopologyError("external authority changed during qualification")
        except TopologyError:
            raise
        except Exception as exc:
            raise TopologyError(
                "external authority changed during qualification",
            ) from exc

    return ExternalAuthoritySession(
        "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS", "VALID",
        "AUTHORITY_COMPLETE_VALIDATED", authority,
        (base_descriptor, artifact_descriptor), postcheck,
    )


@contextmanager
def _retained_external_authority(
    code: str, *, corpus_root: Path = PHASE3B_ROOT, uv_path: Path = LEGACY_UV,
    legacy_root: Path = REAL_LEGACY_ROOT,
    corpus_validator: Callable[[Path], object] = _default_phase3b_validator,
    expected_uv_sha256: str = LEGACY_UV_SHA256,
    expected_uv_version: str = LEGACY_UV_VERSION,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    nautilus_base_root: Path = NAUTILUS_BASE_RUNTIME,
    nautilus_artifact_root: Path = NAUTILUS_ARTIFACT_ROOT,
    nautilus_qualifier: Callable[[Path, Path], dict[str, object]] = (
        _qualify_nautilus_external_inputs
    ),
):
    if code == "EXT-PHASE3B-CORPUS":
        session = _open_phase3b_external_session(
            corpus_root=corpus_root, corpus_validator=corpus_validator,
        )
    elif code == "EXT-LEGACY-UV-AUTHORITY":
        session = _open_legacy_external_session(
            uv_path=uv_path, legacy_root=legacy_root,
            expected_uv_sha256=expected_uv_sha256,
            expected_uv_version=expected_uv_version, runner=runner,
        )
    elif code == "EXT-NAUTILUS-RUNTIME-CLOSURE-INPUTS":
        session = _open_nautilus_external_session(
            base_runtime=nautilus_base_root,
            artifact_root=nautilus_artifact_root,
            qualifier=nautilus_qualifier,
        )
    else:
        raise TopologyError("unknown external authority")
    try:
        yield session
    finally:
        for descriptor in session.descriptors:
            os.close(descriptor)


def _external_preflight(
    code: str, *, corpus_root: Path = PHASE3B_ROOT, uv_path: Path = LEGACY_UV,
    legacy_root: Path = REAL_LEGACY_ROOT,
    corpus_validator: Callable[[Path], object] = _default_phase3b_validator,
    expected_uv_sha256: str = LEGACY_UV_SHA256,
    expected_uv_version: str = LEGACY_UV_VERSION,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> tuple[str, str]:
    with _retained_external_authority(
        code, corpus_root=corpus_root, uv_path=uv_path, legacy_root=legacy_root,
        corpus_validator=corpus_validator, expected_uv_sha256=expected_uv_sha256,
        expected_uv_version=expected_uv_version, runner=runner,
    ) as session:
        if session.state in {"VALID", "ABSENT"}:
            _postcheck_external_authority(session)
        return session.state, session.fact


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


def _resolve_exact_native_artifact_set(
    marker: Path, expected_receipt_raw: bytes, expected_governance_raw: bytes | None,
) -> None:
    """Resolve ambiguous success from retained bytes, never pathname mutation."""
    with _retained_private_native_artifacts(marker) as artifacts:
        if (
            artifacts.marker_raw != expected_receipt_raw
            or artifacts.bundle_receipt_raw != expected_receipt_raw
            or artifacts.governance_raw != expected_governance_raw
        ):
            raise TopologyError("native acceptance marker is foreign or ambiguous")
        receipt = parse_receipt(expected_receipt_raw)
        _validate_native_manifest_bytes(
            artifacts.manifest_raw, receipt, expected_receipt_raw,
            expected_governance_raw,
        )
        _postcheck_private_native_artifacts(artifacts)


def _publish_native_marker_or_resolve(
    marker: Path, receipt_raw: bytes, governance_raw: bytes | None,
) -> None:
    try:
        _publish_native_acceptance_marker(marker, receipt_raw)
    except (OSError, TopologyError) as exc:
        try:
            _resolve_exact_native_artifact_set(marker, receipt_raw, governance_raw)
        except TopologyError as resolution_error:
            raise TopologyError(
                "native acceptance marker publication is unresolved",
            ) from resolution_error
        return
    _resolve_exact_native_artifact_set(marker, receipt_raw, governance_raw)


def _publish_native_receipt_transaction(
    *, receipt: dict[str, object], evidence_root: Path,
    session: NativeProbeSession | NativeMultiAuthoritySession,
    governance_raw: bytes | None,
) -> Path:
    if (
        receipt.get("outcome") not in {"PASS", "DEFERRED"}
        or (receipt["outcome"] == "PASS") != (governance_raw is not None)
    ):
        raise TopologyError("native receipt outcome/governance shape is invalid")
    topology_root = evidence_root / "capability-topology"
    code = str(receipt["capability_or_authority_code"])
    marker = topology_root / f"{code}.json"
    candidate = _stage_native_candidate(topology_root, receipt, governance_raw)
    _postcheck_native_probe(session)
    _publish_native_candidate_bundle(
        candidate, topology_root / f"{code}.artifacts",
    )
    _postcheck_native_probe(session)
    _publish_native_marker_or_resolve(
        marker, canonical_json_bytes(receipt), governance_raw,
    )
    # Any authority check after this sole acceptance point is diagnostic only and
    # cannot revoke or mutate accepted evidence.
    return marker


def _publish_native_failure_marker(
    *, receipt: dict[str, object], evidence_root: Path,
) -> Path:
    topology_root = evidence_root / "capability-topology"
    code = str(receipt["capability_or_authority_code"])
    marker = topology_root / f"{code}.json"
    bundle = topology_root / f"{code}.artifacts"
    receipt_raw = canonical_json_bytes(receipt)
    if not os.path.lexists(bundle):
        candidate = _stage_native_candidate(topology_root, receipt, None)
        _publish_native_candidate_bundle(candidate, bundle)
        _publish_native_marker_or_resolve(marker, receipt_raw, None)
        return marker
    try:
        _publish_native_acceptance_marker(marker, receipt_raw)
    except (OSError, TopologyError) as exc:
        try:
            if _read_private_regular_file(
                marker, label="native FAIL marker",
            ) != receipt_raw:
                raise TopologyError("native FAIL marker is foreign")
        except TopologyError as resolution_error:
            raise TopologyError("native FAIL marker publication is unresolved") from resolution_error
    return marker


def _resolve_exact_external_artifact_set(
    marker: Path, expected_receipt_raw: bytes,
    expected_governance_raw: bytes | None,
) -> None:
    with _retained_private_native_artifacts(marker) as artifacts:
        if (
            artifacts.marker_raw != expected_receipt_raw
            or artifacts.bundle_receipt_raw != expected_receipt_raw
            or artifacts.governance_raw != expected_governance_raw
        ):
            raise TopologyError("external acceptance marker is foreign or ambiguous")
        receipt = parse_receipt(expected_receipt_raw)
        if receipt.get("schema_version") != EXTERNAL_RECEIPT_SCHEMA:
            raise TopologyError("external acceptance marker has mixed receipt semantics")
        _validate_external_manifest_bytes(
            artifacts.manifest_raw, receipt, expected_receipt_raw,
            expected_governance_raw,
        )
        _postcheck_private_native_artifacts(artifacts)


def _publish_external_marker_or_resolve(
    marker: Path, receipt_raw: bytes, governance_raw: bytes | None,
) -> None:
    try:
        _publish_external_acceptance_marker(marker, receipt_raw)
    except (OSError, TopologyError):
        try:
            _resolve_exact_external_artifact_set(
                marker, receipt_raw, governance_raw,
            )
        except TopologyError as resolution_error:
            raise TopologyError(
                "external acceptance marker publication is unresolved",
            ) from resolution_error
        return
    _resolve_exact_external_artifact_set(marker, receipt_raw, governance_raw)


def _publish_external_receipt_transaction(
    *, receipt: dict[str, object], evidence_root: Path,
    session: ExternalAuthoritySession, governance_raw: bytes | None,
) -> Path:
    topology_root = evidence_root / "capability-topology"
    code = str(receipt["capability_or_authority_code"])
    marker = topology_root / f"{code}.json"
    candidate = _stage_external_candidate(topology_root, receipt, governance_raw)
    _postcheck_external_authority(session)
    _publish_external_candidate_bundle(
        candidate, topology_root / f"{code}.artifacts",
    )
    _postcheck_external_authority(session)
    _publish_external_marker_or_resolve(
        marker, canonical_json_bytes(receipt), governance_raw,
    )
    return marker


def _publish_external_failure_marker(
    *, receipt: dict[str, object], evidence_root: Path,
) -> Path:
    topology_root = evidence_root / "capability-topology"
    code = str(receipt["capability_or_authority_code"])
    marker = topology_root / f"{code}.json"
    bundle = topology_root / f"{code}.artifacts"
    receipt_raw = canonical_json_bytes(receipt)
    if not os.path.lexists(bundle):
        candidate = _stage_external_candidate(topology_root, receipt, None)
        _publish_external_candidate_bundle(candidate, bundle)
        _publish_external_marker_or_resolve(marker, receipt_raw, None)
        return marker
    try:
        _publish_external_acceptance_marker(marker, receipt_raw)
    except (OSError, TopologyError):
        try:
            if _read_private_regular_file(
                marker, label="external FAIL marker",
            ) != receipt_raw:
                raise TopologyError("external FAIL marker is foreign")
        except TopologyError as resolution_error:
            raise TopologyError(
                "external FAIL marker publication is unresolved",
            ) from resolution_error
    return marker


def _execute_native_pass_transaction(
    *, baseline: dict[str, object], expected: tuple[str, ...],
    evidence_root: Path, context: dict[str, object], code: str,
    session: NativeProbeSession,
    exact_runner: Callable[[tuple[str, ...], Path], tuple[str, ...]],
) -> Path:
    """Publish an append-only bundle, then its sole canonical acceptance marker."""
    topology_root = evidence_root / "capability-topology"
    execution_root = topology_root / (
        f".native-execution-{code}-{secrets.token_hex(16)}"
    )
    _prepare_private_evidence_directory(execution_root)
    governance = execution_root / NATIVE_BUNDLE_GOVERNANCE
    selected_count = len(expected)
    try:
        selected = _execute_exact_with_retained_custody(
            baseline=baseline, nodes=expected, report=governance,
            runner=exact_runner, retain_provisional=True,
            append_only_native_diagnostic=True,
        )
        custody = _validate_custody_policy(baseline["collector_policy"])
        governance_raw = _read_private_regular_file(
            governance, label="native PASS governance staging record",
        )
        _validate_exact_governance_bytes(governance_raw, expected, custody)
        passed_receipt = make_native_receipt(
            context=context, code=code, expected=expected, collected=selected,
            session=session, outcome="PASS", selected_test_count=selected_count,
            passed=selected_count, failed=0, unavailable=0,
        )
        return _publish_native_receipt_transaction(
            receipt=passed_receipt, evidence_root=evidence_root,
            session=session, governance_raw=governance_raw,
        )
    except Exception as exc:
        if os.path.lexists(topology_root / f"{code}.json"):
            raise
        identity_drift = isinstance(exc, TopologyError) and (
            "native executable identity changed" in str(exc)
        )
        failure = make_native_receipt(
            context=context, code=code, expected=expected, collected=(),
            session=session, outcome="FAIL", selected_test_count=selected_count,
            passed=0, failed=selected_count, unavailable=0,
            fact=(
                "NATIVE_IDENTITY_REPLACED"
                if identity_drift else "NATIVE_EXACT_TEST_FAILURE"
            ),
        )
        _publish_native_failure_marker(receipt=failure, evidence_root=evidence_root)
        if isinstance(exc, TopologyError):
            raise
        raise TopologyError("native exact transaction failed") from exc


def _execute_external_pass_transaction(
    *, baseline: dict[str, object], expected: tuple[str, ...],
    evidence_root: Path, context: dict[str, object], code: str,
    session: ExternalAuthoritySession,
    exact_runner: Callable[[tuple[str, ...], Path], tuple[str, ...]],
) -> Path:
    topology_root = evidence_root / "capability-topology"
    execution_root = topology_root / (
        f".external-execution-{code}-{secrets.token_hex(16)}"
    )
    _prepare_private_evidence_directory(execution_root)
    governance = execution_root / NATIVE_BUNDLE_GOVERNANCE
    selected_count = len(expected)
    try:
        selected = _execute_exact_with_retained_custody(
            baseline=baseline, nodes=expected, report=governance,
            runner=exact_runner, retain_provisional=True,
            append_only_native_diagnostic=True,
        )
        custody = _validate_custody_policy(baseline["collector_policy"])
        governance_raw = _read_private_regular_file(
            governance, label="external PASS governance staging record",
        )
        _validate_exact_governance_bytes(governance_raw, expected, custody)
        passed_receipt = make_external_receipt(
            context=context, code=code, expected=expected, collected=selected,
            session=session, outcome="PASS", selected_test_count=selected_count,
            passed=selected_count, failed=0, unavailable=0,
        )
        return _publish_external_receipt_transaction(
            receipt=passed_receipt, evidence_root=evidence_root,
            session=session, governance_raw=governance_raw,
        )
    except Exception as exc:
        if os.path.lexists(topology_root / f"{code}.json"):
            raise
        authority_drift = isinstance(exc, TopologyError) and (
            "external authority changed" in str(exc)
        )
        failure = make_external_receipt(
            context=context, code=code, expected=expected, collected=(),
            session=session, outcome="FAIL", selected_test_count=selected_count,
            passed=0, failed=selected_count, unavailable=0,
            fact=("AUTHORITY_DRIFTED" if authority_drift else "EXTERNAL_EXACT_TEST_FAILURE"),
            state=("DRIFTED" if authority_drift else "INVALID"),
        )
        _publish_external_failure_marker(
            receipt=failure, evidence_root=evidence_root,
        )
        if isinstance(exc, TopologyError):
            raise
        raise TopologyError("external exact transaction failed") from exc


def run_lane(
    *, lane: str, inventory: Path, evidence_root: Path, run_id: str, head_sha: str,
    exact_runner: Callable[[tuple[str, ...], Path], tuple[str, ...]] = _run_exact,
    foundation_context_path: Path | None = None,
    native_probe_factory: Callable[..., Any] = _retained_native_probe,
    external_session_factory: Callable[..., Any] = _retained_external_authority,
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
    if lane == "native-capabilities" and context is None:
        raise TopologyError("native capability receipts require sealed Foundation context")
    if lane == "external-authorities" and context is None:
        raise TopologyError("external authority receipts require sealed Foundation context")
    publications: list[Path] = []
    for code in sorted(CODE_CLASSIFICATION):
        expected_lane, expected = _expected_rows(rows, code)
        if expected_lane != lane:
            continue
        if lane == "native-capabilities":
            assert context is not None
            with native_probe_factory(code) as session:
                if (
                    not isinstance(
                        session, (NativeProbeSession, NativeMultiAuthoritySession),
                    )
                    or session.code != code
                ):
                    raise TopologyError("native probe returned an invalid capability session")
                if session.state == "BROKEN":
                    failure = make_native_receipt(
                        context=context, code=code, expected=expected, collected=(),
                        session=session, outcome="FAIL", selected_test_count=0,
                        passed=0, failed=0, unavailable=0,
                    )
                    publications.append(_publish_native_failure_marker(
                        receipt=failure, evidence_root=evidence_root,
                    ))
                    raise TopologyError(f"{code} preflight is BROKEN")
                if session.state == "UNAVAILABLE":
                    try:
                        deferred = make_native_receipt(
                            context=context, code=code, expected=expected, collected=(),
                            session=session, outcome="DEFERRED", selected_test_count=0,
                            passed=0, failed=0, unavailable=len(expected),
                        )
                        publications.append(_publish_native_receipt_transaction(
                            receipt=deferred, evidence_root=evidence_root,
                            session=session, governance_raw=None,
                        ))
                    except TopologyError:
                        failure = make_native_receipt(
                            context=context, code=code, expected=expected, collected=(),
                            session=session, outcome="FAIL", selected_test_count=0,
                            passed=0, failed=0, unavailable=0,
                            fact="NATIVE_IDENTITY_REPLACED",
                        )
                        publications.append(_publish_native_failure_marker(
                            receipt=failure, evidence_root=evidence_root,
                        ))
                        raise
                    continue
                if session.state != "AVAILABLE":
                    raise TopologyError("native probe returned an unknown state")
                baseline = load_portable_root_baseline(
                    inventory=inventory, evidence_root=evidence_root, run_id=run_id,
                    head_sha=head_sha, foundation_context_path=foundation_context_path,
                )
                publications.append(_execute_native_pass_transaction(
                    baseline=baseline, expected=expected, evidence_root=evidence_root,
                    context=context, code=code, session=session,
                    exact_runner=exact_runner,
                ))
                continue
        assert context is not None
        with external_session_factory(code) as session:
            if not isinstance(session, ExternalAuthoritySession) or session.code != code:
                raise TopologyError("external preflight returned an invalid authority session")
            if session.state in {"PARTIAL", "INVALID", "DRIFTED"}:
                failure = make_external_receipt(
                    context=context, code=code, expected=expected, collected=(),
                    session=session, outcome="FAIL", selected_test_count=0,
                    passed=0, failed=0, unavailable=0,
                )
                publications.append(_publish_external_failure_marker(
                    receipt=failure, evidence_root=evidence_root,
                ))
                raise TopologyError(f"{code} preflight is {session.state}")
            if session.state == "ABSENT":
                deferred = make_external_receipt(
                    context=context, code=code, expected=expected, collected=(),
                    session=session, outcome="DEFERRED", selected_test_count=0,
                    passed=0, failed=0, unavailable=len(expected),
                )
                try:
                    publications.append(_publish_external_receipt_transaction(
                        receipt=deferred, evidence_root=evidence_root,
                        session=session, governance_raw=None,
                    ))
                except TopologyError as exc:
                    if "external authority changed" in str(exc):
                        failure = make_external_receipt(
                            context=context, code=code, expected=expected,
                            collected=(), session=session, outcome="FAIL",
                            selected_test_count=0, passed=0, failed=0,
                            unavailable=0, fact="AUTHORITY_DRIFTED",
                            state="DRIFTED",
                        )
                        publications.append(_publish_external_failure_marker(
                            receipt=failure, evidence_root=evidence_root,
                        ))
                    raise
                continue
            if session.state != "VALID":
                raise TopologyError("external preflight returned an unknown state")
            baseline = load_portable_root_baseline(
                inventory=inventory, evidence_root=evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=foundation_context_path,
            )
            publications.append(_execute_external_pass_transaction(
                baseline=baseline, expected=expected, evidence_root=evidence_root,
                context=context, code=code, session=session,
                exact_runner=exact_runner,
            ))
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
        "check-closure", "validate-native", "validate-external", "aggregate",
    ))
    parser.add_argument("--lane", choices=tuple(CLASSIFICATION_LANE.values()))
    parser.add_argument("--inventory", type=Path, default=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--foundation-context-path", type=Path, required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_id, head_sha = _active_foundation_identity()
        context = load_foundation_context(
            args.foundation_context_path,
            run_id=run_id,
            head_sha=head_sha,
        )
        if args.require_pass and args.action not in {"validate-native", "validate-external"}:
            raise TopologyError(
                "--require-pass is valid only for native or external validation",
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
        elif args.action == "validate-native":
            rows = _installed_inventory_rows(args.inventory, args.evidence_root)
            baseline = load_portable_root_baseline(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )
            sealed_custody = _validate_custody_policy(baseline["collector_policy"])
            topology_root = args.evidence_root / "capability-topology"
            print(validate_native_artifacts(
                topology_root, rows=rows, foundation_context=context,
                sealed_custody=sealed_custody, require_pass=args.require_pass,
            ))
        elif args.action == "validate-external":
            rows = _installed_inventory_rows(args.inventory, args.evidence_root)
            baseline = load_portable_root_baseline(
                inventory=args.inventory, evidence_root=args.evidence_root,
                run_id=run_id, head_sha=head_sha,
                foundation_context_path=args.foundation_context_path,
            )
            sealed_custody = _validate_custody_policy(baseline["collector_policy"])
            topology_root = args.evidence_root / "capability-topology"
            print(validate_external_artifacts(
                topology_root, rows=rows, foundation_context=context,
                sealed_custody=sealed_custody, require_pass=args.require_pass,
            ))
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
