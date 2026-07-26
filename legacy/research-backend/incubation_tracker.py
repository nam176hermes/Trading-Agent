"""
incubation_tracker.py - Paper incubation safety gate.

Incubation state failures always close the gate. Corrupt state is never replaced
with an empty successful-looking document and persisted `passed` flags are
recomputed from validated resolved signals.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from runtime_paths import data_root

log = logging.getLogger("incubation_tracker")

MEMORY_DIR = data_root() / "memory"
INCUBATION_LOG = MEMORY_DIR / "incubation_log.json"

MIN_PAPER_SIGNALS = 20
MIN_WIN_RATE = 0.50
_VALID_OUTCOMES = {None, "win", "loss"}


class IncubationStateError(RuntimeError):
    """Typed failure for unavailable or corrupt incubation state."""

    def __init__(self, reason_code: str, trace_id: str):
        self.reason_code = reason_code
        self.trace_id = trace_id
        super().__init__(f"{reason_code} trace_id={trace_id}")


def _raise_state_error(reason_code: str, exc: BaseException) -> None:
    trace_id = uuid4().hex[:16]
    log.error(
        "event=incubation_state_unavailable trace_id=%s reason_code=%s "
        "error_type=%s",
        trace_id,
        reason_code,
        type(exc).__name__,
    )
    raise IncubationStateError(reason_code, trace_id) from exc


def _validate_state(state: object) -> dict:
    if not isinstance(state, dict):
        raise ValueError("incubation state must be an object")
    signals = state.get("signals")
    if not isinstance(signals, list):
        raise ValueError("incubation signals must be a list")
    for signal in signals:
        if not isinstance(signal, dict):
            raise ValueError("incubation signal must be an object")
        if not isinstance(signal.get("symbol"), str):
            raise ValueError("incubation signal symbol is invalid")
        if not isinstance(signal.get("action"), str):
            raise ValueError("incubation signal action is invalid")
        if not isinstance(signal.get("confidence"), (int, float)):
            raise ValueError("incubation signal confidence is invalid")
        if signal.get("outcome") not in _VALID_OUTCOMES:
            raise ValueError("incubation signal outcome is invalid")
    return state


def _load() -> dict:
    if not INCUBATION_LOG.exists():
        return {"signals": [], "passed": False}
    try:
        return _validate_state(json.loads(INCUBATION_LOG.read_text()))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        _raise_state_error("INCUBATION_STATE_INVALID", exc)
    raise AssertionError("unreachable")


def _save(state: dict) -> None:
    try:
        validated = _validate_state(state)
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        temporary = INCUBATION_LOG.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(validated, indent=2))
        temporary.replace(INCUBATION_LOG)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        _raise_state_error("INCUBATION_WRITE_FAILED", exc)


def record_paper_signal(
    symbol: str,
    action: str,
    confidence: float,
    outcome: Optional[str] = None,
) -> None:
    """Log a paper signal without overwriting unavailable state."""
    if outcome not in _VALID_OUTCOMES:
        raise ValueError("outcome must be win, loss, or None")
    state = _load()
    state.setdefault("signals", []).append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "outcome": outcome,
        }
    )
    state["passed"] = False
    _save(state)


def record_outcome(symbol: str, outcome: str) -> None:
    """Update the most recent pending paper signal for a symbol."""
    if outcome not in {"win", "loss"}:
        raise ValueError("outcome must be win or loss")
    state = _load()
    for signal in reversed(state.get("signals", [])):
        if signal["symbol"] == symbol and signal.get("outcome") is None:
            signal["outcome"] = outcome
            state["passed"] = False
            _save(state)
            return
    raise ValueError(f"no pending incubation signal for {symbol}")


def _resolved_signals(state: dict) -> list[dict]:
    return [
        signal
        for signal in state.get("signals", [])
        if signal.get("outcome") in ("win", "loss")
    ]


def is_incubation_passed() -> bool:
    """Return False on any unavailable state and recompute all pass criteria."""
    try:
        state = _load()
    except IncubationStateError as exc:
        log.error(
            "event=incubation_gate_closed trace_id=%s reason_code=%s",
            exc.trace_id,
            exc.reason_code,
        )
        return False

    resolved = _resolved_signals(state)
    if len(resolved) < MIN_PAPER_SIGNALS:
        log.info(
            "event=incubation_gate_pending resolved=%d required=%d",
            len(resolved),
            MIN_PAPER_SIGNALS,
        )
        return False

    wins = sum(1 for signal in resolved if signal["outcome"] == "win")
    win_rate = wins / len(resolved)
    if win_rate < MIN_WIN_RATE:
        log.info(
            "event=incubation_gate_pending resolved=%d win_rate=%.4f "
            "required_win_rate=%.4f",
            len(resolved),
            win_rate,
            MIN_WIN_RATE,
        )
        return False

    state["passed"] = True
    state["passed_at"] = datetime.now(timezone.utc).isoformat()
    state["win_rate"] = round(win_rate, 3)
    state["n_signals"] = len(resolved)
    _save(state)
    log.info(
        "event=incubation_gate_passed resolved=%d win_rate=%.4f",
        len(resolved),
        win_rate,
    )
    return True


def incubation_status() -> dict:
    """Return an explicit availability status and fail-closed pass state."""
    try:
        state = _load()
    except IncubationStateError as exc:
        return {
            "status": "UNAVAILABLE",
            "reason_code": exc.reason_code,
            "trace_id": exc.trace_id,
            "passed": False,
            "n_resolved": None,
            "n_required": MIN_PAPER_SIGNALS,
            "win_rate": None,
            "min_win_rate": MIN_WIN_RATE,
            "progress_pct": None,
        }

    resolved = _resolved_signals(state)
    wins = sum(1 for signal in resolved if signal["outcome"] == "win")
    win_rate = wins / len(resolved) if resolved else 0.0
    criteria_met = (
        len(resolved) >= MIN_PAPER_SIGNALS
        and win_rate >= MIN_WIN_RATE
    )
    return {
        "status": "AVAILABLE",
        "reason_code": None,
        "trace_id": None,
        "passed": criteria_met,
        "n_resolved": len(resolved),
        "n_required": MIN_PAPER_SIGNALS,
        "win_rate": round(win_rate, 3),
        "min_win_rate": MIN_WIN_RATE,
        "progress_pct": round(
            min(len(resolved) / MIN_PAPER_SIGNALS, 1.0) * 100,
            1,
        ),
    }


if __name__ == "__main__":
    import json as _json
    import logging as _logging

    _logging.basicConfig(level=logging.INFO)
    print(_json.dumps(incubation_status(), indent=2))
