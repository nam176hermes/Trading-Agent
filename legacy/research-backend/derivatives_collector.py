"""
derivatives_collector.py
------------------------
Fetches funding rates and open interest from Binance Futures (USDT-M).
Both endpoints are read-only. No write access required.

Endpoints used:
  GET /fapi/v1/fundingRate        — historical funding rates (last N)
  GET /fapi/v1/premiumIndex       — current funding rate + mark price
  GET /fapi/v1/openInterest       — current open interest (contracts)
  GET /futures/data/openInterestHist — OI history (for trend)

Plugs into main.py alongside fetch_sentiment() and fetch_onchain_risk().
Output is a null-safe dict that assemble_asset_json() merges directly.
"""

import asyncio
import aiohttp
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from job_attribution import strict_worker_invocation
from runtime_paths import data_root

log = logging.getLogger("derivatives_collector")

LOG_DIR = data_root() / "logs"
_STRICT_WORKER = strict_worker_invocation()
if _STRICT_WORKER is None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

RAW_LOG_PATH = LOG_DIR / "raw_responses.jsonl"

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Futures pairs (USDT-M perpetuals)
FUTURES_SYMBOL_MAP = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "SOL":  "SOLUSDT",
    "TON":  "TONUSDT",
    "DOGE": "DOGEUSDT",
}

# Thresholds for signal interpretation
FUNDING_HIGH_POSITIVE  =  0.001   #  0.1% — overleveraged long, caution
FUNDING_MOD_POSITIVE   =  0.0003  #  0.03% — healthy bullish
FUNDING_NEAR_ZERO_LOW  = -0.0001  # -0.01% — near-neutral lower bound
FUNDING_NEAR_ZERO_HIGH =  0.0001  #  0.01% — near-neutral upper bound
FUNDING_MOD_NEGATIVE   = -0.0003  # -0.03% — healthy bearish
FUNDING_HIGH_NEGATIVE  = -0.001   # -0.1% — overleveraged short, contrarian bullish


# ── Logging helper ────────────────────────────────────────────────────────────

def log_raw(source: str, endpoint: str, payload: dict):
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


# ── Null-safe default ─────────────────────────────────────────────────────────

def null_derivatives(symbol: str, reason: str) -> dict:
    log.warning("NULL DERIVATIVES [%s] — %s", symbol, reason)
    return {
        "funding_rate":           None,
        "funding_rate_annualized": None,
        "funding_signal":         None,
        "funding_source":         "unavailable",
        "open_interest_usd":      None,
        "oi_trend":               None,
        "oi_change_pct":          None,
        "oi_source":              "unavailable",
        "derivatives_signal":     None,
        "derivatives_note":       f"unavailable — {reason}",
        "fetched_at":             datetime.now(timezone.utc).isoformat(),
    }


# ── Funding Rate ──────────────────────────────────────────────────────────────

async def fetch_funding_rate(
    session: aiohttp.ClientSession,
    symbol: str,
) -> Optional[dict]:
    """
    Fetches current funding rate from Binance premiumIndex endpoint.
    Returns dict with rate + interpreted signal, or None on failure.
    """
    pair = FUTURES_SYMBOL_MAP.get(symbol)
    if not pair:
        log.warning("No futures pair mapped for %s", symbol)
        return None

    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex"
    params = {"symbol": pair}

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 429:
                log.warning("Binance Futures rate-limited (429) for %s funding.", symbol)
                return None
            if resp.status != 200:
                log.error("Binance Futures HTTP %s for %s funding.", resp.status, symbol)
                return None

            data = await resp.json()
            log_raw("binance_futures_funding", url, {"symbol": symbol, "response": data})

            rate = float(data.get("lastFundingRate", 0))

            # Annualised: 3 funding periods/day × 365 days
            annualized = round(rate * 3 * 365 * 100, 2)

            signal = interpret_funding_rate(rate)
            log.info("[%s] Funding rate: %.4f%% | Signal: %s | Annualised: %.1f%%",
                     symbol, rate * 100, signal, annualized)

            return {
                "funding_rate":            round(rate, 6),
                "funding_rate_pct":        round(rate * 100, 4),
                "funding_rate_annualized": annualized,
                "funding_signal":          signal,
                "funding_source":          f"Binance Futures — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            }

    except asyncio.TimeoutError:
        log.error("Binance Futures funding rate timeout for %s.", symbol)
        return None
    except Exception as e:
        log.error("Funding rate fetch error for %s: %s", symbol, e)
        return None


def interpret_funding_rate(rate: float) -> str:
    """
    Converts raw funding rate float into a human-readable signal string.
    Thresholds are calibrated for crypto perpetual markets.
    """
    if rate >= FUNDING_HIGH_POSITIVE:
        return "overleveraged_long — caution, squeeze risk"
    elif rate >= FUNDING_MOD_POSITIVE:
        return "bullish_bias — longs paying, healthy"
    elif FUNDING_NEAR_ZERO_LOW <= rate <= FUNDING_NEAR_ZERO_HIGH:
        return "neutral — balanced market"
    elif rate <= FUNDING_HIGH_NEGATIVE:
        return "overleveraged_short — contrarian bullish signal"
    elif rate <= FUNDING_MOD_NEGATIVE:
        return "bearish_bias — shorts paying, healthy"
    else:
        return "mild_negative — slight short bias"


# ── Open Interest ─────────────────────────────────────────────────────────────

async def fetch_open_interest(
    session: aiohttp.ClientSession,
    symbol: str,
    history_periods: int = 5,
) -> Optional[dict]:
    """
    Fetches current OI + recent OI history to calculate trend.

    Current OI: /fapi/v1/openInterest
    OI history:  /futures/data/openInterestHist (5 x 1h periods)

    Returns OI in USD, trend direction, and % change.
    """
    pair = FUTURES_SYMBOL_MAP.get(symbol)
    if not pair:
        return None

    # ── Current OI ──
    current_oi_usd = None
    try:
        url = f"{BINANCE_FUTURES_BASE}/fapi/v1/openInterest"
        params = {"symbol": pair}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                log_raw("binance_futures_oi", url, {"symbol": symbol, "response": data})
                current_oi_usd = float(data.get("openInterest", 0))
            else:
                log.error("Binance OI HTTP %s for %s", resp.status, symbol)
    except Exception as e:
        log.error("OI current fetch error for %s: %s", symbol, e)

    # ── OI History (for trend) ──
    oi_trend      = None
    oi_change_pct = None
    try:
        url = f"{BINANCE_FUTURES_BASE}/futures/data/openInterestHist"
        params = {"symbol": pair, "period": "1h", "limit": history_periods}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                hist = await resp.json()
                log_raw("binance_futures_oi_hist", url, {"symbol": symbol, "periods": history_periods})

                if len(hist) >= 2:
                    oldest = float(hist[0]["sumOpenInterest"])
                    newest = float(hist[-1]["sumOpenInterest"])

                    if oldest > 0:
                        oi_change_pct = round(((newest - oldest) / oldest) * 100, 2)
                        oi_trend = "rising" if oi_change_pct > 1.0 else \
                                   "falling" if oi_change_pct < -1.0 else "flat"

    except Exception as e:
        log.error("OI history fetch error for %s: %s", symbol, e)

    if current_oi_usd is None:
        return None

    log.info("[%s] OI: %s contracts | Trend: %s (%s%%)",
             symbol, current_oi_usd, oi_trend, oi_change_pct)

    return {
        "open_interest_usd": current_oi_usd,
        "oi_trend":          oi_trend,
        "oi_change_pct":     oi_change_pct,
        "oi_source":         f"Binance Futures — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    }


# ── Combined Signal Interpreter ───────────────────────────────────────────────

def interpret_derivatives_signal(
    funding_signal: Optional[str],
    oi_trend: Optional[str],
    price_direction: Optional[str],  # "up", "down", or None
) -> dict:
    """
    Combines funding rate signal and OI trend into a single
    derivatives_signal and human-readable note.

    price_direction should be derived from price_change_24h_pct:
        > 0 → "up", < 0 → "down", else None

    Returns:
        derivatives_signal: str — one of:
            "strong_long_setup"
            "long_setup"
            "short_squeeze_risk"
            "squeeze_risk_caution"
            "strong_short_setup"
            "short_setup"
            "long_squeeze_risk"
            "neutral"
            "mixed"
        derivatives_note: str — plain English explanation
    """
    if not funding_signal or not oi_trend:
        return {
            "derivatives_signal": "unavailable",
            "derivatives_note":   "Insufficient derivatives data to generate signal.",
        }

    # Simplified signal matrix
    # Rows: funding condition | Cols: OI trend + price direction

    is_overleveraged_long  = "overleveraged_long"  in funding_signal
    is_overleveraged_short = "overleveraged_short" in funding_signal
    is_bullish_funding     = "bullish_bias"        in funding_signal
    is_bearish_funding     = "bearish_bias"        in funding_signal or "mild_negative" in funding_signal
    is_neutral_funding     = "neutral"             in funding_signal

    oi_rising  = oi_trend == "rising"
    oi_falling = oi_trend == "falling"
    price_up   = price_direction == "up"
    price_down = price_direction == "down"

    # ── Danger signals (override everything) ──
    if is_overleveraged_long and oi_rising and price_up:
        return {
            "derivatives_signal": "squeeze_risk_caution",
            "derivatives_note": (
                "⚠️ Overleveraged longs + rising OI + price up. "
                "Market is euphoric. High squeeze risk if price reverses. "
                "Avoid chasing entries here."
            ),
        }

    if is_overleveraged_short and oi_rising and price_down:
        return {
            "derivatives_signal": "long_squeeze_risk",
            "derivatives_note": (
                "⚠️ Overleveraged shorts + rising OI + price falling. "
                "Heavy short crowding. Any positive catalyst could trigger a violent short squeeze upward."
            ),
        }

    # ── Strong setups ──
    if is_bullish_funding and oi_rising and price_up:
        return {
            "derivatives_signal": "strong_long_setup",
            "derivatives_note": (
                "Bullish funding + rising OI + price up. "
                "New money entering longs, trend is healthy and supported by derivatives."
            ),
        }

    if is_bearish_funding and oi_rising and price_down:
        return {
            "derivatives_signal": "strong_short_setup",
            "derivatives_note": (
                "Bearish funding + rising OI + price down. "
                "New shorts entering, trend is healthy downward pressure."
            ),
        }

    # ── Exhaustion signals ──
    if oi_falling and price_up:
        return {
            "derivatives_signal": "short_squeeze_relief",
            "derivatives_note": (
                "OI falling + price rising. Shorts covering, not new longs entering. "
                "Move may be running out of fuel. Watch for trend reversal."
            ),
        }

    if oi_falling and price_down:
        return {
            "derivatives_signal": "long_capitulation",
            "derivatives_note": (
                "OI falling + price down. Longs giving up, not new shorts entering. "
                "Potential exhaustion of selling — possible bottom forming."
            ),
        }

    # ── Neutral / mixed ──
    if is_neutral_funding:
        return {
            "derivatives_signal": "neutral",
            "derivatives_note": "Funding near zero, balanced market. No strong derivatives conviction.",
        }

    return {
        "derivatives_signal": "mixed",
        "derivatives_note": (
            f"Funding: {funding_signal} | OI trend: {oi_trend} | "
            "Signals are mixed. No clear derivatives edge."
        ),
    }


# ── Main fetch function (called from main.py) ─────────────────────────────────

async def fetch_derivatives(
    symbol: str,
    price_change_24h_pct: Optional[float] = None,
    allow_exchange: bool = True,
) -> dict:
    """
    Top-level function. Called from main.py alongside fetch_sentiment()
    and fetch_onchain_risk().

    Args:
        symbol:               e.g. "BTC"
        price_change_24h_pct: from raw_data, used for OI signal context
        allow_exchange:       whether Binance Futures requests are permitted

    Returns:
        Null-safe dict ready for assemble_asset_json() to merge directly.
    """
    if not allow_exchange:
        return null_derivatives(symbol, "exchange data disabled")

    async with aiohttp.ClientSession() as session:
        funding_data, oi_data = await asyncio.gather(
            fetch_funding_rate(session, symbol),
            fetch_open_interest(session, symbol),
            return_exceptions=False,
        )

    if funding_data is None and oi_data is None:
        return null_derivatives(symbol, "both funding rate and OI fetch failed")

    # Determine price direction for signal matrix
    price_direction = None
    if price_change_24h_pct is not None:
        price_direction = "up" if price_change_24h_pct > 0 else "down"

    # Merge + interpret
    result = {}

    if funding_data:
        result.update(funding_data)
    else:
        result.update({
            "funding_rate":            None,
            "funding_rate_pct":        None,
            "funding_rate_annualized": None,
            "funding_signal":          None,
            "funding_source":          "unavailable",
        })

    if oi_data:
        result.update(oi_data)
    else:
        result.update({
            "open_interest_usd": None,
            "oi_trend":          None,
            "oi_change_pct":     None,
            "oi_source":         "unavailable",
        })

    # Combined signal
    combined = interpret_derivatives_signal(
        funding_signal  = result.get("funding_signal"),
        oi_trend        = result.get("oi_trend"),
        price_direction = price_direction,
    )
    result.update(combined)
    result["fetched_at"] = datetime.now(timezone.utc).isoformat()

    return result


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    from runtime_paths import configured_env_file

    env_file = configured_env_file()
    if env_file is not None:
        from dotenv import load_dotenv
        load_dotenv(env_file)

    async def main():
        symbols = ["BTC", "ETH", "SOL"]
        for sym in symbols:
            print(f"\n── {sym} ──")
            result = await fetch_derivatives(sym, price_change_24h_pct=2.1)
            for k, v in result.items():
                print(f"  {k}: {v}")

    asyncio.run(main())
