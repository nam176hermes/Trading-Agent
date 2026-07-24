import { makeApi, Zodios, type ZodiosOptions } from "@zodios/core";
import { z } from "zod";

type CapabilityEvidence = Readonly<{
  benchmark_run_id: (string | null) | Readonly<Array<string | null>>;
  capability_id: string;
  evidence_ref: (string | null) | Readonly<Array<string | null>>;
  last_run_at: (string | null) | Readonly<Array<string | null>>;
  metric: (number | null) | Readonly<Array<number | null>>;
  name: string;
  status: CapabilityStatus;
  threshold: (number | null) | Readonly<Array<number | null>>;
  valid_until: (string | null) | Readonly<Array<string | null>>;
}>;
type CapabilityStatus = "PASS" | "FAIL" | "STALE" | "UNKNOWN";
type CapabilityListData = Readonly<{
  readonly items: Readonly<Array<CapabilityEvidence>>;
  summary: string;
  total: number;
  verified: number;
}>;
type CapabilityListEnvelope = Readonly<{
  data: CapabilityListData;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type DataFreshness = Readonly<{
  age_seconds: (number | null) | Readonly<Array<number | null>>;
  as_of: (string | null) | Readonly<Array<string | null>>;
  stale_after_seconds: number;
  status: FreshnessStatus;
}>;
type FreshnessStatus = "FRESH" | "STALE" | "NO_DATA" | "UNKNOWN";
type CostSummary = Readonly<{
  amount?: ((number | null) | Readonly<Array<number | null>>) | undefined;
  currency: string;
  evidence_quality: CostEvidenceQuality;
  note: string;
  readonly sessions: Readonly<Array<CostSession>>;
  total_llm_calls?:
    | ((number | null) | Readonly<Array<number | null>>)
    | undefined;
  total_sessions: number;
  total_tool_calls?:
    | ((number | null) | Readonly<Array<number | null>>)
    | undefined;
}>;
type CostEvidenceQuality = "EXACT" | "ESTIMATED" | "UNKNOWN";
type CostSession = Readonly<{
  decisions: number;
  estimated_cost: number;
  llm_calls: number;
  session: string;
  steps: number;
  readonly symbols: Readonly<Array<string>>;
  tool_calls: number;
}>;
type CostSummaryEnvelope = Readonly<{
  data: CostSummary;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type DecisionDetailEnvelope = Readonly<{
  data: DecisionRecord;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type DecisionRecord = Readonly<{
  action: DecisionAction;
  asset: string;
  confidence: number;
  decision_at: string;
  decision_id: string;
  price_at_decision: number;
  reflected: boolean;
  report_snippet?: string | undefined;
  signals: DecisionSignals;
}>;
type DecisionAction =
  | "BUY"
  | "SELL"
  | "HOLD"
  | "STRONG_BUY"
  | "STRONG_SELL"
  | "WAIT"
  | "NO_SIGNAL"
  | "WATCH_FOR_ENTRY";
type DecisionSignals = Readonly<{
  calculated_at: (string | null) | Readonly<Array<string | null>>;
  close: number;
  macd_histogram: number;
  macd_line: number;
  macd_signal_line: number;
  price_vs_sma200: string;
  rsi_14: number;
  signal: (string | null) | Readonly<Array<string | null>>;
  sma_200: number;
  symbol: string;
  volume_24h: number;
  volume_30d_avg: number;
  volume_trend_ratio: number;
}>;
type DecisionPageEnvelope = Readonly<{
  data: PaginatedResponse;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type PaginatedResponse = Readonly<{
  has_next: boolean;
  readonly items: Readonly<Array<DecisionRecord>>;
  page: number;
  page_size: number;
  total: number;
}>;
type DeploymentMeta = Readonly<{
  build_time: string;
  data_root_identifier: string;
  deployment_id: string;
  effective_mode: ExecutionMode;
  execution_capability: ExecutionCapability;
  git_commit: string;
  kill_switch_state: KillSwitchState;
  live_execution_enabled: boolean;
  live_trading_approved: boolean;
  repo_root_identifier: string;
  requested_mode: ExecutionMode;
  readonly schema_versions: Readonly<Array<string>>;
  service: string;
}>;
type ExecutionMode = "PAPER" | "DRYRUN" | "LIVE";
type ExecutionCapability = "NON_LIVE" | "LIVE_BLOCKED" | "LIVE_AVAILABLE";
type KillSwitchState = "ACTIVE" | "INACTIVE" | "UNKNOWN";
type HTTPValidationError = Partial<
  Readonly<{
    readonly detail: Readonly<Array<ValidationError>>;
  }>
>;
type ValidationError = Readonly<{
  ctx?: {} | undefined;
  input?: unknown | undefined;
  readonly loc: Readonly<
    Array<(string | number) | Readonly<Array<string | number>>>
  >;
  msg: string;
  type: string;
}>;
type HealthEnvelope = Readonly<{
  data: HealthData;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type HealthData = Readonly<{
  status: "UP" | "READY" | "NOT_READY";
}>;
type MarketAssetSnapshot = Readonly<{
  readonly alerts: Readonly<Array<string>>;
  articles_filtered: (number | null) | Readonly<Array<number | null>>;
  articles_found: (number | null) | Readonly<Array<number | null>>;
  articles_scored: (number | null) | Readonly<Array<number | null>>;
  atr_14: number;
  atr_pct: number;
  confidence: ConfidenceLevel;
  current_price: number;
  debate_context: string;
  derivatives_note: (string | null) | Readonly<Array<string | null>>;
  derivatives_signal: (string | null) | Readonly<Array<string | null>>;
  funding_rate: (number | null) | Readonly<Array<number | null>>;
  funding_rate_annualized: (number | null) | Readonly<Array<number | null>>;
  funding_rate_pct: (number | null) | Readonly<Array<number | null>>;
  funding_signal: (string | null) | Readonly<Array<string | null>>;
  funding_source: (string | null) | Readonly<Array<string | null>>;
  macd_signal: "bullish_crossover" | "bearish_crossover" | "neutral";
  market_regime: (string | null) | Readonly<Array<string | null>>;
  memory_context: string;
  oi_change_pct: (number | null) | Readonly<Array<number | null>>;
  oi_source: (string | null) | Readonly<Array<string | null>>;
  oi_trend: (string | null) | Readonly<Array<string | null>>;
  onchain_risk: (string | null) | Readonly<Array<string | null>>;
  onchain_source: (string | null) | Readonly<Array<string | null>>;
  open_interest_usd: (number | null) | Readonly<Array<number | null>>;
  price_change_24h_pct: number;
  price_change_7d_pct: number;
  price_vs_sma200: "above" | "below";
  reasoning: string;
  regime_adx: (number | null) | Readonly<Array<number | null>>;
  regime_confidence: (number | null) | Readonly<Array<number | null>>;
  regime_note: (string | null) | Readonly<Array<string | null>>;
  risk_assessment: RiskAssessment;
  risk_context: string;
  rsi_14: number;
  rsi_signal: "oversold" | "overbought" | "neutral";
  sentiment: (string | null) | Readonly<Array<string | null>>;
  sentiment_distribution: ({} | null) | Readonly<Array<{} | null>>;
  sentiment_score: (number | null) | Readonly<Array<number | null>>;
  sentiment_source: (string | null) | Readonly<Array<string | null>>;
  sentiment_summary: (string | null) | Readonly<Array<string | null>>;
  signal_conflict: boolean;
  stop_loss_suggestion: (number | null) | Readonly<Array<number | null>>;
  stop_method: string;
  stop_note: string;
  suggestion: DecisionAction;
  symbol: string;
  target_suggestion: (number | null) | Readonly<Array<number | null>>;
  volume_trend: string;
  warning: (string | null) | Readonly<Array<string | null>>;
}>;
type ConfidenceLevel = "low" | "medium" | "high";
type RiskAssessment = Readonly<{
  position_size_pct: number;
  rationale: string;
  risk_level: RiskLevel;
  stop_loss_pct: number;
}>;
type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
type MarketLatestData = Readonly<{
  report: (MarketReport | null) | Readonly<Array<MarketReport | null>>;
}>;
type MarketReport = Readonly<{
  as_of: string;
  readonly assets: Readonly<Array<MarketAssetSnapshot>>;
  invalid_source_count: number;
  report_id: string;
  source_file: string;
}>;
type MarketLatestEnvelope = Readonly<{
  data: MarketLatestData;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type MetaEnvelope = Readonly<{
  data: DeploymentMeta;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type Signal = Readonly<{
  as_of: string;
  asset: MarketAssetSnapshot;
  readonly recent_decisions: Readonly<Array<DecisionRecord>>;
  source_report_id: string;
}>;
type SignalListData = Readonly<{
  readonly items: Readonly<Array<Signal>>;
  total: number;
}>;
type SignalListEnvelope = Readonly<{
  data: SignalListData;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;
type SystemStatus = Readonly<{
  api_liveness: string;
  api_readiness: "READY" | "NOT_READY";
  backend_service_liveness: "ALIVE" | "STALE" | "UNKNOWN";
  database_status: "AVAILABLE" | "UNAVAILABLE" | "UNKNOWN";
  effective_mode: ExecutionMode;
  execution_capability: ExecutionCapability;
  kill_switch_state: KillSwitchState;
  live_price_freshness: DataFreshness;
  orders_count: (number | null) | Readonly<Array<number | null>>;
  requested_mode: ExecutionMode;
  research_data_freshness: DataFreshness;
  research_pipeline_health: "HEALTHY" | "STALE" | "NO_DATA" | "UNKNOWN";
  trades_count: (number | null) | Readonly<Array<number | null>>;
}>;
type SystemStatusEnvelope = Readonly<{
  data: SystemStatus;
  freshness?:
    | ((DataFreshness | null) | Readonly<Array<DataFreshness | null>>)
    | undefined;
  generated_at: string;
  schema_version: string;
  trace_id: string;
}>;

const HealthData: z.ZodType<HealthData> = z
  .object({ status: z.enum(["UP", "READY", "NOT_READY"]) })
  .strict()
  .readonly();
const FreshnessStatus = z.enum(["FRESH", "STALE", "NO_DATA", "UNKNOWN"]);
const DataFreshness: z.ZodType<DataFreshness> = z
  .object({
    age_seconds: z.union([z.number(), z.null()]),
    as_of: z.union([z.string(), z.null()]),
    stale_after_seconds: z.number().int().gte(1),
    status: FreshnessStatus,
  })
  .strict()
  .readonly();
const HealthEnvelope: z.ZodType<HealthEnvelope> = z
  .object({
    data: HealthData,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const CapabilityStatus = z.enum(["PASS", "FAIL", "STALE", "UNKNOWN"]);
const CapabilityEvidence: z.ZodType<CapabilityEvidence> = z
  .object({
    benchmark_run_id: z.union([z.string(), z.null()]),
    capability_id: z.string(),
    evidence_ref: z.union([z.string(), z.null()]),
    last_run_at: z.union([z.string(), z.null()]),
    metric: z.union([z.number(), z.null()]),
    name: z.string(),
    status: CapabilityStatus,
    threshold: z.union([z.number(), z.null()]),
    valid_until: z.union([z.string(), z.null()]),
  })
  .strict()
  .readonly();
const CapabilityListData: z.ZodType<CapabilityListData> = z
  .object({
    items: z.array(CapabilityEvidence).readonly(),
    summary: z.string(),
    total: z.number().int().gte(0),
    verified: z.number().int().gte(0),
  })
  .strict()
  .readonly();
const CapabilityListEnvelope: z.ZodType<CapabilityListEnvelope> = z
  .object({
    data: CapabilityListData,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const CostEvidenceQuality = z.enum(["EXACT", "ESTIMATED", "UNKNOWN"]);
const CostSession: z.ZodType<CostSession> = z
  .object({
    decisions: z.number().int().gte(0),
    estimated_cost: z.number().gte(0),
    llm_calls: z.number().int().gte(0),
    session: z.string(),
    steps: z.number().int().gte(0),
    symbols: z.array(z.string()).readonly(),
    tool_calls: z.number().int().gte(0),
  })
  .strict()
  .readonly();
const CostSummary: z.ZodType<CostSummary> = z
  .object({
    amount: z.union([z.number(), z.null()]).optional(),
    currency: z.string(),
    evidence_quality: CostEvidenceQuality,
    note: z.string(),
    sessions: z.array(CostSession).readonly(),
    total_llm_calls: z.union([z.number(), z.null()]).optional(),
    total_sessions: z.number().int().gte(0),
    total_tool_calls: z.union([z.number(), z.null()]).optional(),
  })
  .strict()
  .readonly();
const CostSummaryEnvelope: z.ZodType<CostSummaryEnvelope> = z
  .object({
    data: CostSummary,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const asset = z.union([z.string(), z.null()]).optional();
const DecisionAction = z.enum([
  "BUY",
  "SELL",
  "HOLD",
  "STRONG_BUY",
  "STRONG_SELL",
  "WAIT",
  "NO_SIGNAL",
  "WATCH_FOR_ENTRY",
]);
const action = z.union([DecisionAction, z.null()]).optional();
const DecisionSignals: z.ZodType<DecisionSignals> = z
  .object({
    calculated_at: z.union([z.string(), z.null()]),
    close: z.number(),
    macd_histogram: z.number(),
    macd_line: z.number(),
    macd_signal_line: z.number(),
    price_vs_sma200: z.string(),
    rsi_14: z.number(),
    signal: z.union([z.string(), z.null()]),
    sma_200: z.number(),
    symbol: z.string(),
    volume_24h: z.number(),
    volume_30d_avg: z.number(),
    volume_trend_ratio: z.number(),
  })
  .strict()
  .readonly();
const DecisionRecord: z.ZodType<DecisionRecord> = z
  .object({
    action: DecisionAction,
    asset: z.string(),
    confidence: z.number().gte(0).lte(1),
    decision_at: z.string().datetime({ offset: true }),
    decision_id: z.string(),
    price_at_decision: z.number(),
    reflected: z.boolean(),
    report_snippet: z.string().optional().default(""),
    signals: DecisionSignals,
  })
  .strict()
  .readonly();
const PaginatedResponse: z.ZodType<PaginatedResponse> = z
  .object({
    has_next: z.boolean(),
    items: z.array(DecisionRecord).readonly(),
    page: z.number().int().gte(1),
    page_size: z.number().int().gte(1).lte(200),
    total: z.number().int().gte(0),
  })
  .strict()
  .readonly();
const DecisionPageEnvelope: z.ZodType<DecisionPageEnvelope> = z
  .object({
    data: PaginatedResponse,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const ValidationError: z.ZodType<ValidationError> = z
  .object({
    ctx: z.object({}).partial().strict().passthrough().readonly().optional(),
    input: z.unknown().optional(),
    loc: z.array(z.union([z.string(), z.number()])).readonly(),
    msg: z.string(),
    type: z.string(),
  })
  .strict()
  .passthrough()
  .readonly();
const HTTPValidationError: z.ZodType<HTTPValidationError> = z
  .object({ detail: z.array(ValidationError).readonly() })
  .partial()
  .strict()
  .passthrough()
  .readonly();
const DecisionDetailEnvelope: z.ZodType<DecisionDetailEnvelope> = z
  .object({
    data: DecisionRecord,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const ConfidenceLevel = z.enum(["low", "medium", "high"]);
const RiskLevel = z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]);
const RiskAssessment: z.ZodType<RiskAssessment> = z
  .object({
    position_size_pct: z.number(),
    rationale: z.string(),
    risk_level: RiskLevel,
    stop_loss_pct: z.number(),
  })
  .strict()
  .readonly();
const MarketAssetSnapshot: z.ZodType<MarketAssetSnapshot> = z
  .object({
    alerts: z.array(z.string()).readonly(),
    articles_filtered: z.union([z.number(), z.null()]),
    articles_found: z.union([z.number(), z.null()]),
    articles_scored: z.union([z.number(), z.null()]),
    atr_14: z.number(),
    atr_pct: z.number(),
    confidence: ConfidenceLevel,
    current_price: z.number(),
    debate_context: z.string(),
    derivatives_note: z.union([z.string(), z.null()]),
    derivatives_signal: z.union([z.string(), z.null()]),
    funding_rate: z.union([z.number(), z.null()]),
    funding_rate_annualized: z.union([z.number(), z.null()]),
    funding_rate_pct: z.union([z.number(), z.null()]),
    funding_signal: z.union([z.string(), z.null()]),
    funding_source: z.union([z.string(), z.null()]),
    macd_signal: z.enum(["bullish_crossover", "bearish_crossover", "neutral"]),
    market_regime: z.union([z.string(), z.null()]),
    memory_context: z.string(),
    oi_change_pct: z.union([z.number(), z.null()]),
    oi_source: z.union([z.string(), z.null()]),
    oi_trend: z.union([z.string(), z.null()]),
    onchain_risk: z.union([z.string(), z.null()]),
    onchain_source: z.union([z.string(), z.null()]),
    open_interest_usd: z.union([z.number(), z.null()]),
    price_change_24h_pct: z.number(),
    price_change_7d_pct: z.number(),
    price_vs_sma200: z.enum(["above", "below"]),
    reasoning: z.string(),
    regime_adx: z.union([z.number(), z.null()]),
    regime_confidence: z.union([z.number(), z.null()]),
    regime_note: z.union([z.string(), z.null()]),
    risk_assessment: RiskAssessment,
    risk_context: z.string(),
    rsi_14: z.number(),
    rsi_signal: z.enum(["oversold", "overbought", "neutral"]),
    sentiment: z.union([z.string(), z.null()]),
    sentiment_distribution: z.union([z.record(z.number()), z.null()]),
    sentiment_score: z.union([z.number(), z.null()]),
    sentiment_source: z.union([z.string(), z.null()]),
    sentiment_summary: z.union([z.string(), z.null()]),
    signal_conflict: z.boolean(),
    stop_loss_suggestion: z.union([z.number(), z.null()]),
    stop_method: z.string(),
    stop_note: z.string(),
    suggestion: DecisionAction,
    symbol: z.string(),
    target_suggestion: z.union([z.number(), z.null()]),
    volume_trend: z.string(),
    warning: z.union([z.string(), z.null()]),
  })
  .strict()
  .readonly();
const MarketReport: z.ZodType<MarketReport> = z
  .object({
    as_of: z.string().datetime({ offset: true }),
    assets: z.array(MarketAssetSnapshot).readonly(),
    invalid_source_count: z.number().int().gte(0),
    report_id: z.string(),
    source_file: z.string(),
  })
  .strict()
  .readonly();
const MarketLatestData: z.ZodType<MarketLatestData> = z
  .object({ report: z.union([MarketReport, z.null()]) })
  .strict()
  .readonly();
const MarketLatestEnvelope: z.ZodType<MarketLatestEnvelope> = z
  .object({
    data: MarketLatestData,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const ExecutionMode = z.enum(["PAPER", "DRYRUN", "LIVE"]);
const ExecutionCapability = z.enum([
  "NON_LIVE",
  "LIVE_BLOCKED",
  "LIVE_AVAILABLE",
]);
const KillSwitchState = z.enum(["ACTIVE", "INACTIVE", "UNKNOWN"]);
const DeploymentMeta: z.ZodType<DeploymentMeta> = z
  .object({
    build_time: z.string(),
    data_root_identifier: z.string(),
    deployment_id: z.string(),
    effective_mode: ExecutionMode,
    execution_capability: ExecutionCapability,
    git_commit: z.string(),
    kill_switch_state: KillSwitchState,
    live_execution_enabled: z.boolean(),
    live_trading_approved: z.boolean(),
    repo_root_identifier: z.string(),
    requested_mode: ExecutionMode,
    schema_versions: z.array(z.string()).readonly(),
    service: z.string(),
  })
  .strict()
  .readonly();
const MetaEnvelope: z.ZodType<MetaEnvelope> = z
  .object({
    data: DeploymentMeta,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const Signal: z.ZodType<Signal> = z
  .object({
    as_of: z.string().datetime({ offset: true }),
    asset: MarketAssetSnapshot,
    recent_decisions: z.array(DecisionRecord).readonly(),
    source_report_id: z.string(),
  })
  .strict()
  .readonly();
const SignalListData: z.ZodType<SignalListData> = z
  .object({ items: z.array(Signal).readonly(), total: z.number().int().gte(0) })
  .strict()
  .readonly();
const SignalListEnvelope: z.ZodType<SignalListEnvelope> = z
  .object({
    data: SignalListData,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();
const SystemStatus: z.ZodType<SystemStatus> = z
  .object({
    api_liveness: z.string(),
    api_readiness: z.enum(["READY", "NOT_READY"]),
    backend_service_liveness: z.enum(["ALIVE", "STALE", "UNKNOWN"]),
    database_status: z.enum(["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]),
    effective_mode: ExecutionMode,
    execution_capability: ExecutionCapability,
    kill_switch_state: KillSwitchState,
    live_price_freshness: DataFreshness,
    orders_count: z.union([z.number(), z.null()]),
    requested_mode: ExecutionMode,
    research_data_freshness: DataFreshness,
    research_pipeline_health: z.enum([
      "HEALTHY",
      "STALE",
      "NO_DATA",
      "UNKNOWN",
    ]),
    trades_count: z.union([z.number(), z.null()]),
  })
  .strict()
  .readonly();
const SystemStatusEnvelope: z.ZodType<SystemStatusEnvelope> = z
  .object({
    data: SystemStatus,
    freshness: z.union([DataFreshness, z.null()]).optional(),
    generated_at: z.string().datetime({ offset: true }),
    schema_version: z.string(),
    trace_id: z.string(),
  })
  .strict()
  .readonly();

export const schemas = {
  HealthData,
  FreshnessStatus,
  DataFreshness,
  HealthEnvelope,
  CapabilityStatus,
  CapabilityEvidence,
  CapabilityListData,
  CapabilityListEnvelope,
  CostEvidenceQuality,
  CostSession,
  CostSummary,
  CostSummaryEnvelope,
  asset,
  DecisionAction,
  action,
  DecisionSignals,
  DecisionRecord,
  PaginatedResponse,
  DecisionPageEnvelope,
  ValidationError,
  HTTPValidationError,
  DecisionDetailEnvelope,
  ConfidenceLevel,
  RiskLevel,
  RiskAssessment,
  MarketAssetSnapshot,
  MarketReport,
  MarketLatestData,
  MarketLatestEnvelope,
  ExecutionMode,
  ExecutionCapability,
  KillSwitchState,
  DeploymentMeta,
  MetaEnvelope,
  Signal,
  SignalListData,
  SignalListEnvelope,
  SystemStatus,
  SystemStatusEnvelope,
};

const endpoints = makeApi([
  {
    method: "get",
    path: "/health/live",
    requestFormat: "json",
    response: HealthEnvelope,
  },
  {
    method: "get",
    path: "/health/ready",
    requestFormat: "json",
    response: HealthEnvelope,
  },
  {
    method: "get",
    path: "/v1/capabilities",
    requestFormat: "json",
    response: CapabilityListEnvelope,
  },
  {
    method: "get",
    path: "/v1/costs",
    requestFormat: "json",
    response: CostSummaryEnvelope,
  },
  {
    method: "get",
    path: "/v1/decisions",
    requestFormat: "json",
    parameters: [
      {
        name: "page",
        type: "Query",
        schema: z.number().int().gte(1).optional().default(1),
      },
      {
        name: "page_size",
        type: "Query",
        schema: z.number().int().gte(1).lte(200).optional().default(50),
      },
      {
        name: "asset",
        type: "Query",
        schema: asset,
      },
      {
        name: "action",
        type: "Query",
        schema: action,
      },
      {
        name: "date_from",
        type: "Query",
        schema: asset,
      },
      {
        name: "date_to",
        type: "Query",
        schema: asset,
      },
    ],
    response: DecisionPageEnvelope,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/v1/decisions/:decision_id",
    requestFormat: "json",
    parameters: [
      {
        name: "decision_id",
        type: "Path",
        schema: z.string(),
      },
    ],
    response: DecisionDetailEnvelope,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/v1/market/latest",
    requestFormat: "json",
    response: MarketLatestEnvelope,
  },
  {
    method: "get",
    path: "/v1/meta",
    requestFormat: "json",
    response: MetaEnvelope,
  },
  {
    method: "get",
    path: "/v1/signals",
    requestFormat: "json",
    parameters: [
      {
        name: "asset",
        type: "Query",
        schema: asset,
      },
    ],
    response: SignalListEnvelope,
    errors: [
      {
        status: 422,
        description: `Validation Error`,
        schema: HTTPValidationError,
      },
    ],
  },
  {
    method: "get",
    path: "/v1/system/status",
    requestFormat: "json",
    response: SystemStatusEnvelope,
  },
]);

export const api = new Zodios(endpoints);

export function createApiClient(baseUrl: string, options?: ZodiosOptions) {
  return new Zodios(baseUrl, endpoints, options);
}
