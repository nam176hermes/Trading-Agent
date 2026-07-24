"""Approval-gated two-cluster restore verification for the 0007 authority.

The executable test body skips before a harness call unless a later exact
``DISPOSABLE_PG_GREEN`` record binds the fixed restore operation identifier.
The remaining tests are source-only static contracts.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Iterator, NoReturn

import psycopg
import pytest

from packages.job_authority import (
    CatalogEvidence,
    capture_catalog,
    find_event_chain_violations,
    load_frozen_contract,
)
from packages.restore_proof_failure_codes import (
    NO_RESTORE_PROOF_CODE,
    RESTORE_PROOF_FAILURE_CODES as STANDALONE_RESTORE_PROOF_FAILURE_CODES,
    RESTORE_PROOF_REPORTABLE_FAILURE_CODES as STANDALONE_RESTORE_PROOF_REPORTABLE_FAILURE_CODES,
    extract_restore_proof_failure_code,
)
from tests.jobs._postgres import (
    _disposable_postgres_session,
    _prepare_empty_restore_target,
    _prepare_disposable_database,
    _run_pg_dump,
    _run_pg_restore,
    _upgrade_to_revision,
    _validated_session,
    disposable_role_settings,
)
from trading_control.db import DatabaseSettings


RESTORE_OPERATION_ID = "jobs-transition-restore-green-v1"
EXACT_HEAD = "0007_job_event_chain_authority"
EXACT_0004_HEAD = "0004_durable_research_jobs"
DATABASE_NAME = "trading_agent"
SOURCE_CLUSTER_LABEL = "source"
TARGET_CLUSTER_LABEL = "target"
CUSTOM_DUMP_ARGUMENTS = ("--format=custom", "--create")
RESTORE_ARGUMENTS = (
    "--exit-on-error",
    "--use-set-session-authorization",
    f"--dbname={DATABASE_NAME}",
)
FORBIDDEN_GLOBAL_DUMP_ARGUMENTS = ("--globals-only", "--roles-only")
ROOT = Path(__file__).parents[2]
HARNESS_PATH = ROOT / "tests/jobs/_postgres.py"
CONTRACT = load_frozen_contract(
    ROOT / "ops/postgres/job-plane-authority/query-contract-v1.json"
)
REVIEWED_CATALOG_SHA256 = (
    "1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40"
)
FROZEN_CATALOG_0007_SNAPSHOT = (
    ROOT / "ops/postgres/job-plane-authority/catalog-0007-v1.snapshot"
)
RESTORE_PROOF_FAILURE_EXTRACTOR_PATH = (
    ROOT / "scripts/extract_restore_proof_failure_code.py"
)
_STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST = frozenset({"sys"})
_STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST = frozenset(
    {"__future__", "pathlib", "packages.restore_proof_failure_codes"}
)
_STANDALONE_CODES_DIRECT_IMPORT_ALLOWLIST = frozenset({"re"})
_STANDALONE_CODES_FROM_IMPORT_ALLOWLIST = frozenset({"__future__", "collections.abc"})
GLOBAL_ROLE_NAMES = (
    "trading_owner",
    "trading_migrator",
    "trading_reader",
    "trading_jobs",
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)
RUNTIME_ROLES = (
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
)
EXPECTED_ROLE_SETTINGS_AFTER_PROVISION = (
    ("trading_job_api", None, "timezone", "UTC"),
    ("trading_job_scheduler", None, "timezone", "UTC"),
    ("trading_job_worker", None, "timezone", "UTC"),
    ("trading_migrator", DATABASE_NAME, "timezone", "UTC"),
    ("trading_owner", DATABASE_NAME, "timezone", "UTC"),
    ("trading_reader", DATABASE_NAME, "default_transaction_read_only", "on"),
    ("trading_reader", DATABASE_NAME, "timezone", "UTC"),
)
EXPECTED_ROLE_SETTINGS_AFTER_TARGET_DROP = (
    ("trading_job_api", None, "timezone", "UTC"),
    ("trading_job_scheduler", None, "timezone", "UTC"),
    ("trading_job_worker", None, "timezone", "UTC"),
)
REQUIRED_RESTORE_ASSERTIONS = (
    "independently_provisioned_global_roles",
    "sanitized_role_settings_separate_from_catalog_digest",
    "database_acl_separate_from_globals",
    "exact_0007_head",
    "reviewed_catalog_digest",
    "zero_event_chain_violations",
    "runtime_direct_dml_denial",
    "append_only_event_denial",
    "row_counts",
)
RESTORE_PROOF_INVALID_FAILURE_CODE = "RESTORE_PROOF_INVALID_FAILURE_CODE"
RESTORE_PROOF_SOURCE_EXACT_HEAD_MISMATCH = "RESTORE_PROOF_SOURCE_EXACT_HEAD_MISMATCH"
RESTORE_PROOF_TARGET_BASE_EXACT_HEAD_MISMATCH = (
    "RESTORE_PROOF_TARGET_BASE_EXACT_HEAD_MISMATCH"
)
RESTORE_PROOF_TARGET_RESTORED_EXACT_HEAD_MISMATCH = (
    "RESTORE_PROOF_TARGET_RESTORED_EXACT_HEAD_MISMATCH"
)
RESTORE_PROOF_SOURCE_ROLE_SETTINGS_MISMATCH = (
    "RESTORE_PROOF_SOURCE_ROLE_SETTINGS_MISMATCH"
)
RESTORE_PROOF_TARGET_ROLE_SETTINGS_MISMATCH = (
    "RESTORE_PROOF_TARGET_ROLE_SETTINGS_MISMATCH"
)
RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH = (
    "RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH"
)
RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH = (
    "RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH"
)
RESTORE_PROOF_TARGET_PROVISIONED_GLOBAL_ROLE_EVIDENCE_MISMATCH = (
    "RESTORE_PROOF_TARGET_PROVISIONED_GLOBAL_ROLE_EVIDENCE_MISMATCH"
)
RESTORE_PROOF_TARGET_POST_DROP_GLOBAL_ROLE_EVIDENCE_MISMATCH = (
    "RESTORE_PROOF_TARGET_POST_DROP_GLOBAL_ROLE_EVIDENCE_MISMATCH"
)
RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH = (
    "RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH"
)
RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH = (
    "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH"
)
RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE = (
    "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE"
)
RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_NON_CANONICAL_EVIDENCE = (
    "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_NON_CANONICAL_EVIDENCE"
)
RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH = (
    "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH"
)
RESTORE_PROOF_RESTORED_ROW_COUNTS_MISMATCH = (
    "RESTORE_PROOF_RESTORED_ROW_COUNTS_MISMATCH"
)
RESTORE_PROOF_POST_DENIAL_ROW_COUNTS_MISMATCH = (
    "RESTORE_PROOF_POST_DENIAL_ROW_COUNTS_MISMATCH"
)
RESTORE_PROOF_ROW_COUNT_EVIDENCE_MISSING = (
    "RESTORE_PROOF_ROW_COUNT_EVIDENCE_MISSING"
)
RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH = (
    "RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH"
)
RESTORE_PROOF_RESTORED_EVENT_CHAIN_MISMATCH = (
    "RESTORE_PROOF_RESTORED_EVENT_CHAIN_MISMATCH"
)
RESTORE_PROOF_POST_DENIAL_CATALOG_DIGEST_MISMATCH = (
    "RESTORE_PROOF_POST_DENIAL_CATALOG_DIGEST_MISMATCH"
)
RESTORE_PROOF_POST_DENIAL_EVENT_CHAIN_MISMATCH = (
    "RESTORE_PROOF_POST_DENIAL_EVENT_CHAIN_MISMATCH"
)
RESTORE_PROOF_RESTORED_CATALOG_DATABASE_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_DATABASE_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_SCHEMA_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_SCHEMA_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_OBJECT_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_OBJECT_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_OBJECT_OWNER_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_OBJECT_OWNER_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_OBJECT_IDENTITY_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_OBJECT_IDENTITY_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_COLUMN_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_COLUMN_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_CONSTRAINT_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_CONSTRAINT_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_INDEX_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_INDEX_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_SEQUENCE_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_SEQUENCE_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_FUNCTION_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_FUNCTION_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_TRIGGER_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_TRIGGER_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_POLICY_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_POLICY_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_DEFAULT_ACL_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_DEFAULT_ACL_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_ROLE_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_ROLE_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_MEMBERSHIP_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_MEMBERSHIP_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_ROLE_SETTING_DRIFT = (
    "RESTORE_PROOF_RESTORED_CATALOG_ROLE_SETTING_DRIFT"
)
RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN = (
    "RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN"
)
RESTORE_PROOF_CLUSTER_ROOT_ISOLATION_MISMATCH = (
    "RESTORE_PROOF_CLUSTER_ROOT_ISOLATION_MISMATCH"
)
RESTORE_PROOF_CLUSTER_DATA_ISOLATION_MISMATCH = (
    "RESTORE_PROOF_CLUSTER_DATA_ISOLATION_MISMATCH"
)
RESTORE_PROOF_CLUSTER_PORT_ISOLATION_MISMATCH = (
    "RESTORE_PROOF_CLUSTER_PORT_ISOLATION_MISMATCH"
)
RESTORE_PROOF_TARGET_DROP_MAINTENANCE_CONTEXT_MISMATCH = (
    "RESTORE_PROOF_TARGET_DROP_MAINTENANCE_CONTEXT_MISMATCH"
)
RESTORE_PROOF_TARGET_DATABASE_DROP_MISMATCH = (
    "RESTORE_PROOF_TARGET_DATABASE_DROP_MISMATCH"
)
RESTORE_PROOF_SOURCE_PREPARATION_DUMP_FAILURE = (
    "RESTORE_PROOF_SOURCE_PREPARATION_DUMP_FAILURE"
)
RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE = (
    "RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE"
)
RESTORE_PROOF_RESTORE_EXECUTION_FAILURE = (
    "RESTORE_PROOF_RESTORE_EXECUTION_FAILURE"
)
RESTORE_PROOF_POST_RESTORE_VERIFICATION_FAILURE = (
    "RESTORE_PROOF_POST_RESTORE_VERIFICATION_FAILURE"
)
RESTORE_PROOF_FAILURE_CODES = STANDALONE_RESTORE_PROOF_FAILURE_CODES
RESTORE_PROOF_REPORTABLE_FAILURE_CODES = STANDALONE_RESTORE_PROOF_REPORTABLE_FAILURE_CODES
CATALOG_DRIFT_CODE_BY_KIND = {
    "database": RESTORE_PROOF_RESTORED_CATALOG_DATABASE_DRIFT,
    "schema": RESTORE_PROOF_RESTORED_CATALOG_SCHEMA_DRIFT,
    "object": RESTORE_PROOF_RESTORED_CATALOG_OBJECT_DRIFT,
    "column": RESTORE_PROOF_RESTORED_CATALOG_COLUMN_DRIFT,
    "constraint": RESTORE_PROOF_RESTORED_CATALOG_CONSTRAINT_DRIFT,
    "index": RESTORE_PROOF_RESTORED_CATALOG_INDEX_DRIFT,
    "sequence": RESTORE_PROOF_RESTORED_CATALOG_SEQUENCE_DRIFT,
    "function": RESTORE_PROOF_RESTORED_CATALOG_FUNCTION_DRIFT,
    "trigger": RESTORE_PROOF_RESTORED_CATALOG_TRIGGER_DRIFT,
    "policy": RESTORE_PROOF_RESTORED_CATALOG_POLICY_DRIFT,
    "default_acl": RESTORE_PROOF_RESTORED_CATALOG_DEFAULT_ACL_DRIFT,
    "role": RESTORE_PROOF_RESTORED_CATALOG_ROLE_DRIFT,
    "membership": RESTORE_PROOF_RESTORED_CATALOG_MEMBERSHIP_DRIFT,
    "role_setting": RESTORE_PROOF_RESTORED_CATALOG_ROLE_SETTING_DRIFT,
}
_OBJECT_CATALOG_FIELDS = frozenset(
    {
        "acl",
        "force_row_security",
        "kind",
        "name",
        "owner",
        "persistence",
        "relation_kind",
        "replica_identity",
        "row_security",
        "schema",
    }
)
# C-stable field order for one paired object identity with multiple differences.
_OBJECT_CATALOG_FIELD_DRIFT_CODES = (
    ("acl", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT),
    ("force_row_security", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT),
    ("owner", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_OWNER_DRIFT),
    ("persistence", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT),
    ("relation_kind", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT),
    ("replica_identity", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT),
    ("row_security", RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT),
)
def test_green_restore_contract_requires_two_clusters_and_prepared_target() -> None:
    assert RESTORE_OPERATION_ID == "jobs-transition-restore-green-v1"
    assert EXACT_HEAD == "0007_job_event_chain_authority"
    assert DATABASE_NAME == "trading_agent"
    assert SOURCE_CLUSTER_LABEL != TARGET_CLUSTER_LABEL
    assert CUSTOM_DUMP_ARGUMENTS == ("--format=custom", "--create")
    assert RESTORE_ARGUMENTS == (
        "--exit-on-error",
        "--use-set-session-authorization",
        "--dbname=trading_agent",
    )
    assert all(
        forbidden not in CUSTOM_DUMP_ARGUMENTS + RESTORE_ARGUMENTS
        for forbidden in FORBIDDEN_GLOBAL_DUMP_ARGUMENTS
    )
    assert REQUIRED_RESTORE_ASSERTIONS == (
        "independently_provisioned_global_roles",
        "sanitized_role_settings_separate_from_catalog_digest",
        "database_acl_separate_from_globals",
        "exact_0007_head",
        "reviewed_catalog_digest",
        "zero_event_chain_violations",
        "runtime_direct_dml_denial",
        "append_only_event_denial",
        "row_counts",
    )


def test_restore_proof_equality_diagnostic_is_fail_closed_and_nonrevealing() -> None:
    _assert_restore_proof_equal(
        ("same",),
        ("same",),
        RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH,
    )

    with pytest.raises(AssertionError) as mismatch:
        _assert_restore_proof_equal(
            ("source-evidence",),
            ("target-evidence",),
            RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH,
        )
    assert str(mismatch.value) == RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH
    assert "source-evidence" not in str(mismatch.value)
    assert "target-evidence" not in str(mismatch.value)

    with pytest.raises(AssertionError) as invalid_code:
        _assert_restore_proof_equal(
            ("equal",),
            ("equal",),
            "unapproved-diagnostic-code",
        )
    assert str(invalid_code.value) == RESTORE_PROOF_INVALID_FAILURE_CODE

    with pytest.raises(AssertionError) as acl_diagnostic:
        _raise_restore_proof_failure(
            RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE,
        )
    assert (
        str(acl_diagnostic.value)
        == RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE
    )


def test_restore_proof_stage_guard_preserves_known_fixed_codes() -> None:
    with pytest.raises(AssertionError) as known_failure:
        with _guard_restore_proof_stage(RESTORE_PROOF_SOURCE_PREPARATION_DUMP_FAILURE):
            _raise_restore_proof_failure(RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH)
    assert str(known_failure.value) == RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH

    with pytest.raises(AssertionError) as invalid_stage:
        with _guard_restore_proof_stage("unapproved-stage-code"):
            pytest.fail("invalid stage guard proceeded")
    assert str(invalid_stage.value) == RESTORE_PROOF_INVALID_FAILURE_CODE


@pytest.mark.parametrize(
    "uncoded_error",
    (
        AssertionError("uncoded assertion evidence"),
        RuntimeError("uncoded runtime evidence"),
        psycopg.OperationalError("uncoded database evidence"),
    ),
    ids=("assertion", "runtime", "database"),
)
def test_restore_proof_stage_guard_redacts_uncoded_exceptions(
    uncoded_error: Exception,
) -> None:
    with pytest.raises(AssertionError) as stage_failure:
        with _guard_restore_proof_stage(RESTORE_PROOF_RESTORE_EXECUTION_FAILURE):
            raise uncoded_error
    assert str(stage_failure.value) == RESTORE_PROOF_RESTORE_EXECUTION_FAILURE
    assert "uncoded" not in str(stage_failure.value)
    assert stage_failure.value.__suppress_context__ is True


def test_restore_proof_stage_guard_does_not_catch_base_exception() -> None:
    with pytest.raises(KeyboardInterrupt):
        with _guard_restore_proof_stage(RESTORE_PROOF_POST_RESTORE_VERIFICATION_FAILURE):
            raise KeyboardInterrupt()


def test_restore_proof_output_extractor_retains_only_one_known_code() -> None:
    known_code = RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE
    assert extract_restore_proof_failure_code(
        (
            "ordinary pytest context",
            f"E   AssertionError: {known_code}",
            "other pytest context",
        )
    ) == known_code
    assert extract_restore_proof_failure_code(
        (f"E   AssertionError: {known_code} untrusted surrounding text",)
    ) == NO_RESTORE_PROOF_CODE
    assert extract_restore_proof_failure_code(
        ("E   AssertionError: RESTORE_PROOF_UNKNOWN_STAGE",)
    ) == NO_RESTORE_PROOF_CODE
    assert extract_restore_proof_failure_code(
        (
            f"E   AssertionError: {known_code}",
            f"E   AssertionError: {RESTORE_PROOF_RESTORE_EXECUTION_FAILURE}",
        )
    ) == NO_RESTORE_PROOF_CODE
    for catalog_drift_code in (
        RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT,
        RESTORE_PROOF_RESTORED_CATALOG_OBJECT_OWNER_DRIFT,
        RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT,
        RESTORE_PROOF_RESTORED_CATALOG_OBJECT_IDENTITY_DRIFT,
    ):
        assert extract_restore_proof_failure_code(
            (f"E   AssertionError: {catalog_drift_code}",)
        ) == catalog_drift_code


@pytest.mark.parametrize(
    "failure_code",
    sorted(STANDALONE_RESTORE_PROOF_REPORTABLE_FAILURE_CODES),
)
def test_standalone_restore_proof_extractor_accepts_every_allowlisted_code(
    failure_code: str,
) -> None:
    assert (
        extract_restore_proof_failure_code(
            (
                "private surrounding text",
                f"E   AssertionError: {failure_code}",
                "more private surrounding text",
            )
        )
        == failure_code
    )


@pytest.mark.parametrize(
    "capture_lines",
    (
        (),
        ("ordinary private text",),
        (
            f"E   AssertionError: {RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE}",
            f"E   AssertionError: {RESTORE_PROOF_RESTORE_EXECUTION_FAILURE}",
        ),
        ("E   AssertionError: RESTORE_PROOF_UNKNOWN_STAGE",),
        (
            f"E   AssertionError: {RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE} trailing",
        ),
        (
            f"private E   AssertionError: {RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE}",
        ),
    ),
    ids=("zero", "unrelated", "multiple", "unknown", "trailing", "embedded"),
)
def test_standalone_restore_proof_extractor_rejects_private_or_ambiguous_text(
    capture_lines: tuple[str, ...],
) -> None:
    assert extract_restore_proof_failure_code(capture_lines) == NO_RESTORE_PROOF_CODE


def test_standalone_restore_proof_extractor_rejects_malformed_iterable() -> None:
    assert extract_restore_proof_failure_code((object(),)) == NO_RESTORE_PROOF_CODE


def test_standalone_restore_proof_extractor_cli_emits_only_code_or_sentinel(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "private-capture.txt"
    expected_code = RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT
    capture.write_text(
        "private capture text\n"
        f"E   AssertionError: {expected_code}\n"
        "private catalog-like text\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(RESTORE_PROOF_FAILURE_EXTRACTOR_PATH),
            "--input",
            str(capture),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == f"{expected_code}\n"
    assert result.stderr == ""

    capture.write_bytes(b"\xff")
    malformed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(RESTORE_PROOF_FAILURE_EXTRACTOR_PATH),
            "--input",
            str(capture),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert malformed.returncode == 0
    assert malformed.stdout == f"{NO_RESTORE_PROOF_CODE}\n"
    assert malformed.stderr == ""

    private_marker = "private-value-must-not-appear"
    capture.write_text(private_marker + "\n", encoding="utf-8")
    rejected = subprocess.run(
        [
            sys.executable,
            "-I",
            str(RESTORE_PROOF_FAILURE_EXTRACTOR_PATH),
            "--input",
            str(capture),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 0
    assert rejected.stdout == f"{NO_RESTORE_PROOF_CODE}\n"
    assert rejected.stderr == ""
    assert private_marker not in rejected.stdout
    assert private_marker not in rejected.stderr

    malformed_arguments = subprocess.run(
        [sys.executable, "-I", str(RESTORE_PROOF_FAILURE_EXTRACTOR_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert malformed_arguments.returncode == 0
    assert malformed_arguments.stdout == f"{NO_RESTORE_PROOF_CODE}\n"
    assert malformed_arguments.stderr == ""


def _import_module_sets(
    tree: ast.AST,
) -> tuple[frozenset[str], frozenset[str], bool]:
    direct_imports = frozenset(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    from_imports = frozenset(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module is not None
    )
    has_relative_import = any(
        isinstance(node, ast.ImportFrom) and node.level != 0
        for node in ast.walk(tree)
    )
    return direct_imports, from_imports, has_relative_import


def test_restore_proof_executor_contract_requires_standalone_extractor() -> None:
    assert RESTORE_PROOF_FAILURE_EXTRACTOR_PATH.is_file()
    extractor_source = RESTORE_PROOF_FAILURE_EXTRACTOR_PATH.read_text(encoding="utf-8")
    extractor_tree = ast.parse(
        extractor_source,
        filename=str(RESTORE_PROOF_FAILURE_EXTRACTOR_PATH),
    )
    (
        extractor_direct_imports,
        extractor_from_imports,
        extractor_has_relative_import,
    ) = _import_module_sets(extractor_tree)

    assert extractor_direct_imports == _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST
    assert extractor_from_imports == _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST
    assert not extractor_has_relative_import
    assert "--input" in extractor_source

    codes_module_path = ROOT / "packages/restore_proof_failure_codes.py"
    codes_tree = ast.parse(
        codes_module_path.read_text(encoding="utf-8"),
        filename=str(codes_module_path),
    )
    (
        code_module_direct_imports,
        code_module_from_imports,
        code_module_has_relative_import,
    ) = _import_module_sets(codes_tree)
    assert code_module_direct_imports == _STANDALONE_CODES_DIRECT_IMPORT_ALLOWLIST
    assert code_module_from_imports == _STANDALONE_CODES_FROM_IMPORT_ALLOWLIST
    assert not code_module_has_relative_import


def test_restore_proof_executor_contract_checks_direct_script_imports() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    contract_source = ast.get_source_segment(
        source,
        functions["test_restore_proof_executor_contract_requires_standalone_extractor"],
    )
    import_helper_source = ast.get_source_segment(source, functions["_import_module_sets"])

    assert contract_source is not None
    assert import_helper_source is not None
    assert "extractor_direct_imports" in contract_source
    assert "_import_module_sets" in contract_source
    assert "ast.Import" in import_helper_source
    assert "ast.ImportFrom" in import_helper_source
    assert "node.level" in import_helper_source


@pytest.mark.parametrize(
    ("source", "direct_allowlist", "from_allowlist", "expected_allowed"),
    (
        (
            "from __future__ import annotations\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from packages.restore_proof_failure_codes import NO_RESTORE_PROOF_CODE\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            True,
        ),
        (
            "from __future__ import annotations\n"
            "from collections.abc import Iterable\n"
            "import re\n",
            _STANDALONE_CODES_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_CODES_FROM_IMPORT_ALLOWLIST,
            True,
        ),
        (
            "import tests.jobs.test_job_transition_restore\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            False,
        ),
        (
            "from tests.jobs import test_job_transition_restore\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            False,
        ),
        (
            "import third_party_dependency\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            False,
        ),
        (
            "from third_party_dependency import parser\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            False,
        ),
        (
            "from . import helper\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            False,
        ),
        (
            "from .helpers import helper\n",
            _STANDALONE_EXTRACTOR_DIRECT_IMPORT_ALLOWLIST,
            _STANDALONE_EXTRACTOR_FROM_IMPORT_ALLOWLIST,
            False,
        ),
    ),
    ids=(
        "extractor-stdlib",
        "codes-stdlib",
        "direct-test-module",
        "from-test-module",
        "direct-third-party",
        "from-third-party",
        "bare-relative-import",
        "named-relative-import",
    ),
)
def test_standalone_extractor_import_allowlists_reject_unapproved_modules(
    source: str,
    direct_allowlist: frozenset[str],
    from_allowlist: frozenset[str],
    expected_allowed: bool,
) -> None:
    direct_imports, from_imports, has_relative_import = _import_module_sets(
        ast.parse(source)
    )
    assert (
        direct_imports <= direct_allowlist
        and from_imports <= from_allowlist
        and not has_relative_import
    ) is expected_allowed


def test_restore_proof_allowlist_is_single_sourced_with_standalone_extractor() -> None:
    local_failure_codes = frozenset(
        value
        for name, value in globals().items()
        if name.startswith("RESTORE_PROOF_")
        and name
        not in {
            "RESTORE_PROOF_INVALID_FAILURE_CODE",
            "RESTORE_PROOF_NO_CODE_SENTINEL",
        }
        and isinstance(value, str)
    )
    assert RESTORE_PROOF_FAILURE_CODES is STANDALONE_RESTORE_PROOF_FAILURE_CODES
    assert RESTORE_PROOF_REPORTABLE_FAILURE_CODES is (
        STANDALONE_RESTORE_PROOF_REPORTABLE_FAILURE_CODES
    )
    assert local_failure_codes == STANDALONE_RESTORE_PROOF_FAILURE_CODES
    assert STANDALONE_RESTORE_PROOF_REPORTABLE_FAILURE_CODES == (
        STANDALONE_RESTORE_PROOF_FAILURE_CODES
        | frozenset({RESTORE_PROOF_INVALID_FAILURE_CODE})
    )


def _synthetic_catalog_bytes(*records: dict[str, object]) -> bytes:
    lines = [
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
        for record in records
    ]
    return b"\n".join(sorted(lines)) + b"\n"


def _synthetic_catalog_evidence(canonical_bytes: bytes) -> CatalogEvidence:
    return CatalogEvidence(
        query_id="synthetic-catalog",
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        row_count=canonical_bytes.count(b"\n"),
        canonical_bytes=canonical_bytes,
    )


def _synthetic_object_record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "acl": ["reader=read"],
        "force_row_security": False,
        "kind": "object",
        "name": "synthetic_relation",
        "owner": "synthetic_owner",
        "persistence": "p",
        "relation_kind": "r",
        "replica_identity": "d",
        "row_security": False,
        "schema": "synthetic_schema",
    }
    record.update(changes)
    return record


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    (
        ("database", "RESTORE_PROOF_RESTORED_CATALOG_DATABASE_DRIFT"),
        ("schema", "RESTORE_PROOF_RESTORED_CATALOG_SCHEMA_DRIFT"),
        ("column", "RESTORE_PROOF_RESTORED_CATALOG_COLUMN_DRIFT"),
        ("constraint", "RESTORE_PROOF_RESTORED_CATALOG_CONSTRAINT_DRIFT"),
        ("index", "RESTORE_PROOF_RESTORED_CATALOG_INDEX_DRIFT"),
        ("sequence", "RESTORE_PROOF_RESTORED_CATALOG_SEQUENCE_DRIFT"),
        ("function", "RESTORE_PROOF_RESTORED_CATALOG_FUNCTION_DRIFT"),
        ("trigger", "RESTORE_PROOF_RESTORED_CATALOG_TRIGGER_DRIFT"),
        ("policy", "RESTORE_PROOF_RESTORED_CATALOG_POLICY_DRIFT"),
        ("default_acl", "RESTORE_PROOF_RESTORED_CATALOG_DEFAULT_ACL_DRIFT"),
        ("role", "RESTORE_PROOF_RESTORED_CATALOG_ROLE_DRIFT"),
        ("membership", "RESTORE_PROOF_RESTORED_CATALOG_MEMBERSHIP_DRIFT"),
        ("role_setting", "RESTORE_PROOF_RESTORED_CATALOG_ROLE_SETTING_DRIFT"),
    ),
)
def test_catalog_drift_classifier_emits_each_stable_surface_code(
    kind: str,
    expected_code: str,
) -> None:
    frozen_snapshot = _synthetic_catalog_bytes({"kind": kind, "marker": "a"})
    captured_bytes = _synthetic_catalog_bytes({"kind": kind, "marker": "b"})

    failure_code = _classify_catalog_drift(
        _synthetic_catalog_evidence(captured_bytes),
        frozen_snapshot,
        hashlib.sha256(frozen_snapshot).hexdigest(),
    )

    assert failure_code == expected_code
    assert failure_code in RESTORE_PROOF_FAILURE_CODES
    assert "marker" not in failure_code


@pytest.mark.parametrize(
    ("field", "captured_value", "expected_code"),
    (
        (
            "acl",
            ["reader=read", "writer=write"],
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT,
        ),
        (
            "acl",
            None,
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT,
        ),
        (
            "owner",
            "different_owner",
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_OWNER_DRIFT,
        ),
        (
            "relation_kind",
            "v",
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT,
        ),
        (
            "persistence",
            "u",
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT,
        ),
        (
            "row_security",
            True,
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT,
        ),
        (
            "force_row_security",
            True,
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT,
        ),
        (
            "replica_identity",
            "f",
            RESTORE_PROOF_RESTORED_CATALOG_OBJECT_METADATA_DRIFT,
        ),
    ),
)
def test_catalog_drift_classifier_refines_paired_object_drift(
    field: str,
    captured_value: object,
    expected_code: str,
) -> None:
    frozen_snapshot = _synthetic_catalog_bytes(_synthetic_object_record())
    captured_bytes = _synthetic_catalog_bytes(
        _synthetic_object_record(**{field: captured_value})
    )

    failure_code = _classify_catalog_drift(
        _synthetic_catalog_evidence(captured_bytes),
        frozen_snapshot,
        hashlib.sha256(frozen_snapshot).hexdigest(),
    )

    assert failure_code == expected_code
    assert failure_code in RESTORE_PROOF_FAILURE_CODES
    assert "synthetic_" not in failure_code


def test_catalog_drift_classifier_uses_c_stable_first_object_identity() -> None:
    frozen_snapshot = _synthetic_catalog_bytes(
        _synthetic_object_record(
            acl=["z=read"],
            name="alpha",
        ),
        _synthetic_object_record(
            acl=["a=read"],
            name="beta",
        ),
    )
    captured_bytes = _synthetic_catalog_bytes(
        _synthetic_object_record(
            acl=["z=read"],
            name="alpha",
            owner="changed_owner",
        ),
        _synthetic_object_record(
            acl=["b=read"],
            name="beta",
        ),
    )

    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(captured_bytes),
            frozen_snapshot,
            hashlib.sha256(frozen_snapshot).hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_OBJECT_OWNER_DRIFT
    )


def test_catalog_drift_classifier_uses_c_stable_first_object_field() -> None:
    frozen_snapshot = _synthetic_catalog_bytes(_synthetic_object_record())
    captured_bytes = _synthetic_catalog_bytes(
        _synthetic_object_record(
            acl=["reader=read", "writer=write"],
            owner="changed_owner",
        )
    )

    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(captured_bytes),
            frozen_snapshot,
            hashlib.sha256(frozen_snapshot).hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT
    )


@pytest.mark.parametrize("removed_from", ("captured", "frozen"))
def test_catalog_drift_classifier_reports_unpaired_object_identity(
    removed_from: str,
) -> None:
    object_record = _synthetic_object_record()
    anchor = {"kind": "database", "marker": "anchor"}
    frozen_records = (anchor, object_record) if removed_from == "captured" else (anchor,)
    captured_records = (anchor,) if removed_from == "captured" else (anchor, object_record)
    frozen_snapshot = _synthetic_catalog_bytes(*frozen_records)
    captured_bytes = _synthetic_catalog_bytes(*captured_records)

    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(captured_bytes),
            frozen_snapshot,
            hashlib.sha256(frozen_snapshot).hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_OBJECT_IDENTITY_DRIFT
    )


@pytest.mark.parametrize(
    "malformed_record",
    (
        _synthetic_object_record(acl="not-a-list"),
        _synthetic_object_record(name=""),
        _synthetic_object_record(unexpected="field"),
    ),
    ids=("acl-type", "empty-identity", "unexpected-field"),
)
def test_catalog_drift_classifier_redacts_malformed_object_fields(
    malformed_record: dict[str, object],
) -> None:
    frozen_snapshot = _synthetic_catalog_bytes(_synthetic_object_record())
    captured_bytes = _synthetic_catalog_bytes(malformed_record)

    failure_code = _classify_catalog_drift(
        _synthetic_catalog_evidence(captured_bytes),
        frozen_snapshot,
        hashlib.sha256(frozen_snapshot).hexdigest(),
    )
    assert failure_code == RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    assert "synthetic_" not in failure_code
    assert "not-a-list" not in failure_code


def test_catalog_drift_classifier_rejects_missing_object_fields() -> None:
    frozen_snapshot = _synthetic_catalog_bytes(_synthetic_object_record())
    malformed_record = _synthetic_object_record()
    del malformed_record["owner"]
    captured_bytes = _synthetic_catalog_bytes(malformed_record)

    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(captured_bytes),
            frozen_snapshot,
            hashlib.sha256(frozen_snapshot).hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    )


def test_catalog_drift_classifier_redacts_ambiguous_object_identity() -> None:
    frozen_snapshot = _synthetic_catalog_bytes(
        _synthetic_object_record(owner="first_owner"),
        _synthetic_object_record(owner="second_owner"),
    )
    captured_bytes = _synthetic_catalog_bytes(_synthetic_object_record())

    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(captured_bytes),
            frozen_snapshot,
            hashlib.sha256(frozen_snapshot).hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    )


def test_object_catalog_drift_rejects_authority_without_exposing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen_snapshot = _synthetic_catalog_bytes(_synthetic_object_record())
    captured_bytes = _synthetic_catalog_bytes(
        _synthetic_object_record(acl=["reader=read", "writer=write"])
    )
    snapshot_path = tmp_path / "catalog.snapshot"
    snapshot_path.write_bytes(frozen_snapshot)

    class _Connection:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class _Owner:
        def conninfo(self) -> str:
            return "synthetic-no-connect"

    monkeypatch.setattr(psycopg, "connect", lambda _conninfo: _Connection())
    monkeypatch.setitem(
        globals(),
        "capture_catalog",
        lambda _connection, _contract: _synthetic_catalog_evidence(captured_bytes),
    )
    monkeypatch.setitem(
        globals(),
        "find_event_chain_violations",
        lambda _connection, _contract: (),
    )
    monkeypatch.setitem(globals(), "FROZEN_CATALOG_0007_SNAPSHOT", snapshot_path)
    monkeypatch.setitem(
        globals(),
        "REVIEWED_CATALOG_SHA256",
        hashlib.sha256(frozen_snapshot).hexdigest(),
    )

    with pytest.raises(AssertionError) as failure:
        _assert_reviewed_authority(
            _Owner(),  # type: ignore[arg-type]
            RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH,
            RESTORE_PROOF_RESTORED_EVENT_CHAIN_MISMATCH,
        )
    assert str(failure.value) == RESTORE_PROOF_RESTORED_CATALOG_OBJECT_ACL_DRIFT
    assert "reader=read" not in str(failure.value)
    assert "writer=write" not in str(failure.value)


def test_catalog_drift_classifier_is_bound_to_snapshot_digest_and_falls_back_safely() -> None:
    frozen_snapshot = _synthetic_catalog_bytes({"kind": "database", "marker": "a"})
    captured = _synthetic_catalog_evidence(frozen_snapshot)
    reviewed_digest = hashlib.sha256(frozen_snapshot).hexdigest()

    assert _classify_catalog_drift(captured, frozen_snapshot, reviewed_digest) is None

    unexpected_equal_bytes = CatalogEvidence(
        query_id=captured.query_id,
        sha256="a" * 64,
        row_count=captured.row_count,
        canonical_bytes=captured.canonical_bytes,
    )
    assert (
        _classify_catalog_drift(
            unexpected_equal_bytes,
            frozen_snapshot,
            reviewed_digest,
        )
        == RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH
    )

    different_bytes = _synthetic_catalog_bytes({"kind": "database", "marker": "b"})
    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(different_bytes),
            frozen_snapshot,
            hashlib.sha256(b"incorrect-binding").hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    )


@pytest.mark.parametrize(
    "captured_bytes",
    (
        b"not-json\n",
        _synthetic_catalog_bytes({"kind": "unrecognized", "marker": "a"}),
    ),
    ids=("malformed", "unknown-kind"),
)
def test_catalog_drift_classifier_redacts_malformed_or_unknown_evidence(
    captured_bytes: bytes,
) -> None:
    frozen_snapshot = _synthetic_catalog_bytes({"kind": "schema", "marker": "a"})
    failure_code = _classify_catalog_drift(
        _synthetic_catalog_evidence(captured_bytes),
        frozen_snapshot,
        hashlib.sha256(frozen_snapshot).hexdigest(),
    )
    assert failure_code == RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    assert "json" not in failure_code
    assert "unrecognized" not in failure_code


def test_catalog_drift_classifier_uses_first_c_stable_surface_only() -> None:
    frozen_snapshot = _synthetic_catalog_bytes(
        {"kind": "database", "marker": "a"},
        {"kind": "role", "marker": "a"},
    )
    captured_bytes = _synthetic_catalog_bytes(
        {"kind": "database", "marker": "b"},
        {"kind": "role", "marker": "b"},
    )

    assert (
        _classify_catalog_drift(
            _synthetic_catalog_evidence(captured_bytes),
            frozen_snapshot,
            hashlib.sha256(frozen_snapshot).hexdigest(),
        )
        == RESTORE_PROOF_RESTORED_CATALOG_DATABASE_DRIFT
    )


def test_catalog_drift_runtime_contract_uses_only_the_committed_snapshot() -> None:
    frozen_snapshot = FROZEN_CATALOG_0007_SNAPSHOT.read_bytes()
    assert hashlib.sha256(frozen_snapshot).hexdigest() == (
        REVIEWED_CATALOG_SHA256
    )
    assert (
        _catalog_drift_code_from_canonical_bytes(frozen_snapshot, frozen_snapshot)
        is None
    )
    frozen_lines = frozen_snapshot[:-1].split(b"\n")
    assert _classify_object_catalog_drift(frozen_lines, frozen_lines) is None

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reviewed_classifier_source = ast.get_source_segment(
        source,
        functions["_classify_reviewed_catalog_drift"],
    )
    classifier_source = ast.get_source_segment(
        source,
        functions["_classify_catalog_drift"],
    )
    byte_classifier_source = ast.get_source_segment(
        source,
        functions["_catalog_drift_code_from_canonical_bytes"],
    )
    object_signature_source = ast.get_source_segment(
        source,
        functions["_object_catalog_signature"],
    )
    object_classifier_source = ast.get_source_segment(
        source,
        functions["_classify_object_catalog_drift"],
    )
    authority_source = ast.get_source_segment(
        source,
        functions["_assert_reviewed_authority"],
    )
    assert reviewed_classifier_source is not None
    assert classifier_source is not None
    assert byte_classifier_source is not None
    assert object_signature_source is not None
    assert object_classifier_source is not None
    assert authority_source is not None
    assert "FROZEN_CATALOG_0007_SNAPSHOT.read_bytes()" in reviewed_classifier_source
    assert "REVIEWED_CATALOG_SHA256" in reviewed_classifier_source
    assert "hashlib.sha256(frozen_snapshot).hexdigest()" in classifier_source
    assert "_classify_reviewed_catalog_drift(catalog, digest_failure_code)" in (
        authority_source
    )
    assert "print(" not in byte_classifier_source
    assert "return line" not in byte_classifier_source
    assert "return record" not in byte_classifier_source
    assert "print(" not in object_signature_source
    assert "return document" not in object_signature_source
    assert "print(" not in object_classifier_source
    assert "return identity" not in object_classifier_source


def test_future_green_restore_proof_has_four_nonleaking_stage_guards() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    guard_source = ast.get_source_segment(source, functions["_guard_restore_proof_stage"])
    green_test = functions["test_green_restore_round_trip_preserves_0007_authority"]
    assert guard_source is not None
    assert "except Exception as error:" in guard_source
    assert "BaseException" not in guard_source
    assert "_is_restore_proof_failure(error)" in guard_source
    assert "_raise_restore_proof_failure(failure_code)" in guard_source
    assert "raise\n" not in guard_source

    stage_codes = {
        call.args[0].id
        for call in ast.walk(green_test)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_guard_restore_proof_stage"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
    }
    assert stage_codes == {
        "RESTORE_PROOF_SOURCE_PREPARATION_DUMP_FAILURE",
        "RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE",
        "RESTORE_PROOF_RESTORE_EXECUTION_FAILURE",
        "RESTORE_PROOF_POST_RESTORE_VERIFICATION_FAILURE",
    }

    green_source = ast.get_source_segment(source, green_test)
    assert green_source is not None
    source_stage = green_source.index("RESTORE_PROOF_SOURCE_PREPARATION_DUMP_FAILURE")
    source_prepare = green_source.index("_prepare_disposable_database(source_session)")
    source_dump = green_source.index("_run_pg_dump(source_session)")
    target_stage = green_source.index("RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE")
    target_prepare = green_source.index("_prepare_disposable_database(target_session)")
    target_drop = green_source.index("_drop_only_target_database(target_maintenance)")
    restore_stage = green_source.index("RESTORE_PROOF_RESTORE_EXECUTION_FAILURE")
    restore_run = green_source.index("_run_pg_restore(target_session)")
    post_restore_stage = green_source.index(
        "RESTORE_PROOF_POST_RESTORE_VERIFICATION_FAILURE"
    )
    post_restore_verification = green_source.index(
        "RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH"
    )
    assert source_stage < source_prepare < source_dump < target_stage
    assert target_stage < target_prepare < target_drop < restore_stage
    assert restore_stage < restore_run < post_restore_stage < post_restore_verification


def test_sensitive_evidence_helpers_cannot_bypass_fixed_diagnostics() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    failure_source = ast.get_source_segment(
        source,
        functions["_raise_restore_proof_failure"],
    )
    acl_source = ast.get_source_segment(
        source,
        functions["_canonical_effective_acl_rows"],
    )
    row_counts_source = ast.get_source_segment(
        source,
        functions["_capture_row_counts"],
    )
    assert failure_source is not None
    assert acl_source is not None
    assert row_counts_source is not None
    assert "RESTORE_PROOF_FAILURE_CODES" in failure_source
    assert "raise AssertionError(failure_code)" in failure_source
    assert "raise " not in acl_source
    assert "assert " not in acl_source
    assert acl_source.count("_raise_restore_proof_failure(") == 3
    acl_failure_codes = {
        "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE",
        "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_NON_CANONICAL_EVIDENCE",
        "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH",
    }
    required_failure_codes = acl_failure_codes | {
        "RESTORE_PROOF_ROW_COUNT_EVIDENCE_MISSING",
    }
    assert required_failure_codes == {
        failure_code
        for failure_code in RESTORE_PROOF_FAILURE_CODES
        if failure_code in required_failure_codes
    }
    for failure_code in acl_failure_codes:
        assert failure_code in acl_source
        assert failure_code in source
    assert "assert counts is not None" not in row_counts_source
    assert "raise " not in row_counts_source
    assert "assert " not in row_counts_source
    assert "_raise_restore_proof_failure(" in row_counts_source
    assert "RESTORE_PROOF_ROW_COUNT_EVIDENCE_MISSING" in row_counts_source
    assert "RESTORE_PROOF_ROW_COUNT_EVIDENCE_MISSING" in source


def test_future_green_restore_proof_uses_fixed_equality_diagnostics() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    helper_source = ast.get_source_segment(
        source,
        functions["_assert_restore_proof_equal"],
    )
    green_source = ast.get_source_segment(
        source,
        functions["test_green_restore_round_trip_preserves_0007_authority"],
    )
    assert helper_source is not None
    assert green_source is not None
    assert "if actual != expected:" in helper_source
    assert "_raise_restore_proof_failure(failure_code)" in helper_source
    assert "raise AssertionError" not in helper_source

    green_helper_codes = {
        call.args[2].id
        for call in ast.walk(functions["test_green_restore_round_trip_preserves_0007_authority"])
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_assert_restore_proof_equal"
        and len(call.args) == 3
        and isinstance(call.args[2], ast.Name)
    }
    assert {
        "RESTORE_PROOF_SOURCE_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_CLUSTER_ROOT_ISOLATION_MISMATCH",
        "RESTORE_PROOF_CLUSTER_DATA_ISOLATION_MISMATCH",
        "RESTORE_PROOF_CLUSTER_PORT_ISOLATION_MISMATCH",
        "RESTORE_PROOF_TARGET_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_DROP_GLOBAL_ROLE_EVIDENCE_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH",
        "RESTORE_PROOF_RESTORED_ROW_COUNTS_MISMATCH",
        "RESTORE_PROOF_POST_DENIAL_ROW_COUNTS_MISMATCH",
    }.issubset(green_helper_codes)

    for failure_code in (
        "RESTORE_PROOF_SOURCE_EXACT_HEAD_MISMATCH",
        "RESTORE_PROOF_TARGET_BASE_EXACT_HEAD_MISMATCH",
        "RESTORE_PROOF_TARGET_RESTORED_EXACT_HEAD_MISMATCH",
        "RESTORE_PROOF_SOURCE_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_TARGET_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH",
        "RESTORE_PROOF_TARGET_PROVISIONED_GLOBAL_ROLE_EVIDENCE_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_DROP_GLOBAL_ROLE_EVIDENCE_MISMATCH",
        "RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH",
        "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH",
        "RESTORE_PROOF_RESTORED_ROW_COUNTS_MISMATCH",
        "RESTORE_PROOF_POST_DENIAL_ROW_COUNTS_MISMATCH",
        "RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH",
        "RESTORE_PROOF_RESTORED_EVENT_CHAIN_MISMATCH",
        "RESTORE_PROOF_POST_DENIAL_CATALOG_DIGEST_MISMATCH",
        "RESTORE_PROOF_POST_DENIAL_EVENT_CHAIN_MISMATCH",
    ):
        assert failure_code in green_source

    for function_name in (
        "_assert_exact_head",
        "_capture_provisioned_globals",
        "_capture_sanitized_role_settings",
        "_assert_reviewed_authority",
        "_drop_only_target_database",
    ):
        function_source = ast.get_source_segment(source, functions[function_name])
        assert function_source is not None
        assert "_assert_restore_proof_equal(" in function_source


def test_real_harness_restore_builders_use_a_prepared_disposable_target() -> None:
    harness_source = HARNESS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(harness_source, filename=str(HARNESS_PATH))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    dump_source = ast.get_source_segment(
        harness_source,
        functions["_build_pg_dump_command"],
    )
    restore_source = ast.get_source_segment(
        harness_source,
        functions["_build_pg_restore_command"],
    )
    assert dump_source is not None
    assert restore_source is not None
    assert '"--format=custom",\n            "--create",' in dump_source
    assert (
        '"--exit-on-error",\n'
        '            "--use-set-session-authorization",\n'
        '            f"--dbname={DISPOSABLE_DATABASE}",'
        in restore_source
    )
    assert '"--create"' not in restore_source
    assert all(
        forbidden not in dump_source + restore_source
        for forbidden in FORBIDDEN_GLOBAL_DUMP_ARGUMENTS
    )


def test_database_acl_contract_requires_effective_semantic_evidence() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_canonical_effective_acl_rows" in functions
    acl_source = ast.get_source_segment(source, functions["_capture_database_acl"])
    assert acl_source is not None
    assert "pg_catalog.aclexplode" in acl_source
    assert "pg_catalog.acldefault" in acl_source
    assert "coalesce(" in acl_source
    assert "CASE WHEN acl_entry.grantee = 0 THEN 'PUBLIC'" in acl_source
    assert "acl_entry.is_grantable" in acl_source
    assert 'COLLATE "C"' in acl_source
    assert "array_to_string" not in acl_source


def test_effective_acl_evidence_normalizes_only_semantically_equal_rows() -> None:
    null_acl_effective_rows = [
        ("DATABASE", DATABASE_NAME, "PUBLIC", "trading_owner", "CONNECT", False),
        ("SCHEMA", "public", "PUBLIC", "trading_owner", "USAGE", False),
        ("SCHEMA", "job_plane", "trading_owner", "trading_owner", "USAGE", True),
        ("RELATION", "jobs", "trading_owner", "trading_owner", "SELECT", True),
        (
            "RELATION",
            "job_attempts",
            "trading_owner",
            "trading_owner",
            "SELECT",
            True,
        ),
        (
            "RELATION",
            "job_events",
            "trading_owner",
            "trading_owner",
            "SELECT",
            True,
        ),
        (
            "RELATION",
            "scheduler_heartbeats",
            "trading_owner",
            "trading_owner",
            "SELECT",
            True,
        ),
        (
            "RELATION",
            "job_artifacts",
            "trading_owner",
            "trading_owner",
            "SELECT",
            True,
        ),
        (
            "RELATION",
            "worker_heartbeats",
            "trading_owner",
            "trading_owner",
            "SELECT",
            True,
        ),
    ]
    explicit_equivalent_rows = list(reversed(null_acl_effective_rows))

    expected = _canonical_effective_acl_rows(null_acl_effective_rows)
    assert _canonical_effective_acl_rows(explicit_equivalent_rows) == expected

    changed_grantee = list(null_acl_effective_rows)
    changed_grantee[6] = (
        "RELATION",
        "scheduler_heartbeats",
        "trading_reader",
        "trading_owner",
        "SELECT",
        True,
    )
    assert _canonical_effective_acl_rows(changed_grantee) != expected

    changed_privilege = list(null_acl_effective_rows)
    changed_privilege[6] = (
        "RELATION",
        "scheduler_heartbeats",
        "trading_owner",
        "trading_owner",
        "UPDATE",
        True,
    )
    assert _canonical_effective_acl_rows(changed_privilege) != expected

    changed_grant_option = list(null_acl_effective_rows)
    changed_grant_option[6] = (
        "RELATION",
        "scheduler_heartbeats",
        "trading_owner",
        "trading_owner",
        "SELECT",
        False,
    )
    assert _canonical_effective_acl_rows(changed_grant_option) != expected

    changed_grantor = list(null_acl_effective_rows)
    changed_grantor[6] = (
        "RELATION",
        "scheduler_heartbeats",
        "trading_owner",
        "trading_reader",
        "SELECT",
        True,
    )
    assert _canonical_effective_acl_rows(changed_grantor) != expected

    changed_object = list(null_acl_effective_rows)
    changed_object[6] = (
        "RELATION",
        "unexpected_relation",
        "trading_owner",
        "trading_owner",
        "SELECT",
        True,
    )
    with pytest.raises(AssertionError) as changed_object_error:
        _canonical_effective_acl_rows(changed_object)
    assert (
        str(changed_object_error.value)
        == RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH
    )

    with pytest.raises(AssertionError) as malformed_rows_error:
        _canonical_effective_acl_rows([("malformed",)])
    assert (
        str(malformed_rows_error.value)
        == RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE
    )

    with pytest.raises(AssertionError) as duplicate_rows_error:
        _canonical_effective_acl_rows(null_acl_effective_rows + [null_acl_effective_rows[0]])
    assert (
        str(duplicate_rows_error.value)
        == RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_NON_CANONICAL_EVIDENCE
    )


def test_future_restore_contract_requires_safe_role_settings_and_precreated_dumps() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "_capture_sanitized_role_settings",
        "_secure_precreated_dump",
        "_copy_precreated_custom_dump",
    }.issubset(functions)
    green_source = ast.get_source_segment(
        source,
        functions["test_green_restore_round_trip_preserves_0007_authority"],
    )
    assert green_source is not None
    assert green_source.index("_secure_precreated_dump(source_session)") < (
        green_source.index("_validate_secure_precreated_dump(source_dump)")
    ) < green_source.index("_run_pg_dump(source_session)")
    target_precreate = green_source.index("_secure_precreated_dump(target_session)")
    target_copy = green_source.index("_copy_precreated_custom_dump(")
    target_validation_after_copy = green_source.index(
        "_validate_secure_precreated_dump(target_dump)",
        target_copy,
    )
    assert target_precreate < target_copy < target_validation_after_copy
    assert target_validation_after_copy < green_source.index("_run_pg_restore(target_session)")
    target_provision_settings = green_source.index(
        "target_role_settings = _capture_sanitized_role_settings("
    )
    target_drop_settings = green_source.index(
        "RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH"
    )
    restored_settings = green_source.index(
        "RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH",
        green_source.index("_run_pg_restore(target_session)"),
    )
    assert target_provision_settings < target_drop_settings < restored_settings

    settings_source = ast.get_source_segment(
        source,
        functions["_capture_sanitized_role_settings"],
    )
    secure_source = ast.get_source_segment(
        source,
        functions["_secure_precreated_dump"],
    )
    copy_source = ast.get_source_segment(
        source,
        functions["_copy_precreated_custom_dump"],
    )
    assert settings_source is not None
    assert secure_source is not None
    assert copy_source is not None
    assert "pg_catalog.pg_db_role_setting" in settings_source
    assert "safe_value" in settings_source
    assert "is_safe" in settings_source
    assert "rolpassword" not in settings_source
    assert "os.O_CREAT" in secure_source
    assert "os.O_EXCL" in secure_source
    assert "os.O_NOFOLLOW" in secure_source
    assert "dir_fd=directory_fd" in secure_source
    assert "0o600" in secure_source
    assert secure_source.index("os.fchmod(file_fd, 0o600)") < secure_source.index(
        "yield secure_dump"
    )
    assert "os.O_NOFOLLOW" in copy_source
    assert copy_source.index("_validate_secure_dump_fd(target_dump, target_fd)") < (
        copy_source.index("os.ftruncate(target_fd, 0)")
    )


def _require_green_restore_authority() -> None:
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or not os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", "").strip()
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN restore authority is absent")


def _maintenance_settings(owner: DatabaseSettings) -> DatabaseSettings:
    return replace(owner, database="postgres")


def _raise_restore_proof_failure(failure_code: str) -> NoReturn:
    if failure_code not in RESTORE_PROOF_FAILURE_CODES:
        raise AssertionError(RESTORE_PROOF_INVALID_FAILURE_CODE) from None
    raise AssertionError(failure_code) from None


def _is_restore_proof_failure(error: Exception) -> bool:
    return (
        type(error) is AssertionError
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in RESTORE_PROOF_REPORTABLE_FAILURE_CODES
    )


@contextmanager
def _guard_restore_proof_stage(failure_code: str) -> Iterator[None]:
    if failure_code not in RESTORE_PROOF_FAILURE_CODES:
        _raise_restore_proof_failure(failure_code)
    try:
        yield
    except Exception as error:
        if _is_restore_proof_failure(error):
            _raise_restore_proof_failure(error.args[0])
        _raise_restore_proof_failure(failure_code)


def _strict_catalog_json_object(
    pairs: list[tuple[object, object]],
) -> dict[str, object]:
    if any(not isinstance(key, str) for key, _value in pairs):
        raise ValueError
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


def _catalog_line_kind(line: bytes) -> str | None:
    try:
        record = json.loads(
            line.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_catalog_json_object,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    kind = record.get("kind")
    if not isinstance(kind, str) or kind not in CATALOG_DRIFT_CODE_BY_KIND:
        return None
    return kind


def _catalog_lines_are_canonical(lines: list[bytes]) -> bool:
    return bool(lines) and all(lines) and lines == sorted(lines) and len(set(lines)) == len(
        lines
    )


def _object_catalog_signature(
    line: bytes,
) -> tuple[tuple[bytes, bytes], tuple[bytes, ...]] | None:
    try:
        document = json.loads(
            line.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_catalog_json_object,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or set(document) != _OBJECT_CATALOG_FIELDS:
        return None
    if document.get("kind") != "object":
        return None

    schema = document["schema"]
    name = document["name"]
    owner = document["owner"]
    relation_kind = document["relation_kind"]
    persistence = document["persistence"]
    replica_identity = document["replica_identity"]
    acl = document["acl"]
    text_fields = (
        schema,
        name,
        owner,
        relation_kind,
        persistence,
        replica_identity,
    )
    if (
        not all(
            isinstance(value, str) and value
            for value in text_fields
        )
        or type(document["row_security"]) is not bool
        or type(document["force_row_security"]) is not bool
        or (
            acl is not None
            and (
                not isinstance(acl, list)
                or any(not isinstance(value, str) for value in acl)
            )
        )
    ):
        return None
    try:
        for value in text_fields:
            value.encode("utf-8", errors="strict")
        if isinstance(acl, list):
            for value in acl:
                value.encode("utf-8", errors="strict")
        identity = (
            schema.encode("utf-8", errors="strict"),
            name.encode("utf-8", errors="strict"),
        )
        field_digests = tuple(
            hashlib.sha256(
                json.dumps(
                    document[field],
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8", errors="strict")
            ).digest()
            for field, _failure_code in _OBJECT_CATALOG_FIELD_DRIFT_CODES
        )
    except (TypeError, UnicodeEncodeError, ValueError):
        return None
    return identity, field_digests


def _catalog_object_signatures(
    lines: list[bytes],
) -> dict[tuple[bytes, bytes], tuple[bytes, ...]] | None:
    signatures: dict[tuple[bytes, bytes], tuple[bytes, ...]] = {}
    for line in lines:
        if _catalog_line_kind(line) != "object":
            continue
        signature = _object_catalog_signature(line)
        if signature is None:
            return None
        identity, field_digests = signature
        if identity in signatures:
            return None
        signatures[identity] = field_digests
    return signatures


def _classify_object_catalog_drift(
    expected_lines: list[bytes],
    captured_lines: list[bytes],
) -> str | None:
    expected_objects = _catalog_object_signatures(expected_lines)
    captured_objects = _catalog_object_signatures(captured_lines)
    if expected_objects is None or captured_objects is None:
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN

    unpaired_identities = sorted(set(expected_objects) ^ set(captured_objects))
    if unpaired_identities:
        return RESTORE_PROOF_RESTORED_CATALOG_OBJECT_IDENTITY_DRIFT

    differing_identities = sorted(
        identity
        for identity in expected_objects
        if expected_objects[identity] != captured_objects[identity]
    )
    if not differing_identities:
        return None
    expected_digests = expected_objects[differing_identities[0]]
    captured_digests = captured_objects[differing_identities[0]]
    for index, (_field, failure_code) in enumerate(
        _OBJECT_CATALOG_FIELD_DRIFT_CODES
    ):
        if expected_digests[index] != captured_digests[index]:
            return failure_code
    return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN


def _catalog_drift_code_from_canonical_bytes(
    expected_canonical_bytes: object,
    captured_canonical_bytes: object,
) -> str | None:
    if (
        not isinstance(expected_canonical_bytes, bytes)
        or not isinstance(captured_canonical_bytes, bytes)
        or not expected_canonical_bytes
        or not captured_canonical_bytes
        or not expected_canonical_bytes.endswith(b"\n")
        or not captured_canonical_bytes.endswith(b"\n")
        or expected_canonical_bytes.endswith(b"\n\n")
        or captured_canonical_bytes.endswith(b"\n\n")
        or b"\r" in expected_canonical_bytes
        or b"\r" in captured_canonical_bytes
    ):
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    expected_lines = expected_canonical_bytes[:-1].split(b"\n")
    captured_lines = captured_canonical_bytes[:-1].split(b"\n")
    if not _catalog_lines_are_canonical(expected_lines) or not _catalog_lines_are_canonical(
        captured_lines
    ):
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN

    expected_kinds: dict[bytes, str] = {}
    for line in expected_lines:
        kind = _catalog_line_kind(line)
        if kind is None:
            return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
        expected_kinds[line] = kind
    captured_kinds: dict[bytes, str] = {}
    for line in captured_lines:
        kind = _catalog_line_kind(line)
        if kind is None:
            return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
        captured_kinds[line] = kind

    differing_lines = sorted(set(expected_kinds) ^ set(captured_kinds))
    if not differing_lines:
        return None
    first_kind = expected_kinds.get(differing_lines[0]) or captured_kinds.get(
        differing_lines[0]
    )
    if first_kind is None:
        return None
    if first_kind == "object":
        return _classify_object_catalog_drift(expected_lines, captured_lines)
    return CATALOG_DRIFT_CODE_BY_KIND.get(first_kind)


def _classify_catalog_drift(
    catalog: object,
    frozen_snapshot: object,
    reviewed_digest: object,
    fallback_failure_code: str = RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH,
) -> str | None:
    if not isinstance(catalog, CatalogEvidence):
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    if catalog.sha256 == reviewed_digest:
        return None
    if (
        not isinstance(frozen_snapshot, bytes)
        or not isinstance(reviewed_digest, str)
        or hashlib.sha256(frozen_snapshot).hexdigest() != reviewed_digest
        or fallback_failure_code not in RESTORE_PROOF_FAILURE_CODES
    ):
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    if (
        not isinstance(catalog.canonical_bytes, bytes)
        or type(catalog.row_count) is not int
    ):
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    if catalog.row_count != catalog.canonical_bytes.count(b"\n"):
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    if catalog.canonical_bytes == frozen_snapshot:
        return fallback_failure_code
    if catalog.sha256 != hashlib.sha256(catalog.canonical_bytes).hexdigest():
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    drift_code = _catalog_drift_code_from_canonical_bytes(
        frozen_snapshot,
        catalog.canonical_bytes,
    )
    if drift_code is None:
        return fallback_failure_code
    return drift_code


def _classify_reviewed_catalog_drift(
    catalog: CatalogEvidence,
    fallback_failure_code: str,
) -> str:
    try:
        frozen_snapshot = FROZEN_CATALOG_0007_SNAPSHOT.read_bytes()
    except OSError:
        return RESTORE_PROOF_RESTORED_CATALOG_MALFORMED_OR_UNKNOWN
    failure_code = _classify_catalog_drift(
        catalog,
        frozen_snapshot,
        REVIEWED_CATALOG_SHA256,
        fallback_failure_code,
    )
    if failure_code is None:
        return fallback_failure_code
    return failure_code


def _assert_restore_proof_equal(
    actual: object,
    expected: object,
    failure_code: str,
) -> None:
    if failure_code not in RESTORE_PROOF_FAILURE_CODES:
        _raise_restore_proof_failure(failure_code)
    if actual != expected:
        _raise_restore_proof_failure(failure_code)


def _assert_exact_head(
    owner: DatabaseSettings,
    expected: str,
    failure_code: str,
) -> None:
    with psycopg.connect(owner.conninfo()) as connection:
        rows = connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchall()
    _assert_restore_proof_equal(rows, [(expected,)], failure_code)


def _capture_provisioned_globals(
    maintenance: DatabaseSettings,
    failure_code: str,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
    with psycopg.connect(maintenance.conninfo()) as connection:
        roles = connection.execute(
            """
            SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolinherit, rolreplication, rolbypassrls
            FROM pg_catalog.pg_roles
            WHERE rolname = ANY(%s)
            ORDER BY rolname
            """,
            (list(GLOBAL_ROLE_NAMES),),
        ).fetchall()
        memberships = connection.execute(
            """
            SELECT granted_role.rolname, member_role.rolname
            FROM pg_catalog.pg_auth_members membership
            JOIN pg_catalog.pg_roles granted_role
              ON granted_role.oid = membership.roleid
            JOIN pg_catalog.pg_roles member_role
              ON member_role.oid = membership.member
            WHERE granted_role.rolname = ANY(%s)
               OR member_role.rolname = ANY(%s)
            ORDER BY granted_role.rolname, member_role.rolname
            """,
            (list(GLOBAL_ROLE_NAMES), list(GLOBAL_ROLE_NAMES)),
        ).fetchall()

    _assert_restore_proof_equal(
        [row[0] for row in roles],
        sorted(GLOBAL_ROLE_NAMES),
        failure_code,
    )
    _assert_restore_proof_equal(
        all(
            not value
            for row in roles
            for value in (row[2], row[3], row[4], row[5], row[6], row[7])
        ),
        True,
        failure_code,
    )
    _assert_restore_proof_equal(
        {role_name: can_login for role_name, can_login, *_attributes in roles},
        {
            "trading_owner": True,
            "trading_migrator": True,
            "trading_reader": True,
            "trading_jobs": False,
            "trading_job_api": True,
            "trading_job_worker": True,
            "trading_job_scheduler": True,
        },
        failure_code,
    )
    _assert_restore_proof_equal(memberships, [], failure_code)
    return tuple(roles), tuple(memberships)


def _capture_sanitized_role_settings(
    maintenance: DatabaseSettings,
    failure_code: str,
) -> tuple[tuple[str, str | None, str, str], ...]:
    with psycopg.connect(maintenance.conninfo()) as connection:
        rows = connection.execute(
            """
            WITH raw_settings AS (
              SELECT role_row.rolname AS role_name,
                     CASE WHEN setting_row.setdatabase = 0 THEN NULL::text
                          ELSE database_row.datname
                     END AS database_name,
                     setting_entry.setting_text
              FROM pg_catalog.pg_db_role_setting setting_row
              JOIN pg_catalog.pg_roles role_row
                ON role_row.oid = setting_row.setrole
              LEFT JOIN pg_catalog.pg_database database_row
                ON database_row.oid = setting_row.setdatabase
              CROSS JOIN LATERAL unnest(setting_row.setconfig)
                AS setting_entry(setting_text)
              WHERE role_row.rolname = ANY(%s)
            ),
            setting_keys AS (
              SELECT role_name, database_name,
                     lower(split_part(setting_text, '=', 1)) AS setting_key,
                     setting_text
              FROM raw_settings
            )
            SELECT role_name, database_name, setting_key,
                   CASE WHEN setting_key IN (
                          'default_transaction_read_only',
                          'search_path',
                          'timezone'
                        )
                        THEN split_part(setting_text, '=', 2)
                        ELSE NULL::text
                   END AS safe_value,
                   setting_key IN (
                     'default_transaction_read_only',
                     'search_path',
                     'timezone'
                   ) AS is_safe
            FROM setting_keys
            ORDER BY role_name, database_name NULLS FIRST, setting_key
            """,
            (list(GLOBAL_ROLE_NAMES),),
        ).fetchall()

    _assert_restore_proof_equal(
        all(row[4] is True for row in rows),
        True,
        failure_code,
    )
    return tuple((row[0], row[1], row[2], row[3]) for row in rows)


SELECTED_ACL_OBJECTS = frozenset(
    {
        ("DATABASE", DATABASE_NAME),
        ("SCHEMA", "public"),
        ("SCHEMA", "job_plane"),
        ("RELATION", "jobs"),
        ("RELATION", "job_attempts"),
        ("RELATION", "job_events"),
        ("RELATION", "scheduler_heartbeats"),
        ("RELATION", "job_artifacts"),
        ("RELATION", "worker_heartbeats"),
    }
)


def _canonical_effective_acl_rows(
    rows: list[tuple[object, ...]],
) -> tuple[tuple[str, str, str, str, str, bool], ...]:
    canonical: list[tuple[str, str, str, str, str, bool]] = []
    for row in rows:
        if (
            len(row) != 6
            or any(not isinstance(value, str) or not value for value in row[:5])
            or type(row[5]) is not bool
        ):
            _raise_restore_proof_failure(
                RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MALFORMED_EVIDENCE,
            )
        object_kind, object_name, grantee, grantor, privilege, grantable = row
        canonical.append(
            (object_kind, object_name, grantee, grantor, privilege, grantable)
        )
    canonical.sort(
        key=lambda row: (
            row[0].encode("utf-8"),
            row[1].encode("utf-8"),
            row[2].encode("utf-8"),
            row[3].encode("utf-8"),
            row[4].encode("utf-8"),
            row[5],
        )
    )
    if not canonical or len(set(canonical)) != len(canonical):
        _raise_restore_proof_failure(
            RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_NON_CANONICAL_EVIDENCE,
        )
    if {(object_kind, object_name) for object_kind, object_name, *_ in canonical} != (
        SELECTED_ACL_OBJECTS
    ):
        _raise_restore_proof_failure(
            RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_SELECTED_OBJECTS_MISMATCH,
        )
    return tuple(canonical)


def _capture_database_acl(
    owner: DatabaseSettings,
) -> tuple[tuple[str, str, str, str, str, bool], ...]:
    with psycopg.connect(owner.conninfo()) as connection:
        rows = connection.execute(
            """
            WITH selected_objects AS (
              SELECT 'DATABASE'::text AS object_kind,
                     database_row.datname AS object_name,
                     database_row.datdba AS owner_oid,
                     database_row.datacl AS raw_acl,
                     'd'::"char" AS acl_kind
              FROM pg_catalog.pg_database database_row
              WHERE database_row.datname = current_database()

              UNION ALL

              SELECT 'SCHEMA'::text, namespace_row.nspname,
                     namespace_row.nspowner, namespace_row.nspacl,
                     'n'::"char"
              FROM pg_catalog.pg_namespace namespace_row
              WHERE namespace_row.nspname IN ('public', 'job_plane')

              UNION ALL

              SELECT 'RELATION'::text, relation_row.relname,
                     relation_row.relowner, relation_row.relacl,
                     'r'::"char"
              FROM pg_catalog.pg_class relation_row
              JOIN pg_catalog.pg_namespace namespace_row
                ON namespace_row.oid = relation_row.relnamespace
              WHERE namespace_row.nspname = 'public'
                AND relation_row.relname IN (
                  'jobs', 'job_attempts', 'job_events',
                  'scheduler_heartbeats', 'job_artifacts', 'worker_heartbeats'
                )
            ),
            effective_acls AS (
              SELECT selected.object_kind, selected.object_name,
                     CASE WHEN acl_entry.grantee = 0 THEN 'PUBLIC'
                          ELSE pg_catalog.pg_get_userbyid(acl_entry.grantee)
                     END AS grantee,
                     pg_catalog.pg_get_userbyid(acl_entry.grantor) AS grantor,
                     acl_entry.privilege_type,
                     acl_entry.is_grantable
              FROM selected_objects selected
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                coalesce(
                  selected.raw_acl,
                  pg_catalog.acldefault(selected.acl_kind, selected.owner_oid)
                )
              ) AS acl_entry(grantor, grantee, privilege_type, is_grantable)
            )
            SELECT object_kind, object_name, grantee, grantor,
                   privilege_type, is_grantable
            FROM effective_acls
            ORDER BY object_kind COLLATE "C", object_name COLLATE "C",
                     grantee COLLATE "C", grantor COLLATE "C",
                     privilege_type COLLATE "C", is_grantable
            """
        ).fetchall()
    return _canonical_effective_acl_rows(rows)


def _capture_row_counts(owner: DatabaseSettings) -> tuple[int, int, int]:
    with psycopg.connect(owner.conninfo()) as connection:
        counts = connection.execute(
            """
            SELECT (SELECT count(*) FROM public.jobs),
                   (SELECT count(*) FROM public.job_attempts),
                   (SELECT count(*) FROM public.job_events)
            """
        ).fetchone()
    if counts is None:
        _raise_restore_proof_failure(RESTORE_PROOF_ROW_COUNT_EVIDENCE_MISSING)
    return counts


def _assert_reviewed_authority(
    owner: DatabaseSettings,
    digest_failure_code: str,
    violations_failure_code: str,
) -> None:
    with psycopg.connect(owner.conninfo()) as connection:
        catalog = capture_catalog(connection, CONTRACT)
        violations = find_event_chain_violations(connection, CONTRACT)
    if catalog.sha256 != REVIEWED_CATALOG_SHA256:
        _raise_restore_proof_failure(
            _classify_reviewed_catalog_drift(catalog, digest_failure_code)
        )
    _assert_restore_proof_equal(violations, (), violations_failure_code)


def _drop_only_target_database(maintenance: DatabaseSettings) -> None:
    with psycopg.connect(maintenance.conninfo(), autocommit=True) as connection:
        current_database = connection.execute("SELECT current_database()").fetchone()
        _assert_restore_proof_equal(
            current_database,
            ("postgres",),
            RESTORE_PROOF_TARGET_DROP_MAINTENANCE_CONTEXT_MISMATCH,
        )
        connection.execute("DROP DATABASE trading_agent")
        database_dropped = connection.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database "
            "WHERE datname = %s)",
            (DATABASE_NAME,),
        ).fetchone()
        _assert_restore_proof_equal(
            database_dropped,
            (True,),
            RESTORE_PROOF_TARGET_DATABASE_DROP_MISMATCH,
        )


@dataclass(frozen=True, slots=True)
class _SecurePrecreatedDump:
    directory_fd: int
    filename: str
    directory_device: int
    directory_inode: int
    file_device: int
    file_inode: int


def _secure_dump_failure() -> None:
    raise RuntimeError("restore dump file security invariant failed")


def _validate_secure_precreated_dump(dump: _SecurePrecreatedDump) -> None:
    try:
        directory_info = os.fstat(dump.directory_fd)
        file_info = os.stat(
            dump.filename,
            dir_fd=dump.directory_fd,
            follow_symlinks=False,
        )
    except OSError:
        _secure_dump_failure()
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or directory_info.st_uid != os.getuid()
        or stat.S_IMODE(directory_info.st_mode) != 0o700
        or (directory_info.st_dev, directory_info.st_ino)
        != (dump.directory_device, dump.directory_inode)
        or not stat.S_ISREG(file_info.st_mode)
        or file_info.st_uid != os.getuid()
        or stat.S_IMODE(file_info.st_mode) != 0o600
        or file_info.st_nlink != 1
        or (file_info.st_dev, file_info.st_ino)
        != (dump.file_device, dump.file_inode)
    ):
        _secure_dump_failure()


def _validate_secure_dump_fd(dump: _SecurePrecreatedDump, fd: int) -> None:
    try:
        file_info = os.fstat(fd)
    except OSError:
        _secure_dump_failure()
    if (
        not stat.S_ISREG(file_info.st_mode)
        or file_info.st_uid != os.getuid()
        or stat.S_IMODE(file_info.st_mode) != 0o600
        or file_info.st_nlink != 1
        or (file_info.st_dev, file_info.st_ino)
        != (dump.file_device, dump.file_inode)
    ):
        _secure_dump_failure()


@contextmanager
def _secure_precreated_dump(session: object):
    validated = _validated_session(session)
    if (
        validated.dump.parent != validated.root
        or validated.dump.name != "trading-agent.dump"
    ):
        _secure_dump_failure()
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        directory_fd = os.open(validated.root, directory_flags)
        directory_info = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != os.getuid()
            or stat.S_IMODE(directory_info.st_mode) != 0o700
        ):
            _secure_dump_failure()
        file_fd = os.open(
            validated.dump.name,
            file_flags,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(file_fd, 0o600)
        file_info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(file_info.st_mode)
            or file_info.st_uid != os.getuid()
            or stat.S_IMODE(file_info.st_mode) != 0o600
            or file_info.st_nlink != 1
        ):
            _secure_dump_failure()
        secure_dump = _SecurePrecreatedDump(
            directory_fd=directory_fd,
            filename=validated.dump.name,
            directory_device=directory_info.st_dev,
            directory_inode=directory_info.st_ino,
            file_device=file_info.st_dev,
            file_inode=file_info.st_ino,
        )
        os.close(file_fd)
        file_fd = None
        _validate_secure_precreated_dump(secure_dump)
        yield secure_dump
    except RuntimeError:
        raise
    except OSError:
        _secure_dump_failure()
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _write_all(fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            _secure_dump_failure()
        remaining = remaining[written:]


def _copy_precreated_custom_dump(
    source_dump: _SecurePrecreatedDump,
    target_dump: _SecurePrecreatedDump,
) -> None:
    _validate_secure_precreated_dump(source_dump)
    _validate_secure_precreated_dump(target_dump)
    source_fd: int | None = None
    target_fd: int | None = None
    try:
        source_fd = os.open(
            source_dump.filename,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_dump.directory_fd,
        )
        target_fd = os.open(
            target_dump.filename,
            os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=target_dump.directory_fd,
        )
        _validate_secure_dump_fd(source_dump, source_fd)
        _validate_secure_dump_fd(target_dump, target_fd)
        os.ftruncate(target_fd, 0)
        while chunk := os.read(source_fd, 1024 * 1024):
            _write_all(target_fd, chunk)
        os.fsync(target_fd)
        _validate_secure_dump_fd(target_dump, target_fd)
    except RuntimeError:
        raise
    except OSError:
        _secure_dump_failure()
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if source_fd is not None:
            os.close(source_fd)
    _validate_secure_precreated_dump(source_dump)
    _validate_secure_precreated_dump(target_dump)


def _assert_runtime_direct_dml_denials(owner: DatabaseSettings) -> None:
    for role in RUNTIME_ROLES:
        role_settings = disposable_role_settings(owner, role)
        with psycopg.connect(role_settings.conninfo(), autocommit=True) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "UPDATE public.jobs SET state = 'BLOCKED' WHERE job_id = %s",
                    ("restore-append-only-probe",),
                )


def _assert_append_only_event_denials(owner: DatabaseSettings) -> None:
    with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
        connection.execute(
            """
            INSERT INTO public.jobs (
              job_id, job_type, state, payload, payload_fingerprint,
              idempotency_key, actor_type, actor_id, max_attempts, attempt_count
            ) VALUES (
              'restore-append-only-probe', 'SNAPSHOT', 'QUEUED', '{}'::jsonb,
              %s, 'restore:append-only-probe', 'SYSTEM', 'restore-test', 3, 0
            )
            """,
            ("0" * 64,),
        )
        connection.execute(
            """
            INSERT INTO public.job_events (
              event_id, job_id, sequence, from_state, to_state, reason_code,
              actor_type, actor_id, trace_id
            ) VALUES (
              'restore-append-only-probe-event', 'restore-append-only-probe',
              1, NULL, 'QUEUED', 'RESTORE_PROBE', 'SYSTEM', 'restore-test',
              'restore-trace'
            )
            """
        )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE public.job_events SET reason_code = 'TAMPERED' "
                "WHERE event_id = 'restore-append-only-probe-event'"
            )
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "DELETE FROM public.job_events "
                "WHERE event_id = 'restore-append-only-probe-event'"
            )


def test_green_restore_round_trip_preserves_0007_authority() -> None:
    _require_green_restore_authority()

    with _guard_restore_proof_stage(RESTORE_PROOF_SOURCE_PREPARATION_DUMP_FAILURE):
        with _disposable_postgres_session(
            operation_id=RESTORE_OPERATION_ID
        ) as source_session:
            source_owner = _prepare_disposable_database(source_session)
            _upgrade_to_revision(source_owner, EXACT_HEAD)
            _assert_exact_head(
                source_owner,
                EXACT_HEAD,
                RESTORE_PROOF_SOURCE_EXACT_HEAD_MISMATCH,
            )
            source_role_settings = _capture_sanitized_role_settings(
                _maintenance_settings(source_owner),
                RESTORE_PROOF_SOURCE_ROLE_SETTINGS_MISMATCH,
            )
            _assert_restore_proof_equal(
                source_role_settings,
                EXPECTED_ROLE_SETTINGS_AFTER_PROVISION,
                RESTORE_PROOF_SOURCE_ROLE_SETTINGS_MISMATCH,
            )
            source_database_acl = _capture_database_acl(source_owner)
            source_row_counts = _capture_row_counts(source_owner)
            with _secure_precreated_dump(source_session) as source_dump:
                _validate_secure_precreated_dump(source_dump)
                _run_pg_dump(source_session)
                _validate_secure_precreated_dump(source_dump)

                with _guard_restore_proof_stage(
                    RESTORE_PROOF_TARGET_BOOTSTRAP_DROP_FAILURE
                ):
                    with _disposable_postgres_session(
                        operation_id=RESTORE_OPERATION_ID
                    ) as target_session:
                        _assert_restore_proof_equal(
                            source_session.root == target_session.root,
                            False,
                            RESTORE_PROOF_CLUSTER_ROOT_ISOLATION_MISMATCH,
                        )
                        _assert_restore_proof_equal(
                            source_session.data == target_session.data,
                            False,
                            RESTORE_PROOF_CLUSTER_DATA_ISOLATION_MISMATCH,
                        )
                        _assert_restore_proof_equal(
                            source_session.authority.context.port
                            == target_session.authority.context.port,
                            False,
                            RESTORE_PROOF_CLUSTER_PORT_ISOLATION_MISMATCH,
                        )

                        target_owner = _prepare_disposable_database(target_session)
                        target_maintenance = _maintenance_settings(target_owner)
                        _assert_exact_head(
                            target_owner,
                            EXACT_0004_HEAD,
                            RESTORE_PROOF_TARGET_BASE_EXACT_HEAD_MISMATCH,
                        )
                        target_globals = _capture_provisioned_globals(
                            target_maintenance,
                            RESTORE_PROOF_TARGET_PROVISIONED_GLOBAL_ROLE_EVIDENCE_MISMATCH,
                        )
                        target_role_settings = _capture_sanitized_role_settings(
                            target_maintenance,
                            RESTORE_PROOF_TARGET_ROLE_SETTINGS_MISMATCH,
                        )
                        _assert_restore_proof_equal(
                            target_role_settings,
                            EXPECTED_ROLE_SETTINGS_AFTER_PROVISION,
                            RESTORE_PROOF_TARGET_ROLE_SETTINGS_MISMATCH,
                        )

                        _drop_only_target_database(target_maintenance)
                        _assert_restore_proof_equal(
                            _capture_provisioned_globals(
                                target_maintenance,
                                RESTORE_PROOF_TARGET_POST_DROP_GLOBAL_ROLE_EVIDENCE_MISMATCH,
                            ),
                            target_globals,
                            RESTORE_PROOF_TARGET_POST_DROP_GLOBAL_ROLE_EVIDENCE_MISMATCH,
                        )
                        _assert_restore_proof_equal(
                            _capture_sanitized_role_settings(
                                target_maintenance,
                                RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH,
                            ),
                            EXPECTED_ROLE_SETTINGS_AFTER_TARGET_DROP,
                            RESTORE_PROOF_TARGET_POST_DROP_ROLE_SETTINGS_MISMATCH,
                        )

                        _prepare_empty_restore_target(target_session)

                        with _guard_restore_proof_stage(
                            RESTORE_PROOF_RESTORE_EXECUTION_FAILURE
                        ):
                            with _secure_precreated_dump(target_session) as target_dump:
                                _validate_secure_precreated_dump(target_dump)
                                _copy_precreated_custom_dump(source_dump, target_dump)
                                _validate_secure_precreated_dump(target_dump)
                                _run_pg_restore(target_session)

                        with _guard_restore_proof_stage(
                            RESTORE_PROOF_POST_RESTORE_VERIFICATION_FAILURE
                        ):
                            _assert_restore_proof_equal(
                                _capture_provisioned_globals(
                                    target_maintenance,
                                    RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH,
                                ),
                                target_globals,
                                RESTORE_PROOF_TARGET_POST_RESTORE_GLOBAL_ROLE_EVIDENCE_MISMATCH,
                            )
                            _assert_restore_proof_equal(
                                _capture_sanitized_role_settings(
                                    target_maintenance,
                                    RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH,
                                ),
                                EXPECTED_ROLE_SETTINGS_AFTER_PROVISION,
                                RESTORE_PROOF_TARGET_POST_RESTORE_ROLE_SETTINGS_MISMATCH,
                            )
                            _assert_exact_head(
                                target_owner,
                                EXACT_HEAD,
                                RESTORE_PROOF_TARGET_RESTORED_EXACT_HEAD_MISMATCH,
                            )
                            _assert_restore_proof_equal(
                                _capture_database_acl(target_owner),
                                source_database_acl,
                                RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH,
                            )
                            _assert_restore_proof_equal(
                                _capture_row_counts(target_owner),
                                source_row_counts,
                                RESTORE_PROOF_RESTORED_ROW_COUNTS_MISMATCH,
                            )
                            _assert_reviewed_authority(
                                target_owner,
                                RESTORE_PROOF_RESTORED_CATALOG_DIGEST_MISMATCH,
                                RESTORE_PROOF_RESTORED_EVENT_CHAIN_MISMATCH,
                            )
                            _assert_runtime_direct_dml_denials(target_owner)
                            _assert_append_only_event_denials(target_owner)
                            _assert_restore_proof_equal(
                                _capture_row_counts(target_owner),
                                (
                                    source_row_counts[0] + 1,
                                    source_row_counts[1],
                                    source_row_counts[2] + 1,
                                ),
                                RESTORE_PROOF_POST_DENIAL_ROW_COUNTS_MISMATCH,
                            )
                            _assert_reviewed_authority(
                                target_owner,
                                RESTORE_PROOF_POST_DENIAL_CATALOG_DIGEST_MISMATCH,
                                RESTORE_PROOF_POST_DENIAL_EVENT_CHAIN_MISMATCH,
                            )


def test_future_green_restore_execution_is_statically_interlocked() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    green_test = functions["test_green_restore_round_trip_preserves_0007_authority"]
    first_statement = green_test.body[0]
    assert isinstance(first_statement, ast.Expr)
    assert isinstance(first_statement.value, ast.Call)
    assert isinstance(first_statement.value.func, ast.Name)
    assert first_statement.value.func.id == "_require_green_restore_authority"

    session_calls = [
        node
        for node in ast.walk(green_test)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_disposable_postgres_session"
    ]
    assert len(session_calls) == 2
    for call in session_calls:
        operation_id = next(
            keyword.value for keyword in call.keywords if keyword.arg == "operation_id"
        )
        assert isinstance(operation_id, ast.Name)
        assert operation_id.id == "RESTORE_OPERATION_ID"

    role_setting_calls = [
        node
        for node in ast.walk(green_test)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_capture_sanitized_role_settings"
    ]
    assert len(role_setting_calls) == 4

    green_source = ast.get_source_segment(source, green_test)
    assert green_source is not None
    for required in (
        "_assert_restore_proof_equal(",
        "RESTORE_PROOF_CLUSTER_ROOT_ISOLATION_MISMATCH",
        "RESTORE_PROOF_CLUSTER_DATA_ISOLATION_MISMATCH",
        "RESTORE_PROOF_CLUSTER_PORT_ISOLATION_MISMATCH",
        "source_session.authority.context.port",
        "target_session.authority.context.port",
        "_prepare_disposable_database(source_session)",
        "_prepare_disposable_database(target_session)",
        "_prepare_empty_restore_target(target_session)",
        "_upgrade_to_revision(source_owner, EXACT_HEAD)",
        "RESTORE_PROOF_TARGET_BASE_EXACT_HEAD_MISMATCH",
        "_capture_provisioned_globals(",
        "_capture_sanitized_role_settings(",
        "EXPECTED_ROLE_SETTINGS_AFTER_PROVISION",
        "EXPECTED_ROLE_SETTINGS_AFTER_TARGET_DROP",
        "_drop_only_target_database(target_maintenance)",
        "_secure_precreated_dump(source_session)",
        "_secure_precreated_dump(target_session)",
        "_copy_precreated_custom_dump(source_dump, target_dump)",
        "_run_pg_dump(source_session)",
        "_validate_secure_precreated_dump(source_dump)",
        "_validate_secure_precreated_dump(target_dump)",
        "_run_pg_restore(target_session)",
        "RESTORE_PROOF_TARGET_RESTORED_EXACT_HEAD_MISMATCH",
        "_capture_database_acl(target_owner),",
        "RESTORE_PROOF_EFFECTIVE_DATABASE_ACL_MISMATCH",
        "_assert_reviewed_authority(",
        "_assert_runtime_direct_dml_denials(target_owner)",
        "_assert_append_only_event_denials(target_owner)",
        "source_row_counts[0] + 1",
    ):
        assert required in green_source
    assert green_source.index("_run_pg_dump(source_session)") < (
        green_source.index("_drop_only_target_database(target_maintenance)")
    ) < green_source.index("_prepare_empty_restore_target(target_session)") < (
        green_source.index("_run_pg_restore(target_session)")
    )
    assert "pg_dumpall" not in green_source
    assert "read_bytes" not in green_source

    drop_source = ast.get_source_segment(
        source,
        functions["_drop_only_target_database"],
    )
    assert drop_source is not None
    assert 'connection.execute("DROP DATABASE trading_agent")' in drop_source
    assert "DROP ROLE" not in drop_source
    assert "DROP OWNED" not in drop_source

    authority_source = ast.get_source_segment(
        source,
        functions["_assert_reviewed_authority"],
    )
    globals_source = ast.get_source_segment(
        source,
        functions["_capture_provisioned_globals"],
    )
    role_settings_source = ast.get_source_segment(
        source,
        functions["_capture_sanitized_role_settings"],
    )
    acl_source = ast.get_source_segment(
        source,
        functions["_capture_database_acl"],
    )
    direct_dml_source = ast.get_source_segment(
        source,
        functions["_assert_runtime_direct_dml_denials"],
    )
    append_only_source = ast.get_source_segment(
        source,
        functions["_assert_append_only_event_denials"],
    )
    assert authority_source is not None
    assert globals_source is not None
    assert role_settings_source is not None
    assert acl_source is not None
    assert direct_dml_source is not None
    assert append_only_source is not None
    assert "_assert_restore_proof_equal(" in authority_source
    assert "REVIEWED_CATALOG_SHA256" in authority_source
    assert "violations" in authority_source
    assert "_capture_sanitized_role_settings" not in authority_source
    assert "_assert_restore_proof_equal(" in globals_source
    assert "rolpassword" not in globals_source
    assert "pg_catalog.pg_db_role_setting" in role_settings_source
    assert "rolpassword" not in role_settings_source
    assert "_assert_restore_proof_equal(" in role_settings_source
    assert "pg_catalog.aclexplode" in acl_source
    assert "pg_catalog.acldefault" in acl_source
    assert "_capture_sanitized_role_settings" not in acl_source
    assert "psycopg.errors.InsufficientPrivilege" in direct_dml_source
    assert "psycopg.errors.ObjectNotInPrerequisiteState" in append_only_source
