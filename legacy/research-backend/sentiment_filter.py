"""
sentiment_filter.py
-------------------
Quality-weighted sentiment analysis for crypto news.

Pipeline:
  1. Source Quality Filter  — assigns credibility tier to each Exa result
  2. Claude Haiku interpreter — reads each article in context, scores sentiment
  3. Weighted Aggregator — combines scores weighted by source tier

Replaces the shallow Exa keyword scan in main.py's fetch_sentiment() stub.

Requires: aiohttp
Model used: deepseek-v4-flash (OpenAI-compatible API) — fast and cost-effective for classification tasks
"""

import asyncio
import aiohttp
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from runtime_paths import configured_env_file, data_root

env_file = configured_env_file()
if env_file is not None:
    from dotenv import load_dotenv
    load_dotenv(env_file)

log = logging.getLogger("sentiment_filter")

LOG_DIR = data_root() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

RAW_LOG_PATH = LOG_DIR / "raw_responses.jsonl"

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL   = "deepseek-v4-flash"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

EXA_API_URL = "https://api.exa.ai/search"
EXA_API_KEY = os.getenv("EXA_API_KEY", "")


# ── Source tier definitions ───────────────────────────────────────────────────

# Tier 1: High credibility — established financial/crypto journalism
TIER_1_DOMAINS = {
    "coindesk.com", "cointelegraph.com", "theblock.co", "decrypt.co",
    "bloomberg.com", "reuters.com", "ft.com", "wsj.com",
    "financialtimes.com", "cryptoslate.com", "blockworks.co",
    "axios.com", "theinformation.com",
}

# Tier 2: Medium credibility — reputable but lower editorial bar
TIER_2_DOMAINS = {
    "beincrypto.com", "newsbtc.com", "bitcoinmagazine.com",
    "cryptobriefing.com", "ambcrypto.com", "u.today",
    "coinjournal.net", "cryptonews.com", "coingape.com",
    "binance.com",    # exchange blog — useful but self-interested
    "coinbase.com",
}

# Filtered out entirely — promotional or unreliable by definition
FILTER_OUT_DOMAINS = {
    "medium.com", "substack.com",
    "prnewswire.com", "businesswire.com", "globenewswire.com",  # press releases
    "reddit.com", "twitter.com", "x.com",                       # too noisy for article-level scoring
}

# Promotional content patterns — filtered regardless of source
FILTER_PATTERNS = [
    r"\bpress release\b", r"\bsponsored\b", r"\bpartnership announced\b",
    r"\blaunches new\b", r"\bexciting new\b", r"\bproud to announce\b",
]


# ── Logging helper ────────────────────────────────────────────────────────────

def log_raw(source: str, endpoint: str, payload: dict):
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "endpoint": endpoint,
        "payload": payload,
    }
    with open(RAW_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Null safe default ─────────────────────────────────────────────────────────

def null_sentiment(symbol: str, reason: str) -> dict:
    log.warning("NULL SENTIMENT [%s] — %s", symbol, reason)
    return {
        "sentiment":         None,
        "sentiment_score":   None,
        "sentiment_source":  f"unavailable — {reason}",
        "sentiment_summary": None,
        "articles_found":    0,
        "articles_scored":   0,
        "articles_filtered": 0,
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
    }


# ── Source quality filter ─────────────────────────────────────────────────────

def get_source_tier(url: str, title: str = "") -> int:
    """
    Returns credibility tier for an article source.
      1 = high credibility (weight 3×)
      2 = medium credibility (weight 2×)
      3 = low credibility (weight 1×)
      0 = filtered out entirely

    Priority: filter-out check first, then tier assignment.
    Unknown domains default to Tier 3.
    """
    if not url:
        return 3

    # Extract domain
    domain = url.lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.split("/")[0]

    # Hard filter — exclude entirely
    if domain in FILTER_OUT_DOMAINS:
        return 0

    # Check title/description for promotional patterns
    if title:
        for pattern in FILTER_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                log.debug("Filtered promotional content: '%s'", title[:60])
                return 0

    # Tier assignment
    if domain in TIER_1_DOMAINS:
        return 1
    elif domain in TIER_2_DOMAINS:
        return 2

    # Unknown domain — keep but weight low
    return 3


def tier_to_weight(tier: int) -> int:
    return {1: 3, 2: 2, 3: 1}.get(tier, 1)

def tier_to_label(tier: int) -> str:
    return {1: "high", 2: "medium", 3: "low"}.get(tier, "unknown")


# ── Exa news fetcher ──────────────────────────────────────────────────────────

async def fetch_exa_articles(
    session: aiohttp.ClientSession,
    symbol: str,
    num_results: int = 5,
    days_back: int = 2,
) -> list[dict]:
    """
    Fetches recent news articles for a symbol from Exa.
    Returns list of article dicts (url, title, text snippet, published_date).
    Returns empty list on failure — never raises.
    """
    if not EXA_API_KEY:
        log.warning("EXA_API_KEY not set. Sentiment fetch skipped.")
        return []

    # Map symbols to more searchable terms
    search_terms = {
        "BTC":  "Bitcoin BTC price",
        "ETH":  "Ethereum ETH price",
        "SOL":  "Solana SOL price",
        "TON":  "Toncoin TON crypto",
        "DOGE": "Dogecoin DOGE price",
    }
    query = search_terms.get(symbol, f"{symbol} cryptocurrency")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": EXA_API_KEY,
    }
    payload = {
        "query": query,
        "num_results": num_results,
        "type": "neural",
        "contents": {
            "text": {"max_characters": 800},   # snippet only — enough for sentiment
        },
        "start_published_date": _days_ago_iso(days_back),
    }

    try:
        async with session.post(
            EXA_API_URL,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                log.error("Exa HTTP %s for %s", resp.status, symbol)
                return []

            data = await resp.json()
            results = data.get("results", [])
            log_raw("exa", EXA_API_URL, {"symbol": symbol, "article_count": len(results)})
            log.info("[%s] Exa returned %d articles.", symbol, len(results))
            return results

    except asyncio.TimeoutError:
        log.error("Exa request timed out for %s.", symbol)
        return []
    except Exception as e:
        log.error("Exa fetch error for %s: %s", symbol, e)
        return []


def _days_ago_iso(days: int) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Claude sentiment scorer ───────────────────────────────────────────────────

SENTIMENT_SYSTEM_PROMPT = """You are a financial sentiment analyst specializing in cryptocurrency markets.

Your job: read a news article snippet and return a precise sentiment classification in JSON.

Rules:
- Be conservative. Only mark "positive" or "negative" if the article clearly supports that direction for the asset's PRICE.
- "neutral" = factual reporting, regulatory news without clear price impact, general market commentary.
- "mixed" = article contains both bullish and bearish elements of roughly equal weight.
- Key claim: one sentence max, stating the most price-relevant fact in the article.
- Do not infer. Do not project. Only assess what is explicitly stated.
- Ignore price predictions from unknown analysts. Only weight predictions from named, credible sources.

Respond ONLY with valid JSON. No preamble, no markdown, no explanation outside the JSON.

Required format:
{
  "sentiment": "positive" | "negative" | "neutral" | "mixed",
  "direction_strength": "strong" | "moderate" | "weak",
  "key_claim": "one sentence stating the most price-relevant fact",
  "confidence": "high" | "medium" | "low",
  "reasoning": "one sentence explaining your sentiment classification"
}"""


async def score_article_sentiment(
    session: aiohttp.ClientSession,
    symbol: str,
    title: str,
    text: str,
    source_url: str,
) -> Optional[dict]:
    """
    Sends a single article to Claude Haiku for sentiment classification.
    Returns parsed JSON dict or None on failure.
    """
    user_message = f"""Asset: {symbol}
Source: {source_url}
Title: {title}

Article excerpt:
{text[:700]}

Classify the sentiment of this article for {symbol}'s price outlook."""

    if not DEEPSEEK_API_KEY:
        log.warning("DEEPSEEK_API_KEY not set. Article scoring skipped.")
        return None

    payload = {
        "model":       DEEPSEEK_MODEL,
        "max_tokens":  256,
        "temperature": 1.0,   # DeepSeek official recommended default
        "top_p":       1.0,
        "messages": [
            {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    }

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }

    try:
        async with session.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                log.error("DeepSeek API HTTP %s for %s article scoring.", resp.status, symbol)
                return None

            data = await resp.json()
            raw_text = data["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if present
            raw_text = re.sub(r"^```json\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$",     "", raw_text)

            parsed = json.loads(raw_text)
            log.debug("[%s] Article scored: %s (%s)",
                      symbol, parsed.get("sentiment"), parsed.get("direction_strength"))
            return parsed

    except json.JSONDecodeError as e:
        log.error("Failed to parse DeepSeek sentiment JSON for %s: %s", symbol, e)
        return None
    except asyncio.TimeoutError:
        log.error("DeepSeek sentiment scoring timed out for %s.", symbol)
        return None
    except Exception as e:
        log.error("DeepSeek article scoring error for %s: %s", symbol, e)
        return None


# ── Weighted aggregator ───────────────────────────────────────────────────────

SENTIMENT_SCORE_MAP = {
    ("positive", "strong"):   3,
    ("positive", "moderate"): 2,
    ("positive", "weak"):     1,
    ("neutral",  "strong"):   0,
    ("neutral",  "moderate"): 0,
    ("neutral",  "weak"):     0,
    ("mixed",    "strong"):   0,
    ("mixed",    "moderate"): 0,
    ("mixed",    "weak"):     0,
    ("negative", "weak"):    -1,
    ("negative", "moderate"):-2,
    ("negative", "strong"):  -3,
}

def aggregate_sentiment(scored_articles: list[dict]) -> dict:
    """
    Aggregates individual article scores into a final weighted sentiment signal.

    Each article has:
      - score: numeric (-3 to +3)
      - weight: tier weight (1, 2, or 3)
      - confidence: "high" / "medium" / "low" — scales final confidence

    Returns final sentiment dict ready for assembly.py.
    """
    if not scored_articles:
        return {
            "sentiment":       "neutral",
            "sentiment_score": 0,
            "sentiment_note":  "No articles scored.",
        }

    total_weight   = 0
    weighted_score = 0
    key_claims     = []
    sentiments     = []

    for article in scored_articles:
        score     = article.get("numeric_score", 0)
        weight    = article.get("weight", 1)
        sentiment = article.get("sentiment", "neutral")
        claim     = article.get("key_claim", "")
        title     = article.get("title", "")

        weighted_score += score * weight
        total_weight   += weight
        sentiments.append(sentiment)

        if claim and sentiment in ("positive", "negative", "mixed"):
            source_label = article.get("source_label", "unknown")
            key_claims.append(f"[{source_label}] {claim}")

    if total_weight == 0:
        normalized = 0.0
    else:
        normalized = weighted_score / total_weight

    # Convert normalized score to label
    if normalized >= 1.5:
        final_sentiment = "positive"
    elif normalized <= -1.5:
        final_sentiment = "negative"
    elif -0.5 <= normalized <= 0.5:
        final_sentiment = "neutral"
    else:
        final_sentiment = "mixed"

    # Count sentiment distribution
    sentiment_counts = {s: sentiments.count(s) for s in set(sentiments)}

    # Build summary
    summary_parts = []
    if key_claims:
        summary_parts.append("Key findings: " + " | ".join(key_claims[:3]))  # top 3
    summary_parts.append(
        f"Distribution: {sentiment_counts}. "
        f"Weighted score: {normalized:.2f}."
    )

    return {
        "sentiment":              final_sentiment,
        "sentiment_score":        round(normalized, 2),
        "sentiment_distribution": sentiment_counts,
        "sentiment_summary":      " ".join(summary_parts),
    }


# ── Main entry point (replaces fetch_sentiment stub in main.py) ───────────────

async def fetch_sentiment(
    symbol: str,
    num_articles: int = 5,
    min_tier: int = 3,       # 1=only tier1, 2=tier1+2, 3=all tiers (default)
    min_articles_to_score: int = 1,
) -> dict:
    """
    Full sentiment pipeline for one symbol.
    Drop-in replacement for the stub fetch_sentiment() in main.py.

    Args:
        symbol:                 e.g. "BTC"
        num_articles:           how many Exa results to request (default 5)
        min_tier:               lowest tier to include (default 3 = include all)
        min_articles_to_score:  minimum articles needed to return a non-null result

    Returns null-safe dict ready for assemble_asset_json().
    """
    async with aiohttp.ClientSession() as session:

        # ── Step 1: Fetch articles from Exa ──
        raw_articles = await fetch_exa_articles(session, symbol, num_results=num_articles)

        if not raw_articles:
            return null_sentiment(symbol, "Exa returned no articles")

        # ── Step 2: Apply source quality filter ──
        filtered = []
        filter_count = 0

        for article in raw_articles:
            url   = article.get("url", "")
            title = article.get("title", "")
            tier  = get_source_tier(url, title)

            if tier == 0:
                log.info("[%s] Filtered out: %s", symbol, url[:60])
                filter_count += 1
                continue

            if tier > min_tier:
                log.debug("[%s] Tier %d below threshold %d, skipping: %s",
                          symbol, tier, min_tier, url[:60])
                filter_count += 1
                continue

            filtered.append({
                "url":          url,
                "title":        title,
                "text":         article.get("text", ""),
                "published":    article.get("publishedDate", ""),
                "tier":         tier,
                "tier_label":   tier_to_label(tier),
                "weight":       tier_to_weight(tier),
                "source_label": _extract_domain_label(url),
            })

        log.info("[%s] %d articles after filtering (%d filtered out).",
                 symbol, len(filtered), filter_count)

        if len(filtered) < min_articles_to_score:
            return null_sentiment(
                symbol,
                f"only {len(filtered)} articles passed quality filter (min: {min_articles_to_score})"
            )

        # ── Step 3: Score each article with Claude ──
        # Sequential to avoid rate-limiting the Anthropic API
        scored = []
        for article in filtered:
            result = await score_article_sentiment(
                session,
                symbol,
                title      = article["title"],
                text       = article["text"],
                source_url = article["url"],
            )

            if result is None:
                log.warning("[%s] Skipping unscored article: %s", symbol, article["title"][:60])
                continue

            # Merge article metadata with Claude's classification
            scored.append({
                **article,
                "sentiment":        result.get("sentiment", "neutral"),
                "direction_strength": result.get("direction_strength", "weak"),
                "key_claim":        result.get("key_claim", ""),
                "claude_reasoning": result.get("reasoning", ""),
                "claude_confidence": result.get("confidence", "low"),
                "numeric_score":    SENTIMENT_SCORE_MAP.get(
                    (result.get("sentiment", "neutral"),
                     result.get("direction_strength", "weak")), 0
                ),
            })

        if not scored:
            return null_sentiment(symbol, "all articles failed Claude scoring")

        # ── Step 4: Aggregate weighted scores ──
        aggregated = aggregate_sentiment(scored)

        # Build source list for audit trail
        source_list = ", ".join(
            f"{a['source_label']} (tier {a['tier']})"
            for a in scored
        )
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        log.info("[%s] Final sentiment: %s (score: %s) from %d articles",
                 symbol,
                 aggregated["sentiment"],
                 aggregated["sentiment_score"],
                 len(scored))

        return {
            "sentiment":              aggregated["sentiment"],
            "sentiment_score":        aggregated["sentiment_score"],
            "sentiment_distribution": aggregated.get("sentiment_distribution"),
            "sentiment_summary":      aggregated.get("sentiment_summary"),
            "sentiment_source":       f"Exa + DeepSeek v4 Flash — {len(scored)} articles, {date_str} | Sources: {source_list}",
            "articles_found":         len(raw_articles),
            "articles_scored":        len(scored),
            "articles_filtered":      filter_count,
            "fetched_at":             datetime.now(timezone.utc).isoformat(),
        }


# ── Utility ───────────────────────────────────────────────────────────────────

def _extract_domain_label(url: str) -> str:
    domain = re.sub(r"^https?://", "", url.lower())
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.split("/")[0]
    # Strip TLD for cleaner label (coindesk.com → coindesk)
    parts = domain.split(".")
    return parts[-2] if len(parts) >= 2 else domain


# ── Standalone test (no live API required for filter logic) ───────────────────

if __name__ == "__main__":

    print("── Source Tier Tests ──")
    test_urls = [
        ("https://coindesk.com/markets/2026/04/28/btc-rally",        "BTC hits new high"),
        ("https://cointelegraph.com/news/eth-upgrade",               "ETH upgrade confirmed"),
        ("https://medium.com/@anon/why-bitcoin-is-going-to-1m",      "Why Bitcoin is going to $1M"),
        ("https://prnewswire.com/news/company-xyz-launches-token",   "Company XYZ launches new token"),
        ("https://beincrypto.com/sol-analysis",                      "SOL technical analysis"),
        ("https://unknownblog.xyz/crypto-tips",                      "10 crypto tips"),
        ("https://binance.com/blog/markets/update",                  "Binance market update"),
        ("https://reuters.com/markets/crypto/btc-2026",              "Reuters: BTC market outlook"),
    ]

    for url, title in test_urls:
        tier   = get_source_tier(url, title)
        weight = tier_to_weight(tier)
        label  = tier_to_label(tier) if tier > 0 else "FILTERED"
        domain = _extract_domain_label(url)
        print(f"  Tier {tier} ({label:8s}) weight={weight}  {domain:20s}  '{title[:40]}'")

    print()
    print("── Aggregation Test ──")
    mock_scored = [
        {"sentiment": "positive", "direction_strength": "strong",   "weight": 3,
         "numeric_score": 3, "key_claim": "BTC breaks $100k resistance on institutional buying.",
         "source_label": "coindesk", "title": "BTC ATH"},
        {"sentiment": "negative", "direction_strength": "moderate", "weight": 2,
         "numeric_score": -2, "key_claim": "Regulatory concern raises sell pressure.",
         "source_label": "cointelegraph", "title": "SEC concerns"},
        {"sentiment": "neutral",  "direction_strength": "weak",     "weight": 1,
         "numeric_score": 0, "key_claim": "Bitcoin trading volume stable.",
         "source_label": "unknownblog", "title": "Volume update"},
    ]
    result = aggregate_sentiment(mock_scored)
    print(f"  Final sentiment:  {result['sentiment']}")
    print(f"  Weighted score:   {result['sentiment_score']}")
    print(f"  Distribution:     {result['sentiment_distribution']}")
    print(f"  Summary: {result['sentiment_summary'][:120]}")
