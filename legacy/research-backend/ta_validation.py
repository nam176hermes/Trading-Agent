"""
ta_validation.py — Cross-validate ta_shim.py calculations against Alpha Vantage.
Compares RSI(14), MACD, SMA(50)/SMA(200) for 5 crypto assets.

Fetches daily OHLCV from Binance (free, no auth) for local TA computation,
then compares against Alpha Vantage's built-in technical indicators.

Rate limit: 25 calls/day. 5 assets × 4 indicators = 20 calls max.
Output: reports/ta_validation_<timestamp>.json

Environment:
  ALPHA_VANTAGE_KEY  — API key (required; 25 calls/day on free tier)
"""

import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np

from collector_utils import _fetch_with_retry
from runtime_paths import reports_dir

sys.path.insert(0, str(Path(__file__).parent))
import ta_shim

log = logging.getLogger("ta_validation")

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
AV_BASE = "https://www.alphavantage.co/query"
REPORTS_DIR = reports_dir()

CRYPTO_ASSETS = [
    {"symbol": "BTC", "binance_pair": "BTCUSDT"},
    {"symbol": "ETH", "binance_pair": "ETHUSDT"},
    {"symbol": "SOL", "binance_pair": "SOLUSDT"},
    {"symbol": "DOGE", "binance_pair": "DOGEUSDT"},
    {"symbol": "ADA", "binance_pair": "ADAUSDT"},
]

INDICATORS = [
    {"name": "RSI",  "function": "RSI",  "params": "&time_period=14&series_type=close"},
    {"name": "MACD", "function": "MACD", "params": "&series_type=close"},
    {"name": "SMA",  "function": "SMA",  "params": "&time_period=50&series_type=close"},
    {"name": "SMA",  "function": "SMA",  "params": "&time_period=200&series_type=close"},
]


def _http_fetch_binance_klines(symbol: str, limit: int) -> list:
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_binance_klines(symbol: str, limit: int = 250) -> "pd.DataFrame | None":
    data = _fetch_with_retry("binance", _http_fetch_binance_klines, symbol, limit)
    if not data or not isinstance(data, list) or len(data) < 50:
        log.warning("Binance: insufficient data for %s (%d candles)", symbol, len(data) if data else 0)
        return None

    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_vol", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(data, columns=cols)
    for col in ("close", "high", "low", "open"):
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def _http_fetch_av(function: str, symbol: str, extra_params: str) -> dict:
    url = f"{AV_BASE}?function={function}&symbol={symbol}&interval=daily{extra_params}&apikey={ALPHA_VANTAGE_KEY}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_av_indicator(function: str, symbol: str, extra_params: str) -> "dict | None":
    if not ALPHA_VANTAGE_KEY:
        log.warning("ALPHA_VANTAGE_KEY not set — skipping AV fetch for %s/%s", function, symbol)
        return None
    data = _fetch_with_retry("alphavantage", _http_fetch_av, function, symbol, extra_params)
    if data is None:
        return None
    if "Note" in data or "Information" in data:
        note = data.get("Note") or data.get("Information", "")
        log.warning("AV rate-limit for %s/%s: %s", function, symbol, note[:80])
        return None
    key = "Technical Analysis: " + function
    if key in data:
        return data[key]
    if function in data:
        return data[function]
    log.warning("Unexpected AV response for %s/%s: %s", function, symbol, list(data.keys())[:3])
    return None


def _latest_av_value(indicator_data: dict, name: str) -> "float | None":
    if not indicator_data:
        return None
    try:
        dates = sorted(indicator_data.keys(), reverse=True)
        latest = indicator_data[dates[0]]
        return float(latest.get(name, 0))
    except (ValueError, TypeError, KeyError):
        return None


def validate() -> dict:
    """Run full cross-validation. Returns a report dict."""
    validations = []
    ok_count = warn_count = fail_count = 0
    total_div = 0.0

    for asset in CRYPTO_ASSETS:
        symbol = asset["symbol"]
        pair = asset["binance_pair"]
        log.info("Validating %s (%s)", symbol, pair)

        df = fetch_binance_klines(pair)
        if df is None or len(df) < 50:
            log.warning("SKIP %s: insufficient price data", symbol)
            continue

        close = df["close"]

        for ind in INDICATORS:
            name = ind["name"]
            func = ind["function"]
            params = ind["params"]
            indicator_key = f"{name}_{params.split('=')[1].split('&')[0] if 'time_period' in params else 'default'}"

            # Local TA computation
            local_value = None
            try:
                if name == "RSI":
                    local_value = float(ta_shim.rsi(close, length=14).iloc[-1])
                elif name == "MACD":
                    local_value = float(ta_shim.macd(close)["MACD_12_26_9"].iloc[-1])
                elif name == "SMA":
                    period = int(params.split("time_period=")[1].split("&")[0]) if "time_period=" in params else 200
                    local_value = float(ta_shim.sma(close, length=period).iloc[-1])
            except Exception as e:
                log.warning("Local compute error %s/%s: %s", symbol, name, e)
                continue

            if local_value is None or np.isnan(local_value):
                log.warning("SKIP %s/%s: local value NaN", symbol, name)
                continue

            av_data = fetch_av_indicator(func, symbol, params)
            av_value = _latest_av_value(av_data, name)

            if av_value is None:
                log.info("No AV value for %s/%s — local only entry", symbol, name)
                validations.append({
                    "symbol": symbol,
                    "indicator": indicator_key,
                    "ta_shim_value": round(local_value, 4),
                    "alpha_vantage_value": None,
                    "divergence": None,
                    "divergence_pct": None,
                    "status": "no_av_data",
                })
                time.sleep(12)
                continue

            divergence = abs(local_value - av_value)
            divergence_pct = (divergence / abs(av_value)) * 100 if av_value != 0 else 0

            if divergence_pct < 1.0:
                status = "ok"; ok_count += 1
            elif divergence_pct < 3.0:
                status = "warn"; warn_count += 1
            else:
                status = "fail"; fail_count += 1

            total_div += divergence_pct
            log.info("%s %s: local=%.4f AV=%.4f div=%.2f%% [%s]",
                     symbol, indicator_key, local_value, av_value, divergence_pct, status)

            validations.append({
                "symbol": symbol,
                "indicator": indicator_key,
                "ta_shim_value": round(local_value, 4),
                "alpha_vantage_value": round(av_value, 4),
                "divergence": round(divergence, 4),
                "divergence_pct": round(divergence_pct, 2),
                "status": status,
            })

            time.sleep(12)

    total_checks = len([v for v in validations if v["alpha_vantage_value"] is not None])
    avg_div = round(total_div / total_checks, 2) if total_checks > 0 else 0

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "alphavantage",
        "validations": validations,
        "summary": {
            "total_checks": total_checks,
            "passed": ok_count,
            "failed": fail_count,
            "warned": warn_count,
            "avg_divergence_pct": avg_div,
        },
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not ALPHA_VANTAGE_KEY:
        log.error("ALPHA_VANTAGE_KEY not set — export it before running")
        sys.exit(1)

    log.info("TA Cross-Validation — Alpha Vantage vs ta_shim.py")
    log.info("Rate limit: 25 calls/day. Max usage: %d calls.", len(CRYPTO_ASSETS) * len(INDICATORS))
    log.info("Estimated time: %.1f min.", len(CRYPTO_ASSETS) * len(INDICATORS) * 12 / 60)

    report = validate()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"ta_validation_{ts_str}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    s = report["summary"]
    log.info("SUMMARY: %d/%d passed, %d failed, %d warned, avg_div=%.2f%%",
             s["passed"], s["total_checks"], s["failed"], s["warned"], s["avg_divergence_pct"])
    print(f"Report → {out_path}")


if __name__ == "__main__":
    main()
