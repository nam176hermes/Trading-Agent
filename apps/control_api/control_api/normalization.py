from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Mapping

from .contracts import (
    ConfidenceLevel,
    DecisionAction,
    DecisionRecord,
    DecisionSignals,
    MarketAssetSnapshot,
    RiskAssessment,
    RiskLevel,
)


def parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_action(value: object) -> DecisionAction:
    if not isinstance(value, str):
        return DecisionAction.NO_SIGNAL
    canonical = value.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {"NO__SIGNAL": "NO_SIGNAL"}
    canonical = aliases.get(canonical, canonical)
    try:
        return DecisionAction(canonical)
    except ValueError:
        return DecisionAction.NO_SIGNAL


def number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def optional_number(value: object) -> float | None:
    return number(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def normalize_symbol_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({
        item.strip().upper()
        for item in value
        if isinstance(item, str) and item.strip()
    })


def normalize_asset(value: Mapping[str, Any]) -> MarketAssetSnapshot:
    symbol = text(value.get("symbol")).upper()
    if not symbol or not isinstance(value.get("current_price"), (int, float)):
        raise ValueError("asset requires symbol and numeric current_price")
    raw_risk = value.get("risk_assessment")
    risk = raw_risk if isinstance(raw_risk, Mapping) else {}
    distribution = value.get("sentiment_distribution")
    normalized_distribution = None
    if isinstance(distribution, Mapping):
        normalized_distribution = {
            str(key): number(score)
            for key, score in distribution.items()
            if isinstance(score, (int, float)) and not isinstance(score, bool)
        }
    alerts = value.get("alerts")
    confidence = text(value.get("confidence"), "medium").lower()
    if confidence not in {item.value for item in ConfidenceLevel}:
        confidence = ConfidenceLevel.MEDIUM.value
    risk_level = text(risk.get("risk_level"), "MEDIUM").upper()
    if risk_level not in {item.value for item in RiskLevel}:
        risk_level = RiskLevel.MEDIUM.value
    rsi_signal = text(value.get("rsi_signal"), "neutral")
    if rsi_signal not in {"oversold", "overbought", "neutral"}:
        rsi_signal = "neutral"
    macd_signal = text(value.get("macd_signal"), "neutral")
    if macd_signal not in {"bullish_crossover", "bearish_crossover", "neutral"}:
        macd_signal = "neutral"
    return MarketAssetSnapshot(
        symbol=symbol,
        current_price=number(value.get("current_price")),
        price_change_24h_pct=number(value.get("price_change_24h_pct")),
        price_change_7d_pct=number(value.get("price_change_7d_pct")),
        rsi_14=number(value.get("rsi_14")),
        rsi_signal=rsi_signal,
        macd_signal=macd_signal,
        price_vs_sma200="above" if value.get("price_vs_sma200") == "above" else "below",
        volume_trend=text(value.get("volume_trend")),
        sentiment=optional_text(value.get("sentiment")),
        sentiment_source=optional_text(value.get("sentiment_source")),
        sentiment_score=optional_number(value.get("sentiment_score")),
        sentiment_distribution=normalized_distribution,
        sentiment_summary=optional_text(value.get("sentiment_summary")),
        articles_found=optional_number(value.get("articles_found")),
        articles_scored=optional_number(value.get("articles_scored")),
        articles_filtered=optional_number(value.get("articles_filtered")),
        onchain_risk=optional_text(value.get("onchain_risk")),
        onchain_source=optional_text(value.get("onchain_source")),
        funding_rate=optional_number(value.get("funding_rate")),
        funding_rate_pct=optional_number(value.get("funding_rate_pct")),
        funding_rate_annualized=optional_number(value.get("funding_rate_annualized")),
        funding_signal=optional_text(value.get("funding_signal")),
        funding_source=optional_text(value.get("funding_source")),
        open_interest_usd=optional_number(value.get("open_interest_usd")),
        oi_trend=optional_text(value.get("oi_trend")),
        oi_change_pct=optional_number(value.get("oi_change_pct")),
        oi_source=optional_text(value.get("oi_source")),
        derivatives_signal=optional_text(value.get("derivatives_signal")),
        derivatives_note=optional_text(value.get("derivatives_note")),
        suggestion=normalize_action(value.get("suggestion")),
        confidence=ConfidenceLevel(confidence),
        signal_conflict=value.get("signal_conflict") is True,
        reasoning=text(value.get("reasoning")),
        stop_loss_suggestion=optional_number(value.get("stop_loss_suggestion")),
        target_suggestion=optional_number(value.get("target_suggestion")),
        atr_14=number(value.get("atr_14")),
        atr_pct=number(value.get("atr_pct")),
        stop_method=text(value.get("stop_method")),
        stop_note=text(value.get("stop_note")),
        warning=optional_text(value.get("warning")),
        alerts=[item for item in alerts if isinstance(item, str)] if isinstance(alerts, list) else [],
        market_regime=optional_text(value.get("market_regime")),
        regime_confidence=optional_number(value.get("regime_confidence")),
        regime_adx=optional_number(value.get("regime_adx")),
        regime_note=optional_text(value.get("regime_note")),
        risk_assessment=RiskAssessment(
            position_size_pct=number(risk.get("position_size_pct")),
            stop_loss_pct=number(risk.get("stop_loss_pct")),
            risk_level=RiskLevel(risk_level),
            rationale=text(risk.get("rationale")),
        ),
        memory_context=text(value.get("_memory_context")),
        debate_context=text(value.get("_debate_context")),
        risk_context=text(value.get("_risk_context")),
    )


def stable_decision_id(line_number: int, raw_line: str) -> str:
    digest = hashlib.sha256(f"{line_number}:{raw_line}".encode()).hexdigest()[:24]
    return f"decision_{digest}"


def normalize_decision(value: Mapping[str, Any], *, line_number: int, raw_line: str) -> DecisionRecord:
    asset = text(value.get("ticker")).upper()
    if not asset:
        raise ValueError("decision ticker is required")
    raw_confidence = value.get("confidence")
    if not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
        raise ValueError("decision confidence must be numeric")
    confidence = max(0.0, min(1.0, float(raw_confidence)))
    raw_signals = value.get("signals")
    signals = raw_signals if isinstance(raw_signals, Mapping) else {}
    calculated_at = None
    if signals.get("calculated_at"):
        try:
            calculated_at = parse_datetime(signals.get("calculated_at"))
        except ValueError:
            calculated_at = None
    return DecisionRecord(
        decision_id=stable_decision_id(line_number, raw_line),
        asset=asset,
        action=normalize_action(value.get("suggestion")),
        confidence=confidence,
        decision_at=parse_datetime(value.get("stored_at") or value.get("date")),
        price_at_decision=number(value.get("price_at_decision")),
        reflected=value.get("reflected") is True,
        signals=DecisionSignals(
            symbol=text(signals.get("symbol"), asset).upper(),
            close=number(signals.get("close")),
            rsi_14=number(signals.get("rsi_14")),
            macd_line=number(signals.get("macd_line")),
            macd_signal_line=number(signals.get("macd_signal_line")),
            macd_histogram=number(signals.get("macd_histogram")),
            sma_200=number(signals.get("sma_200")),
            price_vs_sma200=text(signals.get("price_vs_sma200")),
            volume_24h=number(signals.get("volume_24h")),
            volume_30d_avg=number(signals.get("volume_30d_avg")),
            volume_trend_ratio=number(signals.get("volume_trend_ratio")),
            signal=optional_text(signals.get("signal")),
            calculated_at=calculated_at,
        ),
        report_snippet=text(value.get("report_snippet")),
    )
