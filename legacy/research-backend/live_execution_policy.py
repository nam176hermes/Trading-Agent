"""Central live-execution authorization policy.

This module contains no broker or credential handling. It only evaluates
explicit safety inputs and returns a structured decision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping

TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def is_explicitly_true(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in TRUTHY_VALUES


@dataclass(frozen=True)
class LiveExecutionDecision:
    allowed: bool
    requested_mode: str
    effective_mode: str
    execution_capability: str
    reason_code: str


class LiveExecutionPolicy:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        kill_switch_reader: Callable[[], object] | None = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._kill_switch_reader = kill_switch_reader or self._default_kill_switch_reader

    @staticmethod
    def _default_kill_switch_reader() -> object:
        from kill_switch import read_kill_switch_state
        return read_kill_switch_state().state

    def evaluate(
        self,
        requested_mode: str,
        *,
        risk_preflight_pass: bool = True,
        adapter_initialized: bool = True,
        credentials_available: bool = True,
    ) -> LiveExecutionDecision:
        requested = requested_mode.strip().lower()
        if requested != "live":
            return LiveExecutionDecision(False, requested, requested, "NON_LIVE", "MODE_NOT_LIVE")
        if not is_explicitly_true(self._env.get("LIVE_EXECUTION_ENABLED")):
            return LiveExecutionDecision(False, requested, "paper", "LIVE_BLOCKED", "LIVE_EXECUTION_DISABLED")
        if not is_explicitly_true(self._env.get("LIVE_TRADING_APPROVED")):
            return LiveExecutionDecision(False, requested, "paper", "LIVE_BLOCKED", "LIVE_APPROVAL_MISSING")

        raw_state = self._kill_switch_reader()
        state = getattr(raw_state, "value", raw_state)
        state = getattr(state, "state", state)
        state = getattr(state, "value", state)
        if state != "INACTIVE":
            return LiveExecutionDecision(False, requested, "paper", "LIVE_BLOCKED", "KILL_SWITCH_ACTIVE")
        if not risk_preflight_pass:
            return LiveExecutionDecision(False, requested, "paper", "LIVE_BLOCKED", "RISK_PREFLIGHT_FAILED")
        if not credentials_available:
            return LiveExecutionDecision(False, requested, "paper", "LIVE_BLOCKED", "CREDENTIALS_UNAVAILABLE")
        if not adapter_initialized:
            return LiveExecutionDecision(False, requested, "paper", "LIVE_BLOCKED", "EXECUTION_ADAPTER_UNAVAILABLE")
        return LiveExecutionDecision(True, requested, "live", "LIVE_ALLOWED", "ALLOW")
