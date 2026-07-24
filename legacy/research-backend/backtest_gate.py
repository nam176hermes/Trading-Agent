"""
backtest_gate.py — Pre-trade gate that checks walk-forward validation results.

Gate rules:
- If aggregate Sharpe < 0.5 OR win rate < 45%: BLOCK trades
- If most recent test window was negative: reduce position size by 50%
- If no backtest data exists: ALLOW with warning (first-run mode)

Usage:
  from backtest_gate import GateChecker
  gate = GateChecker()
  result = gate.check(symbol="BTC")
  if result["status"] == "block":
      print("Trades blocked:", result["reason"])
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("backtest_gate")

BACKTEST_DIR = data_root() / "memory" / "backtest"
MIN_SHARPE = 0.5       # block if aggregate walk-forward Sharpe < 0.5
MIN_WIN_RATE = 0.40   # block if win rate < 40%
MIN_TRADES_FOR_GATE = 30  # need at least 30 completed trades for statistically meaningful gate
DEFAULT_POSITION_MODIFIER = 1.0
NEGATIVE_WINDOW_MODIFIER = 0.5


class GateResult:
    """Result of a gate check."""
    def __init__(self, status: str, reason: str, position_modifier: float = 1.0,
                 metrics: Optional[dict] = None):
        self.status = status            # "allow", "block", "warning"
        self.reason = reason
        self.position_modifier = position_modifier
        self.metrics = metrics or {}

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "position_modifier": self.position_modifier,
            "metrics": self.metrics,
        }

    def __repr__(self):
        return f"GateResult(status={self.status}, reason={self.reason}, mod={self.position_modifier})"


def _find_latest_walk_forward(symbol: Optional[str] = None) -> dict | None:
    """Find the most recent walk-forward result file."""
    pattern = "walk_forward_"
    if not BACKTEST_DIR.exists():
        return None

    files = sorted(
        [f for f in os.listdir(BACKTEST_DIR)
         if f.startswith(pattern) and f.endswith(".json")],
        reverse=True,
    )

    if symbol:
        symbol = symbol.upper()
        files = [f for f in files if f"_{symbol}_" in f or f.startswith(f"walk_forward_combined")]

    if not files:
        return None

    try:
        path = BACKTEST_DIR / files[0]
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read walk-forward results: %s", e)
        return None


def _extract_metrics(result: dict, symbol: Optional[str] = None) -> dict:
    """Extract gate-relevant metrics from a walk-forward result dict."""
    # Combined format: {"results": {"BTC": {"aggregate": {...}}}}
    if "results" in result:
        sym_data = None
        if symbol:
            sym_data = result["results"].get(symbol.upper())
        if not sym_data:
            # Take first symbol if none specified
            syms = list(result["results"].keys())
            if syms:
                sym_data = result["results"][syms[0]]
        if sym_data:
            agg = sym_data.get("aggregate", {})
            return {
                "sharpe": agg.get("sharpe", 0),
                "win_rate": agg.get("avg_win_rate", 0),
                "total_pnl": agg.get("total_pnl", 0),
                "max_drawdown_pct": agg.get("max_drawdown_pct", 0),
                "consistency": agg.get("consistency", 0),
                "total_trades": agg.get("total_trades", 0),
                "last_run": result.get("generated_at"),
            }

    # Single-symbol format: {"aggregate": {...}, "windows": [...]}
    agg = result.get("aggregate", {})
    windows = result.get("windows", [])
    recent_pnl = windows[-1]["total_pnl"] if windows else 0

    return {
        "sharpe": agg.get("sharpe", 0),
        "win_rate": agg.get("avg_win_rate", 0),
        "total_pnl": agg.get("total_pnl", 0),
        "max_drawdown_pct": agg.get("max_drawdown_pct", 0),
        "consistency": agg.get("consistency", 0),
        "total_trades": agg.get("total_trades", 0),
        "recent_window_pnl": recent_pnl,
        "last_run": result.get("generated_at"),
    }


class GateChecker:
    """Checks walk-forward validation results before allowing live trades."""

    def __init__(self):
        self._last_result: Optional[GateResult] = None

    def check(self, symbol: Optional[str] = None) -> dict:
        """
        Check if trades should be allowed for the given symbol.

        Returns dict with:
            status: "allow" | "block" | "warning"
            reason: human-readable explanation
            position_modifier: 1.0 (normal), 0.5 (reduced), 0.0 (blocked)
            metrics: relevant backtest metrics
        """
        result = _find_latest_walk_forward(symbol)

        # ── No data: first-run mode ──
        if result is None:
            self._last_result = GateResult(
                status="allow",
                reason="No backtest data — first-run mode (trades allowed)",
                position_modifier=DEFAULT_POSITION_MODIFIER,
                metrics={"last_run": None},
            )
            log.info("Gate: ALLOW (no backtest data)")
            return self._last_result.to_dict()

        metrics = _extract_metrics(result, symbol)
        sharpe = metrics["sharpe"]
        win_rate = metrics["win_rate"]
        total_trades = metrics.get("total_trades", 0)

        # ── Insufficient data: allow (not statistically meaningful yet) ──
        if total_trades < MIN_TRADES_FOR_GATE:
            self._last_result = GateResult(
                status="allow",
                reason=f"Only {total_trades} trades — need {MIN_TRADES_FOR_GATE}+ for statistical gate (learning mode)",
                position_modifier=DEFAULT_POSITION_MODIFIER,
                metrics=metrics,
            )
            log.info("Gate: ALLOW (learning mode — %d/%d trades)", total_trades, MIN_TRADES_FOR_GATE)
            return self._last_result.to_dict()

        # ── Negative alpha: BLOCK ──
        if sharpe < MIN_SHARPE:
            self._last_result = GateResult(
                status="block",
                reason=f"Sharpe {sharpe:.2f} < {MIN_SHARPE} — strategy shows no edge",
                position_modifier=0.0,
                metrics=metrics,
            )
            log.warning("Gate: BLOCK (Sharpe %.2f < %.2f)", sharpe, MIN_SHARPE)
            return self._last_result.to_dict()

        if win_rate < MIN_WIN_RATE:
            self._last_result = GateResult(
                status="block",
                reason=f"Win rate {win_rate:.1%} < {MIN_WIN_RATE:.0%} — strategy shows no edge",
                position_modifier=0.0,
                metrics=metrics,
            )
            log.warning("Gate: BLOCK (win rate %.1%% < %.0%%)", win_rate * 100, MIN_WIN_RATE * 100)
            return self._last_result.to_dict()

        # ── Recent window negative: reduce position ──
        recent_pnl = metrics.get("recent_window_pnl", 0)
        if recent_pnl < 0:
            self._last_result = GateResult(
                status="allow",
                reason=f"Recent window P&L negative (${recent_pnl:,.2f}) — position size reduced 50%",
                position_modifier=NEGATIVE_WINDOW_MODIFIER,
                metrics=metrics,
            )
            log.info("Gate: ALLOW (reduced — recent window P&L < 0)")
            return self._last_result.to_dict()

        # ── Pass ──
        self._last_result = GateResult(
            status="allow",
            reason="Walk-forward validation passed",
            position_modifier=DEFAULT_POSITION_MODIFIER,
            metrics=metrics,
        )
        log.info("Gate: ALLOW (Sharpe=%.2f WinRate=%.1f%%)", sharpe, win_rate * 100)
        return self._last_result.to_dict()

    def get_dashboard_status(self, symbol: Optional[str] = None) -> dict:
        """
        Return a simplified status dict for dashboard display.
        """
        result = _find_latest_walk_forward(symbol)
        if result is None:
            return {
                "status": "no_data",
                "label": "⚠️ No backtest data",
                "icon": "⚠️",
                "last_run": None,
                "sharpe": None,
                "win_rate": None,
                "total_pnl": None,
            }

        metrics = _extract_metrics(result, symbol)
        sharpe = metrics["sharpe"]
        win_rate = metrics["win_rate"]

        if sharpe >= MIN_SHARPE and win_rate >= MIN_WIN_RATE:
            status = "pass"
            label = "✅ Walk-Forward Valid"
            icon = "✅"
        elif sharpe < MIN_SHARPE or win_rate < MIN_WIN_RATE:
            status = "fail"
            label = "❌ Walk-Forward Invalid"
            icon = "❌"
        else:
            status = "warning"
            label = "⚠️ Walk-Forward Marginal"
            icon = "⚠️"

        return {
            "status": status,
            "label": label,
            "icon": icon,
            "last_run": metrics["last_run"],
            "sharpe": sharpe,
            "win_rate": win_rate,
            "total_pnl": metrics["total_pnl"],
            "consistency": metrics["consistency"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
        }


# ── Module-level convenience function ──────────────────────────────────────────

_default_gate = GateChecker()


def check(symbol: Optional[str] = None) -> dict:
    """Convenience function: check gate for a symbol. Returns gate result dict."""
    return _default_gate.check(symbol)


def get_dashboard_status(symbol: Optional[str] = None) -> dict:
    """Convenience function: get dashboard-friendly status."""
    return _default_gate.get_dashboard_status(symbol)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="Backtest gate checker")
    parser.add_argument("--symbol", type=str, default=None, help="Symbol to check")
    parser.add_argument("--dashboard", action="store_true", help="Output dashboard status format")
    args = parser.parse_args()

    gate = GateChecker()
    if args.dashboard:
        status = gate.get_dashboard_status(args.symbol)
        print(json.dumps(status, indent=2, default=str))
    else:
        result = gate.check(args.symbol)
        print(json.dumps(result, indent=2, default=str))
