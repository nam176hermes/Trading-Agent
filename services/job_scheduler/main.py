"""Scheduler oneshot composition root; importing it has no runtime side effects."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import socket
import sys
from uuid import uuid4

from services.job_store.config import JobStoreSettings
from services.job_store.repository import JobRepository

from .scheduler import (
    SchedulerHeartbeatOutcome,
    SchedulerIdentity,
    schedule_tick,
)


def _identity() -> SchedulerIdentity:
    scheduler_id = os.environ.get(
        "TRADING_SCHEDULER_ID"
    ) or f"scheduler-{socket.gethostname()}"
    return SchedulerIdentity(
        scheduler_id=scheduler_id,
        code_commit=os.environ.get("TRADING_CODE_COMMIT", "unknown"),
        actor_id=os.environ.get("TRADING_SCHEDULER_ACTOR_ID", scheduler_id),
        trace_id=f"scheduler-{uuid4().hex}",
    )


def main(
    *,
    now: datetime | None = None,
    identity: SchedulerIdentity | None = None,
) -> int:
    try:
        with JobRepository(
            JobStoreSettings.from_env(expected_user="trading_job_scheduler")
        ) as repository:
            result = schedule_tick(
                now or datetime.now(timezone.utc),
                repository,
                identity or _identity(),
            )
        return 1 if result.outcome is SchedulerHeartbeatOutcome.FAILED else 0
    except Exception:
        print("scheduler tick failed: database outcome unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
