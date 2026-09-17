from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from packages.job_contracts import (
    ActorIdentity,
    JobPayload,
    JobState,
    JobType,
)


def validate_p3_job_id(job_id: object) -> None:
    if not isinstance(job_id,str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}",job_id,re.ASCII) is None:
        raise ValueError("P3 bound job id is invalid")


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



class EnqueueOutcome(StrEnum):
    ENQUEUED = "ENQUEUED"
    DEDUPLICATED = "DEDUPLICATED"


@dataclass(frozen=True, slots=True)
class JobFilters:
    job_type: JobType | str | None = None
    state: JobState | str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    requested_from: datetime | None = None
    requested_to: datetime | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    job_type: JobType
    state: JobState
    payload: JobPayload
    payload_fingerprint: str
    idempotency_key: str
    actor: ActorIdentity
    priority: int
    requested_at: datetime
    updated_at: datetime
    attempt_count: int
    max_attempts: int
    reason_code: str | None
    result_hash: str | None
    cancel_requested_at: datetime | None
    cancel_actor: ActorIdentity | None


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    job_id: str
    attempt_id: str | None
    sequence: int
    from_state: JobState | None
    to_state: JobState
    reason_code: str
    actor: ActorIdentity
    trace_id: str
    metadata: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    job_id: str
    attempt_number: int
    worker_id: str
    outcome: str
    claimed_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    termination_reason: str | None


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    job_id: str
    attempt_id: str
    artifact_type: str
    relative_ref: str
    sha256: str
    size_bytes: int
    media_type: str
    truncated: bool
    validator_id: str
    validation_metadata: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class JobDetailRecord:
    job: JobRecord
    attempts: tuple[AttemptRecord, ...] = ()
    events: tuple[EventRecord, ...] = ()
    artifacts: tuple[ArtifactRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job: JobRecord
    outcome: EnqueueOutcome
