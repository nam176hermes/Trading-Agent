"""
portfolio_stats.py — Risk and performance metrics for paper trading.

Computes: Sharpe, Sortino, VaR, max drawdown, win rate, profit factor,
Calmar ratio, and more. Reads from paper_trader portfolio + trade journal.

Inspired by FinceptTerminal's PaperTrading::pt_get_stats().
"""

import json
import math
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from runtime_paths import data_root

log = logging.getLogger("portfolio_stats")

PAPER_DIR = data_root() / "memory" / "paper"
PORTFOLIO_FILE = PAPER_DIR / "portfolio.json"
JOURNAL_FILE = data_root() / "memory" / "trade_journal.jsonl"
INITIAL_CASH = 100_000.0
RISK_FREE_RATE = 0.04  # 4% annual


# ── Data Loading ───────────────────────────────────────────────

def load_trades() -> List[Dict]:
    """Load all completed trades from the journal."""
    if not JOURNAL_FILE.exists():
        return []
    trades = []
    with open(JOURNAL_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                trades.append(trade)
            except json.JSONDecodeError:
                continue
    return trades


def load_portfolio() -> Dict:
    """Load current paper portfolio state."""
    if not PORTFOLIO_FILE.exists():
        return {"cash": INITIAL_CASH, "positions": {}, "pnl": 0.0, "peak_equity": INITIAL_CASH}
    return json.loads(PORTFOLIO_FILE.read_text())


# ── Core Metrics ───────────────────────────────────────────────

def compute_returns(trades: List[Dict]) -> List[float]:
    """
    Extract PnL from each trade as return percentages.
    Returns list of decimal returns (e.g., 0.02 = 2% gain).
    """
    returns = []
    for t in trades:
        pnl = t.get("pnl", 0)
        # Approximate: divide PnL by INITIAL_CASH for return %
        returns.append(pnl / INITIAL_CASH)
    return returns


def daily_returns(trades: List[Dict]) -> List[float]:
    """
    Aggregate PnL by day for daily return series.
    Returns list of daily returns (as decimals).
    """
    if not trades:
        return []

    daily = {}
    for t in trades:
        ts = t.get("timestamp", "")
        if not ts:
            continue
        day = ts[:10]  # YYYY-MM-DD
        pnl = t.get("pnl", 0)
        daily[day] = daily.get(day, 0.0) + pnl

    return [pnl / INITIAL_CASH for pnl in sorted(daily.values())]


def sharpe_ratio(trades: List[Dict], annualize: bool = True) -> float:
    """Sharpe ratio = (mean return - risk free) / std(returns)."""
    rets = daily_returns(trades)
    if len(rets) < 2:
        return 0.0

    mean_ret = sum(rets) / len(rets)
    if mean_ret == 0:
        return 0.0

    variance = sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)
    std_ret = math.sqrt(variance)
    if std_ret == 0:
        return 0.0

    daily_rf = RISK_FREE_RATE / 365
    sharpe_daily = (mean_ret - daily_rf) / std_ret

    if annualize:
        return sharpe_daily * math.sqrt(365)
    return sharpe_daily


def sortino_ratio(trades: List[Dict], annualize: bool = True) -> float:
    """Sortino ratio — like Sharpe but only penalizes downside deviation."""
    rets = daily_returns(trades)
    if len(rets) < 2:
        return 0.0

    mean_ret = sum(rets) / len(rets)

    # Downside deviation: only negative returns
    downside = [min(0, r - 0) for r in rets]  # target = 0 (breakeven)
    if len(downside) < 2:
        return 0.0

    down_var = sum(d ** 2 for d in downside) / (len(downside) - 1)
    down_std = math.sqrt(down_var)
    if down_std == 0:
        return 0.0 if mean_ret <= 0 else 999.0  # infinite Sortino if no downside

    daily_rf = RISK_FREE_RATE / 365
    sortino_daily = (mean_ret - daily_rf) / down_std

    if annualize:
        return sortino_daily * math.sqrt(365)
    return sortino_daily


def max_drawdown(trades: List[Dict]) -> Tuple[float, Optional[str], Optional[str]]:
    """
    Maximum drawdown from peak equity.
    Returns (drawdown_pct, peak_date, trough_date).
    """
    if not trades:
        return (0.0, None, None)

    # Build equity curve from cumulative PnL
    equity = INITIAL_CASH
    peak = INITIAL_CASH
    max_dd = 0.0
    peak_date = None
    trough_date = None
    current_peak_date = trades[0].get("timestamp", "")[:10] if trades else None

    for t in trades:
        pnl = t.get("pnl", 0)
        equity += pnl

        if equity > peak:
            peak = equity
            current_peak_date = t.get("timestamp", "")[:10]

        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            peak_date = current_peak_date
            trough_date = t.get("timestamp", "")[:10]

    return (max_dd, peak_date, trough_date)


def value_at_risk(returns: List[float], confidence: float = 0.95) -> float:
    """
    Historical VaR: the loss at the given confidence level.
    Returns negative value (e.g., -0.05 = 5% loss at 95% confidence).
    """
    if not returns:
        return 0.0

    sorted_rets = sorted(returns)
    index = int(len(sorted_rets) * (1 - confidence))
    return sorted_rets[index] if index < len(sorted_rets) else sorted_rets[-1]


def win_rate(trades: List[Dict]) -> Tuple[float, int, int]:
    """Win rate: trades with positive PnL / total trades."""
    if not trades:
        return (0.0, 0, 0)

    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    losses = sum(1 for t in trades if t.get("pnl", 0) < 0)
    total = wins + losses
    if total == 0:
        return (0.0, wins, losses)

    return (wins / total, wins, losses)


def profit_factor(trades: List[Dict]) -> float:
    """Profit factor = gross profit / gross loss."""
    gross_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
    gross_loss = abs(sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0))

    if gross_loss == 0:
        return 999.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_win_loss(trades: List[Dict]) -> Tuple[float, float]:
    """Average win and average loss amounts."""
    wins = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0]
    losses = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0]

    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    return (avg_w, avg_l)


def calmar_ratio(trades: List[Dict]) -> float:
    """Calmar ratio = annualized return / max drawdown."""
    rets = daily_returns(trades)
    if len(rets) < 2:
        return 0.0

    mean_daily = sum(rets) / len(rets)
    annual_return = mean_daily * 365

    dd, _, _ = max_drawdown(trades)
    if dd == 0:
        return 999.0 if annual_return > 0 else 0.0

    return annual_return / dd


def total_pnl(trades: List[Dict]) -> Tuple[float, float]:
    """Total realized + current unrealized PnL."""
    realized = sum(t.get("pnl", 0) for t in trades)
    pf = load_portfolio()
    unrealized = pf.get("pnl", 0.0)  # paper_trader tracks cumulative PnL

    # Note: paper_trader's pnl already includes realized, so just return it
    total = pf.get("pnl", realized)
    return (round(total, 2), round(realized, 2))


# ── Full Report ────────────────────────────────────────────────

def compute_all() -> Dict:
    """
    Compute all portfolio statistics.

    Returns dict with all metrics. Suitable for dashboard display
    or Telegram reporting.
    """
    trades = load_trades()
    rets = compute_returns(trades)
    daily = daily_returns(trades)

    wr, wins, losses = win_rate(trades)
    dd, dd_peak, dd_trough = max_drawdown(trades)
    total_pnl_val, realized_pnl = total_pnl(trades)
    avg_w, avg_l = avg_win_loss(trades)

    return {
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr * 100, 1),
        "total_pnl": total_pnl_val,
        "realized_pnl": realized_pnl,
        "pnl_pct": round(total_pnl_val / INITIAL_CASH * 100, 2),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "avg_win_loss_ratio": round(abs(avg_w / avg_l), 2) if avg_l != 0 else 999,
        "profit_factor": round(profit_factor(trades), 2),
        "sharpe_ratio": round(sharpe_ratio(trades), 2),
        "sortino_ratio": round(sortino_ratio(trades), 2),
        "calmar_ratio": round(calmar_ratio(trades), 2),
        "max_drawdown_pct": round(dd * 100, 2),
        "max_drawdown_peak": dd_peak,
        "max_drawdown_trough": dd_trough,
        "var_95_daily": round(value_at_risk(daily, 0.95) * 100, 2),
        "var_99_daily": round(value_at_risk(daily, 0.99) * 100, 2),
        "daily_return_mean": round(sum(daily) / len(daily) * 100, 4) if daily else 0,
        "daily_return_std": round(
            math.sqrt(sum((r - (sum(daily) / len(daily))) ** 2 for r in daily) / (len(daily) - 1)) * 100, 4
        ) if len(daily) > 1 else 0,
        "best_day": round(max(daily) * 100, 2) if daily else 0,
        "worst_day": round(min(daily) * 100, 2) if daily else 0,
        "current_equity": round(INITIAL_CASH + total_pnl_val, 2),
        "risk_free_rate_pct": RISK_FREE_RATE * 100,
    }


def format_report(stats: Dict) -> str:
    """Format stats as a human-readable markdown report for Telegram."""
    # Color/emoji for key metrics
    sharpe_emoji = "🟢" if stats["sharpe_ratio"] > 1.0 else ("🟡" if stats["sharpe_ratio"] > 0 else "🔴")
    pnl_sign = "+" if stats["total_pnl"] >= 0 else ""
    dd_emoji = "🟢" if stats["max_drawdown_pct"] < 10 else ("🟡" if stats["max_drawdown_pct"] < 25 else "🔴")

    lines = [
        "📊 *Portfolio Statistics*",
        "",
        "*Performance:*",
        f"  Total P&L: ${stats['total_pnl']:+,.2f} ({stats['pnl_pct']:+.2f}%)",
        f"  Current Equity: ${stats['current_equity']:,.2f}",
        f"  Trades: {stats['total_trades']} (W: {stats['wins']} / L: {stats['losses']})",
        f"  Win Rate: {stats['win_rate']}%",
        f"  Profit Factor: {stats['profit_factor']}",
        "",
        "*Risk-Adjusted:*",
        f"  {sharpe_emoji} Sharpe: {stats['sharpe_ratio']}",
        f"  Sortino: {stats['sortino_ratio']}",
        f"  Calmar: {stats['calmar_ratio']}",
        "",
        "*Risk:*",
        f"  {dd_emoji} Max Drawdown: {stats['max_drawdown_pct']}%",
        f"  VaR 95% (daily): {stats['var_95_daily']}%",
        f"  VaR 99% (daily): {stats['var_99_daily']}%",
        "",
        "*Trade Quality:*",
        f"  Avg Win: ${stats['avg_win']:+,.2f}",
        f"  Avg Loss: ${stats['avg_loss']:+,.2f}",
        f"  Win/Loss Ratio: {stats['avg_win_loss_ratio']}",
        "",
        "*Daily Returns:*",
        f"  Mean: {stats['daily_return_mean']}%",
        f"  Std: {stats['daily_return_std']}%",
        f"  Best: {stats['best_day']:+.2f}%",
        f"  Worst: {stats['worst_day']:+.2f}%",
    ]

    if stats["max_drawdown_peak"]:
        lines.append(f"  DD: {stats['max_drawdown_peak']} → {stats['max_drawdown_trough']}")

    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio Stats — Risk & Performance Metrics")
    parser.add_argument("--full", action="store_true", help="Show full report")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--sharpe", action="store_true", help="Show Sharpe ratio only")
    args = parser.parse_args()

    stats = compute_all()

    if args.json:
        print(json.dumps(stats, indent=2))
    elif args.sharpe:
        print(f"Sharpe: {stats['sharpe_ratio']} | Sortino: {stats['sortino_ratio']} | Max DD: {stats['max_drawdown_pct']}%")
    elif args.full:
        print(format_report(stats))
    else:
        # Default: key metrics only
        print(f"Trades: {stats['total_trades']} | Win Rate: {stats['win_rate']}%")
        print(f"P&L: ${stats['total_pnl']:+,.2f} ({stats['pnl_pct']:+.2f}%)")
        print(f"Sharpe: {stats['sharpe_ratio']} | Sortino: {stats['sortino_ratio']}")
        print(f"Max DD: {stats['max_drawdown_pct']}% | Profit Factor: {stats['profit_factor']}")
        print(f"VaR 95%: {stats['var_95_daily']}% | VaR 99%: {stats['var_99_daily']}%")
