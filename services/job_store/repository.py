from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType, TracebackType
from typing import Any, TypeAlias
from uuid import uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from packages.job_contracts import (
    ActorIdentity,
    ActorType,
    EnqueueJobRequest,
    JobState,
    JobType,
    canonical_payload_json,
    parse_payload,
    payload_fingerprint,
)

from .config import JobStoreSettings
from .errors import (
    IdempotencyConflict,
    InvalidJobFilters,
    InvalidTraceId,
    JobNotFound,
)
from .records import (
    ArtifactRecord,
    AttemptRecord,
    EnqueueOutcome,
    EnqueueResult,
    EventRecord,
    JobDetailRecord,
    JobFilters,
    JobRecord,
)


_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)
_JOB_COLUMNS = """
    job_id, job_type, state, payload, payload_fingerprint, idempotency_key,
    actor_type, actor_id, priority, requested_at, updated_at, attempt_count,
    max_attempts, reason_code, result_hash, cancel_requested_at,
    cancel_actor_type, cancel_actor_id
"""
_FrozenJson: TypeAlias = (
    str | int | float | bool | None | tuple["_FrozenJson", ...] | Mapping[str, "_FrozenJson"]
)


class JobRepository:
    """Transactional PostgreSQL repository for durable research jobs."""

    def __init__(self, settings: JobStoreSettings) -> None:
        self._pool = ConnectionPool(
            conninfo=settings.conninfo(),
            min_size=settings.pool_min,
            max_size=settings.pool_max,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def __enter__(self) -> "JobRepository":
        self._pool.wait()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._pool.close()

    @staticmethod
    def _validate_trace_id(trace_id: str) -> None:
        if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
            raise InvalidTraceId("trace identity is invalid")

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    def enqueue(
        self, request: EnqueueJobRequest, *, trace_id: str
    ) -> EnqueueResult:
        self._validate_trace_id(trace_id)
        with self._pool.connection() as connection:
            with connection.transaction():
                return self._enqueue(connection, request=request, trace_id=trace_id)

    def _enqueue(
        self,
        connection: Connection[Any],
        *,
        request: EnqueueJobRequest,
        trace_id: str,
    ) -> EnqueueResult:
        job_type = JobType(request.job_type)
        if job_type is not JobType.SNAPSHOT:
            raise ValueError("Job API is authorized only for SNAPSHOT jobs")
        if request.actor.actor_type is not ActorType.OPERATOR:
            raise ValueError("Job API actor authority is invalid")
        payload = parse_payload(job_type, request.payload.model_dump(mode="json"))
        canonical_json = canonical_payload_json(payload)
        fingerprint = payload_fingerprint(payload)
        job_id = self._new_id("job")
        try:
            result = connection.execute(
                """
                SELECT job_id, outcome
                FROM job_plane.api_enqueue_snapshot(
                    %s, %s::jsonb, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    job_id,
                    canonical_json,
                    fingerprint,
                    request.idempotency_key,
                    request.actor.actor_id,
                    request.priority,
                    trace_id,
                    self._new_id("event"),
                ),
            ).fetchone()
        except UniqueViolation as error:
            if error.diag.constraint_name == "job_plane_idempotency_identity":
                raise IdempotencyConflict(
                    job_type.value, request.idempotency_key
                ) from None
            raise
        if result is None:
            raise RuntimeError("enqueue authority returned no result")
        row = self._select_job(connection, result["job_id"])
        return EnqueueResult(
            job=self._job_record(row),
            outcome=EnqueueOutcome(result["outcome"]),
        )

    def schedule_snapshot(
        self,
        *,
        request: EnqueueJobRequest,
        scheduler_id: str,
        code_commit: str,
        trace_id: str,
        tick_at: datetime,
        slot_at: datetime,
    ) -> EnqueueResult:
        """Atomically enqueue a scheduled snapshot and append its heartbeat."""

        self._validate_trace_id(trace_id)
        job_type = JobType(request.job_type)
        if job_type is not JobType.SNAPSHOT:
            raise ValueError("scheduler is authorized only for SNAPSHOT jobs")
        if request.actor.actor_type is not ActorType.SCHEDULER:
            raise ValueError("scheduler actor authority is invalid")
        payload = parse_payload(job_type, request.payload.model_dump(mode="json"))
        canonical_json = canonical_payload_json(payload)
        fingerprint = payload_fingerprint(payload)
        with self._pool.connection() as connection:
            with connection.transaction():
                try:
                    authority = connection.execute(
                        """
                        SELECT job_id, outcome
                        FROM job_plane.scheduler_enqueue_snapshot(
                            %s, %s::jsonb, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            self._new_id("job"),
                            canonical_json,
                            fingerprint,
                            request.idempotency_key,
                            request.actor.actor_id,
                            trace_id,
                            self._new_id("event"),
                        ),
                    ).fetchone()
                except UniqueViolation as error:
                    if error.diag.constraint_name == "job_plane_idempotency_identity":
                        raise IdempotencyConflict(
                            job_type.value, request.idempotency_key
                        ) from None
                    raise
                if authority is None:
                    raise RuntimeError("scheduler enqueue authority returned no result")
                result = EnqueueResult(
                    job=self._job_record(
                        self._select_job(connection, authority["job_id"])
                    ),
                    outcome=EnqueueOutcome(authority["outcome"]),
                )
                self._insert_scheduler_heartbeat(
                    connection,
                    scheduler_id=scheduler_id,
                    code_commit=code_commit,
                    actor_id=request.actor.actor_id,
                    trace_id=trace_id,
                    tick_at=tick_at,
                    slot_at=slot_at,
                    outcome=result.outcome.value,
                    job_id=result.job.job_id,
                    reason_code=result.outcome.value,
                )
                return result

    def record_scheduler_heartbeat(
        self,
        *,
        scheduler_id: str,
        code_commit: str,
        actor_id: str,
        trace_id: str,
        tick_at: datetime,
        slot_at: datetime | None,
        outcome: str,
        job_id: str | None = None,
        reason_code: str,
    ) -> None:
        """Append a scheduler heartbeat in its own transaction."""

        self._validate_trace_id(trace_id)
        with self._pool.connection() as connection:
            with connection.transaction():
                self._insert_scheduler_heartbeat(
                    connection,
                    scheduler_id=scheduler_id,
                    code_commit=code_commit,
                    actor_id=actor_id,
                    trace_id=trace_id,
                    tick_at=tick_at,
                    slot_at=slot_at,
                    outcome=str(outcome),
                    job_id=job_id,
                    reason_code=reason_code,
                )

    def _insert_scheduler_heartbeat(
        self,
        connection: Connection[Any],
        *,
        scheduler_id: str,
        code_commit: str,
        actor_id: str,
        trace_id: str,
        tick_at: datetime,
        slot_at: datetime | None,
        outcome: str,
        job_id: str | None,
        reason_code: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO scheduler_heartbeats (
                heartbeat_id, scheduler_id, code_commit, actor_id, trace_id,
                tick_at, slot_at, outcome, job_id, reason_code, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                self._new_id("heartbeat"),
                scheduler_id,
                code_commit,
                actor_id,
                trace_id,
                tick_at,
                slot_at,
                outcome,
                job_id,
                reason_code,
            ),
        )

    def get_job(self, job_id: str) -> JobDetailRecord | None:
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                row = connection.execute(
                    f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id = %s", (job_id,)
                ).fetchone()
                if row is None:
                    return None
                attempts = connection.execute(
                    """
                    SELECT attempt_id, job_id, attempt_number, worker_id, outcome,
                           claimed_at, started_at, finished_at, exit_code,
                           termination_reason
                    FROM job_attempts
                    WHERE job_id = %s
                    ORDER BY attempt_number ASC, attempt_id ASC
                    """,
                    (job_id,),
                ).fetchall()
                events = connection.execute(
                    """
                    SELECT event_id, job_id, attempt_id, sequence, from_state,
                           to_state, reason_code, actor_type, actor_id, trace_id,
                           metadata, created_at
                    FROM job_events
                    WHERE job_id = %s
                    ORDER BY sequence ASC, event_id ASC
                    """,
                    (job_id,),
                ).fetchall()
                artifacts = connection.execute(
                    """
                    SELECT artifact_id, job_id, attempt_id, artifact_type,
                           relative_ref, sha256, size_bytes, media_type, truncated,
                           validator_id, validation_metadata, created_at
                    FROM job_artifacts
                    WHERE job_id = %s
                    ORDER BY created_at ASC, artifact_id ASC
                    """,
                    (job_id,),
                ).fetchall()
        return JobDetailRecord(
            job=self._job_record(row),
            attempts=tuple(AttemptRecord(**attempt) for attempt in attempts),
            events=tuple(self._event_record(event) for event in events),
            artifacts=tuple(self._artifact_record(artifact) for artifact in artifacts),
        )

    def list_jobs(self, filters: JobFilters | None = None) -> tuple[JobRecord, ...]:
        if filters is not None and not isinstance(filters, JobFilters):
            raise InvalidJobFilters("list filters must be a JobFilters value")
        selected = filters or JobFilters()
        job_type, state, actor_type = self._validate_filters(selected)
        predicates: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("job_type", job_type.value if job_type else None),
            ("state", state.value if state else None),
            ("actor_type", actor_type.value if actor_type else None),
            ("actor_id", selected.actor_id),
        ):
            if value is not None:
                predicates.append(f"{column} = %s")
                parameters.append(value)
        if selected.requested_from is not None:
            predicates.append("requested_at >= %s")
            parameters.append(selected.requested_from)
        if selected.requested_to is not None:
            predicates.append("requested_at <= %s")
            parameters.append(selected.requested_to)
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        parameters.extend((selected.limit, selected.offset))
        with self._pool.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM jobs
                {where}
                ORDER BY requested_at DESC, job_id DESC
                LIMIT %s OFFSET %s
                """,
                parameters,
            ).fetchall()
        return tuple(self._job_record(row) for row in rows)

    @staticmethod
    def _validate_filters(
        filters: JobFilters,
    ) -> tuple[JobType | None, JobState | None, ActorType | None]:
        if (
            not isinstance(filters.limit, int)
            or isinstance(filters.limit, bool)
            or not 1 <= filters.limit <= 100
        ):
            raise InvalidJobFilters("list limit must be between 1 and 100")
        if (
            not isinstance(filters.offset, int)
            or isinstance(filters.offset, bool)
            or filters.offset < 0
        ):
            raise InvalidJobFilters("list offset must be non-negative")
        if filters.actor_id is not None and not isinstance(filters.actor_id, str):
            raise InvalidJobFilters("actor_id must be a string")
        if filters.actor_id is not None and filters.actor_type is None:
            raise InvalidJobFilters("actor_id requires actor_type")
        for name, value in (
            ("requested_from", filters.requested_from),
            ("requested_to", filters.requested_to),
        ):
            if value is not None and (
                not isinstance(value, datetime) or value.utcoffset() is None
            ):
                raise InvalidJobFilters(f"{name} must be a timezone-aware datetime")
        if (
            filters.requested_from
            and filters.requested_to
            and filters.requested_from > filters.requested_to
        ):
            raise InvalidJobFilters("requested time bounds are reversed")
        try:
            job_type = JobType(filters.job_type) if filters.job_type is not None else None
            state = JobState(filters.state) if filters.state is not None else None
            actor_type = (
                ActorType(filters.actor_type) if filters.actor_type is not None else None
            )
        except (TypeError, ValueError) as error:
            raise InvalidJobFilters("list filter contains an unknown enum value") from error
        return job_type, state, actor_type

    def request_cancel(
        self, job_id: str, actor: ActorIdentity, trace_id: str
    ) -> JobRecord:
        self._validate_trace_id(trace_id)
        if actor.actor_type is not ActorType.OPERATOR:
            raise ValueError("Job API actor authority is invalid")
        with self._pool.connection() as connection:
            with connection.transaction():
                authority = connection.execute(
                    """
                    SELECT job_id, state, changed
                    FROM job_plane.api_cancel_snapshot(%s, %s, %s, %s)
                    """,
                    (job_id, actor.actor_id, trace_id, self._new_id("event")),
                ).fetchone()
                if authority is None:
                    raise JobNotFound("job does not exist")
                return self._job_record(
                    self._select_job(connection, authority["job_id"])
                )

    @staticmethod
    def _select_job(connection: Connection[Any], job_id: str) -> dict[str, Any]:
        row = connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id = %s", (job_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("transition authority returned an unknown job")
        return row

    @staticmethod
    def _job_record(row: dict[str, Any]) -> JobRecord:
        cancel_actor = None
        if row["cancel_actor_type"] is not None:
            cancel_actor = ActorIdentity(
                actor_type=row["cancel_actor_type"], actor_id=row["cancel_actor_id"]
            )
        return JobRecord(
            job_id=row["job_id"],
            job_type=JobType(row["job_type"]),
            state=JobState(row["state"]),
            payload=parse_payload(row["job_type"], row["payload"]),
            payload_fingerprint=row["payload_fingerprint"],
            idempotency_key=row["idempotency_key"],
            actor=ActorIdentity(
                actor_type=row["actor_type"], actor_id=row["actor_id"]
            ),
            priority=row["priority"],
            requested_at=row["requested_at"],
            updated_at=row["updated_at"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            reason_code=row["reason_code"],
            result_hash=row["result_hash"],
            cancel_requested_at=row["cancel_requested_at"],
            cancel_actor=cancel_actor,
        )

    @staticmethod
    def _event_record(row: dict[str, Any]) -> EventRecord:
        return EventRecord(
            event_id=row["event_id"],
            job_id=row["job_id"],
            attempt_id=row["attempt_id"],
            sequence=row["sequence"],
            from_state=JobState(row["from_state"]) if row["from_state"] else None,
            to_state=JobState(row["to_state"]),
            reason_code=row["reason_code"],
            actor=ActorIdentity(
                actor_type=row["actor_type"], actor_id=row["actor_id"]
            ),
            trace_id=row["trace_id"],
            metadata=JobRepository._freeze_mapping(row["metadata"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _artifact_record(row: dict[str, Any]) -> ArtifactRecord:
        return ArtifactRecord(
            **{
                **row,
                "validation_metadata": JobRepository._freeze_mapping(
                    row["validation_metadata"]
                ),
            }
        )

    @staticmethod
    def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, _FrozenJson]:
        return MappingProxyType(
            {key: JobRepository._freeze_json(item) for key, item in value.items()}
        )

    @staticmethod
    def _freeze_json(value: object) -> _FrozenJson:
        if isinstance(value, Mapping):
            return JobRepository._freeze_mapping(value)
        if isinstance(value, list):
            return tuple(JobRepository._freeze_json(item) for item in value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError("database metadata contains a non-JSON value")
