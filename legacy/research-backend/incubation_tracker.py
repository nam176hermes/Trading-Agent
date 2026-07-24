"""
incubation_tracker.py — Kevin Davey incubation gate.

Before any live order is permitted, the strategy must produce
MIN_PAPER_SIGNALS resolved paper-trade signals with a win rate
>= MIN_WIN_RATE. This catches slippage and data anomalies not
visible in backtest.

Usage:
    from incubation_tracker import is_incubation_passed, record_paper_signal
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("incubation_tracker")

MEMORY_DIR = data_root() / "memory"
INCUBATION_LOG = MEMORY_DIR / "incubation_log.json"

MIN_PAPER_SIGNALS = 20   # resolved paper trades required before live
MIN_WIN_RATE = 0.50      # paper win rate threshold to pass gate


def _load() -> dict:
    if INCUBATION_LOG.exists():
        try:
            return json.loads(INCUBATION_LOG.read_text())
        except Exception:
            pass
    return {"signals": [], "passed": False}


def _save(state: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    INCUBATION_LOG.write_text(json.dumps(state, indent=2))


def record_paper_signal(symbol: str, action: str, confidence: float,
                        outcome: Optional[str] = None) -> None:
    """Log a paper trade signal. outcome is 'win'/'loss'/None (pending)."""
    state = _load()
    state.setdefault("signals", []).append({
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "symbol":     symbol,
        "action":     action,
        "confidence": confidence,
        "outcome":    outcome,
    })
    _save(state)


def record_outcome(symbol: str, outcome: str) -> None:
    """Update the most recent pending signal for a symbol with its outcome."""
    state = _load()
    for sig in reversed(state.get("signals", [])):
        if sig["symbol"] == symbol and sig.get("outcome") is None:
            sig["outcome"] = outcome
            break
    _save(state)


def is_incubation_passed() -> bool:
    """Return True if the incubation gate has been cleared."""
    state = _load()
    if state.get("passed"):
        return True

    resolved = [s for s in state.get("signals", [])
                if s.get("outcome") in ("win", "loss")]
    if len(resolved) < MIN_PAPER_SIGNALS:
        log.info("Incubation: %d/%d signals resolved", len(resolved), MIN_PAPER_SIGNALS)
        return False

    wins = sum(1 for s in resolved if s["outcome"] == "win")
    win_rate = wins / len(resolved)

    if win_rate >= MIN_WIN_RATE:
        state["passed"] = True
        state["passed_at"] = datetime.now(timezone.utc).isoformat()
        state["win_rate"] = round(win_rate, 3)
        state["n_signals"] = len(resolved)
        _save(state)
        log.info("Incubation gate PASSED: %d signals, %.0f%% win rate",
                 len(resolved), win_rate * 100)
        return True

    log.info("Incubation: %d signals resolved, win rate %.0f%% < %.0f%% required",
             len(resolved), win_rate * 100, MIN_WIN_RATE * 100)
    return False


def incubation_status() -> dict:
    """Return progress summary toward incubation gate."""
    state = _load()
    resolved = [s for s in state.get("signals", [])
                if s.get("outcome") in ("win", "loss")]
    wins = sum(1 for s in resolved if s["outcome"] == "win")
    return {
        "passed":        state.get("passed", False),
        "n_resolved":    len(resolved),
        "n_required":    MIN_PAPER_SIGNALS,
        "win_rate":      round(wins / len(resolved), 3) if resolved else 0.0,
        "min_win_rate":  MIN_WIN_RATE,
        "progress_pct":  round(len(resolved) / MIN_PAPER_SIGNALS * 100, 1),
    }


if __name__ == "__main__":
    import logging as _logging
    import json as _json
    _logging.basicConfig(level=logging.INFO)
    print(_json.dumps(incubation_status(), indent=2))
