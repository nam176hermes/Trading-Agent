"""
daily_report.py — End-of-day P&L summary.

Reads portfolio + today's orders to compute:
starting equity, ending equity, PnL, PnL%, trades, win/loss, fees.
Outputs markdown suitable for Telegram.
"""

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import data_root

log = logging.getLogger("daily_report")

PAPER_DIR = data_root() / "memory" / "paper"
PORTFOLIO_FILE = PAPER_DIR / "portfolio.json"
ORDERS_FILE = PAPER_DIR / "orders.jsonl"
PNL_HISTORY_FILE = data_root() / "memory" / "pnl_history.jsonl"

# Realistic cost model: taker fee + slippage (Saleh methodology)
TAKER_FEE_PCT = 0.05    # 0.05% Coinbase/Kraken taker fee per leg
SLIPPAGE_PCT  = 0.10    # 0.10% average market-order slippage per leg
ROUND_TRIP_COST_PCT = (TAKER_FEE_PCT + SLIPPAGE_PCT) * 2  # entry + exit


def generate_daily_report() -> str:
    pf = load_portfolio()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cash = pf.get("cash", 0)
    equity = _estimate_equity(pf)
    peak = pf.get("peak_equity", 100000)
    drawdown = (peak - equity) / peak * 100 if peak > 0 else 0

    # Parse today's orders
    orders = _load_today_orders(today)
    buys = [o for o in orders if o.get("side") == "BUY"]
    sells = [o for o in orders if o.get("side") == "SELL"]

    total_trades = len(orders)
    buy_count = len(buys)
    sell_count = len(sells)
    total_pnl = sum(o.get("pnl", 0) for o in sells)

    # Apply realistic cost model
    cost_deduction = 0.0
    for o in orders:
        notional = o.get("cost", 0) or (o.get("shares", 0) * o.get("price", 0))
        cost_deduction += notional * (TAKER_FEE_PCT + SLIPPAGE_PCT) / 100
    cost_adjusted_pnl = total_pnl - cost_deduction

    win_trades = [o for o in sells if o.get("pnl", 0) > 0]
    loss_trades = [o for o in sells if o.get("pnl", 0) < 0]
    win_rate = len(win_trades) / len(sells) * 100 if sells else 0

    # Sortino ratio from pnl_history (EcoEngineering methodology)
    sortino = _compute_sortino()

    # Positions summary
    positions = pf.get("positions", {})
    pos_summary = ""
    for sym, pos in positions.items():
        pos_summary += f"\n  {sym}: {pos.get('shares', 0):.4f} @ ${pos.get('avg_cost', 0):,.2f}"

    report = f"""📈 *Daily P&L Report — {today}*

*Equity:* ${equity:,.0f}
*Drawdown:* {drawdown:.1f}% (peak=${peak:,.0f})
*Cash:* ${cash:,.0f}

*Trades Today:* {total_trades} ({buy_count} buys, {sell_count} sells)
*Gross P&L:* ${total_pnl:,.2f}
*Net P&L (after fees+slippage):* ${cost_adjusted_pnl:,.2f}
*Win Rate:* {win_rate:.0f}% ({len(win_trades)}W / {len(loss_trades)}L)
*Sortino Ratio (30d):* {sortino:.2f}

*Open Positions:* {len(positions)}{pos_summary if pos_summary else ' None'}
"""
    return report


def _compute_sortino(lookback_days: int = 30) -> float:
    """
    Sortino ratio from recent daily equity snapshots.
    Penalizes only downside volatility — better metric for asymmetric crypto gains.
    Returns annualized Sortino (sqrt(252) scaling).
    """
    if not PNL_HISTORY_FILE.exists():
        return 0.0
    try:
        entries = []
        for line in PNL_HISTORY_FILE.read_text().strip().split("\n"):
            if line:
                entries.append(json.loads(line))
        entries = entries[-lookback_days:]
        if len(entries) < 5:
            return 0.0
        equities = [e["equity"] for e in entries if "equity" in e]
        if len(equities) < 5:
            return 0.0
        returns = [(equities[i] - equities[i - 1]) / equities[i - 1]
                   for i in range(1, len(equities))]
        mean_ret = sum(returns) / len(returns)
        downside = [r for r in returns if r < 0]
        if not downside:
            return 0.0
        downside_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
        return round(mean_ret / downside_std * math.sqrt(252), 3) if downside_std > 0 else 0.0
    except Exception:
        return 0.0


def load_portfolio() -> dict:
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"cash": 0, "positions": {}, "pnl": 0, "peak_equity": 100000}


def _estimate_equity(pf: dict) -> float:
    """Estimate equity using position avg_cost as proxy (no live prices)."""
    positions_value = 0.0
    for sym, pos in pf.get("positions", {}).items():
        positions_value += pos.get("shares", 0) * pos.get("avg_cost", 0)
    return pf.get("cash", 0) + positions_value


def _load_today_orders(today: str) -> list[dict]:
    orders = []
    if ORDERS_FILE.exists():
        for line in ORDERS_FILE.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                o = json.loads(line)
                ts = o.get("timestamp", "")
                if ts.startswith(today):
                    orders.append(o)
            except json.JSONDecodeError:
                continue
    return orders


def save_pnl_snapshot():
    """Append daily equity snapshot to pnl history."""
    pf = load_portfolio()
    equity = _estimate_equity(pf)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "equity": round(equity, 2),
        "cash": round(pf.get("cash", 0), 2),
        "positions": len(pf.get("positions", {})),
        "pnl": round(pf.get("pnl", 0), 2),
        "sortino": _compute_sortino(),
    }
    PNL_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PNL_HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    report = generate_daily_report()
    print(report)
    save_pnl_snapshot()

    # Send to Telegram if configured
    try:
        from alert_manager import send_telegram_text
        if send_telegram_text(report):
            log.info("Daily report sent to Telegram")
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
