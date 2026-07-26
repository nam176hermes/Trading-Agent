"""
schemas.py — Single Source of Truth for Crypto Trading Agent

Pydantic models for all structured data flowing through the trading pipeline.
Replaces free-text parsing with typed, validated schemas.

Phase 1: Core trading decision models
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime
from typing import Literal, Optional

# Type aliases for clarity
Action = Literal["BUY", "SELL", "HOLD", "WATCH"]
Persona = Literal["aggressive", "conservative", "neutral"]


class TradingSignal(BaseModel):
    """Structured trading signal with validation."""
    asset: str
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    entry_price: Optional[float] = Field(default=None, ge=0)
    stop_loss: Optional[float] = Field(default=None, ge=0)
    take_profit: Optional[float] = Field(default=None, ge=0)
    time_horizon: Literal["intraday", "swing", "position"] = "swing"
    reasoning: str = Field(min_length=20)

    @field_validator("asset")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class BullArgument(BaseModel):
    """Bullish thesis with supporting evidence."""
    thesis: str = Field(default="No thesis provided", min_length=1)
    description: Optional[str] = None  # LLM sometimes returns "description" instead of "thesis"
    supporting_signals: list[str] = Field(default_factory=list)
    counter_to_bear: Optional[str] = None
    conviction: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode='before')
    @classmethod
    def _remap_fields(cls, data: any) -> any:
        """Map common LLM field name mistakes to correct schema fields."""
        if isinstance(data, dict):
            # Map truncated/misspelled variants to correct field names
            _remap_key(data, 'thesis', ('description', 'descriptio', 'bull_argument',
                'bear_argument', 'argument', 'summary', 'content', 'text'))
            _remap_key(data, 'supporting_signals', ('signals', 'supporting_signal',
                'bull_signals', 'bullish_signals'))
            _remap_key(data, 'conviction', ('confidence', 'score'))
            _remap_key(data, 'risk_factors', ('risks', 'risk_factor',
                'bear_signals', 'bearish_signals'))
        return data


def _remap_key(data: dict, target: str, aliases: tuple[str, ...]) -> None:
    """If target key is missing, try each alias and move its value to target."""
    if target in data:
        return
    for alt in aliases:
        if alt in data:
            data[target] = data.pop(alt)
            return


class BearArgument(BaseModel):
    """Bearish thesis with risk factors."""
    thesis: str = Field(default="No thesis provided", min_length=1)
    description: Optional[str] = None  # LLM sometimes returns "description" instead of "thesis"
    risk_factors: list[str] = Field(default_factory=list)
    counter_to_bull: Optional[str] = None
    conviction: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode='before')
    @classmethod
    def _remap_fields(cls, data: any) -> any:
        """Map common LLM field name mistakes to correct schema fields."""
        if isinstance(data, dict):
            # Map truncated/misspelled variants to correct field names
            _remap_key(data, 'thesis', ('description', 'descriptio', 'bull_argument',
                'bear_argument', 'argument', 'summary', 'content', 'text'))
            _remap_key(data, 'risk_factors', ('risks', 'risk_factor',
                'bear_signals', 'bearish_signals'))
            _remap_key(data, 'conviction', ('confidence', 'score'))
            _remap_key(data, 'supporting_signals', ('signals', 'supporting_signal',
                'bull_signals', 'bullish_signals'))
        return data


class DebateRound(BaseModel):
    """One round of bull/bear debate."""
    round_num: int
    bull: BullArgument
    bear: BearArgument


class RiskAssessment(BaseModel):
    """Risk persona evaluation."""
    persona: Persona
    accept_signal: bool
    position_size_pct: float = Field(ge=0.0, le=100.0)
    adjusted_stop_loss: Optional[float] = None
    adjusted_take_profit: Optional[float] = None
    rationale: str = Field(min_length=20)
    status: Literal["AVAILABLE", "UNAVAILABLE"] = "AVAILABLE"
    reason_code: Optional[str] = None
    trace_id: Optional[str] = None


class TradingDecision(BaseModel):
    """Complete trading decision with full audit trail."""
    timestamp: datetime
    asset: str
    initial_signal: TradingSignal
    debate_rounds: list[DebateRound]
    bull_synthesis: str
    bear_synthesis: str
    risk_assessments: list[RiskAssessment]
    final_action: Action
    final_position_size_pct: float = Field(ge=0.0, le=100.0)
    executive_summary: str = Field(min_length=20)
    entry_price: Optional[float] = Field(default=None, ge=0)
    exit_price: Optional[float] = Field(default=None, ge=0)
    pnl_pct: Optional[float] = None
    outcome_label: Optional[Literal["win", "loss", "breakeven"]] = None
    resolved_at: Optional[datetime] = None
    quality_score: Optional[dict] = None

# ── Phase 3: Analyst Reports ─────────────────────────────────────────────────────

class TechnicalReport(BaseModel):
    """Technical analyst report with indicators and levels."""
    asset: str
    rsi: Optional[float] = None
    macd: Optional[str] = None
    trend: Literal["bullish", "bearish", "neutral", "oversold", "overbought"]
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    volume_profile: Literal["high", "normal", "low", "anomalous"]
    key_signals: list[str] = Field(min_length=1)
    summary: str = Field(min_length=20)


class SentimentReport(BaseModel):
    """Sentiment analyst report with market emotion data."""
    asset: str
    fear_greed_index: Optional[float] = Field(default=None, ge=0, le=100)
    social_volume: Literal["high", "normal", "low", "spiking"]
    funding_rate: Optional[float] = None
    funding_bias: Literal["long_biased", "short_biased", "neutral"]
    key_signals: list[str] = Field(min_length=1)
    summary: str = Field(min_length=20)


class OnchainReport(BaseModel):
    """On-chain analyst report with whale movements and exchange flows."""
    asset: str
    whale_activity: Literal["accumulating", "distributing", "neutral", "anomalous"]
    exchange_outflows: Literal["strong", "moderate", "weak", "neutral"]
    exchange_inflows: Literal["strong", "moderate", "weak", "neutral"]
    hdl_state: Literal["accumulating", "distributing", "neutral"]  # HODLer behavior
    key_signals: list[str] = Field(min_length=1)
    summary: str = Field(min_length=20)
    note: str = "Stub implementation — data source not yet wired"


class MacroReport(BaseModel):
    """Macro/news analyst report with ETF flows and regulatory events."""
    asset: str
    etf_flows: Literal["strong_inflows", "moderate_inflows", "neutral", "outflows"]
    regulatory_status: Literal["favorable", "neutral", "concerning", "hostile"]
    macro_correlation: Literal["high", "moderate", "low", "inverse"]
    key_events: list[str] = Field(min_length=1)
    summary: str = Field(min_length=20)
    note: str = "Stub implementation — data source not yet wired"


# ── Phase 3: Portfolio Decision ──────────────────────────────────────────────────

class PortfolioDecision(BaseModel):
    """Portfolio manager's final decision on whether to ratify, modify, or reject a signal."""
    asset: str
    original_signal: TradingSignal
    action: Literal["ratify", "modify", "reject"]
    modified_action: Optional[Action] = None
    modified_position_size_pct: Optional[float] = Field(default=None, ge=0, le=100)
    modified_stop_loss: Optional[float] = Field(default=None, ge=0)
    modified_take_profit: Optional[float] = Field(default=None, ge=0)
    rationale: str = Field(min_length=20)
    risk_adjusted_return: Optional[float] = None
    conviction: float = Field(ge=0.0, le=1.0)
