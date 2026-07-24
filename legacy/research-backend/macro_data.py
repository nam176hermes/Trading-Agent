"""
macro_data.py — Macroeconomic + cross-asset data fetchers.

Steals FinceptTerminal's 100+ connector concept: adds FRED, IMF,
World Bank, and yfinance cross-asset data to our trading pipeline.

All sources are FREE. No API keys required beyond what we already have.

Sources:
  FRED (via CSV)  — Fed rate, CPI, unemployment, GDP, M2, 10Y yield
  yfinance        — VIX, SPY, GLD, DXY proxy (UUP), TLT (bonds)
  CoinGecko       — Total crypto market cap, BTC dominance, fear & greed
  IMF / World Bank — Stub for now (free but complex auth)
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from runtime_paths import data_root

log = logging.getLogger("macro_data")

CACHE_DIR = data_root() / "memory" / "macro"
CACHE_TTL = 3600  # 1 hour for macro data (slow-moving)

# ── FRED Data (via CSV export — no auth needed) ────────────────

FRED_SERIES = {
    "fed_funds_rate":    "FEDFUNDS",      # Federal Funds Effective Rate
    "cpi_yoy":           "CPIAUCSL",       # CPI All Urban (need to calc YoY)
    "unemployment":      "UNRATE",         # Unemployment Rate
    "gdp":               "GDP",            # Gross Domestic Product
    "gdp_growth_q":      "GDPC1",          # Real GDP % change QoQ annualized
    "m2_money_supply":   "M2SL",           # M2 Money Supply
    "treasury_10y":      "DGS10",          # 10-Year Treasury Yield
    "treasury_2y":       "DGS2",           # 2-Year Treasury Yield
    "sp500":             "SP500",          # S&P 500 Index
    "trade_weighted_usd": "DTWEXBGS",      # Trade Weighted USD Index
}


def fetch_fred_series(series_id: str, max_obs: int = 3) -> Optional[Dict]:
    """
    Fetch latest observations from FRED via CSV export.
    Free, no API key needed for basic access.

    Returns: {"latest": float, "previous": float, "trend": "up"|"down"|"flat", "observations": [...]}
    """
    url = (
        f"https://fred.stlouisfed.org/data/{series_id}.txt"
    )
    try:
        import requests
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            log.debug("FRED %s: HTTP %d", series_id, resp.status_code)
            return None

        lines = resp.text.strip().split("\n")
        data_lines = []
        for line in lines:
            if line.startswith("DATE"):
                continue  # Header
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    date_str = parts[0]
                    value = float(parts[1].replace(",", ""))
                    data_lines.append({"date": date_str, "value": value})
                except (ValueError, IndexError):
                    continue

        if len(data_lines) < 2:
            return None

        recent = data_lines[-max_obs:]
        latest = recent[-1]["value"]
        previous = recent[-2]["value"] if len(recent) >= 2 else latest

        if latest > previous * 1.001:
            trend = "up"
        elif latest < previous * 0.999:
            trend = "down"
        else:
            trend = "flat"

        return {
            "latest": latest,
            "previous": previous,
            "trend": trend,
            "value": latest,
            "date": recent[-1]["date"],
            "observations": recent,
        }
    except Exception as e:
        log.warning("FRED %s failed: %s", series_id, e)
        return None


def fetch_fred_macro() -> Dict:
    """Fetch all FRED macro indicators. Cached 1 hour."""
    cache_file = CACHE_DIR / "fred_cache.json"
    if _cache_valid(cache_file):
        return json.loads(cache_file.read_text())

    result = {}
    for name, series_id in FRED_SERIES.items():
        data = fetch_fred_series(series_id)
        if data:
            result[name] = {
                "value": data["value"],
                "trend": data["trend"],
                "date": data.get("date", ""),
            }
        time.sleep(0.2)  # Rate limit: be nice to FRED

    # Derived: yield curve
    if "treasury_10y" in result and "treasury_2y" in result:
        spread = result["treasury_10y"]["value"] - result["treasury_2y"]["value"]
        result["yield_curve_2s10s"] = {
            "value": round(spread, 2),
            "trend": "inverted" if spread < 0 else "normal" if spread > 0.5 else "flat",
            "date": result["treasury_10y"]["date"],
        }

    if result:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, indent=2))

    return result


# ── Cross-Asset Data (via yfinance) ────────────────────────────

YFINANCE_SYMBOLS = {
    "vix":       "^VIX",      # CBOE Volatility Index
    "spy":       "^GSPC",     # S&P 500
    "gold":      "GC=F",      # Gold Futures
    "dxy_proxy": "UUP",       # USD Bullish ETF (DXY proxy)
    "bonds":     "TLT",       # 20Y+ Treasury Bond ETF
    "oil":       "CL=F",      # Crude Oil Futures
    "nasdaq":    "^IXIC",     # NASDAQ Composite
    "btc_correlation": "BTC-USD",  # For correlation calc
}


def fetch_yfinance_macro() -> Dict:
    """Fetch cross-asset data via yfinance. Cached 1 hour."""
    cache_file = CACHE_DIR / "yf_macro_cache.json"
    if _cache_valid(cache_file):
        return json.loads(cache_file.read_text())

    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — skipping cross-asset data")
        return {}

    result = {}
    try:
        tickers = yf.Tickers(" ".join(YFINANCE_SYMBOLS.values()))
    except Exception as e:
        log.warning("yfinance bulk fetch failed: %s", e)
        return {}

    for name, symbol in YFINANCE_SYMBOLS.items():
        try:
            t = tickers.tickers.get(symbol)
            if not t:
                continue
            info = t.fast_info if hasattr(t, 'fast_info') else t.info
            hist = t.history(period="5d")

            if hist.empty:
                continue

            current = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
            change_pct = ((current - prev_close) / prev_close * 100) if prev_close else 0

            # 5-day trend
            if len(hist) >= 5:
                week_ago = float(hist["Close"].iloc[-5])
                week_change = ((current - week_ago) / week_ago * 100)
            else:
                week_change = 0

            result[name] = {
                "price": round(current, 2),
                "change_24h_pct": round(change_pct, 2),
                "change_5d_pct": round(week_change, 2),
                "trend": "up" if week_change > 0.5 else ("down" if week_change < -0.5 else "flat"),
            }
        except Exception as e:
            log.debug("yfinance %s: %s", symbol, e)
            continue

    # Derived: risk-on/off signal
    if "vix" in result and "spy" in result:
        vix = result["vix"]["price"]
        result["risk_signal"] = {
            "level": "risk_on" if vix < 20 else ("neutral" if vix < 30 else "risk_off"),
            "vix": vix,
        }

    if result:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, indent=2))

    return result


# ── Crypto Market Data (via CoinGecko) ─────────────────────────

def fetch_coingecko_global() -> Dict:
    """Fetch global crypto market data from CoinGecko. Cached 1 hour."""
    cache_file = CACHE_DIR / "coingecko_global_cache.json"
    if _cache_valid(cache_file):
        return json.loads(cache_file.read_text())

    result = {}
    try:
        import requests
        # Global market data
        resp = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            mc = data.get("total_market_cap", {})
            vol = data.get("total_volume", {})
            dom = data.get("market_cap_percentage", {})

            result["total_market_cap"] = mc.get("usd", 0)
            result["total_volume_24h"] = vol.get("usd", 0)
            result["btc_dominance"] = dom.get("btc", 0)
            result["eth_dominance"] = dom.get("eth", 0)
            result["market_cap_change_24h"] = data.get("market_cap_change_percentage_24h_usd", 0)
            result["active_cryptocurrencies"] = data.get("active_cryptocurrencies", 0)

        # Fear & Greed Index (alternative.me — free, no auth)
        resp2 = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if resp2.status_code == 200:
            fng = resp2.json().get("data", [{}])[0]
            result["fear_greed_index"] = int(fng.get("value", 50))
            result["fear_greed_classification"] = fng.get("value_classification", "neutral")

        if result:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result, indent=2))

    except Exception as e:
        log.warning("CoinGecko global failed: %s", e)

    return result


# ── Aggregate Macro Snapshot ───────────────────────────────────

def get_macro_snapshot() -> Dict:
    """
    Fetch all macro data in one call.
    Returns a rich dict for the MacroAnalyst to consume.

    Keys: fred, cross_asset, crypto_global, timestamp
    """
    return {
        "fred": fetch_fred_macro(),
        "cross_asset": fetch_yfinance_macro(),
        "crypto_global": fetch_coingecko_global(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_macro_context(snapshot: Dict) -> str:
    """Format macro snapshot as a text context for LLM prompts."""
    lines = []

    # FRED
    fred = snapshot.get("fred", {})
    if fred:
        lines.append("**US Macro (FRED):**")
        indicators = []
        for name in ["fed_funds_rate", "cpi_yoy", "unemployment", "gdp_growth_q",
                      "treasury_10y", "yield_curve_2s10s"]:
            d = fred.get(name)
            if d:
                val = d["value"]
                trend = d.get("trend", "")
                if name == "fed_funds_rate":
                    indicators.append(f"Fed Rate: {val}%")
                elif name == "cpi_yoy":
                    indicators.append(f"CPI: {val}")
                elif name == "unemployment":
                    indicators.append(f"Unemployment: {val}%")
                elif name == "treasury_10y":
                    indicators.append(f"10Y Yield: {val}%")
                elif name == "yield_curve_2s10s":
                    indicators.append(f"Yield Curve: {val}bps ({trend})")
        lines.append("  " + " | ".join(indicators))

    # Cross-asset
    ca = snapshot.get("cross_asset", {})
    if ca:
        lines.append("**Cross-Asset:**")
        assets = []
        for name, label in [("vix", "VIX"), ("spy", "S&P 500"), ("gold", "Gold"),
                             ("dxy_proxy", "USD"), ("oil", "Oil")]:
            d = ca.get(name)
            if d:
                assets.append(f"{label}: {d['price']} ({d.get('change_24h_pct', 0):+.1f}%)")
        lines.append("  " + " | ".join(assets))

    # Crypto global
    cg = snapshot.get("crypto_global", {})
    if cg:
        lines.append("**Crypto Market:**")
        lines.append(f"  Total MCap: ${cg.get('total_market_cap', 0)/1e9:.0f}B | "
                     f"24h Change: {cg.get('market_cap_change_24h', 0):+.1f}%")
        lines.append(f"  BTC Dominance: {cg.get('btc_dominance', 0):.1f}% | "
                     f"Fear & Greed: {cg.get('fear_greed_index', '?')} "
                     f"({cg.get('fear_greed_classification', '?')})")

    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────

def _cache_valid(cache_file: Path) -> bool:
    """Check if cache is fresh (< 1 hour old)."""
    if not cache_file.exists():
        return False
    age = time.time() - cache_file.stat().st_mtime
    return age < CACHE_TTL


# ── Data Connector Catalog ─────────────────────────────────────

# Documenting which FinceptTerminal connectors we've adopted
# and which are available for future integration.

CONNECTOR_CATALOG = {
    "adopted": {
        "fred": {
            "source": "Federal Reserve Economic Data",
            "url": "https://fred.stlouisfed.org/",
            "data": "Fed rate, CPI, unemployment, GDP, M2, yield curve",
            "auth": "None (CSV export)",
            "cost": "Free",
        },
        "yfinance": {
            "source": "Yahoo Finance (via yfinance library)",
            "url": "https://pypi.org/project/yfinance/",
            "data": "VIX, SPY, GLD, TLT, UUP, CL=F, BTC-USD",
            "auth": "None",
            "cost": "Free",
        },
        "coingecko_global": {
            "source": "CoinGecko API",
            "url": "https://www.coingecko.com/en/api",
            "data": "Total market cap, BTC dominance, volume",
            "auth": "None (free tier)",
            "cost": "Free (10-30 calls/min)",
        },
        "fear_greed": {
            "source": "Alternative.me Fear & Greed Index",
            "url": "https://alternative.me/crypto/fear-and-greed-index/",
            "data": "Crypto Fear & Greed (0-100)",
            "auth": "None",
            "cost": "Free",
        },
    },
    "available_future": {
        "dbnomics": {
            "source": "DBnomics",
            "url": "https://db.nomics.world/",
            "data": "200M+ series from 100+ providers (IMF, WB, Eurostat, BIS)",
            "auth": "None (free API)",
            "cost": "Free",
            "priority": "high",
        },
        "imf_weo": {
            "source": "IMF World Economic Outlook",
            "url": "https://www.imf.org/en/Publications/SPROLLs/world-economic-outlook-databases",
            "data": "GDP forecasts, inflation forecasts, current account",
            "auth": "None (CSV download)",
            "cost": "Free",
            "priority": "medium",
        },
        "world_bank": {
            "source": "World Bank API",
            "url": "https://data.worldbank.org/",
            "data": "Global GDP, population, development indicators",
            "auth": "None",
            "cost": "Free",
            "priority": "low",
        },
        "polygon": {
            "source": "Polygon.io",
            "url": "https://polygon.io/",
            "data": "Real-time stocks, forex, crypto; WebSocket",
            "auth": "API key (free tier: 5 calls/min)",
            "cost": "Free tier available",
            "priority": "medium",
        },
    },
}


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Macro Data Fetchers — FRED + yfinance + CoinGecko")
    parser.add_argument("--fetch", action="store_true", help="Fetch fresh macro snapshot")
    parser.add_argument("--context", action="store_true", help="Show LLM-ready context string")
    parser.add_argument("--catalog", action="store_true", help="Show connector catalog")
    parser.add_argument("--json", action="store_true", help="Output snapshot as JSON")
    args = parser.parse_args()

    if args.catalog:
        print("=== Adopted Connectors ===")
        for name, info in CONNECTOR_CATALOG["adopted"].items():
            print(f"  {name}: {info['data']} ({info['cost']})")
        print("\n=== Available for Future ===")
        for name, info in sorted(
            CONNECTOR_CATALOG["available_future"].items(),
            key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x[1]["priority"], 9)
        ):
            print(f"  [{info['priority']}] {name}: {info['data']} ({info['cost']})")

    elif args.fetch or args.json or args.context:
        print("Fetching macro data...")
        snapshot = get_macro_snapshot()

        if args.json:
            print(json.dumps(snapshot, indent=2, default=str))
        elif args.context:
            print(format_macro_context(snapshot))
        else:
            print(format_macro_context(snapshot))

    else:
        parser.print_help()
