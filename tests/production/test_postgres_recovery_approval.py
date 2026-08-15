from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/production/runbooks/postgresql-preserve-recover.md"
SCHEMA = ROOT / "schemas/postgres-recovery-approval-record.schema.json"
EXAMPLE = ROOT / "ops/postgres/postgres-recovery-approval-record.example.yaml"
DRAFT = (
    ROOT
    / "ops/postgres/pending/postgres-recovery-approval-data-001-preparation.yaml"
)
VALIDATOR = ROOT / "scripts/validate_postgres_recovery_approval.py"
REVIEW = ROOT / "docs/implementation/postgres-recovery-approval-review.md"
RUNBOOK_SHA256 = "feabc083b5fe35681fde63d8fbc45ae10e56b9938d575eec604718facf9aa15c"
PLACEHOLDER = "REQUIRES_REVIEWER_INPUT"
NOTICE = "RECOVERY APPROVAL STATUS: DRAFT — NOT AUTHORIZED"
AUTO_TRUSTED_EVIDENCE_ROOT = object()

EVIDENCE_AGE_FIELDS = (
    "trading_safety_observed_at_utc",
    "job_plane_observed_at_utc",
    "database_incident_observed_date",
    "evidence_status",
    "current_revalidation",
)
SAFETY_BASELINE_FIELDS = (
    "requested_mode",
    "effective_mode",
    "kill_switch",
    "orders_count",
    "trades_count",
    "job_api_state",
    "job_worker_state",
    "job_scheduler_state",
    "job_scheduler_timer_state",
    "port_8401_state",
)
EXPECTED_PRE_RECOVERY_FIELDS = (
    "alembic_head",
    "canonical_rows",
    "quarantine_rows",
    "jobs_count",
    "job_attempts_count",
    "job_events_count",
    "scheduler_heartbeats_count",
    "job_artifacts_count",
    "worker_heartbeats_count",
)
STALE_PID_EVIDENCE_FIELDS = (
    "classification",
    "incident_evidence_reference",
    "zero_write_gate_reference",
    "pid_file_action",
    "current_pid_revalidation",
)
RECOVERY_REVIEW_FIELDS = (
    "postmaster_status_outcome",
    "process_identity_outcome",
    "port_55432_outcome",
    "data_directory_outcome",
    "independent_disk_outcome",
    "recovery_log_outcome",
)
PROCEDURE_REFERENCE_FIELDS = (
    "prohibited_actions_reference",
    "exact_runbook_commands_reference",
    "execution_status",
)
BACKUP_RESTORE_CONTROL_FIELDS = (
    "backup_permission_reference",
    "backup_permission_outcome",
    "restore_reference",
    "restore_outcome",
    "rollback_reference",
    "stop_conditions_reference",
    "stop_conditions_outcome",
)

REQUIRED_ARTIFACTS = (SCHEMA, EXAMPLE, DRAFT, VALIDATOR, REVIEW)


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="postgres-recovery-approval-", dir="/tmp") as raw:
        yield Path(raw)


def _require_artifacts() -> None:
    missing = [path.relative_to(ROOT).as_posix() for path in REQUIRED_ARTIFACTS if not path.is_file()]
    if missing:
        pytest.skip(f"implementation artifacts not present yet: {missing}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runbook_fields(text: str | None = None) -> list[str]:
    source = RUNBOOK.read_text(encoding="utf-8") if text is None else text
    section = source.split("## 4. Exact dual-reviewed execution transcript", 1)[1]
    block = section.split("~~~text\n", 1)[1].split("\n~~~", 1)[0]
    fields = [line.split("\t", 1)[0] for line in block.splitlines() if line]
    assert all("\t" in line for line in block.splitlines() if line)
    return fields


def _runbook_parser_fields(text: str | None = None) -> list[str]:
    source = RUNBOOK.read_text(encoding="utf-8") if text is None else text
    block = source.split("required_keys=(\n", 1)[1].split("\n)\ntest ", 1)[0]
    return re.findall(r"\b[A-Z][A-Z0-9_]+\b", block)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _walk_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _walk_strings(child)]
    return []


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _canonical_digest(document: dict[str, object]) -> str:
    transcript = document["transcript"]
    assert isinstance(transcript, dict)
    raw = "".join(f"{key}\t{transcript[key]}\n" for key in _runbook_fields())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _refresh_canonical_digest(document: dict[str, object]) -> None:
    integrity = document["integrity"]
    assert isinstance(integrity, dict)
    integrity["canonical_transcript_sha256"] = _canonical_digest(document)


def _run(
    record: Path,
    *,
    schema_only: bool = False,
    runbook: Path = RUNBOOK,
    trusted_evidence_root: Path | None | object = AUTO_TRUSTED_EVIDENCE_ROOT,
) -> subprocess.CompletedProcess[str]:
    command = [
        "/usr/bin/python3",
        "-I",
        str(VALIDATOR),
        str(record),
        "--schema",
        str(SCHEMA),
        "--runbook",
        str(runbook),
    ]
    if schema_only:
        command.append("--schema-only")
    elif trusted_evidence_root is AUTO_TRUSTED_EVIDENCE_ROOT:
        try:
            document = json.loads(record.read_text(encoding="utf-8"))
            transcript = document.get("transcript", {})
            declared_root = transcript.get("EVIDENCE_PARENT")
        except (
            OSError,
            RecursionError,
            UnicodeError,
            json.JSONDecodeError,
            AttributeError,
        ):
            declared_root = None
        if isinstance(declared_root, str) and declared_root != PLACEHOLDER:
            command.extend(("--trusted-evidence-root", declared_root))
    elif isinstance(trusted_evidence_root, Path):
        command.extend(("--trusted-evidence-root", str(trusted_evidence_root)))
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _git(arguments: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _complete_fixture(tmp_path: Path) -> tuple[dict[str, object], Path]:
    now = datetime.now(UTC).replace(microsecond=0)
    approved_at = now - timedelta(minutes=30)
    expires_at = now + timedelta(hours=2)
    reviewed_at = now - timedelta(minutes=15)
    source = tmp_path / "reviewed-source"
    migration = source / "alembic/versions/0004_durable_research_jobs.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("revision = '0004_durable_research_jobs'\n", encoding="utf-8")
    runbook = source / "docs/production/runbooks/postgresql-preserve-recover.md"
    runbook.parent.mkdir(parents=True)
    runbook.write_bytes(RUNBOOK.read_bytes())
    _git(["init", "-q"], source)
    _git(["config", "user.email", "fixture@example.invalid"], source)
    _git(["config", "user.name", "Fixture Reviewer"], source)
    _git(["add", "."], source)
    _git(["commit", "-q", "-m", "isolated fixture"], source)
    source_commit = _git(["rev-parse", "HEAD"], source)
    source_tree = _git(["rev-parse", "HEAD^{tree}"], source)
    parents = {}
    for name in ("evidence", "preserve", "backup", "secret", "iso-socket", "iso-pgdata"):
        path = tmp_path / name
        path.mkdir(mode=0o700)
        parents[name] = str(path.resolve())

    evidence = Path(parents["evidence"])
    catalog = evidence / "expected-catalog.snapshot"
    catalog.write_text(
        "snapshot|query_id=PG16_COMPLETE_RELATION_CATALOG_V2|pg_major=16\n",
        encoding="utf-8",
    )
    change_artifact = evidence / "authenticated-change-export.json"
    change_artifact.write_text('{"change":"fixture-only"}\n', encoding="utf-8")
    change_artifact.chmod(0o600)

    fingerprint = "7" * 64
    identity = evidence / "original-identity.json"
    identity_document = {
        "postgresql_major": "16",
        "cluster": "trading-agent",
        "host": "127.0.0.1",
        "port": "55432",
        "database": "trading_agent",
        "pgdata": "/home/thenam176/.local/share/trading-agent/postgres/16/trading-agent",
        "system_id": "1234567890123456789",
        "pgdata_nlink": "2",
        "socket_nlink": "2",
        "log_dir_nlink": "2",
        "log_nlink": "1",
        "pgdata_fingerprint": fingerprint,
    }
    _write_json(identity, identity_document)
    identity.chmod(0o600)

    transcript_values = {
        "DECISION": "APPROVED_POSTGRESQL16_RECOVERY_SUBGATE",
        "CHANGE_ID": "CHANGE-FIXTURE-001",
        "INCIDENT_ID": "INCIDENT-FIXTURE-001",
        "CHANGE_ARTIFACT": str(change_artifact.resolve()),
        "CHANGE_ARTIFACT_SHA256": _sha(change_artifact),
        "APPROVED_AT_UTC": _timestamp(approved_at),
        "EXPIRES_AT_UTC": _timestamp(expires_at),
        "OPERATOR_NAME": "fixture.operator",
        "REVIEWER_NAME": "fixture.reviewer",
        "OPERATOR_ATTESTATION": "I_APPROVE_THIS_EXACT_RECOVERY_TRANSCRIPT",
        "REVIEWER_ATTESTATION": "I_INDEPENDENTLY_REVIEWED_THIS_EXACT_RECOVERY_TRANSCRIPT",
        "RUN_ID": "fixture-run-001",
        "RUNBOOK_SHA256": _sha(runbook),
        "SOURCE_COMMIT": source_commit,
        "SOURCE_TREE": source_tree,
        "MIGRATION_SHA256": _sha(migration),
        "EXPECTED_CATALOG_SHA256": _sha(catalog),
        "EXPECTED_CATALOG_QUERY_ID": "PG16_COMPLETE_RELATION_CATALOG_V2",
        "EXPECTED_CATALOG_PROVENANCE": "PRECOMPUTED_CLEAN_DISPOSABLE_PG16_EXACT_0001_0004_CATALOG_V2",
        "EXPECTED_CATALOG_REVIEW_ATTESTATION": "INDEPENDENTLY_REVIEWED_NOT_FROM_INCIDENT_OR_THIS_RUN_RESTORE",
        "ORIG_SYSTEM_ID": identity_document["system_id"],
        "ORIG_PGDATA_NLINK": identity_document["pgdata_nlink"],
        "ORIG_SOCKET_NLINK": identity_document["socket_nlink"],
        "ORIG_LOG_DIR_NLINK": identity_document["log_dir_nlink"],
        "ORIG_LOG_NLINK": identity_document["log_nlink"],
        "EVIDENCE_PARENT": parents["evidence"],
        "PRESERVE_PARENT": parents["preserve"],
        "BACKUP_PARENT": parents["backup"],
        "SECRET_PARENT": parents["secret"],
        "ISO_HOST": parents["iso-socket"],
        "ISO_PORT": "6543",
        "ISO_ADMIN_DB": "postgres",
        "ISO_RESTORE_DB": "trading_agent_restore_fixture_001",
        "ISO_PGDATA": parents["iso-pgdata"],
        "ISO_SYSTEM_ID": "9876543210987654321",
        "ISO_SOCKET": parents["iso-socket"],
        "ISO_ADMIN_ENV": str((tmp_path / "isolated-admin.env").resolve()),
        "ALLOW_STOP_ALL_LISTED_UNITS": "YES",
        "ALLOW_OFFLINE_COLD_COPY": "YES",
        "ALLOW_ONE_ORIGINAL_START": "YES",
        "ALLOW_ONE_ORIGINAL_STOP": "YES",
        "ALLOW_READ_ONLY_VERIFICATION": "YES",
        "ALLOW_IMMEDIATE_LOGICAL_BACKUP": "YES",
        "ALLOW_MIGRATE_ORIGINAL_IF_0003": "YES",
        "ALLOW_ISOLATED_RESTORE": "YES",
        "ACCEPT_INTERRUPTED_SHUTDOWN": "YES",
        "ACCEPT_CHECKSUMS_DISABLED": "YES",
        "ACKNOWLEDGE_NO_PITR": "YES",
        "RECOVERY_LOG_POLICY_ID": "PG16_INTERRUPTED_RECOVERY_V1",
        "ORIGINAL_START_POLICY_ID": "PG16_FAIL_CLOSED_MAINTENANCE_START_V1",
    }
    assert set(transcript_values) == set(_runbook_fields())
    transcript = {key: str(transcript_values[key]) for key in _runbook_fields()}
    document: dict[str, object] = {
        "document_kind": "POSTGRESQL_RECOVERY_APPROVAL_PREPARATION",
        "format_version": "1",
        "record_id": "POSTGRES_RECOVERY_ISOLATED_FIXTURE",
        "authorization_state": "DRAFT_NOT_AUTHORIZED",
        "evidence_age": {
            "trading_safety_observed_at_utc": "2026-07-11T23:46:34Z",
            "job_plane_observed_at_utc": "2026-07-16T15:12:29Z",
            "database_incident_observed_date": "2026-07-16",
            "evidence_status": "CURRENT_REVIEW_COMPLETE",
            "current_revalidation": _timestamp(reviewed_at),
        },
        "safety_baseline": {
            "requested_mode": "paper",
            "effective_mode": "paper",
            "kill_switch": "INACTIVE",
            "orders_count": "30",
            "trades_count": "0",
            "job_api_state": "INACTIVE",
            "job_worker_state": "INACTIVE",
            "job_scheduler_state": "INACTIVE",
            "job_scheduler_timer_state": "DISABLED",
            "port_8401_state": "CLOSED",
        },
        "expected_pre_recovery": {
            "alembic_head": "0003_contract_lineage_repair_OR_0004_durable_research_jobs",
            "canonical_rows": "43055",
            "quarantine_rows": "222",
            "jobs_count": "0",
            "job_attempts_count": "0",
            "job_events_count": "0",
            "scheduler_heartbeats_count": "0",
            "job_artifacts_count": "0",
            "worker_heartbeats_count": "0",
        },
        "stale_pid_evidence": {
            "classification": "STALE_PID",
            "incident_evidence_reference": "docs/production/runbooks/postgresql-preserve-recover.md#1-known-incident-state",
            "zero_write_gate_reference": "docs/production/runbooks/postgresql-preserve-recover.md#52-zero-write-canonical-path-overlap-storage-and-destination-gates",
            "pid_file_action": "PRESERVE_UNCHANGED",
            "current_pid_revalidation": "STALE_PID_REVALIDATED_NO_LIVE_PROCESS",
        },
        "recovery_review": {
            "postmaster_status_outcome": "OFFLINE_CONFIRMED",
            "process_identity_outcome": "NO_LIVE_POSTMASTER_FOR_ORIGINAL_PGDATA",
            "port_55432_outcome": "CLOSED",
            "data_directory_outcome": "MATCHED_REVIEWED_IDENTITY",
            "independent_disk_outcome": "INDEPENDENT_CAPACITY_CONFIRMED",
            "recovery_log_outcome": "INTERRUPTED_SHUTDOWN_CLASSIFIED_WITHOUT_DENIED_PATTERNS",
        },
        "procedure_references": {
            "prohibited_actions_reference": "docs/production/runbooks/postgresql-preserve-recover.md#2-absolute-prohibitions-and-stop-conditions",
            "exact_runbook_commands_reference": "docs/production/runbooks/postgresql-preserve-recover.md#5-conditional-operator-procedure",
            "execution_status": "NOT_EXECUTED",
        },
        "backup_restore_controls": {
            "backup_permission_reference": "docs/production/runbooks/postgresql-preserve-recover.md#4-exact-dual-reviewed-execution-transcript",
            "backup_permission_outcome": "AUTHENTICATED_DUAL_REVIEW_CONFIRMED",
            "restore_reference": "docs/production/runbooks/postgresql-preserve-recover.md#515-create-one-isolated-database-restore-and-compare-exact-gates",
            "restore_outcome": "ISOLATED_RESTORE_ONLY_REVIEWED",
            "rollback_reference": "docs/production/runbooks/postgresql-preserve-recover.md#6-non-destructive-rollback",
            "stop_conditions_reference": "docs/production/runbooks/postgresql-preserve-recover.md#2-absolute-prohibitions-and-stop-conditions",
            "stop_conditions_outcome": "STOP_CONDITIONS_REVIEWED",
        },
        "target": {
            "postgresql_major": "16",
            "cluster": "trading-agent",
            "host": "127.0.0.1",
            "port": "55432",
            "database": "trading_agent",
            "pgdata": "/home/thenam176/.local/share/trading-agent/postgres/16/trading-agent",
        },
        "preflight": {
            "operation": "POSTGRESQL16_RECOVERY_SUBGATE",
            "scope": "DATA_001_PRESERVATION_RECOVERY_ONLY",
            "execution_mode": "PAPER_ONLY",
            "live_execution_approved": "false",
            "live_trading_approved": "false",
            "baseline_complete": "true",
            "all_listed_units_inactive": "true",
            "original_port_55432_listener_absent": "true",
            "review_completed_at_utc": _timestamp(reviewed_at),
        },
        "integrity": {
            "canonical_transcript_sha256": "0" * 64,
            "source_repository": str(source.resolve()),
            "migration_artifact": str(migration.resolve()),
            "expected_catalog_artifact": str(catalog.resolve()),
            "original_identity_evidence": str(identity.resolve()),
            "original_identity_evidence_sha256": _sha(identity),
            "original_pgdata_fingerprint": fingerprint,
        },
        "transcript": transcript,
        "authorization_notice": NOTICE,
    }
    _refresh_canonical_digest(document)
    return document, runbook


def _write_complete_fixture(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    document, runbook = _complete_fixture(tmp_path)
    record = tmp_path / "approval-preparation.yaml"
    _write_json(record, document)
    return document, record, runbook


def test_required_recovery_approval_artifacts_exist() -> None:
    assert [path.relative_to(ROOT).as_posix() for path in REQUIRED_ARTIFACTS if not path.is_file()] == []


def test_schema_exactly_tracks_the_unchanged_runbook_transcript() -> None:
    _require_artifacts()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    fields = _runbook_fields()
    transcript_schema = schema["properties"]["transcript"]

    assert _sha(RUNBOOK) == RUNBOOK_SHA256
    assert len(fields) == 50
    assert len(set(fields)) == 50
    assert _runbook_parser_fields() == fields
    assert schema["x-runbook-transcript-order"] == fields
    assert transcript_schema["required"] == fields
    assert list(transcript_schema["properties"]) == fields
    assert transcript_schema["minProperties"] == 50
    assert transcript_schema["maxProperties"] == 50
    assert transcript_schema["additionalProperties"] is False


@pytest.mark.parametrize("record", (EXAMPLE, DRAFT))
def test_preparation_envelope_explicitly_carries_all_recovery_review_groups(
    record: Path,
) -> None:
    _require_artifacts()
    document = json.loads(record.read_text(encoding="utf-8"))
    expected_groups = {
        "evidence_age": EVIDENCE_AGE_FIELDS,
        "safety_baseline": SAFETY_BASELINE_FIELDS,
        "expected_pre_recovery": EXPECTED_PRE_RECOVERY_FIELDS,
        "stale_pid_evidence": STALE_PID_EVIDENCE_FIELDS,
        "recovery_review": RECOVERY_REVIEW_FIELDS,
        "procedure_references": PROCEDURE_REFERENCE_FIELDS,
        "backup_restore_controls": BACKUP_RESTORE_CONTROL_FIELDS,
    }

    for group, fields in expected_groups.items():
        assert group in document
        assert tuple(document[group]) == fields
        assert all(isinstance(value, str) for value in document[group].values())

    assert document["safety_baseline"] == {
        "requested_mode": "paper",
        "effective_mode": "paper",
        "kill_switch": "INACTIVE",
        "orders_count": "30",
        "trades_count": "0",
        "job_api_state": "INACTIVE",
        "job_worker_state": "INACTIVE",
        "job_scheduler_state": "INACTIVE",
        "job_scheduler_timer_state": "DISABLED",
        "port_8401_state": "CLOSED",
    }
    assert document["expected_pre_recovery"] == {
        "alembic_head": "0003_contract_lineage_repair_OR_0004_durable_research_jobs",
        "canonical_rows": "43055",
        "quarantine_rows": "222",
        "jobs_count": "0",
        "job_attempts_count": "0",
        "job_events_count": "0",
        "scheduler_heartbeats_count": "0",
        "job_artifacts_count": "0",
        "worker_heartbeats_count": "0",
    }
    assert document["stale_pid_evidence"]["classification"] == "STALE_PID"
    assert document["stale_pid_evidence"]["current_pid_revalidation"] == PLACEHOLDER
    assert set(document["recovery_review"].values()) == {PLACEHOLDER}
    assert document["backup_restore_controls"]["backup_permission_outcome"] == PLACEHOLDER
    assert document["backup_restore_controls"]["restore_outcome"] == PLACEHOLDER
    assert document["backup_restore_controls"]["stop_conditions_outcome"] == PLACEHOLDER
    assert list(document["transcript"]) == _runbook_fields()
    assert len(document["transcript"]) == 50
    assert not set(expected_groups) & set(document["transcript"])


@pytest.mark.parametrize("record", (EXAMPLE, DRAFT))
def test_committed_records_keep_every_human_or_current_field_at_sentinel(
    record: Path,
) -> None:
    _require_artifacts()
    document = json.loads(record.read_text(encoding="utf-8"))
    preflight_sentinels = {
        "baseline_complete",
        "all_listed_units_inactive",
        "original_port_55432_listener_absent",
        "review_completed_at_utc",
    }
    integrity_sentinels = {
        "canonical_transcript_sha256",
        "source_repository",
        "migration_artifact",
        "expected_catalog_artifact",
        "original_identity_evidence",
        "original_identity_evidence_sha256",
        "original_pgdata_fingerprint",
    }
    committed_transcript_constants = {
        "EXPECTED_CATALOG_QUERY_ID",
        "EXPECTED_CATALOG_PROVENANCE",
        "ISO_ADMIN_DB",
        "RECOVERY_LOG_POLICY_ID",
        "ORIGINAL_START_POLICY_ID",
    }
    current_envelope_fields = {
        "evidence_age": {"current_revalidation"},
        "stale_pid_evidence": {"current_pid_revalidation"},
        "recovery_review": set(RECOVERY_REVIEW_FIELDS),
        "backup_restore_controls": {
            "backup_permission_outcome",
            "restore_outcome",
            "stop_conditions_outcome",
        },
    }

    assert all(
        document["preflight"][field] == PLACEHOLDER
        for field in preflight_sentinels
    )
    assert all(
        document["integrity"][field] == PLACEHOLDER
        for field in integrity_sentinels
    )
    assert all(
        value == PLACEHOLDER
        for field, value in document["transcript"].items()
        if field not in committed_transcript_constants
    )
    for group, fields in current_envelope_fields.items():
        assert all(document[group][field] == PLACEHOLDER for field in fields)


def test_schema_closes_every_explicit_recovery_review_group() -> None:
    _require_artifacts()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    expected_groups = {
        "evidence_age": EVIDENCE_AGE_FIELDS,
        "safety_baseline": SAFETY_BASELINE_FIELDS,
        "expected_pre_recovery": EXPECTED_PRE_RECOVERY_FIELDS,
        "stale_pid_evidence": STALE_PID_EVIDENCE_FIELDS,
        "recovery_review": RECOVERY_REVIEW_FIELDS,
        "procedure_references": PROCEDURE_REFERENCE_FIELDS,
        "backup_restore_controls": BACKUP_RESTORE_CONTROL_FIELDS,
    }

    for group, fields in expected_groups.items():
        group_schema = schema["properties"][group]
        assert group_schema["required"] == list(fields)
        assert list(group_schema["properties"]) == list(fields)
        assert group_schema["additionalProperties"] is False
        assert group_schema["minProperties"] == len(fields)
        assert group_schema["maxProperties"] == len(fields)


@pytest.mark.parametrize(
    ("group", "mutation"),
    (
        ("safety_baseline", "missing"),
        ("expected_pre_recovery", "unknown"),
        ("recovery_review", "nonstring"),
    ),
)
def test_explicit_recovery_review_groups_are_strictly_schematized(
    tmp_path: Path,
    group: str,
    mutation: str,
) -> None:
    _require_artifacts()
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if mutation == "missing":
        document[group].pop(next(iter(document[group])))
    elif mutation == "unknown":
        document[group]["unreviewed_extra"] = PLACEHOLDER
    else:
        document[group][next(iter(document[group]))] = False
    record = tmp_path / f"invalid-{group}.yaml"
    _write_json(record, document)

    result = _run(record, schema_only=True)
    assert result.returncode != 0
    assert "SCHEMA" in result.stderr


@pytest.mark.parametrize(
    ("group", "field", "unsafe_value"),
    (
        ("evidence_age", "evidence_status", "CURRENT_UNREVIEWED"),
        ("safety_baseline", "requested_mode", "live"),
        ("expected_pre_recovery", "jobs_count", "1"),
        ("stale_pid_evidence", "classification", "UNKNOWN_PID"),
        ("recovery_review", "postmaster_status_outcome", "ASSUMED_OFFLINE"),
        ("procedure_references", "execution_status", "EXECUTED"),
        ("backup_restore_controls", "restore_reference", "unreviewed-section"),
    ),
)
def test_explicit_recovery_review_values_fail_closed(
    tmp_path: Path,
    group: str,
    field: str,
    unsafe_value: str,
) -> None:
    _require_artifacts()
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document[group][field] = unsafe_value
    record = tmp_path / f"invalid-{group}-{field}.yaml"
    _write_json(record, document)

    result = _run(record, schema_only=True)

    assert result.returncode != 0
    assert "SCHEMA" in result.stderr


@pytest.mark.parametrize("record", (EXAMPLE, DRAFT))
def test_committed_yaml_subset_is_schema_valid_and_explicitly_nonauthorizing(
    record: Path,
) -> None:
    _require_artifacts()
    before = record.read_bytes()
    result = _run(record, schema_only=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("NON-AUTHORIZING:")
    assert "APPROVAL_RECORD" in result.stdout
    assert result.stderr == ""
    assert record.read_bytes() == before

    document = json.loads(before)
    assert document["authorization_state"] == "DRAFT_NOT_AUTHORIZED"
    assert document["authorization_notice"] == NOTICE
    assert list(document["transcript"]) == _runbook_fields()
    assert all(isinstance(value, str) for value in _walk_strings(document))
    assert not any(
        re.search(
            r"(?i)(postgres(?:ql)?://|password\s*=|PGPASSWORD\s*=|BEGIN [A-Z ]*PRIVATE KEY)",
            value,
        )
        for value in _walk_strings(document)
    )
    assert not any(value.startswith("APPROVED_") for value in _walk_strings(document))
    assert "YES" not in document["transcript"].values()


def test_pending_draft_uses_stable_identifier_and_ends_with_status_notice() -> None:
    _require_artifacts()
    document = json.loads(DRAFT.read_text(encoding="utf-8"))

    assert DRAFT.name == "postgres-recovery-approval-data-001-preparation.yaml"
    assert document["record_id"] == "POSTGRES_RECOVERY_DATA_001_PREPARATION"
    assert list(document)[-1] == "authorization_notice"
    assert DRAFT.read_text(encoding="utf-8").rfind(NOTICE) > DRAFT.read_text(encoding="utf-8").rfind('"transcript"')


@pytest.mark.parametrize(
    ("name", "raw"),
    (
        ("malformed", b'{"document_kind":'),
        ("ordinary_yaml", b"document_kind: POSTGRESQL_RECOVERY_APPROVAL_PREPARATION\n"),
        ("yaml_tag", b"!!python/object/apply:os.system ['id']\n"),
        ("yaml_anchor", b"a: &anchor value\nb: *anchor\n"),
        ("yaml_document", b"---\ndocument_kind: unsafe\n"),
        ("json_nan", b'{"value": NaN}\n'),
    ),
)
def test_restricted_yaml_subset_rejects_malformed_or_executable_yaml(
    tmp_path: Path,
    name: str,
    raw: bytes,
) -> None:
    _require_artifacts()
    record = tmp_path / f"{name}.yaml"
    record.write_bytes(raw)
    result = _run(record, schema_only=True)

    assert result.returncode != 0
    assert "PARSE" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("schema_only", (True, False), ids=("schema-only", "default"))
def test_pathologically_nested_json_rejects_without_recursion_traceback(
    tmp_path: Path,
    schema_only: bool,
) -> None:
    _require_artifacts()
    record = tmp_path / "deeply-nested.yaml"
    depth = 2_000
    record.write_text(
        '{"nested":' + "[" * depth + '"leaf"' + "]" * depth + "}\n",
        encoding="utf-8",
    )

    result = _run(record, schema_only=schema_only)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert any(code in result.stderr for code in ("PARSE", "SCHEMA"))


def test_string_walker_handles_deep_values_iteratively() -> None:
    _require_artifacts()
    module = _load_validator_module()
    nested: object = "leaf"
    for _ in range(2_000):
        nested = {"child": nested}

    assert list(module._walk_strings(nested)) == ["leaf"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "mutation",
    ("missing", "unknown", "top_unknown", "nested_unknown", "nonstring", "duplicate", "reordered"),
)
def test_schema_fails_closed_on_field_set_duplicates_and_order(
    tmp_path: Path,
    mutation: str,
) -> None:
    _require_artifacts()
    raw = EXAMPLE.read_text(encoding="utf-8")
    document = json.loads(raw)
    if mutation == "missing":
        del document["transcript"]["RUN_ID"]
        raw = json.dumps(document, ensure_ascii=False)
    elif mutation == "unknown":
        document["transcript"]["UNREVIEWED_EXTRA"] = PLACEHOLDER
        raw = json.dumps(document, ensure_ascii=False)
    elif mutation == "top_unknown":
        document["unreviewed_extra"] = PLACEHOLDER
        raw = json.dumps(document, ensure_ascii=False)
    elif mutation == "nested_unknown":
        document["preflight"]["unreviewed_extra"] = PLACEHOLDER
        raw = json.dumps(document, ensure_ascii=False)
    elif mutation == "nonstring":
        document["transcript"]["RUN_ID"] = 1
        raw = json.dumps(document, ensure_ascii=False)
    elif mutation == "reordered":
        transcript = document["transcript"]
        first = next(iter(transcript))
        value = transcript.pop(first)
        transcript[first] = value
        raw = json.dumps(document, ensure_ascii=False)
    else:
        raw = raw.replace(
            f'"RUN_ID": "{PLACEHOLDER}"',
            f'"RUN_ID": "{PLACEHOLDER}",\n    "RUN_ID": "{PLACEHOLDER}"',
            1,
        )
    record = tmp_path / f"{mutation}.yaml"
    record.write_text(raw + ("" if raw.endswith("\n") else "\n"), encoding="utf-8")

    result = _run(record, schema_only=True)
    assert result.returncode != 0
    assert any(code in result.stderr for code in ("DUPLICATE", "SCHEMA", "TRANSCRIPT_ORDER"))


@pytest.mark.parametrize(
    ("member", "unsafe_value"),
    (
        ("target", []),
        ("preflight", "not-a-mapping"),
        ("integrity", []),
    ),
)
@pytest.mark.parametrize("schema_only", (True, False), ids=("schema-only", "default"))
def test_invalid_nested_containers_reject_without_traceback(
    tmp_path: Path,
    member: str,
    unsafe_value: object,
    schema_only: bool,
) -> None:
    _require_artifacts()
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document[member] = unsafe_value
    record = tmp_path / f"invalid-{member}.yaml"
    _write_json(record, document)

    result = _run(record, schema_only=schema_only)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "SCHEMA" in result.stderr
    if not schema_only:
        assert "YAML_PREPARATION_ONLY" in result.stderr


def test_default_mode_rejects_placeholders_and_never_authorizes_the_draft() -> None:
    _require_artifacts()
    before = DRAFT.read_bytes()
    result = _run(DRAFT)

    assert result.returncode != 0
    assert "PLACEHOLDER" in result.stderr
    assert "YAML_PREPARATION_ONLY" in result.stderr
    assert result.stdout == ""
    assert DRAFT.read_bytes() == before


@pytest.mark.parametrize(
    "unsafe_value",
    (2, "9" * 5000),
    ids=("nonstring-nlink", "pathological-nlink"),
)
def test_default_mode_rejects_unsafe_numeric_values_without_traceback(
    native_tmp_path: Path,
    unsafe_value: object,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = document["transcript"]
    assert isinstance(transcript, dict)
    transcript["ORIG_PGDATA_NLINK"] = unsafe_value
    _refresh_canonical_digest(document)
    record = native_tmp_path / "unsafe-numeric.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert any(code in result.stderr for code in ("SCHEMA", "ORIGINAL_IDENTITY_MISMATCH"))
    assert "YAML_PREPARATION_ONLY" in result.stderr


def test_complete_isolated_record_still_stops_at_yaml_nonauthority_boundary(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    _, record, runbook = _write_complete_fixture(native_tmp_path)
    before = record.read_bytes()

    schema_result = _run(record, schema_only=True, runbook=runbook)
    assert schema_result.returncode == 0, schema_result.stderr
    assert schema_result.stdout.startswith("NON-AUTHORIZING:")

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert result.stderr.strip() == "REJECTED: YAML_PREPARATION_ONLY"
    assert result.stdout == ""
    assert record.read_bytes() == before


def test_complete_record_requires_independently_supplied_evidence_root(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    _, record, runbook = _write_complete_fixture(native_tmp_path)

    result = _run(
        record,
        runbook=runbook,
        trusted_evidence_root=None,
    )

    assert result.returncode != 0
    assert "TRUSTED_EVIDENCE_ROOT_REQUIRED" in result.stderr
    assert result.stdout == ""


def test_trusted_evidence_root_must_match_the_declared_private_root(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    _, record, runbook = _write_complete_fixture(native_tmp_path)
    different_root = native_tmp_path / "independent-different-root"
    different_root.mkdir(mode=0o700)

    result = _run(
        record,
        runbook=runbook,
        trusted_evidence_root=different_root,
    )

    assert result.returncode != 0
    assert "TRUSTED_EVIDENCE_ROOT_MISMATCH" in result.stderr
    assert result.stdout == ""


def test_trusted_evidence_root_must_be_owned_private_0700(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, record, runbook = _write_complete_fixture(native_tmp_path)
    evidence_root = Path(_nested(document, "transcript")["EVIDENCE_PARENT"])
    evidence_root.chmod(0o755)

    result = _run(record, runbook=runbook)

    assert result.returncode != 0
    assert "TRUSTED_EVIDENCE_ROOT_INVALID" in result.stderr
    assert result.stdout == ""


def test_future_review_and_revalidation_are_rejected(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    future_review = _timestamp(datetime.now(UTC) + timedelta(minutes=30))
    _nested(document, "preflight")["review_completed_at_utc"] = future_review
    _nested(document, "evidence_age")["current_revalidation"] = future_review
    record = native_tmp_path / "future-review.yaml"
    _write_json(record, document)

    result = _run(
        record,
        runbook=runbook,
        trusted_evidence_root=None,
    )

    assert result.returncode != 0
    assert "FUTURE_REVIEW" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("control", ("\u0000", "\u001b", "\u007f"))
@pytest.mark.parametrize("schema_only", (True, False), ids=("schema-only", "default"))
@pytest.mark.parametrize(
    ("member", "field"),
    (
        ("integrity", "expected_catalog_artifact"),
        ("transcript", "CHANGE_ARTIFACT"),
    ),
)
def test_control_characters_in_paths_fail_closed_without_traceback(
    native_tmp_path: Path,
    member: str,
    field: str,
    schema_only: bool,
    control: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    mapping = _nested(document, member)
    mapping[field] += f"{control}alias"
    if member == "transcript":
        _refresh_canonical_digest(document)
    record = native_tmp_path / "control-path.yaml"
    _write_json(record, document)

    result = _run(
        record,
        schema_only=schema_only,
        runbook=runbook,
        trusted_evidence_root=None,
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "SCHEMA" in result.stderr
    if not schema_only:
        assert "YAML_PREPARATION_ONLY" in result.stderr


def _load_validator_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "postgres_recovery_approval_validator_under_test",
        VALIDATOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("swap_kind", ("file", "ancestor", "ancestor_symlink"))
def test_stable_snapshot_rejects_path_replacement_after_read(
    native_tmp_path: Path,
    swap_kind: str,
) -> None:
    _require_artifacts()
    module = _load_validator_module()
    root = native_tmp_path / "snapshot-root"
    root.mkdir()
    artifact = root / "artifact.txt"
    artifact.write_text("reviewed bytes\n", encoding="utf-8")

    def replace_after_read() -> None:
        if swap_kind == "file":
            replacement = root / "replacement.txt"
            replacement.write_text("replacement bytes\n", encoding="utf-8")
            os.replace(replacement, artifact)
            return
        moved = native_tmp_path / "snapshot-root-moved"
        root.rename(moved)
        if swap_kind == "ancestor":
            root.mkdir()
            (root / "artifact.txt").write_text("replacement bytes\n", encoding="utf-8")
        else:
            root.symlink_to(moved, target_is_directory=True)

    snapshot = module._stable_file_snapshot(  # type: ignore[attr-defined]
        artifact,
        collect=True,
        _after_read=replace_after_read,
    )

    assert snapshot is None


@pytest.mark.parametrize("loader", ("json", "text"))
def test_primary_input_loaders_use_stable_descriptor_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    loader: str,
) -> None:
    _require_artifacts()
    module = _load_validator_module()
    payload = b'{"safe": "value"}' if loader == "json" else b"stable text"
    calls: list[tuple[Path, bool]] = []

    def fake_snapshot(
        path: Path,
        *,
        collect: bool,
        **_kwargs: object,
    ) -> tuple[str, bytes, None]:
        calls.append((path, collect))
        return "0" * 64, payload, None

    monkeypatch.setattr(module, "_stable_file_snapshot", fake_snapshot)
    nonexistent = Path("/does/not/exist/review-input")
    if loader == "json":
        observed = module._load_json_subset(nonexistent)  # type: ignore[attr-defined]
        assert observed == {"safe": "value"}
    else:
        observed = module._read_text(nonexistent)  # type: ignore[attr-defined]
        assert observed == "stable text"
    assert calls == [(nonexistent, True)]


def _nested(document: dict[str, object], member: str) -> dict[str, str]:
    value = document[member]
    assert isinstance(value, dict)
    return value  # type: ignore[return-value]


def _mutate_case(name: str, document: dict[str, object], tmp_path: Path) -> None:
    transcript = _nested(document, "transcript")
    preflight = _nested(document, "preflight")
    integrity = _nested(document, "integrity")
    target = _nested(document, "target")

    if name == "placeholder":
        transcript["REVIEWER_NAME"] = PLACEHOLDER
    elif name == "expired":
        transcript["EXPIRES_AT_UTC"] = _timestamp(datetime.now(UTC) - timedelta(seconds=1))
    elif name == "future_approval":
        transcript["APPROVED_AT_UTC"] = _timestamp(datetime.now(UTC) + timedelta(minutes=30))
        transcript["EXPIRES_AT_UTC"] = _timestamp(datetime.now(UTC) + timedelta(hours=2))
    elif name == "window_too_long":
        approved = _parse_timestamp(transcript["APPROVED_AT_UTC"])
        transcript["EXPIRES_AT_UTC"] = _timestamp(approved + timedelta(hours=4, seconds=1))
    elif name == "invalid_timestamp":
        transcript["APPROVED_AT_UTC"] = "2026-02-30T00:00:00Z"
    elif name == "zero_window":
        transcript["EXPIRES_AT_UTC"] = transcript["APPROVED_AT_UTC"]
    elif name == "runbook_hash":
        transcript["RUNBOOK_SHA256"] = "0" * 64
    elif name == "source_commit":
        transcript["SOURCE_COMMIT"] = "0" * 40
    elif name == "source_tree":
        transcript["SOURCE_TREE"] = "f" * 40
    elif name == "migration_digest":
        transcript["MIGRATION_SHA256"] = "0" * 64
    elif name == "catalog_digest":
        transcript["EXPECTED_CATALOG_SHA256"] = "0" * 64
    elif name == "change_digest":
        transcript["CHANGE_ARTIFACT_SHA256"] = "0" * 64
    elif name == "identity":
        transcript["ORIG_SYSTEM_ID"] = "1111111111111111111"
    elif name == "identity_evidence_digest":
        integrity["original_identity_evidence_sha256"] = "0" * 64
    elif name == "pgdata_fingerprint":
        integrity["original_pgdata_fingerprint"] = "8" * 64
    elif name == "fixed_cluster":
        target["cluster"] = "wrong-cluster"
    elif name == "fixed_host":
        target["host"] = "0.0.0.0"
    elif name == "fixed_port":
        target["port"] = "5432"
    elif name == "fixed_database":
        target["database"] = "postgres"
    elif name == "fixed_pgdata":
        target["pgdata"] = str((tmp_path / "wrong-pgdata").resolve())
    elif name == "incomplete_baseline":
        preflight["baseline_complete"] = "false"
    elif name == "same_reviewer":
        transcript["REVIEWER_NAME"] = transcript["OPERATOR_NAME"]
    elif name == "review_out_of_window":
        expires = _parse_timestamp(transcript["EXPIRES_AT_UTC"])
        preflight["review_completed_at_utc"] = _timestamp(expires + timedelta(seconds=1))
    elif name == "wrong_operation":
        preflight["operation"] = "PRESERVATION_ONLY"
    elif name == "wrong_scope":
        preflight["scope"] = "ALL_PRODUCTION"
    elif name == "non_paper":
        preflight["execution_mode"] = "LIVE"
    elif name == "live_execution":
        preflight["live_execution_approved"] = "true"
    elif name == "live_trading":
        preflight["live_trading_approved"] = "true"
    elif name == "unsafe_services":
        preflight["all_listed_units_inactive"] = "false"
    elif name == "unsafe_port":
        preflight["original_port_55432_listener_absent"] = "false"
    elif name == "canonical_digest":
        integrity["canonical_transcript_sha256"] = "0" * 64
        return
    elif name == "decision":
        transcript["DECISION"] = "APPROVED_SOMETHING_ELSE"
    elif name == "operator_attestation":
        transcript["OPERATOR_ATTESTATION"] = "YES"
    elif name == "reviewer_attestation":
        transcript["REVIEWER_ATTESTATION"] = "YES"
    elif name == "yes_gate":
        transcript["ALLOW_ONE_ORIGINAL_START"] = "NO"
    elif name == "catalog_constant":
        transcript["EXPECTED_CATALOG_QUERY_ID"] = "UNREVIEWED_QUERY"
    elif name == "policy_constant":
        transcript["ORIGINAL_START_POLICY_ID"] = "START_WITH_DEFAULTS"
    else:  # pragma: no cover - test table guards this branch
        raise AssertionError(name)
    _refresh_canonical_digest(document)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("placeholder", "PLACEHOLDER"),
        ("expired", "EXPIRED"),
        ("future_approval", "FUTURE_APPROVAL"),
        ("window_too_long", "WINDOW_TOO_LONG"),
        ("invalid_timestamp", "INVALID_TIME"),
        ("zero_window", "INVALID_WINDOW"),
        ("runbook_hash", "RUNBOOK_HASH_MISMATCH"),
        ("source_commit", "SOURCE_COMMIT_MISMATCH"),
        ("source_tree", "SOURCE_TREE_MISMATCH"),
        ("migration_digest", "MIGRATION_DIGEST_MISMATCH"),
        ("catalog_digest", "CATALOG_DIGEST_MISMATCH"),
        ("change_digest", "CHANGE_ARTIFACT_DIGEST_MISMATCH"),
        ("identity", "ORIGINAL_IDENTITY_MISMATCH"),
        ("identity_evidence_digest", "ORIGINAL_IDENTITY_EVIDENCE_DIGEST_MISMATCH"),
        ("pgdata_fingerprint", "PGDATA_FINGERPRINT_MISMATCH"),
        ("fixed_cluster", "FIXED_TARGET_MISMATCH"),
        ("fixed_host", "FIXED_TARGET_MISMATCH"),
        ("fixed_port", "FIXED_TARGET_MISMATCH"),
        ("fixed_database", "FIXED_TARGET_MISMATCH"),
        ("fixed_pgdata", "FIXED_TARGET_MISMATCH"),
        ("incomplete_baseline", "INCOMPLETE_BASELINE"),
        ("same_reviewer", "SAME_OPERATOR_REVIEWER"),
        ("review_out_of_window", "REVIEW_OUT_OF_WINDOW"),
        ("wrong_operation", "WRONG_OPERATION_OR_SCOPE"),
        ("wrong_scope", "WRONG_OPERATION_OR_SCOPE"),
        ("non_paper", "NON_PAPER_MODE"),
        ("live_execution", "LIVE_GATE_NOT_FALSE"),
        ("live_trading", "LIVE_GATE_NOT_FALSE"),
        ("unsafe_services", "UNSAFE_SERVICE_BASELINE"),
        ("unsafe_port", "UNSAFE_PORT_BASELINE"),
        ("canonical_digest", "CANONICAL_TRANSCRIPT_DIGEST_MISMATCH"),
        ("decision", "INVALID_FIXED_CONSTANT"),
        ("operator_attestation", "INVALID_FIXED_CONSTANT"),
        ("reviewer_attestation", "INVALID_FIXED_CONSTANT"),
        ("yes_gate", "INVALID_FIXED_CONSTANT"),
        ("catalog_constant", "INVALID_FIXED_CONSTANT"),
        ("policy_constant", "INVALID_FIXED_CONSTANT"),
    ),
)
def test_authorization_checks_fail_closed_for_each_binding_class(
    native_tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    _mutate_case(case, document, native_tmp_path)
    record = native_tmp_path / f"{case}.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert expected in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("artifact", ("catalog", "change", "identity"))
def test_local_artifacts_outside_reviewed_evidence_root_are_rejected(
    native_tmp_path: Path,
    artifact: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    integrity = _nested(document, "integrity")
    outside = native_tmp_path / f"outside-{artifact}.bin"

    if artifact == "catalog":
        source = Path(integrity["expected_catalog_artifact"])
        outside.write_bytes(source.read_bytes())
        integrity["expected_catalog_artifact"] = str(outside.resolve())
        transcript["EXPECTED_CATALOG_SHA256"] = _sha(outside)
    elif artifact == "change":
        source = Path(transcript["CHANGE_ARTIFACT"])
        outside.write_bytes(source.read_bytes())
        outside.chmod(0o600)
        transcript["CHANGE_ARTIFACT"] = str(outside.resolve())
        transcript["CHANGE_ARTIFACT_SHA256"] = _sha(outside)
    else:
        source = Path(integrity["original_identity_evidence"])
        outside.write_bytes(source.read_bytes())
        outside.chmod(0o600)
        integrity["original_identity_evidence"] = str(outside.resolve())
        integrity["original_identity_evidence_sha256"] = _sha(outside)
    _refresh_canonical_digest(document)
    record = native_tmp_path / f"outside-{artifact}.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert "ARTIFACT_PATH_OUTSIDE_REVIEW_ROOT" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("artifact", ("catalog", "change", "identity"))
def test_review_artifacts_must_be_owned_single_link_files(
    native_tmp_path: Path,
    artifact: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    integrity = _nested(document, "integrity")
    paths = {
        "catalog": Path(integrity["expected_catalog_artifact"]),
        "change": Path(transcript["CHANGE_ARTIFACT"]),
        "identity": Path(integrity["original_identity_evidence"]),
    }
    os.link(paths[artifact], paths[artifact].with_name(f"{artifact}-hardlink"))
    record = native_tmp_path / f"hardlinked-{artifact}.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)

    assert result.returncode != 0
    expected = {
        "catalog": "CATALOG_DIGEST_MISMATCH",
        "change": "CHANGE_ARTIFACT_DIGEST_MISMATCH",
        "identity": "ORIGINAL_IDENTITY_EVIDENCE_DIGEST_MISMATCH",
    }
    assert expected[artifact] in result.stderr
    assert result.stdout == ""


def test_deep_identity_evidence_rejects_without_recursion_traceback(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    integrity = _nested(document, "integrity")
    identity = Path(integrity["original_identity_evidence"])
    depth = 10_000
    identity.write_text(
        '{"nested":' + "[" * depth + '"leaf"' + "]" * depth + "}\n",
        encoding="utf-8",
    )
    identity.chmod(0o600)
    integrity["original_identity_evidence_sha256"] = _sha(identity)
    record = native_tmp_path / "deep-identity-evidence.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "ORIGINAL_IDENTITY_MISMATCH" in result.stderr
    assert "YAML_PREPARATION_ONLY" in result.stderr


def test_credential_named_artifact_is_rejected_even_under_trusted_root(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    integrity = _nested(document, "integrity")
    evidence_root = Path(transcript["EVIDENCE_PARENT"])
    source = Path(integrity["expected_catalog_artifact"])
    credential_alias = evidence_root / "oauth-access-token.txt"
    credential_alias.write_bytes(source.read_bytes())
    integrity["expected_catalog_artifact"] = str(credential_alias.resolve())
    transcript["EXPECTED_CATALOG_SHA256"] = _sha(credential_alias)
    _refresh_canonical_digest(document)
    record = native_tmp_path / "credential-alias.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)

    assert result.returncode != 0
    assert "ARTIFACT_PATH_OUTSIDE_REVIEW_ROOT" in result.stderr
    assert result.stdout == ""


def test_migration_binding_rejects_noncanonical_repository_relative_path(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    integrity = _nested(document, "integrity")
    source = Path(integrity["source_repository"])
    alternate = source / "alembic/versions/alternate.py"
    alternate.write_text("revision = 'alternate'\n", encoding="utf-8")
    _git(["add", "."], source)
    _git(["commit", "-q", "-m", "add alternate fixture"], source)
    transcript["SOURCE_COMMIT"] = _git(["rev-parse", "HEAD"], source)
    transcript["SOURCE_TREE"] = _git(["rev-parse", "HEAD^{tree}"], source)
    transcript["MIGRATION_SHA256"] = _sha(alternate)
    integrity["migration_artifact"] = str(alternate.resolve())
    _refresh_canonical_digest(document)
    record = native_tmp_path / "alternate-migration.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert "MIGRATION_DIGEST_MISMATCH" in result.stderr


def test_validator_git_calls_are_object_only_and_disable_lazy_fetch() -> None:
    _require_artifacts()
    source = VALIDATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    observed_commands: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_git_run"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.List)
            and node.args[1].elts
            and isinstance(node.args[1].elts[0], ast.Constant)
            and isinstance(node.args[1].elts[0].value, str)
        ):
            continue
        observed_commands.add(node.args[1].elts[0].value)

    assert observed_commands == {"rev-parse", "ls-tree", "cat-file"}
    assert '"GIT_NO_LAZY_FETCH": "1"' in source


@pytest.mark.parametrize("substitution", ("skip-worktree", "ignored"))
def test_migration_binding_rejects_worktree_bytes_not_in_declared_git_tree(
    native_tmp_path: Path,
    substitution: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    integrity = _nested(document, "integrity")
    source = Path(integrity["source_repository"])
    relative = Path("alembic/versions/0004_durable_research_jobs.py")
    migration = source / relative

    if substitution == "skip-worktree":
        _git(["update-index", "--skip-worktree", relative.as_posix()], source)
        migration.write_text("revision = 'uncommitted_hidden_substitution'\n", encoding="utf-8")
    else:
        _git(["rm", "--cached", relative.as_posix()], source)
        (source / ".gitignore").write_text(f"/{relative.as_posix()}\n", encoding="utf-8")
        _git(["add", ".gitignore"], source)
        _git(["commit", "-q", "-m", "exclude migration blob"], source)
        transcript["SOURCE_COMMIT"] = _git(["rev-parse", "HEAD"], source)
        transcript["SOURCE_TREE"] = _git(["rev-parse", "HEAD^{tree}"], source)

    transcript["MIGRATION_SHA256"] = _sha(migration)
    _refresh_canonical_digest(document)
    record = native_tmp_path / f"hidden-migration-{substitution}.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)

    assert result.returncode != 0
    assert "MIGRATION_DIGEST_MISMATCH" in result.stderr
    assert result.stdout == ""


def test_runbook_argument_must_be_the_declared_repository_file(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, _runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    copied_runbook = native_tmp_path / "caller-selected-runbook-copy.md"
    copied_runbook.write_bytes(RUNBOOK.read_bytes())
    transcript["RUNBOOK_SHA256"] = _sha(copied_runbook)
    _refresh_canonical_digest(document)
    record = native_tmp_path / "copied-runbook.yaml"
    _write_json(record, document)

    result = _run(record, runbook=copied_runbook)

    assert result.returncode != 0
    assert "RUNBOOK_REPOSITORY_BINDING_MISMATCH" in result.stderr
    assert result.stdout == ""


def test_runbook_worktree_bytes_must_match_declared_commit_blob(
    native_tmp_path: Path,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    integrity = _nested(document, "integrity")
    source = Path(integrity["source_repository"])
    relative = runbook.relative_to(source)
    _git(["update-index", "--skip-worktree", relative.as_posix()], source)
    runbook.write_text(
        runbook.read_text(encoding="utf-8") + "\n<!-- hidden substitution -->\n",
        encoding="utf-8",
    )
    transcript["RUNBOOK_SHA256"] = _sha(runbook)
    _refresh_canonical_digest(document)
    record = native_tmp_path / "hidden-runbook.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)

    assert result.returncode != 0
    assert "RUNBOOK_HASH_MISMATCH" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "command_text",
    (
        "pg_resetwal --force /somewhere",
        "initdb /somewhere",
        "rm -rf /somewhere",
        "DROP TABLE jobs",
        "TRUNCATE audit_events",
        "DELETE FROM job_events",
        "VACUUM FULL jobs",
        "REINDEX DATABASE trading_agent",
        "GRANT ALL ON DATABASE trading_agent TO someone",
        "REVOKE CONNECT ON DATABASE trading_agent FROM PUBLIC",
        "ALTER SYSTEM SET shared_preload_libraries = 'x'",
        "kill -9 123",
        "systemctl start postgresql",
        "systemctl --user start postgresql",
        "systemctl --user restart postgresql",
        "systemctl --user enable postgresql",
        "pg_ctl -D /somewhere start",
        "source /protected/credentials.env",
        "eval $(cat /protected/credentials.env)",
        "set -x",
    ),
)
def test_prohibited_command_text_is_rejected(
    native_tmp_path: Path,
    command_text: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    transcript["CHANGE_ID"] = command_text
    _refresh_canonical_digest(document)
    record = native_tmp_path / "prohibited.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert "PROHIBITED_COMMAND_TEXT" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "secret_text",
    (
        "postgresql://" + "someone:credential@example.invalid/database",
        "password=credential-value",
        "PGPASSWORD=credential-value",
        "-----BEGIN " + "PRIVATE KEY-----",
    ),
    ids=("dsn-uri", "field-assignment", "environment-assignment", "pem-boundary"),
)
def test_secret_or_dsn_content_is_rejected(
    native_tmp_path: Path,
    secret_text: str,
) -> None:
    _require_artifacts()
    document, runbook = _complete_fixture(native_tmp_path)
    transcript = _nested(document, "transcript")
    transcript["CHANGE_ID"] = secret_text
    _refresh_canonical_digest(document)
    record = native_tmp_path / "secret.yaml"
    _write_json(record, document)

    result = _run(record, runbook=runbook)
    assert result.returncode != 0
    assert "SECRET_CONTENT" in result.stderr
    assert secret_text not in result.stderr
    assert result.stdout == ""


def test_schema_has_no_locally_toggleable_yaml_approval_state() -> None:
    _require_artifacts()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    state = schema["properties"]["authorization_state"]

    assert state == {"const": "DRAFT_NOT_AUTHORIZED", "type": "string"}
    assert schema["properties"]["authorization_notice"]["const"] == NOTICE


def test_review_document_explains_identity_digest_and_transcript_boundaries() -> None:
    _require_artifacts()
    text = " ".join(REVIEW.read_text(encoding="utf-8").split())

    assert "exactly 50" in text
    assert "literal-TAB" in text
    assert "mode 0600" in text
    assert "YAML" in text and "preparation" in text
    assert "OPERATOR" in text and "different named REVIEWER" in text
    assert "authenticated change control" in text
    assert "not a cryptographic signature" in text
    assert NOTICE in text
    assert "stdlib" in text
