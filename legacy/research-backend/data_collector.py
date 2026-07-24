"""
data_collector.py
-----------------
Async data collection module for crypto trading research agent.
Sources: CoinGecko (prices/meta) + Binance (OHLCV/order book)
All raw responses are logged. Null is returned — never fabricated — when data is unavailable.
"""

import asyncio
import aiohttp
import logging
import json
import os
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from collector_utils import _async_fetch_with_retry

# Import vendor routing with fallback chain
from data_vendors import route_to_vendor, RateLimitError
from job_attribution import strict_worker_invocation
from runtime_paths import data_root, reports_dir as runtime_reports_dir

# ── Logging setup ────────────────────────────────────────────────────────────

LOG_DIR = data_root() / "logs"
_STRICT_WORKER = strict_worker_invocation()
if _STRICT_WORKER is None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_HANDLERS: list[logging.Handler] = [logging.StreamHandler()]
if _STRICT_WORKER is None:
    _LOG_HANDLERS.insert(0, logging.FileHandler(LOG_DIR / "collector.log"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_LOG_HANDLERS,
)
log = logging.getLogger("data_collector")

RAW_LOG_PATH = LOG_DIR / "raw_responses.jsonl"

def log_raw(source: str, endpoint: str, payload: dict):
    """Append a raw API response to the JSONL audit log."""
    if _STRICT_WORKER is not None:
        return
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "endpoint": endpoint,
        "payload": payload,
    }
    with open(RAW_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Config ───────────────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
BINANCE_BASE   = "https://api.binance.com/api/v3"

# CoinGecko IDs mapped from ticker symbols
CG_ID_MAP = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "TON":  "the-open-network",
    "DOGE": "dogecoin",
    "ADA":  "cardano",
    "AVAX": "avalanche-2",
    "DOT":  "polkadot",
    "LINK": "chainlink",
    "MATIC": "matic-network",
}

# Binance trading pairs
BINANCE_SYMBOL_MAP = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "SOL":  "SOLUSDT",
    "TON":  "TONUSDT",
    "DOGE": "DOGEUSDT",
    "ADA":  "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "DOT":  "DOTUSDT",
    "LINK": "LINKUSDT",
    "MATIC": "MATICUSDT",
}

# Polling intervals in seconds
POLL_INTERVALS = {
    "BTC":  300,   # 5 min
    "ETH":  300,
    "SOL":  300,
    "TON":  900,   # 15 min
    "DOGE": 900,
}

STOCK_WATCHLIST = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
ETF_WATCHLIST = ["SPY", "QQQ"]
FOREX_WATCHLIST = ["EURUSD=X"]

# Load API keys from environment (never hardcode)
COINGECKO_API_KEY  = os.getenv("COINGECKO_API_KEY", "")   # optional, free tier works without


def _binance_headers(allow_exchange: bool) -> dict[str, str]:
    """Read Binance credentials only for explicitly exchange-enabled calls."""
    if not allow_exchange:
        return {}
    api_key = os.getenv("BINANCE_API_KEY", "")
    # Preserve the legacy secret read for deployments that validate both values.
    os.getenv("BINANCE_API_SECRET", "")
    return {"X-MBX-APIKEY": api_key} if api_key else {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def null_asset(symbol: str, reason: str) -> dict:
    """Return a null-filled asset dict with a logged reason."""
    log.warning("NULL ASSET [%s] — %s", symbol, reason)
    return {
        "symbol": symbol,
        "source": "unavailable",
        "reason": reason,
        "current_price": None,
        "ohlcv": None,
        "volume_24h": None,
        "market_cap": None,
        "price_change_24h_pct": None,
        "price_change_7d_pct": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ── CoinGecko ────────────────────────────────────────────────────────────────

async def fetch_coingecko_prices(
    session: aiohttp.ClientSession,
    symbols: list[str],
) -> dict[str, dict]:
    """
    Pull price, market cap, 24h/7d change for a batch of symbols.
    Returns dict keyed by symbol. Sets null on failure.
    """
    ids = ",".join(CG_ID_MAP[s] for s in symbols if s in CG_ID_MAP)
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ids,
        "price_change_percentage": "24h,7d",
        "per_page": len(symbols),
        "page": 1,
    }
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}

    async def _do_fetch():
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                resp.raise_for_status()
            return await resp.json()

    try:
        data = await _async_fetch_with_retry("coingecko", _do_fetch)
    except Exception as e:
        log.error("CoinGecko fetch error: %s", e)
        return {}

    if data is None:
        log.warning("CoinGecko unavailable — cooldown active or all retries exhausted")
        return {}

    log_raw("coingecko", url, {"params": params, "response_count": len(data)})

    result = {}
    cg_id_to_symbol = {v: k for k, v in CG_ID_MAP.items()}
    for coin in data:
        sym = cg_id_to_symbol.get(coin["id"])
        if not sym:
            continue
        result[sym] = {
            "symbol": sym,
            "source": "coingecko",
            "current_price": coin.get("current_price"),
            "market_cap": coin.get("market_cap"),
            "volume_24h": coin.get("total_volume"),
            "price_change_24h_pct": coin.get("price_change_percentage_24h_in_currency"),
            "price_change_7d_pct": coin.get("price_change_percentage_7d_in_currency"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    return result


# ── Binance ──────────────────────────────────────────────────────────────────

async def fetch_binance_ohlcv(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str = "1d",
    limit: int = 210,  # 200-day SMA needs 200+ candles; 210 gives buffer
    allow_exchange: bool = True,
) -> Optional[list[dict]]:
    """
    Pull OHLCV candles from Binance for a single symbol.
    Returns list of candle dicts, or None on failure.
    """
    if not allow_exchange:
        return None
    pair = BINANCE_SYMBOL_MAP.get(symbol)
    if not pair:
        log.warning("No Binance pair mapped for symbol: %s", symbol)
        return None

    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": pair, "interval": interval, "limit": limit}
    headers = _binance_headers(allow_exchange)

    async def _do_fetch():
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                resp.raise_for_status()
            return await resp.json()

    try:
        raw = await _async_fetch_with_retry("binance", _do_fetch)
    except Exception as e:
        log.error("Binance OHLCV fetch error for %s: %s", symbol, e)
        return None

    if raw is None:
        return None

    log_raw("binance", url, {"symbol": symbol, "interval": interval, "candle_count": len(raw)})

    candles = []
    for k in raw:
        candles.append({
            "open_time":  int(k[0]),
            "open":       float(k[1]),
            "high":       float(k[2]),
            "low":        float(k[3]),
            "close":      float(k[4]),
            "volume":     float(k[5]),
            "close_time": int(k[6]),
        })
    return candles


async def fetch_binance_price_fallback(
    session: aiohttp.ClientSession,
    symbol: str,
    allow_exchange: bool = True,
) -> Optional[float]:
    """
    Lightweight price fallback via Binance ticker/price endpoint.
    Used when CoinGecko is rate-limited.
    """
    if not allow_exchange:
        return None
    pair = BINANCE_SYMBOL_MAP.get(symbol)
    if not pair:
        return None

    url = f"{BINANCE_BASE}/ticker/price"
    params = {"symbol": pair}
    headers = _binance_headers(allow_exchange)

    async def _do_fetch():
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                resp.raise_for_status()
            return await resp.json()

    try:
        data = await _async_fetch_with_retry("binance", _do_fetch)
    except Exception as e:
        log.error("Binance fallback price error for %s: %s", symbol, e)
        return None

    if data is None:
        return None

    log_raw("binance_fallback", url, {"symbol": symbol, "response": data})
    log.info("Used Binance fallback price for %s: %s", symbol, data.get("price"))
    return float(data["price"])


# ── Orchestrator ─────────────────────────────────────────────────────────────


def _load_cached_report(symbols: list[str]) -> dict[str, dict]:
    """
    TACN-CN 3rd-tier fallback: load last known good data from cached reports.
    Returns dict keyed by symbol with stale-but-usable data.
    """
    reports_dir = runtime_reports_dir()
    if not reports_dir.exists():
        return {}

    files = sorted(reports_dir.glob("report_*.json"), reverse=True)
    if not files:
        log.warning("No cached reports available for fallback.")
        return {}

    latest = files[0]
    try:
        with open(latest) as f:
            report = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error("Failed to read cached report %s: %s", latest.name, e)
        return {}

    # Calculate staleness
    try:
        ts_str = latest.stem.replace("report_", "")
        report_ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
        age_hours = (datetime.now() - report_ts).total_seconds() / 3600
    except ValueError:
        age_hours = 999

    log.info("Cached report %s is %.1f hours old", latest.name, age_hours)

    cached = {}
    for asset in report.get("assets", []):
        sym = asset.get("symbol")
        if sym and sym in symbols:
            cached[sym] = {
                "symbol": sym,
                "source": "cached_report",
                "current_price": asset.get("price"),
                "market_cap": None,
                "volume_24h": None,
                "price_change_24h_pct": asset.get("change_24h"),
                "price_change_7d_pct": None,
                "fetched_at": report.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "cache_age_hours": round(age_hours, 1),
            }

    return cached

async def collect_all(
    symbols: list[str], allow_exchange: bool = True
) -> dict[str, dict]:
    """
    Main collection function — unified crypto + stock data collection.

    Crypto: CoinGecko/Binance vendor routing with fallback chain.
    Stocks: Twelve Data for quotes + OHLCV.
    Returns dict keyed by symbol with all raw fields (or null).
    """
    crypto_syms = [s for s in symbols if s not in STOCK_WATCHLIST]
    stock_syms = [s for s in symbols if s in STOCK_WATCHLIST]

    assets = {}

    # ── Crypto collection (existing vendor routing) ──
    if crypto_syms:
        async with aiohttp.ClientSession() as session:
            log.info("Fetching crypto prices via vendor routing for: %s", crypto_syms)
            price_tasks = {
                sym: route_to_vendor(
                    "get_price", sym, session, allow_exchange=allow_exchange
                )
                for sym in crypto_syms
            }
            price_results = await asyncio.gather(*price_tasks.values())
            price_data = dict(zip(price_tasks.keys(), price_results))

            vendor_success = sum(1 for v in price_data.values() if v is not None)
            log.info(f"Vendor routing succeeded for {vendor_success}/{len(crypto_syms)} crypto symbols")

            log.info("Fetching crypto OHLCV via vendor routing for: %s", crypto_syms)
            ohlcv_tasks = {
                sym: route_to_vendor(
                    "get_technicals", sym, session, allow_exchange=allow_exchange
                )
                for sym in crypto_syms
            }
            ohlcv_results = await asyncio.gather(*ohlcv_tasks.values())
            ohlcv_data = dict(zip(ohlcv_tasks.keys(), ohlcv_results))

            cached_data = {}
            if vendor_success == 0:
                cached_data = _load_cached_report(crypto_syms)
                if cached_data:
                    log.warning("Using cached report data — live sources unavailable.")

            for sym in crypto_syms:
                ohlcv = ohlcv_data.get(sym)
                if sym in price_data and price_data[sym] is not None:
                    asset = price_data[sym]
                elif sym in cached_data:
                    asset = cached_data[sym]
                    asset["source"] = "cached_report"
                else:
                    asset = null_asset(sym, "all sources failed: vendor routing + cache")

                if ohlcv:
                    asset["ohlcv"] = ohlcv
                    asset["ohlcv_source"] = "binance"
                    asset["ohlcv_candle_count"] = len(ohlcv)
                else:
                    asset["ohlcv"] = None
                    asset["ohlcv_source"] = "unavailable"
                    asset["ohlcv_candle_count"] = None

                asset["asset_class"] = "crypto"
                assets[sym] = asset

    # ── Stock collection (Twelve Data) ──
    if stock_syms:
        from twelve_data import fetch_quote, fetch_ohlcv
        for sym in stock_syms:
            quote = fetch_quote(sym)
            if not quote:
                log.warning("[stocks] No quote for %s", sym)
                assets[sym] = null_asset(sym, "twelve_data: no quote")
                continue
            price = float(quote.get("close") or quote.get("current_price") or 0)
            change = float(quote.get("percent_change") or quote.get("price_change_24h_pct") or 0)
            volume = int(float(quote.get("volume") or 0))
            ohlcv = fetch_ohlcv(sym)
            asset = {
                "symbol": sym,
                "current_price": price,
                "price_change_24h_pct": change,
                "volume_24h": volume,
                "market_cap": None,
                "source": "twelve_data",
                "asset_class": "stock",
            }
            if ohlcv:
                asset["ohlcv"] = ohlcv
                asset["ohlcv_source"] = "twelve_data"
                asset["ohlcv_candle_count"] = len(ohlcv)
            else:
                asset["ohlcv"] = None
                asset["ohlcv_source"] = "unavailable"
                asset["ohlcv_candle_count"] = None
            assets[sym] = asset
        log.info("[stocks] Collected %d stock(s): %s", len(stock_syms), list(assets.keys() & set(stock_syms)))

    log.info("Collection complete. Total assets: %d (crypto=%d, stocks=%d)",
             len(assets), len(crypto_syms), len(stock_syms))
    return assets


# ── Polling loop ──────────────────────────────────────────────────────────────

async def polling_loop(
    watchlist: list[str], on_update=None, allow_exchange: bool = True
):
    """
    Runs continuous polling with per-symbol intervals.
    Calls on_update(symbol, asset_data) whenever fresh data arrives.

    Designed to be replaced by a scheduler (APScheduler, etc.) if needed.
    This implementation is a lightweight asyncio-native loop.
    """
    last_fetched: dict[str, float] = {sym: 0.0 for sym in watchlist}

    log.info("Polling loop started. Watchlist: %s", watchlist)

    async with aiohttp.ClientSession() as session:
        while True:
            now = asyncio.get_event_loop().time()
            due = [sym for sym in watchlist if (now - last_fetched[sym]) >= POLL_INTERVALS[sym]]

            if due:
                log.info("Polling due for: %s", due)

                for sym in due:
                    # Use vendor routing for price and OHLCV
                    price_data = await route_to_vendor(
                        "get_price", sym, session, allow_exchange=allow_exchange
                    )
                    ohlcv = await route_to_vendor(
                        "get_technicals", sym, session, allow_exchange=allow_exchange
                    )

                    if price_data:
                        asset = price_data
                    else:
                        asset = null_asset(sym, "all price sources failed")

                    asset["ohlcv"] = ohlcv
                    asset["ohlcv_source"] = "binance" if ohlcv else "unavailable"
                    asset["ohlcv_candle_count"] = len(ohlcv) if ohlcv else None

                    last_fetched[sym] = now

                    if on_update:
                        await on_update(sym, asset)

            await asyncio.sleep(30)  # Check every 30s which symbols are due


# ── Entry point (standalone test run) ────────────────────────────────────────

if __name__ == "__main__":
    WATCHLIST = ["BTC", "ETH", "SOL", "TON", "DOGE"]

    async def main():
        results = await collect_all(WATCHLIST)
        for sym, data in results.items():
            print(f"\n── {sym} ──")
            print(f"  Source:         {data.get('source')}")
            print(f"  Price:          {data.get('current_price')}")
            print(f"  24h change:     {data.get('price_change_24h_pct')}")
            print(f"  7d change:      {data.get('price_change_7d_pct')}")
            print(f"  Volume 24h:     {data.get('volume_24h')}")
            print(f"  OHLCV candles:  {data.get('ohlcv_candle_count')}")
            print(f"  OHLCV source:   {data.get('ohlcv_source')}")
            print(f"  Fetched at:     {data.get('fetched_at')}")

    asyncio.run(main())
