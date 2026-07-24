#!/usr/bin/env python3
"""
yfinance_collector.py — Fundamentals and price data via yfinance MCP.

Fetches company fundamentals (P/E, market cap, sector, analyst targets, etc.)
for stocks/ETFs and 60d price history for crypto.

Output: reports/fundamentals_report_<timestamp>.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import reports_dir
from typing import Optional

import pandas as pd

log = logging.getLogger("yfinance_collector")

OUTPUT_DIR = reports_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc).isoformat()

STOCK_TICKERS = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
ETF_TICKERS = ["SPY", "QQQ"]


def _safe_get(info: dict, *keys, default=None):
    """Safely extract nested keys from a dict."""
    d = info
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return default
    return d if d is not None else default


def collect_stock_fundamentals(symbol: str) -> Optional[dict]:
    """
    Fetch fundamental data for a single stock via yfinance.
    Returns dict with profile, valuation, financials, earnings or None on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — skipping fundamentals for %s", symbol)
        return None

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("trailingPegRatio") is None and info.get("previousClose") is None:
            # Try history as fallback for price
            hist = ticker.history(period="5d")
            if hist is not None and not hist.empty:
                price = float(hist["Close"].iloc[-1])
            else:
                return None
        else:
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")

        if price is None:
            return None

        # Profile
        profile = {
            "companyName": info.get("shortName") or info.get("longName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "marketCap": info.get("marketCap"),
            "beta": info.get("beta"),
        }

        # Valuation
        valuation = {
            "peRatio": info.get("trailingPE") or info.get("forwardPE"),
            "pbRatio": info.get("priceToBook"),
            "psRatio": info.get("priceToSalesTrailing12Months"),
            "evToEbitda": info.get("enterpriseToEbitda"),
            "roe": info.get("returnOnEquity"),
            "debtToEquity": info.get("debtToEquity"),
            "currentRatio": info.get("currentRatio"),
        }

        # Financials
        financials = {
            "revenueGrowth": info.get("revenueGrowth"),
            "earningsGrowth": info.get("earningsGrowth"),
            "grossMargin": info.get("grossMargins"),
            "netMargin": info.get("profitMargins"),
            "latestQuarterRevenue": info.get("totalRevenue"),
            "latestQuarterEarnings": info.get("netIncomeToCommon"),
        }

        # Earnings
        earnings = {
            "nextDate": None,
            "estimatedEps": info.get("forwardEps"),
        }
        try:
            cal = ticker.calendar
            if cal and hasattr(cal, "iloc"):
                earnings_dates = ticker.earnings_dates
                if earnings_dates is not None and not earnings_dates.empty:
                    next_date = earnings_dates.index[0]
                    earnings["nextDate"] = str(next_date.date()) if hasattr(next_date, "date") else str(next_date)
        except Exception:
            pass

        # Analyst data
        analyst = {
            "targetHighPrice": info.get("targetHighPrice"),
            "targetLowPrice": info.get("targetLowPrice"),
            "targetMeanPrice": info.get("targetMeanPrice"),
            "recommendationMean": info.get("recommendationMean"),
            "recommendationKey": info.get("recommendationKey"),
            "numberOfAnalystOpinions": info.get("numberOfAnalystOpinions"),
        }

        # Dividend
        dividend = {
            "dividendYield": info.get("dividendYield"),
            "dividendRate": info.get("dividendRate"),
            "payoutRatio": info.get("payoutRatio"),
        }

        return {
            "symbol": symbol,
            "asset_class": "stock",
            "current_price": round(float(price), 2),
            "profile": profile,
            "valuation": valuation,
            "financials": financials,
            "earnings": earnings,
            "analyst": analyst,
            "dividend": dividend,
            "source": "yfinance",
        }

    except Exception as e:
        log.warning("yfinance fundamentals failed for %s: %s", symbol, e)
        return None


def collect_crypto_price_history(symbols: list[str], days: int = 60) -> dict[str, Optional[dict]]:
    """
    Fetch 60d daily price history for crypto symbols via yfinance.
    Returns dict keyed by symbol with price data or None.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    results = {}
    yf_tickers = [s + "-USD" for s in symbols]

    try:
        data = yf.download(yf_tickers, period=f"{days}d", interval="1d", progress=False, auto_adjust=True)
    except Exception as e:
        log.warning("yfinance crypto download failed: %s", e)
        return {}

    if data is None or data.empty:
        return {}

    for sym, yf_t in zip(symbols, yf_tickers):
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if "Close" in data.columns and yf_t in data["Close"].columns:
                    close_series = data["Close"][yf_t].dropna()
                else:
                    continue
            elif len(symbols) == 1:
                close_series = data["Close"].dropna() if "Close" in data.columns else data.dropna()
            else:
                continue

            if close_series.empty:
                continue

            prices = close_series.tolist()
            current_price = round(float(close_series.iloc[-1]), 2)

            results[sym] = {
                "symbol": sym,
                "asset_class": "crypto",
                "current_price": current_price,
                "prices_60d": [round(float(p), 2) for p in prices],
                "source": "yfinance",
            }
        except Exception as e:
            log.warning("yfinance crypto parse failed for %s: %s", sym, e)
            results[sym] = None

    return results


def collect_all_fundamentals() -> dict:
    """Collect fundamentals for all configured stocks/ETFs."""
    assets = []

    for sym in STOCK_TICKERS + ETF_TICKERS:
        result = collect_stock_fundamentals(sym)
        if result:
            assets.append(result)
        else:
            # Minimal stub
            assets.append({
                "symbol": sym,
                "asset_class": "etf" if sym in ETF_TICKERS else "stock",
                "current_price": None,
                "profile": {"companyName": sym, "sector": "", "industry": "", "marketCap": None, "beta": None},
                "valuation": {"peRatio": None, "pbRatio": None, "psRatio": None, "evToEbitda": None, "roe": None, "debtToEquity": None, "currentRatio": None},
                "financials": {"revenueGrowth": None, "earningsGrowth": None, "grossMargin": None, "netMargin": None},
                "earnings": {"nextDate": None, "estimatedEps": None},
                "analyst": {"targetHighPrice": None, "targetLowPrice": None, "targetMeanPrice": None, "recommendationKey": None},
                "dividend": {"dividendYield": None},
                "source": "unavailable",
            })

    return {
        "timestamp": NOW,
        "source": "yfinance",
        "assets": assets,
    }


def main():
    report = collect_all_fundamentals()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"fundamentals_report_{ts}.json"

    # Keep last 10 fundamentals reports
    old_reports = sorted(OUTPUT_DIR.glob("fundamentals_report_*.json"))
    for old in old_reports[:-9]:
        old.unlink(missing_ok=True)

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Fundamentals report written to {out_path}")
    for a in report["assets"]:
        print(f"  {a['symbol']}: price={a.get('current_price')}, pe={a.get('valuation', {}).get('peRatio')}")


if __name__ == "__main__":
    main()
