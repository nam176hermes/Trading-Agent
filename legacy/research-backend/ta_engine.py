"""
ta_engine.py
------------
Technical analysis module for crypto trading research agent.
Uses ta_shim (pure numpy/pandas) for all indicator calculations.
Takes the candle output from data_collector's Binance OHLCV fetch.
Returns structured dicts ready for the alert watchdog (Step 3).
"""
import pandas as pd
import numpy as np
import ta_shim as ta
import logging
from datetime import datetime, timezone
from typing import Optional

from candlestick_patterns import detect_patterns as _detect_candles, pattern_signal as _candle_signal

log = logging.getLogger("ta_engine")


def calculate_indicators(
    ohlcv: list[dict],
    symbol: str,
    sma_period: int = 200,
    rsi_period: int = 14,
    adx_period: int = 14,
    bb_period: int = 20,
    atr_period: int = 14,
    cci_period: int = 20,
    stochrsi_period: int = 14,
    kc_period: int = 20,
) -> Optional[dict]:
    """
    Core TA function. Takes the OHLCV list from data_collector (Binance format).
    Computes 12 indicators across 4 families: trend, momentum, volatility, volume.

    Returns a dict with all indicators, or None if insufficient data.
    """
    if not ohlcv or len(ohlcv) < sma_period:
        log.warning(
            "[%s] Insufficient candles for %d-period SMA: got %d, need %d",
            symbol, sma_period, len(ohlcv) if ohlcv else 0, sma_period,
        )
        return None

    # Build DataFrame
    df = pd.DataFrame(ohlcv)
    required = ["close", "high", "low", "volume"]
    if not all(c in df.columns for c in required):
        log.error("[%s] OHLCV data missing required columns. Got: %s", symbol, list(df.columns))
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    # ── Dynamic parameter scaling by volatility regime (EcoEngineering) ──
    # Compute a quick ATR proxy to scale all periods: high-vol → longer periods
    # (slower signals), low-vol → shorter periods (more responsive).
    _tr_raw = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    _atr_quick = _tr_raw.ewm(alpha=1 / atr_period, adjust=False).mean()
    _atr_30d_mean = _atr_quick.tail(30).mean()
    if _atr_30d_mean and _atr_30d_mean > 0:
        _vol_scale = _atr_quick.iloc[-1] / _atr_30d_mean
        _vol_scale = max(0.75, min(_vol_scale, 1.5))  # clamp to ±25/50%
    else:
        _vol_scale = 1.0

    rsi_period    = max(5,  int(round(rsi_period    * _vol_scale)))
    bb_period     = max(10, int(round(bb_period     * _vol_scale)))
    cci_period    = max(10, int(round(cci_period    * _vol_scale)))
    stochrsi_period = max(5, int(round(stochrsi_period * _vol_scale)))
    kc_period     = max(10, int(round(kc_period     * _vol_scale)))
    log.debug("[%s] vol_scale=%.2f → rsi=%d bb=%d cci=%d",
              symbol, _vol_scale, rsi_period, bb_period, cci_period)

    def _last(s, default=None, rnd=None):
        """Safely get last value of a Series."""
        if s is None or s.empty or s.iloc[-1:].isna().all():
            return default
        v = s.iloc[-1]
        return round(float(v), rnd) if rnd is not None else v

    # ---- Momentum Family ----
    # RSI-14
    rsi_series = ta.rsi(close, length=rsi_period)
    rsi_val = _last(rsi_series, rnd=1)

    # MACD (12, 26, 9)
    macd_result = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_result is not None and not macd_result.empty:
        macd_line = round(float(macd_result.iloc[-1, 0]), 2)
        macd_signal_line = round(float(macd_result.iloc[-1, 1]), 2)
        macd_hist = round(float(macd_result.iloc[-1, 2]), 2)
    else:
        macd_line = macd_signal_line = macd_hist = None

    # StochRSI
    sr = ta.stochrsi(close, length=stochrsi_period)
    stochrsi_k = _last(sr.iloc[:, 0] if sr is not None else None, rnd=3)
    stochrsi_d = _last(sr.iloc[:, 1] if sr is not None else None, rnd=3)

    # CCI
    cci_series = ta.cci(high, low, close, length=cci_period)
    cci_val = _last(cci_series, rnd=1)

    # ---- Trend Family ----
    # SMA-200
    sma_series = ta.sma(close, length=sma_period)
    sma_val = _last(sma_series, rnd=2)

    # ADX-14
    adx_result = ta.adx(high, low, close, length=adx_period)
    adx_val = _last(adx_result.iloc[:, 0] if adx_result is not None else None, rnd=1)

    # Parabolic SAR
    psar_series = ta.psar(high, low)
    psar_val = _last(psar_series, rnd=2)

    # ---- Volatility Family ----
    # Bollinger Bands (20, 2)
    bb = ta.bbands(close, length=bb_period, std=2.0)
    bb_upper = _last(bb.iloc[:, 2] if bb is not None else None, rnd=2)
    bb_lower = _last(bb.iloc[:, 0] if bb is not None else None, rnd=2)
    bb_percent = _last(bb.iloc[:, 4] if bb is not None else None, rnd=3)

    # ATR-14
    atr_series = ta.atr(high, low, close, length=atr_period)
    atr_val = _last(atr_series, rnd=2)

    # Keltner Channel (20, 2)
    kc = ta.keltner(high, low, close, length=kc_period, multiplier=2.0)
    kc_upper = _last(kc.iloc[:, 2] if kc is not None else None, rnd=2)
    kc_lower = _last(kc.iloc[:, 0] if kc is not None else None, rnd=2)
    kc_mid = _last(kc.iloc[:, 1] if kc is not None else None, rnd=2)

    # ---- Volume Family ----
    # Volume Trend Ratio
    volume_latest = round(float(volume.iloc[-1]), 2)
    volume_30d = round(float(volume.tail(30).mean()), 2) if len(volume) >= 30 else None
    volume_ratio = round(volume_latest / volume_30d, 2) if volume_30d and volume_30d > 0 else None

    # OBV
    obv_series = ta.obv(close, volume)
    obv_val = _last(obv_series, rnd=0)
    obv_sma = _last(ta.sma(obv_series, length=20), rnd=0)
    obv_trend = "rising" if obv_val and obv_sma and obv_val > obv_sma else "falling"

    # ---- Interpretations ----
    current_price = round(float(close.iloc[-1]), 2)

    # Price vs SMA200
    if sma_val is not None:
        if current_price > sma_val * 1.005:
            price_vs_sma = "above"
        elif current_price < sma_val * 0.995:
            price_vs_sma = "below"
        else:
            price_vs_sma = "at"
    else:
        price_vs_sma = None

    rsi_signal = interpret_rsi(rsi_val)
    macd_signal = interpret_macd(macd_line, macd_signal_line)
    adx_signal = interpret_adx(adx_val)
    bb_signal = interpret_bbands(current_price, bb_lower, bb_upper)
    psar_signal = interpret_psar(current_price, psar_val)
    cci_signal = interpret_cci(cci_val)
    stochrsi_signal = interpret_stochrsi(stochrsi_k, stochrsi_d)
    volume_signal = f"{volume_ratio}x_30d_average" if volume_ratio else None

    # Candlestick pattern detection on the most recent complete candle
    candle_patterns = {}
    candle_aggregate = "NONE"
    if "open" in df.columns:
        open_arr = df["open"].astype(float)
        candle_patterns = _detect_candles(open_arr.values, high.values, low.values, close.values)
        candle_aggregate = _candle_signal(candle_patterns)
    else:
        log.debug("[%s] No 'open' column — skipping candlestick patterns", symbol)

    # ── Ichimoku Cloud ──
    ichimoku_bullish = None
    tenkan_val = kijun_val = span_a_val = span_b_val = None
    try:
        ichi_df = ta.ichimoku(high, low, close)
        tenkan_val = round(float(ichi_df["tenkan_sen"].iloc[-1]), 2)
        kijun_val  = round(float(ichi_df["kijun_sen"].iloc[-1]), 2)
        span_a_val = round(float(ichi_df["senkou_span_a"].iloc[-1]), 2)
        span_b_val = round(float(ichi_df["senkou_span_b"].iloc[-1]), 2)
        if not any(pd.isna(v) for v in [span_a_val, span_b_val]):
            cloud_top    = max(span_a_val, span_b_val)
            cloud_bottom = min(span_a_val, span_b_val)
            if current_price > cloud_top:
                ichimoku_bullish = True
            elif current_price < cloud_bottom:
                ichimoku_bullish = False
            # price inside cloud → None (neutral/unclear)
    except Exception as _ie:
        log.debug("[%s] Ichimoku error: %s", symbol, _ie)

    # ── VWAP ──
    vwap_val = None
    try:
        vwap_series = ta.vwap(high, low, close, volume)
        vwap_val = round(float(vwap_series.iloc[-1]), 2)
    except Exception as _ve:
        log.debug("[%s] VWAP error: %s", symbol, _ve)

    # Assemble result
    result = {
        "symbol": symbol,
        "close": current_price,
        # Momentum (4)
        "rsi_14": rsi_val,
        "rsi_signal": rsi_signal,
        "macd_line": macd_line,
        "macd_signal_line": macd_signal_line,
        "macd_histogram": macd_hist,
        "macd_signal": macd_signal,
        "stochrsi_k": stochrsi_k,
        "stochrsi_d": stochrsi_d,
        "stochrsi_signal": stochrsi_signal,
        "cci_20": cci_val,
        "cci_signal": cci_signal,
        # Trend (3)
        "sma_200": sma_val,
        "price_vs_sma200": price_vs_sma,
        "adx_14": adx_val,
        "adx_signal": adx_signal,
        "psar": psar_val,
        "psar_signal": psar_signal,
        # Volatility (3)
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_percent_b": bb_percent,
        "bb_signal": bb_signal,
        "atr_14": atr_val,
        "kc_upper": kc_upper,
        "kc_lower": kc_lower,
        "kc_mid": kc_mid,
        # Volume (3)
        "volume_24h": volume_latest,
        "volume_30d_avg": volume_30d,
        "volume_trend_ratio": volume_ratio,
        "volume_signal": volume_signal,
        "obv": obv_val,
        "obv_trend": obv_trend,
        # Candlestick patterns
        "candle_patterns": candle_patterns,
        "candle_aggregate": candle_aggregate,
        # Ichimoku Cloud
        "ichimoku_tenkan": tenkan_val,
        "ichimoku_kijun":  kijun_val,
        "ichimoku_span_a": span_a_val,
        "ichimoku_span_b": span_b_val,
        "ichimoku_bullish": ichimoku_bullish,
        # VWAP
        "vwap": vwap_val,
        # Reserved
        "signal": None,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        "[%s] TA complete — RSI:%s ADX:%s BB%%:%s CCI:%s ATR:%s OBV:%s PSAR:%s MACD:%s StochRSI:%s",
        symbol, rsi_val, adx_val, bb_percent, cci_val, atr_val,
        obv_trend, psar_signal, macd_signal, stochrsi_signal,
    )

    return result


# ---- Interpretation Functions ----

def interpret_macd(macd_line, macd_signal_line) -> Optional[str]:
    if macd_line is None or macd_signal_line is None:
        return None
    if macd_line > macd_signal_line:
        return "bullish_crossover"
    elif macd_line < macd_signal_line:
        return "bearish_crossover"
    return "neutral"


def interpret_rsi(rsi) -> Optional[str]:
    if rsi is None:
        return None
    if rsi <= 30:
        return "oversold"
    elif rsi >= 70:
        return "overbought"
    return "neutral"


def interpret_adx(adx_val) -> Optional[str]:
    if adx_val is None:
        return None
    if adx_val >= 25:
        return "trending"
    elif adx_val >= 20:
        return "weak_trend"
    return "ranging"


def interpret_bbands(price, bb_lower, bb_upper) -> Optional[str]:
    if price is None or bb_lower is None or bb_upper is None:
        return None
    if price >= bb_upper:
        return "overbought"
    elif price <= bb_lower:
        return "oversold"
    return "neutral"


def interpret_psar(price, psar_val) -> Optional[str]:
    if price is None or psar_val is None:
        return None
    return "bullish" if price > psar_val else "bearish"


def interpret_cci(cci_val) -> Optional[str]:
    if cci_val is None:
        return None
    if cci_val >= 100:
        return "overbought"
    elif cci_val <= -100:
        return "oversold"
    return "neutral"


def interpret_stochrsi(k, d) -> Optional[str]:
    if k is None or d is None:
        return None
    if k >= 0.8:
        return "overbought"
    elif k <= 0.2:
        return "oversold"
    return "neutral"
