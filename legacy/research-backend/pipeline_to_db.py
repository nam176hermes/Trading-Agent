"""
Bridge: Pipeline decisions → SQLite signals.
Reads decisions_scored.jsonl and populates the signals table
so the trading agent can act on them.

Tracks sync position via agent_state to avoid re-processing.
"""

import json
import sys
import os
import logging
from pathlib import Path
from typing import Tuple

sys.path.insert(0, str(Path(__file__).parent))

from db import repository as db
from runtime_paths import data_root

log = logging.getLogger("pipeline-to-db")

DECISIONS_FILE = data_root() / "decisions_scored.jsonl"
STATE_KEY = "pipeline_to_db_last_line"


def _get_last_line() -> int:
    """Get the last synced line number (0 if never synced)."""
    val = db.get_state(STATE_KEY)
    return int(val) if val else 0


def _set_last_line(line: int):
    """Record the last synced line number."""
    db.set_state(STATE_KEY, str(line))


def _count_lines() -> int:
    """Count lines in decisions_scored.jsonl (fast: wc -l)."""
    if not DECISIONS_FILE.exists():
        return 0
    with open(DECISIONS_FILE) as f:
        return sum(1 for _ in f)


def sync_new(limit: int = 50) -> Tuple[int, int]:
    """
    Sync only new pipeline decisions to the signals table.
    Returns (inserted, skipped).
    Uses agent_state to track last synced line.
    """
    if not DECISIONS_FILE.exists():
        return (0, 0)

    total_lines = _count_lines()
    last_synced = _get_last_line()

    if total_lines <= last_synced:
        log.debug("No new decisions — %d total, last synced=%d", total_lines, last_synced)
        return (0, 0)

    # Read only new lines (from last_synced to end)
    new_line_count = total_lines - last_synced
    process_count = min(new_line_count, limit)

    inserted = 0
    skipped = 0

    with open(DECISIONS_FILE) as f:
        # Fast-forward to last_synced
        for _ in range(last_synced):
            next(f, None)

        for i in range(process_count):
            line = next(f, None)
            if not line:
                break

            try:
                decision = json.loads(line.strip())
            except json.JSONDecodeError:
                skipped += 1
                continue

            symbol = decision.get("ticker", "")
            direction = (decision.get("suggestion") or "HOLD").upper()
            confidence = decision.get("confidence", 0)
            quality = decision.get("quality_score", {}).get("overall", 0)

            # Map suggestion to valid direction
            direction_map = {
                "BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD",
                "STRONG BUY": "BUY", "STRONG SELL": "SELL",
                "WATCH": "HOLD", "WATCH FOR EXIT": "SELL",
            }
            direction = direction_map.get(direction, "HOLD")

            # Skip low-confidence signals (all directions).
            # A 50% confidence signal IS noise — the market already prices it in.
            # Only signals >= MIN_SIGNAL_CONFIDENCE pass through.
            MIN_SIGNAL_CONFIDENCE = 0.72  # Matches trading_agent.py gate
            if confidence < MIN_SIGNAL_CONFIDENCE:
                skipped += 1
                continue

            # Extract TA indicators so trading_agent.py gates can read them
            signals_data = decision.get("signals", {}) or {}

            # ADX gate: skip BUY signals in non-trending (ranging) markets.
            # ADX < 20 = no directional trend; RSI/MACD signals are noise in ranging markets.
            adx = signals_data.get("adx_14")
            if direction == "BUY" and adx is not None:
                try:
                    if float(adx) < 20:
                        skipped += 1
                        log.debug("ADX gate: skipping BUY %s — ADX=%.1f < 20 (ranging market)", symbol, float(adx))
                        continue
                except (TypeError, ValueError):
                    pass

            metadata = {
                "quality_score": quality,
                "report_snippet": decision.get("report_snippet", "")[:500],
                "price": decision.get("price_at_decision", 0),
                "rsi_14": signals_data.get("rsi_14"),
                "adx_14": adx,
                "atr_14": signals_data.get("atr_14"),
                "macd_signal": signals_data.get("macd_signal"),
                "price_vs_sma200": signals_data.get("price_vs_sma200"),
                "stochrsi_signal": signals_data.get("stochrsi_signal"),
            }

            signal_id = db.insert_signal(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                strategy="research-pipeline",
                source="decisions_scored.jsonl",
                metadata=metadata,
            )
            inserted += 1
            log.debug("Signal #%d: %s %s (conf=%.0f%%)",
                      signal_id, symbol, direction, (confidence or 0) * 100)

    # Advance the pointer
    new_position = last_synced + process_count
    _set_last_line(new_position)

    log.info("Synced %d/%d new decisions (lines %d→%d of %d)",
             inserted, process_count, last_synced, new_position, total_lines)
    return (inserted, skipped)


def sync_latest(limit: int = 50) -> int:
    """Legacy wrapper — syncs new decisions, returns total inserted."""
    inserted, _ = sync_new(limit=limit)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    inserted, skipped = sync_new()
    print(f"✅ Inserted {inserted} signals, skipped {skipped}" +
          (" (no new decisions)" if inserted == 0 else ""))
