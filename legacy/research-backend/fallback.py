"""
fallback.py — Multi-source fallback chain for stock prices.
Called internally by twelve_data.py when the primary source fails.
Chain: Twelve Data → Finnhub → Polygon.io
"""
import json
import os
import urllib.request
import urllib.error
from urllib.parse import urlencode


def _validated_provider_key(key: str | None) -> str | None:
    """Return a bounded printable provider key without retaining it globally."""
    if key is None:
        return None
    key = key.strip()
    if not key or len(key) > 512 or not key.isprintable():
        return None
    return key


def fetch_finnhub_quote(symbol: str) -> dict | None:
    """Fallback 1: Finnhub free-tier quote endpoint.
    Returns dict with price, change_pct, volume or None on failure.
    """
    api_key = _validated_provider_key(os.environ.get("FINNHUB_API_KEY"))
    if api_key is None:
        print("    Finnhub: provider key unavailable")
        return None
    query = urlencode({"symbol": symbol, "token": api_key})
    url = f"https://finnhub.io/api/v1/quote?{query}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"    Finnhub error for {symbol}: {e}")
        return None

    c = data.get("c", 0)
    if not c or c == 0:
        print(f"    Finnhub: no current price for {symbol}")
        return None

    prev_close = data.get("pc", c)
    change_pct = ((c - prev_close) / prev_close * 100) if prev_close else 0

    return {
        "symbol": symbol,
        "current_price": c,
        "price_change_24h_pct": round(change_pct, 2),
        "volume": data.get("t", 0) or 0,
        "_data_source": "finnhub",
        "_fallback_used": True,
    }


def fetch_polygon_prev(symbol: str) -> dict | None:
    """Fallback 2: Polygon.io free-tier previous close.
    Returns dict with price, change_pct, volume or None on failure.
    """
    api_key = _validated_provider_key(os.environ.get("POLYGON_API_KEY"))
    if api_key is None:
        print("    Polygon: provider key unavailable")
        return None
    query = urlencode({"apiKey": api_key})
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev?{query}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"    Polygon error for {symbol}: {e}")
        return None

    results = data.get("results", [])
    if not results:
        print(f"    Polygon: no results for {symbol}")
        return None

    r = results[0]
    c = r.get("c", 0)
    if not c or c == 0:
        print(f"    Polygon: no close price for {symbol}")
        return None

    o = r.get("o", c)
    change_pct = ((c - o) / o * 100) if o else 0

    return {
        "symbol": symbol,
        "current_price": c,
        "price_change_24h_pct": round(change_pct, 2),
        "volume": r.get("v", 0) or 0,
        "_data_source": "polygon",
        "_fallback_used": True,
    }


def fetch_with_fallback(symbol: str) -> dict | None:
    """Try finnhub first, then polygon. Returns first successful result or None."""
    print(f"    Trying fallback chain for {symbol}...")

    result = fetch_finnhub_quote(symbol)
    if result:
        print(f"    → Used finnhub for {symbol}: ${result['current_price']}")
        return result

    result = fetch_polygon_prev(symbol)
    if result:
        print(f"    → Used polygon for {symbol}: ${result['current_price']}")
        return result

    print(f"    All fallbacks failed for {symbol}")
    return None
