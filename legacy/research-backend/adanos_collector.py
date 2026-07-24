"""
adanos_collector.py — Social sentiment data via Adanos Market Sentiment API.
Free tier: 250 req/month. Fetches Reddit crypto + X/Twitter + Polymarket sentiment.
Requires ADANOS_API_KEY in the process environment or configured runtime env file.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from collector_utils import _fetch_with_retry
from runtime_paths import configured_env_file, reports_dir

ADANOS_BASE = "https://api.adanos.org"
REPORTS_DIR = reports_dir()
CRYPTO_TOKENS = ["BTC", "ETH", "SOL", "DOGE", "ADA"]


def _get_key():
    key = os.getenv("ADANOS_API_KEY")
    if not key:
        env_file = configured_env_file()
        if env_file is not None:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            key = os.getenv("ADANOS_API_KEY")
    if not key:
        print("[adanos] ADANOS_API_KEY not set — skipping. Sign up at https://adanos.org")
        return None
    return key


def _fetch_json(url, api_key):
    req = urllib.request.Request(
        url,
        headers={"X-API-Key": api_key, "User-Agent": "trading-agent/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def collect():
    key = _get_key()
    if not key:
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    results = {}

    # 1. Reddit crypto sentiment — trending tokens
    def _fetch_reddit():
        url = f"{ADANOS_BASE}/reddit/crypto/v1/trending"
        return _fetch_json(url, key)

    reddit = _fetch_with_retry("adanos-reddit", _fetch_reddit)
    if reddit:
        results["reddit_crypto"] = reddit

    # 2. X/Twitter sentiment for tracked tokens
    def _fetch_x():
        url = f"{ADANOS_BASE}/x/stocks/v1/trending"
        return _fetch_json(url, key)

    x_data = _fetch_with_retry("adanos-x", _fetch_x)
    if x_data:
        results["x_twitter"] = x_data

    # 3. Polymarket conviction
    def _fetch_poly():
        url = f"{ADANOS_BASE}/polymarket/stocks/v1/trending"
        return _fetch_json(url, key)

    poly = _fetch_with_retry("adanos-polymarket", _fetch_poly)
    if poly:
        results["polymarket"] = poly

    if not results:
        print("[adanos] no data collected")
        return None

    report = {
        "source": "adanos",
        "collected_at": timestamp,
        "sections": list(results.keys()),
        "data": results,
    }

    out_path = REPORTS_DIR / f"social_sentiment_{timestamp}.json"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"[adanos] saved {len(results)} sections → {out_path.name}")
    return report


if __name__ == "__main__":
    collect()
