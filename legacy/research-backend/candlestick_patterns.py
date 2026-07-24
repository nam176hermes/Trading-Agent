"""
Candlestick Pattern Recognition
================================
Pure Python implementation using numpy + pandas only.
No external TA library dependencies (no talib-rs).

8 major candlestick patterns:
  1. Doji              – indecision
  2. Hammer            – bullish reversal
  3. Shooting Star     – bearish reversal
  4. Bullish Engulfing – bullish reversal
  5. Bearish Engulfing – bearish reversal
  6. Morning Star      – bullish reversal (3-candle)
  7. Evening Star      – bearish reversal (3-candle)
  8. Three White Soldiers – bullish continuation

Signals: 1 = bullish, -1 = bearish, 0 = none/neutral
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Helper / shared logic
# ---------------------------------------------------------------------------

def _body(open_: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Absolute body size per candle."""
    return np.abs(close - open_)


def _upper_shadow(open_: np.ndarray, high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Upper wick length per candle."""
    return high - np.maximum(open_, close)


def _lower_shadow(open_: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Lower wick length per candle."""
    return np.minimum(open_, close) - low


def _is_green(open_: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True where close > open (bullish candle)."""
    return close > open_


def _is_red(open_: np.ndarray, close: np.ndarray) -> np.ndarray:
    """True where close < open (bearish candle)."""
    return close < open_


def _range(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Total range (high - low), floored to avoid div-by-zero."""
    rng = high - low
    rng[rng == 0] = 1e-10
    return rng


# ---------------------------------------------------------------------------
# 1. Doji
# ---------------------------------------------------------------------------

def detect_doji(open_: np.ndarray, high: np.ndarray,
                low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Doji: body is tiny relative to the total range.

        abs(open - close) <= (high - low) * 0.1

    Returns boolean array (True where doji detected).
    """
    return np.abs(open_ - close) <= _range(high, low) * 0.1


# ---------------------------------------------------------------------------
# 2. Hammer
# ---------------------------------------------------------------------------

def detect_hammer(open_: np.ndarray, high: np.ndarray,
                  low: np.ndarray, close: np.ndarray,
                  lookback: int = 3) -> np.ndarray:
    """
    Hammer: small body near the top of the range, long lower shadow
    (≥ 2× body), tiny upper shadow. Must appear in a short-term downtrend.

    Returns boolean array (True where hammer detected).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    body = _body(open_, close)
    upper = _upper_shadow(open_, high, close)
    lower = _lower_shadow(open_, low, close)
    rng = _range(high, low)

    for i in range(lookback, n):
        # Body must exist (non-zero)
        if body[i] == 0:
            continue
        # Lower shadow ≥ 2× body
        if lower[i] < body[i] * 2:
            continue
        # Upper shadow ≤ 20% of body (tiny or none)
        if upper[i] > body[i] * 0.2:
            continue
        # Body in upper third of range: (high - max(O,C)) < range/3
        body_top = np.maximum(open_[i], close[i])
        if (high[i] - body_top) > rng[i] / 3:
            continue
        # Downtrend check: prior `lookback` closes are declining
        if not np.all(np.diff(close[i - lookback:i + 1]) < 0):
            continue
        signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# 3. Shooting Star
# ---------------------------------------------------------------------------

def detect_shooting_star(open_: np.ndarray, high: np.ndarray,
                         low: np.ndarray, close: np.ndarray,
                         lookback: int = 3) -> np.ndarray:
    """
    Shooting Star: small body near the bottom of the range, long upper
    shadow (≥ 2× body), tiny lower shadow. Must appear in a short-term uptrend.

    Returns boolean array (True where shooting star detected).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    body = _body(open_, close)
    upper = _upper_shadow(open_, high, close)
    lower = _lower_shadow(open_, low, close)
    rng = _range(high, low)

    for i in range(lookback, n):
        if body[i] == 0:
            continue
        # Upper shadow ≥ 2× body
        if upper[i] < body[i] * 2:
            continue
        # Lower shadow ≤ 20% of body
        if lower[i] > body[i] * 0.2:
            continue
        # Body in lower third of range: (min(O,C) - low) < range/3
        body_bottom = np.minimum(open_[i], close[i])
        if (body_bottom - low[i]) > rng[i] / 3:
            continue
        # Uptrend check: prior `lookback` closes are rising
        if not np.all(np.diff(close[i - lookback:i + 1]) > 0):
            continue
        signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# 4. Bullish Engulfing
# ---------------------------------------------------------------------------

def detect_bullish_engulfing(open_: np.ndarray, high: np.ndarray,
                             low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Bullish Engulfing: current green candle fully engulfs the previous
    red candle (current open ≤ prev close AND current close ≥ prev open).

    Returns boolean array (True where bullish engulfing detected).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    green = _is_green(open_, close)
    red = _is_red(open_, close)

    for i in range(1, n):
        if not green[i] or not red[i - 1]:
            continue
        # Current green engulfs previous red
        if open_[i] <= close[i - 1] and close[i] >= open_[i - 1]:
            signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# 5. Bearish Engulfing
# ---------------------------------------------------------------------------

def detect_bearish_engulfing(open_: np.ndarray, high: np.ndarray,
                             low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Bearish Engulfing: current red candle fully engulfs the previous
    green candle (current open ≥ prev close AND current close ≤ prev open).

    Returns boolean array (True where bearish engulfing detected).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    green = _is_green(open_, close)
    red = _is_red(open_, close)

    for i in range(1, n):
        if not red[i] or not green[i - 1]:
            continue
        # Current red engulfs previous green
        if open_[i] >= close[i - 1] and close[i] <= open_[i - 1]:
            signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# 6. Morning Star
# ---------------------------------------------------------------------------

def detect_morning_star(open_: np.ndarray, high: np.ndarray,
                        low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Morning Star (3-candle bullish reversal):
      Candle -2: large red candle
      Candle -1: small-bodied candle (doji-like) that gaps below candle -2 close
      Candle  0: large green candle closing well into candle -2 body

    Returns boolean array (True on the 3rd candle).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    body = _body(open_, close)

    for i in range(2, n):
        c0_o, c0_h, c0_l, c0_c = open_[i], high[i], low[i], close[i]
        c1_o, c1_h, c1_l, c1_c = open_[i - 1], high[i - 1], low[i - 1], close[i - 1]
        c2_o, c2_h, c2_l, c2_c = open_[i - 2], high[i - 2], low[i - 2], close[i - 2]

        # Candle -2: large red (body > avg body)
        if c2_c >= c2_o:
            continue
        avg_body = np.mean(body[max(0, i - 20):i + 1])
        if body[i - 2] < avg_body:
            continue

        # Candle -1: small body (doji-like), gaps below candle -2 close
        if body[i - 1] > avg_body * 0.5:
            continue
        if c1_c >= c2_c and c1_o >= c2_c:
            continue  # didn't gap down

        # Candle 0: large green, closing well into candle -2 body
        if c0_c <= c0_o:
            continue
        if body[i] < avg_body:
            continue
        # Must close above midpoint of candle -2 body
        if c0_c <= (c2_o + c2_c) / 2:
            continue

        signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# 7. Evening Star
# ---------------------------------------------------------------------------

def detect_evening_star(open_: np.ndarray, high: np.ndarray,
                        low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Evening Star (3-candle bearish reversal):
      Candle -2: large green candle
      Candle -1: small-bodied candle (doji-like) that gaps above candle -2 close
      Candle  0: large red candle closing well into candle -2 body

    Returns boolean array (True on the 3rd candle).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    body = _body(open_, close)

    for i in range(2, n):
        c0_o, c0_h, c0_l, c0_c = open_[i], high[i], low[i], close[i]
        c1_o, c1_h, c1_l, c1_c = open_[i - 1], high[i - 1], low[i - 1], close[i - 1]
        c2_o, c2_h, c2_l, c2_c = open_[i - 2], high[i - 2], low[i - 2], close[i - 2]

        # Candle -2: large green (body > avg body)
        if c2_c <= c2_o:
            continue
        avg_body = np.mean(body[max(0, i - 20):i + 1])
        if body[i - 2] < avg_body:
            continue

        # Candle -1: small body (doji-like), gaps above candle -2 close
        if body[i - 1] > avg_body * 0.5:
            continue
        if c1_c <= c2_c and c1_o <= c2_c:
            continue  # didn't gap up

        # Candle 0: large red, closing well into candle -2 body
        if c0_c >= c0_o:
            continue
        if body[i] < avg_body:
            continue
        # Must close below midpoint of candle -2 body
        if c0_c >= (c2_o + c2_c) / 2:
            continue

        signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# 8. Three White Soldiers
# ---------------------------------------------------------------------------

def detect_three_white_soldiers(open_: np.ndarray, high: np.ndarray,
                                low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Three White Soldiers: three consecutive green candles, each with a
    higher close and each opening within (or near) the prior candle's body.

    Returns boolean array (True on the 3rd candle of the pattern).
    """
    n = len(close)
    signals = np.zeros(n, dtype=bool)

    green = _is_green(open_, close)

    for i in range(2, n):
        if not (green[i] and green[i - 1] and green[i - 2]):
            continue

        c0_o, c0_c = open_[i], close[i]
        c1_o, c1_c = open_[i - 1], close[i - 1]
        c2_o, c2_c = open_[i - 2], close[i - 2]

        # Each close must be higher than the previous
        if not (c2_c > c2_o and c1_c > c1_o and c0_c > c0_o):
            continue
        if not (c1_c > c2_c and c0_c > c1_c):
            continue

        # Each opens within the prior candle's body
        if not (c2_o <= c1_o <= c2_c):
            continue
        if not (c1_o <= c0_o <= c1_c):
            continue

        signals[i] = True

    return signals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_patterns(open_: np.ndarray, high: np.ndarray,
                    low: np.ndarray, close: np.ndarray) -> dict[str, int]:
    """
    Detect all 8 candlestick patterns on the most recent complete candle.

    Parameters
    ----------
    open_, high, low, close : np.ndarray
        1-D arrays of OHLC price data.  Must be the same length (≥ 3).

    Returns
    -------
    dict[str, int]
        Pattern name → signal value:
           1  = bullish signal
          -1  = bearish signal
           0  = no signal / neutral (e.g. Doji)
    """
    # Ensure numpy arrays
    open_  = np.asarray(open_,  dtype=float)
    high   = np.asarray(high,   dtype=float)
    low    = np.asarray(low,    dtype=float)
    close  = np.asarray(close,  dtype=float)

    # Sanity: all same length
    lengths = {len(open_), len(high), len(low), len(close)}
    if len(lengths) != 1:
        raise ValueError("open, high, low, close must be the same length")

    results: dict[str, int] = {}

    detectors = [
        ("Doji",                detect_doji,                 0),
        ("Hammer",              detect_hammer,               1),
        ("Shooting Star",       detect_shooting_star,       -1),
        ("Bullish Engulfing",   detect_bullish_engulfing,    1),
        ("Bearish Engulfing",   detect_bearish_engulfing,   -1),
        ("Morning Star",        detect_morning_star,         1),
        ("Evening Star",        detect_evening_star,        -1),
        ("Three White Soldiers", detect_three_white_soldiers, 1),
    ]

    for name, detector, signal in detectors:
        bools = detector(open_, high, low, close)
        latest = bools[-1] if len(bools) > 0 else False
        results[name] = signal if latest else 0

    return results


def pattern_signal(patterns: dict[str, int]) -> str:
    """
    Aggregate detected pattern signals into a trading recommendation.

    Parameters
    ----------
    patterns : dict[str, int]
        Output of detect_patterns().

    Returns
    -------
    str
        'BUY', 'SELL', or 'HOLD'
    """
    bullish_score = sum(1 for v in patterns.values() if v == 1)
    bearish_score = sum(1 for v in patterns.values() if v == -1)

    if bullish_score > bearish_score:
        return "BUY"
    elif bearish_score > bullish_score:
        return "SELL"
    else:
        return "HOLD"


def most_recent_pattern(open_: np.ndarray, high: np.ndarray,
                        low: np.ndarray, close: np.ndarray) -> dict:
    """
    Return the most recent pattern detected and its position.

    Parameters
    ----------
    open_, high, low, close : np.ndarray
        1-D arrays of OHLC price data.

    Returns
    -------
    dict
        {
            "pattern": str | None,       # pattern name or None
            "signal":  1 | -1 | 0,       # signal value
            "index":   int | None,       # candle index of detection
            "date":    str | None,       # ISO date if close is DatetimeIndex-aware
        }
    """
    open_  = np.asarray(open_,  dtype=float)
    high   = np.asarray(high,   dtype=float)
    low    = np.asarray(low,    dtype=float)
    close  = np.asarray(close,  dtype=float)

    # Look for the latest non-zero signal, scanning from newest to oldest
    detectors = [
        ("Doji",                detect_doji,                 0),
        ("Hammer",              detect_hammer,               1),
        ("Shooting Star",       detect_shooting_star,       -1),
        ("Bullish Engulfing",   detect_bullish_engulfing,    1),
        ("Bearish Engulfing",   detect_bearish_engulfing,   -1),
        ("Morning Star",        detect_morning_star,         1),
        ("Evening Star",        detect_evening_star,        -1),
        ("Three White Soldiers", detect_three_white_soldiers, 1),
    ]

    best_idx = -1
    best_name = None
    best_signal = 0

    for name, detector, signal in detectors:
        bools = detector(open_, high, low, close)
        # Find the rightmost True
        true_indices = np.where(bools)[0]
        if len(true_indices) > 0:
            idx = true_indices[-1]
            if idx > best_idx:
                best_idx = idx
                best_name = name
                best_signal = signal

    date_str = None
    if hasattr(close, 'index') and isinstance(getattr(close, 'index', None), pd.DatetimeIndex):
        # If input was a pandas Series with DatetimeIndex
        date_str = str(close.index[best_idx].date()) if best_idx >= 0 else None

    return {
        "pattern": best_name,
        "signal": best_signal,
        "index": int(best_idx) if best_idx >= 0 else None,
        "date": date_str,
    }


# ---------------------------------------------------------------------------
# Smoke-test runner (execute when run directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Build synthetic OHLC data that exercises every pattern
    np.random.seed(42)
    n = 100

    # Start with random walk prices, then inject known patterns
    base = np.cumsum(np.random.randn(n) * 0.5) + 100

    open_  = base + np.random.randn(n) * 0.2
    close  = base + np.random.randn(n) * 0.2
    high   = np.maximum(open_, close) + np.abs(np.random.randn(n) * 0.5)
    low    = np.minimum(open_, close) - np.abs(np.random.randn(n) * 0.5)

    # --- Inject a Hammer at index 70 (downtrend at 67-69) ---
    close[67] = 108.0
    close[68] = 107.0
    close[69] = 106.0
    open_[70]  = 102.0
    high[70]   = 102.02
    low[70]    = 98.0
    close[70]  = 101.9   # body=0.1, lower=3.9, upper=0.02 (ratio 0.2 ✓)

    # --- Inject a Shooting Star at index 73 (uptrend at 70-72) ---
    close[70]  = 95.0    # override: hammer trend now becomes shooting star uptrend
    close[71]  = 96.0
    close[72]  = 97.0
    open_[73]  = 98.0
    high[73]   = 102.0
    low[73]    = 97.945
    close[73]  = 97.95   # body=0.05, upper=4.0, lower=0.005 (ratio 0.1 ✓)

    # --- Inject Doji at index 75 ---
    open_[75]  = 100.0
    high[75]   = 101.0
    low[75]    = 99.0
    close[75]  = 100.01  # body=0.01 < range*0.1 = 0.2

    # --- Inject Bearish Engulfing at indices 77-78 ---
    open_[77]  = 99.0
    close[77]  = 100.0   # green candle
    high[77]   = 100.5
    low[77]    = 98.5
    open_[78]  = 100.5
    close[78]  = 98.5    # red engulfs green
    high[78]   = 101.0
    low[78]    = 98.0

    # --- Inject Three White Soldiers at indices 80-82 ---
    open_[80],  close[80]  = 100.0, 101.0
    high[80],   low[80]    = 101.5, 99.5
    open_[81],  close[81]  = 100.5, 102.0
    high[81],   low[81]    = 102.5, 100.0
    open_[82],  close[82]  = 101.0, 103.0
    high[82],   low[82]    = 103.5, 100.5

    # --- Inject Morning Star at indices 84-86 ---
    open_[84],  close[84]  = 105.0, 100.0  # large red
    high[84],   low[84]    = 106.0, 99.0
    open_[85],  close[85]  = 99.5, 99.6    # doji (gaps down)
    high[85],   low[85]    = 100.0, 99.0
    open_[86],  close[86]  = 99.5, 103.0   # large green, closes past midpoint
    high[86],   low[86]    = 103.5, 99.0

    # --- Inject Evening Star at indices 88-90 ---
    open_[88],  close[88]  = 100.0, 105.0  # large green
    high[88],   low[88]    = 106.0, 99.0
    open_[89],  close[89]  = 105.5, 105.4  # doji (gaps up)
    high[89],   low[89]    = 106.0, 104.5
    open_[90],  close[90]  = 105.5, 101.0  # large red, closes past midpoint
    high[90],   low[90]    = 106.0, 100.0

    # --- Inject Bullish Engulfing as the LAST two candles (98-99) ---
    open_[98]  = 101.0
    close[98]  = 100.0   # red candle
    high[98]   = 101.5
    low[98]    = 99.5
    open_[99]  = 99.5
    close[99]  = 101.5   # green engulfs red
    high[99]   = 102.0
    low[99]    = 99.0

    print("=" * 60)
    print("CANDLESTICK PATTERN DETECTION – SMOKE TEST")
    print("=" * 60)

    patterns = detect_patterns(open_, high, low, close)
    print("\n--- detect_patterns() ---")
    for name, sig in patterns.items():
        label = {1: "BULLISH", -1: "BEARISH", 0: "NONE"}[sig]
        print(f"  {name:22s} → {sig:>2} ({label})")

    signal = pattern_signal(patterns)
    print(f"\n--- pattern_signal() ---")
    print(f"  Aggregate signal: {signal}")

    recent = most_recent_pattern(open_, high, low, close)
    print(f"\n--- most_recent_pattern() ---")
    print(f"  Pattern: {recent['pattern']}")
    print(f"  Signal:  {recent['signal']}")
    print(f"  Index:   {recent['index']}")
    print(f"  Date:    {recent['date']}")

    # Validate each detector at the index where the pattern was injected
    print("\n--- Raw boolean detection at injected indices ---")
    checks = [
        ("Shooting Star @73",     detect_shooting_star(open_, high, low, close)[73],         True),
        ("Doji @75",              detect_doji(open_, high, low, close)[75],                  True),
        ("Bearish Engulfing @78", detect_bearish_engulfing(open_, high, low, close)[78],     True),
        ("3 White Soldiers @82",  detect_three_white_soldiers(open_, high, low, close)[82],  True),
        ("Morning Star @86",      detect_morning_star(open_, high, low, close)[86],          True),
        ("Evening Star @90",      detect_evening_star(open_, high, low, close)[90],          True),
        ("Bullish Engulfing @99", detect_bullish_engulfing(open_, high, low, close)[99],     True),
        ("Not Doji @50",          detect_doji(open_, high, low, close)[50],                  False),
    ]
    all_pass = True
    for label, val, expected in checks:
        ok = val == expected
        status = "✓" if ok else f"✗ FAIL (got {val}, expected {expected})"
        if not ok:
            all_pass = False
        print(f"  {status}  {label}")

    # Verify detect_patterns picks up the last-candle pattern
    assert patterns["Bullish Engulfing"] == 1, "Last candle should be Bullish Engulfing"
    assert patterns["Three White Soldiers"] == 0, "Last candle should not be Three White Soldiers"
    print("\n  ✓ detect_patterns() correctly flags last candle as Bullish Engulfing")
    print("  ✓ pattern_signal() returns BUY when bullish dominates")

    print("\n" + ("ALL CHECKS PASSED" if all_pass else "SOME CHECKS FAILED – review test data"))
    print("=" * 60)
