from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    NO_DATA = "NO_DATA"
    UNKNOWN = "UNKNOWN"


class CapabilityStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CostEvidenceQuality(StrEnum):
    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class DecisionAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    STRONG_BUY = "STRONG_BUY"
    STRONG_SELL = "STRONG_SELL"
    WAIT = "WAIT"
    NO_SIGNAL = "NO_SIGNAL"
    WATCH_FOR_ENTRY = "WATCH_FOR_ENTRY"


class ExecutionMode(StrEnum):
    PAPER = "PAPER"
    DRYRUN = "DRYRUN"
    LIVE = "LIVE"


class ExecutionCapability(StrEnum):
    NON_LIVE = "NON_LIVE"
    LIVE_BLOCKED = "LIVE_BLOCKED"
    LIVE_AVAILABLE = "LIVE_AVAILABLE"


class AssetClass(StrEnum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUTURE = "FUTURE"
    FOREX = "FOREX"
    UNKNOWN = "UNKNOWN"


class KillSwitchState(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Asset(StrictModel):
    asset_id: str
    symbol: str
    asset_class: AssetClass


class RiskAssessment(StrictModel):
    position_size_pct: float
    stop_loss_pct: float
    risk_level: RiskLevel
    rationale: str


class MarketAssetSnapshot(StrictModel):
    symbol: str
    current_price: float
    price_change_24h_pct: float
    price_change_7d_pct: float
    rsi_14: float
    rsi_signal: Literal["oversold", "overbought", "neutral"]
    macd_signal: Literal["bullish_crossover", "bearish_crossover", "neutral"]
    price_vs_sma200: Literal["above", "below"]
    volume_trend: str
    sentiment: str | None
    sentiment_source: str | None
    sentiment_score: float | None
    sentiment_distribution: dict[str, float] | None
    sentiment_summary: str | None
    articles_found: float | None
    articles_scored: float | None
    articles_filtered: float | None
    onchain_risk: str | None
    onchain_source: str | None
    funding_rate: float | None
    funding_rate_pct: float | None
    funding_rate_annualized: float | None
    funding_signal: str | None
    funding_source: str | None
    open_interest_usd: float | None
    oi_trend: str | None
    oi_change_pct: float | None
    oi_source: str | None
    derivatives_signal: str | None
    derivatives_note: str | None
    suggestion: DecisionAction
    confidence: ConfidenceLevel
    signal_conflict: bool
    reasoning: str
    stop_loss_suggestion: float | None
    target_suggestion: float | None
    atr_14: float
    atr_pct: float
    stop_method: str
    stop_note: str
    warning: str | None
    alerts: list[str]
    market_regime: str | None
    regime_confidence: float | None
    regime_adx: float | None
    regime_note: str | None
    risk_assessment: RiskAssessment
    memory_context: str
    debate_context: str
    risk_context: str


class DataFreshness(StrictModel):
    status: FreshnessStatus
    as_of: datetime | None
    age_seconds: int | None = Field(ge=0)
    stale_after_seconds: int = Field(ge=1)


class MarketReport(StrictModel):
    report_id: str
    as_of: datetime
    assets: list[MarketAssetSnapshot]
    source_file: str
    invalid_source_count: int = Field(ge=0)


class Signal(StrictModel):
    asset: MarketAssetSnapshot
    as_of: datetime
    source_report_id: str
    recent_decisions: list["DecisionRecord"]


class DecisionSignals(StrictModel):
    symbol: str
    close: float
    rsi_14: float
    macd_line: float
    macd_signal_line: float
    macd_histogram: float
    sma_200: float
    price_vs_sma200: str
    volume_24h: float
    volume_30d_avg: float
    volume_trend_ratio: float
    signal: str | None
    calculated_at: datetime | None


class DecisionRecord(StrictModel):
    decision_id: str
    asset: str
    action: DecisionAction
    confidence: float = Field(ge=0.0, le=1.0)
    decision_at: datetime
    price_at_decision: float
    reflected: bool
    signals: DecisionSignals
    report_snippet: str = ""


class DecisionSummary(StrictModel):
    total: int = Field(ge=0)
    latest_at: datetime | None


class PaginatedResponse(StrictModel):
    items: list[DecisionRecord]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_next: bool


class SystemStatus(StrictModel):
    api_liveness: Literal["UP"]
    api_readiness: Literal["READY", "NOT_READY"]
    backend_service_liveness: Literal["ALIVE", "STALE", "UNKNOWN"]
    research_pipeline_health: Literal["HEALTHY", "STALE", "NO_DATA", "UNKNOWN"]
    research_data_freshness: DataFreshness
    live_price_freshness: DataFreshness
    database_status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
    requested_mode: ExecutionMode
    effective_mode: ExecutionMode
    execution_capability: ExecutionCapability
    kill_switch_state: KillSwitchState
    orders_count: int | None = Field(ge=0)
    trades_count: int | None = Field(ge=0)


class CapabilityEvidence(StrictModel):
    capability_id: str
    name: str
    status: CapabilityStatus
    last_run_at: datetime | None
    valid_until: datetime | None
    benchmark_run_id: str | None
    metric: float | None
    threshold: float | None
    evidence_ref: str | None


class CostSession(StrictModel):
    session: str
    symbols: list[str]
    steps: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    decisions: int = Field(ge=0)
    estimated_cost: float = Field(ge=0.0)


class CostSummary(StrictModel):
    evidence_quality: CostEvidenceQuality
    currency: Literal["USD"]
    total_sessions: int = Field(ge=0)
    total_llm_calls: int | None = Field(default=None, ge=0)
    total_tool_calls: int | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0.0)
    sessions: list[CostSession]
    note: str


class DeploymentMeta(StrictModel):
    service: str
    git_commit: str
    build_time: str
    deployment_id: str
    repo_root_identifier: str
    data_root_identifier: str
    schema_versions: list[str]
    requested_mode: ExecutionMode
    effective_mode: ExecutionMode
    execution_capability: ExecutionCapability
    live_execution_enabled: bool
    live_trading_approved: bool
    kill_switch_state: KillSwitchState


class HealthData(StrictModel):
    status: Literal["UP", "READY", "NOT_READY"]


class ApiError(StrictModel):
    code: str
    message: str
    details: dict[str, Any]


T = TypeVar("T")


class ApiEnvelope(StrictModel, Generic[T]):
    schema_version: str
    trace_id: str
    generated_at: datetime
    data: T
    freshness: DataFreshness | None = None


class ApiErrorEnvelope(StrictModel):
    schema_version: str
    trace_id: str
    generated_at: datetime
    error: ApiError


class MarketLatestData(StrictModel):
    report: MarketReport | None


class SignalListData(StrictModel):
    items: list[Signal]
    total: int = Field(ge=0)


class CapabilityListData(StrictModel):
    items: list[CapabilityEvidence]
    total: int = Field(ge=0)
    verified: int = Field(ge=0)
    summary: str


class HealthEnvelope(ApiEnvelope[HealthData]):
    pass


class MetaEnvelope(ApiEnvelope[DeploymentMeta]):
    pass


class SystemStatusEnvelope(ApiEnvelope[SystemStatus]):
    pass


class MarketLatestEnvelope(ApiEnvelope[MarketLatestData]):
    pass


class SignalListEnvelope(ApiEnvelope[SignalListData]):
    pass


class DecisionPageEnvelope(ApiEnvelope[PaginatedResponse]):
    pass


class DecisionDetailEnvelope(ApiEnvelope[DecisionRecord]):
    pass


class CapabilityListEnvelope(ApiEnvelope[CapabilityListData]):
    pass


class CostSummaryEnvelope(ApiEnvelope[CostSummary]):
    pass
