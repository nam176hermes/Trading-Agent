"""
atr_stops.py
------------
ATR-based stop-loss and target calculator.
Replaces the fixed 7-8% band logic in assembly.py.

Drop-in usage in assembly.py:
    from atr_stops import calculate_atr, calculate_stop_target

The function signature of calculate_stop_target() is intentionally
identical to the old one — no changes needed in assemble_asset_json().
"""

import logging
import pandas as pd
from typing import Optional

log = logging.getLogger("atr_stops")


# ── ATR Calculation ───────────────────────────────────────────────────────────

def calculate_atr(ohlcv: list[dict], period: int = 14) -> Optional[float]:
    """
    Calculates Average True Range over `period` candles.

    True Range for each candle = max of:
      - high - low                        (normal candle range)
      - abs(high - previous_close)        (gap up)
      - abs(low  - previous_close)        (gap down)

    ATR = rolling mean of True Range over `period`.

    Args:
        ohlcv:  List of candle dicts with keys: high, low, close
                (same format produced by data_collector.py)
        period: ATR period. Default 14 — industry standard.

    Returns:
        float: ATR value in price units (e.g. $2,800 for BTC)
        None:  if insufficient data
    """
    if not ohlcv or len(ohlcv) < period + 1:
        log.warning("Insufficient candles for ATR-%d calculation (%d provided).", period, len(ohlcv) if ohlcv else 0)
        return None

    df = pd.DataFrame(ohlcv)

    # Validate required columns
    required = {"high", "low", "close"}
    if not required.issubset(df.columns):
        log.error("OHLCV data missing required columns for ATR. Found: %s", list(df.columns))
        return None

    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    df["prev_close"] = df["close"].shift(1)

    df["tr"] = df[["high", "low", "prev_close"]].apply(
        lambda row: max(
            row["high"] - row["low"],
            abs(row["high"] - row["prev_close"]) if pd.notna(row["prev_close"]) else 0,
            abs(row["low"]  - row["prev_close"]) if pd.notna(row["prev_close"]) else 0,
        ),
        axis=1,
    )

    # Use Wilder's smoothing (standard for ATR) — equivalent to EWM with alpha=1/period
    atr_series = df["tr"].ewm(alpha=1 / period, adjust=False).mean()
    atr_value  = round(float(atr_series.iloc[-1]), 6)

    log.debug("ATR-%d calculated: %s", period, atr_value)
    return atr_value


# ── ATR Percentage Helper ─────────────────────────────────────────────────────

def atr_as_pct(atr: float, price: float) -> Optional[float]:
    """
    Express ATR as a percentage of current price.
    Useful for logging and context ("ATR is 3.2% of price").
    """
    if not price or price == 0:
        return None
    return round((atr / price) * 100, 2)


# ── Stop / Target Calculator ──────────────────────────────────────────────────

def calculate_stop_target(
    price: float,
    ohlcv: list[dict],
    suggestion: str,
    atr_period: int = 14,
    stop_multiplier: float = 2.0,
    target_multiplier: float = 3.0,
) -> dict:
    """
    Calculates ATR-based stop-loss and target price.

    Replaces the fixed-band version in assembly.py.
    Signature is intentionally compatible — assemble_asset_json() needs
    no changes other than importing this function.

    Args:
        price:             Current asset price
        ohlcv:             Raw candle list from data_collector.py
        suggestion:        Signal string ("BUY", "SELL", "WATCH FOR ENTRY", etc.)
        atr_period:        ATR lookback period (default: 14)
        stop_multiplier:   ATR multiple for stop distance (default: 2.0)
        target_multiplier: ATR multiple for target distance (default: 3.0)

    Returns dict with:
        stop_loss_suggestion:  float or None
        target_suggestion:     float or None
        atr_14:                float or None  (new field — add to JSON schema)
        atr_pct:               float or None  (ATR as % of price — context)
        stop_method:           str            (always "atr" — audit trail)
        stop_note:             str            (human-readable explanation)

    R:R ratio with defaults: 1 : 1.5 (risking 2× ATR to make 3× ATR)
    """
    # Null-safe defaults
    null_result = {
        "stop_loss_suggestion": None,
        "target_suggestion":    None,
        "atr_14":               None,
        "atr_pct":              None,
        "stop_method":          "atr",
        "stop_note":            "unavailable — insufficient OHLCV data",
    }

    if not price or not ohlcv:
        log.warning("calculate_stop_target: missing price or OHLCV. Returning null.")
        return null_result

    atr = calculate_atr(ohlcv, period=atr_period)
    if atr is None:
        return null_result

    pct = atr_as_pct(atr, price)

    # Neutral / conflicted / no-signal: don't issue stop or target
    if suggestion in ("NO SIGNAL", "HOLD", None, ""):
        return {
            "stop_loss_suggestion": None,
            "target_suggestion":    None,
            "atr_14":               atr,
            "atr_pct":              pct,
            "stop_method":          "atr",
            "stop_note":            f"No stop issued — signal is '{suggestion}'. ATR-14: {atr} ({pct}% of price).",
        }

    stop_distance   = atr * stop_multiplier
    target_distance = atr * target_multiplier

    if suggestion in ("BUY", "WATCH FOR ENTRY"):
        stop_loss = round(price - stop_distance, 6)
        target    = round(price + target_distance, 6)
        direction = "long"
    elif suggestion == "SELL":
        stop_loss = round(price + stop_distance, 6)  # stop above for shorts
        target    = round(price - target_distance, 6)
        direction = "short"
    else:
        # Unrecognised signal — return ATR context but no levels
        return {
            "stop_loss_suggestion": None,
            "target_suggestion":    None,
            "atr_14":               atr,
            "atr_pct":              pct,
            "stop_method":          "atr",
            "stop_note":            f"Unrecognised signal '{suggestion}'. ATR-14: {atr} ({pct}% of price).",
        }

    note = (
        f"{direction.upper()} | ATR-{atr_period}: {atr} ({pct}% of price) | "
        f"Stop: {stop_multiplier}× ATR = {round(stop_distance, 4)} | "
        f"Target: {target_multiplier}× ATR = {round(target_distance, 4)} | "
        f"R:R ≈ 1:{round(target_multiplier / stop_multiplier, 1)}"
    )

    log.info("[ATR STOP] %s", note)

    return {
        "stop_loss_suggestion": stop_loss,
        "target_suggestion":    target,
        "atr_14":               atr,
        "atr_pct":              pct,
        "stop_method":          "atr",
        "stop_note":            note,
    }


# ── Sanity check / standalone test ───────────────────────────────────────────

if __name__ == "__main__":
    # Synthetic candles — replace with real OHLCV from data_collector in production
    import random
    random.seed(42)

    base = 94000.0
    fake_ohlcv = []
    for i in range(210):
        o = base + random.uniform(-800, 800)
        h = o  + random.uniform(200, 1800)
        l = o  - random.uniform(200, 1800)
        c = o  + random.uniform(-600, 600)
        v = random.uniform(10000, 50000)
        fake_ohlcv.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
        base = c

    price = fake_ohlcv[-1]["close"]
    print(f"\nTest price: ${price:,.2f}")

    atr = calculate_atr(fake_ohlcv)
    print(f"ATR-14:     ${atr:,.2f}  ({atr_as_pct(atr, price)}% of price)")

    for signal in ["BUY", "SELL", "WATCH FOR ENTRY", "NO SIGNAL"]:
        result = calculate_stop_target(price, fake_ohlcv, signal)
        print(f"\nSignal: {signal}")
        print(f"  Stop loss : {result['stop_loss_suggestion']}")
        print(f"  Target    : {result['target_suggestion']}")
        print(f"  Note      : {result['stop_note']}")
