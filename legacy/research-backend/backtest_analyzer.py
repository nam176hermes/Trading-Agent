"""
backtest_analyzer.py
--------------------
Reads signal records from backtest_engine.py and produces a
full performance report.

Measures:
  - Win rate overall + by symbol, regime, confidence, signal type
  - Average forward returns (1d, 3d, 7d, 14d) per signal type
  - Max drawdown per symbol
  - False positive rate (NO SIGNAL days that were actually good entries)
  - Regime suppression effectiveness (did suppressed signals save losses?)
  - Derivatives signal accuracy (did squeeze_risk_caution actually precede drops?)
  - Confidence calibration (does "high" outperform "low"?)

Output:
  backtest_results/performance_report.json  — full machine-readable report
  backtest_results/performance_summary.txt  — human-readable summary

Usage:
  python backtest_analyzer.py                    # analyzes all symbols
  python backtest_analyzer.py --symbol BTC       # single symbol
  python backtest_analyzer.py --start 2022-01-01 --end 2023-12-31  # range filter
"""

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("backtest_analyzer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

RESULTS_DIR = data_root() / "backtest_results"
WATCHLIST   = ["BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "LINK",
               "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]

DIRECTIONAL_SIGNALS = {"WATCH FOR ENTRY", "CAUTIOUS ENTRY", "WATCH FOR EXIT", "CAUTIOUS EXIT"}
LONG_SIGNALS        = {"WATCH FOR ENTRY", "CAUTIOUS ENTRY"}
SHORT_SIGNALS       = {"WATCH FOR EXIT",  "CAUTIOUS EXIT"}


# ── Data loader ───────────────────────────────────────────────────────────────

def load_signals(
    symbols:    list[str],
    start_date: Optional[str] = None,
    end_date:   Optional[str] = None,
) -> list[dict]:
    """Loads and merges signal records from all symbol files in backtest_results/."""
    all_signals = []

    for symbol in symbols:
        # Find matching files for this symbol
        files = sorted(RESULTS_DIR.glob(f"signals_{symbol}_*.json"))
        if not files:
            log.warning("No backtest file found for %s in %s", symbol, RESULTS_DIR)
            continue

        for path in files:
            with open(path) as f:
                records = json.load(f)

            # Optional date filter
            if start_date or end_date:
                records = [
                    r for r in records
                    if (not start_date or r["date"] >= start_date) and
                       (not end_date   or r["date"] <= end_date)
                ]

            all_signals.extend(records)
            log.info("Loaded %d records from %s", len(records), path.name)

    log.info("Total records loaded: %d", len(all_signals))
    return all_signals


# ── Core metric calculators ───────────────────────────────────────────────────

def win_rate(records: list[dict]) -> dict:
    """
    Calculates win/loss/open rates for directional signals only.
    Excludes NO SIGNAL records — they have no outcome to measure.
    """
    outcomes = [r["outcome"] for r in records
                if r.get("suggestion") in DIRECTIONAL_SIGNALS
                and r.get("outcome") in ("WIN", "LOSS", "OPEN")]

    if not outcomes:
        return {"win": None, "loss": None, "open": None, "total": 0}

    total = len(outcomes)
    wins  = outcomes.count("WIN")
    losses= outcomes.count("LOSS")
    opens = outcomes.count("OPEN")

    return {
        "win":   round(wins  / total * 100, 1),
        "loss":  round(losses/ total * 100, 1),
        "open":  round(opens / total * 100, 1),
        "total": total,
        "wins":  wins,
        "losses":losses,
    }


def avg_forward_returns(records: list[dict], signal_filter=None) -> dict:
    """Average forward returns for 1d, 3d, 7d, 14d windows."""
    filtered = records
    if signal_filter:
        filtered = [r for r in records if r.get("suggestion") in signal_filter]

    result = {}
    for window in [1, 3, 7, 14]:
        key = f"return_{window}d_pct"
        values = [r[key] for r in filtered if r.get(key) is not None]

        # For short signals, invert the return (price going down is a win)
        adjusted = []
        for r in filtered:
            val = r.get(key)
            if val is None:
                continue
            if r.get("suggestion") in SHORT_SIGNALS:
                adjusted.append(-val)
            else:
                adjusted.append(val)

        result[f"avg_{window}d_pct"] = (
            round(sum(adjusted) / len(adjusted), 3) if adjusted else None
        )
        result[f"n_{window}d"] = len(adjusted)

    return result


def max_drawdown(records: list[dict], symbol: str) -> Optional[float]:
    """
    Calculates max drawdown from a sequence of daily closes.
    Uses the entry_price series as a proxy for portfolio NAV.
    """
    symbol_records = [r for r in records if r["symbol"] == symbol]
    if not symbol_records:
        return None

    prices = [r["entry_price"] for r in sorted(symbol_records, key=lambda x: x["date"])]
    if not prices:
        return None

    peak = prices[0]
    max_dd = 0.0
    for price in prices:
        if price > peak:
            peak = price
        dd = (peak - price) / peak * 100
        max_dd = max(max_dd, dd)

    return round(max_dd, 2)


def signal_accuracy_by_group(records: list[dict], group_key: str) -> dict:
    """
    Breaks down win rate by a grouping key (regime, confidence, symbol, etc.).
    Only counts directional signals with resolved outcomes (WIN/LOSS).
    """
    groups = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})

    for r in records:
        if r.get("suggestion") not in DIRECTIONAL_SIGNALS:
            continue
        if r.get("outcome") not in ("WIN", "LOSS"):
            continue

        group = r.get(group_key, "unknown") or "unknown"
        groups[group]["total"] += 1
        if r["outcome"] == "WIN":
            groups[group]["wins"] += 1
        else:
            groups[group]["losses"] += 1

    result = {}
    for group, counts in sorted(groups.items()):
        total = counts["total"]
        if total > 0:
            result[str(group)] = {
                "win_rate": round(counts["wins"] / total * 100, 1),
                "wins":     counts["wins"],
                "losses":   counts["losses"],
                "total":    total,
            }
    return result


def regime_suppression_analysis(records: list[dict]) -> dict:
    """
    Measures how effective regime suppression was.

    Compares:
      A) Forward returns on days where a signal WAS generated in a choppy regime
         (these shouldn't exist if suppression worked — flags if they do)
      B) Average 7d returns during choppy regime days overall
         (to verify choppy = bad entry environment)
    """
    choppy_days          = [r for r in records if r.get("regime") == "choppy"]
    choppy_with_signal   = [r for r in choppy_days
                            if r.get("suggestion") in DIRECTIONAL_SIGNALS]
    choppy_no_signal     = [r for r in choppy_days
                            if r.get("suggestion") == "NO SIGNAL"]

    def avg_return_7d(recs):
        vals = [r.get("return_7d_pct") for r in recs if r.get("return_7d_pct") is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "choppy_days_total":        len(choppy_days),
        "choppy_days_with_signal":  len(choppy_with_signal),
        "choppy_days_no_signal":    len(choppy_no_signal),
        "avg_7d_return_when_choppy_signal":    avg_return_7d(choppy_with_signal),
        "avg_7d_return_when_choppy_no_signal": avg_return_7d(choppy_no_signal),
        "suppression_note": (
            "If avg_7d_return_when_choppy_signal is near 0 or negative, "
            "regime suppression correctly avoided bad entries."
        ),
    }


def derivatives_accuracy(records: list[dict]) -> dict:
    """
    Measures whether derivatives signals predicted outcomes.

    squeeze_risk_caution  → expects price to DROP → 7d return should be negative
    long_squeeze_risk     → expects price to RISE  → 7d return should be positive
    strong_long_setup     → expects price to RISE
    neutral               → expects no strong directional move
    """
    signal_types = [
        "squeeze_risk_caution",
        "long_squeeze_risk",
        "strong_long_setup",
        "strong_short_setup",
        "neutral",
        "mixed",
    ]

    result = {}
    for sig in signal_types:
        recs = [r for r in records if r.get("derivatives_signal") == sig]
        if not recs:
            continue

        returns_7d = [r["return_7d_pct"] for r in recs
                      if r.get("return_7d_pct") is not None]
        avg_ret = round(sum(returns_7d) / len(returns_7d), 3) if returns_7d else None

        result[sig] = {
            "count":        len(recs),
            "avg_7d_return": avg_ret,
            "directional_correct": None,  # filled below
        }

        # Did the signal predict correctly?
        if avg_ret is not None:
            if sig in ("squeeze_risk_caution", "strong_short_setup"):
                result[sig]["directional_correct"] = avg_ret < 0
            elif sig in ("long_squeeze_risk", "strong_long_setup"):
                result[sig]["directional_correct"] = avg_ret > 0

    return result


def conflict_analysis(records: list[dict]) -> dict:
    """
    Validates the conflict detection rule.
    When signal_conflict=True and suggestion=NO SIGNAL, was that the right call?
    Checks what the 7d return was on conflict days — should be near zero or mixed.
    """
    conflict_days    = [r for r in records if r.get("signal_conflict") is True]
    no_conflict_days = [r for r in records if r.get("signal_conflict") is False
                        and r.get("suggestion") in DIRECTIONAL_SIGNALS]

    def avg_abs_return(recs):
        vals = [abs(r["return_7d_pct"]) for r in recs
                if r.get("return_7d_pct") is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    def avg_return(recs):
        vals = [r["return_7d_pct"] for r in recs
                if r.get("return_7d_pct") is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "conflict_days_total":         len(conflict_days),
        "avg_abs_7d_return_conflict":  avg_abs_return(conflict_days),
        "avg_7d_return_conflict":      avg_return(conflict_days),
        "avg_7d_return_no_conflict":   avg_return(no_conflict_days),
        "note": (
            "If avg_abs_7d_return_conflict is lower than no_conflict, "
            "NO SIGNAL on conflict days was the right call."
        ),
    }


def signal_frequency(records: list[dict]) -> dict:
    """How often each suggestion appeared."""
    counts = defaultdict(int)
    for r in records:
        counts[r.get("suggestion", "unknown")] += 1
    total = len(records)
    return {
        sig: {
            "count":   cnt,
            "pct":     round(cnt / total * 100, 1) if total else 0,
        }
        for sig, cnt in sorted(counts.items(), key=lambda x: -x[1])
    }


# ── Report assembly ───────────────────────────────────────────────────────────

def build_report(
    records:    list[dict],
    symbols:    list[str],
    start_date: Optional[str],
    end_date:   Optional[str],
) -> dict:
    """Assembles the full performance report dict."""

    directional = [r for r in records if r.get("suggestion") in DIRECTIONAL_SIGNALS]
    resolved    = [r for r in directional if r.get("outcome") in ("WIN", "LOSS")]

    report = {
        "meta": {
            "symbols":      symbols,
            "start_date":   start_date or "all",
            "end_date":     end_date or "all",
            "total_records": len(records),
            "directional_signals": len(directional),
            "resolved_outcomes":   len(resolved),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },

        # ── Overall win rate ──
        "overall_win_rate": win_rate(records),

        # ── Forward returns by signal direction ──
        "forward_returns": {
            "all_directional":    avg_forward_returns(records, DIRECTIONAL_SIGNALS),
            "long_signals":       avg_forward_returns(records, LONG_SIGNALS),
            "short_signals":      avg_forward_returns(records, SHORT_SIGNALS),
            "high_confidence":    avg_forward_returns(
                [r for r in records if r.get("confidence") == "high"],
                DIRECTIONAL_SIGNALS
            ),
            "low_confidence":     avg_forward_returns(
                [r for r in records if r.get("confidence") == "low"],
                DIRECTIONAL_SIGNALS
            ),
        },

        # ── Breakdown by grouping ──
        "win_rate_by_symbol":     signal_accuracy_by_group(records, "symbol"),
        "win_rate_by_regime":     signal_accuracy_by_group(records, "regime"),
        "win_rate_by_confidence": signal_accuracy_by_group(records, "confidence"),
        "win_rate_by_signal":     signal_accuracy_by_group(records, "suggestion"),

        # ── Drawdown ──
        "max_drawdown_by_symbol": {
            sym: max_drawdown(records, sym) for sym in symbols
        },

        # ── Signal frequency ──
        "signal_frequency": signal_frequency(records),

        # ── Regime suppression ──
        "regime_suppression": regime_suppression_analysis(records),

        # ── Derivatives accuracy ──
        "derivatives_accuracy": derivatives_accuracy(records),

        # ── Conflict detection ──
        "conflict_analysis": conflict_analysis(records),

        # ── Go/No-Go thresholds check ──
        "go_nogo_check": _go_nogo_check(records),
    }

    return report


def _go_nogo_check(records: list[dict]) -> dict:
    """
    Checks whether the backtest results meet Step 6 Go/No-Go thresholds.
    These are the same thresholds as step6_paper_trading.md.
    """
    wr = win_rate(records)
    high_conf = [r for r in records
                 if r.get("confidence") == "high"
                 and r.get("suggestion") in DIRECTIONAL_SIGNALS]
    high_conf_wr = win_rate(high_conf)

    conflict_respected = all(
        r.get("suggestion") == "NO SIGNAL"
        for r in records
        if r.get("signal_conflict") is True
    )

    overall_pass  = wr["win"] is not None and wr["win"] >= 55.0
    highconf_pass = high_conf_wr["win"] is not None and high_conf_wr["win"] >= 65.0

    return {
        "signal_accuracy_55pct":  {
            "threshold": "≥55%",
            "actual":    f"{wr['win']}%" if wr["win"] is not None else "N/A",
            "pass":      overall_pass,
        },
        "high_confidence_65pct":  {
            "threshold": "≥65%",
            "actual":    f"{high_conf_wr['win']}%" if high_conf_wr["win"] is not None else "N/A",
            "pass":      highconf_pass,
        },
        "conflict_100pct_respected": {
            "threshold": "100%",
            "actual":    "100%" if conflict_respected else "FAILED",
            "pass":      conflict_respected,
        },
        "overall_go_nogo": "GO" if (overall_pass and highconf_pass and conflict_respected) else "NO-GO",
    }


# ── Human-readable summary ────────────────────────────────────────────────────

def format_summary(report: dict) -> str:
    """Formats the report as a readable text summary."""
    lines = []
    m = report["meta"]

    lines.append("═" * 60)
    lines.append("  BACKTEST PERFORMANCE REPORT")
    lines.append("═" * 60)
    lines.append(f"  Symbols:    {', '.join(m['symbols'])}")
    lines.append(f"  Period:     {m['start_date']} → {m['end_date']}")
    lines.append(f"  Records:    {m['total_records']} days")
    lines.append(f"  Signals:    {m['directional_signals']} directional")
    lines.append(f"  Resolved:   {m['resolved_outcomes']} (WIN/LOSS)")
    lines.append("")

    wr = report["overall_win_rate"]
    lines.append("── OVERALL WIN RATE ──────────────────────────────────")
    lines.append(f"  Win:   {wr['win']}%  ({wr.get('wins', 0)} trades)")
    lines.append(f"  Loss:  {wr['loss']}%  ({wr.get('losses', 0)} trades)")
    lines.append(f"  Open:  {wr['open']}%  (no resolution yet)")
    lines.append("")

    lines.append("── FORWARD RETURNS (avg % return after signal) ───────")
    fr = report["forward_returns"]
    for label, data in fr.items():
        if not data:
            continue
        parts = []
        for w in [1, 3, 7, 14]:
            val = data.get(f"avg_{w}d_pct")
            if val is not None:
                parts.append(f"{w}d: {val:+.2f}%")
        lines.append(f"  {label:20s}  {' | '.join(parts)}")
    lines.append("")

    lines.append("── WIN RATE BY SYMBOL ────────────────────────────────")
    for sym, data in report["win_rate_by_symbol"].items():
        lines.append(f"  {sym:6s}  {data['win_rate']}%  ({data['total']} signals)")
    lines.append("")

    lines.append("── WIN RATE BY REGIME ────────────────────────────────")
    for regime, data in report["win_rate_by_regime"].items():
        lines.append(f"  {regime:15s}  {data['win_rate']}%  ({data['total']} signals)")
    lines.append("")

    lines.append("── WIN RATE BY CONFIDENCE ────────────────────────────")
    for conf, data in report["win_rate_by_confidence"].items():
        lines.append(f"  {conf:10s}  {data['win_rate']}%  ({data['total']} signals)")
    lines.append("")

    lines.append("── SIGNAL FREQUENCY ──────────────────────────────────")
    for sig, data in report["signal_frequency"].items():
        lines.append(f"  {sig:20s}  {data['count']:4d}  ({data['pct']}%)")
    lines.append("")

    lines.append("── REGIME SUPPRESSION ────────────────────────────────")
    rs = report["regime_suppression"]
    lines.append(f"  Choppy days:           {rs['choppy_days_total']}")
    lines.append(f"  Signals in chop:       {rs['choppy_days_with_signal']}")
    lines.append(f"  Avg 7d return (signal in chop):    {rs['avg_7d_return_when_choppy_signal']}")
    lines.append(f"  Avg 7d return (no signal in chop): {rs['avg_7d_return_when_choppy_no_signal']}")
    lines.append("")

    lines.append("── DERIVATIVES ACCURACY ──────────────────────────────")
    for sig, data in report["derivatives_accuracy"].items():
        correct = "✅" if data.get("directional_correct") else (
                  "❌" if data.get("directional_correct") is False else "—")
        lines.append(
            f"  {sig:25s}  n={data['count']:4d}  "
            f"avg 7d: {str(data['avg_7d_return']):>8s}%  {correct}"
        )
    lines.append("")

    lines.append("── GO / NO-GO CHECK ──────────────────────────────────")
    gn = report["go_nogo_check"]
    for key, data in gn.items():
        if key == "overall_go_nogo":
            continue
        status = "✅ PASS" if data["pass"] else "❌ FAIL"
        lines.append(f"  {key:35s}  {data['actual']:>8s}  {status}")
    lines.append("")
    verdict = gn["overall_go_nogo"]
    lines.append(f"  VERDICT:  {'✅ GO — thresholds met' if verdict == 'GO' else '❌ NO-GO — thresholds not met'}")
    lines.append("═" * 60)

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest performance analyzer")
    parser.add_argument("--symbol",  type=str, default=None)
    parser.add_argument("--start",   type=str, default=None)
    parser.add_argument("--end",     type=str, default=None)
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else WATCHLIST
    records = load_signals(symbols, args.start, args.end)

    if not records:
        print("No records found. Run backtest_engine.py first.")
        exit(1)

    report  = build_report(records, symbols, args.start, args.end)
    summary = format_summary(report)

    # Save
    json_path = RESULTS_DIR / "performance_report.json"
    txt_path  = RESULTS_DIR / "performance_summary.txt"

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    with open(txt_path, "w") as f:
        f.write(summary)

    print(summary)
    print(f"\nFull report saved → {json_path}")
    print(f"Text summary saved → {txt_path}")
