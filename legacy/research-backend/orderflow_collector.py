"""
orderflow_collector.py
Footprint candle builder using CCXT recent-trades data.

Aggregates raw trades into per-price-level bid/ask volume buckets,
computes delta, POC, and stacked imbalance signals.
"""

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from runtime_paths import reports_dir

log = logging.getLogger("orderflow_collector")

REPORTS_DIR = reports_dir()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

CCXT_SYMBOL_MAP = {
    "BTC":  "BTC/USDT",
    "ETH":  "ETH/USDT",
    "SOL":  "SOL/USDT",
    "TON":  "TON/USDT",
    "DOGE": "DOGE/USDT",
}

IMBALANCE_THRESHOLD = 3.0   # bid/ask ratio > 3x = imbalance at a level
STACKED_MIN         = 3     # consecutive imbalanced levels = stacked signal
FETCH_LIMIT         = 1000  # max trades per fetch


def _auto_tick_size(prices: list[float]) -> float:
    """Estimate a reasonable tick bucket size from price range."""
    if not prices:
        return 1.0
    lo, hi = min(prices), max(prices)
    spread = hi - lo
    if spread <= 0:
        return 1.0
    raw = spread / 50          # ~50 buckets across range
    magnitude = 10 ** math.floor(math.log10(raw))
    for step in [1, 2, 2.5, 5, 10]:
        candidate = step * magnitude
        if candidate >= raw:
            return candidate
    return magnitude * 10


def _bucket_price(price: float, tick: float) -> float:
    """Round price down to nearest tick boundary."""
    return math.floor(price / tick) * tick


def build_footprint(trades: list[dict]) -> dict:
    """
    Aggregate a list of CCXT trade dicts into footprint candle metrics.

    Each trade: {price, amount, side ('buy'|'sell'), timestamp, ...}
    Returns footprint dict with delta, poc_price, imbalances, stacked_imbalances.
    """
    if not trades:
        return {}

    prices = [float(t["price"]) for t in trades]
    tick   = _auto_tick_size(prices)

    bid_vol: dict[float, float] = {}
    ask_vol: dict[float, float] = {}
    total_bid = 0.0
    total_ask = 0.0

    for t in trades:
        price  = float(t["price"])
        amount = float(t["amount"])
        bucket = _bucket_price(price, tick)
        if t.get("side") == "buy":
            bid_vol[bucket] = bid_vol.get(bucket, 0.0) + amount
            total_bid += amount
        else:
            ask_vol[bucket] = ask_vol.get(bucket, 0.0) + amount
            total_ask += amount

    all_buckets = sorted(set(bid_vol) | set(ask_vol))
    if not all_buckets:
        return {}

    delta     = total_bid - total_ask
    delta_pct = delta / (total_bid + total_ask) if (total_bid + total_ask) > 0 else 0.0

    # POC = bucket with highest total volume
    poc_price = max(
        all_buckets,
        key=lambda b: bid_vol.get(b, 0.0) + ask_vol.get(b, 0.0),
    )

    # Imbalance: levels where bid/ask ratio > threshold
    imbalances: list[dict] = []
    for b in all_buckets:
        bv = bid_vol.get(b, 0.0)
        av = ask_vol.get(b, 0.0)
        if av > 0 and bv / av >= IMBALANCE_THRESHOLD:
            imbalances.append({"price": b, "direction": "bid", "ratio": round(bv / av, 2)})
        elif bv > 0 and av / bv >= IMBALANCE_THRESHOLD:
            imbalances.append({"price": b, "direction": "ask", "ratio": round(av / bv, 2)})

    # Stacked imbalances: STACKED_MIN consecutive levels in same direction
    stacked: list[dict] = []
    if len(imbalances) >= STACKED_MIN:
        run_dir   = None
        run_start = 0
        run_prices = []
        for i, im in enumerate(imbalances):
            if im["direction"] == run_dir:
                run_prices.append(im["price"])
            else:
                if len(run_prices) >= STACKED_MIN:
                    stacked.append({
                        "direction": run_dir,
                        "count":     len(run_prices),
                        "price_lo":  min(run_prices),
                        "price_hi":  max(run_prices),
                    })
                run_dir    = im["direction"]
                run_prices = [im["price"]]
        if len(run_prices) >= STACKED_MIN:
            stacked.append({
                "direction": run_dir,
                "count":     len(run_prices),
                "price_lo":  min(run_prices),
                "price_hi":  max(run_prices),
            })

    return {
        "tick_size":           tick,
        "total_bid_vol":       round(total_bid, 4),
        "total_ask_vol":       round(total_ask, 4),
        "delta":               round(delta, 4),
        "delta_pct":           round(delta_pct, 4),
        "poc_price":           poc_price,
        "imbalance_count":     len(imbalances),
        "stacked_imbalances":  stacked,
        "trade_count":         len(trades),
    }


def _derive_signal(fp: dict) -> dict:
    """
    Convert footprint metrics into a directional signal.
    Returns {direction, confidence, reasons}.
    """
    direction = "NEUTRAL"
    confidence = 0.0
    reasons: list[str] = []

    delta_pct = fp.get("delta_pct", 0.0)
    stacked   = fp.get("stacked_imbalances", [])

    # Delta bias
    if delta_pct > 0.15:
        direction   = "BULLISH"
        confidence += 0.4
        reasons.append(f"Buy delta dominance {delta_pct:.1%}")
    elif delta_pct < -0.15:
        direction   = "BEARISH"
        confidence += 0.4
        reasons.append(f"Sell delta dominance {delta_pct:.1%}")

    # Stacked imbalances
    for s in stacked:
        if s["direction"] == "bid":
            if direction != "BEARISH":
                direction   = "BULLISH"
            confidence += min(0.2 * s["count"], 0.4)
            reasons.append(
                f"Stacked bid imbalance x{s['count']} "
                f"@ {s['price_lo']:.2f}-{s['price_hi']:.2f}"
            )
        else:
            if direction != "BULLISH":
                direction   = "BEARISH"
            confidence += min(0.2 * s["count"], 0.4)
            reasons.append(
                f"Stacked ask imbalance x{s['count']} "
                f"@ {s['price_lo']:.2f}-{s['price_hi']:.2f}"
            )

    confidence = min(confidence, 1.0)
    if confidence < 0.3:
        direction = "NEUTRAL"

    return {
        "direction":  direction,
        "confidence": round(confidence, 3),
        "reasons":    reasons,
    }


async def fetch_and_build(symbol: str, exchange_id: str = "binance") -> Optional[dict]:
    """
    Fetch recent trades for one symbol via CCXT and build footprint.
    Returns footprint dict with signal, or None on error.
    """
    try:
        import ccxt.async_support as ccxt_async
    except ImportError:
        log.warning("ccxt not installed — orderflow_collector disabled")
        return None

    ccxt_symbol = CCXT_SYMBOL_MAP.get(symbol)
    if not ccxt_symbol:
        return None

    exchange = None
    try:
        exchange = getattr(ccxt_async, exchange_id)({"enableRateLimit": True})
        trades   = await exchange.fetch_trades(ccxt_symbol, limit=FETCH_LIMIT)
        fp       = build_footprint(trades)
        if not fp:
            return None
        fp["symbol"]    = symbol
        fp["signal"]    = _derive_signal(fp)
        fp["timestamp"] = datetime.now(timezone.utc).isoformat()
        return fp
    except Exception as exc:
        log.warning("[%s] fetch_and_build error: %s", symbol, exc)
        return None
    finally:
        if exchange:
            try:
                await exchange.close()
            except Exception:
                pass


async def collect_all(symbols: list[str]) -> dict[str, dict]:
    """Fetch footprints for all symbols concurrently, save reports."""
    tasks   = {sym: fetch_and_build(sym) for sym in symbols}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    out: dict[str, dict] = {}
    for sym, res in zip(tasks.keys(), results):
        if isinstance(res, Exception) or res is None:
            continue
        out[sym] = res
        _save_report(sym, res)

    return out


def _save_report(symbol: str, data: dict) -> None:
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = REPORTS_DIR / f"orderflow_{symbol}_{ts}.json"
    path.write_text(json.dumps(data, indent=2))

    # Keep only last 5 per symbol
    existing = sorted(REPORTS_DIR.glob(f"orderflow_{symbol}_*.json"))
    for old in existing[:-5]:
        old.unlink(missing_ok=True)


def get_orderflow_for_symbol(symbol: str) -> Optional[dict]:
    """
    Load the most recent cached orderflow report for a symbol.
    Returns a signal dict compatible with assembly.py or None.
    """
    files = sorted(REPORTS_DIR.glob(f"orderflow_{symbol}_*.json"))
    if not files:
        return None
    try:
        data   = json.loads(files[-1].read_text())
        signal = data.get("signal", {})
        return {
            "orderflow_direction":  signal.get("direction", "NEUTRAL"),
            "orderflow_confidence": signal.get("confidence", 0.0),
            "orderflow_reasons":    signal.get("reasons", []),
            "delta_pct":            data.get("delta_pct", 0.0),
            "stacked_imbalances":   data.get("stacked_imbalances", []),
            "timestamp":            data.get("timestamp"),
        }
    except Exception as exc:
        log.warning("[%s] Failed to load orderflow cache: %s", symbol, exc)
        return None


def collect(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """Sync wrapper — runs collect_all in a new event loop."""
    if symbols is None:
        try:
            from config import CRYPTO_WATCHLIST
            symbols = CRYPTO_WATCHLIST
        except ImportError:
            symbols = ["BTC", "ETH", "SOL"]
    return asyncio.run(collect_all(symbols))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for sym, fp in results.items():
        sig = fp.get("signal", {})
        print(f"{sym}: {sig.get('direction')} conf={sig.get('confidence'):.2f} | "
              f"delta={fp.get('delta_pct', 0):.1%} | "
              f"stacked={len(fp.get('stacked_imbalances', []))}")
