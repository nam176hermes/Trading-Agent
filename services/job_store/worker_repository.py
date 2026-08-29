"""Transactional claims, lease fencing, and expired-attempt recovery."""

from __future__ import annotations

import hashlib
import json
from functools import wraps
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from packages.job_contracts import JobPayload, JobState, JobType, parse_payload, validate_transition
from services.job_worker.recovery import ProcessIdentity, ProcessInspector

from .config import (
    CANONICAL_DATABASE_REVISION,
    P1_DISPOSABLE_DATABASE_REVISION,
    JobStoreSettings,
)
from .errors import InvalidTraceId


_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)
_HASH = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_COMMAND_LINEAGE_KEYS = (
    "authority_document_sha256",
    "backend_manifest_sha256",
    "semantic_policy_sha256",
    "semantic_active_authority_sha256",
    "semantic_version_manifest_sha256",
    "semantic_input_fingerprint",
    "semantic_manifest_version",
    "semantic_generated_at",
    "semantic_expires_at",
)
_SAFETY_LINEAGE_KEYS = (
    "snapshot_sha256",
    "generated_at",
    "expires_at",
    "requested_mode",
    "effective_mode",
    "live_execution_enabled",
    "live_trading_approved",
    "kill_switch_state",
)
_ACTIVE_ATTEMPT_OUTCOMES: dict[JobState, frozenset[str]] = {
    JobState.CLAIMED: frozenset({"CLAIMED"}),
    JobState.RUNNING: frozenset({"RUNNING"}),
    JobState.CANCEL_REQUESTED: frozenset({"CLAIMED", "RUNNING"}),
}


class _FinalizeFenceLost(RuntimeError):
    """Internal control flow that forces transaction rollback on fence loss."""


def _rollback_fence_loss_as_false(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except _FinalizeFenceLost:
            return False
    return wrapped


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: str
    job_type: JobType
    payload: JobPayload
    attempt_id: str
    attempt_number: int
    worker_id: str
    lease_token: str = field(repr=False)
    lease_expires_at: datetime
    max_attempts: int

    @property
    def lease_token_sha256(self) -> str:
        return hashlib.sha256(self.lease_token.encode("utf-8")).hexdigest()


class WorkerRepository:
    """Short PostgreSQL transactions for one fenced worker execution."""

    def __init__(self, settings: JobStoreSettings) -> None:
        self._pool = ConnectionPool(
            conninfo=settings.conninfo(), min_size=settings.pool_min,
            max_size=settings.pool_max, kwargs={"row_factory": dict_row}, open=True,
        )

    def __enter__(self) -> "WorkerRepository":
        self._pool.wait()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self._pool.close()

    def engine_event_ingestor(self):
        """Bind engine-result ingestion to this worker's protected DB pool."""

        from .engine_event_repository import PostgresEngineEventLedger

        return PostgresEngineEventLedger(self._pool)

    def assert_runtime_identity(
        self, *, expected_user: str, expected_revision: str,
    ) -> None:
        """Fail startup unless PostgreSQL reports the exact worker role/head."""

        if expected_user != "trading_job_worker":
            raise ValueError("worker database role authority is invalid")
        if expected_revision != CANONICAL_DATABASE_REVISION:
            raise ValueError("worker database revision authority is invalid")
        self._assert_database_identity(expected_user, expected_revision)

    def assert_p1_disposable_runtime_identity(self) -> None:
        """Require the code-owned disposable P1 worker role and 0013 head."""

        self._assert_database_identity(
            "trading_job_worker", P1_DISPOSABLE_DATABASE_REVISION
        )

    def _assert_database_identity(
        self, expected_user: str, expected_revision: str
    ) -> None:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT current_user AS current_user,
                           (SELECT version_num FROM alembic_version) AS version_num
                    """
                ).fetchone()
            current_user = (
                row.get("current_user") if isinstance(row, dict) else row[0]
            )
            version_num = (
                row.get("version_num") if isinstance(row, dict) else row[1]
            )
            if current_user != expected_user or version_num != expected_revision:
                raise ValueError
        except Exception:
            raise RuntimeError("worker database authority is unavailable") from None

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    @staticmethod
    def _validate_trace(trace_id: str) -> None:
        if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
            raise InvalidTraceId("trace identity is invalid")

    @staticmethod
    def _validate_worker(worker_id: str) -> None:
        if not isinstance(worker_id, str) or not _IDENTITY.fullmatch(worker_id):
            raise ValueError("worker identity is invalid")

    @staticmethod
    def _validate_lease_seconds(lease_seconds: int) -> None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease duration must be between 1 and 3600 seconds")

    def claim_next(
        self,
        worker_id: str,
        lease_seconds: int,
        trace_id: str,
        *,
        allowed_job_types: tuple[JobType, ...] = (JobType.SNAPSHOT,),
    ) -> ClaimedJob | None:
        """Claim one eligible job and close the transaction before returning it."""

        self._validate_worker(worker_id)
        self._validate_lease_seconds(lease_seconds)
        self._validate_trace(trace_id)
        selected_types = tuple(JobType(item) for item in allowed_job_types)
        backtest_job_type = getattr(JobType, "BACKTEST", None)
        permitted_type_sets = {(JobType.SNAPSHOT,)}
        if backtest_job_type is not None:
            permitted_type_sets.add((JobType.SNAPSHOT, backtest_job_type))
        if selected_types not in permitted_type_sets:
            raise ValueError("worker job-type authority is invalid")
        attempt_id = self._new_id("attempt")
        lease_token = secrets.token_urlsafe(48)
        claim_capability = (
            "worker_claim_paper"
            if (
                backtest_job_type is not None
                and selected_types == (JobType.SNAPSHOT, backtest_job_type)
            )
            else "worker_claim_snapshot"
        )
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    f"""
                    SELECT job_id, job_type, payload, attempt_number,
                           max_attempts, lease_expires_at
                    FROM job_plane.{claim_capability}(%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        attempt_id,
                        worker_id,
                        lease_token,
                        lease_seconds,
                        trace_id,
                        self._new_id("event"),
                    ),
                ).fetchone()
                if row is None:
                    return None
                return ClaimedJob(
                    job_id=row["job_id"], job_type=JobType(row["job_type"]),
                    payload=parse_payload(row["job_type"], row["payload"]),
                    attempt_id=attempt_id, attempt_number=row["attempt_number"],
                    worker_id=worker_id, lease_token=lease_token,
                    lease_expires_at=row["lease_expires_at"],
                    max_attempts=row["max_attempts"],
                )

    def start_attempt(self, job_id: str, attempt_id: str, worker_id: str, lease_token: str, identity: ProcessIdentity, trace_id: str) -> bool:
        self._validate_worker(worker_id)
        self._validate_trace(trace_id)
        if not isinstance(identity, ProcessIdentity) or not _HASH.fullmatch(identity.command_fingerprint):
            raise ValueError("complete process identity is required")
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    SELECT job_plane.worker_start_paper(
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) AS started
                    """,
                    (
                        job_id,
                        attempt_id,
                        worker_id,
                        lease_token,
                        identity.pid,
                        identity.process_group,
                        identity.start_ticks,
                        identity.command_fingerprint,
                        trace_id,
                        self._new_id("event"),
                    ),
                ).fetchone()
                return bool(row and row["started"])

    def heartbeat(
        self, job_id: str, attempt_id: str, worker_id: str,
        lease_token: str, lease_seconds: int, *,
        expected_state: JobState | str = JobState.RUNNING,
        expected_attempt_outcome: str,
    ) -> bool:
        self._validate_worker(worker_id)
        self._validate_lease_seconds(lease_seconds)
        source = JobState(expected_state)
        attempt_outcome = self._validate_active_attempt_outcome(
            source, expected_attempt_outcome
        )
        phase = "PRE_SPAWN" if attempt_outcome == "CLAIMED" else "RUNNING"
        control = self._control_snapshot_lease(
            job_id,
            attempt_id,
            worker_id,
            lease_token,
            lease_seconds,
            phase,
        )
        if control == "STALE":
            return False
        expected_cancel = source is JobState.CANCEL_REQUESTED
        return (control == "CANCEL") is expected_cancel

    def heartbeat_control(
        self, job_id: str, attempt_id: str, worker_id: str,
        lease_token: str, lease_seconds: int,
    ) -> str:
        """Renew the current fence and return cancellation without a race."""

        self._validate_worker(worker_id)
        self._validate_lease_seconds(lease_seconds)
        return self._control_snapshot_lease(
            job_id,
            attempt_id,
            worker_id,
            lease_token,
            lease_seconds,
            "RUNNING",
        )

    def pre_spawn_control(
        self, job_id: str, attempt_id: str, worker_id: str,
        lease_token: str, lease_seconds: int,
    ) -> str:
        """Fence the CLAIMED-to-spawn boundary and observe cancellation."""

        self._validate_worker(worker_id)
        self._validate_lease_seconds(lease_seconds)
        return self._control_snapshot_lease(
            job_id,
            attempt_id,
            worker_id,
            lease_token,
            lease_seconds,
            "PRE_SPAWN",
        )

    def _control_snapshot_lease(
        self,
        job_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        phase: str,
    ) -> str:
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    SELECT job_plane.worker_control_paper_lease(
                        %s, %s, %s, %s, %s, %s
                    ) AS control
                    """,
                    (
                        job_id,
                        attempt_id,
                        worker_id,
                        lease_token,
                        lease_seconds,
                        phase,
                    ),
                ).fetchone()
        if row is None or row["control"] not in {"CONTINUE", "CANCEL", "STALE"}:
            raise RuntimeError("worker lease authority returned an invalid result")
        return row["control"]

    def worker_heartbeat(
        self, worker_id: str, code_commit: str, status: str, *,
        current_job_id: str | None, current_attempt_id: str | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._validate_worker(worker_id)
        if not isinstance(code_commit, str) or not (1 <= len(code_commit) <= 64):
            raise ValueError("code commit identity is invalid")
        if status not in {"IDLE", "BUSY", "STOPPING", "UNHEALTHY"}:
            raise ValueError("worker heartbeat status is invalid")
        if (current_job_id is None) != (current_attempt_id is None):
            raise ValueError("worker current job identity is incomplete")
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO worker_heartbeats (
                    worker_id, code_commit, status, current_job_id,
                    current_attempt_id, heartbeat_at, metadata
                ) VALUES (%s, %s, %s, %s, %s, now(), %s::jsonb)
                ON CONFLICT (worker_id) DO UPDATE SET
                    code_commit = EXCLUDED.code_commit,
                    status = EXCLUDED.status,
                    current_job_id = EXCLUDED.current_job_id,
                    current_attempt_id = EXCLUDED.current_attempt_id,
                    heartbeat_at = EXCLUDED.heartbeat_at,
                    metadata = EXCLUDED.metadata
                """,
                (worker_id, code_commit, status, current_job_id,
                 current_attempt_id, metadata_json),
            )
            connection.commit()

    @_rollback_fence_loss_as_false
    def finalize(
        self, job_id: str, attempt_id: str, worker_id: str, lease_token: str, *,
        expected_state: JobState | str, expected_attempt_outcome: str,
        final_state: JobState | str,
        reason_code: str, trace_id: str, exit_code: int | None = None,
        termination_reason: str | None = None, result_hash: str | None = None,
        result_metadata: dict[str, object] | None = None,
        error_code: str | None = None, error_message: str | None = None,
        artifacts: tuple[object, ...] = (), retry: bool = False,
    ) -> bool:
        self._validate_worker(worker_id)
        self._validate_trace(trace_id)
        source, target = JobState(expected_state), JobState(final_state)
        attempt_outcome = self._validate_active_attempt_outcome(
            source, expected_attempt_outcome
        )
        validate_transition(source, target, reason_code, trace_id=trace_id)
        if retry:
            validate_transition(
                target, JobState.QUEUED, "PROCESS_RETRY_SCHEDULED",
                retry_allowed=True, trace_id=trace_id,
            )
        if result_hash is not None and not _HASH.fullmatch(result_hash):
            raise ValueError("result hash must be lowercase SHA-256")
        metadata_json = json.dumps(result_metadata or {}, sort_keys=True, separators=(",", ":"))
        event_metadata_json = json.dumps(
            self._completion_event_metadata(result_metadata or {}),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._pool.connection() as connection:
            with connection.transaction():
                terminal_event_id = self._new_id("event")
                retry_event_id = self._new_id("event") if retry else None
                for artifact in artifacts:
                    validation_metadata = json.dumps(
                        getattr(artifact, "validation_metadata", {}),
                        sort_keys=True, separators=(",", ":"),
                    )
                    connection.execute(
                        """
                        INSERT INTO job_artifacts (
                            artifact_id, job_id, attempt_id, artifact_type,
                            relative_ref, sha256, size_bytes, media_type,
                            truncated, validator_id, validation_metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            self._new_id("artifact"), job_id, attempt_id,
                            artifact.artifact_type, artifact.relative_ref,
                            artifact.sha256, artifact.size_bytes, artifact.media_type,
                            artifact.truncated, artifact.validator_id,
                            validation_metadata,
                        ),
                    )
                authority = connection.execute(
                    """
                    SELECT job_plane.worker_finalize_paper(
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb
                    ) AS finalized
                    """,
                    (
                        job_id,
                        attempt_id,
                        worker_id,
                        lease_token,
                        source.value,
                        attempt_outcome,
                        target.value,
                        reason_code,
                        trace_id,
                        terminal_event_id,
                        exit_code,
                        termination_reason,
                        result_hash,
                        metadata_json,
                        error_code,
                        error_message,
                        retry,
                        retry_event_id,
                        event_metadata_json,
                    ),
                ).fetchone()
                if authority is None or not authority["finalized"]:
                    raise _FinalizeFenceLost
                return True

    @staticmethod
    def _completion_event_metadata(
        result_metadata: dict[str, object],
    ) -> dict[str, object]:
        """Return digest plus an explicit, allowlisted completion lineage copy."""

        canonical = json.dumps(
            result_metadata, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        event: dict[str, object] = {
            "result_metadata_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        raw_lineage = result_metadata.get("lineage")
        if not isinstance(raw_lineage, dict):
            return event
        raw_command = raw_lineage.get("command")
        raw_safety = raw_lineage.get("safety")
        if not isinstance(raw_command, dict) or not isinstance(raw_safety, dict):
            return event
        initial = raw_safety.get("initial")
        final = raw_safety.get("final")
        if not isinstance(initial, dict) or not isinstance(final, dict):
            return event

        command = {
            key: raw_command[key]
            for key in _COMMAND_LINEAGE_KEYS
            if isinstance(raw_command.get(key), str)
        }

        def sanitize_safety(value: dict[str, object]) -> dict[str, object]:
            return {
                key: value[key]
                for key in _SAFETY_LINEAGE_KEYS
                if isinstance(value.get(key), (str, bool))
            }

        event["lineage"] = {
            "command": command,
            "safety": {
                "initial": sanitize_safety(initial),
                "final": sanitize_safety(final),
            },
        }
        return event

    def finalize_execution(
        self, claimed: ClaimedJob, *, expected_state: JobState,
        expected_attempt_outcome: str, final_state: JobState,
        reason_code: str, trace_id: str, outcome: object | None,
        result: object | None, stream_artifacts: tuple[object, ...],
    ) -> bool:
        artifacts = (*stream_artifacts, *((result,) if result is not None else ()))
        result_metadata: dict[str, object] = {
            "artifacts": [
                {
                    "type": item.artifact_type, "ref": item.relative_ref,
                    "sha256": item.sha256, "size_bytes": item.size_bytes,
                    "truncated": item.truncated,
                }
                for item in artifacts
            ]
        }
        if outcome is not None:
            result_metadata["command_capability_fingerprint"] = outcome.capability_fingerprint
            result_metadata["lineage"] = outcome.lineage.as_metadata()
        profile_result = getattr(result, "profile_result", None)
        if profile_result is not None:
            from packages.engine_event_ledger import EngineEventBatchReceipt
            from packages.engine_portfolio_projection.parity import (
                P1PortfolioParityReceipt,
            )
            from packages.nautilus_runtime_contracts.result import P1ValidatedResult

            validation_metadata = getattr(result, "validation_metadata", None)
            if (
                type(profile_result) is P1ValidatedResult
                and type(validation_metadata) is dict
            ):
                engine_receipt = EngineEventBatchReceipt.model_validate_json(
                    json.dumps(validation_metadata.get("engine_event_receipt"))
                )
                parity_receipt = P1PortfolioParityReceipt.model_validate_json(
                    json.dumps(validation_metadata.get("p1_portfolio_parity"))
                )
                result_metadata.update(
                    engine_event_receipt=engine_receipt.model_dump(mode="json"),
                    p1_portfolio_parity=parity_receipt.model_dump(mode="json"),
                )
        return self.finalize(
            claimed.job_id, claimed.attempt_id, claimed.worker_id,
            claimed.lease_token, expected_state=expected_state,
            expected_attempt_outcome=expected_attempt_outcome,
            final_state=final_state, reason_code=reason_code, trace_id=trace_id,
            exit_code=None if outcome is None else outcome.exit_code,
            termination_reason=None if outcome is None else outcome.termination_reason,
            result_hash=None if result is None else result.sha256,
            result_metadata=result_metadata, artifacts=artifacts,
            error_code=None if final_state is JobState.SUCCEEDED else reason_code,
            error_message=None,
        )

    def finalize_retry(
        self, claimed: ClaimedJob, *, reason_code: str, trace_id: str,
        outcome: object, stream_artifacts: tuple[object, ...],
    ) -> bool:
        return self.finalize(
            claimed.job_id, claimed.attempt_id, claimed.worker_id,
            claimed.lease_token, expected_state=JobState.RUNNING,
            expected_attempt_outcome="RUNNING", final_state=JobState.FAILED,
            reason_code=reason_code, trace_id=trace_id,
            exit_code=outcome.exit_code,
            termination_reason=outcome.termination_reason,
            result_metadata={
                "artifacts": [
                    {"type": item.artifact_type, "ref": item.relative_ref,
                     "sha256": item.sha256, "size_bytes": item.size_bytes,
                     "truncated": item.truncated}
                    for item in stream_artifacts
                ],
                "command_capability_fingerprint": outcome.capability_fingerprint,
                "lineage": outcome.lineage.as_metadata(),
            },
            error_code=reason_code, artifacts=stream_artifacts, retry=True,
        )

    @staticmethod
    def _validate_active_attempt_outcome(state: JobState, outcome: str) -> str:
        allowed = _ACTIVE_ATTEMPT_OUTCOMES.get(state, frozenset())
        if outcome not in allowed:
            raise ValueError(
                f"attempt outcome {outcome!r} does not match job state {state.value}"
            )
        return outcome

    def recover_expired_leases(self, process_inspector: ProcessInspector, *, trace_id: str | None = None, recovery_id: str = "lease-recovery") -> tuple[tuple[str, str], ...]:
        """Recover expired attempts only after process identity is resolved."""

        run_trace = trace_id or f"recovery:{uuid4().hex}"
        self._validate_trace(run_trace)
        self._validate_worker(recovery_id)
        with self._pool.connection() as connection:
            candidates = connection.execute(
                """
                SELECT j.job_id, j.state, j.attempt_count, j.max_attempts,
                       j.lease_owner, j.lease_token,
                       a.attempt_id, a.child_pid, a.process_group_id,
                       a.process_start_ticks, a.command_fingerprint,
                       a.outcome AS attempt_outcome
                FROM jobs j JOIN job_attempts a
                  ON a.job_id = j.job_id AND a.attempt_number = j.attempt_count
                WHERE j.state IN ('CLAIMED','RUNNING','CANCEL_REQUESTED')
                  AND j.job_type IN ('SNAPSHOT','BACKTEST')
                  AND j.lease_expires_at <= now()
                  AND (
                    (j.state = 'CLAIMED' AND a.outcome = 'CLAIMED')
                    OR (j.state = 'RUNNING' AND a.outcome = 'RUNNING')
                    OR (j.state = 'CANCEL_REQUESTED'
                        AND a.outcome IN ('CLAIMED','RUNNING'))
                  )
                ORDER BY j.lease_expires_at, j.job_id
                """
            ).fetchall()
        outcomes: list[tuple[str, str]] = []
        for candidate in candidates:
            try:
                stored = self._stored_identity(candidate)
                if stored is None:
                    observation = "UNVERIFIABLE"
                else:
                    observed = process_inspector.inspect(stored.pid)
                    if observed is None:
                        observation = "ABSENT"
                    elif observed == stored:
                        observation = "STILL_RUNNING"
                    else:
                        observation = "IDENTITY_MISMATCH"
            except (OSError, PermissionError, RuntimeError, ValueError):
                observation = "UNVERIFIABLE"
            reason = self._recover_observed_candidate(
                candidate, observation, run_trace, recovery_id
            )
            outcomes.append((candidate["job_id"], reason))
        return tuple(outcomes)

    @staticmethod
    def _stored_identity(row: dict[str, Any]) -> ProcessIdentity | None:
        values = (row["child_pid"], row["process_group_id"], row["process_start_ticks"], row["command_fingerprint"])
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise RuntimeError("stored process identity is incomplete")
        return ProcessIdentity(values[0], values[1], values[2], values[3])

    def _recover_observed_candidate(
        self, candidate: dict[str, Any], observation: str,
        trace_id: str, recovery_id: str,
    ) -> str:
        """Lock and re-read the full fence after the potentially slow procfs read."""

        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    SELECT job_plane.worker_recover_expired_paper(
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    ) AS outcome
                    """,
                    (
                        candidate["job_id"],
                        candidate["attempt_id"],
                        candidate["state"],
                        candidate["attempt_outcome"],
                        candidate["lease_owner"],
                        candidate["lease_token"],
                        candidate["child_pid"],
                        candidate["process_group_id"],
                        candidate["process_start_ticks"],
                        candidate["command_fingerprint"],
                        observation,
                        trace_id,
                        recovery_id,
                        self._new_id("event"),
                        self._new_id("event"),
                    ),
                ).fetchone()
        if row is None or not isinstance(row["outcome"], str):
            raise RuntimeError("worker recovery authority returned an invalid result")
        return row["outcome"]
