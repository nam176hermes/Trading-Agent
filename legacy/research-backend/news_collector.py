#!/usr/bin/env python3
"""News + sentiment collector — Marketaux API.

Output: reports/news_report_<timestamp>.json

Rate limit: 2s between API calls (free tier).
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import reports_dir
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from collector_utils import _fetch_with_retry

BASE_URL = "https://api.marketaux.com/v1/news/all"

OUTPUT_DIR = reports_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc).isoformat()

# Assets to fetch news for
EQUITY_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "META", "AMZN"]
CRYPTO_SYMBOLS = ["BTC", "ETH"]
MACRO_QUERY = "Federal Reserve interest rate economy"


def _http_fetch_news(url: str) -> dict:
    """Raw HTTP GET for Marketaux — raises on errors for wrapper retry."""
    try:
        parsed = urlsplit(url)
        allowed = (
            parsed.scheme == "https"
            and parsed.hostname == "api.marketaux.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and parsed.path == "/v1/news/all"
            and not parsed.fragment
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise ValueError("Marketaux endpoint is not allowed")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def _provider_key() -> str | None:
    """Return a bounded, printable provider key without retaining it globally."""
    key = os.environ.get("MARKETAUX_API_KEY")
    if not isinstance(key, str) or not 1 <= len(key) <= 512:
        return None
    if not key.isascii() or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        return None
    return key


def fetch_news(symbols: str | None = None, search: str | None = None, limit: int = 5) -> dict | None:
    key = _provider_key()
    if key is None:
        return None
    params: dict[str, str | int | bool] = {
        "filter_entities": "true",
        "language": "en",
        "limit": limit,
        "api_token": key,
    }
    if symbols:
        params["symbols"] = symbols
    if search:
        params["search"] = search

    url = f"{BASE_URL}?{urlencode(params)}"
    return _fetch_with_retry("marketaux", _http_fetch_news, url)


def sentiment_label(score: float) -> str:
    if score > 0.3:
        return "positive"
    elif score < -0.3:
        return "negative"
    return "neutral"


def parse_articles(data: dict, default_symbol: str) -> list[dict]:
    articles = []
    for item in data.get("data", []):
        entities = item.get("entities") or []
        entity_names = []
        article_symbol = default_symbol
        sentiment_score = 0.0

        for ent in entities:
            ent_name = ent.get("name", "")
            if ent_name:
                entity_names.append(ent_name)
            if ent.get("symbol", "").upper() == default_symbol.upper():
                sentiment_score = ent.get("sentiment_score") or 0.0

        # If no matching entity, use first entity's score
        if sentiment_score == 0.0 and entities:
            sentiment_score = entities[0].get("sentiment_score") or 0.0
            article_symbol = entities[0].get("symbol", default_symbol)

        articles.append({
            "symbol": article_symbol.upper(),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source", ""),
            "published_at": item.get("published_at", ""),
            "sentiment": sentiment_label(sentiment_score),
            "sentiment_score": round(sentiment_score, 4),
            "entities": entity_names[:5],
        })

    return articles


def compute_summary(articles: list[dict]) -> dict:
    by_symbol: dict[str, dict[str, list]] = {}
    for a in articles:
        sym = a["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {"scores": [], "labels": []}
        by_symbol[sym]["scores"].append(a["sentiment_score"])
        by_symbol[sym]["labels"].append(a["sentiment"])

    summary = {}
    for sym, data in sorted(by_symbol.items()):
        labels = data["labels"]
        scores = data["scores"]
        summary[sym] = {
            "positive": labels.count("positive"),
            "negative": labels.count("negative"),
            "neutral": labels.count("neutral"),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        }

    return summary


def collect_news() -> dict:
    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    all_symbols = EQUITY_SYMBOLS + CRYPTO_SYMBOLS
    tasks = [(sym, sym, None) for sym in all_symbols] + [(None, "MACRO", MACRO_QUERY)]

    for i, (symbols_param, label, search_param) in enumerate(tasks):
        if i > 0:
            time.sleep(2)

        print(f"  Fetching news for {label} ({i + 1}/{len(tasks)})...")
        data = fetch_news(symbols=symbols_param, search=search_param, limit=5)

        if data and data.get("data"):
            default_sym = symbols_param or "MACRO"
            articles = parse_articles(data, default_sym)
            for a in articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    all_articles.append(a)
            print(f"    Got {len(articles)} articles")
        else:
            print(f"    No articles returned")

    summary = compute_summary(all_articles)

    return {
        "timestamp": NOW,
        "source": "marketaux",
        "articles": all_articles,
        "sentiment_summary": summary,
    }


def main():
    print("Starting news collection...")
    report = collect_news()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"news_report_{ts}.json"

    # Keep last 5 news reports
    old_reports = sorted(OUTPUT_DIR.glob("news_report_*.json"))
    for old in old_reports[:-4]:
        old.unlink(missing_ok=True)

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    total = len(report["articles"])
    symbols_with_news = len(report["sentiment_summary"])
    print(f"\nNews report written to {out_path}")
    print(f"  Articles: {total}")
    print(f"  Symbols covered: {symbols_with_news}")
    for sym, s in sorted(report["sentiment_summary"].items()):
        print(f"    {sym}: {s['positive']}P/{s['negative']}N/{s['neutral']}U (avg: {s['avg_score']:+.2f})")


if __name__ == "__main__":
    main()
