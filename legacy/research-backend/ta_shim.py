"""
ta_shim.py - Drop-in replacement for pandas_ta.
Implements indicators using pure numpy/pandas. No external TA library needed.
Indicators: RSI, MACD, SMA, ADX, BBANDS, ATR, CCI, OBV, StochRSI, KeltnerChannel, PSAR,
            Ichimoku Cloud, VWAP
"""
import pandas as pd
import numpy as np


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD - returns DataFrame with MACD, Signal, Histogram columns."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"MACD_12_26_9": macd_line, "MACDs_12_26_9": signal_line, "MACDh_12_26_9": histogram}
    )


def sma(close: pd.Series, length: int = 200) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(window=length).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.DataFrame:
    """Average Directional Index. Returns DataFrame with ADX, DMP, DMN columns."""
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / length, adjust=False).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=close.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=close.index)

    plus_di = 100 * (plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr)

    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx_val = dx.ewm(alpha=1 / length, adjust=False).mean()

    return pd.DataFrame(
        {f"ADX_{length}": adx_val, f"DMP_{length}": plus_di, f"DMN_{length}": minus_di}
    )


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands. Returns DataFrame with BBL, BBM, BBU, BBB, BBP columns."""
    mid = close.rolling(window=length).mean()
    stdev = close.rolling(window=length).std()
    upper = mid + std * stdev
    lower = mid - std * stdev
    bandwidth = (upper - lower) / mid
    percent_b = (close - lower) / (upper - lower)
    return pd.DataFrame({
        f"BBL_{length}_{std}": lower,
        f"BBM_{length}_{std}": mid,
        f"BBU_{length}_{std}": upper,
        f"BBB_{length}_{std}": bandwidth,
        f"BBP_{length}_{std}": percent_b,
    })


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range."""
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20) -> pd.Series:
    """Commodity Channel Index."""
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(window=length).mean()
    mad = tp.rolling(window=length).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def stochrsi(close: pd.Series, length: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stochastic RSI. Returns DataFrame with STOCHRSIk, STOCHRSId columns."""
    rsi_vals = rsi(close, length=length)
    rsi_min = rsi_vals.rolling(window=length).min()
    rsi_max = rsi_vals.rolling(window=length).max()
    stoch = (rsi_vals - rsi_min) / (rsi_max - rsi_min)
    k_line = stoch.rolling(window=k).mean()
    d_line = k_line.rolling(window=d).mean()
    return pd.DataFrame({f"STOCHRSIk_{length}_{k}_{d}": k_line, f"STOCHRSId_{length}_{k}_{d}": d_line})


def keltner(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20, multiplier: float = 2.0) -> pd.DataFrame:
    """Keltner Channel. Returns DataFrame with KCLe, KCMi, KCUe columns."""
    typical = (high + low + close) / 3
    ema_mid = typical.ewm(span=length, adjust=False).mean()
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr_val = tr.ewm(span=length, adjust=False).mean()
    upper = ema_mid + multiplier * atr_val
    lower = ema_mid - multiplier * atr_val
    return pd.DataFrame({
        f"KCL_{length}_{multiplier}": lower,
        f"KCM_{length}_{multiplier}": ema_mid,
        f"KCU_{length}_{multiplier}": upper,
    })


def psar(high: pd.Series, low: pd.Series, acceleration: float = 0.02, maximum: float = 0.2) -> pd.Series:
    """Parabolic SAR."""
    n = len(high)
    sar = np.full(n, np.nan)
    af = acceleration
    ep = low.iloc[0]
    trend = 1  # 1=uptrend, -1=downtrend

    sar[0] = low.iloc[0]  # Initialize

    for i in range(1, n):
        prev_sar = sar[i - 1]
        prev_high = high.iloc[i - 1]
        prev_low = low.iloc[i - 1]

        if trend == 1:
            sar[i] = prev_sar + af * (ep - prev_sar)
            sar[i] = min(sar[i], prev_low, low.iloc[i - 1] if i >= 2 else prev_low)
            if high.iloc[i] > ep:
                ep = high.iloc[i]
                af = min(af + acceleration, maximum)
            if low.iloc[i] < sar[i]:
                trend = -1
                sar[i] = ep
                ep = low.iloc[i]
                af = acceleration
        else:
            sar[i] = prev_sar + af * (ep - prev_sar)
            sar[i] = max(sar[i], prev_high, high.iloc[i - 1] if i >= 2 else prev_high)
            if low.iloc[i] < ep:
                ep = low.iloc[i]
                af = min(af + acceleration, maximum)
            if high.iloc[i] > sar[i]:
                trend = 1
                sar[i] = ep
                ep = high.iloc[i]
                af = acceleration

    return pd.Series(sar, index=high.index)


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series,
             tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> pd.DataFrame:
    """
    Ichimoku Kinko Hyo Cloud.
    Returns DataFrame with: tenkan_sen, kijun_sen, senkou_span_a, senkou_span_b, chikou_span.

    Cloud signal: price above max(span_a, span_b) = bullish; below min = bearish.
    """
    def _midpoint(h: pd.Series, l: pd.Series, period: int) -> pd.Series:
        return (h.rolling(period).max() + l.rolling(period).min()) / 2

    tenkan_sen  = _midpoint(high, low, tenkan)
    kijun_sen   = _midpoint(high, low, kijun)
    senkou_a    = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
    senkou_b    = _midpoint(high, low, senkou_b).shift(kijun)
    chikou_span = close.shift(-kijun)

    return pd.DataFrame({
        "tenkan_sen":    tenkan_sen,
        "kijun_sen":     kijun_sen,
        "senkou_span_a": senkou_a,
        "senkou_span_b": senkou_b,
        "chikou_span":   chikou_span,
    })


def special_k(close: pd.Series) -> pd.Series:
    """
    Pring Special K — multi-cycle momentum oscillator.
    Weighted sum of smoothed ROC at 10, 13, 14, 15, 17, 18, 19, 20, 26, 29, 30, 37, 39, 45, 49 periods,
    then smoothed with a 10-period SMA. Captures short, intermediate, and long cycles simultaneously.
    """
    def _roc(s: pd.Series, n: int) -> pd.Series:
        return (s - s.shift(n)) / s.shift(n) * 100

    # Short cycle (10-period ROC smoothed ×1)
    short = (
        _roc(close, 10).rolling(10).mean() * 1
        + _roc(close, 13).rolling(13).mean() * 2
        + _roc(close, 14).rolling(14).mean() * 3
        + _roc(close, 15).rolling(15).mean() * 4
    )
    # Intermediate cycle (smoothed ×1)
    mid = (
        _roc(close, 17).rolling(4).mean() * 1
        + _roc(close, 18).rolling(2).mean() * 2
        + _roc(close, 19).rolling(3).mean() * 3
        + _roc(close, 20).rolling(4).mean() * 4
    )
    # Long cycle (smoothed ×1)
    lng = (
        _roc(close, 26).rolling(4).mean() * 1
        + _roc(close, 29).rolling(2).mean() * 2
        + _roc(close, 30).rolling(3).mean() * 3
        + _roc(close, 37).rolling(4).mean() * 4
        + _roc(close, 39).rolling(5).mean() * 1
        + _roc(close, 45).rolling(5).mean() * 2
        + _roc(close, 49).rolling(5).mean() * 3
    )
    raw = short + mid + lng
    return raw.rolling(10).mean()


def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series) -> pd.Series:
    """
    Volume Weighted Average Price (session VWAP using all available bars).
    VWAP = cumulative(typical_price × volume) / cumulative(volume)
    """
    typical = (high + low + close) / 3
    return (typical * volume).cumsum() / volume.cumsum()
