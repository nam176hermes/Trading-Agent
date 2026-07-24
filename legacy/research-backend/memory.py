"""
memory.py — Trading Decision Memory & Reflection Loop
Adapted from TradingAgents (TauricResearch).

Stores every trade decision with timestamp + ticker. On subsequent runs
for the same ticker, fetches realized returns, generates an LLM reflection
on what went right/wrong, and injects past lessons into the current analysis.

This transforms the agent from amnesiac → learning. Single biggest gap vs
TradingAgents that we can close with zero new dependencies.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from schemas import TradingDecision, TradingSignal
from job_attribution import strict_worker_invocation
from runtime_paths import data_root

log = logging.getLogger("memory")

MEMORY_DIR = data_root() / "memory"
if strict_worker_invocation() is None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
DECISIONS_FILE = MEMORY_DIR / "decisions.jsonl"
REFLECTIONS_FILE = MEMORY_DIR / "reflections.jsonl"


def store_decision(
    ticker: str,
    trade_date: str,
    suggestion: str,
    confidence: float,
    price: float,
    signals: dict | None = None,
    report_snippet: str = "",
) -> bool:
    """
    Store a trade decision for future reflection.
    Called after every pipeline run that produces a signal.

    Returns:
        True if write-confirmation succeeded, False otherwise
    """
    entry = {
        "ticker": ticker.upper(),
        "date": trade_date,
        "suggestion": str(suggestion),
        "confidence": float(confidence) if isinstance(confidence, (int, float)) else 0.5,
        "price_at_decision": float(price) if isinstance(price, (int, float)) else 0.0,
        "signals": signals or {},
        "report_snippet": report_snippet[:500],
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "reflected": False,             # Will be flipped after reflection
    }

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # Write decision
    with open(DECISIONS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Write-confirmation: read it back
    try:
        with open(DECISIONS_FILE, "r") as f:
            lines = f.readlines()
            last_line = lines[-1].strip() if lines else ""
            restored = json.loads(last_line)

            # Verify key fields match
            if (restored.get("ticker") == entry["ticker"] and
                restored.get("date") == entry["date"] and
                restored.get("stored_at") == entry["stored_at"]):
                log.info("[memory] Stored %s decision: %s (confidence=%s, price=%s) ✓",
                         ticker, suggestion, confidence, price)
                return True
            else:
                log.warning("[memory] Write-confirmation failed for %s decision: data mismatch", ticker)
                return False

    except (json.JSONDecodeError, OSError, IndexError) as e:
        log.error("[memory] Write-confirmation failed for %s decision: %s", ticker, e)
        return False


def get_pending_reflections(ticker: str) -> list[dict]:
    """
    Get decisions that haven't been reflected on yet for a ticker.
    Only returns decisions ≥24h old so returns data is meaningful.
    """
    if not DECISIONS_FILE.exists():
        return []

    pending = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    with open(DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["ticker"] != ticker.upper():
                    continue
                if entry.get("reflected", False):
                    continue
                # Only reflect on decisions at least 24h old
                stored = datetime.fromisoformat(entry["stored_at"])
                if stored > cutoff:
                    continue
                pending.append(entry)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    return pending


def get_recent_reflections(ticker: str, limit: int = 5) -> list[dict]:
    """Get recent reflection lessons for a ticker."""
    if not REFLECTIONS_FILE.exists():
        return []

    reflections = []
    with open(REFLECTIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["ticker"] == ticker.upper():
                    reflections.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue

    return sorted(reflections, key=lambda r: r.get("date", ""), reverse=True)[:limit]


def get_cross_ticker_lessons(limit: int = 3) -> list[dict]:
    """Get recent lessons from other tickers for cross-pollination."""
    if not REFLECTIONS_FILE.exists():
        return []

    all_reflections = []
    with open(REFLECTIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                all_reflections.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue

    return sorted(all_reflections, key=lambda r: r.get("date", ""), reverse=True)[:limit]


def build_memory_context(ticker: str) -> str:
    """
    Build a memory injection string for the current analysis.
    Includes: recent same-ticker decisions, reflections, cross-ticker lessons.
    Returns empty string if no history exists.
    """
    parts = []

    # Recent same-ticker reflections
    reflections = get_recent_reflections(ticker, limit=5)
    if reflections:
        parts.append(f"## Past Analysis on {ticker}\n")
        for r in reflections:
            parts.append(
                f"- [{r.get('date', '?')}] Signal: {r.get('suggestion', '?')} "
                f"(confidence: {r.get('confidence', '?')}). "
                f"Result: {r.get('raw_return_pct', '?')}% raw, "
                f"{r.get('alpha_return_pct', '?')}% alpha.\n"
                f"  Lesson: {r.get('reflection', 'No reflection yet.')}\n"
            )

    # Cross-ticker lessons
    cross = get_cross_ticker_lessons(limit=3)
    if cross:
        parts.append("\n## Cross-Ticker Lessons\n")
        for c in cross:
            if c["ticker"] == ticker.upper():
                continue
            parts.append(
                f"- [{c.get('date', '?')}] {c['ticker']}: "
                f"{c.get('reflection', '')[:200]}\n"
            )

    return "".join(parts) if parts else ""


# ── Per-Agent Memory Banks ───────────────────────────────────────────────────────

def get_memory_for_bull(ticker: str, limit: int = 5) -> str:
    """
    Get memory context specifically for the bull agent.
    Returns times the bull was WRONG (should have been bearish or was wrongly bullish).
    """
    if not REFLECTIONS_FILE.exists():
        return ""

    bull_mistakes = []

    with open(REFLECTIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["ticker"] != ticker.upper():
                    continue

                # Bull was wrong if:
                # 1. Signal was BUY but outcome was loss
                # 2. Signal was HOLD but price went up (missed opportunity)
                suggestion = entry.get("suggestion", "").upper()
                raw_return = entry.get("raw_return_pct", 0)

                if suggestion == "BUY" and raw_return < 0:
                    bull_mistakes.append(entry)
                elif suggestion == "SELL" and raw_return > 0:
                    bull_mistakes.append(entry)  # Bull was wrong to be bearish
                elif suggestion == "HOLD" and raw_return > 5:
                    bull_mistakes.append(entry)  # Bull missed opportunity

            except (json.JSONDecodeError, KeyError):
                continue

    # Sort by return (worst first) and limit
    bull_mistakes.sort(key=lambda x: x.get("raw_return_pct", 0))
    bull_mistakes = bull_mistakes[:limit]

    if not bull_mistakes:
        return ""

    lines = [f"## Times the Bull Case Was Wrong for {ticker}\n"]
    for m in bull_mistakes:
        lines.append(
            f"- [{m.get('date', '?')}] You argued for {m.get('suggestion', '?')} "
            f"but result was {m.get('raw_return_pct', '?')}%. "
            f"Lesson: {m.get('reflection', 'No reflection.')[:200]}\n"
        )

    return "\n".join(lines)


def get_memory_for_bear(ticker: str, limit: int = 5) -> str:
    """
    Get memory context specifically for the bear agent.
    Returns times the bear was WRONG (should have been bullish or was wrongly bearish).
    """
    if not REFLECTIONS_FILE.exists():
        return ""

    bear_mistakes = []

    with open(REFLECTIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["ticker"] != ticker.upper():
                    continue

                # Bear was wrong if:
                # 1. Signal was SELL but price went up (loss on short)
                # 2. Signal was HOLD but price went down (missed hedge)
                # 3. Signal was BUY but price went down (bear was right to worry)
                suggestion = entry.get("suggestion", "").upper()
                raw_return = entry.get("raw_return_pct", 0)

                if suggestion == "SELL" and raw_return > 0:
                    bear_mistakes.append(entry)  # Bear was wrong, price went up
                elif suggestion == "BUY" and raw_return < 0:
                    bear_mistakes.append(entry)  # Bear was right to worry
                elif suggestion == "HOLD" and raw_return < -5:
                    bear_mistakes.append(entry)  # Bear missed hedge opportunity

            except (json.JSONDecodeError, KeyError):
                continue

    # Sort by return (worst for bear first) and limit
    bear_mistakes.sort(key=lambda x: -x.get("raw_return_pct", 0))  # Negative for bear
    bear_mistakes = bear_mistakes[:limit]

    if not bear_mistakes:
        return ""

    lines = [f"## Times the Bear Case Was Wrong for {ticker}\n"]
    for m in bear_mistakes:
        lines.append(
            f"- [{m.get('date', '?')}] You argued for {m.get('suggestion', '?')} "
            f"but result was {m.get('raw_return_pct', '?')}%. "
            f"Lesson: {m.get('reflection', 'No reflection.')[:200]}\n"
        )

    return "\n".join(lines)


def mark_reflected(entry_date: str, ticker: str) -> bool:
    """
    Mark a decision as reflected so it won't be processed again.
    Uses a simple file rewrite approach — fine for small decision logs.
    """
    if not DECISIONS_FILE.exists():
        return False

    updated = []
    found = False

    with open(DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["ticker"] == ticker.upper() and entry["date"] == entry_date:
                    entry["reflected"] = True
                    found = True
                updated.append(entry)
            except (json.JSONDecodeError, KeyError):
                updated.append(json.loads(line) if line.strip() else {})

    if found:
        with open(DECISIONS_FILE, "w") as f:
            for entry in updated:
                f.write(json.dumps(entry) + "\n")
        log.info("[memory] Marked %s %s as reflected", ticker, entry_date)

    return found


def store_reflection(
    ticker: str,
    trade_date: str,
    suggestion: str,
    confidence: float,
    raw_return_pct: float,
    alpha_return_pct: float,
    holding_days: int,
    reflection: str,
):
    """Store a generated reflection for future injection."""
    entry = {
        "ticker": ticker.upper(),
        "date": trade_date,
        "suggestion": suggestion,
        "confidence": confidence,
        "raw_return_pct": round(raw_return_pct, 2),
        "alpha_return_pct": round(alpha_return_pct, 2),
        "holding_days": holding_days,
        "reflection": reflection,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(REFLECTIONS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    log.info("[memory] Stored reflection for %s %s", ticker, trade_date)


# ── Typed Decision Storage (Phase 5) ──────────────────────────────────────────────

TYPED_DECISIONS_FILE = MEMORY_DIR / "typed_decisions.jsonl"


def store_typed_decision(decision: TradingDecision) -> bool:
    """
    Store a Pydantic TradingDecision as JSON line + markdown entry.
    Marks as "pending" for deferred reflection.

    Returns:
        True if write-confirmation succeeded, False otherwise
    """
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        # Store as structured JSON line
        entry = decision.model_dump(mode="json", exclude_none=True)
        entry["_pending_reflection"] = True
        entry["_stored_at"] = datetime.now(timezone.utc).isoformat()

        with open(TYPED_DECISIONS_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        # Write-confirmation: read it back
        try:
            with open(TYPED_DECISIONS_FILE, "r") as f:
                lines = f.readlines()
                last_line = lines[-1].strip() if lines else ""
                restored = json.loads(last_line)

                if (restored.get("asset") == entry["asset"] and
                    restored.get("_stored_at") == entry["_stored_at"]):
                    pass  # Success
                else:
                    log.warning("[memory] Write-confirmation failed for typed decision: data mismatch")
                    return False

        except (json.JSONDecodeError, OSError, IndexError) as e:
            log.error("[memory] Write-confirmation failed for typed decision: %s", e)
            return False

        # Also append to trading_memory.md (human-readable)
        md_path = MEMORY_DIR / "trading_memory.md"
        with open(md_path, "a") as f:
            f.write(f"\n## {decision.asset} — {decision.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n")
            f.write(f"**Action:** {decision.final_action} ({decision.final_position_size_pct}%)\n")
            f.write(f"**Executive Summary:** {decision.executive_summary}\n")
            f.write(f"**Initial Signal:** {decision.initial_signal.action} (confidence: {decision.initial_signal.confidence:.0%})\n")
            if decision.bull_synthesis:
                f.write(f"**Bull Case:** {decision.bull_synthesis[:200]}...\n")
            if decision.bear_synthesis:
                f.write(f"**Bear Case:** {decision.bear_synthesis[:200]}...\n")
            f.write(f"**Status:** Pending Reflection\n")

        log.info("[memory] Stored typed decision for %s: %s (%.0f%%) ✓",
                 decision.asset, decision.final_action, decision.final_position_size_pct)
        return True
    except Exception as e:
        log.error("[memory] Failed to store typed decision: %s", e)
        return False


def get_typed_decisions(ticker: str, limit: int = 10) -> list[TradingDecision]:
    """
    Retrieve past typed decisions for a ticker.
    Returns most recent decisions first.
    """
    if not TYPED_DECISIONS_FILE.exists():
        return []

    decisions = []
    with open(TYPED_DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("asset", "").upper() == ticker.upper():
                    decisions.append(TradingDecision(**entry))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                log.debug("[memory] Skipping malformed entry: %s", e)
                continue

    # Sort by timestamp descending
    decisions.sort(key=lambda d: d.timestamp, reverse=True)
    return decisions[:limit]


def get_pending_reflections_typed(horizon_days: int = 7) -> list[tuple[str, TradingDecision]]:
    """
    Get all decisions that are past their reflection horizon but not yet reflected.
    Returns (entry_date, decision) tuples.
    """
    if not TYPED_DECISIONS_FILE.exists():
        return []

    pending = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=horizon_days)

    with open(TYPED_DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if not entry.get("_pending_reflection", False):
                    continue

                # Check if past horizon
                stored_at = datetime.fromisoformat(entry.get("_stored_at", ""))
                if stored_at > cutoff:
                    continue

                decision = TradingDecision(**entry)
                pending.append((stored_at, decision))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                log.debug("[memory] Skipping entry: %s", e)
                continue

    # Sort by storage time
    pending.sort(key=lambda x: x[0])
    return pending


def mark_decision_reflected(decision_timestamp: datetime, asset: str) -> bool:
    """
    Mark a typed decision as reflected so it won't be processed again.
    """
    if not TYPED_DECISIONS_FILE.exists():
        return False

    updated = []
    found = False
    target_ts = decision_timestamp.isoformat()

    with open(TYPED_DECISIONS_FILE) as f:
        for line in f:
            try:
                entry = json.loads(line)
                if (entry.get("asset", "").upper() == asset.upper() and
                    entry.get("timestamp") == target_ts):
                    entry["_pending_reflection"] = False
                    entry["_reflected_at"] = datetime.now(timezone.utc).isoformat()
                    found = True
                updated.append(entry)
            except (json.JSONDecodeError, KeyError):
                if line.strip():
                    updated.append(json.loads(line) if line.strip() else {})

    if found:
        with open(TYPED_DECISIONS_FILE, "w") as f:
            for entry in updated:
                f.write(json.dumps(entry, default=str) + "\n")
        log.info("[memory] Marked %s %s as reflected", asset, decision_timestamp.strftime('%Y-%m-%d'))

    return found


# ── Reflection Prompt ─────────────────────────────────────────────────────────

REFLECTION_PROMPT = """You are reviewing a past trading decision for {ticker}.

Original decision:
  Date: {trade_date}
  Signal: {suggestion} (confidence: {confidence})
  Price at decision: ${price}

Actual outcome:
  Raw return: {raw_return_pct}%
  Alpha vs benchmark: {alpha_return_pct}%
  Holding period: {days} days

The original analysis said:
{report_snippet}

Write a ONE-PARAGRAPH honest reflection. Cover:
1. Was this call right or wrong? Why?
2. What signal was most predictive (or misleading)?
3. What one lesson should be carried forward to the next {ticker} analysis?

Be direct. No fluff. If the call was wrong, say so clearly."""


def build_reflection_prompt(
    ticker: str,
    trade_date: str,
    suggestion: str,
    confidence: float,
    price: float,
    raw_return_pct: float,
    alpha_return_pct: float,
    days: int,
    report_snippet: str,
) -> str:
    return REFLECTION_PROMPT.format(
        ticker=ticker,
        trade_date=trade_date,
        suggestion=suggestion,
        confidence=confidence,
        price=price,
        raw_return_pct=raw_return_pct,
        alpha_return_pct=alpha_return_pct,
        days=days,
        report_snippet=report_snippet[:500],
    )
