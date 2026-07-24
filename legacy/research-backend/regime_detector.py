"""
regime_detector.py
------------------
Detects market regime (trending_up / trending_down / choppy / unclear)
using three independent methods: ADX, Bollinger Band position, SMA slope.

Two of three must agree for a confirmed regime.
Regime output is consumed by assembly.py to adjust signal weights and
confidence levels before the final JSON is assembled.

Requires: pandas, pandas-ta
"""

import logging
import pandas as pd
import ta_shim as ta
from typing import Optional
from dataclasses import dataclass, field

from ml_regime import detect_current_regime as ml_detect_regime

log = logging.getLogger("regime_detector")


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class RegimeResult:
    """
    Full regime detection result. Consumed by assembly.py.

    regime:           The confirmed market regime string
    confidence:       How many of 3 methods agreed ("strong" / "moderate" / "weak")
    adx_value:        Raw ADX reading
    adx_signal:       "trending" / "weak_trend" / "choppy"
    bb_signal:        "trending_up" / "trending_down" / "choppy"
    sma_slope_signal: "rising" / "falling" / "flat"
    agreement_count:  How many methods agreed (1, 2, or 3)
    signal_modifier:  Dict of adjustments assembly.py applies to confidence/signals
    note:             Plain-English explanation for reasoning field in JSON
    raw:              Dict of all raw values for audit trail
    """
    regime:           str
    confidence:       str
    adx_value:        Optional[float]
    adx_signal:       Optional[str]
    bb_signal:        Optional[str]
    sma_slope_signal: Optional[str]
    agreement_count:  int
    signal_modifier:  dict
    note:             str
    raw:              dict = field(default_factory=dict)
    vol_regime:       Optional[str] = None


# ── ADX ───────────────────────────────────────────────────────────────────────

def calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[dict]:
    """
    Calculates ADX using pandas-ta.
    Returns dict with adx value and signal string, or None on failure.

    ADX thresholds:
      < 20  → choppy, no trend
      20-25 → weak trend forming
      > 25  → trend confirmed
      > 40  → strong trend
    """
    try:
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
        if adx_df is None or adx_df.empty:
            log.warning("ADX calculation returned empty result.")
            return None

        # pandas-ta returns columns: ADX_14, DMP_14, DMN_14
        adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
        if not adx_col:
            log.warning("ADX column not found in pandas-ta output.")
            return None

        adx_value = float(adx_df[adx_col[0]].iloc[-1])

        if pd.isna(adx_value):
            log.warning("ADX value is NaN — insufficient data.")
            return None

        if adx_value >= 25:
            signal = "trending"
        elif adx_value >= 20:
            signal = "weak_trend"
        else:
            signal = "choppy"

        log.debug("ADX-%d: %.2f → %s", period, adx_value, signal)
        return {"adx_value": round(adx_value, 2), "adx_signal": signal}

    except Exception as e:
        log.error("ADX calculation error: %s", e)
        return None


# ── Bollinger Band Position ───────────────────────────────────────────────────

def calculate_bb_regime(
    df: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
    lookback: int = 5,
) -> Optional[str]:
    """
    Determines regime from Bollinger Band position over the last `lookback` candles.

    Logic:
      - Count how many of the last N closes are in the upper 25% of the band
      - Count how many are in the lower 25% of the band
      - Count how many are in the middle 50%

    If price is consistently hugging upper or lower band → trending
    If price is bouncing in the middle zone → choppy

    Returns: "trending_up" / "trending_down" / "choppy" / None
    """
    try:
        bb = ta.bbands(df["close"], length=period, std=std)
        if bb is None or bb.empty:
            log.warning("Bollinger Bands calculation returned empty result.")
            return None

        # pandas-ta column names: BBL_20_2.0, BBM_20_2.0, BBU_20_2.0
        lower_col = [c for c in bb.columns if c.startswith("BBL_")]
        upper_col = [c for c in bb.columns if c.startswith("BBU_")]
        mid_col   = [c for c in bb.columns if c.startswith("BBM_")]

        if not (lower_col and upper_col and mid_col):
            log.warning("Bollinger Band columns not found. Available: %s", list(bb.columns))
            return None

        recent_close = df["close"].iloc[-lookback:]
        recent_lower = bb[lower_col[0]].iloc[-lookback:]
        recent_upper = bb[upper_col[0]].iloc[-lookback:]
        recent_mid   = bb[mid_col[0]].iloc[-lookback:]

        upper_zone_count = 0
        lower_zone_count = 0
        mid_zone_count   = 0

        for i in range(lookback):
            close = recent_close.iloc[i]
            lower = recent_lower.iloc[i]
            upper = recent_upper.iloc[i]
            mid   = recent_mid.iloc[i]

            if pd.isna(close) or pd.isna(lower) or pd.isna(upper):
                continue

            band_width = upper - lower
            if band_width == 0:
                continue

            upper_threshold = upper - (band_width * 0.25)
            lower_threshold = lower + (band_width * 0.25)

            if close >= upper_threshold:
                upper_zone_count += 1
            elif close <= lower_threshold:
                lower_zone_count += 1
            else:
                mid_zone_count += 1

        # Majority rules
        if upper_zone_count >= lookback * 0.6:
            signal = "trending_up"
        elif lower_zone_count >= lookback * 0.6:
            signal = "trending_down"
        else:
            signal = "choppy"

        log.debug("BB regime: upper=%d lower=%d mid=%d → %s",
                  upper_zone_count, lower_zone_count, mid_zone_count, signal)
        return signal

    except Exception as e:
        log.error("Bollinger Band regime error: %s", e)
        return None


# ── SMA Slope ─────────────────────────────────────────────────────────────────

def calculate_sma_slope(
    df: pd.DataFrame,
    sma_period: int = 50,
    slope_lookback: int = 10,
    slope_threshold_pct: float = 0.3,
) -> Optional[str]:
    """
    Determines trend direction from the slope of the SMA over `slope_lookback` candles.

    slope_threshold_pct: minimum % change in SMA over the lookback period
                         to qualify as trending (not flat).
                         Default 0.3% — calibrated for daily crypto candles.

    Returns: "rising" / "falling" / "flat" / None
    """
    try:
        sma = ta.sma(df["close"], length=sma_period)
        if sma is None or sma.empty:
            log.warning("SMA-%d calculation returned empty result.", sma_period)
            return None

        # Need enough values at the end
        valid = sma.dropna()
        if len(valid) < slope_lookback:
            log.warning("Not enough SMA values for slope calculation.")
            return None

        sma_now  = float(valid.iloc[-1])
        sma_then = float(valid.iloc[-slope_lookback])

        if sma_then == 0:
            return None

        slope_pct = ((sma_now - sma_then) / sma_then) * 100

        if slope_pct > slope_threshold_pct:
            signal = "rising"
        elif slope_pct < -slope_threshold_pct:
            signal = "falling"
        else:
            signal = "flat"

        log.debug("SMA-%d slope over %d candles: %.3f%% → %s",
                  sma_period, slope_lookback, slope_pct, signal)
        return signal

    except Exception as e:
        log.error("SMA slope calculation error: %s", e)
        return None


# ── Regime Voting Logic ───────────────────────────────────────────────────────

def resolve_regime(
    adx_signal:       Optional[str],
    bb_signal:        Optional[str],
    sma_slope_signal: Optional[str],
    adx_value:        Optional[float],
) -> tuple[str, int, str]:
    """
    Votes across three signals to determine final regime.
    Returns (regime_string, agreement_count, confidence_string).

    Voting rules:
      3/3 agree → strong confirmation
      2/3 agree → moderate confirmation
      1/3 or 0/3 → unclear

    ADX acts as a veto for trending claims:
      Even if BB and SMA say trending, if ADX < 20 (choppy),
      the regime is downgraded to "unclear" rather than overriding ADX.
    """
    votes = {"trending_up": 0, "trending_down": 0, "choppy": 0}

    # ADX vote
    if adx_signal == "trending":
        # ADX says trending but doesn't know direction — defer to BB/SMA for direction
        pass
    elif adx_signal == "choppy":
        votes["choppy"] += 1
    elif adx_signal == "weak_trend":
        pass  # abstain — weak trend could go either way

    # BB vote
    if bb_signal == "trending_up":
        votes["trending_up"] += 1
    elif bb_signal == "trending_down":
        votes["trending_down"] += 1
    elif bb_signal == "choppy":
        votes["choppy"] += 1

    # SMA slope vote
    if sma_slope_signal == "rising":
        votes["trending_up"] += 1
    elif sma_slope_signal == "falling":
        votes["trending_down"] += 1
    elif sma_slope_signal == "flat":
        votes["choppy"] += 1

    top_regime = max(votes, key=votes.get)
    top_votes  = votes[top_regime]

    # ADX veto: if top regime is trending but ADX says choppy → unclear
    if top_regime in ("trending_up", "trending_down") and adx_signal == "choppy":
        return "unclear", top_votes, "weak"

    # ADX boost: if ADX is strongly trending, boost confidence
    if top_regime in ("trending_up", "trending_down") and adx_value and adx_value >= 40:
        return top_regime, top_votes + 1, "strong"

    if top_votes >= 2:
        confidence = "strong" if top_votes == 3 else "moderate"
        return top_regime, top_votes, confidence
    else:
        return "unclear", top_votes, "weak"


# ── Signal Modifier ───────────────────────────────────────────────────────────

def build_signal_modifier(regime: str, confidence: str) -> dict:
    """
    Returns a dict of adjustments that assembly.py applies to signals.

    Keys:
      suppress_rsi_oversold:  bool — ignore RSI <30 buy signals
      suppress_rsi_overbought: bool — ignore RSI >70 sell signals  
      suppress_macd:          bool — ignore MACD crossover signals
      cap_confidence:         str or None — maximum allowed confidence level
      trade_bias:             "long" / "short" / "none" / "both"
      regime_warning:         str or None — warning text added to reasoning
    """
    modifiers = {
        "suppress_rsi_oversold":   False,
        "suppress_rsi_overbought": False,
        "suppress_macd":           False,
        "cap_confidence":          None,
        "trade_bias":              "both",
        "regime_warning":          None,
    }

    if regime == "choppy":
        modifiers.update({
            "suppress_rsi_oversold":   True,
            "suppress_rsi_overbought": True,
            "suppress_macd":           True,
            "cap_confidence":          "low",
            "trade_bias":              "none",
            "regime_warning": (
                "⚠️ CHOPPY MARKET — RSI and MACD signals suppressed. "
                "No directional trades recommended until trend is confirmed."
            ),
        })

    elif regime == "trending_down":
        modifiers.update({
            "suppress_rsi_oversold": True,   # don't catch falling knives
            "trade_bias":            "short",
            "regime_warning": (
                "Downtrend confirmed. RSI oversold signals suppressed — "
                "price can stay oversold in a trend. Short bias only."
            ),
        })

    elif regime == "trending_up":
        modifiers.update({
            "suppress_rsi_overbought": True,  # don't short a strong uptrend
            "trade_bias":              "long",
        })

    elif regime == "unclear":
        cap = "medium" if confidence == "moderate" else "low"
        modifiers.update({
            "cap_confidence": cap,
            "regime_warning": (
                "Regime unclear — indicator methods disagree on market structure. "
                f"Confidence capped at '{cap}'."
            ),
        })

    return modifiers


# ── GARCH Vol Regime Classification ────────────────────────────────────────────

def _classify_vol_regime(ohlcv: list[dict], window: int = 20) -> Optional[str]:
    """
    Classify volatility regime as LOW_VOL, NORMAL_VOL, or HIGH_VOL
    based on current annualized vol vs trailing percentile.

    Uses log returns from close prices. Falls back to price-range proxy
    if returns computation fails.

    Args:
        ohlcv: List of candle dicts with 'close' key.
        window: Lookback window for percentile ranking (default 20).

    Returns:
        'LOW_VOL', 'NORMAL_VOL', 'HIGH_VOL', or None if insufficient data.
    """
    if not ohlcv or len(ohlcv) < window + 1:
        return None

    try:
        import numpy as np

        closes = np.array([c.get("close", c.get("Close", 0)) for c in ohlcv], dtype=float)
        closes = closes[closes > 0]

        if len(closes) < window + 1:
            log.warning("Vol regime: insufficient valid closes (%d)", len(closes))
            return None

        # Compute rolling annualized volatility from log returns
        log_returns = np.diff(np.log(closes))
        if len(log_returns) < window:
            return None

        # Rolling annualized vol
        rolling_vol = np.array([
            np.std(log_returns[i:i+window]) * np.sqrt(252)
            for i in range(len(log_returns) - window + 1)
        ])

        current_vol = rolling_vol[-1]
        low_pctile = np.percentile(rolling_vol, 20)
        high_pctile = np.percentile(rolling_vol, 80)

        if current_vol < low_pctile:
            regime = "LOW_VOL"
        elif current_vol > high_pctile:
            regime = "HIGH_VOL"
        else:
            regime = "NORMAL_VOL"

        log.debug(
            "Vol regime: %.1f%% annualized — %s "
            "(20th=%.1f%%, 80th=%.1f%%)",
            current_vol * 100, regime,
            low_pctile * 100, high_pctile * 100,
        )
        return regime

    except Exception as e:
        log.warning("Vol regime classification failed: %s", e)
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def detect_regime(ohlcv: list[dict], symbol: str = "") -> Optional[RegimeResult]:
    """
    Top-level function. Called from main.py or assembly.py.

    Detection order:
      1. ML regime (PCA + KMeans on 19-asset returns) — primary
      2. ADX + Bollinger Band + SMA slope heuristics — fallback

    Args:
        ohlcv:  Raw candle list from data_collector.py (need ≥60 candles minimum,
                ≥200 recommended for SMA-50 slope to be meaningful)
        symbol: Symbol string for logging only

    Returns:
        RegimeResult dataclass, or None if insufficient data.
    """
    # ── Try ML regime detection first ──
    try:
        ml_regime = ml_detect_regime()
        if ml_regime and ml_regime.get("confidence", 0) > 0.5:
            regime_label = ml_regime["regime_label"]
            inherited = ml_regime.get("inherited_regime", "unclear")
            confidence_num = float(ml_regime["confidence"])
            if confidence_num >= 0.8:
                confidence_str = "strong"
            elif confidence_num >= 0.5:
                confidence_str = "moderate"
            else:
                confidence_str = "weak"

            log.info("[%s] ML regime → %s (id=%d conf=%.2f → %s)",
                     symbol, regime_label,
                     ml_regime.get("regime_id", -1),
                     confidence_num, inherited)

            chars = ml_regime.get("characteristics", {})
            return RegimeResult(
                regime=regime_label,
                confidence=confidence_str,
                adx_value=None,
                adx_signal=None,
                bb_signal=None,
                sma_slope_signal=None,
                vol_regime=_classify_vol_regime(ohlcv),
                agreement_count=3 if confidence_num >= 0.8 else 2,
                signal_modifier=build_signal_modifier(regime_label, confidence_str),
                note=(
                    f"ML Regime: {regime_label} (confidence={confidence_num:.2f}). "
                    f"Heuristic would say: {inherited}. "
                    f"Avg ret={chars.get('avg_daily_return', 0):.4%} "
                    f"vol={chars.get('avg_volatility', 0):.4f} "
                    f"corr={chars.get('avg_correlation', 0):.3f}"
                ),
                raw={
                    "method": "ml_pca_kmeans",
                    "ml_regime": ml_regime,
                },
            )
    except Exception as e:
        log.debug("[%s] ML regime detection failed, falling back to heuristics: %s", symbol, e)

    # ── Fallback: existing heuristic detectors ──
    if not ohlcv or len(ohlcv) < 60:
        log.warning("[%s] Insufficient candles for regime detection (%d). Need ≥60.",
                    symbol, len(ohlcv) if ohlcv else 0)
        return None

    # Build DataFrame
    df = pd.DataFrame(ohlcv)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    # ── Run all three detectors ──
    adx_result       = calculate_adx(df)
    bb_signal        = calculate_bb_regime(df)
    sma_slope_signal = calculate_sma_slope(df)

    adx_value  = adx_result["adx_value"]  if adx_result else None
    adx_signal = adx_result["adx_signal"] if adx_result else None

    # ── Vote ──
    regime, agreement_count, confidence = resolve_regime(
        adx_signal, bb_signal, sma_slope_signal, adx_value
    )

    # ── Build modifier dict for assembly.py ──
    signal_modifier = build_signal_modifier(regime, confidence)

    # ── Build human-readable note ──
    note_parts = [f"Regime: {regime.upper()} ({confidence} confirmation, {agreement_count}/3 methods agree)."]

    if adx_value:
        note_parts.append(f"ADX-14: {adx_value} ({adx_signal}).")
    if bb_signal:
        note_parts.append(f"BB position: {bb_signal}.")
    if sma_slope_signal:
        note_parts.append(f"SMA-50 slope: {sma_slope_signal}.")
    if signal_modifier.get("regime_warning"):
        note_parts.append(signal_modifier["regime_warning"])

    note = " ".join(note_parts)
    log.info("[%s] %s", symbol, note)

    return RegimeResult(
        regime           = regime,
        confidence       = confidence,
        adx_value        = adx_value,
        adx_signal       = adx_signal,
        bb_signal        = bb_signal,
        sma_slope_signal = sma_slope_signal,
        vol_regime       = _classify_vol_regime(ohlcv),
        agreement_count  = agreement_count,
        signal_modifier  = signal_modifier,
        note             = note,
        raw = {
            "adx_value":        adx_value,
            "adx_signal":       adx_signal,
            "bb_signal":        bb_signal,
            "sma_slope_signal": sma_slope_signal,
            "candle_count":     len(ohlcv),
        },
    )


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import random
    random.seed(99)

    def make_candles(n, start, trend="up", noise=800):
        candles = []
        price = start
        for _ in range(n):
            if trend == "up":
                drift = random.uniform(50, 300)
            elif trend == "down":
                drift = random.uniform(-300, -50)
            else:
                drift = random.uniform(-400, 400)

            o = price + random.uniform(-noise/2, noise/2)
            h = o + random.uniform(100, noise)
            l = o - random.uniform(100, noise)
            c = o + drift
            v = random.uniform(10000, 50000)
            candles.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
            price = c
        return candles

    scenarios = [
        ("BTC_BULL",  make_candles(210, 60000, "up"),   "Expected: trending_up"),
        ("ETH_BEAR",  make_candles(210, 3000,  "down"), "Expected: trending_down"),
        ("DOGE_CHOP", make_candles(210, 0.15,  "flat"), "Expected: choppy"),
    ]

    for label, candles, expected in scenarios:
        print(f"\n── {label} — {expected} ──")
        result = detect_regime(candles, symbol=label)
        if result:
            print(f"  Regime:     {result.regime}")
            print(f"  Confidence: {result.confidence} ({result.agreement_count}/3)")
            print(f"  ADX:        {result.adx_value} → {result.adx_signal}")
            print(f"  BB:         {result.bb_signal}")
            print(f"  SMA slope:  {result.sma_slope_signal}")
            print(f"  Cap conf:   {result.signal_modifier.get('cap_confidence')}")
            print(f"  Suppress RSI buy: {result.signal_modifier.get('suppress_rsi_oversold')}")
            print(f"  Note: {result.note[:100]}...")
