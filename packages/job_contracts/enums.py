"""Closed enumerations shared by the durable job components."""

from enum import StrEnum


class JobType(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    DEBATE = "DEBATE"
    REPLAY = "REPLAY"
    BACKTEST = "BACKTEST"


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
