#!/usr/bin/env python3
"""Validate a non-authorizing PostgreSQL recovery approval preparation record.

The accepted ``.yaml`` representation is deliberately restricted to JSON, a
deterministic subset of YAML 1.2.  This program never renders an executable
transcript and never treats YAML as an ``APPROVAL_RECORD``.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Iterable


PLACEHOLDER = "REQUIRES_REVIEWER_INPUT"
NOTICE = "RECOVERY APPROVAL STATUS: DRAFT — NOT AUTHORIZED"

TRANSCRIPT_FIELDS = (
    "DECISION",
    "CHANGE_ID",
    "INCIDENT_ID",
    "CHANGE_ARTIFACT",
    "CHANGE_ARTIFACT_SHA256",
    "APPROVED_AT_UTC",
    "EXPIRES_AT_UTC",
    "OPERATOR_NAME",
    "REVIEWER_NAME",
    "OPERATOR_ATTESTATION",
    "REVIEWER_ATTESTATION",
    "RUN_ID",
    "RUNBOOK_SHA256",
    "SOURCE_COMMIT",
    "SOURCE_TREE",
    "MIGRATION_SHA256",
    "EXPECTED_CATALOG_SHA256",
    "EXPECTED_CATALOG_QUERY_ID",
    "EXPECTED_CATALOG_PROVENANCE",
    "EXPECTED_CATALOG_REVIEW_ATTESTATION",
    "ORIG_SYSTEM_ID",
    "ORIG_PGDATA_NLINK",
    "ORIG_SOCKET_NLINK",
    "ORIG_LOG_DIR_NLINK",
    "ORIG_LOG_NLINK",
    "EVIDENCE_PARENT",
    "PRESERVE_PARENT",
    "BACKUP_PARENT",
    "SECRET_PARENT",
    "ISO_HOST",
    "ISO_PORT",
    "ISO_ADMIN_DB",
    "ISO_RESTORE_DB",
    "ISO_PGDATA",
    "ISO_SYSTEM_ID",
    "ISO_SOCKET",
    "ISO_ADMIN_ENV",
    "ALLOW_STOP_ALL_LISTED_UNITS",
    "ALLOW_OFFLINE_COLD_COPY",
    "ALLOW_ONE_ORIGINAL_START",
    "ALLOW_ONE_ORIGINAL_STOP",
    "ALLOW_READ_ONLY_VERIFICATION",
    "ALLOW_IMMEDIATE_LOGICAL_BACKUP",
    "ALLOW_MIGRATE_ORIGINAL_IF_0003",
    "ALLOW_ISOLATED_RESTORE",
    "ACCEPT_INTERRUPTED_SHUTDOWN",
    "ACCEPT_CHECKSUMS_DISABLED",
    "ACKNOWLEDGE_NO_PITR",
    "RECOVERY_LOG_POLICY_ID",
    "ORIGINAL_START_POLICY_ID",
)

TOP_FIELDS = (
    "document_kind",
    "format_version",
    "record_id",
    "authorization_state",
    "evidence_age",
    "safety_baseline",
    "expected_pre_recovery",
    "stale_pid_evidence",
    "recovery_review",
    "procedure_references",
    "backup_restore_controls",
    "target",
    "preflight",
    "integrity",
    "transcript",
    "authorization_notice",
)
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
TARGET_FIELDS = (
    "postgresql_major",
    "cluster",
    "host",
    "port",
    "database",
    "pgdata",
)
PREFLIGHT_FIELDS = (
    "operation",
    "scope",
    "execution_mode",
    "live_execution_approved",
    "live_trading_approved",
    "baseline_complete",
    "all_listed_units_inactive",
    "original_port_55432_listener_absent",
    "review_completed_at_utc",
)
INTEGRITY_FIELDS = (
    "canonical_transcript_sha256",
    "source_repository",
    "migration_artifact",
    "expected_catalog_artifact",
    "original_identity_evidence",
    "original_identity_evidence_sha256",
    "original_pgdata_fingerprint",
)
IDENTITY_FIELDS = (
    "postgresql_major",
    "cluster",
    "host",
    "port",
    "database",
    "pgdata",
    "system_id",
    "pgdata_nlink",
    "socket_nlink",
    "log_dir_nlink",
    "log_nlink",
    "pgdata_fingerprint",
)

FIXED_TARGET = {
    "postgresql_major": "16",
    "cluster": "trading-agent",
    "host": "127.0.0.1",
    "port": "55432",
    "database": "trading_agent",
    "pgdata": "/home/thenam176/.local/share/trading-agent/postgres/16/trading-agent",
}
FIXED_EVIDENCE_AGE = {
    "trading_safety_observed_at_utc": "2026-07-11T23:46:34Z",
    "job_plane_observed_at_utc": "2026-07-16T15:12:29Z",
    "database_incident_observed_date": "2026-07-16",
}
FIXED_SAFETY_BASELINE = {
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
FIXED_EXPECTED_PRE_RECOVERY = {
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
RUNBOOK_REFERENCE_ROOT = "docs/production/runbooks/postgresql-preserve-recover.md"
FIXED_STALE_PID_EVIDENCE = {
    "classification": "STALE_PID",
    "incident_evidence_reference": f"{RUNBOOK_REFERENCE_ROOT}#1-known-incident-state",
    "zero_write_gate_reference": (
        f"{RUNBOOK_REFERENCE_ROOT}#52-zero-write-canonical-path-overlap-storage-and-destination-gates"
    ),
    "pid_file_action": "PRESERVE_UNCHANGED",
}
FIXED_PROCEDURE_REFERENCES = {
    "prohibited_actions_reference": (
        f"{RUNBOOK_REFERENCE_ROOT}#2-absolute-prohibitions-and-stop-conditions"
    ),
    "exact_runbook_commands_reference": (
        f"{RUNBOOK_REFERENCE_ROOT}#5-conditional-operator-procedure"
    ),
    "execution_status": "NOT_EXECUTED",
}
FIXED_BACKUP_RESTORE_REFERENCES = {
    "backup_permission_reference": (
        f"{RUNBOOK_REFERENCE_ROOT}#4-exact-dual-reviewed-execution-transcript"
    ),
    "restore_reference": (
        f"{RUNBOOK_REFERENCE_ROOT}#515-create-one-isolated-database-restore-and-compare-exact-gates"
    ),
    "rollback_reference": f"{RUNBOOK_REFERENCE_ROOT}#6-non-destructive-rollback",
    "stop_conditions_reference": (
        f"{RUNBOOK_REFERENCE_ROOT}#2-absolute-prohibitions-and-stop-conditions"
    ),
}
RECOVERY_REVIEW_VALUES = {
    "postmaster_status_outcome": "OFFLINE_CONFIRMED",
    "process_identity_outcome": "NO_LIVE_POSTMASTER_FOR_ORIGINAL_PGDATA",
    "port_55432_outcome": "CLOSED",
    "data_directory_outcome": "MATCHED_REVIEWED_IDENTITY",
    "independent_disk_outcome": "INDEPENDENT_CAPACITY_CONFIRMED",
    "recovery_log_outcome": (
        "INTERRUPTED_SHUTDOWN_CLASSIFIED_WITHOUT_DENIED_PATTERNS"
    ),
}
BACKUP_RESTORE_OUTCOMES = {
    "backup_permission_outcome": "AUTHENTICATED_DUAL_REVIEW_CONFIRMED",
    "restore_outcome": "ISOLATED_RESTORE_ONLY_REVIEWED",
    "stop_conditions_outcome": "STOP_CONDITIONS_REVIEWED",
}
FIXED_TRANSCRIPT = {
    "DECISION": "APPROVED_POSTGRESQL16_RECOVERY_SUBGATE",
    "OPERATOR_ATTESTATION": "I_APPROVE_THIS_EXACT_RECOVERY_TRANSCRIPT",
    "REVIEWER_ATTESTATION": "I_INDEPENDENTLY_REVIEWED_THIS_EXACT_RECOVERY_TRANSCRIPT",
    "EXPECTED_CATALOG_QUERY_ID": "PG16_COMPLETE_RELATION_CATALOG_V2",
    "EXPECTED_CATALOG_PROVENANCE": "PRECOMPUTED_CLEAN_DISPOSABLE_PG16_EXACT_0001_0004_CATALOG_V2",
    "EXPECTED_CATALOG_REVIEW_ATTESTATION": "INDEPENDENTLY_REVIEWED_NOT_FROM_INCIDENT_OR_THIS_RUN_RESTORE",
    "ISO_ADMIN_DB": "postgres",
    "RECOVERY_LOG_POLICY_ID": "PG16_INTERRUPTED_RECOVERY_V1",
    "ORIGINAL_START_POLICY_ID": "PG16_FAIL_CLOSED_MAINTENANCE_START_V1",
}
YES_FIELDS = (
    "ALLOW_STOP_ALL_LISTED_UNITS",
    "ALLOW_OFFLINE_COLD_COPY",
    "ALLOW_ONE_ORIGINAL_START",
    "ALLOW_ONE_ORIGINAL_STOP",
    "ALLOW_READ_ONLY_VERIFICATION",
    "ALLOW_IMMEDIATE_LOGICAL_BACKUP",
    "ALLOW_MIGRATE_ORIGINAL_IF_0003",
    "ALLOW_ISOLATED_RESTORE",
    "ACCEPT_INTERRUPTED_SHUTDOWN",
    "ACCEPT_CHECKSUMS_DISABLED",
    "ACKNOWLEDGE_NO_PITR",
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{1,127}$")
CHANGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
SYSTEM_ID_RE = re.compile(r"^[0-9]{10,24}$")
POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
ISO_PORT_RE = re.compile(r"^[1-9][0-9]{0,4}$")
RESTORE_DB_RE = re.compile(r"^trading_agent_restore_[A-Za-z0-9_]{1,48}$")
RECORD_ID_RE = re.compile(r"^POSTGRES_RECOVERY_[A-Z0-9_-]{1,80}$")

SECRET_RE = re.compile(
    r"(?i)(?:postgres(?:ql)?://|password\s*=|pgpassword\s*=|BEGIN [A-Z ]*PRIVATE KEY)"
)
PROHIBITED_COMMAND_RE = re.compile(
    r"(?ix)(?:"
    r"\bpg_resetwal\b|\binitdb\b|\brm\s+-rf\b|"
    r"\bdrop\s+(?:table|database|schema|role)\b|\btruncate\b|"
    r"\bdelete\s+from\b|\bvacuum\b|\breindex\b|\bgrant\b|\brevoke\b|"
    r"\balter\s+system\b|\bkill(?:all)?\b|"
    r"\bsystemctl\b[^\r\n;&|]*\b(?:start|restart|enable)\b|"
    r"\bpg_ctl\b[^\r\n]*\bstart\b|"
    r"(?:^|\s)source\s+|(?:^|\s)eval\s+|(?:^|\s)set\s+-x(?:\s|$)"
    r")"
)


class InputError(ValueError):
    """A safe, non-disclosing input classification."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _reject_constant(_value: str) -> None:
    raise InputError("PARSE")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError("DUPLICATE")
        result[key] = value
    return result


def _absolute_cli_path(path: Path) -> Path:
    return path if path.is_absolute() else Path(os.path.abspath(path))


def _load_json_subset_snapshot(
    path: Path,
) -> tuple[dict[str, Any], str, os.stat_result]:
    try:
        snapshot = _stable_file_snapshot(_absolute_cli_path(path), collect=True)
        if snapshot is None:
            raise InputError("INPUT_FILE")
        digest, raw, metadata = snapshot
        if raw is None:
            raise InputError("INPUT_FILE")
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except InputError:
        raise
    except (
        OSError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise InputError("PARSE") from error
    if not isinstance(value, dict):
        raise InputError("SCHEMA")
    return value, digest, metadata


def _load_json_subset(path: Path, *, require_safe_file: bool = True) -> dict[str, Any]:
    del require_safe_file
    return _load_json_subset_snapshot(path)[0]


def _read_text_snapshot(path: Path) -> tuple[str, str, os.stat_result]:
    try:
        snapshot = _stable_file_snapshot(_absolute_cli_path(path), collect=True)
        if snapshot is None:
            raise InputError("RUNBOOK_SCHEMA_DRIFT")
        digest, raw, metadata = snapshot
        if raw is None:
            raise InputError("RUNBOOK_SCHEMA_DRIFT")
        return raw.decode("utf-8", errors="strict"), digest, metadata
    except (OSError, UnicodeError, ValueError) as error:
        raise InputError("RUNBOOK_SCHEMA_DRIFT") from error


def _read_text(path: Path) -> str:
    return _read_text_snapshot(path)[0]


def _extract_runbook_fields(source: str) -> tuple[str, ...]:
    try:
        section = source.split("## 4. Exact dual-reviewed execution transcript", 1)[1]
        prose_block = section.split("~~~text\n", 1)[1].split("\n~~~", 1)[0]
        prose_fields = tuple(
            line.split("\t", 1)[0] for line in prose_block.splitlines() if line
        )
        if not all("\t" in line for line in prose_block.splitlines() if line):
            raise InputError("RUNBOOK_SCHEMA_DRIFT")
        parser_block = source.split("required_keys=(\n", 1)[1].split(
            "\n)\ntest ", 1
        )[0]
        parser_fields = tuple(re.findall(r"\b[A-Z][A-Z0-9_]+\b", parser_block))
    except (IndexError, ValueError) as error:
        raise InputError("RUNBOOK_SCHEMA_DRIFT") from error
    if (
        len(prose_fields) != 50
        or len(set(prose_fields)) != 50
        or prose_fields != parser_fields
        or prose_fields != TRANSCRIPT_FIELDS
    ):
        raise InputError("RUNBOOK_SCHEMA_DRIFT")
    return prose_fields


def _schema_matches_runbook(schema: dict[str, Any], fields: tuple[str, ...]) -> bool:
    try:
        transcript = schema["properties"]["transcript"]
        return (
            schema["x-runbook-transcript-order"] == list(fields)
            and transcript["required"] == list(fields)
            and tuple(transcript["properties"]) == fields
            and transcript["minProperties"] == 50
            and transcript["maxProperties"] == 50
            and transcript["additionalProperties"] is False
            and schema["properties"]["authorization_state"]
            == {"const": "DRAFT_NOT_AUTHORIZED", "type": "string"}
            and schema["properties"]["authorization_notice"]["const"] == NOTICE
        )
    except (KeyError, TypeError):
        return False


def _walk_strings(value: Any) -> Iterable[str]:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, dict):
            pending.extend(reversed(tuple(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))


def _exact_mapping(value: Any, fields: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(fields) and len(value) == len(fields)


def _placeholder_or(value: Any, predicate: Any) -> bool:
    return value == PLACEHOLDER or (isinstance(value, str) and bool(predicate(value)))


def _absolute_path(value: str) -> bool:
    return value.startswith("/") and all(32 <= ord(character) <= 126 for character in value)


def _validate_document_schema(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _exact_mapping(document, TOP_FIELDS):
        return ["SCHEMA"]

    evidence_age = document["evidence_age"]
    safety_baseline = document["safety_baseline"]
    expected_pre_recovery = document["expected_pre_recovery"]
    stale_pid_evidence = document["stale_pid_evidence"]
    recovery_review = document["recovery_review"]
    procedure_references = document["procedure_references"]
    backup_restore_controls = document["backup_restore_controls"]
    target = document["target"]
    preflight = document["preflight"]
    integrity = document["integrity"]
    transcript = document["transcript"]
    for mapping, fields in (
        (evidence_age, EVIDENCE_AGE_FIELDS),
        (safety_baseline, SAFETY_BASELINE_FIELDS),
        (expected_pre_recovery, EXPECTED_PRE_RECOVERY_FIELDS),
        (stale_pid_evidence, STALE_PID_EVIDENCE_FIELDS),
        (recovery_review, RECOVERY_REVIEW_FIELDS),
        (procedure_references, PROCEDURE_REFERENCE_FIELDS),
        (backup_restore_controls, BACKUP_RESTORE_CONTROL_FIELDS),
    ):
        if not _exact_mapping(mapping, fields):
            errors.append("SCHEMA")
    if not _exact_mapping(target, TARGET_FIELDS):
        errors.append("SCHEMA")
    if not _exact_mapping(preflight, PREFLIGHT_FIELDS):
        errors.append("SCHEMA")
    if not _exact_mapping(integrity, INTEGRITY_FIELDS):
        errors.append("SCHEMA")
    if not _exact_mapping(transcript, TRANSCRIPT_FIELDS):
        errors.append("SCHEMA")
    if errors:
        return errors
    if tuple(transcript) != TRANSCRIPT_FIELDS:
        errors.append("TRANSCRIPT_ORDER")

    scalar_members = (
        document["document_kind"],
        document["format_version"],
        document["record_id"],
        document["authorization_state"],
        document["authorization_notice"],
        *evidence_age.values(),
        *safety_baseline.values(),
        *expected_pre_recovery.values(),
        *stale_pid_evidence.values(),
        *recovery_review.values(),
        *procedure_references.values(),
        *backup_restore_controls.values(),
        *target.values(),
        *preflight.values(),
        *integrity.values(),
        *transcript.values(),
    )
    if any(not isinstance(value, str) for value in scalar_members):
        errors.append("SCHEMA")
        return errors
    if any(
        not value or any(ord(character) < 32 or ord(character) > 126 for character in value)
        for value in transcript.values()
    ):
        errors.append("SCHEMA")
    if any(
        len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"')
        for value in transcript.values()
    ):
        errors.append("SCHEMA")

    if (
        document["document_kind"] != "POSTGRESQL_RECOVERY_APPROVAL_PREPARATION"
        or document["format_version"] != "1"
        or not RECORD_ID_RE.fullmatch(document["record_id"])
        or document["authorization_state"] != "DRAFT_NOT_AUTHORIZED"
        or document["authorization_notice"] != NOTICE
    ):
        errors.append("SCHEMA")
    if target != FIXED_TARGET:
        errors.append("SCHEMA")

    if any(
        evidence_age[field] != expected
        for field, expected in FIXED_EVIDENCE_AGE.items()
    ):
        errors.append("SCHEMA")
    if evidence_age["evidence_status"] not in (
        "HISTORICAL_READ_ONLY_NOT_CURRENT",
        "CURRENT_REVIEW_COMPLETE",
    ):
        errors.append("SCHEMA")
    if not _placeholder_or(
        evidence_age["current_revalidation"], TIMESTAMP_RE.fullmatch
    ):
        errors.append("SCHEMA")
    if safety_baseline != FIXED_SAFETY_BASELINE:
        errors.append("SCHEMA")
    if expected_pre_recovery != FIXED_EXPECTED_PRE_RECOVERY:
        errors.append("SCHEMA")
    if any(
        stale_pid_evidence[field] != expected
        for field, expected in FIXED_STALE_PID_EVIDENCE.items()
    ):
        errors.append("SCHEMA")
    if stale_pid_evidence["current_pid_revalidation"] not in (
        PLACEHOLDER,
        "STALE_PID_REVALIDATED_NO_LIVE_PROCESS",
    ):
        errors.append("SCHEMA")
    if any(
        recovery_review[field] not in (PLACEHOLDER, expected)
        for field, expected in RECOVERY_REVIEW_VALUES.items()
    ):
        errors.append("SCHEMA")
    if procedure_references != FIXED_PROCEDURE_REFERENCES:
        errors.append("SCHEMA")
    if any(
        backup_restore_controls[field] != expected
        for field, expected in FIXED_BACKUP_RESTORE_REFERENCES.items()
    ):
        errors.append("SCHEMA")
    if any(
        backup_restore_controls[field] not in (PLACEHOLDER, expected)
        for field, expected in BACKUP_RESTORE_OUTCOMES.items()
    ):
        errors.append("SCHEMA")

    if (
        preflight["operation"] != "POSTGRESQL16_RECOVERY_SUBGATE"
        or preflight["scope"] != "DATA_001_PRESERVATION_RECOVERY_ONLY"
        or preflight["execution_mode"] != "PAPER_ONLY"
        or preflight["live_execution_approved"] != "false"
        or preflight["live_trading_approved"] != "false"
    ):
        errors.append("SCHEMA")
    for field in (
        "baseline_complete",
        "all_listed_units_inactive",
        "original_port_55432_listener_absent",
    ):
        if preflight[field] not in (PLACEHOLDER, "true", "false"):
            errors.append("SCHEMA")
    if not _placeholder_or(preflight["review_completed_at_utc"], TIMESTAMP_RE.fullmatch):
        errors.append("SCHEMA")

    for field in ("canonical_transcript_sha256", "original_identity_evidence_sha256", "original_pgdata_fingerprint"):
        if not _placeholder_or(integrity[field], SHA256_RE.fullmatch):
            errors.append("SCHEMA")
    for field in (
        "source_repository",
        "migration_artifact",
        "expected_catalog_artifact",
        "original_identity_evidence",
    ):
        if not _placeholder_or(integrity[field], _absolute_path):
            errors.append("SCHEMA")

    for field, expected in FIXED_TRANSCRIPT.items():
        if transcript[field] not in (PLACEHOLDER, expected):
            errors.append("SCHEMA")
    for field in YES_FIELDS:
        if transcript[field] not in (PLACEHOLDER, "YES"):
            errors.append("SCHEMA")
    for field in ("CHANGE_ID", "INCIDENT_ID"):
        if not _placeholder_or(transcript[field], CHANGE_ID_RE.fullmatch):
            errors.append("SCHEMA")
    for field in ("OPERATOR_NAME", "REVIEWER_NAME"):
        if not _placeholder_or(transcript[field], NAME_RE.fullmatch):
            errors.append("SCHEMA")
    if not _placeholder_or(transcript["RUN_ID"], RUN_ID_RE.fullmatch):
        errors.append("SCHEMA")
    for field in (
        "CHANGE_ARTIFACT_SHA256",
        "RUNBOOK_SHA256",
        "MIGRATION_SHA256",
        "EXPECTED_CATALOG_SHA256",
    ):
        if not _placeholder_or(transcript[field], SHA256_RE.fullmatch):
            errors.append("SCHEMA")
    for field in ("SOURCE_COMMIT", "SOURCE_TREE"):
        if not _placeholder_or(transcript[field], GIT_OBJECT_RE.fullmatch):
            errors.append("SCHEMA")
    for field in ("APPROVED_AT_UTC", "EXPIRES_AT_UTC"):
        if not _placeholder_or(transcript[field], TIMESTAMP_RE.fullmatch):
            errors.append("SCHEMA")
    for field in ("ORIG_SYSTEM_ID", "ISO_SYSTEM_ID"):
        if not _placeholder_or(transcript[field], SYSTEM_ID_RE.fullmatch):
            errors.append("SCHEMA")
    for field in (
        "ORIG_PGDATA_NLINK",
        "ORIG_SOCKET_NLINK",
        "ORIG_LOG_DIR_NLINK",
    ):
        if not _placeholder_or(transcript[field], POSITIVE_INTEGER_RE.fullmatch):
            errors.append("SCHEMA")
    if transcript["ORIG_LOG_NLINK"] not in (PLACEHOLDER, "1"):
        errors.append("SCHEMA")
    for field in (
        "CHANGE_ARTIFACT",
        "EVIDENCE_PARENT",
        "PRESERVE_PARENT",
        "BACKUP_PARENT",
        "SECRET_PARENT",
        "ISO_HOST",
        "ISO_PGDATA",
        "ISO_SOCKET",
        "ISO_ADMIN_ENV",
    ):
        if not _placeholder_or(transcript[field], _absolute_path):
            errors.append("SCHEMA")
    if not _placeholder_or(transcript["ISO_PORT"], ISO_PORT_RE.fullmatch):
        errors.append("SCHEMA")
    if not _placeholder_or(transcript["ISO_RESTORE_DB"], RESTORE_DB_RE.fullmatch):
        errors.append("SCHEMA")
    return errors


def _parse_timestamp(value: str) -> datetime | None:
    if not TIMESTAMP_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _integer_at_least(value: str, minimum: int) -> bool:
    if len(value) > 24 or not POSITIVE_INTEGER_RE.fullmatch(value):
        return False
    try:
        return int(value) >= minimum
    except ValueError:
        return False


def _lexical_absolute_path(value: str) -> Path | None:
    if not _absolute_path(value):
        return None
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    if (
        not path.is_absolute()
        or os.path.normpath(value) != value
        or any(part in ("", ".", "..") for part in path.parts[1:])
    ):
        return None
    return path


def _inode_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        *_inode_identity(metadata),
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_directory_chain(
    path: Path,
) -> tuple[int, tuple[tuple[int, int, int, int, int], ...]] | None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_fd: int | None = None
    try:
        directory_fd = os.open("/", directory_flags)
        identities = [_inode_identity(os.fstat(directory_fd))]
        for component in path.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            identities.append(_inode_identity(os.fstat(directory_fd)))
        return directory_fd, tuple(identities)
    except (OSError, ValueError):
        if directory_fd is not None:
            os.close(directory_fd)
        return None


def _open_file_chain(
    path: Path,
) -> tuple[int, tuple[tuple[int, int, int, int, int], ...]] | None:
    parent = _open_directory_chain(path.parent)
    if parent is None:
        return None
    parent_fd, identities = parent
    file_fd: int | None = None
    try:
        file_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        return file_fd, identities
    except (OSError, ValueError):
        if file_fd is not None:
            os.close(file_fd)
        return None
    finally:
        os.close(parent_fd)


def _read_open_file(
    file_fd: int,
    *,
    collect: bool,
) -> tuple[str, bytes | None, os.stat_result] | None:
    before = os.fstat(file_fd)
    if not stat.S_ISREG(before.st_mode):
        return None
    digest = hashlib.sha256()
    collected = bytearray() if collect else None
    total = 0
    while chunk := os.read(file_fd, 1024 * 1024):
        total += len(chunk)
        if collect and total > 1024 * 1024:
            return None
        digest.update(chunk)
        if collected is not None:
            collected.extend(chunk)
    after = os.fstat(file_fd)
    if _file_identity(before) != _file_identity(after):
        return None
    return digest.hexdigest(), bytes(collected) if collected is not None else None, after


def _stable_file_snapshot(
    path: Path,
    *,
    collect: bool,
    _after_read: Any = None,
) -> tuple[str, bytes | None, os.stat_result] | None:
    """Read a regular file, then re-traverse its no-follow pathname identity."""

    lexical = _lexical_absolute_path(str(path))
    if lexical is None or len(lexical.parts) < 2:
        return None
    opened = _open_file_chain(lexical)
    if opened is None:
        return None
    file_fd, directory_identities = opened
    reopened_fd: int | None = None
    try:
        snapshot = _read_open_file(file_fd, collect=collect)
        if snapshot is None:
            return None
        if _after_read is not None:
            _after_read()
        reopened = _open_file_chain(lexical)
        if reopened is None:
            return None
        reopened_fd, reopened_directories = reopened
        if (
            reopened_directories != directory_identities
            or _file_identity(os.fstat(reopened_fd)) != _file_identity(snapshot[2])
        ):
            return None
        return snapshot
    except (OSError, ValueError):
        return None
    finally:
        os.close(file_fd)
        if reopened_fd is not None:
            os.close(reopened_fd)


def _open_relative_file_chain(
    root_fd: int,
    relative: Path,
) -> tuple[int, tuple[tuple[int, int, int, int, int], ...]] | None:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in ("", ".", "..") for part in relative.parts)
        or not all(_absolute_path(f"/{part}") for part in relative.parts)
    ):
        return None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        directory_fd = os.dup(root_fd)
        identities = [_inode_identity(os.fstat(directory_fd))]
        for component in relative.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            identities.append(_inode_identity(os.fstat(directory_fd)))
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        return file_fd, tuple(identities)
    except (OSError, ValueError):
        if file_fd is not None:
            os.close(file_fd)
        return None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _stable_file_snapshot_at(
    root_fd: int,
    relative: Path,
    *,
    collect: bool,
) -> tuple[str, bytes | None, os.stat_result] | None:
    opened = _open_relative_file_chain(root_fd, relative)
    if opened is None:
        return None
    file_fd, directory_identities = opened
    reopened_fd: int | None = None
    try:
        snapshot = _read_open_file(file_fd, collect=collect)
        if snapshot is None:
            return None
        reopened = _open_relative_file_chain(root_fd, relative)
        if reopened is None:
            return None
        reopened_fd, reopened_directories = reopened
        if (
            reopened_directories != directory_identities
            or _file_identity(os.fstat(reopened_fd)) != _file_identity(snapshot[2])
        ):
            return None
        return snapshot
    except (OSError, ValueError):
        return None
    finally:
        os.close(file_fd)
        if reopened_fd is not None:
            os.close(reopened_fd)


def _open_stable_directory(
    path: Path,
    *,
    private: bool,
) -> tuple[int, os.stat_result] | None:
    lexical = _lexical_absolute_path(str(path))
    if lexical is None:
        return None
    opened = _open_directory_chain(lexical)
    if opened is None:
        return None
    directory_fd, identities = opened
    verified_fd: int | None = None
    success = False
    try:
        metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or (private and stat.S_IMODE(metadata.st_mode) != 0o700)
        ):
            return None
        verified = _open_directory_chain(lexical)
        if verified is None:
            return None
        verified_fd, verified_identities = verified
        if identities != verified_identities:
            return None
        success = True
        return directory_fd, metadata
    except (OSError, ValueError):
        return None
    finally:
        if verified_fd is not None:
            os.close(verified_fd)
        if not success:
            os.close(directory_fd)


def _is_strict_descendant(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _looks_like_credential_path(path: Path) -> bool:
    forbidden_parts = {
        ".ssh",
        ".gnupg",
        ".config",
        "credential",
        "credentials",
        "secret",
        "secrets",
    }
    forbidden_suffixes = {".env", ".key", ".pem", ".p12", ".pgpass", ".netrc"}
    forbidden_name_fragments = (
        "credential",
        "oauth",
        "passwd",
        "password",
        "secret",
        "token",
    )
    name = path.name.lower()
    return (
        any(part.lower() in forbidden_parts for part in path.parts)
        or path.suffix.lower() in forbidden_suffixes
        or name in {"pgpass", ".pgpass", ".netrc"}
        or any(fragment in name for fragment in forbidden_name_fragments)
    )


def _git_run(
    repository_fd: int,
    arguments: list[str],
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            [
                "/usr/bin/git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *arguments,
            ],
            cwd=f"/proc/self/fd/{repository_fd}",
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            pass_fds=(repository_fd,),
            timeout=10,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_NO_LAZY_FETCH": "1",
            },
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _git_value(repository_fd: int, revision: str) -> str | None:
    result = _git_run(repository_fd, ["rev-parse", "--verify", revision])
    if result is None:
        return None
    try:
        return result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeError:
        return None


def _git_blob_sha256(repository_fd: int, tree: str, relative: Path) -> str | None:
    path = relative.as_posix()
    listing = _git_run(repository_fd, ["ls-tree", "-z", tree, "--", path])
    if listing is None:
        return None
    entries = [entry for entry in listing.stdout.split(b"\0") if entry]
    if len(entries) != 1:
        return None
    try:
        metadata, observed_path = entries[0].split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        if (
            mode not in {"100644", "100755"}
            or kind != "blob"
            or observed_path.decode("utf-8", errors="strict") != path
            or not GIT_OBJECT_RE.fullmatch(object_id)
        ):
            return None
    except (UnicodeError, ValueError):
        return None
    blob = _git_run(repository_fd, ["cat-file", "blob", object_id])
    if blob is None:
        return None
    return hashlib.sha256(blob.stdout).hexdigest()


def _canonical_transcript_sha256(transcript: dict[str, str]) -> str:
    raw = "".join(f"{field}\t{transcript[field]}\n" for field in TRANSCRIPT_FIELDS)
    return hashlib.sha256(raw.encode("ascii", errors="strict")).hexdigest()


def _validate_local_bindings(
    document_metadata: os.stat_result,
    runbook_path: Path,
    runbook_digest: str,
    runbook_metadata: os.stat_result,
    trusted_evidence_root: Path,
    target: dict[str, str],
    integrity: dict[str, str],
    transcript: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    source_path = _lexical_absolute_path(integrity["source_repository"])
    migration_path = _lexical_absolute_path(integrity["migration_artifact"])
    review_root = _lexical_absolute_path(transcript["EVIDENCE_PARENT"])
    catalog_path = _lexical_absolute_path(integrity["expected_catalog_artifact"])
    change_path = _lexical_absolute_path(transcript["CHANGE_ARTIFACT"])
    identity_path = _lexical_absolute_path(integrity["original_identity_evidence"])
    secret_root = _lexical_absolute_path(transcript["SECRET_PARENT"])
    isolated_pgdata = _lexical_absolute_path(transcript["ISO_PGDATA"])
    original_pgdata = _lexical_absolute_path(target["pgdata"])
    review_paths = (catalog_path, change_path, identity_path)
    if (
        review_root is None
        or any(path is None for path in review_paths)
        or _looks_like_credential_path(review_root)
        or any(_looks_like_credential_path(path) for path in review_paths if path is not None)
        or any(
            forbidden is not None and _paths_overlap(review_root, forbidden)
            for forbidden in (secret_root, isolated_pgdata, original_pgdata)
        )
        or any(
            path is not None and not _is_strict_descendant(path, review_root)
            for path in review_paths
        )
    ):
        return ["ARTIFACT_PATH_OUTSIDE_REVIEW_ROOT"]
    trusted_root = _lexical_absolute_path(str(trusted_evidence_root))
    if trusted_root is None or trusted_root != review_root:
        return ["TRUSTED_EVIDENCE_ROOT_MISMATCH"]
    trusted_open = _open_stable_directory(trusted_root, private=True)
    if trusted_open is None:
        return ["TRUSTED_EVIDENCE_ROOT_INVALID"]
    trusted_root_fd, _trusted_metadata = trusted_open
    if (
        source_path is None
        or migration_path is None
        or migration_path
        != source_path / "alembic/versions/0004_durable_research_jobs.py"
    ):
        os.close(trusted_root_fd)
        return ["MIGRATION_DIGEST_MISMATCH"]
    try:
        source_open = _open_stable_directory(source_path, private=False)
        if source_open is None:
            errors.extend(
                (
                    "SOURCE_COMMIT_MISMATCH",
                    "SOURCE_TREE_MISMATCH",
                    "MIGRATION_DIGEST_MISMATCH",
                    "RUNBOOK_HASH_MISMATCH",
                )
            )
        else:
            source_fd, _source_metadata = source_open
            try:
                source_commit = transcript["SOURCE_COMMIT"]
                source_tree = transcript["SOURCE_TREE"]
                if _git_value(source_fd, "HEAD") != source_commit:
                    errors.append("SOURCE_COMMIT_MISMATCH")
                if _git_value(source_fd, f"{source_commit}^{{tree}}") != source_tree:
                    errors.append("SOURCE_TREE_MISMATCH")
                migration_relative = Path(
                    "alembic/versions/0004_durable_research_jobs.py"
                )
                migration_snapshot = _stable_file_snapshot_at(
                    source_fd,
                    migration_relative,
                    collect=False,
                )
                migration_blob_digest = _git_blob_sha256(
                    source_fd,
                    source_tree,
                    migration_relative,
                )
                if (
                    migration_snapshot is None
                    or migration_snapshot[0] != transcript["MIGRATION_SHA256"]
                    or migration_blob_digest != transcript["MIGRATION_SHA256"]
                ):
                    errors.append("MIGRATION_DIGEST_MISMATCH")

                runbook_relative = Path(
                    "docs/production/runbooks/postgresql-preserve-recover.md"
                )
                expected_runbook_path = source_path / runbook_relative
                declared_runbook_path = _lexical_absolute_path(
                    str(_absolute_cli_path(runbook_path))
                )
                source_runbook_snapshot = _stable_file_snapshot_at(
                    source_fd,
                    runbook_relative,
                    collect=False,
                )
                runbook_blob_digest = _git_blob_sha256(
                    source_fd,
                    source_tree,
                    runbook_relative,
                )
                if declared_runbook_path != expected_runbook_path:
                    errors.append("RUNBOOK_REPOSITORY_BINDING_MISMATCH")
                if (
                    source_runbook_snapshot is None
                    or source_runbook_snapshot[0] != runbook_digest
                    or _inode_identity(source_runbook_snapshot[2])
                    != _inode_identity(runbook_metadata)
                    or runbook_digest != transcript["RUNBOOK_SHA256"]
                    or runbook_blob_digest != transcript["RUNBOOK_SHA256"]
                ):
                    errors.append("RUNBOOK_HASH_MISMATCH")
            finally:
                os.close(source_fd)

        assert catalog_path is not None
        assert change_path is not None
        assert identity_path is not None
        catalog_snapshot = _stable_file_snapshot_at(
            trusted_root_fd,
            catalog_path.relative_to(trusted_root),
            collect=False,
        )
        if (
            catalog_snapshot is None
            or catalog_snapshot[0] != transcript["EXPECTED_CATALOG_SHA256"]
            or catalog_snapshot[2].st_uid != os.geteuid()
            or catalog_snapshot[2].st_nlink != 1
        ):
            errors.append("CATALOG_DIGEST_MISMATCH")

        change_snapshot = _stable_file_snapshot_at(
            trusted_root_fd,
            change_path.relative_to(trusted_root),
            collect=False,
        )
        if change_snapshot is None:
            errors.append("CHANGE_ARTIFACT_DIGEST_MISMATCH")
        else:
            change_digest, _change_raw, metadata = change_snapshot
            binding_safe = (
                stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_nlink == 1
                and metadata.st_uid == os.geteuid()
                and _inode_identity(metadata) != _inode_identity(document_metadata)
            )
            if not binding_safe or change_digest != transcript["CHANGE_ARTIFACT_SHA256"]:
                errors.append("CHANGE_ARTIFACT_DIGEST_MISMATCH")

        identity_snapshot = _stable_file_snapshot_at(
            trusted_root_fd,
            identity_path.relative_to(trusted_root),
            collect=True,
        )
        if (
            identity_snapshot is None
            or identity_snapshot[2].st_uid != os.geteuid()
            or identity_snapshot[2].st_nlink != 1
        ):
            errors.append("ORIGINAL_IDENTITY_EVIDENCE_DIGEST_MISMATCH")
            errors.append("ORIGINAL_IDENTITY_MISMATCH")
            return errors
        identity_digest, identity_raw, _identity_metadata = identity_snapshot
        if identity_digest != integrity["original_identity_evidence_sha256"]:
            errors.append("ORIGINAL_IDENTITY_EVIDENCE_DIGEST_MISMATCH")
        try:
            assert identity_raw is not None
            identity = json.loads(
                identity_raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (
            AssertionError,
            InputError,
            RecursionError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            errors.append("ORIGINAL_IDENTITY_MISMATCH")
            return errors
        if (
            not _exact_mapping(identity, IDENTITY_FIELDS)
            or any(not isinstance(value, str) for value in identity.values())
        ):
            errors.append("ORIGINAL_IDENTITY_MISMATCH")
            return errors
        expected_identity = {
            **target,
            "system_id": transcript["ORIG_SYSTEM_ID"],
            "pgdata_nlink": transcript["ORIG_PGDATA_NLINK"],
            "socket_nlink": transcript["ORIG_SOCKET_NLINK"],
            "log_dir_nlink": transcript["ORIG_LOG_DIR_NLINK"],
            "log_nlink": transcript["ORIG_LOG_NLINK"],
            "pgdata_fingerprint": integrity["original_pgdata_fingerprint"],
        }
        if identity != expected_identity:
            errors.append("ORIGINAL_IDENTITY_MISMATCH")
        if identity.get("pgdata_fingerprint") != integrity["original_pgdata_fingerprint"]:
            errors.append("PGDATA_FINGERPRINT_MISMATCH")
        return errors
    finally:
        os.close(trusted_root_fd)


def _validate_operational_requirements(
    document_metadata: os.stat_result,
    runbook_path: Path,
    runbook_digest: str,
    runbook_metadata: os.stat_result,
    trusted_evidence_root: Path | None,
    document: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not all(
        isinstance(document.get(member), dict)
        for member in (
            "evidence_age",
            "safety_baseline",
            "expected_pre_recovery",
            "stale_pid_evidence",
            "recovery_review",
            "procedure_references",
            "backup_restore_controls",
            "target",
            "preflight",
            "integrity",
            "transcript",
        )
    ):
        return ["SCHEMA"]
    evidence_age: dict[str, str] = document["evidence_age"]
    safety_baseline: dict[str, str] = document["safety_baseline"]
    expected_pre_recovery: dict[str, str] = document["expected_pre_recovery"]
    stale_pid_evidence: dict[str, str] = document["stale_pid_evidence"]
    recovery_review: dict[str, str] = document["recovery_review"]
    procedure_references: dict[str, str] = document["procedure_references"]
    backup_restore_controls: dict[str, str] = document["backup_restore_controls"]
    target: dict[str, str] = document["target"]
    preflight: dict[str, str] = document["preflight"]
    integrity: dict[str, str] = document["integrity"]
    transcript: dict[str, str] = document["transcript"]
    mappings = (
        (evidence_age, EVIDENCE_AGE_FIELDS),
        (safety_baseline, SAFETY_BASELINE_FIELDS),
        (expected_pre_recovery, EXPECTED_PRE_RECOVERY_FIELDS),
        (stale_pid_evidence, STALE_PID_EVIDENCE_FIELDS),
        (recovery_review, RECOVERY_REVIEW_FIELDS),
        (procedure_references, PROCEDURE_REFERENCE_FIELDS),
        (backup_restore_controls, BACKUP_RESTORE_CONTROL_FIELDS),
        (target, TARGET_FIELDS),
        (preflight, PREFLIGHT_FIELDS),
        (integrity, INTEGRITY_FIELDS),
        (transcript, TRANSCRIPT_FIELDS),
    )
    if any(not _exact_mapping(mapping, fields) for mapping, fields in mappings):
        return ["SCHEMA"]
    if any(
        not isinstance(value, str)
        for mapping, _fields in mappings
        for value in mapping.values()
    ):
        return ["SCHEMA"]

    strings = list(_walk_strings(document))
    if any(
        value == PLACEHOLDER
        or value.upper() in {"TBD", "TODO", "CHANGEME"}
        or (value.startswith("<") and value.endswith(">"))
        for value in strings
    ):
        errors.append("PLACEHOLDER")
    if any(SECRET_RE.search(value) for value in strings):
        errors.append("SECRET_CONTENT")
    if any(PROHIBITED_COMMAND_RE.search(value) for value in strings):
        errors.append("PROHIBITED_COMMAND_TEXT")

    if any(transcript.get(field) != expected for field, expected in FIXED_TRANSCRIPT.items()):
        errors.append("INVALID_FIXED_CONSTANT")
    if any(transcript.get(field) != "YES" for field in YES_FIELDS):
        errors.append("INVALID_FIXED_CONSTANT")

    if (
        any(
            evidence_age.get(field) != expected
            for field, expected in FIXED_EVIDENCE_AGE.items()
        )
        or evidence_age.get("evidence_status") != "CURRENT_REVIEW_COMPLETE"
        or _parse_timestamp(evidence_age.get("current_revalidation", "")) is None
    ):
        errors.append("EVIDENCE_AGE_INVALID")
    if safety_baseline != FIXED_SAFETY_BASELINE:
        errors.append("BASELINE_METADATA_INVALID")
    if expected_pre_recovery != FIXED_EXPECTED_PRE_RECOVERY:
        errors.append("EXPECTED_PRE_RECOVERY_INVALID")
    if (
        any(
            stale_pid_evidence.get(field) != expected
            for field, expected in FIXED_STALE_PID_EVIDENCE.items()
        )
        or stale_pid_evidence.get("current_pid_revalidation")
        != "STALE_PID_REVALIDATED_NO_LIVE_PROCESS"
    ):
        errors.append("INCIDENT_METADATA_INVALID")
    if recovery_review != RECOVERY_REVIEW_VALUES:
        errors.append("RECOVERY_REVIEW_INVALID")
    if procedure_references != FIXED_PROCEDURE_REFERENCES:
        errors.append("PROCEDURE_REFERENCE_INVALID")
    if (
        any(
            backup_restore_controls.get(field) != expected
            for field, expected in FIXED_BACKUP_RESTORE_REFERENCES.items()
        )
        or any(
            backup_restore_controls.get(field) != expected
            for field, expected in BACKUP_RESTORE_OUTCOMES.items()
        )
    ):
        errors.append("BACKUP_RESTORE_REFERENCE_INVALID")

    if target != FIXED_TARGET:
        errors.append("FIXED_TARGET_MISMATCH")
    if (
        preflight.get("operation") != "POSTGRESQL16_RECOVERY_SUBGATE"
        or preflight.get("scope") != "DATA_001_PRESERVATION_RECOVERY_ONLY"
    ):
        errors.append("WRONG_OPERATION_OR_SCOPE")
    if preflight.get("execution_mode") != "PAPER_ONLY":
        errors.append("NON_PAPER_MODE")
    if (
        preflight.get("live_execution_approved") != "false"
        or preflight.get("live_trading_approved") != "false"
    ):
        errors.append("LIVE_GATE_NOT_FALSE")
    if preflight.get("baseline_complete") != "true":
        errors.append("INCOMPLETE_BASELINE")
    if preflight.get("all_listed_units_inactive") != "true":
        errors.append("UNSAFE_SERVICE_BASELINE")
    if preflight.get("original_port_55432_listener_absent") != "true":
        errors.append("UNSAFE_PORT_BASELINE")

    if transcript.get("OPERATOR_NAME") == transcript.get("REVIEWER_NAME"):
        errors.append("SAME_OPERATOR_REVIEWER")
    if (
        transcript.get("ORIG_SYSTEM_ID") == transcript.get("ISO_SYSTEM_ID")
        and transcript.get("ORIG_SYSTEM_ID") != PLACEHOLDER
    ):
        errors.append("ORIGINAL_IDENTITY_MISMATCH")
    for field in ("ORIG_PGDATA_NLINK", "ORIG_SOCKET_NLINK", "ORIG_LOG_DIR_NLINK"):
        value = transcript.get(field, "")
        if not _integer_at_least(value, 2):
            errors.append("ORIGINAL_IDENTITY_MISMATCH")
    if transcript.get("ORIG_LOG_NLINK") != "1":
        errors.append("ORIGINAL_IDENTITY_MISMATCH")
    iso_port = transcript.get("ISO_PORT", "")
    if (
        not ISO_PORT_RE.fullmatch(iso_port)
        or int(iso_port) > 65535
        or iso_port == "55432"
    ):
        errors.append("INVALID_FIXED_CONSTANT")
    if transcript.get("ISO_HOST") != transcript.get("ISO_SOCKET"):
        errors.append("INVALID_FIXED_CONSTANT")
    if not RESTORE_DB_RE.fullmatch(transcript.get("ISO_RESTORE_DB", "")):
        errors.append("INVALID_FIXED_CONSTANT")

    approved = _parse_timestamp(transcript.get("APPROVED_AT_UTC", ""))
    expires = _parse_timestamp(transcript.get("EXPIRES_AT_UTC", ""))
    reviewed = _parse_timestamp(preflight.get("review_completed_at_utc", ""))
    revalidated = _parse_timestamp(evidence_age.get("current_revalidation", ""))
    if approved is None or expires is None or reviewed is None or revalidated is None:
        errors.append("INVALID_TIME")
    else:
        now = datetime.now(UTC)
        if approved > now:
            errors.append("FUTURE_APPROVAL")
        if reviewed > now or revalidated > now:
            errors.append("FUTURE_REVIEW")
        if now > expires:
            errors.append("EXPIRED")
        if expires <= approved:
            errors.append("INVALID_WINDOW")
        if (expires - approved).total_seconds() > 14_400:
            errors.append("WINDOW_TOO_LONG")
        if not approved <= reviewed <= expires:
            errors.append("REVIEW_OUT_OF_WINDOW")
        if not approved <= revalidated <= expires or revalidated != reviewed:
            errors.append("REVIEW_OUT_OF_WINDOW")

    try:
        observed_canonical = _canonical_transcript_sha256(transcript)
    except (KeyError, UnicodeError):
        observed_canonical = ""
    if observed_canonical != integrity.get("canonical_transcript_sha256"):
        errors.append("CANONICAL_TRANSCRIPT_DIGEST_MISMATCH")

    local_values = (
        transcript.get("RUNBOOK_SHA256"),
        transcript.get("SOURCE_COMMIT"),
        transcript.get("SOURCE_TREE"),
        transcript.get("MIGRATION_SHA256"),
        transcript.get("EXPECTED_CATALOG_SHA256"),
        transcript.get("CHANGE_ARTIFACT"),
        transcript.get("CHANGE_ARTIFACT_SHA256"),
        integrity.get("source_repository"),
        integrity.get("migration_artifact"),
        integrity.get("expected_catalog_artifact"),
        integrity.get("original_identity_evidence"),
        integrity.get("original_identity_evidence_sha256"),
        integrity.get("original_pgdata_fingerprint"),
    )
    if trusted_evidence_root is None:
        errors.append("TRUSTED_EVIDENCE_ROOT_REQUIRED")
    elif all(isinstance(value, str) and value != PLACEHOLDER for value in local_values):
        errors.extend(
            _validate_local_bindings(
                document_metadata,
                runbook_path,
                runbook_digest,
                runbook_metadata,
                trusted_evidence_root,
                target,
                integrity,
                transcript,
            )
        )
    return errors


def _unique(errors: Iterable[str]) -> list[str]:
    result: list[str] = []
    for error in errors:
        if error not in result:
            result.append(error)
    return result


def _arguments() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Validate a non-authorizing PostgreSQL recovery approval preparation record."
    )
    parser.add_argument("record", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=root / "schemas/postgres-recovery-approval-record.schema.json",
    )
    parser.add_argument(
        "--runbook",
        type=Path,
        default=root / "docs/production/runbooks/postgresql-preserve-recover.md",
    )
    parser.add_argument(
        "--trusted-evidence-root",
        type=Path,
        help=(
            "independently supplied private 0700 evidence root; required for "
            "operational completeness checks"
        ),
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="check the non-authorizing preparation shape only",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        document, _document_digest, document_metadata = _load_json_subset_snapshot(
            arguments.record
        )
        schema = _load_json_subset(arguments.schema)
        runbook_text, runbook_digest, runbook_metadata = _read_text_snapshot(
            arguments.runbook
        )
        runbook_fields = _extract_runbook_fields(runbook_text)
    except InputError as error:
        print(f"REJECTED: {error.code}", file=sys.stderr)
        return 1

    errors: list[str] = []
    if not _schema_matches_runbook(schema, runbook_fields):
        errors.append("RUNBOOK_SCHEMA_DRIFT")
    errors.extend(_validate_document_schema(document))
    strings = list(_walk_strings(document))
    if any(SECRET_RE.search(value) for value in strings):
        errors.append("SECRET_CONTENT")
    if any(PROHIBITED_COMMAND_RE.search(value) for value in strings):
        errors.append("PROHIBITED_COMMAND_TEXT")

    if arguments.schema_only:
        errors = _unique(errors)
        if errors:
            print(f"REJECTED: {', '.join(errors)}", file=sys.stderr)
            return 1
        print(
            "NON-AUTHORIZING: schema-valid preparation only; "
            "never use YAML as executable APPROVAL_RECORD"
        )
        return 0

    errors.extend(
        _validate_operational_requirements(
            document_metadata,
            arguments.runbook,
            runbook_digest,
            runbook_metadata,
            arguments.trusted_evidence_root,
            document,
        )
    )
    errors.append("YAML_PREPARATION_ONLY")
    print(f"REJECTED: {', '.join(_unique(errors))}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
