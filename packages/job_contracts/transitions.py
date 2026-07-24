"""Single state-transition policy for API, store, worker, and recovery code."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final

from .enums import JobState


class InvalidTransition(ValueError):
    """Raised when a requested state mutation is outside the approved policy."""


class TransitionDisposition(StrEnum):
    APPLY = "APPLY"
    NOOP = "NOOP"


ORDINARY_TRANSITIONS: Final[frozenset[tuple[JobState, JobState]]] = frozenset(
    {
        (JobState.QUEUED, JobState.CLAIMED),
        (JobState.QUEUED, JobState.CANCELLED),
        (JobState.CLAIMED, JobState.RUNNING),
        (JobState.CLAIMED, JobState.CANCEL_REQUESTED),
        (JobState.CLAIMED, JobState.BLOCKED),
        (JobState.RUNNING, JobState.SUCCEEDED),
        (JobState.RUNNING, JobState.FAILED),
        (JobState.RUNNING, JobState.TIMED_OUT),
        (JobState.RUNNING, JobState.CANCEL_REQUESTED),
        (JobState.RUNNING, JobState.BLOCKED),
        (JobState.CANCEL_REQUESTED, JobState.CANCELLED),
        (JobState.CANCEL_REQUESTED, JobState.BLOCKED),
    }
)
RETRY_TRANSITIONS: Final[frozenset[tuple[JobState, JobState]]] = frozenset(
    {
        (JobState.FAILED, JobState.QUEUED),
        (JobState.TIMED_OUT, JobState.QUEUED),
    }
)
TERMINAL_STATES: Final[frozenset[JobState]] = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.BLOCKED,
        JobState.TIMED_OUT,
        JobState.CANCELLED,
    }
)
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$", re.ASCII)
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", re.ASCII)


def _state(value: JobState | str) -> JobState:
    try:
        return JobState(value)
    except (TypeError, ValueError) as exc:
        raise InvalidTransition(f"unknown job state: {value!r}") from exc


def _validate_context(reason_code: str, trace_id: str) -> None:
    if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
        raise InvalidTransition("transition requires a valid reason code")
    if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
        raise InvalidTransition("transition requires a valid trace identity")


def validate_transition(
    current: JobState | str,
    target: JobState | str,
    reason_code: str,
    *,
    retry_allowed: bool = False,
    trace_id: str,
) -> TransitionDisposition:
    """Validate an intended transition and return whether to apply or no-op.

    Retry eligibility is decided by the fixed retry policy and must be supplied
    explicitly. A terminal cancel is represented by a same-state CANCEL_* call,
    preserving the existing row without inventing a state transition.
    """

    _validate_context(reason_code, trace_id)
    source = _state(current)
    destination = _state(target)
    pair = (source, destination)

    if source in TERMINAL_STATES and source is destination and reason_code.startswith(
        "CANCEL_"
    ):
        return TransitionDisposition.NOOP
    if pair in ORDINARY_TRANSITIONS:
        return TransitionDisposition.APPLY
    if pair in RETRY_TRANSITIONS:
        if retry_allowed is not True:
            raise InvalidTransition("transition requires fixed retry policy approval")
        return TransitionDisposition.APPLY
    raise InvalidTransition(f"transition is not allowed: {source.value} -> {destination.value}")


def cancel_target(current: JobState | str) -> JobState:
    """Return the only state a cancellation request may target."""

    state = _state(current)
    if state is JobState.QUEUED:
        return JobState.CANCELLED
    if state in {JobState.CLAIMED, JobState.RUNNING}:
        return JobState.CANCEL_REQUESTED
    return state
