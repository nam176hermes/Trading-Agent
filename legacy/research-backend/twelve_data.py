"""
twelve_data.py — Stock/ETF/FX price collector via Twelve Data API.
Free tier: 800 calls/day, 8 calls/min. Respects rate limit with time.sleep().
Output: reports/equity_report_<timestamp>.json
"""

import json
import os
import time
import urllib.request
import urllib.error
from urllib.parse import urlencode
from datetime import datetime, timezone
from pathlib import Path

from collector_utils import _fetch_with_retry
from runtime_paths import reports_dir

BASE_URL = "https://api.twelvedata.com"

ASSETS = {
    "stock": ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "META", "AMZN"],
    "etf": ["SPY", "QQQ", "IWM", "DIA", "VIX"],
    "forex": ["EUR/USD", "GBP/USD", "USD/JPY", "BTC/USD"],
}

REPORTS_DIR = reports_dir()


def _provider_key() -> str | None:
    """Return a bounded, printable provider key without retaining it globally."""
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not isinstance(key, str) or not 1 <= len(key) <= 512:
        return None
    if not key.isascii() or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        return None
    return key


def _http_fetch_quote(symbol: str, api_key: str) -> dict:
    """Raw HTTP call for fetch_quote — raises on HTTP errors for wrapper retry."""
    url = f"{BASE_URL}/quote?{urlencode({'symbol': symbol, 'apikey': api_key})}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_quote(symbol: str) -> dict | None:
    """Fetch real-time quote for a single symbol. Falls back to finnhub/polygon on failure."""
    api_key = _provider_key()
    if api_key is None:
        from fallback import fetch_with_fallback
        return fetch_with_fallback(symbol)
    data = _fetch_with_retry("twelvedata", _http_fetch_quote, symbol, api_key)

    if data is not None and "code" in data and data["code"] != 200:
        print(f"  API error for {symbol}: {data.get('message', data)}")
        data = None

    if data is not None:
        return data

    # Fallback chain: Finnhub → Polygon
    from fallback import fetch_with_fallback
    return fetch_with_fallback(symbol)



def _http_fetch_ohlcv(symbol: str, outputsize: int, api_key: str) -> dict:
    """Raw HTTP call for fetch_ohlcv — raises on HTTP errors for wrapper retry."""
    params = {"symbol": symbol, "interval": "1day", "outputsize": outputsize, "apikey": api_key}
    url = f"{BASE_URL}/time_series?{urlencode(params)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def fetch_ohlcv(symbol: str, outputsize: int = 200) -> list[dict] | None:
    """Fetch daily OHLCV candles for a stock symbol. Returns list oldest-first."""
    api_key = _provider_key()
    if api_key is None:
        return None
    data = _fetch_with_retry("twelvedata", _http_fetch_ohlcv, symbol, outputsize, api_key)
    if data is None:
        return None

    if data.get("status") != "ok" or "values" not in data:
        print(f"  OHLCV API error for {symbol}: {data.get('message', data.get('status', 'unknown'))}")
        return None

    candles = []
    for v in reversed(data["values"]):  # API returns newest-first; reverse to oldest-first
        try:
            candles.append({
                "open":   float(v["open"]),
                "high":   float(v["high"]),
                "low":    float(v["low"]),
                "close":  float(v["close"]),
                "volume": int(float(v["volume"])),
            })
        except (KeyError, ValueError):
            continue
    return candles if candles else None


def collect_all():
    """Collect quotes for all assets and write report."""
    results = []

    for asset_class, symbols in ASSETS.items():
        print(f"\n{'='*50}")
        print(f"Collecting {asset_class} ({len(symbols)} symbols)")
        print(f"{'='*50}")

        for i, symbol in enumerate(symbols):
            print(f"  [{i+1}/{len(symbols)}] {symbol}...", end=" ", flush=True)
            data = fetch_quote(symbol)
            if data:
                # Check if result came from fallback (already formatted as entry)
                if "_fallback_used" in data:
                    entry = {
                        "symbol": symbol,
                        "asset_class": asset_class,
                        "current_price": data["current_price"],
                        "price_change_24h_pct": data["price_change_24h_pct"],
                        "volume": data["volume"],
                        "_data_source": data.get("_data_source", "fallback"),
                        "_fallback_used": True,
                    }
                else:
                    entry = {
                        "symbol": symbol,
                        "asset_class": asset_class,
                        "current_price": float(data.get("close", 0) or 0),
                        "price_change_24h_pct": float(data.get("percent_change", 0) or 0),
                        "volume": int(float(data.get("volume", 0) or 0)),
                        "_data_source": "twelve_data",
                        "_fallback_used": False,
                    }
                results.append(entry)
                print(f"${entry['current_price']} ({entry['price_change_24h_pct']:+.2f}%) [{entry.get('_data_source', '?')}]")
            else:
                print("FAILED")

            # Respect 8 calls/min rate limit: wait 8 seconds between requests
            if i < len(symbols) - 1:
                time.sleep(8)

    timestamp = datetime.now(timezone.utc).isoformat()
    report = {
        "timestamp": timestamp,
        "source": "twelve_data",
        "assets": results,
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"equity_report_{ts_str}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to {out_path}")
    print(f"Total assets collected: {len(results)}")


if __name__ == "__main__":
    collect_all()
