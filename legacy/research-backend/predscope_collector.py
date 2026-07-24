"""
predscope_collector.py — Polymarket prediction market data via free PredScope API.
No API key needed. Polls every 5+ minutes. Saves filtered crypto/macro markets.
"""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from collector_utils import _fetch_with_retry
from runtime_paths import reports_dir

PREDSCOPE_URL = "https://predscope.com/api/markets.json"
REPORTS_DIR = reports_dir()
REFRESH_SEC = 300  # 5 min between polls (rate limit: 100/hr)


def _fetch_predscope():
    """Fetch raw PredScope markets JSON."""
    req = urllib.request.Request(
        PREDSCOPE_URL,
        headers={"User-Agent": "trading-agent/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _filter_relevant(markets: list) -> list:
    """Keep crypto + key macro categories only."""
    relevant_cats = {"crypto", "politics", "geopolitics", "economy", "fed"}
    out = []
    for m in markets:
        cats = {c.lower() for c in m.get("categories", [])}
        if cats & relevant_cats:
            out.append({
                "title": m.get("title"),
                "slug": m.get("slug"),
                "volume": m.get("volume"),
                "volume_24h": m.get("volume_24h"),
                "liquidity": m.get("liquidity"),
                "categories": list(cats),
                "outcomes": [
                    {
                        "title": o.get("title"),
                        "probability": o.get("probability"),
                        "day_change": o.get("day_change"),
                    }
                    for o in m.get("outcomes", [])
                ],
            })
    return out


def collect():
    """Fetch + filter + save prediction market data."""
    raw = _fetch_with_retry("predscope", _fetch_predscope)
    if raw is None:
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    markets = raw if isinstance(raw, list) else raw.get("markets", raw.get("data", []))
    filtered = _filter_relevant(markets)

    report = {
        "source": "predscope",
        "collected_at": timestamp,
        "total_markets": len(markets),
        "filtered_markets": len(filtered),
        "markets": filtered,
    }

    out_path = REPORTS_DIR / f"prediction_market_{timestamp}.json"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"[predscope] saved {len(filtered)} markets → {out_path.name}")
    return report


if __name__ == "__main__":
    collect()
