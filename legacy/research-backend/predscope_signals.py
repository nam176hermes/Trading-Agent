"""
predscope_signals.py — Polymarket odds → trading signals.

Reads the latest reports/prediction_market_*.json, maps market titles
to tracked crypto symbols via keyword matching, and emits BUY/SELL signals
based on outcome probabilities.

Signal logic:
- probability > 0.70 → BUY with confidence=probability
- probability < 0.30 → SELL with confidence=(1-probability)
- 0.45-0.55 → no signal (uncertain)
- 0.55-0.70 → weak BUY (confidence=probability-0.45, scaled)
- 0.30-0.45 → weak SELL
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from runtime_paths import reports_dir, signal_output_dir

REPORTS_DIR = reports_dir()
SIGNALS_DIR = signal_output_dir()
OUTPUT_FILE = SIGNALS_DIR / "predscope_signals.json"

# Keyword → symbol mapping for Polymarket market titles
SYMBOL_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
    "TON": ["toncoin", "ton"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "DOT": ["polkadot", "dot"],
    "LINK": ["chainlink", "link"],
    "MATIC": ["polygon", "matic"],
    "XRP": ["ripple", "xrp"],
    "LTC": ["litecoin", "ltc"],
    "UNI": ["uniswap", "uni"],
    "AAVE": ["aave"],
    "ATOM": ["cosmos", "atom"],
    "NEAR": ["near protocol", "near"],
}


def _build_keyword_patterns():
    """Pre-compile regex patterns for symbol matching."""
    patterns = {}
    for symbol, keywords in SYMBOL_KEYWORDS.items():
        pattern = re.compile(
            r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b',
            re.IGNORECASE,
        )
        patterns[symbol] = pattern
    return patterns


def _find_latest_report() -> dict | None:
    """Find and load the newest prediction_market_*.json report."""
    files = sorted(REPORTS_DIR.glob("prediction_market_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _match_symbol(title: str, patterns: dict) -> str | None:
    """Match a market title to a tracked crypto symbol."""
    for symbol, pattern in patterns.items():
        if pattern.search(title):
            return symbol
    return None


def _best_probability(outcomes: list[dict]) -> float | None:
    """Extract the single best outcome probability from a market's outcomes list.
    For binary markets (e.g., "Yes/No"), the "Yes" probability is the signal.
    If multiple outcomes exist, return the highest probability."""
    if not outcomes:
        return None
    # Prefer "Yes" or "Up" outcomes for directional signal
    for o in outcomes:
        ot = o.get("title", "").lower()
        if ot in ("yes", "up", "higher"):
            return o.get("probability")
    # Otherwise return the max probability
    return max((o.get("probability", 0) for o in outcomes), default=None)


def _probability_to_signal(prob: float) -> tuple[str, float] | None:
    """Convert a probability to a (direction, confidence) tuple, or None if no signal."""
    if prob is None:
        return None
    if prob > 0.70:
        return ("BUY", min(prob, 1.0))
    if prob < 0.30:
        return ("SELL", min(1.0 - prob, 1.0))
    if 0.55 < prob <= 0.70:
        return ("BUY", (prob - 0.45) * 4)  # scale to roughly 0.10-1.0
    if 0.30 <= prob < 0.45:
        return ("SELL", (0.45 - prob) * 4)
    return None  # 0.45-0.55 = uncertain


def get_predscope_signals() -> list[dict]:
    """Generate trading signals from latest Polymarket prediction data.

    Returns a list of signal dicts: {symbol, direction, confidence, source,
    market_question, timestamp}.
    """
    report = _find_latest_report()
    if not report:
        return []

    patterns = _build_keyword_patterns()
    markets = report.get("markets", [])
    timestamp = report.get("collected_at", datetime.now(timezone.utc).isoformat())

    signals = []
    seen = set()

    for m in markets:
        title = m.get("title", "")
        symbol = _match_symbol(title, patterns)
        if not symbol or symbol in seen:
            continue

        outcomes = m.get("outcomes", [])
        prob = _best_probability(outcomes)
        if prob is None:
            continue

        result = _probability_to_signal(prob)
        if result is None:
            continue

        direction, confidence = result
        seen.add(symbol)
        signals.append({
            "symbol": symbol,
            "direction": direction,
            "confidence": round(confidence, 4),
            "source": "prediction_market",
            "market_question": title,
            "timestamp": timestamp,
        })

    # Persist to signals/ directory
    SIGNALS_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(signals, indent=2, default=str))

    return signals


if __name__ == "__main__":
    sigs = get_predscope_signals()
    print(f"PredScope signals: {len(sigs)}")
    for s in sigs:
        print(f"  {s['symbol']:6s} {s['direction']:4s} conf={s['confidence']:.3f}  {s['market_question']}")
