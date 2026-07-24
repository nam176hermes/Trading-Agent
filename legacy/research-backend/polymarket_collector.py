"""
polymarket_collector.py — Polymarket prediction market probabilities.

Fetches crypto-related prediction market "Yes" prices from Polymarket's
CLOB API (public, no auth required). A "Yes" price ≈ probability (0–1)
that the market resolves YES (e.g., "Will BTC close above $X?").

Used as a forward-looking sentiment feature in ml_predictor.py:
    feats["prediction_market_prob"] = polymarket probability for that symbol

Usage:
    from polymarket_collector import collect, get_prediction_prob
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import data_root

log = logging.getLogger("polymarket_collector")

MEMORY_DIR = data_root() / "memory"
CACHE_FILE = MEMORY_DIR / "polymarket_signals.json"

CLOB_BASE = "https://clob.polymarket.com"
CRYPTO_KEYWORDS = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"]


def _fetch(url: str, timeout: int = 10) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NovaTrade/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.debug("Polymarket fetch failed (%s): %s", url, e)
        return None


def fetch_crypto_markets() -> list[dict]:
    """Return open Polymarket markets whose question mentions crypto."""
    data = _fetch(f"{CLOB_BASE}/markets?active=true&closed=false&limit=100")
    if not data:
        return []
    markets = data if isinstance(data, list) else data.get("data", [])
    return [m for m in markets
            if any(kw in (m.get("question") or "").lower()
                   for kw in CRYPTO_KEYWORDS)]


def _symbol_from_question(question: str) -> Optional[str]:
    q = question.lower()
    if "btc" in q or "bitcoin" in q:
        return "BTC"
    if "eth" in q or "ethereum" in q:
        return "ETH"
    if "sol" in q or "solana" in q:
        return "SOL"
    return None


def extract_signals(markets: list[dict]) -> dict[str, float]:
    """
    Extract bullish probability per symbol.
    Averages across all matching markets for that symbol.
    """
    bucket: dict[str, list[float]] = {}
    for m in markets:
        symbol = _symbol_from_question(m.get("question") or "")
        if not symbol:
            continue
        tokens = m.get("tokens") or []
        for tok in tokens:
            outcome = (tok.get("outcome") or "").lower()
            if outcome in ("yes", "higher", "above", "up"):
                try:
                    price = float(tok.get("price", 0))
                    bucket.setdefault(symbol, []).append(price)
                except (TypeError, ValueError):
                    pass
                break
    return {sym: round(sum(probs) / len(probs), 4)
            for sym, probs in bucket.items() if probs}


def collect() -> dict:
    """Fetch, extract, cache, and return Polymarket crypto signals."""
    try:
        markets = fetch_crypto_markets()
        signals = extract_signals(markets)
    except Exception as e:
        log.warning("Polymarket collection failed: %s", e)
        signals = {}

    result = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "signals":      signals,
        "n_markets":    len(signals),
    }
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(result, indent=2))
    log.info("Polymarket: %d crypto market probabilities — %s", len(signals), signals)
    return result


def get_prediction_prob(symbol: str) -> Optional[float]:
    """Return cached bullish probability for a symbol (0.0–1.0), or None."""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            return data.get("signals", {}).get(symbol.upper())
        except Exception:
            pass
    return None


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    result = collect()
    import json as _json
    print(_json.dumps(result, indent=2))
