"""
adanos_signals.py — Social Sentiment → Trading Signals

Reads the latest reports/social_sentiment_*.json (Adanos-collected Reddit crypto data),
computes buzz change and sentiment signals, and emits BUY/SELL signals.

Momentum signal (buzz spike = crowd attention before price move):
- buzz_change_24h > 0.30 AND sentiment_score > 0.3 → BUY, confidence=buzz_change*sentiment_score
- buzz_change_24h > 0.50 → strong BUY regardless of sentiment

Contrarian signal (extreme sentiment = reversal incoming):
- sentiment_score > 0.80 → SELL (euphoria = top)
- sentiment_score < -0.60 → BUY (fear = bottom)

Source attribution: "adanos_social"
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from runtime_paths import reports_dir, signal_output_dir

REPORTS_DIR = reports_dir()
SIGNALS_DIR = signal_output_dir()
OUTPUT_FILE = SIGNALS_DIR / "adanos_signals.json"

# Tokens tracked by the trading pipeline
TRACKED_TOKENS = {
    "BTC", "ETH", "SOL", "TON", "DOGE", "ADA", "AVAX", "DOT", "LINK", "MATIC",
    "XRP", "LTC", "UNI", "AAVE", "ATOM", "NEAR",
}


def _find_latest_report() -> dict | None:
    files = sorted(REPORTS_DIR.glob("social_sentiment_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _compute_buzz_change(trend_history: list[float]) -> float:
    """Compute 24h buzz change from trend_history array.
    trend_history has 7 entries (latest last). Returns fractional change."""
    if not trend_history or len(trend_history) < 2:
        return 0.0
    first = trend_history[0]
    last = trend_history[-1]
    if first == 0:
        return 0.0
    return (last - first) / first


def get_adanos_signals() -> list[dict]:
    """Generate trading signals from latest social sentiment data.

    Returns list of signal dicts: {symbol, direction, confidence, source, reason, timestamp}.
    """
    report = _find_latest_report()
    if not report:
        return []

    data = report.get("data", {})
    reddit_crypto = data.get("reddit_crypto", [])
    timestamp = report.get("collected_at", datetime.now(timezone.utc).isoformat())

    signals = []

    for entry in reddit_crypto:
        symbol = entry.get("symbol", "").upper()
        if symbol not in TRACKED_TOKENS:
            continue

        buzz_change = _compute_buzz_change(entry.get("trend_history", []))
        sentiment = entry.get("sentiment_score", 0.0)
        confidence = 0.0
        direction = None
        reason = ""

        # Contrarian signals (extreme sentiment = reversal)
        if sentiment > 0.80:
            direction = "SELL"
            confidence = min(sentiment, 1.0)
            reason = f"euphoria_top(sentiment={sentiment:.3f})"
        elif sentiment < -0.60:
            direction = "BUY"
            confidence = min(abs(sentiment), 1.0)
            reason = f"fear_bottom(sentiment={sentiment:.3f})"

        # Momentum signals (buzz spike = crowd attention)
        if direction is None:
            if buzz_change > 0.50:
                direction = "BUY"
                confidence = min(buzz_change, 1.0)
                reason = f"strong_buzz_spike(buzz_change_24h={buzz_change:.2f})"
            elif buzz_change > 0.30 and sentiment > 0.3:
                direction = "BUY"
                confidence = min(buzz_change * sentiment, 1.0)
                reason = f"buzz_momentum(buzz={buzz_change:.2f},sentiment={sentiment:.3f})"

        if direction is None:
            continue

        signals.append({
            "symbol": symbol,
            "direction": direction,
            "confidence": round(confidence, 4),
            "source": "adanos_social",
            "buzz_score": entry.get("buzz_score"),
            "buzz_change_24h": round(buzz_change, 4),
            "sentiment_score": round(sentiment, 4),
            "reason": reason,
            "timestamp": timestamp,
        })

    SIGNALS_DIR.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(signals, indent=2, default=str))

    return signals


if __name__ == "__main__":
    sigs = get_adanos_signals()
    print(f"Adanos signals: {len(sigs)}")
    for s in sigs:
        print(f"  {s['symbol']:6s} {s['direction']:4s} conf={s['confidence']:.3f}  {s['reason']}")
