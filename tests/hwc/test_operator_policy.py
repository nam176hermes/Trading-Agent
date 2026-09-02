from __future__ import annotations

import hashlib
import inspect
from datetime import UTC, datetime, timedelta

import pytest

from packages.operator_control import policy
from packages.operator_control.contracts import (
    OperatorActorV1,
    OperatorSafetyEvidenceV1,
    OperatorSourceStateV1,
    SubmitOperatorCommandV1,
)
from packages.operator_control.policy import (
    OperatorCommandRejected,
    decide_operator_command,
)


NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
SHA = "a" * 64


def actor(interface: str) -> OperatorActorV1:
    return OperatorActorV1(
        schema_version="operator-actor-v1",
        principal_id="operator.alice",
        interface=interface,
    )


def state(*, mode: str = "PAPER", kill: str = "INACTIVE") -> OperatorSourceStateV1:
    return OperatorSourceStateV1(
        schema_version="operator-source-state-v1",
        requested_mode=mode,
        kill_switch_state=kill,
        kill_switch_activated_at=NOW - timedelta(minutes=1)
        if kill == "ACTIVE"
        else None,
        kill_switch_reason="existing incident" if kill == "ACTIVE" else None,
        mode_file_sha256=SHA if mode != "UNKNOWN" else None,
        kill_switch_file_sha256=SHA if kill == "ACTIVE" else None,
        state_sha256=SHA,
    )


def request(
    command: dict[str, object], *, expected: str | None = SHA
) -> SubmitOperatorCommandV1:
    return SubmitOperatorCommandV1.model_validate(
        {
            "schema_version": "submit-operator-command-v1",
            "command_id": "cmd_0123456789abcdef0123456789abcdef",
            "idempotency_key": "idem.1",
            "correlation_id": "corr.1",
            "expected_state_sha256": expected,
            "command": command,
        }
    )


def safety(**changes: object) -> OperatorSafetyEvidenceV1:
    values: dict[str, object] = {
        "schema_version": "operator-safety-evidence-v1",
        "requested_mode": "PAPER",
        "effective_mode": "PAPER",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "kill_switch_state": "ACTIVE",
        "observed_at": NOW - timedelta(seconds=1),
        "source_fingerprint": SHA,
        "evidence_sha256": SHA,
    }
    values.update(changes)
    return OperatorSafetyEvidenceV1.model_validate(values)


def decide(
    interface: str,
    command: dict[str, object],
    current: OperatorSourceStateV1,
    **kwargs: object,
):
    return decide_operator_command(
        actor=actor(interface),
        request=request(command, expected=kwargs.pop("expected", SHA)),
        current=current,
        accepted_at=NOW,
        safety=kwargs.pop("safety", None),
    )


def rejection_code(call) -> str:
    with pytest.raises(OperatorCommandRejected) as caught:
        call()
    assert caught.value.http_status in {403, 409, 422, 503}
    return caught.value.code


def test_complete_interface_command_source_state_matrix() -> None:
    for interface in ("WEB", "CLI"):
        for desired_mode in ("PAPER", "DRYRUN", "LIVE"):
            for current_mode in ("PAPER", "DRYRUN", "LIVE", "UNKNOWN"):
                call = lambda: decide(
                    interface,
                    {
                        "command_type": "SET_REQUESTED_MODE",
                        "desired_mode": desired_mode,
                    },
                    state(mode=current_mode),
                )
                if interface == "WEB":
                    assert rejection_code(call) == "CAPABILITY_FORBIDDEN"
                elif desired_mode == "DRYRUN":
                    assert rejection_code(call) == "PAPER_ONLY_RELEASE"
                elif desired_mode == "LIVE":
                    assert rejection_code(call) == "LIVE_EXECUTION_DISABLED"
                else:
                    assert call().operation == (
                        "NO_CHANGE" if current_mode == "PAPER" else "WRITE_MODE"
                    )

        for desired_kill in ("ACTIVE", "INACTIVE"):
            for current_kill in ("ACTIVE", "INACTIVE", "UNKNOWN"):
                command = {
                    "command_type": "SET_KILL_SWITCH",
                    "desired_state": desired_kill,
                    "reason": "incident" if desired_kill == "ACTIVE" else None,
                }
                call = lambda: decide(
                    interface,
                    command,
                    state(kill=current_kill),
                    safety=safety() if desired_kill == "INACTIVE" else None,
                )
                if desired_kill == "INACTIVE" and interface == "WEB":
                    assert rejection_code(call) == "CAPABILITY_FORBIDDEN"
                elif current_kill == "UNKNOWN":
                    assert rejection_code(call) == "SOURCE_STATE_UNKNOWN"
                elif desired_kill == "ACTIVE":
                    assert call().operation == (
                        "NO_CHANGE" if current_kill == "ACTIVE" else "WRITE_KILL_SWITCH"
                    )
                elif current_kill == "INACTIVE":
                    assert rejection_code(call) == "KILL_SWITCH_NOT_ACTIVE"
                else:
                    assert call().operation == "CLEAR_KILL_SWITCH"


def test_expected_state_is_checked_and_required_for_clear() -> None:
    set_mode = {"command_type": "SET_REQUESTED_MODE", "desired_mode": "PAPER"}
    assert (
        rejection_code(lambda: decide("CLI", set_mode, state(), expected="b" * 64))
        == "EXPECTED_STATE_CONFLICT"
    )
    clear = {
        "command_type": "SET_KILL_SWITCH",
        "desired_state": "INACTIVE",
        "reason": None,
    }
    assert (
        rejection_code(
            lambda: decide(
                "CLI", clear, state(kill="ACTIVE"), expected=None, safety=safety()
            )
        )
        == "EXPECTED_STATE_REQUIRED"
    )
    assert (
        rejection_code(
            lambda: decide("CLI", clear, state(kill="UNKNOWN"), expected=None)
        )
        == "EXPECTED_STATE_REQUIRED"
    )


def test_activation_freezes_exact_bytes_and_digests_without_replacing_existing_metadata() -> (
    None
):
    command = {
        "command_type": "SET_KILL_SWITCH",
        "desired_state": "ACTIVE",
        "reason": "  incident  ",
    }
    plan = decide("WEB", command, state())
    assert plan.desired_kill_switch_bytes == b"2026-09-02T12:00:00Z: incident\n"
    assert (
        plan.desired_file_sha256
        == hashlib.sha256(plan.desired_kill_switch_bytes).hexdigest()
    )
    assert plan.reason_sha256 == hashlib.sha256(b"incident").hexdigest()
    assert [decide("WEB", command, state()) for _ in range(3)] == [plan, plan, plan]

    unchanged = decide("WEB", command, state(kill="ACTIVE"))
    assert unchanged.operation == "NO_CHANGE"
    assert unchanged.desired_kill_switch_bytes is None
    assert unchanged.reason_sha256 is None


def test_paper_mode_uses_exact_lowercase_source_bytes() -> None:
    plan = decide(
        "CLI",
        {"command_type": "SET_REQUESTED_MODE", "desired_mode": "PAPER"},
        state(mode="UNKNOWN"),
    )
    assert plan.desired_mode_bytes == b"paper\n"
    assert plan.desired_file_sha256 == hashlib.sha256(b"paper\n").hexdigest()


@pytest.mark.parametrize(
    "changes",
    (
        {"requested_mode": "UNKNOWN"},
        {"effective_mode": "DRYRUN"},
        {"live_execution_enabled": None},
        {"live_execution_enabled": True},
        {"live_trading_approved": None},
        {"live_trading_approved": True},
        {"kill_switch_state": "UNKNOWN"},
        {"kill_switch_state": "INACTIVE"},
        {"observed_at": NOW - timedelta(seconds=6)},
        {"observed_at": NOW + timedelta(microseconds=1)},
    ),
)
def test_clear_fails_closed_on_unknown_unsafe_or_stale_safety(
    changes: dict[str, object],
) -> None:
    clear = {
        "command_type": "SET_KILL_SWITCH",
        "desired_state": "INACTIVE",
        "reason": None,
    }
    assert (
        rejection_code(
            lambda: decide("CLI", clear, state(kill="ACTIVE"), safety=safety(**changes))
        )
        == "KILL_SWITCH_CLEAR_UNSAFE"
    )


def test_clear_requires_safety_evidence() -> None:
    clear = {
        "command_type": "SET_KILL_SWITCH",
        "desired_state": "INACTIVE",
        "reason": None,
    }
    assert (
        rejection_code(lambda: decide("CLI", clear, state(kill="ACTIVE")))
        == "SAFETY_EVIDENCE_REQUIRED"
    )


def test_policy_module_has_no_io_or_runtime_imports() -> None:
    source = inspect.getsource(policy)
    for forbidden in (
        "pathlib",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "psycopg",
        "apps.",
        "services.",
    ):
        assert forbidden not in source
