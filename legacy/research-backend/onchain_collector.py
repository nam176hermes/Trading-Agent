#!/usr/bin/env python3
"""
onchain_collector.py — On-chain crypto metrics collector.

Collects blockchain-specific signals:
- Exchange reserves (inflow/outflow) via CoinGecko API
- Whale transaction count (>$1M) where available
- Funding rate from derivatives data
- Active addresses, transaction volume

Output: reports/onchain_report_<timestamp>.json

Wire into assembly.py: onchain score x 0.10 weight in final signal.
If no onchain data available: skip gracefully, don't block signals.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import reports_dir as runtime_reports_dir
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

log = logging.getLogger("onchain_collector")

OUTPUT_DIR = runtime_reports_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc).isoformat()

# CoinGecko API base
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Crypto assets to track (CoinGecko IDs)
CRYPTO_ASSETS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "TON": "the-open-network",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "MATIC": "matic-network",
}


def _http_fetch_json(url: str, timeout: int = 15) -> dict | None:
    """Raw HTTP GET — returns dict or None on failure."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        log.warning("HTTP fetch failed for %s: %s", url[:80], e)
        return None
    except Exception as e:
        log.warning("Unexpected error fetching %s: %s", url[:80], e)
        return None


def fetch_exchange_reserves(cg_id: str) -> Optional[dict]:
    """
    Fetch exchange reserves data from CoinGecko.
    Uses the exchange volume/trust data as a proxy for reserves.
    Returns dict with exchange data or None.
    """
    # CoinGecko /exchanges endpoint gives exchange-specific data
    url = f"{COINGECKO_BASE}/exchanges"
    data = _http_fetch_json(url, timeout=20)
    if not data:
        return None

    # Get top exchanges by volume as reserve proxy
    exchanges = []
    for ex in data[:10]:  # top 10 exchanges
        exch_data = {
            "name": ex.get("name", ""),
            "trust_score": ex.get("trust_score"),
            "trade_volume_24h_btc": ex.get("trade_volume_24h_btc"),
            "year_established": ex.get("year_established"),
        }
        exchanges.append(exch_data)

    return {
        "exchanges_tracked": len(exchanges),
        "top_exchanges": exchanges,
    }


def fetch_coin_community_data(cg_id: str) -> Optional[dict]:
    """
    Fetch community and developer data from CoinGecko /coins/{id} endpoint.
    This provides active addresses proxy, developer activity, etc.
    """
    url = f"{COINGECKO_BASE}/coins/{cg_id}?localization=false&tickers=false&market_data=false&community_data=true&developer_data=true"
    data = _http_fetch_json(url, timeout=15)
    if not data:
        return None

    community = data.get("community_data", {})
    developer = data.get("developer_data", {})

    return {
        "twitter_followers": community.get("twitter_followers"),
        "reddit_subscribers": community.get("reddit_subscribers"),
        "reddit_active_accounts": community.get("reddit_average_posts_48h"),
        "telegram_channel_user_count": community.get("telegram_channel_user_count"),
        "github_stars": developer.get("stars"),
        "github_forks": developer.get("forks"),
        "github_commits_4w": developer.get("commit_count_4_weeks"),
    }


def fetch_derivatives_onchain(symbol: str) -> Optional[dict]:
    """
    Get funding rate and open interest data from derivatives_collector output.
    Serves as on-chain proxy for exchange flow.
    """
    import glob
    from pathlib import Path as _Path
    reports_dir = runtime_reports_dir()
    # Check derivatives report
    deriv_files = sorted(glob.glob(str(reports_dir / "derivatives_report_*.json")))
    if not deriv_files:
        return None

    try:
        report = json.loads(_Path(deriv_files[-1]).read_text())
    except Exception:
        return None

    assets = report.get("assets", [])
    for a in assets:
        if a.get("symbol", "").upper() == symbol.upper():
            return {
                "funding_rate": a.get("funding_rate"),
                "funding_rate_pct": a.get("funding_rate_pct"),
                "open_interest_usd": a.get("open_interest_usd"),
                "oi_change_pct": a.get("oi_change_pct"),
            }

    return None


def assess_onchain_risk(community_data: Optional[dict], derivatives_data: Optional[dict]) -> str:
    """
    Assess on-chain risk level based on available data.
    Returns 'low', 'medium', 'high', or 'critical'.
    """
    risk_score = 0

    # Funding rate extremes
    if derivatives_data:
        fr = derivatives_data.get("funding_rate_pct")
        if fr is not None:
            if abs(fr) > 0.1:  # >0.1% = very elevated
                risk_score += 3
            elif abs(fr) > 0.05:
                risk_score += 1

        oi_change = derivatives_data.get("oi_change_pct")
        if oi_change is not None:
            if oi_change > 20:  # rapid OI increase = potential squeeze
                risk_score += 2
            elif oi_change < -15:  # rapid OI decrease = capitulation
                risk_score += 2

    if risk_score >= 4:
        return "critical"
    elif risk_score >= 2:
        return "high"
    elif risk_score >= 1:
        return "medium"
    return "low"


def collect_onchain() -> dict:
    """Main on-chain collection pipeline."""
    assets = {}

    for symbol, cg_id in CRYPTO_ASSETS.items():
        try:
            community = fetch_coin_community_data(cg_id)
            derivatives = fetch_derivatives_onchain(symbol)
            risk = assess_onchain_risk(community, derivatives)

            assets[symbol] = {
                "symbol": symbol,
                "cg_id": cg_id,
                "community": community,
                "derivatives": derivatives,
                "onchain_risk": risk,
                "onchain_source": "coingecko" if community else ("derivatives" if derivatives else "unavailable"),
            }
        except Exception as e:
            log.warning("On-chain collection failed for %s: %s", symbol, e)
            assets[symbol] = {
                "symbol": symbol,
                "cg_id": cg_id,
                "onchain_risk": "unknown",
                "onchain_source": f"unavailable — {e}",
            }

    # Exchange-level data
    try:
        exchange_data = fetch_exchange_reserves("bitcoin")  # global exchange data
    except Exception:
        exchange_data = None

    # Summary
    risk_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0, "unknown": 0}
    for a in assets.values():
        r = a.get("onchain_risk", "unknown")
        risk_counts[r] = risk_counts.get(r, 0) + 1

    return {
        "timestamp": NOW,
        "source": "coingecko+derivatives",
        "exchange_data": exchange_data,
        "assets": assets,
        "summary": risk_counts,
    }


def get_onchain_for_asset(symbol: str) -> dict:
    """
    Get on-chain data for a specific asset from the latest on-chain report.
    Used by assembly.py signal scoring.
    Returns dict with onchain_risk and source.
    """
    onchain_files = sorted(OUTPUT_DIR.glob("onchain_report_*.json"))
    if not onchain_files:
        return {"onchain_risk": None, "onchain_source": "unavailable — no onchain data"}

    try:
        report = json.loads(onchain_files[-1].read_text())
    except Exception:
        return {"onchain_risk": None, "onchain_source": "unavailable"}

    asset_data = report.get("assets", {}).get(symbol.upper(), {})
    if not asset_data:
        return {"onchain_risk": None, "onchain_source": "unavailable — not found"}

    return {
        "onchain_risk": asset_data.get("onchain_risk"),
        "onchain_source": asset_data.get("onchain_source", "onchain_collector"),
    }


def main():
    print("Starting on-chain collection...")
    report = collect_onchain()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"onchain_report_{ts}.json"

    # Keep last 10 on-chain reports
    old_reports = sorted(OUTPUT_DIR.glob("onchain_report_*.json"))
    for old in old_reports[:-9]:
        old.unlink(missing_ok=True)

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"On-chain report written to {out_path}")
    print(f"  Assets tracked: {len(report.get('assets', {}))}")
    print(f"  Risk distribution: {report.get('summary', {})}")
    for sym, data in sorted(report.get("assets", {}).items()):
        print(f"    {sym}: risk={data.get('onchain_risk', '?')} | source={data.get('onchain_source', '?')}")


if __name__ == "__main__":
    main()
