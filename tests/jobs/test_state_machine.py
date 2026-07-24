import itertools

import pytest

from packages.job_contracts import (
    InvalidTransition,
    JobState,
    TransitionDisposition,
    cancel_target,
    validate_transition,
)


ALLOWED_CASES = [
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
]
ALLOWED_SET = set(ALLOWED_CASES)
RETRY_CASES = [
    (JobState.FAILED, JobState.QUEUED),
    (JobState.TIMED_OUT, JobState.QUEUED),
]
FORBIDDEN_CASES = [
    pair
    for pair in itertools.product(JobState, repeat=2)
    if pair not in ALLOWED_SET and pair not in RETRY_CASES
]


@pytest.mark.parametrize("source,target", ALLOWED_CASES)
def test_allowed_transition(source, target):
    assert (
        validate_transition(source, target, "TEST_REASON", trace_id="trace-test")
        is TransitionDisposition.APPLY
    )


@pytest.mark.parametrize("source,target", FORBIDDEN_CASES)
def test_forbidden_transition(source, target):
    with pytest.raises(InvalidTransition):
        validate_transition(source, target, "TEST_REASON", trace_id="trace-test")


@pytest.mark.parametrize("source,target", RETRY_CASES)
def test_retry_transitions_require_explicit_policy_approval(source, target):
    with pytest.raises(InvalidTransition, match="retry policy"):
        validate_transition(source, target, "TRANSIENT_FAILURE", trace_id="trace-test")

    assert (
        validate_transition(
            source,
            target,
            "TRANSIENT_FAILURE",
            retry_allowed=True,
            trace_id="trace-test",
        )
        is TransitionDisposition.APPLY
    )


@pytest.mark.parametrize("reason_code", [None, "", " ", "unsafe reason", "bad\nreason"])
def test_transition_requires_a_bounded_reason_code(reason_code):
    with pytest.raises(InvalidTransition, match="reason code"):
        validate_transition(
            JobState.QUEUED,
            JobState.CLAIMED,
            reason_code,
            trace_id="trace-test",
        )


def test_transition_rejects_an_omitted_trace_id():
    with pytest.raises(TypeError, match="trace_id"):
        validate_transition(JobState.QUEUED, JobState.CLAIMED, "CLAIMED")


@pytest.mark.parametrize("trace_id", [None, "", " ", "bad\ntrace"])
def test_explicit_transition_trace_must_be_present_and_safe(trace_id):
    with pytest.raises(InvalidTransition, match="trace"):
        validate_transition(
            JobState.QUEUED,
            JobState.CLAIMED,
            "CLAIMED",
            trace_id=trace_id,
        )


@pytest.mark.parametrize(
    "state",
    [
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.BLOCKED,
        JobState.TIMED_OUT,
        JobState.CANCELLED,
    ],
)
def test_terminal_cancel_is_an_idempotent_noop(state):
    assert cancel_target(state) is state
    assert (
        validate_transition(state, state, "CANCEL_NOOP", trace_id="trace-test")
        is TransitionDisposition.NOOP
    )


@pytest.mark.parametrize(
    "state,target",
    [
        (JobState.QUEUED, JobState.CANCELLED),
        (JobState.CLAIMED, JobState.CANCEL_REQUESTED),
        (JobState.RUNNING, JobState.CANCEL_REQUESTED),
        (JobState.CANCEL_REQUESTED, JobState.CANCEL_REQUESTED),
    ],
)
def test_cancel_target_is_centralized(state, target):
    assert cancel_target(state) is target


def test_unknown_state_is_rejected_as_an_invalid_transition():
    with pytest.raises(InvalidTransition):
        validate_transition(
            "MADE_UP",
            JobState.RUNNING,
            "TEST_REASON",
            trace_id="trace-test",
        )
