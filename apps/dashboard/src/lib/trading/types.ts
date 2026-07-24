export type AssetSymbol = string;

export type AssetClass = 'crypto' | 'stock' | 'etf' | 'forex';

export type SignalType =
  | 'BUY' | 'SELL' | 'HOLD' | 'STRONG BUY' | 'STRONG SELL'
  | 'WAIT' | 'NO SIGNAL' | 'WATCH FOR ENTRY';

export type ConfidenceLevel = 'low' | 'medium' | 'high';

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export type RSISignal = 'oversold' | 'overbought' | 'neutral';

export type MACDSignal = 'bullish_crossover' | 'bearish_crossover' | 'neutral';

export interface AssetData {
  symbol: AssetSymbol;
  asset_class: AssetClass;
  current_price: number;
  price_change_24h_pct: number;
  price_change_7d_pct: number;
  rsi_14: number;
  rsi_signal: RSISignal;
  macd_signal: MACDSignal;
  price_vs_sma200: 'above' | 'below';
  volume_trend: string;
  sentiment: string | null;
  sentiment_source: string | null;
  sentiment_score: number | null;
  sentiment_distribution: Record<string, number> | null;
  sentiment_summary: string | null;
  articles_found: number | null;
  articles_scored: number | null;
  articles_filtered: number | null;
  onchain_risk: string | null;
  onchain_source: string | null;
  funding_rate: number | null;
  funding_rate_pct: number | null;
  funding_rate_annualized: number | null;
  funding_signal: string | null;
  funding_source: string | null;
  open_interest_usd: number | null;
  oi_trend: string | null;
  oi_change_pct: number | null;
  oi_source: string | null;
  derivatives_signal: string | null;
  derivatives_note: string | null;
  suggestion: SignalType;
  confidence: ConfidenceLevel;
  signal_conflict: boolean;
  reasoning: string;
  stop_loss_suggestion: number | null;
  target_suggestion: number | null;
  atr_14: number;
  atr_pct: number;
  stop_method: string;
  stop_note: string;
  warning: string | null;
  alerts: string[];
  market_regime: string | null;
  macro_regime?: string | null;
  macro_regime_confidence?: number | null;
  regime_confidence: number | null;
  regime_adx: number | null;
  regime_note: string | null;
  risk_assessment: RiskAssessment;
  _memory_context: string;
  _debate_context: string;
  _risk_context: string;
}

export interface RiskAssessment {
  position_size_pct: number;
  stop_loss_pct: number;
  risk_level: RiskLevel;
  rationale: string;
}

export interface EquityAsset {
  symbol: string;
  asset_class: AssetClass;
  current_price: number;
  price_change_24h_pct: number;
  volume: number;
}

export interface CryptoAsset {
  symbol: string;
  asset_class: 'crypto';
  current_price: number;
  price_change_24h_pct: number;
  volume: number;
  market_cap?: number;
}

export interface MarketReport {
  timestamp: string;
  assets: AssetData[];
  source?: string;
}

export interface EquityReport {
  timestamp: string;
  source: string;
  assets: EquityAsset[];
}

export interface CryptoReport {
  timestamp: string;
  source: string;
  assets: CryptoAsset[];
}

export interface Decision {
  ticker: AssetSymbol;
  date: string;
  suggestion: SignalType;
  confidence: number;
  price_at_decision: number;
  signals: {
    symbol: AssetSymbol;
    close: number;
    rsi_14: number;
    macd_line: number;
    macd_signal_line: number;
    macd_histogram: number;
    sma_200: number;
    price_vs_sma200: string;
    volume_24h: number;
    volume_30d_avg: number;
    volume_trend_ratio: number;
    signal: string | null;
    calculated_at: string;
  };
  report_snippet: string;
  stored_at: string;
  reflected: boolean;
}

export interface ScratchpadEntry {
  type: 'init' | 'thinking' | 'tool_result' | 'decision' | 'error';
  timestamp: string;
  query?: string;
  symbols?: AssetSymbol[];
  mode?: string;
  session_id?: string;
  category?: string;
  content?: string;
  toolName?: string;
  args?: unknown;
  success?: boolean;
  duration_ms?: number;
  result?: unknown;
  llmSummary?: string;
}

export interface MarketTicker {
  symbol: AssetSymbol;
  asset_class?: AssetClass;
  price: number;
  change24h: number;
  volume24h: number | null;
  sector?: string;
}

export interface ResearchPlan {
  id: string;
  query: string;
  keywords: string[];
  steps: PlanStep[];
  created_at: string;
}

export interface PlanStep {
  id: string;
  title: string;
  description: string;
  dependencies: string[];
  status: 'pending' | 'in_progress' | 'completed';
}

export interface DebateSummary {
  symbol: AssetSymbol;
  bull_case: string;
  bear_case: string;
  synthesis: string;
}

export interface MultiPersonaRisk {
  symbol: AssetSymbol;
  aggressive: string;
  conservative: string;
  neutral: string;
  synthesis: string;
}

export interface RiskAssessmentEntry {
  persona: string;
  accept_signal: boolean;
  position_size_pct: number;
  rationale: string;
}

export interface TypedDecisionSignal {
  asset: string;
  action: string;
  confidence: number;
  entry_price: number | null;
  stop_loss: number | null;
  reasoning: string;
}

export interface TypedDecision {
  timestamp: string;
  asset: string;
  initial_signal: TypedDecisionSignal;
  bull_synthesis: string;
  bear_synthesis: string;
  risk_assessments: RiskAssessmentEntry[];
  final_action: string;
  final_position_size_pct: number;
  executive_summary: string;
}

// --- Fundamentals Phase 2 ---

export interface CompanyProfile {
  companyName: string;
  sector: string;
  industry: string;
  marketCap: number;
  beta: number;
}

export interface ValuationMetrics {
  peRatio: number | null;
  pbRatio: number | null;
  psRatio: number | null;
  evToEbitda: number | null;
  roe: number | null;
  debtToEquity: number | null;
  currentRatio: number | null;
}

export interface FinancialSnapshot {
  revenueGrowth: number | null;
  earningsGrowth: number | null;
  grossMargin: number | null;
  netMargin: number | null;
  latestQuarterRevenue: number | null;
  latestQuarterEarnings: number | null;
}

export interface EarningsInfo {
  nextDate: string | null;
  estimatedEps: number | null;
}

export interface FundamentalAsset {
  symbol: string;
  asset_class: string;
  profile: CompanyProfile;
  valuation: ValuationMetrics;
  financials: FinancialSnapshot;
  earnings: EarningsInfo;
}

export interface FundamentalsReport {
  timestamp: string;
  source: string;
  assets: FundamentalAsset[];
}

// --- Macro & News Phase 3 ---

export interface MacroIndicator {
  value: number;
  unit: string;
  period: string;
  trend: 'up' | 'down' | 'stable' | 'flat';
}

export interface MacroReport {
  timestamp: string;
  source: string;
  indicators: Record<string, MacroIndicator>;
  regime: string;
  regime_confidence: number;
  regime_rationale: string;
}

export interface NewsArticle {
  symbol: string;
  title: string;
  url: string;
  source: string;
  published_at: string;
  sentiment: string;
  sentiment_score: number;
  entities: string[];
}

export interface SentimentSummary {
  [symbol: string]: {
    positive: number;
    negative: number;
    neutral: number;
    avg_score: number;
  };
}

export interface NewsReport {
  timestamp: string;
  source: string;
  articles: NewsArticle[];
  sentiment_summary: SentimentSummary;
}

// --- Phase 4: TA Validation ---

export interface TaValidationEntry {
  symbol: string;
  indicator: string;
  ta_shim_value: number;
  alpha_vantage_value: number;
  divergence: number;
  divergence_pct: number;
  status: 'ok' | 'warn' | 'fail';
}

export interface TaValidationReport {
  timestamp: string;
  source: string;
  validations: TaValidationEntry[];
  summary: {
    total_checks: number;
    passed: number;
    failed: number;
    warned: number;
    avg_divergence: number;
  };
}

// --- Phase 4: Data Source Health ---

export interface DataSourceHealth {
  id: string;
  name: string;
  status: 'ok' | 'error' | 'unknown';
  latency_ms: number | null;
  rate_limit_remaining: number | null;
  last_check: string | null;
  error_message: string | null;
}
