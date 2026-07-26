"""Opt-in complete Package 6 runtime chain.

Explicit selection is a required-runtime operation: incomplete authority is a
failure, never a skip.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Mapping, cast

import pytest

from packages.runtime_release.staging_v2 import (
    StagingAuthorityMaterial,
    attest_staging_material,
)
from scripts.validate_package6_runtime_approval import (
    Package6ApprovalContext,
    ValidatedPackage6Capability,
    validate_package6_runtime_approval,
    validate_source_binding_files,
)
from scripts.validate_disposable_postgres_approval import (
    DisposablePostgresApprovalContext,
    load_protected_approval_record as load_postgres_approval,
    validate_disposable_postgres_approval,
    validate_source_binding_files as validate_postgres_source_bindings,
)
from services.paper_runtime.controller import issue_runtime_child_authorities
from services.paper_runtime.evidence import (
    request_and_wait_for_postgres_cleanup,
    verify_runtime_evidence_bundle,
    write_runtime_evidence_bundle,
)
from services.paper_runtime.integration import run_approved_runtime_chain


pytestmark = pytest.mark.runtime_postgres

_REQUIRED = (
    "TRADING_PACKAGE6_APPROVAL_PATH",
    "TRADING_PACKAGE6_SOURCE_ROOT",
    "TRADING_PACKAGE6_SOURCE_COMMIT",
    "TRADING_PACKAGE6_SOURCE_TREE",
    "TRADING_PACKAGE6_STAGING_SCOPE",
    "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH",
    "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH",
    "TRADING_DISPOSABLE_POSTGRES_APPROVAL_SHA256",
    "TRADING_DISPOSABLE_POSTGRES_APPROVAL_PATH",
    "TRADING_DISPOSABLE_POSTGRES_OPERATION_ID",
    "TRADING_PACKAGE6_POSTGRES_HOST",
    "TRADING_PACKAGE6_POSTGRES_PORT",
    "TRADING_PACKAGE6_POSTGRES_DATABASE",
    "TRADING_PACKAGE6_POSTGRES_PGDATA",
    "TRADING_PACKAGE6_JOB_API_CREDENTIALS",
    "TRADING_PACKAGE6_WORKER_CREDENTIALS",
)


def _required_environment() -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in _REQUIRED}
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        pytest.fail(
            "Package 6 runtime authority is incomplete: " + ", ".join(missing)
        )
    return values


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise AssertionError(f"{label} is not an object")
    return value


def _build_approval_contexts(
    values: dict[str, str],
    *,
    operation_ids: tuple[str, ...],
    now: datetime,
) -> tuple[DisposablePostgresApprovalContext, Package6ApprovalContext]:
    postgres_context = DisposablePostgresApprovalContext(
        scope="DISPOSABLE_PG_GREEN",
        source_commit=values["TRADING_PACKAGE6_SOURCE_COMMIT"],
        source_tree=values["TRADING_PACKAGE6_SOURCE_TREE"],
        test_path="tests/foundation/test_package6_runtime_integration.py",
        operation_id=values["TRADING_DISPOSABLE_POSTGRES_OPERATION_ID"],
        pgdata=values["TRADING_PACKAGE6_POSTGRES_PGDATA"],
        bind_host=values["TRADING_PACKAGE6_POSTGRES_HOST"],
        port=int(values["TRADING_PACKAGE6_POSTGRES_PORT"]),
        cluster_name="trading-agent-disposable-tests",
        database_name=values["TRADING_PACKAGE6_POSTGRES_DATABASE"],
        runtime_setting_names=frozenset(),
        now=now,
    )
    package6_context = Package6ApprovalContext(
        source_commit=values["TRADING_PACKAGE6_SOURCE_COMMIT"],
        source_tree=values["TRADING_PACKAGE6_SOURCE_TREE"],
        operation_ids=operation_ids,
        disposable_postgres_approval_sha256=values[
            "TRADING_DISPOSABLE_POSTGRES_APPROVAL_SHA256"
        ],
        postgres_bind_host=values["TRADING_PACKAGE6_POSTGRES_HOST"],
        postgres_port=int(values["TRADING_PACKAGE6_POSTGRES_PORT"]),
        postgres_database_name=values["TRADING_PACKAGE6_POSTGRES_DATABASE"],
        postgres_pgdata=values["TRADING_PACKAGE6_POSTGRES_PGDATA"],
        postgres_cluster_name="trading-agent-disposable-tests",
        postgres_service_roles=("trading_job_api", "trading_job_worker"),
        now=now,
        source_root=Path(values["TRADING_PACKAGE6_SOURCE_ROOT"]),
        staging_scope=values["TRADING_PACKAGE6_STAGING_SCOPE"],
        staging_authority_path=Path(
            values["TRADING_PACKAGE6_STAGING_AUTHORITY_PATH"]
        ),
        staging_activation_path=Path(
            values["TRADING_PACKAGE6_STAGING_ACTIVATION_PATH"]
        ),
    )
    return postgres_context, package6_context


def _validated_capability(
    values: dict[str, str],
) -> tuple[ValidatedPackage6Capability, bytes, bytes]:
    source_root = Path(values["TRADING_PACKAGE6_SOURCE_ROOT"])
    postgres_approval_path = Path(
        values["TRADING_DISPOSABLE_POSTGRES_APPROVAL_PATH"]
    )
    postgres_approval_bytes = postgres_approval_path.read_bytes()
    if hashlib.sha256(postgres_approval_bytes).hexdigest() != values[
        "TRADING_DISPOSABLE_POSTGRES_APPROVAL_SHA256"
    ]:
        pytest.fail("disposable PostgreSQL approval byte digest does not match")
    postgres_approval = load_postgres_approval(postgres_approval_path)
    approval_path = Path(values["TRADING_PACKAGE6_APPROVAL_PATH"])
    try:
        approval_bytes = approval_path.read_bytes()
        record = _mapping(json.loads(approval_bytes), "Package 6 approval")
    except (OSError, json.JSONDecodeError) as error:
        raise AssertionError(
            f"Package 6 approval is unavailable or invalid: {type(error).__name__}"
        ) from error
    raw_operations = record["operations"]
    if not isinstance(raw_operations, list):
        raise AssertionError("Package 6 approval operations are not a list")
    operations = tuple(
        _mapping(item, "Package 6 approval operation") for item in raw_operations
    )
    operation_ids = tuple(
        operation_id
        for item in operations
        if isinstance((operation_id := item["operation_id"]), str)
    )
    if len(operation_ids) != len(operations):
        raise AssertionError("Package 6 approval operation ID is invalid")
    postgres_context, context = _build_approval_contexts(
        values,
        operation_ids=operation_ids,
        now=datetime.now(UTC),
    )
    validate_disposable_postgres_approval(postgres_approval, postgres_context)
    validate_postgres_source_bindings(postgres_approval, source_root)
    capability = validate_package6_runtime_approval(
        record, context, approval_bytes=approval_bytes
    )
    validate_source_binding_files(record, source_root)
    return capability, approval_bytes, postgres_approval_bytes


def test_approval_contexts_use_exact_authority_field_sets(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    values = {
        "TRADING_PACKAGE6_SOURCE_ROOT": str(tmp_path / "source"),
        "TRADING_PACKAGE6_SOURCE_COMMIT": "a" * 40,
        "TRADING_PACKAGE6_SOURCE_TREE": "b" * 40,
        "TRADING_PACKAGE6_STAGING_SCOPE": "PACKAGE6_STAGING_V2",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH": str(tmp_path / "authority.json"),
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH": str(tmp_path / "activation.json"),
        "TRADING_DISPOSABLE_POSTGRES_OPERATION_ID": "package6.runtime.integration",
        "TRADING_PACKAGE6_POSTGRES_HOST": "127.0.0.1",
        "TRADING_PACKAGE6_POSTGRES_PORT": "55433",
        "TRADING_PACKAGE6_POSTGRES_DATABASE": "trading_agent_disposable_test",
        "TRADING_PACKAGE6_POSTGRES_PGDATA": str(tmp_path / "pgdata"),
        "TRADING_DISPOSABLE_POSTGRES_APPROVAL_SHA256": "c" * 64,
    }

    postgres_context, package6_context = _build_approval_contexts(
        values,
        operation_ids=("job-api.start", "job-api.stop", "worker.start", "worker.stop"),
        now=now,
    )

    assert postgres_context._fields == DisposablePostgresApprovalContext._fields
    assert postgres_context.now is now
    assert package6_context._fields == Package6ApprovalContext._fields
    context_fields = package6_context._asdict()
    assert context_fields["source_root"] == tmp_path / "source"
    assert context_fields["staging_scope"] == "PACKAGE6_STAGING_V2"
    assert context_fields["staging_authority_path"] == tmp_path / "authority.json"
    assert context_fields["staging_activation_path"] == tmp_path / "activation.json"


def test_staged_artifact_exact_job_api_command_reaches_runtime_authority_gate() -> None:
    values = _required_environment()
    capability, _approval_bytes, _postgres_approval_bytes = _validated_capability(
        values
    )
    material = cast(
        StagingAuthorityMaterial,
        getattr(capability, "staging_material"),
    )
    operation = capability.operations["job-api.start"]
    command = (
        str(material.application_python),
        "-I",
        "-m",
        "apps.job_api.main",
    )
    assert operation.argv == command
    assert operation.cwd == material.application_root
    assert operation.executable_sha256 == material.application_python_sha256
    assert attest_staging_material(
        material,
        runtime_python_path=material.application_python,
    )

    completed = subprocess.run(
        command,
        cwd=material.application_root,
        env={
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
            "LIVE_TRADING_ENABLED": "false",
            "PATH": "/usr/bin:/bin",
            "TRADING_MODE": "paper",
            "TZ": "UTC",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    diagnostic = (completed.stdout + completed.stderr).replace("\\", "/")
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "ModuleNotFoundError" not in diagnostic
    assert diagnostic.splitlines()[-1] == (
        "packages.runtime_release.config.ProtectedAuthorityError: "
        "protected runtime authority is unavailable"
    )


def test_complete_package6_runtime_chain() -> None:
    values = _required_environment()
    source_root = Path(values["TRADING_PACKAGE6_SOURCE_ROOT"])
    capability, approval_bytes, postgres_approval_bytes = _validated_capability(values)
    child_authorities = issue_runtime_child_authorities(
        capability,
        job_api_credentials=Path(values["TRADING_PACKAGE6_JOB_API_CREDENTIALS"]),
        worker_credentials=Path(values["TRADING_PACKAGE6_WORKER_CREDENTIALS"]),
    )

    evidence = run_approved_runtime_chain(capability, child_authorities)

    assert evidence.first_request["status"] == 201
    assert evidence.duplicate_request["status"] == 200
    assert evidence.database["idempotent_job_count"] == 1
    assert evidence.database["queue_depth"] == 0
    assert _mapping(evidence.database["job"], "durable job")["state"] == "SUCCEEDED"
    assert evidence.worker_stop["cleanup_proven"] is True
    assert evidence.job_api_stop["cleanup_proven"] is True
    cleanup = request_and_wait_for_postgres_cleanup(capability)
    bundle = write_runtime_evidence_bundle(
        capability,
        child_authorities,
        evidence,
        cleanup,
        source_root=source_root,
        approval_bytes=approval_bytes,
        postgres_approval_bytes=postgres_approval_bytes,
    )
    assert verify_runtime_evidence_bundle(bundle) is True
