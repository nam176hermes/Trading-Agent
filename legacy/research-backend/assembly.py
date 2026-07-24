"""
assembly.py
------------
Step 3 + Step 4: Alert watchdog + JSON assembly layer.

Phase 1: Adds Pydantic-based structured signal generation.

Interface matches what main.py expects:
  assemble_asset_json(raw_data, ta_data, sentiment_data, onchain_data) -> dict
  assemble_full_report(assembled_assets) -> dict
  generate_initial_signal(llm, asset, market_data) -> TradingSignal (NEW)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from ta_engine import calculate_indicators, interpret_macd, interpret_rsi
from atr_stops import calculate_stop_target as atr_stop_target
from regime_detector import RegimeResult
from runtime_paths import data_root, reports_dir as runtime_reports_dir

log = logging.getLogger("assembly")

# ── Signal filter gating ──
try:
    from signal_filters import apply_signal_filters as _apply_filters
    _HAS_SIGNAL_FILTERS = True
except ImportError:
    _HAS_SIGNAL_FILTERS = False
    _apply_filters = None

# ── Alpha signal caching ────────────────────────────────────────────────
_alpha_cache: dict[str, list[dict]] | None = None


def _get_alpha_signals() -> dict[str, list[dict]]:
    """Load predscope + adanos signals once per process, keyed by symbol."""
    global _alpha_cache
    if _alpha_cache is not None:
        return _alpha_cache
    _alpha_cache = {"predscope": {}, "adanos": {}}
    try:
        from predscope_signals import get_predscope_signals
        for s in get_predscope_signals():
            _alpha_cache["predscope"][s["symbol"]] = s
    except Exception:
        pass
    try:
        from adanos_signals import get_adanos_signals
        for s in get_adanos_signals():
            _alpha_cache["adanos"][s["symbol"]] = s
    except Exception:
        pass
    return _alpha_cache


def _load_macro_regime() -> tuple[str, float]:
    """Read the latest macro_report_*.json and return (regime, confidence).
    Falls back to ('neutral', 0.5) if no report exists."""
    import os as _os
    reports_dir = runtime_reports_dir()
    try:
        files = sorted(
            [f for f in _os.listdir(reports_dir) if f.startswith("macro_report_") and f.endswith(".json")],
            reverse=True,
        )
        if not files:
            return "neutral", 0.5
        data = json.loads((reports_dir / files[0]).read_text())
        return data.get("regime", "neutral"), float(data.get("regime_confidence", 0.5))
    except Exception:
        return "neutral", 0.5


def _load_prediction_data() -> dict:
    """Read the latest prediction_market_*.json and return filtered crypto summary.
    Falls back to empty dict if no report exists."""
    reports_dir = runtime_reports_dir()
    try:
        files = sorted(
            [f for f in reports_dir.iterdir() if f.name.startswith("prediction_market_") and f.suffix == ".json"],
            reverse=True,
        )
        if not files:
            return {}
        raw = json.loads(files[0].read_text())
        # Extract crypto-specific markets and aggregate probabilities
        crypto_markets = [m for m in raw.get("markets", []) if "crypto" in m.get("categories", [])]
        return {
            "collected_at": raw.get("collected_at"),
            "crypto_markets": crypto_markets[:10],
            "crypto_market_count": len(crypto_markets),
            "total_filtered": raw.get("filtered_markets", 0),
        }
    except Exception:
        return {}


def _load_social_sentiment() -> dict:
    """Read the latest social_sentiment_*.json and return sentiment summary."""
    reports_dir = runtime_reports_dir()
    try:
        files = sorted(
            [f for f in reports_dir.iterdir() if f.name.startswith("social_sentiment_") and f.suffix == ".json"],
            reverse=True,
        )
        if not files:
            return {}
        raw = json.loads(files[0].read_text())
        data = raw.get("data", {})
        # Extract buzz and sentiment scores from Reddit/X
        reddit = data.get("reddit_crypto", {})
        twitter = data.get("x_twitter", {})
        poly = data.get("polymarket", {})
        return {
            "collected_at": raw.get("collected_at"),
            "reddit_crypto": reddit,
            "x_twitter": twitter,
            "polymarket_conviction": poly,
        }
    except Exception:
        return {}

# Import new Pydantic schemas
from schemas import TradingSignal as PydanticTradingSignal
from signal_parser import build_schema_prompt, parse_structured

# ── Trigger thresholds ───────────────────────────────────────────────

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
VOLUME_SPIKE_MULTIPLIER = 2.0
SMA_CROSS_DEADBAND = 0.005


# ── Phase 1: Structured Signal Generation ────────────────────────────

async def generate_initial_signal(llm_client, asset: str, market_data: dict) -> PydanticTradingSignal:
    """
    Generate a structured trading signal using LLM with Pydantic validation.

    Args:
        llm_client: LLM client with call() method (async or sync)
        asset: Asset symbol (e.g., "BTC")
        market_data: Dict with current market context (price, indicators, etc.)

    Returns:
        Validated PydanticTradingSignal instance
    """
    from schemas import Action

    # Build the prompt with schema instructions
    schema_prompt = build_schema_prompt(PydanticTradingSignal)

    prompt = f"""Generate a trading signal for {asset} based on the following market data:

Market Data:
{json.dumps(market_data, indent=2, default=str)[:1000]}

Consider:
- Current price action and trend
- Technical indicators (RSI, MACD, moving averages)
- Volume patterns
- Support/resistance levels

Provide a specific entry price, stop loss, and take profit if recommending a trade.
{schema_prompt}

Generate a TradingSignal object for {asset}:"""

    # Call LLM
    import asyncio
    if asyncio.iscoroutinefunction(llm_client):
        raw_output = await llm_client(prompt)
    else:
        raw_output = llm_client(prompt)

    # Parse and validate
    signal = parse_structured(
        llm_client,
        raw_output,
        PydanticTradingSignal,
        context_hint=f"Trading signal generation for {asset}",
    )

    log.info("Generated structured signal for %s: %s @ %.2f confidence",
             asset, signal.action, signal.confidence)

    return signal


# ── Step 3: Alert Watchdog ──────────────────────────────────────────

def evaluate_alerts(ta: dict) -> list[str]:
    """
    Evaluate TA indicators against trigger conditions.
    Returns a list of active alert strings (empty list = no alerts).
    """
    alerts = []

    rsi = ta.get("rsi_14")
    if rsi is not None:
        if rsi <= RSI_OVERSOLD:
            alerts.append(f"RSI_OVERSOLD ({rsi})")
        elif rsi >= RSI_OVERBOUGHT:
            alerts.append(f"RSI_OVERBOUGHT ({rsi})")

    current = ta.get("close")
    sma = ta.get("sma_200")
    pos = ta.get("price_vs_sma200")
    if current is not None and sma is not None and pos is not None:
        if abs(current - sma) / sma <= SMA_CROSS_DEADBAND:
            alerts.append(f"SMA_CROSS (price at {pos} SMA200)")
        elif pos == "above":
            alerts.append("ABOVE_SMA200")
        elif pos == "below":
            alerts.append("BELOW_SMA200")

    vol_ratio = ta.get("volume_trend_ratio")
    if vol_ratio is not None and vol_ratio >= VOLUME_SPIKE_MULTIPLIER:
        alerts.append(f"VOLUME_SPIKE ({vol_ratio}x 30d avg)")

    return alerts


def macd_signal(ta: dict) -> Optional[str]:
    """Classify MACD crossover signal from ta dict."""
    if any(ta.get(k) is None for k in ["macd_histogram", "macd_line", "macd_signal_line"]):
        return None
    return interpret_macd(ta["macd_line"], ta["macd_signal_line"])


def resolve_signal_and_confidence(
    rsi_signal: Optional[str],
    macd_sig: Optional[str],
    price_vs_sma: Optional[str],
    alerts: list[str],
    sentiment: Optional[str],
    onchain_risk: Optional[str],
    derivatives_signal: Optional[str] = None,
    ta: Optional[dict] = None,
) -> tuple[Optional[str], str, Optional[str], bool]:
    """
    Resolve the final trading signal, confidence level, and warnings.
    Returns (suggestion, confidence, warning, signal_conflict).
    
    QuantAgent-inspired: weighted signal synthesis with higher weight
    for strong directional signals, neutral signals ignored.
    """
    signal_conflict = False
    warning = None
    suggestion = None
    current = ta.get("close") if ta else None
    sma = ta.get("sma_200") if ta else None
    rsi = ta.get("rsi_14") if ta else None

    if onchain_risk == "critical":
        warning = "⚠️ HIGH RISK — review on-chain data before entering"
        return "NO SIGNAL", "0.30", warning, False

    # Strategy risk gate — per-strategy drawdown kill switch (Coding Jesus)
    try:
        from strategy_risk_manager import is_strategy_allowed
        _ta_ok, _ta_reason = is_strategy_allowed("ta")
        if not _ta_ok:
            return "NO SIGNAL", "0.20", f"TA strategy suspended: {_ta_reason}", False
    except ImportError:
        pass

    bull_score = 0.0
    bear_score = 0.0
    # ── QuantAgent-inspired WEIGHTED signal synthesis ──
    # "Higher weight to strong directional signals, ignore neutral"
    # Each indicator now carries a weight based on signal strength

    def _score(condition: bool, weight: float) -> float:
        return weight if condition else 0.0

    bull_score = 0.0
    bear_score = 0.0

    # RSI — extreme levels carry 3x weight, neutral = no weight
    if rsi is not None:
        if rsi <= 25:
            bear_score += _score(True, 3.0)   # Extreme oversold → strong bearish
        elif rsi <= RSI_OVERSOLD:
            bear_score += _score(True, 2.0)   # Oversold
        elif rsi >= 85:
            bull_score += _score(True, -3.0)  # Extreme overbought → bearish (inverted bull)
        elif rsi >= RSI_OVERBOUGHT:
            bear_score += _score(True, 2.0)   # Overbought = bearish signal
        # 40-60 = neutral, no weight added (QuantAgent: "ignore neutral")

    # MACD — crossover stronger than histogram
    if macd_sig == "bullish_crossover":
        bull_score += 2.5
    elif macd_sig == "bullish":
        bull_score += 1.5
    elif macd_sig == "bearish_crossover":
        bear_score += 2.5
    elif macd_sig == "bearish":
        bear_score += 1.5
    # "neutral" MACD adds nothing (QuantAgent: ignore neutral)

    # SMA200 — extreme distance = stronger signal
    if price_vs_sma == "above":
        if current and sma:
            pct_above = (current - sma) / sma
            if pct_above > 0.15:
                bull_score += 2.0  # 15%+ above = strong trend
            else:
                bull_score += 1.0
    elif price_vs_sma == "below":
        if current and sma:
            pct_below = (sma - current) / sma
            if pct_below > 0.15:
                bear_score += 2.0  # 15%+ below = strong trend
            else:
                bear_score += 1.0

    # Volume — spike detection with multiplier
    vol_ratio = ta.get('volume_vs_30d', 1.0)
    if vol_ratio >= 2.5:
        # Heavy volume amplifies whichever direction price is moving
        if price_vs_sma == 'above':
            bull_score += 2.0  # Volume confirms uptrend
        elif price_vs_sma == 'below':
            bear_score += 2.0  # Volume confirms downtrend
    elif vol_ratio >= 1.5:
        if price_vs_sma == 'above':
            bull_score += 1.0
        elif price_vs_sma == 'below':
            bear_score += 1.0

    # Sentiment (if available) — weighted by VADER score × 0.15 factor
    # Threshold: >0.3 = bullish bonus, < -0.3 = bearish penalty
    sentiment_score_val = ta.get("sentiment_score") if ta else None
    if sentiment_score_val is not None:
        if sentiment_score_val > 0.3:
            bull_score += abs(sentiment_score_val) * 3.0  # bullish bonus
        elif sentiment_score_val < -0.3:
            bear_score += abs(sentiment_score_val) * 3.0  # bearish penalty
    elif sentiment == 'positive':
        bull_score += 1.5
    elif sentiment == 'negative':
        bear_score += 1.5

    # On-chain risk — structural, carries extra weight
    if onchain_risk == 'high':
        bear_score += 3.0  # Heavy weight for on-chain red flags

    # Derivatives signal — covers all values from derivatives_collector.interpret_derivatives_signal()
    if derivatives_signal:
        ds = derivatives_signal.lower()
        if 'squeeze_risk_caution' in ds:
            bear_score += 3.0   # overleveraged longs, high squeeze risk
        elif 'long_squeeze_risk' in ds:
            bull_score += 3.0   # overleveraged shorts, violent upward squeeze possible
        elif 'strong_long_setup' in ds:
            bull_score += 2.5   # bullish funding + rising OI + price up
        elif 'strong_short_setup' in ds:
            bear_score += 2.5   # bearish funding + rising OI + price down
        elif 'long_capitulation' in ds:
            bull_score += 2.0   # longs giving up = contrarian bottom signal
        elif 'long_setup' in ds:
            bull_score += 1.5   # long_setup (healthy bullish)
        elif 'short_setup' in ds:
            bear_score += 1.5   # short_setup (healthy bearish)
        elif 'short_squeeze_relief' in ds:
            bull_score += 0.5   # shorts covering — modest bullish but momentum may fade

    # ── Phase 6: Wire dormant TA signals into scoring ──────────────────────
    if ta:
        adx_sig   = ta.get("adx_signal")
        cdl       = ta.get("candle_aggregate")      # BUY / SELL / HOLD
        srsi_sig  = ta.get("stochrsi_signal")
        cci_sig   = ta.get("cci_signal")
        psar_sig  = ta.get("psar_signal")
        obv_sig   = ta.get("obv_trend")
        price     = ta.get("close")
        kc_upper  = ta.get("kc_upper")
        kc_lower  = ta.get("kc_lower")
        ichi_bull = ta.get("ichimoku_bullish")      # True/False/None
        vwap_val  = ta.get("vwap")

        # ── AlgoAdvantage: Parallel trend + mean-reversion scoring ──────────
        # Run both systems simultaneously; ADX blends their weights instead
        # of suppressing one side entirely.

        trend_bull = 0.0
        trend_bear = 0.0
        reversion_bull = 0.0
        reversion_bear = 0.0

        # --- Trend signals ---
        # MACD (already in bull_score/bear_score above) — re-weight here
        if psar_sig == "bullish":
            trend_bull += 1.0
        elif psar_sig == "bearish":
            trend_bear += 1.0

        if ichi_bull is True:
            trend_bull += 1.5
        elif ichi_bull is False:
            trend_bear += 1.5

        if cdl == "BUY":
            trend_bull += 1.0
        elif cdl == "SELL":
            trend_bear += 1.0

        if obv_sig == "rising" and price_vs_sma == "above":
            trend_bull += 1.0
        elif obv_sig == "falling" and price_vs_sma == "below":
            trend_bear += 1.0

        if price and kc_upper and price > kc_upper:
            trend_bull += 1.0
        elif price and kc_lower and price < kc_lower:
            trend_bear += 1.0

        # --- Mean-reversion signals ---
        if srsi_sig == "oversold":
            reversion_bull += 1.5
        elif srsi_sig == "overbought":
            reversion_bear += 1.5

        if cci_sig == "oversold":
            reversion_bull += 1.0
        elif cci_sig == "overbought":
            reversion_bear += 1.0

        if vwap_val and price:
            pct_from_vwap = (price - vwap_val) / vwap_val
            if pct_from_vwap < -0.01:
                reversion_bull += 0.8
            elif pct_from_vwap > 0.02:
                reversion_bear += 0.8

        # --- ADX-based regime blending (AlgoAdvantage parallel strategy) ---
        # trending → weight toward trend signals
        # ranging  → weight toward mean-reversion signals
        # neutral  → 50/50 blend
        adx_val_raw = ta.get("adx_14", 22)  # fallback to neutral
        if adx_val_raw is None:
            adx_val_raw = 22
        if adx_val_raw >= 25:       # trending market
            trend_w, rev_w = 0.70, 0.30
        elif adx_val_raw <= 20:     # ranging market
            trend_w, rev_w = 0.30, 0.70
        else:                       # transitional
            trend_w, rev_w = 0.50, 0.50

        bull_score += trend_bull * trend_w + reversion_bull * rev_w
        bear_score += trend_bear * trend_w + reversion_bear * rev_w

    # ── Decision logic with weighted scores ──
    # Fix: Lower thresholds so trades actually execute.
    # Confidence thresholds (per spec):
    #   high:   bullish >= 3 AND bearish == 0
    #   medium: bullish >= 2 AND bearish <= 1
    #   low:    bullish >= 1 AND bearish <= 2
    # Mirror for bearish signals.

    net = bull_score - bear_score
    total_signal = bull_score + bear_score

    # ── Confidence level determination (bull case) ──
    if bull_score >= 3.0 and bear_score == 0.0:
        conf_level = 'high'
        if suggestion is None:
            suggestion = 'BUY'
    elif bull_score >= 2.0 and bear_score <= 1.0:
        conf_level = 'medium'
        if suggestion is None:
            suggestion = 'BUY'
    elif bull_score >= 1.0 and bear_score <= 2.0:
        conf_level = 'low'
        if suggestion is None:
            suggestion = 'BUY'
    # ── Bear case ──
    elif bear_score >= 3.0 and bull_score == 0.0:
        conf_level = 'high'
        if suggestion is None:
            suggestion = 'SELL'
    elif bear_score >= 2.0 and bull_score <= 1.0:
        conf_level = 'medium'
        if suggestion is None:
            suggestion = 'SELL'
    elif bear_score >= 1.0 and bull_score <= 2.0:
        conf_level = 'low'
        if suggestion is None:
            suggestion = 'SELL'
    else:
        conf_level = 'conflicted'
        suggestion = 'NO SIGNAL'

    # ── Override with legacy strong-signal detection ──
    if net >= 5.0 and bear_score < 3.0:
        suggestion = 'STRONG BUY'
        conf_level = 'high'
    elif net <= -5.0 and bull_score < 3.0:
        suggestion = 'STRONG SELL'
        conf_level = 'high'

    # ── Assign numeric confidence ──
    if conf_level == 'high':
        confidence_num = 0.85 + min(abs(net) * 0.02, 0.10) if net != 0 else 0.85
    elif conf_level == 'medium':
        confidence_num = 0.70
    elif conf_level == 'low':
        confidence_num = 0.55
    elif total_signal < 1.0:
        confidence_num = 0.30
    else:
        confidence_num = 0.35
        signal_conflict = True

    if suggestion == 'NO SIGNAL':
        confidence_num = min(confidence_num, 0.35)

    confidence = f'{confidence_num:.2f}'
    warning = f'bull={bull_score:.1f} bear={bear_score:.1f} net={net:+.1f} conf={conf_level}'

    return suggestion, confidence, warning, signal_conflict

def assemble_asset_json(
    raw_data: dict,
    ta_data: Optional[dict],
    sentiment_data: dict,
    onchain_data: dict,
    derivatives_data: Optional[dict] = None,
    regime_result: Optional[RegimeResult] = None,
    macro_regime_override: Optional[tuple[str, float]] = None,
) -> dict:
    """
    Assemble the final JSON block for one asset.
    Signature matches main.py's calls.

    Args:
        raw_data: From data_collector — dict with current_price, ohlcv, changes, etc.
        ta_data: From ta_engine — dict with RSI, MACD, SMA, volume trend, or None.
        sentiment_data: Dict with "sentiment" and "sentiment_source".
        onchain_data: Dict with "onchain_risk" and "onchain_source".

    Returns:
        Dict matching the final output JSON schema.
    """
    ta = ta_data or {}
    symbol = raw_data.get("symbol", "???")

    # ── Alerts ──────
    alerts = evaluate_alerts(ta) if ta else []

    # ── Signal interpretation ──────
    rsi_sig = interpret_rsi(ta.get("rsi_14")) if ta else None
    macd_sig = macd_signal(ta) if ta else None

    sentiment = sentiment_data.get("sentiment")
    sentiment_source = sentiment_data.get("sentiment_source")
    onchain_risk = onchain_data.get("onchain_risk")
    onchain_source = onchain_data.get("onchain_source")

    # Derivatives
    deriv = derivatives_data or {}

    # Regime detection — applies signal modifiers before confidence resolution
    regime = regime_result
    sig_mod = regime.signal_modifier if regime else {}

    # Apply regime-based signal suppression
    if sig_mod.get("suppress_rsi_oversold") and rsi_sig == "oversold":
        rsi_sig = None
        log.debug("RSI oversold suppressed — regime: %s", regime.regime if regime else "N/A")
    if sig_mod.get("suppress_rsi_overbought") and rsi_sig == "overbought":
        rsi_sig = None
        log.debug("RSI overbought suppressed — regime: %s", regime.regime if regime else "N/A")
    if sig_mod.get("suppress_macd"):
        macd_sig = None
        log.debug("MACD suppressed — regime: %s", regime.regime if regime else "N/A")

    # Inject sentiment_score into ta for resolve_signal_and_confidence
    if ta is not None:
        ta = dict(ta)  # don't mutate original
    else:
        ta = {}
    ta["sentiment_score"] = sentiment_data.get("sentiment_score")

    # ── Alpha signal priority gate (PredScope + Adanos) ──
    # PredScope high-confidence or PredScope+Adanos agreement take priority
    # over ML models, giving first look to prediction-market and social data.
    alpha = _get_alpha_signals()
    ps = alpha.get("predscope", {}).get(symbol)
    ad = alpha.get("adanos", {}).get(symbol)
    alpha_source = None

    if ps and ps.get("confidence", 0) > 0.75:
        alpha_source = "predscope"
        log.info("[%s] ALPHA: PredScope direct (%s, %.3f)", symbol, ps["direction"], ps["confidence"])
    elif ps and ad and ps["direction"] == ad["direction"]:
        alpha_source = "predscope+adanos"
        log.info("[%s] ALPHA: PredScope+Adanos agree (%s, %.3f)", symbol, ps["direction"], max(ps["confidence"], ad["confidence"]) * 1.1)
    elif ad and ad.get("confidence", 0) > 0.60:
        alpha_source = "adanos_social"
        log.info("[%s] ALPHA: Adanos solo (%s, %.3f)", symbol, ad["direction"], ad["confidence"])

    # ── Orderflow / footprint candle signal ──
    orderflow_data: dict = {}
    try:
        from orderflow_collector import get_orderflow_for_symbol
        _of = get_orderflow_for_symbol(symbol)
        if _of:
            orderflow_data = _of
            log.debug("[%s] Orderflow: %s conf=%.2f", symbol,
                      _of.get("orderflow_direction"), _of.get("orderflow_confidence", 0))
    except Exception as _ofe:
        log.debug("[%s] Orderflow unavailable: %s", symbol, _ofe)

    # ── ML prediction gate (LSTM priority → LightGBM fallback → TA voting) ──
    ml_suggestion = None
    ml_confidence = None
    ml_prob = 0.5
    ml_weights = {}
    ml_adjusted_prob = None

    # Priority 1: LSTM deep learning predictor
    try:
        from dl_predictor import predict_from_ohlcv
        ohlcv_raw = raw_data.get("ohlcv")
        if ohlcv_raw:
            import pandas as pd
            ohlcv_df = pd.DataFrame(ohlcv_raw, columns=["close", "high", "low", "open", "volume"])
            ohlcv_df.index = pd.to_datetime(ohlcv_df.index) if "timestamp" in ohlcv_raw else pd.DatetimeIndex([pd.Timestamp.now()] * len(ohlcv_df))
            dl_result = predict_from_ohlcv(symbol, ohlcv_df)
            if dl_result is not None:
                dl_dir = dl_result.get("direction", "")
                dl_prob = dl_result.get("probability", 0.5)
                dl_conf = dl_result.get("confidence", "low")
                ml_prob = dl_prob
                ml_adjusted_prob = dl_prob
                if dl_dir == "UP":
                    ml_suggestion = "WATCH FOR ENTRY"
                    ml_confidence = "medium" if dl_conf == "medium" else "high"
                elif dl_dir == "DOWN":
                    ml_suggestion = "WATCH FOR EXIT"
                    ml_confidence = "medium" if dl_conf == "medium" else "high"
                log.info("[%s] LSTM prediction: %s (prob=%.3f conf=%s)", symbol, dl_dir, dl_prob, dl_conf)
    except Exception as e:
        log.debug("[%s] LSTM prediction unavailable: %s", symbol, e)

    # Priority 2: LightGBM fallback
    if not ml_suggestion:
        try:
            from ml_predictor import predict_next
            import lightgbm as lgb
            model_path = data_root() / "models" / f"{symbol.lower()}_lightgbm_latest.txt"
            if model_path.exists():
                model = lgb.Booster(model_file=str(model_path))
                ohlcv_raw = raw_data.get("ohlcv")
                if ohlcv_raw:
                    import pandas as pd
                    ohlcv_df = pd.DataFrame(ohlcv_raw, columns=["close", "high", "low", "open", "volume"])
                    ohlcv_df.index = pd.to_datetime(ohlcv_df.index) if "timestamp" in ohlcv_raw else pd.DatetimeIndex([pd.Timestamp.now()] * len(ohlcv_df))
                    ml_pred = predict_next(model, ohlcv_df)
                    ml_direction = ml_pred.get("direction", "")
                    ml_prob = ml_pred.get("probability", 0.5)

                    # Bayesian signal weighting — adjust raw ML probability by model track record
                    try:
                        from bayesian_weighting import get_signal_weights
                        ml_weights = get_signal_weights(symbol)
                        if ml_direction == "BUY":
                            bw = ml_weights.get("buy_weight", 0.5)
                        elif ml_direction == "SELL":
                            bw = ml_weights.get("sell_weight", 0.5)
                        else:
                            bw = 0.5
                        ml_adjusted_prob = ml_prob * bw
                    except Exception:
                        ml_adjusted_prob = ml_prob

                    adj = ml_adjusted_prob if ml_adjusted_prob is not None else ml_prob
                    if ml_direction == "BUY":
                        ml_suggestion = "WATCH FOR ENTRY"
                        ml_confidence = "medium" if adj < 0.8 else "high"
                    elif ml_direction == "SELL":
                        ml_suggestion = "WATCH FOR EXIT"
                        ml_confidence = "medium" if adj < 0.8 else "high"
        except Exception as e:
            log.debug("[%s] LightGBM prediction unavailable: %s", symbol, e)

    if ml_suggestion and ml_confidence:
        suggestion, confidence, warning, conflict = ml_suggestion, ml_confidence, None, False
        ml_prob_display = ml_adjusted_prob if ml_adjusted_prob is not None else ml_prob
        log.info("[%s] ML prediction: %s (raw=%.3f adj=%.3f)", symbol, ml_suggestion, ml_prob,
                 ml_prob_display)
    else:
        suggestion, confidence, warning, conflict = resolve_signal_and_confidence(
            rsi_sig, macd_sig,
            ta.get("price_vs_sma200") if ta else None,
            alerts, sentiment, onchain_risk,
            derivatives_signal=deriv.get("derivatives_signal"),
            ta=ta,
        )

    # ── RL agent signal (fallback when no alpha and no ML) ──
    if not alpha_source and not ml_suggestion:
        try:
            from rl_agent import rl_signal_for_assembly
            _rl = rl_signal_for_assembly({
                "rsi_14":             ta.get("rsi_14"),
                "macd_histogram":     ta.get("macd_histogram"),
                "price_vs_sma200":    ta.get("price_vs_sma200"),
                "volume_trend_ratio": ta.get("volume_trend_ratio"),
                "sentiment_score":    ta.get("sentiment_score"),
            })
            if _rl:
                dir_map = {"BUY": "WATCH FOR ENTRY", "SELL": "WATCH FOR EXIT"}
                _rl_sug = dir_map.get(_rl["direction"])
                if _rl_sug:
                    suggestion = _rl_sug
                    confidence = "medium" if _rl["confidence"] > 0.60 else "low"
                    warning    = f"RL:Q-table(conf={_rl['confidence']:.2f})"
                    conflict   = False
                    log.info("[%s] RL signal: %s conf=%.2f", symbol, _rl["direction"], _rl["confidence"])
        except Exception as _rle:
            log.debug("[%s] RL agent unavailable: %s", symbol, _rle)

    # ── Alpha signal override ──
    # When PredScope or Adanos has a strong signal, it takes priority over ML/TA.
    if alpha_source == "predscope" and ps:
        suggestion = ps["direction"]
        confidence = f"{ps['confidence']:.2f}"
        warning = f"Alpha:PredScope({ps.get('market_question','')[:60]})"
        conflict = False
    elif alpha_source == "predscope+adanos" and ps and ad:
        suggestion = ps["direction"]
        boosted = min(max(ps["confidence"], ad["confidence"]) * 1.1, 1.0)
        confidence = f"{boosted:.2f}"
        warning = "Alpha:PredScope+Adanos(agree)"
        conflict = False
    elif alpha_source == "adanos_social" and ad:
        suggestion = ad["direction"]
        confidence = f"{ad['confidence']:.2f}"
        warning = f"Alpha:Adanos({ad.get('reason','')[:60]})"
        conflict = False

    # Apply regime confidence cap (skip for ML — ML already encodes regime in features)
    if not ml_suggestion:
        cap = sig_mod.get("cap_confidence")
        conf_order = ["high", "medium", "low", "conflicted"]
        if cap and confidence in conf_order and cap in conf_order:
            if conf_order.index(confidence) < conf_order.index(cap):
                log.debug("Confidence capped: %s → %s (regime: %s)", confidence, cap, regime.regime if regime else "N/A")
                confidence = cap

    # Append regime warning to reasoning
    r_warn = sig_mod.get("regime_warning")
    if r_warn:
        warning = (warning + " " if warning else "") + r_warn

    # Macro regime modifier — caps BUY confidence during risk-off environments
    macro_regime, macro_conf = (
        macro_regime_override
        if macro_regime_override is not None
        else _load_macro_regime()
    )
    if macro_regime == "risk_off" and suggestion in ("BUY", "STRONG BUY"):
        try:
            conf_num = float(confidence)
            capped = round(min(conf_num, 0.65), 2)
            if capped < conf_num:
                log.debug("Macro risk-off: confidence capped %.2f → %.2f for %s",
                          conf_num, capped, symbol)
                confidence = f"{capped:.2f}"
        except (ValueError, TypeError):
            pass
        warning = (f"Macro risk-off (score={macro_conf:.2f}). {warning}" if warning
                   else f"Macro risk-off environment (score={macro_conf:.2f}).")

    # ── Signal filter gating (BUY only — SELL handled by RSI gate in trading_agent) ──
    if _HAS_SIGNAL_FILTERS and ta and suggestion in ("BUY", "STRONG BUY"):
        try:
            # Map ta_engine key names to signal_filters expected keys
            ta_mapped = dict(ta)
            if "rsi_14" in ta_mapped and "rsi" not in ta_mapped:
                ta_mapped["rsi"] = ta_mapped["rsi_14"]
            if "macd_signal_line" in ta_mapped and "macd_signal" not in ta_mapped:
                ta_mapped["macd_signal"] = ta_mapped["macd_signal_line"]

            available = ["multi_timeframe"]
            volume = ta.get("volume")
            vol_avg = ta.get("volume_sma_20")
            if volume is not None and vol_avg is not None and vol_avg > 0:
                available.append("volume")

            filter_ctx = {
                "4h": {},          # No real 4H data — trigger RSI check skipped safely
                "1d": ta_mapped,   # 1D bias: RSI>50, MACD bullish, SMA50
                "1w": ta_mapped,   # 1W bias (proxy with 1D data — conservative)
                "current_volume": volume,
                "volume_avg_20": vol_avg,
            }
            filtered, filter_log = _apply_filters(suggestion, filter_ctx, available)

            if filtered == "HOLD":
                log.info("[%s] Signal %s → HOLD (MTF filter) — %s",
                         symbol, suggestion,
                         filter_log.split("\n")[-1] if filter_log else "rejected")
                suggestion = "HOLD"
                confidence = "0.25"
                warning = (warning + " | downgraded by MTF filter") if warning else "Signal downgraded by MTF filter"
            else:
                log.debug("[%s] Signal %s passed MTF filter gating", symbol, suggestion)
        except Exception as e:
            log.debug("[%s] Signal filter gating error: %s", symbol, e)

    current_price = raw_data.get("current_price") or ta.get("close")
    sma_val = ta.get("sma_200") if ta else None

    atr_result = atr_stop_target(
        price=current_price,
        ohlcv=raw_data.get("ohlcv", []),
        suggestion=suggestion,
    )
    stop = atr_result.get("stop_loss_suggestion")
    target = atr_result.get("target_suggestion")
    atr_val = atr_result.get("atr_14")

    # ── Build reasoning ──────
    reasoning_parts = []
    if rsi_sig:
        reasoning_parts.append(f"RSI {ta['rsi_14']} ({rsi_sig})")
    if macd_sig:
        reasoning_parts.append(f"MACD {macd_sig}")
    if ta and ta.get("price_vs_sma200"):
        reasoning_parts.append(f"price {ta['price_vs_sma200']} SMA200")
    if ta and ta.get("volume_trend_ratio") and ta["volume_trend_ratio"] >= 1.5:
        reasoning_parts.append(f"volume {ta['volume_trend_ratio']}x avg")
    if sentiment:
        reasoning_parts.append(f"sentiment {sentiment}")
    if onchain_risk and onchain_risk != "low":
        reasoning_parts.append(f"on-chain risk: {onchain_risk}")

    # Include derivatives note if it contains warnings (squeeze/capitulation)
    deriv_note = deriv.get("derivatives_note")
    if deriv_note and ("squeeze" in deriv_note.lower() or "capitulation" in deriv_note.lower()):
        reasoning_parts.append(f"derivatives: {deriv_note}")

    reasoning = ". ".join(reasoning_parts) + "." if reasoning_parts else "No strong signals."

    if regime:
        reasoning = f"{regime.note} {reasoning}"

    if conflict:
        conflicting = []
        if rsi_sig == "oversold":
            conflicting.append("RSI oversold (bullish)")
        elif rsi_sig == "overbought":
            conflicting.append("RSI overbought (bearish)")
        if macd_sig == "bullish_crossover":
            conflicting.append("MACD bullish")
        elif macd_sig == "bearish_crossover":
            conflicting.append("MACD bearish")
        if sentiment == "positive":
            conflicting.append("sentiment positive")
        elif sentiment == "negative":
            conflicting.append("sentiment negative")
        reasoning = f"Conflicting signals: {', '.join(conflicting)}. Waiting for convergence."

    asset_json = {
        "symbol": symbol,
        "current_price": current_price,
        "price_change_24h_pct": raw_data.get("price_change_24h_pct"),
        "price_change_7d_pct": raw_data.get("price_change_7d_pct"),
        "rsi_14": ta.get("rsi_14") if ta else None,
        "rsi_signal": rsi_sig,
        "macd_signal": macd_sig,
        "price_vs_sma200": ta.get("price_vs_sma200") if ta else None,
        # New indicators (Phase A — 12-indicator engine)
        "adx_14": ta.get("adx_14") if ta else None,
        "adx_signal": ta.get("adx_signal") if ta else None,
        "bb_upper": ta.get("bb_upper") if ta else None,
        "bb_lower": ta.get("bb_lower") if ta else None,
        "bb_percent_b": ta.get("bb_percent_b") if ta else None,
        "bb_signal": ta.get("bb_signal") if ta else None,
        "cci_20": ta.get("cci_20") if ta else None,
        "cci_signal": ta.get("cci_signal") if ta else None,
        "stochrsi_k": ta.get("stochrsi_k") if ta else None,
        "stochrsi_d": ta.get("stochrsi_d") if ta else None,
        "stochrsi_signal": ta.get("stochrsi_signal") if ta else None,
        "psar": ta.get("psar") if ta else None,
        "psar_signal": ta.get("psar_signal") if ta else None,
        "kc_upper": ta.get("kc_upper") if ta else None,
        "kc_lower": ta.get("kc_lower") if ta else None,
        "kc_mid": ta.get("kc_mid") if ta else None,
        "obv": ta.get("obv") if ta else None,
        "obv_trend": ta.get("obv_trend") if ta else None,
        "volume_trend": f"{ta.get('volume_trend_ratio', 'N/A')}x_30d_average" if ta else None,
        "candle_aggregate": ta.get("candle_aggregate") if ta else None,
        "candle_patterns": ta.get("candle_patterns") if ta else None,
        "ichimoku_bullish": ta.get("ichimoku_bullish") if ta else None,
        "ichimoku_tenkan": ta.get("ichimoku_tenkan") if ta else None,
        "ichimoku_kijun": ta.get("ichimoku_kijun") if ta else None,
        "ichimoku_span_a": ta.get("ichimoku_span_a") if ta else None,
        "ichimoku_span_b": ta.get("ichimoku_span_b") if ta else None,
        "vwap": ta.get("vwap") if ta else None,
        "sentiment": sentiment,
        "sentiment_source": sentiment_source if sentiment else None,
        "sentiment_score": sentiment_data.get("sentiment_score"),
        "sentiment_distribution": sentiment_data.get("sentiment_distribution"),
        "sentiment_summary": sentiment_data.get("sentiment_summary"),
        "articles_found": sentiment_data.get("articles_found"),
        "articles_scored": sentiment_data.get("articles_scored"),
        "articles_filtered": sentiment_data.get("articles_filtered"),
        "onchain_risk": onchain_risk,
        "onchain_source": onchain_source,

        # Derivatives fields
        "funding_rate": deriv.get("funding_rate"),
        "funding_rate_pct": deriv.get("funding_rate_pct"),
        "funding_rate_annualized": deriv.get("funding_rate_annualized"),
        "funding_signal": deriv.get("funding_signal"),
        "funding_source": deriv.get("funding_source"),
        "open_interest_usd": deriv.get("open_interest_usd"),
        "oi_trend": deriv.get("oi_trend"),
        "oi_change_pct": deriv.get("oi_change_pct"),
        "oi_source": deriv.get("oi_source"),
        "derivatives_signal": deriv.get("derivatives_signal"),
        "derivatives_note": deriv.get("derivatives_note"),
        "orderflow_direction": orderflow_data.get("orderflow_direction"),
        "orderflow_confidence": orderflow_data.get("orderflow_confidence"),
        "orderflow_reasons": orderflow_data.get("orderflow_reasons"),
        "orderflow_delta_pct": orderflow_data.get("delta_pct"),
        "suggestion": suggestion,
        "confidence": confidence,
        "signal_source": alpha_source or "ml_ta",
        "signal_conflict": conflict,
        "reasoning": reasoning,
        "stop_loss_suggestion": stop,
        "target_suggestion": target,
        "atr_14": atr_result.get("atr_14"),
        "atr_pct": atr_result.get("atr_pct"),
        "stop_method": atr_result.get("stop_method"),
        "stop_note": atr_result.get("stop_note"),
        "warning": warning,
        "alerts": alerts,

        # Regime fields (technical)
        "market_regime": regime.regime if regime else None,
        "regime_confidence": regime.confidence if regime else None,
        "regime_adx": regime.adx_value if regime else None,
        "regime_note": regime.note if regime else None,

        # Macro regime fields
        "macro_regime": macro_regime,
        "macro_regime_confidence": macro_conf,

        # Bayesian signal weighting
        "bayesian_weights": ml_weights,
        "ml_adjusted_prob": ml_adjusted_prob,
    }

    return asset_json


def assemble_full_report(assembled_assets: list[dict]) -> dict:
    """
    Full report wrapper. Signature matches main.py's call.
    Takes a list of assembled asset dicts.
    """
    risk_warnings = []
    best_trade = None
    best_confidence_rank = {"conflicted": 0, "low": 1, "medium": 2, "high": 3}
    suggestion_rank = {
        "NO SIGNAL": 0,
        "CAUTIOUS EXIT": 1,
        "CAUTIOUS ENTRY": 1,
        "WATCH FOR EXIT": 2,
        "WATCH FOR ENTRY": 3,
    }

    for asset in assembled_assets:
        if asset.get("warning"):
            risk_warnings.append(f"{asset['symbol']}: {asset['warning']}")

        conf = asset.get("confidence", "low")
        if best_trade is None or best_confidence_rank.get(conf, 0) > best_confidence_rank.get(best_trade.get("confidence", "low"), 0):
            if asset.get("suggestion") and asset["suggestion"] not in ("NO SIGNAL",):
                best_trade = {"symbol": asset["symbol"], "suggestion": asset["suggestion"], "confidence": conf}
        # Tiebreaker: same confidence → suggestion priority (WATCH FOR ENTRY > WATCH FOR EXIT > CAUTIOUS)
        elif best_confidence_rank.get(conf, 0) == best_confidence_rank.get(best_trade.get("confidence", "low"), 0):
            sug = asset.get("suggestion", "NO SIGNAL")
            current_best_sug = best_trade.get("suggestion", "NO SIGNAL") if best_trade else "NO SIGNAL"
            if suggestion_rank.get(sug, 0) > suggestion_rank.get(current_best_sug, 0):
                best_trade = {"symbol": asset["symbol"], "suggestion": sug, "confidence": conf}

    # Inject auxiliary data into report
    prediction_data = _load_prediction_data()
    social_data = _load_social_sentiment()

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "assets": assembled_assets,
        "highest_confidence_trade": best_trade,
        "risk_warnings": risk_warnings,
        "prediction_markets": prediction_data,
        "social_sentiment": social_data,
    }
