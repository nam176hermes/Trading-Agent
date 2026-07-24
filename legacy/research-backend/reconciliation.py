"""
reconciliation.py — Backtest-to-live performance reconciliation.

Compares the most recent walk-forward backtest metrics against live paper
trading stats and produces a divergence report. Called by the dashboard
via /api/trading/reconciliation and can be run standalone as a CLI tool.

Output:
  - summary dict with drift scores
  - writes reports/reconciliation_<ts>.json
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root, reports_dir

log = logging.getLogger("reconciliation")

BACKTEST_DIR  = data_root() / "memory" / "backtest"
REPORTS_DIR   = reports_dir()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_latest_walkforward() -> Optional[dict]:
    if not BACKTEST_DIR.exists():
        return None
    files = sorted(
        [f for f in os.listdir(BACKTEST_DIR)
         if f.startswith("walk_forward_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None
    try:
        return json.loads((BACKTEST_DIR / files[0]).read_text())
    except Exception as e:
        log.warning("Failed to read walk-forward file: %s", e)
        return None


def _extract_backtest_metrics(wf: dict) -> dict:
    """Pull aggregate metrics from walk-forward result dict."""
    if "results" in wf:
        syms = list(wf["results"].keys())
        if not syms:
            return {}
        agg = wf["results"][syms[0]].get("aggregate", {})
    else:
        agg = wf.get("aggregate", {})

    return {
        "sharpe":        agg.get("sharpe", 0.0),
        "win_rate":      agg.get("avg_win_rate", 0.0),
        "max_drawdown":  agg.get("max_drawdown_pct", 0.0),
        "total_pnl":     agg.get("total_pnl", 0.0),
        "total_trades":  agg.get("total_trades", 0),
        "consistency":   agg.get("consistency", 0.0),
        "last_run":      wf.get("generated_at"),
    }


def _load_live_metrics() -> dict:
    """Load live paper trading metrics via portfolio_stats."""
    try:
        from portfolio_stats import compute_all
        stats = compute_all()
        wr_pct = stats.get("win_rate", 0.0) or 0.0
        dd_pct = stats.get("max_drawdown_pct", 0.0) or 0.0
        return {
            "sharpe":       stats.get("sharpe_ratio", 0.0),
            "win_rate":     round(wr_pct / 100.0, 4),   # normalize to decimal (backtest uses 0.2 not 20)
            "max_drawdown": round(dd_pct / 100.0, 4),   # normalize to decimal (backtest uses 0.49 not 49)
            "total_pnl":    stats.get("pnl_pct", 0.0),
            "total_trades": stats.get("total_trades", 0),
        }
    except Exception as e:
        log.warning("portfolio_stats.compute_all() failed: %s", e)
        return {}


# ── Reconciliation logic ──────────────────────────────────────────────────────

def _drift_status(bt_val: float, live_val: float, threshold: float) -> str:
    """Classify drift between backtest and live metric."""
    if bt_val == 0:
        return "no_backtest_data"
    drift = abs(live_val - bt_val) / max(abs(bt_val), 1e-9)
    if drift <= threshold:
        return "aligned"
    elif drift <= threshold * 2:
        return "mild_drift"
    else:
        return "significant_drift"


def reconcile() -> dict:
    """
    Compare most recent walk-forward results to live paper trading.
    Returns a structured report with per-metric drift analysis.
    """
    wf = _load_latest_walkforward()
    live = _load_live_metrics()

    if wf is None:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "no_backtest_data",
            "message": "No walk-forward results found. Run walk-forward validation first.",
            "backtest": {},
            "live": live,
            "drift": {},
            "overall_drift": "unknown",
        }

    bt = _extract_backtest_metrics(wf)

    if not live or live.get("total_trades", 0) < 5:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "insufficient_live_data",
            "message": f"Only {live.get('total_trades', 0)} live trades — need ≥5 for reconciliation.",
            "backtest": bt,
            "live": live,
            "drift": {},
            "overall_drift": "unknown",
            "backtest_last_run": bt.get("last_run"),
        }

    # Per-metric drift analysis (threshold = 30% relative deviation = "aligned")
    drift = {
        "sharpe": {
            "backtest": round(bt.get("sharpe", 0), 3),
            "live": round(live.get("sharpe", 0), 3),
            "delta": round(live.get("sharpe", 0) - bt.get("sharpe", 0), 3),
            "status": _drift_status(bt.get("sharpe", 0), live.get("sharpe", 0), 0.30),
        },
        "win_rate": {
            "backtest": round(bt.get("win_rate", 0), 3),
            "live": round(live.get("win_rate", 0), 3),
            "delta": round(live.get("win_rate", 0) - bt.get("win_rate", 0), 3),
            "status": _drift_status(bt.get("win_rate", 0), live.get("win_rate", 0), 0.30),
        },
        "max_drawdown": {
            "backtest": round(bt.get("max_drawdown", 0), 3),
            "live": round(live.get("max_drawdown", 0), 3),
            "delta": round(live.get("max_drawdown", 0) - bt.get("max_drawdown", 0), 3),
            "status": _drift_status(bt.get("max_drawdown", 0), live.get("max_drawdown", 0), 0.30),
        },
    }

    # Overall drift score
    statuses = [d["status"] for d in drift.values()]
    if all(s == "aligned" for s in statuses):
        overall = "aligned"
    elif any(s == "significant_drift" for s in statuses):
        overall = "significant_drift"
    elif any(s == "mild_drift" for s in statuses):
        overall = "mild_drift"
    else:
        overall = "aligned"

    # Recommendations
    recommendations = []
    if drift["sharpe"]["status"] != "aligned":
        if drift["sharpe"]["delta"] < 0:
            recommendations.append(
                f"Sharpe live={drift['sharpe']['live']:.2f} vs backtest={drift['sharpe']['backtest']:.2f}: "
                "strategy underperforming backtest expectations — review signal filter or regime detection."
            )
        else:
            recommendations.append(
                f"Sharpe live={drift['sharpe']['live']:.2f} outperforming backtest={drift['sharpe']['backtest']:.2f}: "
                "check for look-ahead bias or market-condition change."
            )

    if drift["win_rate"]["status"] != "aligned":
        recommendations.append(
            f"Win rate drift: live={drift['win_rate']['live']:.1%} vs backtest={drift['win_rate']['backtest']:.1%}."
        )

    if drift["max_drawdown"]["delta"] > 0.05:
        recommendations.append(
            f"Live drawdown ({drift['max_drawdown']['live']:.1%}) exceeds backtest ({drift['max_drawdown']['backtest']:.1%}). "
            "Consider tightening stop-losses."
        )

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "backtest_last_run": bt.get("last_run"),
        "live_trades": live.get("total_trades", 0),
        "backtest_trades": bt.get("total_trades", 0),
        "backtest": {k: v for k, v in bt.items() if k != "last_run"},
        "live": live,
        "drift": drift,
        "overall_drift": overall,
        "recommendations": recommendations,
    }

    # Persist report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"reconciliation_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("Reconciliation report → %s (overall: %s)", out_path, overall)

    return report


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    report = reconcile()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
