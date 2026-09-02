"""Pure authorization and transition policy for operator commands."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from .contracts import (
    OperatorActorV1,
    OperatorSafetyEvidenceV1,
    OperatorSourceStateV1,
    SetKillSwitchV1,
    SetRequestedModeV1,
    SubmitOperatorCommandV1,
)
from .hashing import reason_sha256 as hash_reason
from .hashing import request_sha256


_SAFETY_EVIDENCE_MAX_AGE = timedelta(seconds=6)


class OperatorCommandRejected(ValueError):
    def __init__(self, code: str, http_status: int) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class OperatorMutationPlan:
    request_sha256: str
    operation: Literal[
        "NO_CHANGE", "WRITE_MODE", "WRITE_KILL_SWITCH", "CLEAR_KILL_SWITCH"
    ]
    outcome_code: str
    prior_state_sha256: str
    expected_state_sha256: str | None
    safety_evidence_sha256: str | None
    reason_sha256: str | None
    desired_mode_bytes: bytes | None
    desired_kill_switch_bytes: bytes | None
    desired_file_sha256: str | None


def _reject(code: str, status: int) -> None:
    raise OperatorCommandRejected(code, status)


def _file_digest(payload: bytes | None) -> str | None:
    return hashlib.sha256(payload).hexdigest() if payload is not None else None


def _plan(
    *,
    actor: OperatorActorV1,
    request: SubmitOperatorCommandV1,
    current: OperatorSourceStateV1,
    operation: Literal[
        "NO_CHANGE", "WRITE_MODE", "WRITE_KILL_SWITCH", "CLEAR_KILL_SWITCH"
    ],
    outcome_code: str,
    safety: OperatorSafetyEvidenceV1 | None = None,
    reason_digest: str | None = None,
    mode_bytes: bytes | None = None,
    kill_bytes: bytes | None = None,
    desired_digest: str | None = None,
) -> OperatorMutationPlan:
    desired = mode_bytes if mode_bytes is not None else kill_bytes
    return OperatorMutationPlan(
        request_sha256=request_sha256(actor, request),
        operation=operation,
        outcome_code=outcome_code,
        prior_state_sha256=current.state_sha256,
        expected_state_sha256=request.expected_state_sha256,
        safety_evidence_sha256=safety.evidence_sha256 if safety else None,
        reason_sha256=reason_digest,
        desired_mode_bytes=mode_bytes,
        desired_kill_switch_bytes=kill_bytes,
        desired_file_sha256=desired_digest or _file_digest(desired),
    )


def _check_expected(
    request: SubmitOperatorCommandV1, current: OperatorSourceStateV1
) -> None:
    expected = request.expected_state_sha256
    if expected is not None and expected != current.state_sha256:
        _reject("EXPECTED_STATE_CONFLICT", 409)


def _decide_mode(
    actor: OperatorActorV1,
    request: SubmitOperatorCommandV1,
    current: OperatorSourceStateV1,
    command: SetRequestedModeV1,
) -> OperatorMutationPlan:
    if actor.interface != "CLI":
        _reject("CAPABILITY_FORBIDDEN", 403)
    if command.desired_mode == "DRYRUN":
        _reject("PAPER_ONLY_RELEASE", 403)
    if command.desired_mode == "LIVE":
        _reject("LIVE_EXECUTION_DISABLED", 403)
    _check_expected(request, current)
    if current.requested_mode == "PAPER":
        return _plan(
            actor=actor,
            request=request,
            current=current,
            operation="NO_CHANGE",
            outcome_code="MODE_ALREADY_PAPER",
        )
    return _plan(
        actor=actor,
        request=request,
        current=current,
        operation="WRITE_MODE",
        outcome_code="MODE_SET_PAPER",
        mode_bytes=b"paper\n",
    )


def _decide_kill_switch(
    actor: OperatorActorV1,
    request: SubmitOperatorCommandV1,
    current: OperatorSourceStateV1,
    accepted_at: datetime,
    safety: OperatorSafetyEvidenceV1 | None,
    command: SetKillSwitchV1,
) -> OperatorMutationPlan:
    if command.desired_state == "INACTIVE" and actor.interface != "CLI":
        _reject("CAPABILITY_FORBIDDEN", 403)
    if command.desired_state == "INACTIVE" and request.expected_state_sha256 is None:
        _reject("EXPECTED_STATE_REQUIRED", 409)
    _check_expected(request, current)
    if current.kill_switch_state == "UNKNOWN":
        _reject("SOURCE_STATE_UNKNOWN", 503)
    if command.desired_state == "ACTIVE":
        if current.kill_switch_state == "ACTIVE":
            return _plan(
                actor=actor,
                request=request,
                current=current,
                operation="NO_CHANGE",
                outcome_code="KILL_SWITCH_ALREADY_ACTIVE",
            )
        assert command.reason is not None
        frozen = accepted_at.isoformat().replace("+00:00", "Z").encode("ascii")
        desired = frozen + b": " + command.reason.encode("utf-8") + b"\n"
        return _plan(
            actor=actor,
            request=request,
            current=current,
            operation="WRITE_KILL_SWITCH",
            outcome_code="KILL_SWITCH_ACTIVATED",
            reason_digest=hash_reason(command.reason),
            kill_bytes=desired,
        )

    if current.kill_switch_state != "ACTIVE":
        _reject("KILL_SWITCH_NOT_ACTIVE", 409)
    if current.kill_switch_file_sha256 is None:
        _reject("SOURCE_STATE_UNKNOWN", 503)
    if safety is None:
        _reject("SAFETY_EVIDENCE_REQUIRED", 503)
    age = accepted_at - safety.observed_at
    if (
        age < timedelta(0)
        or age >= _SAFETY_EVIDENCE_MAX_AGE
        or safety.requested_mode != "PAPER"
        or safety.effective_mode != "PAPER"
        or safety.live_execution_enabled is not False
        or safety.live_trading_approved is not False
        or safety.kill_switch_state != "ACTIVE"
    ):
        _reject("KILL_SWITCH_CLEAR_UNSAFE", 409)
    return _plan(
        actor=actor,
        request=request,
        current=current,
        operation="CLEAR_KILL_SWITCH",
        outcome_code="KILL_SWITCH_CLEARED",
        safety=safety,
        desired_digest=current.kill_switch_file_sha256,
    )


def decide_operator_command(
    *,
    actor: OperatorActorV1,
    request: SubmitOperatorCommandV1,
    current: OperatorSourceStateV1,
    accepted_at: datetime,
    safety: OperatorSafetyEvidenceV1 | None,
) -> OperatorMutationPlan:
    if accepted_at.tzinfo is not UTC:
        _reject("OPERATOR_CLOCK_INVALID", 503)
    if isinstance(request.command, SetRequestedModeV1):
        return _decide_mode(actor, request, current, request.command)
    return _decide_kill_switch(
        actor, request, current, accepted_at, safety, request.command
    )
