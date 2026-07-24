"""
strategy_risk_manager.py — Per-strategy rolling drawdown kill switch.

Each strategy (ml, ta, pairs, rl, alpha) gets an independent 7-day
rolling drawdown budget. Breaching the limit suspends that strategy
for COOLDOWN_HOURS, blocking its signals from assembly.py.

Usage:
    from strategy_risk_manager import is_strategy_allowed, record_strategy_pnl
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("strategy_risk_manager")

MEMORY_DIR = data_root() / "memory"
RISK_STATE_FILE = MEMORY_DIR / "strategy_risk_state.json"

STRATEGY_LIMITS: dict[str, float] = {
    "ml":      8.0,
    "ta":      6.0,
    "pairs":   5.0,
    "rl":      4.0,
    "alpha":   7.0,
}
LOOKBACK_DAYS = 7
COOLDOWN_HOURS = 24


def _load() -> dict:
    if RISK_STATE_FILE.exists():
        try:
            return json.loads(RISK_STATE_FILE.read_text())
        except Exception:
            pass
    return {"strategies": {}, "pnl_log": []}


def _save(state: dict) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    RISK_STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def record_strategy_pnl(strategy: str, pnl_pct: float) -> None:
    """Record a realized P&L % for a strategy (called after each closed trade)."""
    state = _load()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    log_entries = [e for e in state.get("pnl_log", []) if e["ts"] >= cutoff]
    log_entries.append({
        "ts":       datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "pnl_pct":  pnl_pct,
    })
    state["pnl_log"] = log_entries
    _save(state)


def _rolling_drawdown(strategy: str, state: dict) -> float:
    """Compute rolling peak-to-trough drawdown over LOOKBACK_DAYS for a strategy."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    entries = [e for e in state.get("pnl_log", [])
               if e["strategy"] == strategy and e["ts"] >= cutoff]
    if not entries:
        return 0.0
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for e in entries:
        equity *= (1 + e["pnl_pct"] / 100)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 3)


def is_strategy_allowed(strategy: str) -> tuple[bool, Optional[str]]:
    """
    Check whether a strategy may contribute signals this cycle.
    Returns (allowed: bool, reason: str | None).
    """
    state = _load()
    strat_info = state.get("strategies", {}).get(strategy, {})

    # Check active suspension
    suspended_until = strat_info.get("suspended_until")
    if suspended_until:
        now = datetime.now(timezone.utc).isoformat()
        if now < suspended_until:
            return False, f"{strategy} suspended until {suspended_until[:16]}Z (drawdown limit)"
        # Cooldown expired — clear
        state.setdefault("strategies", {})[strategy] = {}
        _save(state)

    limit = STRATEGY_LIMITS.get(strategy, 10.0)
    dd = _rolling_drawdown(strategy, state)

    if dd >= limit:
        until = (datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)).isoformat()
        state.setdefault("strategies", {})[strategy] = {
            "suspended_until":           until,
            "drawdown_at_suspension":    dd,
            "limit":                     limit,
        }
        _save(state)
        log.warning("[%s] Suspended: %.1f%% drawdown >= %.1f%% limit — cooling down %dh",
                    strategy, dd, limit, COOLDOWN_HOURS)
        return False, f"{strategy} suspended: {dd:.1f}% 7d drawdown >= {limit}% limit"

    return True, None


def strategy_risk_summary() -> dict:
    """Return current drawdown and status for all tracked strategies."""
    state = _load()
    result = {}
    for strat, limit in STRATEGY_LIMITS.items():
        dd = _rolling_drawdown(strat, state)
        allowed, reason = is_strategy_allowed(strat)
        result[strat] = {
            "drawdown_7d_pct": dd,
            "limit_pct":       limit,
            "allowed":         allowed,
            "reason":          reason,
        }
    return result


if __name__ == "__main__":
    import logging as _logging
    import json as _json
    _logging.basicConfig(level=logging.INFO)
    print(_json.dumps(strategy_risk_summary(), indent=2))
