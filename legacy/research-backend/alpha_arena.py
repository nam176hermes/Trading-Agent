"""
alpha_arena.py — LLM Agent Competition Engine

Pits multiple analyst agents against each other on the same market data.
Each analyst gets identical inputs, produces independent trading signals,
and their paper-traded PnL is tracked on a leaderboard.

Inspired by FinceptTerminal's Alpha Arena.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from db.repository import get_db, transaction

log = logging.getLogger("alpha_arena")

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL"]
DEFAULT_CAPITAL = 100_000.0
ANALYST_NAMES = ["technical", "sentiment", "onchain", "macro"]


# ── Competition Management ─────────────────────────────────────

def create_competition(name: str, config: Optional[dict] = None) -> int:
    """Start a new competition. Returns competition_id."""
    db = get_db()
    db.execute(
        """INSERT INTO arena_competitions (name, config_json)
           VALUES (?, ?)""",
        (name, json.dumps(config or {})),
    )
    db.commit()
    cid = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Initialize portfolio for each analyst
    for analyst in ANALYST_NAMES:
        db.execute(
            """INSERT OR IGNORE INTO arena_portfolios
               (competition_id, analyst, cash, equity)
               VALUES (?, ?, ?, ?)""",
            (cid, analyst, DEFAULT_CAPITAL, DEFAULT_CAPITAL),
        )
    db.commit()
    log.info("Competition #%d '%s' created with %d analysts", cid, name, len(ANALYST_NAMES))
    return cid


def end_competition(competition_id: int):
    """Mark a competition as completed."""
    db = get_db()
    db.execute(
        "UPDATE arena_competitions SET status='completed', ended_at=datetime('now') WHERE id=?",
        (competition_id,),
    )
    db.commit()


# ── Round Execution ────────────────────────────────────────────

def start_round(competition_id: int, symbol: str, price: float) -> int:
    """Create a new round. Returns round_id."""
    db = get_db()
    try:
        db.execute(
            """INSERT INTO arena_rounds (competition_id, symbol, round_number, price)
               SELECT ?, ?, COALESCE(MAX(round_number), 0) + 1, ?
               FROM arena_rounds
               WHERE competition_id = ? AND symbol = ?""",
            (competition_id, symbol, price, competition_id, symbol),
        )
        db.commit()
        round_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        log.debug("Round #%d: %s @ $%.2f", round_id, symbol, price)
        return round_id
    except Exception:
        # Race condition — round already exists for this (competition, symbol, round_number)
        row = db.execute(
            "SELECT id FROM arena_rounds WHERE competition_id=? AND symbol=? ORDER BY round_number DESC LIMIT 1",
            (competition_id, symbol),
        ).fetchone()
        return row[0] if row else 0


def record_signal(
    round_id: int,
    analyst: str,
    action: str,
    confidence: float,
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    reasoning: str = "",
) -> int:
    """Record an analyst's signal for a round."""
    db = get_db()
    db.execute(
        """INSERT INTO arena_signals (round_id, analyst, action, confidence,
           entry_price, stop_loss, take_profit, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (round_id, analyst, action, confidence, entry_price, stop_loss, take_profit, reasoning),
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── PnL Tracking ───────────────────────────────────────────────

def update_portfolio_pnl(
    competition_id: int,
    analyst: str,
    signal_action: str,
    entry_price: float,
    current_price: float,
):
    """
    Update paper PnL for an analyst based on their signal.

    Simple model:
    - BUY → track hypothetical long from entry to current price
    - SELL → track hypothetical short
    - HOLD/WATCH → no change

    We use 5% position size of equity per trade, 0.1% fee.
    """
    POSITION_SIZE_PCT = 0.05
    FEE_RATE = 0.001

    db = get_db()
    row = db.execute(
        "SELECT cash, equity, pnl, trades, win_rate FROM arena_portfolios WHERE competition_id=? AND analyst=?",
        (competition_id, analyst),
    ).fetchone()

    if not row:
        return

    cash, equity, current_pnl, trades, wr = row
    signal_action = signal_action.upper()

    if signal_action in ("HOLD", "WATCH") or not entry_price:
        return

    position_value = equity * POSITION_SIZE_PCT
    fee = position_value * FEE_RATE

    if signal_action == "BUY":
        # Long: pnl = (current/entry - 1) * position
        trade_pnl = position_value * ((current_price / entry_price) - 1) - fee
    elif signal_action == "SELL":
        # Short: pnl = (1 - current/entry) * position
        trade_pnl = position_value * (1 - (current_price / entry_price)) - fee
    else:
        return

    new_pnl = current_pnl + trade_pnl
    new_equity = DEFAULT_CAPITAL + new_pnl
    new_trades = trades + 1

    # Rolling win rate
    win = 1 if trade_pnl > 0 else 0
    new_win_rate = ((wr or 0) * trades + win) / new_trades if new_trades > 0 else 0

    db.execute(
        """UPDATE arena_portfolios
           SET pnl=?, equity=?, trades=?, win_rate=?
           WHERE competition_id=? AND analyst=?""",
        (new_pnl, new_equity, new_trades, new_win_rate, competition_id, analyst),
    )
    db.commit()


# ── Leaderboard ────────────────────────────────────────────────

def get_leaderboard(competition_id: int) -> List[Dict]:
    """Return analysts ranked by PnL."""
    db = get_db()
    rows = db.execute(
        """SELECT analyst, equity, pnl, trades, win_rate, sharpe, max_drawdown
           FROM arena_portfolios
           WHERE competition_id = ?
           ORDER BY pnl DESC""",
        (competition_id,),
    ).fetchall()

    return [
        {
            "rank": i + 1,
            "analyst": r["analyst"],
            "equity": round(r["equity"], 2),
            "pnl": round(r["pnl"], 2),
            "pnl_pct": round(r["pnl"] / DEFAULT_CAPITAL * 100, 2),
            "trades": r["trades"],
            "win_rate": round(r["win_rate"] * 100, 1) if r["win_rate"] else 0,
            "sharpe": round(r["sharpe"], 2) if r["sharpe"] else 0,
            "max_drawdown": round(r["max_drawdown"] * 100, 2) if r["max_drawdown"] else 0,
        }
        for i, r in enumerate(rows)
    ]


def update_arena_stats(competition_id: int):
    """
    Compute Sharpe and max drawdown for each analyst from arena_signals,
    and update the arena_portfolios table. Call after each round.
    """
    import math

    db = get_db()
    analysts = [r[0] for r in db.execute(
        "SELECT analyst FROM arena_portfolios WHERE competition_id=?", (competition_id,)
    ).fetchall()]

    for analyst in analysts:
        # Get signal PnL history from arena_signals
        rows = db.execute(
            """SELECT s.pnl_impact, r.created_at
               FROM arena_signals s
               JOIN arena_rounds r ON s.round_id = r.id
               WHERE r.competition_id = ? AND s.analyst = ?
               ORDER BY r.created_at""",
            (competition_id, analyst),
        ).fetchall()

        returns = [r["pnl_impact"] / DEFAULT_CAPITAL for r in rows]

        # Sharpe ratio
        if len(returns) >= 2:
            mean_ret = sum(returns) / len(returns)
            variance = sum((x - mean_ret) ** 2 for x in returns) / (len(returns) - 1)
            std_ret = math.sqrt(variance)
            sharpe = (mean_ret / std_ret) * math.sqrt(365) if std_ret > 0 else 0
        else:
            sharpe = 0

        # Max drawdown from cumulative PnL
        cumulative = DEFAULT_CAPITAL
        peak = DEFAULT_CAPITAL
        max_dd = 0.0
        for r in rows:
            cumulative += r["pnl_impact"]
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        db.execute(
            "UPDATE arena_portfolios SET sharpe=?, max_drawdown=? WHERE competition_id=? AND analyst=?",
            (round(sharpe, 4), round(max_dd, 4), competition_id, analyst),
        )
    db.commit()


def format_leaderboard(competition_id: int) -> str:
    """Pretty-print leaderboard as text (for Telegram/CLI)."""
    lb = get_leaderboard(competition_id)
    if not lb:
        return "No data yet."

    lines = ["🏆 *Alpha Arena Leaderboard*", ""]
    for entry in lb:
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(entry["rank"], f"#{entry['rank']}")
        sharpe_str = f"Sharpe: {entry['sharpe']}" if entry.get('sharpe') else ""
        dd_str = f"DD: {entry['max_drawdown']}%" if entry.get('max_drawdown') else ""
        extras = " | ".join(filter(None, [sharpe_str, dd_str]))
        lines.append(
            f"{medal} *{entry['analyst'].title()}* — "
            f"${entry['pnl']:+,.0f} ({entry['pnl_pct']:+.1f}%) | "
            f"Win: {entry['win_rate']:.0f}% | "
            f"Trades: {entry['trades']}"
        )
        if extras:
            lines.append(f"   {extras}")
    return "\n".join(lines)


# ── Competition Status ─────────────────────────────────────────

def get_active_competitions() -> List[Dict]:
    """Get all active competitions."""
    db = get_db()
    return [
        dict(r)
        for r in db.execute(
            "SELECT * FROM arena_competitions WHERE status='active' ORDER BY started_at DESC"
        ).fetchall()
    ]


def get_round_history(competition_id: int, limit: int = 20) -> List[Dict]:
    """Get recent rounds with all analyst signals."""
    db = get_db()
    rows = db.execute(
        """SELECT r.round_number, r.symbol, r.price, r.created_at,
                  s.analyst, s.action, s.confidence, s.entry_price
           FROM arena_rounds r
           JOIN arena_signals s ON s.round_id = r.id
           WHERE r.competition_id = ?
           ORDER BY r.round_number DESC, s.analyst
           LIMIT ?""",
        (competition_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Alpha Arena — LLM Agent Competition")
    parser.add_argument("--new", type=str, help="Create a new competition (give it a name)")
    parser.add_argument("--leaderboard", type=int, help="Show leaderboard for competition ID")
    parser.add_argument("--end", type=int, help="End competition by ID")
    parser.add_argument("--list", action="store_true", help="List active competitions")

    args = parser.parse_args()

    from db.repository import get_db

    # Force schema init
    get_db()

    if args.new:
        cid = create_competition(args.new)
        print(f"✅ Competition #{cid} '{args.new}' created")
        print(f"   Analysts: {', '.join(ANALYST_NAMES)}")
        print(f"   Capital: ${DEFAULT_CAPITAL:,.0f} each")

    elif args.leaderboard:
        print(format_leaderboard(args.leaderboard))

    elif args.end:
        end_competition(args.end)
        print(f"✅ Competition #{args.end} ended")

    elif args.list:
        active = get_active_competitions()
        if not active:
            print("No active competitions.")
        for c in active:
            print(f"  #{c['id']} — {c['name']} (started {c['started_at']})")

    else:
        parser.print_help()
