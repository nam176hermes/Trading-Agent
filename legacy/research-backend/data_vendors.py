"""
data_vendors.py
---------------
Multi-vendor abstraction with automatic fallback chain.
Routes data requests to primary vendor, falls through on 429 rate limits or timeouts.
Vendors: CoinGecko, Binance, CryptoCompare, Coinpaprika
"""

import asyncio
import aiohttp
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

# Limit concurrency to avoid rate-limiting free API tiers
_VENDOR_SEMAPHORE = asyncio.Semaphore(3)

# ── Logging ─────────────────────────────────────────────────────────────────────

log = logging.getLogger("data_vendors")

_ccxt_async = None


def _load_ccxt():
    """Import CCXT only when an exchange-enabled route actually needs it."""
    global _ccxt_async
    if _ccxt_async is None:
        try:
            import ccxt.async_support as ccxt_async
        except ImportError:
            return None
        _ccxt_async = ccxt_async
    return _ccxt_async



# ── Staleness Configuration ──────────────────────────────────────────────────────

MAX_STALENESS_MINUTES = 5  # Reject data older than 5 minutes
MAX_SERVER_TIME_DRIFT_SECONDS = 5  # Flag server time drift > 5 seconds

# ── Exceptions ──────────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    """Raised when a vendor returns 429 Too Many Requests."""
    pass


class StaleDataError(Exception):
    """Raised when vendor data is too old."""
    pass


class ServerTimeDriftError(Exception):
    """Raised when server time drift exceeds threshold."""
    pass


# ── Freshness Validation ─────────────────────────────────────────────────────────

def check_data_freshness(
    data: dict,
    source: str,
    fetched_at_str: Optional[str] = None,
) -> bool:
    """
    Check if vendor data is fresh enough.

    Args:
        data: Response dict from vendor (may contain last_updated_at, serverTime, etc.)
        source: Vendor name for logging
        fetched_at_str: Optional ISO timestamp of when we fetched the data

    Returns:
        True if data is fresh, False if stale

    Raises:
        StaleDataError: If data is too stale
        ServerTimeDriftError: If server time drift is too high
    """
    now = datetime.now(timezone.utc)

    # Check vendor's reported last_updated_at if present
    if "last_updated_at" in data:
        try:
            last_updated = datetime.fromisoformat(data["last_updated_at"])
            age = now - last_updated

            if age > timedelta(minutes=MAX_STALENESS_MINUTES):
                log.warning(
                    "[stale_check] %s data is too old: last_updated=%s (age=%s)",
                    source, last_updated, age
                )
                raise StaleDataError(f"Data from {source} is {age.total_seconds()/60:.1f} minutes old")
            else:
                log.debug(
                    "[stale_check] %s data is fresh: last_updated=%s (age=%s)",
                    source, last_updated, age
                )

        except (ValueError, TypeError) as e:
            log.debug("[stale_check] Could not parse last_updated_at: %s", e)

    # Check serverTime drift for Binance
    if "serverTime" in data:
        try:
            server_time = datetime.fromtimestamp(data["serverTime"] / 1000, tz=timezone.utc)
            drift = abs((now - server_time).total_seconds())

            if drift > MAX_SERVER_TIME_DRIFT_SECONDS:
                log.warning(
                    "[stale_check] %s server time drift too high: %s seconds",
                    source, drift
                )
                raise ServerTimeDriftError(f"{source} server time drift: {drift:.1f}s")
            else:
                log.debug("[stale_check] %s server time drift OK: %s seconds", source, drift)

        except (ValueError, TypeError) as e:
            log.debug("[stale_check] Could not parse serverTime: %s", e)

    # Check our fetch time if provided
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            age = now - fetched_at

            if age > timedelta(minutes=MAX_STALENESS_MINUTES):
                log.warning(
                    "[stale_check] %s fetched data is too old: fetched=%s (age=%s)",
                    source, fetched_at, age
                )
                raise StaleDataError(f"Fetched data from {source} is {age.total_seconds()/60:.1f} minutes old")

        except (ValueError, TypeError) as e:
            log.debug("[stale_check] Could not parse fetched_at: %s", e)

    return True


# ── Vendor Configuration ────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
BINANCE_BASE   = "https://api.binance.com/api/v3"
CRYPTOCOMPARE_BASE = "https://min-api.cryptocompare.com/data"
COINPAPRIKA_BASE = "https://api.coinpaprika.com/v1"

# Symbol mappings
CG_ID_MAP = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "TON":  "the-open-network",
    "DOGE": "dogecoin",
}

BINANCE_SYMBOL_MAP = {
    "BTC":   "BTCUSDT",
    "ETH":   "ETHUSDT",
    "SOL":   "SOLUSDT",
    "TON":   "TONUSDT",
    "DOGE":  "DOGEUSDT",
    "ADA":   "ADAUSDT",
    "AVAX":  "AVAXUSDT",
    "DOT":   "DOTUSDT",
    "LINK":  "LINKUSDT",
    "MATIC": "MATICUSDT",
}

# Fallback chain configuration
VENDOR_MAP = {
    "get_price": {
        "primary": "coingecko",
        "fallbacks": ["binance", "cryptocompare", "coinpaprika", "ccxt_binance"]
    },
    "get_volume": {
        "primary": "coingecko",
        "fallbacks": ["binance", "cryptocompare"]
    },
    "get_technicals": {
        "primary": "binance",
        "fallbacks": ["coingecko", "ccxt_binance"]
    },
}


# ── Vendor Implementations ───────────────────────────────────────────────────────

async def _coingecko_get_price(
    session: aiohttp.ClientSession,
    symbol: str,
    **kwargs
) -> Optional[dict]:
    """Fetch price and metadata from CoinGecko."""
    coin_id = CG_ID_MAP.get(symbol)
    if not coin_id:
        return None

    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": coin_id,
        "price_change_percentage": "24h,7d",
    }

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                raise RateLimitError("CoinGecko rate-limited (429)")
            if resp.status != 200:
                return None

            data = await resp.json()
            if not data:
                return None

            coin = data[0]
            fetched_at = datetime.now(timezone.utc).isoformat()

            result = {
                "symbol": symbol,
                "source": "coingecko",
                "current_price": coin.get("current_price"),
                "market_cap": coin.get("market_cap"),
                "volume_24h": coin.get("total_volume"),
                "price_change_24h_pct": coin.get("price_change_percentage_24h_in_currency"),
                "price_change_7d_pct": coin.get("price_change_percentage_7d_in_currency"),
                "fetched_at": fetched_at,
                "last_updated_at": coin.get("last_updated"),  # CoinGecko provides this
            }

            # Check staleness
            check_data_freshness(result, "coingecko", fetched_at)

            return result
    except asyncio.TimeoutError:
        raise RateLimitError("CoinGecko timeout")
    except RateLimitError:
        raise
    except StaleDataError:
        raise  # Re-raise staleness errors
    except Exception as e:
        log.warning(f"CoinGecko error for {symbol}: {e}")
        return None


async def _binance_get_price(
    session: aiohttp.ClientSession,
    symbol: str,
    **kwargs
) -> Optional[dict]:
    """Fetch current price from Binance ticker."""
    pair = BINANCE_SYMBOL_MAP.get(symbol)
    if not pair:
        return None

    url = f"{BINANCE_BASE}/ticker/price"
    params = {"symbol": pair}

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 429:
                raise RateLimitError("Binance rate-limited (429)")
            if resp.status != 200:
                return None

            data = await resp.json()
            return {
                "symbol": symbol,
                "source": "binance",
                "current_price": float(data["price"]),
                "market_cap": None,
                "volume_24h": None,
                "price_change_24h_pct": None,
                "price_change_7d_pct": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except asyncio.TimeoutError:
        raise RateLimitError("Binance timeout")
    except RateLimitError:
        raise
    except Exception as e:
        log.warning(f"Binance price error for {symbol}: {e}")
        return None


async def _cryptocompare_get_price(
    session: aiohttp.ClientSession,
    symbol: str,
    **kwargs
) -> Optional[dict]:
    """Fetch price from CryptoCompare (free tier, no auth)."""
    url = f"{CRYPTOCOMPARE_BASE}/pricemultifull"
    params = {
        "fsyms": symbol,
        "tsyms": "USD",
    }

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                raise RateLimitError("CryptoCompare rate-limited (429)")
            if resp.status != 200:
                return None

            data = await resp.json()
            raw = data.get("RAW", {}).get(symbol, {}).get("USD", {})
            if not raw:
                return None

            return {
                "symbol": symbol,
                "source": "cryptocompare",
                "current_price": raw.get("PRICE"),
                "market_cap": raw.get("MKTCAP"),
                "volume_24h": raw.get("VOLUME24HOUR"),
                "price_change_24h_pct": raw.get("CHANGEPCT24HOUR"),
                "price_change_7d_pct": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except asyncio.TimeoutError:
        raise RateLimitError("CryptoCompare timeout")
    except RateLimitError:
        raise
    except Exception as e:
        log.warning(f"CryptoCompare error for {symbol}: {e}")
        return None


async def _coinpaprika_get_price(
    session: aiohttp.ClientSession,
    symbol: str,
    **kwargs
) -> Optional[dict]:
    """Fetch price from Coinpaprika (free tier, no auth)."""
    coin_id = CG_ID_MAP.get(symbol)
    if not coin_id:
        return None

    url = f"{COINPAPRIKA_BASE}/tickers/{coin_id}"

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                raise RateLimitError("Coinpaprika rate-limited (429)")
            if resp.status != 200:
                return None

            data = await resp.json()
            quotes = data.get("quotes", {}).get("USD", {})
            if not quotes:
                return None

            return {
                "symbol": symbol,
                "source": "coinpaprika",
                "current_price": quotes.get("price"),
                "market_cap": quotes.get("market_cap"),
                "volume_24h": quotes.get("volume_24h"),
                "price_change_24h_pct": None,
                "price_change_7d_pct": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except asyncio.TimeoutError:
        raise RateLimitError("Coinpaprika timeout")
    except RateLimitError:
        raise
    except Exception as e:
        log.warning(f"Coinpaprika error for {symbol}: {e}")
        return None


async def _binance_get_technicals(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str = "1d",
    limit: int = 210,
    **kwargs
) -> Optional[list[dict]]:
    """Fetch OHLCV candles from Binance."""
    pair = BINANCE_SYMBOL_MAP.get(symbol)
    if not pair:
        return None

    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": pair, "interval": interval, "limit": limit}

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 429:
                raise RateLimitError("Binance rate-limited (429)")
            if resp.status != 200:
                return None

            raw = await resp.json()
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

            # Check staleness: use close_time of most recent candle
            if candles:
                latest_close_time = candles[-1]["close_time"]
                try:
                    latest_dt = datetime.fromtimestamp(latest_close_time / 1000, tz=timezone.utc)
                    age = datetime.now(timezone.utc) - latest_dt

                    if age > timedelta(minutes=MAX_STALENESS_MINUTES):
                        log.warning(
                            "[stale_check] Binance technicals for %s are stale: latest_candle=%s (age=%s)",
                            symbol, latest_dt, age
                        )
                        raise StaleDataError(f"Binance technicals for {symbol} are {age.total_seconds()/60:.1f} minutes old")
                    else:
                        log.debug(
                            "[stale_check] Binance technicals for %s are fresh: latest_candle=%s (age=%s)",
                            symbol, latest_dt, age
                        )

                except (ValueError, TypeError) as e:
                    log.debug("[stale_check] Could not parse candle close_time: %s", e)

            return candles
    except asyncio.TimeoutError:
        raise RateLimitError("Binance timeout")
    except RateLimitError:
        raise
    except StaleDataError:
        raise  # Re-raise staleness errors
    except Exception as e:
        log.warning(f"Binance technicals error for {symbol}: {e}")
        return None




# ── CCXT vendor (Binance via ccxt.async_support) ────────────────────────────────

async def _ccxt_get_price(
    session: aiohttp.ClientSession,
    symbol: str,
    allow_exchange: bool = True,
    **kwargs
) -> Optional[dict]:
    """Fetch price from Binance via CCXT async client (100+ exchanges, unified API)."""
    if not allow_exchange:
        return None
    ccxt_async = _load_ccxt()
    if ccxt_async is None:
        return None
    pair = BINANCE_SYMBOL_MAP.get(symbol)
    if not pair:
        return None
    # CCXT uses slash notation: BTCUSDT → BTC/USDT
    ccxt_symbol = f"{symbol}/USDT"
    try:
        exchange = ccxt_async.binance({"enableRateLimit": True})
        ticker = await exchange.fetch_ticker(ccxt_symbol)
        await exchange.close()
        if not ticker or ticker.get("last") is None:
            return None
        return {
            "symbol": symbol,
            "source": "ccxt_binance",
            "current_price": float(ticker["last"]),
            "market_cap": None,
            "volume_24h": ticker.get("quoteVolume"),
            "price_change_24h_pct": ticker.get("percentage"),
            "price_change_7d_pct": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        log.warning("CCXT price error for %s: %s", symbol, e)
        return None


async def _ccxt_get_technicals(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str = "1d",
    limit: int = 210,
    allow_exchange: bool = True,
    **kwargs
) -> Optional[list[dict]]:
    """Fetch OHLCV from any exchange via CCXT (unified multi-exchange OHLCV)."""
    if not allow_exchange:
        return None
    ccxt_async = _load_ccxt()
    if ccxt_async is None:
        return None
    ccxt_symbol = f"{symbol}/USDT"
    # Map interval strings to CCXT timeframe notation
    tf_map = {"1d": "1d", "4h": "4h", "1h": "1h", "15m": "15m", "1m": "1m"}
    timeframe = tf_map.get(interval, "1d")
    try:
        exchange = ccxt_async.binance({"enableRateLimit": True})
        ohlcv_raw = await exchange.fetch_ohlcv(ccxt_symbol, timeframe, limit=limit)
        await exchange.close()
        if not ohlcv_raw:
            return None
        candles = []
        for row in ohlcv_raw:
            # CCXT OHLCV: [timestamp_ms, open, high, low, close, volume]
            candles.append({
                "open_time": int(row[0]),
                "open":      float(row[1]),
                "high":      float(row[2]),
                "low":       float(row[3]),
                "close":     float(row[4]),
                "volume":    float(row[5]),
                "close_time": int(row[0]) + 86_399_999,  # approx end of candle
            })
        return candles
    except Exception as e:
        log.warning("CCXT OHLCV error for %s: %s", symbol, e)
        return None

# ── Vendor Registry ──────────────────────────────────────────────────────────────

VENDOR_FUNCTIONS = {
    "coingecko": {
        "get_price": _coingecko_get_price,
        "get_technicals": _coingecko_get_price,  # Price only, no OHLCV
    },
    "binance": {
        "get_price": _binance_get_price,
        "get_volume": _binance_get_price,
        "get_technicals": _binance_get_technicals,
    },
    "cryptocompare": {
        "get_price": _cryptocompare_get_price,
        "get_volume": _cryptocompare_get_price,
    },
    "coinpaprika": {
        "get_price": _coinpaprika_get_price,
        "get_volume": _coinpaprika_get_price,
    },
    "ccxt_binance": {
        "get_price": _ccxt_get_price,
        "get_volume": _ccxt_get_price,
        "get_technicals": _ccxt_get_technicals,
    },
}


# ── Router with Fallback Chain ───────────────────────────────────────────────────

async def route_to_vendor(
    method: str,
    symbol: str,
    session: aiohttp.ClientSession,
    allow_exchange: bool = True,
    **kwargs
) -> Optional[dict]:
    """
    Try primary vendor, fall through on rate limit (429), timeout, or stale data.
    Returns None if all vendors fail.
    """
    if method not in VENDOR_MAP:
        log.error(f"Unknown method: {method}")
        return None

    chain = [VENDOR_MAP[method]["primary"]] + VENDOR_MAP[method].get("fallbacks", [])
    if not allow_exchange:
        chain = [vendor for vendor in chain if vendor not in {"binance", "ccxt_binance"}]
    last_error = None

    for vendor in chain:
        try:
            vendor_funcs = VENDOR_FUNCTIONS.get(vendor, {})
            if method not in vendor_funcs:
                log.warning(f"{vendor} does not support {method}")
                continue

            func = vendor_funcs[method]
            async with _VENDOR_SEMAPHORE:
                result = await func(
                    session, symbol, allow_exchange=allow_exchange, **kwargs
                )

            if result is not None:
                log.info(f"{method}({symbol}) → {vendor} ✓")
                return result

        except RateLimitError:
            log.warning(f"{method}({symbol}) → {vendor} rate-limited, trying next")
            last_error = "rate_limited"
            continue
        except StaleDataError:
            log.warning(f"{method}({symbol}) → {vendor} data stale, trying next")
            last_error = "stale_data"
            continue
        except ServerTimeDriftError:
            log.warning(f"{method}({symbol}) → {vendor} server time drift, trying next")
            last_error = "server_time_drift"
            continue
        except Exception as e:
            log.warning(f"{method}({symbol}) → {vendor} failed: {e}")
            last_error = str(e)
            continue

    log.error(f"{method}({symbol}) → ALL vendors failed (last: {last_error})")
    return None
