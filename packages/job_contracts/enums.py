"""Closed enumerations shared by the durable job components."""

from enum import StrEnum


class JobType(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    DEBATE = "DEBATE"
    REPLAY = "REPLAY"
    BACKTEST = "BACKTEST"
    ALPHA_CAMPAIGN = "ALPHA_CAMPAIGN"


class AlphaCampaignOperation(StrEnum):
    BASELINES = "BASELINES"
    REGISTER_FAMILY = "REGISTER_FAMILY"
    OOS = "OOS"
    HOLDOUT = "HOLDOUT"
    PARITY = "PARITY"
    PHASE_EXIT = "PHASE_EXIT"


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
