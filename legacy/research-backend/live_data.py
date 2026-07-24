"""
Live market data collectors — replace stale JSON file readers.
All functions return fresh data. Call directly, don't read from disk.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("live_data")

# ── Live Macro (delegates to macro.py collect_macro) ──────────────────────────

def format_macro_report_context(report: dict) -> str:
    """Format an already collected macro report for debate prompts."""
    indicators = report.get("indicators", {})
    parts = []
    for key in ["vix", "dxy", "sp500", "us10y", "fed_funds_rate"]:
        if key in indicators:
            parts.append(f"{key.upper()} at {indicators[key]['value']}")

    regime = report.get("regime", "")
    confidence = report.get("regime_confidence", 0)
    rationale = report.get("regime_rationale", "")
    line = "Current macro: " + ", ".join(parts) + "."
    if regime:
        line += f" Regime: {regime} (confidence: {confidence})."
    if rationale:
        line += f" ({rationale[:200]})"
    return line

def get_live_macro_context(allow_kalshi: bool = True) -> str:
    """
    Return formatted macro context string for debate prompts.
    Calls collect_macro() fresh every time — no stale JSON.
    """
    try:
        from macro import collect_macro
        report = collect_macro(allow_kalshi=allow_kalshi)
        return format_macro_report_context(report)
    except Exception as e:
        log.warning("Live macro fetch failed: %s", e)
        return ""


# ── Live Fundamentals (yfinance) ──────────────────────────────────────────────

FUNDAMENTAL_FIELDS = [
    "trailingPE", "forwardPE", "priceToBook", "debtToEquity",
    "returnOnEquity", "revenueGrowth", "earningsGrowth",
    "profitMargins", "marketCap", "beta", "sector", "industry",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "shortPercentOfFloat",
]

def fetch_fundamentals(symbols: list[str]) -> dict:
    """
    Fetch live fundamentals from yfinance for a list of stock symbols.
    Returns {symbol: {pe, pb, roe, growth, ...}} dict.
    
    Falls back to None for missing data — never fabricates.
    """
    import yfinance as yf
    
    results = {}
    for symbol in symbols:
        try:
            t = yf.Ticker(symbol)
            info = t.info
            
            if not info or "symbol" not in info:
                log.warning("No yfinance data for %s", symbol)
                continue
            
            fund = {}
            for field in FUNDAMENTAL_FIELDS:
                val = info.get(field)
                if val is not None:
                    # Round floats for readability
                    fund[field] = round(float(val), 4) if isinstance(val, (int, float)) else val
            
            # Add derived metrics
            fund["name"] = info.get("longName") or info.get("shortName", symbol)
            fund["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
            fund["currency"] = info.get("currency", "USD")
            
            results[symbol] = fund
            log.info("Fetched fundamentals for %s: P/E=%.1f, P/B=%.2f",
                     symbol, fund.get("trailingPE", 0), fund.get("priceToBook", 0))
        except Exception as e:
            log.warning("Fundamentals fetch failed for %s: %s", symbol, e)
    
    return results


def format_fundamentals_context(symbol: str) -> str:
    """
    Return formatted fundamentals string for a single stock symbol.
    Used in debate and portfolio manager prompts.
    """
    data = fetch_fundamentals([symbol])
    if symbol not in data:
        return f"No fundamentals data available for {symbol}."
    
    f = data[symbol]
    lines = [f"Fundamentals for {symbol} ({f.get('name', '')}):"]
    
    if f.get("trailingPE"):
        lines.append(f"  P/E (TTM): {f['trailingPE']:.1f}")
    if f.get("forwardPE"):
        lines.append(f"  Forward P/E: {f['forwardPE']:.1f}")
    if f.get("priceToBook"):
        lines.append(f"  P/B: {f['priceToBook']:.2f}")
    if f.get("debtToEquity"):
        lines.append(f"  Debt/Equity: {f['debtToEquity']:.0f}%")
    if f.get("returnOnEquity"):
        lines.append(f"  ROE: {f['returnOnEquity']:.1%}")
    if f.get("revenueGrowth"):
        lines.append(f"  Revenue Growth: {f['revenueGrowth']:.1%}")
    if f.get("profitMargins"):
        lines.append(f"  Profit Margin: {f['profitMargins']:.1%}")
    if f.get("beta"):
        lines.append(f"  Beta: {f['beta']:.2f}")
    if f.get("marketCap"):
        cap = f["marketCap"]
        if cap > 1e12:
            lines.append(f"  Market Cap: ${cap/1e12:.1f}T")
        else:
            lines.append(f"  Market Cap: ${cap/1e9:.0f}B")
    
    return "\n".join(lines)


# ── Live Sentiment (Fear & Greed + price proxy) ──────────────────────────────

def fetch_fear_greed() -> Optional[dict]:
    """
    Fetch Crypto Fear & Greed Index from alternative.me API.
    Free, no API key required.
    Returns {value: 0-100, classification: "Fear"/"Greed"/etc, timestamp: iso}
    """
    import urllib.request
    
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        
        item = data["data"][0]
        return {
            "value": int(item["value"]),
            "classification": item["value_classification"],
            "timestamp": datetime.fromtimestamp(
                int(item["timestamp"]), tz=timezone.utc
            ).isoformat(),
        }
    except Exception as e:
        log.warning("Fear & Greed fetch failed: %s", e)
        return None


def get_sentiment_context(asset: str, change_24h: float = 0) -> dict:
    """
    Get live sentiment context for an asset.
    Returns dict with sentiment metrics.
    
    Uses Fear & Greed Index for crypto, price-action proxy for stocks.
    """
    fg = fetch_fear_greed()
    
    result = {
        "asset": asset,
        "fear_greed_index": fg["value"] if fg else None,
        "fear_greed_label": fg["classification"] if fg else None,
    }
    
    # Price-action sentiment proxy (always available)
    if change_24h > 10:
        result["price_sentiment"] = "extremely_bullish"
        result["social_volume"] = "spiking"
    elif change_24h > 5:
        result["price_sentiment"] = "strongly_bullish"
        result["social_volume"] = "high"
    elif change_24h > 2:
        result["price_sentiment"] = "bullish"
        result["social_volume"] = "elevated"
    elif change_24h < -10:
        result["price_sentiment"] = "extremely_bearish"
        result["social_volume"] = "spiking"
    elif change_24h < -5:
        result["price_sentiment"] = "strongly_bearish"
        result["social_volume"] = "high"
    elif change_24h < -2:
        result["price_sentiment"] = "bearish"
        result["social_volume"] = "elevated"
    else:
        result["price_sentiment"] = "neutral"
        result["social_volume"] = "normal"
    
    # Funding rate bias
    result["funding_bias"] = "neutral"  # Updated by derivatives collector
    
    return result


# ── On-Chain Data Proxy (Binance large trades) ────────────────────────────────

def fetch_binance_large_trades(symbol: str, limit: int = 50) -> Optional[dict]:
    """
    Fetch recent large trades from Binance as on-chain data proxy.
    Free, no API key required.
    """
    import urllib.request
    
    binance_symbol = {
        "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
        "TON": "TONUSDT", "DOGE": "DOGEUSDT", "ADA": "ADAUSDT",
        "AVAX": "AVAXUSDT", "DOT": "DOTUSDT", "LINK": "LINKUSDT",
        "MATIC": "MATICUSDT",
    }.get(symbol.upper())
    
    if not binance_symbol:
        return None
    
    try:
        url = f"https://api.binance.com/api/v3/trades?symbol={binance_symbol}&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            trades = json.loads(resp.read().decode())
        
        if not trades:
            return None
        
        # Analyze trade patterns
        total_buy_vol = sum(float(t["quoteQty"]) for t in trades if not t["isBuyerMaker"])
        total_sell_vol = sum(float(t["quoteQty"]) for t in trades if t["isBuyerMaker"])
        total_vol = total_buy_vol + total_sell_vol
        
        # Find large trades (>$10k)
        large_threshold = 10000
        large_buys = [t for t in trades if not t["isBuyerMaker"] and float(t["quoteQty"]) > large_threshold]
        large_sells = [t for t in trades if t["isBuyerMaker"] and float(t["quoteQty"]) > large_threshold]
        
        buy_ratio = total_buy_vol / total_vol if total_vol > 0 else 0.5
        
        # Determine whale activity
        if len(large_buys) > len(large_sells) * 1.5:
            whale_activity = "accumulating"
        elif len(large_sells) > len(large_buys) * 1.5:
            whale_activity = "distributing"
        else:
            whale_activity = "neutral"
        
        return {
            "symbol": symbol,
            "recent_trades": len(trades),
            "buy_volume_ratio": round(buy_ratio, 3),
            "large_buy_count": len(large_buys),
            "large_sell_count": len(large_sells),
            "whale_activity": whale_activity,
            "exchange_flow_signal": "inflow" if buy_ratio < 0.45 else "outflow" if buy_ratio > 0.55 else "neutral",
        }
    except Exception as e:
        log.warning("Binance large trades fetch failed for %s: %s", symbol, e)
        return None
