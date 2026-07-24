#!/usr/bin/env python3
"""Macro data collector — yfinance + World Bank API.

Output: reports/macro_report_<timestamp>.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from runtime_paths import reports_dir
from urllib.request import Request, urlopen

from collector_utils import _fetch_with_retry

OUTPUT_DIR = reports_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc).isoformat()
NOW_STR = datetime.now().strftime("%b %Y")


def _http_fetch_json(url: str) -> dict:
    """Raw HTTP GET — raises on errors for wrapper retry."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def fetch_json(url: str, timeout: int = 15) -> dict | None:
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8-sig"))
    except Exception:
        return None


# ── Yahoo Finance (VIX, DXY, short-term rates) ──────────────────────────────

YF_TICKERS = {
    "vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "fed_funds_rate": "^IRX",  # 13-week T-bill as short-term rate proxy
    "us10y": "^TNX",           # 10-year Treasury yield
    "sp500": "^GSPC",          # S&P 500 index
}


def fetch_yfinance() -> dict:
    results = {}
    try:
        import yfinance as yf
    except ImportError:
        return results

    for key, ticker in YF_TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            info = _fetch_with_retry("yfinance", lambda: t.info) or {}
            price = info.get("regularMarketPrice") or info.get("previousClose")
            if price is None:
                hist = _fetch_with_retry("yfinance", lambda: t.history(period="5d"))
                if hist is not None and not hist.empty:
                    price = float(hist["Close"].iloc[-1])
            if price is not None:
                results[key] = {"value": round(float(price), 2), "period": NOW_STR}
        except Exception:
            continue

    return results


# ── World Bank API (GDP, CPI, Unemployment) ─────────────────────────────────

WB_INDICATORS = {
    "gdp_growth": ("NY.GDP.MKTP.KD.ZG", "GDP growth (annual %)"),
    "cpi_inflation": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)"),
    "unemployment": ("SL.UEM.TOTL.ZS", "Unemployment, total (% of labor force)"),
}


def fetch_worldbank_indicator(indicator_id: str) -> dict | None:
    url = (
        f"https://api.worldbank.org/v2/country/US/indicator/{indicator_id}"
        f"?format=json&per_page=5&mrnev=1"
    )
    data = _fetch_with_retry("worldbank", _http_fetch_json, url)
    if not data or len(data) < 2:
        return None

    records = data[1]
    if not records:
        return None

    # Find most recent non-null value
    for record in records:
        val = record.get("value")
        if val is not None:
            return {
                "value": round(float(val), 2),
                "period": record.get("date", "N/A"),
            }

    return None


def fetch_worldbank() -> dict:
    results = {}
    for key, (indicator_id, _label) in WB_INDICATORS.items():
        val = fetch_worldbank_indicator(indicator_id)
        if val:
            results[key] = val
    return results


# ── Trend detection ─────────────────────────────────────────────────────────

def detect_trend(key: str, val: float) -> str:
    if key == "gdp_growth":
        if val > 3.0:
            return "up"
        elif val < 1.5:
            return "down"
        return "stable"
    elif key == "cpi_inflation":
        if val > 4.0:
            return "up"
        elif val < 2.5:
            return "down"
        return "stable"
    elif key == "fed_funds_rate":
        if val > 5.0:
            return "up"
        elif val < 3.5:
            return "down"
        return "flat"
    elif key == "unemployment":
        if val > 5.0:
            return "up"
        elif val < 3.8:
            return "down"
        return "stable"
    elif key == "dxy":
        if val > 106:
            return "up"
        elif val < 100:
            return "down"
        return "stable"
    elif key == "vix":
        if val > 25:
            return "up"
        elif val < 15:
            return "down"
        return "stable"
    elif key == "us10y":
        if val > 5.0:
            return "up"
        elif val < 3.5:
            return "down"
        return "stable"
    elif key == "sp500":
        if val > 5500:
            return "up"
        elif val < 4500:
            return "down"
        return "stable"
    return "stable"


# ── Regime detection ────────────────────────────────────────────────────────

def detect_regime(
    indicators: dict, allow_kalshi: bool = True
) -> tuple[str, float, str]:
    reasons = []
    score = 0.5

    cpi = indicators.get("cpi_inflation")
    if cpi:
        if cpi["trend"] == "down":
            score += 0.1
            reasons.append("declining inflation")
        elif cpi["trend"] == "up":
            score -= 0.15
            reasons.append("rising inflation")
        if cpi["value"] < 3.0:
            score += 0.05
        elif cpi["value"] > 5.0:
            score -= 0.1

    gdp = indicators.get("gdp_growth")
    if gdp:
        if gdp["trend"] == "up":
            score += 0.1
            reasons.append("GDP growing")
        elif gdp["trend"] == "down":
            score -= 0.12
            reasons.append("GDP slowing")

    fed = indicators.get("fed_funds_rate")
    if fed:
        if fed["trend"] in ("flat", "down"):
            score += 0.08
            reasons.append("stable rates")
        elif fed["trend"] == "up":
            score -= 0.12
            reasons.append("rising rates")

    unemp = indicators.get("unemployment")
    if unemp:
        if unemp["trend"] in ("down", "stable"):
            score += 0.05
        elif unemp["trend"] == "up":
            score -= 0.08
            reasons.append("rising unemployment")

    vix = indicators.get("vix")
    if vix:
        if vix["trend"] == "down" and vix["value"] < 20:
            score += 0.1
            reasons.append("low VIX")
        elif vix["trend"] == "up" or vix["value"] > 25:
            score -= 0.12
            reasons.append("elevated VIX")

    dxy = indicators.get("dxy")
    if dxy:
        if dxy["trend"] == "up" and dxy["value"] > 105:
            score -= 0.05
            reasons.append("strong dollar headwind")

    # VIX-based sub-classification (used by debate.py)
    us10y = indicators.get("us10y")
    if us10y:
        if us10y["trend"] == "up" and us10y["value"] > 4.5:
            score -= 0.05
            reasons.append("high bond yields")
        elif us10y["trend"] in ("down", "stable") and us10y["value"] < 4.0:
            score += 0.03

    sp500 = indicators.get("sp500")
    if sp500:
        if sp500["trend"] == "up":
            score += 0.05
            reasons.append("S&P 500 trending up")
        elif sp500["trend"] == "down":
            score -= 0.05
            reasons.append("S&P 500 declining")

    # Kalshi prediction market augmentation
    # Market-implied macro probabilities override/enrich indicator-based scoring
    if allow_kalshi:
        try:
            from kalshi_collector import load_latest as _kalshi_latest
            kalshi = _kalshi_latest()
            if kalshi and kalshi.get('regime_score_delta') is not None:
                score += kalshi['regime_score_delta']
                reasons.extend(kalshi.get('regime_reasons', []))
        except Exception:
            pass

    score = max(0.0, min(1.0, score))

    if score >= 0.60:
        regime = "risk_on"
    elif score <= 0.40:
        regime = "risk_off"
    else:
        regime = "neutral"

    if reasons:
        rationale = f"{', '.join(reasons)} = {regime.replace('_', '-').title()} environment"
    else:
        rationale = "Insufficient data for confident regime call"

    return regime, round(score, 2), rationale


# ── Main ────────────────────────────────────────────────────────────────────

UNITS = {
    "gdp_growth": "%",
    "cpi_inflation": "%",
    "fed_funds_rate": "%",
    "unemployment": "%",
    "dxy": "index",
    "vix": "index",
    "us10y": "%",
    "sp500": "index",
}

ORDER = ["gdp_growth", "cpi_inflation", "fed_funds_rate", "unemployment", "dxy", "vix", "us10y", "sp500"]


def collect_macro(allow_kalshi: bool = True) -> dict:
    # Tier 1: World Bank for GDP, CPI, unemployment
    wb = fetch_worldbank()

    # Tier 2: yfinance for VIX, DXY, rates
    yf = fetch_yfinance()

    # Merge (yfinance overrides World Bank if both present, but they should be disjoint)
    raw = {**wb, **yf}

    indicators = {}
    for key in ORDER:
        if key in raw:
            entry = raw[key]
            trend = detect_trend(key, entry["value"])
            indicators[key] = {
                "value": entry["value"],
                "unit": UNITS[key],
                "period": entry["period"],
                "trend": trend,
            }

    regime, confidence, rationale = detect_regime(
        indicators, allow_kalshi=allow_kalshi
    )

    sources = []
    if wb:
        sources.append("worldbank")
    if yf:
        sources.append("yfinance")

    return {
        "timestamp": NOW,
        "source": "+".join(sources) if sources else "none",
        "indicators": indicators,
        "regime": regime,
        "regime_confidence": confidence,
        "regime_rationale": rationale,
    }


def main(allow_kalshi: bool = True):
    report = collect_macro(allow_kalshi=allow_kalshi)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"macro_report_{ts}.json"

    # Keep last 5 macro reports
    old_reports = sorted(OUTPUT_DIR.glob("macro_report_*.json"))
    for old in old_reports[:-4]:
        old.unlink(missing_ok=True)

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Macro report written to {out_path}")
    print(f"  Regime: {report['regime']} (confidence: {report['regime_confidence']})")
    print(f"  Indicators: {list(report['indicators'].keys())}")
    print(f"  Rationale: {report['regime_rationale']}")


if __name__ == "__main__":
    main()
