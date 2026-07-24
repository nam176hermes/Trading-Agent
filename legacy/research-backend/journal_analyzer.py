"""
journal_analyzer.py — Win/loss pattern analysis from trade history.

Reads trade_journal.jsonl and decisions.jsonl to find which TA conditions
predict wins vs losses. Outputs:
  - Learned rules appended to SOUL.md
  - Walk-forward summary in memory/backtest/walk_forward_combined.json
    (read by backtest_gate.py to decide whether to allow trades)

Run standalone:  python3 journal_analyzer.py
Called by:       trading_agent.py after every REFLECTION_TRIGGER_TRADES trades
"""

import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("journal_analyzer")

MEMORY_DIR = data_root() / "memory"
JOURNAL_FILE = MEMORY_DIR / "trade_journal.jsonl"
DECISIONS_FILE = MEMORY_DIR / "decisions.jsonl"
SOUL_FILE = Path(__file__).parent / "SOUL.md"
BACKTEST_DIR = MEMORY_DIR / "backtest"


# ── Data Loading ──────────────────────────────────────────────────────────────

def _load_journal() -> list[dict]:
    """Load all entries from trade_journal.jsonl."""
    if not JOURNAL_FILE.exists():
        return []
    entries = []
    with open(JOURNAL_FILE) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return entries


def _load_decisions() -> list[dict]:
    """Load all entries from decisions.jsonl."""
    if not DECISIONS_FILE.exists():
        return []
    entries = []
    with open(DECISIONS_FILE) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return entries


# ── Core Analysis ─────────────────────────────────────────────────────────────

def analyze_trades(journal: list[dict]) -> dict:
    """
    Compute win/loss stats from trade journal SELL entries (realized PnL).
    Returns a stats dict.
    """
    sells = [t for t in journal if t.get("side", "").upper() == "SELL"
             and t.get("pnl") is not None]

    if not sells:
        return {"total_trades": 0}

    pnls = [float(t["pnl"]) for t in sells]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) if pnls else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    total_pnl = sum(pnls)

    # Max drawdown from PnL series
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Sharpe — simplified: mean / std of per-trade PnL (not annualized)
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        sharpe = (mean / std) if std > 0 else 0.0
    else:
        sharpe = 0.0

    # By symbol
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for t in sells:
        sym = t.get("symbol", "?").upper()
        by_symbol[sym].append(float(t["pnl"]))

    symbol_stats = {}
    for sym, sym_pnls in by_symbol.items():
        sym_wins = [p for p in sym_pnls if p > 0]
        symbol_stats[sym] = {
            "trades": len(sym_pnls),
            "win_rate": len(sym_wins) / len(sym_pnls) if sym_pnls else 0.0,
            "total_pnl": sum(sym_pnls),
        }

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_pnl": total_pnl,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "by_symbol": symbol_stats,
    }


def analyze_signal_quality(decisions: list[dict]) -> dict:
    """
    Analyze which TA conditions appear on BUY signals vs historical outcomes.
    Since we don't have guaranteed pnl for every decision, we bucket by
    TA conditions and summarize signal distribution.
    """
    buy_decisions = [d for d in decisions if d.get("suggestion", "").upper() in ("BUY", "STRONG BUY")]
    sell_decisions = [d for d in decisions if d.get("suggestion", "").upper() in ("SELL", "STRONG SELL")]

    def _bucket_rsi(signals: dict) -> str:
        rsi = signals.get("rsi_14") or signals.get("rsi")
        if rsi is None:
            return "unknown"
        rsi = float(rsi)
        if rsi < 30:
            return "oversold(<30)"
        elif rsi < 50:
            return "bearish(30-50)"
        elif rsi < 70:
            return "bullish(50-70)"
        else:
            return "overbought(>70)"

    def _bucket_adx(signals: dict) -> str:
        adx = signals.get("adx_14")
        if adx is None:
            return "unknown"
        adx = float(adx)
        if adx < 20:
            return "ranging(<20)"
        elif adx < 40:
            return "trending(20-40)"
        else:
            return "strong(>40)"

    # RSI distribution on BUY signals
    rsi_buckets: dict[str, int] = defaultdict(int)
    adx_buckets: dict[str, int] = defaultdict(int)
    ranging_buys = 0

    for d in buy_decisions:
        sig = d.get("signals", {}) or {}
        rsi_buckets[_bucket_rsi(sig)] += 1
        adx_b = _bucket_adx(sig)
        adx_buckets[adx_b] += 1
        if adx_b == "ranging(<20)":
            ranging_buys += 1

    return {
        "total_buy_signals": len(buy_decisions),
        "total_sell_signals": len(sell_decisions),
        "rsi_on_buys": dict(rsi_buckets),
        "adx_on_buys": dict(adx_buckets),
        "ranging_market_buys": ranging_buys,
        "avg_confidence_buys": (
            sum(float(d.get("confidence", 0) or 0) for d in buy_decisions) / len(buy_decisions)
            if buy_decisions else 0.0
        ),
    }


# ── SOUL.md Update ────────────────────────────────────────────────────────────

def _build_learned_rules(trade_stats: dict, signal_stats: dict) -> str:
    """Generate a concise learned-rules block from trade and signal statistics."""
    lines = [
        f"\n## Learned Rules (Auto-generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')})\n",
        f"*Updated automatically after every 5 completed trades.*\n",
    ]

    n = trade_stats.get("total_trades", 0)
    if n == 0:
        lines.append("No completed trades yet — no rules generated.\n")
        return "".join(lines)

    wr = trade_stats.get("win_rate", 0)
    pf = trade_stats.get("profit_factor", 0)
    sharpe = trade_stats.get("sharpe", 0)
    total_pnl = trade_stats.get("total_pnl", 0)
    avg_win = trade_stats.get("avg_win", 0)
    avg_loss = trade_stats.get("avg_loss", 0)

    lines.append(f"### Performance Summary ({n} completed trades)\n")
    lines.append(f"- Win rate: **{wr:.0%}** | Profit factor: **{pf:.2f}** | Sharpe: **{sharpe:.2f}**\n")
    lines.append(f"- Total realized P&L: **${total_pnl:+.2f}**\n")
    lines.append(f"- Avg win: ${avg_win:+.2f} | Avg loss: ${avg_loss:+.2f}\n")
    if avg_loss < 0 and avg_win > 0:
        rr = abs(avg_win / avg_loss)
        lines.append(f"- Reward/risk ratio: **{rr:.1f}:1**\n")

    # Symbol breakdown
    by_sym = trade_stats.get("by_symbol", {})
    if by_sym:
        lines.append("\n### By Symbol\n")
        sorted_syms = sorted(by_sym.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
        for sym, s in sorted_syms:
            flag = "✅" if s["total_pnl"] > 0 else "❌"
            lines.append(
                f"- {flag} **{sym}**: {s['trades']} trades | "
                f"win rate {s['win_rate']:.0%} | P&L ${s['total_pnl']:+.2f}\n"
            )

    # Signal quality insights
    lines.append("\n### Signal Quality Insights\n")

    rsi_buys = signal_stats.get("rsi_on_buys", {})
    if rsi_buys:
        lines.append(f"- RSI on BUY signals: {dict(rsi_buys)}\n")
        bearish_rsi_buys = rsi_buys.get("bearish(30-50)", 0)
        total_buys = signal_stats.get("total_buy_signals", 1)
        if bearish_rsi_buys / total_buys > 0.3:
            lines.append(
                f"  ⚠️ **{bearish_rsi_buys}/{total_buys} BUY signals had bearish RSI (30-50)** — "
                f"consider raising RSI floor for entries\n"
            )

    ranging_buys = signal_stats.get("ranging_market_buys", 0)
    if ranging_buys > 0:
        lines.append(
            f"- **{ranging_buys} BUY signals were in ranging markets (ADX<20)** — "
            f"these are noise; ADX gate now blocking them\n"
        )

    # Actionable rules from performance
    lines.append("\n### Rules Reinforced by Data\n")
    if wr < 0.40:
        lines.append(
            f"- ❌ Win rate {wr:.0%} below 40% — **increase selectivity**: raise confidence "
            f"threshold or require stronger MTF confirmation before BUY\n"
        )
    elif wr >= 0.50:
        lines.append(f"- ✅ Win rate {wr:.0%} — signal quality gates are working\n")

    if pf < 1.0:
        lines.append(
            f"- ❌ Profit factor {pf:.2f} < 1 — **losses exceed gains in dollar terms**: "
            f"widen take-profit targets or tighten stop-losses\n"
        )
    elif pf >= 1.5:
        lines.append(f"- ✅ Profit factor {pf:.2f} — position sizing and exits are effective\n")

    if avg_loss < -200:
        lines.append(
            f"- ❌ Average loss ${avg_loss:.0f} — **stops are too wide**: "
            f"consider reducing stop_multiplier in ATR sizing from 2.0× to 1.5×\n"
        )

    if trade_stats.get("max_drawdown", 0) > 1000:
        lines.append(
            f"- ⚠️ Max drawdown ${trade_stats['max_drawdown']:.0f} — "
            f"**reduce correlation group exposure or MAX_PER_TRADE_PCT**\n"
        )

    return "".join(lines)


def update_soul_md(trade_stats: dict, signal_stats: dict):
    """Append or replace the learned rules section in SOUL.md."""
    if not SOUL_FILE.exists():
        log.warning("SOUL.md not found at %s — skipping update", SOUL_FILE)
        return

    current = SOUL_FILE.read_text()

    # Strip existing auto-generated section
    marker = "\n## Learned Rules (Auto-generated"
    if marker in current:
        current = current[:current.index(marker)]

    new_rules = _build_learned_rules(trade_stats, signal_stats)
    SOUL_FILE.write_text(current.rstrip() + "\n" + new_rules)
    log.info("SOUL.md updated with learned rules (%d trades)", trade_stats.get("total_trades", 0))


# ── Backtest Gate Output ──────────────────────────────────────────────────────

def write_walk_forward_summary(trade_stats: dict):
    """
    Write a walk-forward summary JSON that backtest_gate.py can read.
    Format matches what GateChecker._extract_metrics() expects.
    """
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)

    n = trade_stats.get("total_trades", 0)
    sharpe = trade_stats.get("sharpe", 0.0)
    win_rate = trade_stats.get("win_rate", 0.0)
    total_pnl = trade_stats.get("total_pnl", 0.0)
    max_dd = trade_stats.get("max_drawdown", 0.0)

    # Build in single-symbol format (aggregate + windows stub)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "journal_analyzer",
        "total_completed_trades": n,
        "aggregate": {
            "sharpe": round(sharpe, 3),
            "avg_win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "consistency": round(win_rate, 4),
            "total_trades": n,
        },
        "windows": [
            {
                "label": "all_time",
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(win_rate, 4),
                "trades": n,
            }
        ],
        "by_symbol": trade_stats.get("by_symbol", {}),
    }

    # Use dated filename so _find_latest_walk_forward() always picks the newest
    datestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = BACKTEST_DIR / f"walk_forward_combined_{datestamp}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    log.info("Walk-forward summary written → %s (Sharpe=%.2f WinRate=%.0f%%)",
             out_path.name, sharpe, win_rate * 100)
    return summary


# ── Reflection Log ────────────────────────────────────────────────────────────

def store_analysis_log(trade_stats: dict, signal_stats: dict):
    """Append a compact analysis snapshot to memory/reflections.jsonl."""
    from memory import REFLECTIONS_FILE, MEMORY_DIR as _MEM_DIR
    _MEM_DIR.mkdir(exist_ok=True)

    entry = {
        "type": "journal_analysis",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trade_stats": {k: v for k, v in trade_stats.items() if k != "by_symbol"},
        "signal_stats": signal_stats,
    }
    with open(REFLECTIONS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("Analysis snapshot stored in reflections.jsonl")


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_analysis(update_soul: bool = True) -> dict:
    """
    Full analysis pipeline. Returns stats dict.
    Called by trading_agent.py after every N trades.
    """
    log.info("Journal analysis started")

    journal = _load_journal()
    decisions = _load_decisions()

    trade_stats = analyze_trades(journal)
    signal_stats = analyze_signal_quality(decisions)

    n = trade_stats.get("total_trades", 0)
    log.info("Analyzed %d completed trades, %d decisions",
             n, len(decisions))

    if n > 0:
        wf_summary = write_walk_forward_summary(trade_stats)
        if update_soul:
            update_soul_md(trade_stats, signal_stats)
        store_analysis_log(trade_stats, signal_stats)
        log.info(
            "Analysis complete: WinRate=%.0f%% PF=%.2f Sharpe=%.2f PnL=$%+.2f",
            trade_stats["win_rate"] * 100,
            trade_stats["profit_factor"],
            trade_stats["sharpe"],
            trade_stats["total_pnl"],
        )
    else:
        log.info("No completed trades yet — skipping SOUL.md and gate updates")

    return {**trade_stats, "signal_stats": signal_stats}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    import argparse
    parser = argparse.ArgumentParser(description="Trade journal analyzer")
    parser.add_argument("--no-soul", action="store_true", help="Skip SOUL.md update")
    args = parser.parse_args()

    result = run_analysis(update_soul=not args.no_soul)

    print("\n── Journal Analysis ──────────────────────────────────")
    print(f"  Completed trades : {result.get('total_trades', 0)}")
    print(f"  Win rate         : {result.get('win_rate', 0):.0%}")
    print(f"  Profit factor    : {result.get('profit_factor', 0):.2f}")
    print(f"  Sharpe           : {result.get('sharpe', 0):.2f}")
    print(f"  Total P&L        : ${result.get('total_pnl', 0):+.2f}")
    print(f"  Max drawdown     : ${result.get('max_drawdown', 0):.2f}")
    sig = result.get("signal_stats", {})
    print(f"  BUY signals      : {sig.get('total_buy_signals', 0)}")
    print(f"  Ranging-mkt BUYs : {sig.get('ranging_market_buys', 0)}")
    by_sym = result.get("by_symbol", {})
    if by_sym:
        print("\n  By symbol:")
        for sym, s in sorted(by_sym.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
            print(f"    {sym:6s}  {s['trades']} trades  wr={s['win_rate']:.0%}  "
                  f"pnl=${s['total_pnl']:+.2f}")
