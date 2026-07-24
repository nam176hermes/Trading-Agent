"""
weekly_report.py — Weekly/Monthly performance reports.

Generated every Monday at 9 AM (via cron or manual trigger).
Reports: P&L summary, top/bottom 3 trades, Sharpe, drawdown, strategy notes.
Delivered via Telegram.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("weekly_report")

PAPER_DIR = data_root() / "memory" / "paper"
PORTFOLIO_FILE = PAPER_DIR / "portfolio.json"
ORDERS_FILE = PAPER_DIR / "orders.jsonl"
PNL_HISTORY_FILE = data_root() / "memory" / "pnl_history.jsonl"
PNL_SUMMARY_FILE = data_root() / "memory" / "pnl_summary.json"
TRADE_JOURNAL_FILE = data_root() / "memory" / "trade_journal.jsonl"
WEEKLY_DIR = data_root() / "weekly_reports"


def _format_currency(v: float) -> str:
    return f"${v:,.2f}"


def _format_pct(v: float) -> str:
    return f"{v:+.1f}%"


def load_portfolio() -> dict:
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"cash": 100000, "positions": {}, "pnl": 0, "peak_equity": 100000}


def _load_orders_since(since: datetime) -> list[dict]:
    """Load orders since a given datetime."""
    orders = []
    if ORDERS_FILE.exists():
        for line in ORDERS_FILE.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                o = json.loads(line)
                ts = o.get("timestamp", "")
                if ts:
                    try:
                        ot = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if ot >= since:
                            orders.append(o)
                    except (ValueError, TypeError):
                        # Try parsing as date string
                        if ts >= since.strftime("%Y-%m-%d"):
                            orders.append(o)
            except json.JSONDecodeError:
                continue
    return orders


def _load_pnl_since(since: datetime) -> list[dict]:
    """Load P&L snapshots since a given datetime."""
    snapshots = []
    if PNL_HISTORY_FILE.exists():
        for line in PNL_HISTORY_FILE.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                s = json.loads(line)
                ts = s.get("timestamp", "")
                if ts:
                    try:
                        st = datetime.fromisoformat(ts)
                        if st >= since:
                            snapshots.append(s)
                    except ValueError:
                        continue
            except json.JSONDecodeError:
                continue
    return snapshots


def _estimate_equity(pf: dict) -> float:
    positions_value = 0.0
    for sym, pos in pf.get("positions", {}).items():
        positions_value += pos.get("shares", 0) * pos.get("avg_cost", 0)
    return pf.get("cash", 0) + positions_value


def _sharpe_ratio(returns: list[float]) -> float:
    """Calculate Sharpe ratio from daily returns (risk-free = 0)."""
    if len(returns) < 3:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
    std = variance ** 0.5
    if std == 0:
        return 0.0
    # Annualized: daily Sharpe * sqrt(365) for crypto
    return (avg / std) * (365 ** 0.5)


def generate_weekly_report() -> str:
    """Generate a weekly performance report as markdown."""
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = now

    pf = load_portfolio()
    equity = _estimate_equity(pf)
    start_equity = 100000.0  # paper default
    peak = pf.get("peak_equity", 100000)
    drawdown = (peak - equity) / peak * 100 if peak > 0 else 0

    orders = _load_orders_since(week_start)
    sells = [o for o in orders if o.get("side") == "SELL"]
    buys = [o for o in orders if o.get("side") == "BUY"]

    realized_pnl = sum(o.get("pnl", 0) for o in sells)
    total_fees = sum(o.get("cost", 0) * 0.001 for o in orders)

    # Win/loss analysis
    wins = [o for o in sells if o.get("pnl", 0) > 0]
    losses = [o for o in sells if o.get("pnl", 0) < 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    # Top/bottom trades
    sorted_sells = sorted(sells, key=lambda o: o.get("pnl", 0), reverse=True)
    top3 = sorted_sells[:3]
    bottom3 = sorted_sells[-3:] if len(sorted_sells) >= 3 else sorted_sells[::-1]

    # P&L history for Sharpe
    pnl_snapshots = _load_pnl_since(week_start)
    daily_returns = []
    for i in range(1, len(pnl_snapshots)):
        prev_eq = pnl_snapshots[i - 1].get("equity", 100000)
        curr_eq = pnl_snapshots[i].get("equity", 100000)
        if prev_eq > 0:
            daily_returns.append((curr_eq - prev_eq) / prev_eq)

    sharpe = _sharpe_ratio(daily_returns)

    # Position summary
    positions = pf.get("positions", {})
    pos_lines = ""
    if positions:
        for sym, pos in positions.items():
            pos_lines += f"\n  • {sym}: {pos.get('shares', 0):.4f} @ ${pos.get('avg_cost', 0):,.2f}"
    else:
        pos_lines = "\n  *No open positions*"

    # Top trades section
    top_lines = ""
    for t in top3:
        pnl = t.get("pnl", 0)
        top_lines += f"\n  • {t.get('symbol')} {t.get('side')} — {_format_currency(pnl)}"

    bottom_lines = ""
    for t in bottom3:
        pnl = t.get("pnl", 0)
        bottom_lines += f"\n  • {t.get('symbol')} {t.get('side')} — {_format_currency(pnl)}"

    # Strategy notes
    strategy_notes = ""
    if win_rate >= 60:
        strategy_notes = "✅ Win rate above 60% — strategy is performing well."
    elif win_rate >= 40:
        strategy_notes = "⚠️ Win rate moderate — consider tighter stops or smaller position sizes."
    else:
        strategy_notes = "🔴 Win rate below 40% — review entry criteria and stop placement."

    if drawdown > 15:
        strategy_notes += "\n⚠️ Drawdown >15% — consider reducing exposure."
    if abs(sharpe) > 1:
        strategy_notes += f"\n✅ Sharpe ratio {sharpe:.2f} — risk-adjusted returns positive."
    elif sharpe < 0:
        strategy_notes += "\n🔴 Negative Sharpe — strategy is underperforming cash."

    date_range = f"{week_start.strftime('%b %d')} → {week_end.strftime('%b %d, %Y')}"
    report_id = f"weekly_report_{week_start.strftime('%Y%m%d')}"

    report = f"""📊 *Weekly Performance Report*
*{date_range}*

━━━━━━━━━━━━━━━━━━━━

*Portfolio*
  Equity: {_format_currency(equity)}
  Cash: {_format_currency(pf.get('cash', 0))}
  Drawdown: {drawdown:.1f}% (peak ${peak:,.0f})
  Open Positions: {len(positions)}

*Trading Activity*
  Total Trades: {len(orders)} ({len(buys)} buys, {len(sells)} sells)
  Realized P&L: {_format_currency(realized_pnl)}
  Win Rate: {win_rate:.0f}% ({len(wins)}W / {len(losses)}L)

*Risk Metrics*
  Sharpe Ratio: {sharpe:.2f}
  Max Drawdown (period): {drawdown:.1f}%

*Top 3 Trades*{top_lines}

*Bottom 3 Trades*{bottom_lines}

*Open Positions*{pos_lines}

*Strategy Notes*
{strategy_notes}

━━━━━━━━━━━━━━━━━━━━

🔹 Report ID: `{report_id}`
"""

    return report


def save_weekly_report(report_md: str) -> Path:
    """Save the weekly report to disk and return the path."""
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())
    filename = f"weekly_report_{week_start.strftime('%Y%m%d')}.md"

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    filepath = WEEKLY_DIR / filename
    filepath.write_text(report_md)
    log.info("Weekly report saved: %s", filepath)
    return filepath


def send_to_telegram(report: str) -> bool:
    """Send the report to Telegram via alert_manager."""
    try:
        from alert_manager import send_telegram_text
        if send_telegram_text(report):
            log.info("Weekly report sent to Telegram")
            return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    report = generate_weekly_report()
    print(report)

    filepath = save_weekly_report(report)
    print(f"\nSaved to: {filepath}")

    if send_to_telegram(report):
        print("✅ Sent to Telegram")
    else:
        print("⚠️ Telegram not configured — report saved locally only")
