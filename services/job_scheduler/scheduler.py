"""Enqueue-only scheduler orchestration with durable heartbeat outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from packages.job_contracts import EnqueueJobRequest
from services.job_store.records import EnqueueOutcome, EnqueueResult

from .slots import Slot, slot_for_tick


class SchedulerHeartbeatOutcome(StrEnum):
    ENQUEUED = "ENQUEUED"
    DEDUPLICATED = "DEDUPLICATED"
    SKIPPED_NOT_SLOT = "SKIPPED_NOT_SLOT"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SchedulerIdentity:
    scheduler_id: str
    code_commit: str
    actor_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class SchedulerOutcome:
    outcome: SchedulerHeartbeatOutcome
    slot: Slot | None
    job_id: str | None = None


class SchedulerRepository(Protocol):
    def schedule_snapshot(self, **kwargs: object) -> EnqueueResult: ...

    def record_scheduler_heartbeat(self, **kwargs: object) -> None: ...


def _request(slot: Slot, identity: SchedulerIdentity) -> EnqueueJobRequest:
    return EnqueueJobRequest.model_validate(
        {
            "job_type": "SNAPSHOT",
            "payload": {"scope": "default", "requested_as_of": None},
            "idempotency_key": slot.idempotency_key,
            "actor": {"actor_type": "SCHEDULER", "actor_id": identity.actor_id},
            "priority": 0,
        }
    )


def schedule_tick(
    now: datetime,
    repository: SchedulerRepository,
    identity: SchedulerIdentity,
) -> SchedulerOutcome:
    """Persist exactly one outcome for the injected current scheduler tick."""

    slot = slot_for_tick(now)
    tick_at = now.astimezone(timezone.utc)
    heartbeat = {
        "scheduler_id": identity.scheduler_id,
        "code_commit": identity.code_commit,
        "actor_id": identity.actor_id,
        "trace_id": identity.trace_id,
        "tick_at": tick_at,
        "slot_at": slot.at if slot else None,
    }
    if slot is None:
        repository.record_scheduler_heartbeat(
            **heartbeat,
            outcome=SchedulerHeartbeatOutcome.SKIPPED_NOT_SLOT,
            reason_code="SKIPPED_NOT_SLOT",
        )
        return SchedulerOutcome(
            outcome=SchedulerHeartbeatOutcome.SKIPPED_NOT_SLOT, slot=None
        )

    try:
        result = repository.schedule_snapshot(
            request=_request(slot, identity),
            scheduler_id=identity.scheduler_id,
            code_commit=identity.code_commit,
            trace_id=identity.trace_id,
            tick_at=tick_at,
            slot_at=slot.at,
        )
    except Exception:
        repository.record_scheduler_heartbeat(
            **heartbeat,
            outcome=SchedulerHeartbeatOutcome.FAILED,
            reason_code="DATABASE_ERROR",
        )
        return SchedulerOutcome(
            outcome=SchedulerHeartbeatOutcome.FAILED, slot=slot
        )

    outcome = (
        SchedulerHeartbeatOutcome.ENQUEUED
        if result.outcome is EnqueueOutcome.ENQUEUED
        else SchedulerHeartbeatOutcome.DEDUPLICATED
    )
    return SchedulerOutcome(outcome=outcome, slot=slot, job_id=result.job.job_id)
