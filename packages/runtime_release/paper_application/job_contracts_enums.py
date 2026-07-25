"""Canonical paper-only job enums."""

from enum import StrEnum

class JobType(StrEnum):
    SNAPSHOT = "SNAPSHOT"

class JobState(StrEnum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    TIMED_OUT = "TIMED_OUT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"

class ActorType(StrEnum):
    OPERATOR = "OPERATOR"
    SCHEDULER = "SCHEDULER"
    WORKER = "WORKER"
    RECOVERY = "RECOVERY"
    SYSTEM = "SYSTEM"

TERMINAL_JOB_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.BLOCKED, JobState.TIMED_OUT, JobState.CANCELLED})
ACTIVE_JOB_STATES = frozenset({JobState.QUEUED, JobState.CLAIMED, JobState.RUNNING, JobState.CANCEL_REQUESTED})

__all__ = ["ACTIVE_JOB_STATES", "ActorType", "JobState", "JobType", "TERMINAL_JOB_STATES"]
