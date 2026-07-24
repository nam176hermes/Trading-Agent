import 'server-only';

const CONTROL_API_ORIGIN = 'http://127.0.0.1:8400';
const CONTROL_API_SCHEMA_VERSION = '2.0.0';
const CONTROL_API_TIMEOUT_MS = 5_000;
const MAX_UPSTREAM_BODY_BYTES = 512 * 1024;

type JsonObject = Record<string, unknown>;
type Parser<T> = (value: unknown) => T | null;

const FRESHNESS = new Set(['FRESH', 'STALE', 'NO_DATA', 'UNKNOWN']);
const EXECUTION_MODES = new Set(['PAPER', 'DRYRUN', 'LIVE']);
const EXECUTION_CAPABILITIES = new Set(['NON_LIVE', 'LIVE_BLOCKED', 'LIVE_AVAILABLE']);
const KILL_SWITCH_STATES = new Set(['ACTIVE', 'INACTIVE', 'UNKNOWN']);
const DECISION_ACTIONS = new Set([
  'BUY', 'SELL', 'HOLD', 'STRONG_BUY', 'STRONG_SELL', 'WAIT', 'NO_SIGNAL',
  'WATCH_FOR_ENTRY',
]);

export interface ControlFreshness {
  status: 'FRESH' | 'STALE' | 'NO_DATA' | 'UNKNOWN';
  as_of: string | null;
  age_seconds: number | null;
  stale_after_seconds: number;
}

export interface ControlRiskAssessment {
  position_size_pct: number;
  stop_loss_pct: number;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  rationale: string;
}

export interface ControlMarketAsset {
  symbol: string;
  current_price: number;
  price_change_24h_pct: number;
  price_change_7d_pct: number;
  rsi_14: number;
  rsi_signal: 'oversold' | 'overbought' | 'neutral';
  macd_signal: 'bullish_crossover' | 'bearish_crossover' | 'neutral';
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
  suggestion: string;
  confidence: 'low' | 'medium' | 'high';
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
  regime_confidence: number | null;
  regime_adx: number | null;
  regime_note: string | null;
  risk_assessment: ControlRiskAssessment;
  memory_context: string;
  debate_context: string;
  risk_context: string;
}

export interface ControlMarketReport {
  report_id: string;
  as_of: string;
  assets: ControlMarketAsset[];
  source_file: string;
  invalid_source_count: number;
}

export interface ControlDecision {
  decision_id: string;
  asset: string;
  action: string;
  confidence: number;
  decision_at: string;
  price_at_decision: number;
  reflected: boolean;
  signals: {
    symbol: string;
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
    calculated_at: string | null;
  };
  report_snippet: string;
}

export interface ControlDecisionPage {
  items: ControlDecision[];
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
}

export interface ControlSystemStatus {
  api_liveness: 'UP';
  api_readiness: 'READY' | 'NOT_READY';
  backend_service_liveness: 'ALIVE' | 'STALE' | 'UNKNOWN';
  research_pipeline_health: 'HEALTHY' | 'STALE' | 'NO_DATA' | 'UNKNOWN';
  research_data_freshness: ControlFreshness;
  live_price_freshness: ControlFreshness;
  database_status: 'AVAILABLE' | 'UNAVAILABLE' | 'UNKNOWN';
  requested_mode: 'PAPER' | 'DRYRUN' | 'LIVE';
  effective_mode: 'PAPER' | 'DRYRUN' | 'LIVE';
  execution_capability: 'NON_LIVE' | 'LIVE_BLOCKED' | 'LIVE_AVAILABLE';
  kill_switch_state: 'ACTIVE' | 'INACTIVE' | 'UNKNOWN';
  orders_count: number | null;
  trades_count: number | null;
}

export interface ControlCapability {
  capability_id: string;
  name: string;
  status: 'PASS' | 'FAIL' | 'STALE' | 'UNKNOWN';
  last_run_at: string | null;
  valid_until: string | null;
  benchmark_run_id: string | null;
  metric: number | null;
  threshold: number | null;
  evidence_ref: string | null;
}

export interface ControlCapabilities {
  items: ControlCapability[];
  total: number;
  verified: number;
  summary: string;
}

export interface ControlCostSummary {
  evidence_quality: 'EXACT' | 'ESTIMATED' | 'UNKNOWN';
  currency: 'USD';
  total_sessions: number;
  total_llm_calls: number | null;
  total_tool_calls: number | null;
  amount: number | null;
  sessions: Array<{
    session: string;
    symbols: string[];
    steps: number;
    llm_calls: number;
    tool_calls: number;
    decisions: number;
    estimated_cost: number;
  }>;
  note: string;
}

export interface ControlDeploymentMeta {
  service: string;
  git_commit: string;
  build_time: string;
  deployment_id: string;
  repo_root_identifier: string;
  data_root_identifier: string;
  schema_versions: string[];
  requested_mode: 'PAPER' | 'DRYRUN' | 'LIVE';
  effective_mode: 'PAPER' | 'DRYRUN' | 'LIVE';
  execution_capability: 'NON_LIVE' | 'LIVE_BLOCKED' | 'LIVE_AVAILABLE';
  live_execution_enabled: boolean;
  live_trading_approved: boolean;
  kill_switch_state: 'ACTIVE' | 'INACTIVE' | 'UNKNOWN';
}

export interface ControlEnvelope<T> {
  schema_version: '2.0.0';
  trace_id: string;
  generated_at: string;
  data: T;
  freshness: ControlFreshness | null;
}

export class ControlApiClientError extends Error {
  constructor() {
    super('Control API is unavailable.');
    this.name = 'ControlApiClientError';
  }
}

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function boundedString(value: unknown, max = 16_384): value is string {
  return typeof value === 'string' && value.length <= max;
}

function nonEmptyString(value: unknown, max = 512): value is string {
  return boundedString(value, max) && value.length > 0;
}

function isoDate(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 35
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function nullableString(value: unknown, max = 16_384): value is string | null {
  return value === null || boundedString(value, max);
}

function nullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value));
}

function nonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function parseFreshness(value: unknown): ControlFreshness | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['status', 'as_of', 'age_seconds', 'stale_after_seconds'])
    && typeof value.status === 'string' && FRESHNESS.has(value.status)
    && (value.as_of === null || isoDate(value.as_of))
    && (value.age_seconds === null || nonNegativeInteger(value.age_seconds))
    && nonNegativeInteger(value.stale_after_seconds) && value.stale_after_seconds > 0)) return null;
  return value as unknown as ControlFreshness;
}

function parseRisk(value: unknown): ControlRiskAssessment | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['position_size_pct', 'stop_loss_pct', 'risk_level', 'rationale'])
    && typeof value.position_size_pct === 'number' && Number.isFinite(value.position_size_pct)
    && typeof value.stop_loss_pct === 'number' && Number.isFinite(value.stop_loss_pct)
    && typeof value.risk_level === 'string' && ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].includes(value.risk_level)
    && boundedString(value.rationale))) return null;
  return value as unknown as ControlRiskAssessment;
}

const MARKET_ASSET_KEYS = [
  'symbol', 'current_price', 'price_change_24h_pct', 'price_change_7d_pct', 'rsi_14',
  'rsi_signal', 'macd_signal', 'price_vs_sma200', 'volume_trend', 'sentiment',
  'sentiment_source', 'sentiment_score', 'sentiment_distribution', 'sentiment_summary',
  'articles_found', 'articles_scored', 'articles_filtered', 'onchain_risk', 'onchain_source',
  'funding_rate', 'funding_rate_pct', 'funding_rate_annualized', 'funding_signal',
  'funding_source', 'open_interest_usd', 'oi_trend', 'oi_change_pct', 'oi_source',
  'derivatives_signal', 'derivatives_note', 'suggestion', 'confidence', 'signal_conflict',
  'reasoning', 'stop_loss_suggestion', 'target_suggestion', 'atr_14', 'atr_pct',
  'stop_method', 'stop_note', 'warning', 'alerts', 'market_regime', 'regime_confidence',
  'regime_adx', 'regime_note', 'risk_assessment', 'memory_context', 'debate_context',
  'risk_context',
] as const;

function parseMarketAsset(value: unknown): ControlMarketAsset | null {
  if (!isObject(value) || !hasExactKeys(value, MARKET_ASSET_KEYS)) return null;
  const requiredNumbers = [
    'current_price', 'price_change_24h_pct', 'price_change_7d_pct', 'rsi_14', 'atr_14', 'atr_pct',
  ] as const;
  const nullableNumbers = [
    'sentiment_score', 'articles_found', 'articles_scored', 'articles_filtered', 'funding_rate',
    'funding_rate_pct', 'funding_rate_annualized', 'open_interest_usd', 'oi_change_pct',
    'stop_loss_suggestion', 'target_suggestion', 'regime_confidence', 'regime_adx',
  ] as const;
  const nullableStrings = [
    'sentiment', 'sentiment_source', 'sentiment_summary', 'onchain_risk', 'onchain_source',
    'funding_signal', 'funding_source', 'oi_trend', 'oi_source', 'derivatives_signal',
    'derivatives_note', 'warning', 'market_regime', 'regime_note',
  ] as const;
  if (!(nonEmptyString(value.symbol, 32)
    && requiredNumbers.every((key) => typeof value[key] === 'number' && Number.isFinite(value[key]))
    && nullableNumbers.every((key) => nullableNumber(value[key]))
    && nullableStrings.every((key) => nullableString(value[key]))
    && ['oversold', 'overbought', 'neutral'].includes(String(value.rsi_signal))
    && ['bullish_crossover', 'bearish_crossover', 'neutral'].includes(String(value.macd_signal))
    && ['above', 'below'].includes(String(value.price_vs_sma200))
    && ['low', 'medium', 'high'].includes(String(value.confidence))
    && typeof value.suggestion === 'string' && DECISION_ACTIONS.has(value.suggestion)
    && typeof value.signal_conflict === 'boolean'
    && boundedString(value.volume_trend) && boundedString(value.reasoning)
    && boundedString(value.stop_method) && boundedString(value.stop_note)
    && boundedString(value.memory_context) && boundedString(value.debate_context)
    && boundedString(value.risk_context)
    && Array.isArray(value.alerts) && value.alerts.length <= 100
    && value.alerts.every((item) => boundedString(item, 1_024)))) return null;
  if (value.sentiment_distribution !== null) {
    if (!isObject(value.sentiment_distribution)
      || Object.keys(value.sentiment_distribution).length > 32
      || Object.values(value.sentiment_distribution).some((item) => typeof item !== 'number' || !Number.isFinite(item))) return null;
  }
  if (!parseRisk(value.risk_assessment)) return null;
  return value as unknown as ControlMarketAsset;
}

function parseMarket(value: unknown): { report: ControlMarketReport | null } | null {
  if (!isObject(value) || !hasExactKeys(value, ['report'])) return null;
  if (value.report === null) return { report: null };
  const report = value.report;
  if (!(isObject(report)
    && hasExactKeys(report, ['report_id', 'as_of', 'assets', 'source_file', 'invalid_source_count'])
    && nonEmptyString(report.report_id) && isoDate(report.as_of)
    && nonEmptyString(report.source_file, 512) && nonNegativeInteger(report.invalid_source_count)
    && Array.isArray(report.assets) && report.assets.length <= 1_000)) return null;
  const assets = report.assets.map(parseMarketAsset);
  if (assets.some((asset) => asset === null)) return null;
  return { report: { ...report, assets } as ControlMarketReport };
}

function parseDecisionSignals(value: unknown): ControlDecision['signals'] | null {
  if (!(isObject(value)
    && hasExactKeys(value, [
      'symbol', 'close', 'rsi_14', 'macd_line', 'macd_signal_line', 'macd_histogram',
      'sma_200', 'price_vs_sma200', 'volume_24h', 'volume_30d_avg',
      'volume_trend_ratio', 'signal', 'calculated_at',
    ])
    && nonEmptyString(value.symbol, 32) && boundedString(value.price_vs_sma200, 64)
    && ['close', 'rsi_14', 'macd_line', 'macd_signal_line', 'macd_histogram', 'sma_200',
      'volume_24h', 'volume_30d_avg', 'volume_trend_ratio']
      .every((key) => typeof value[key] === 'number' && Number.isFinite(value[key]))
    && nullableString(value.signal, 256)
    && (value.calculated_at === null || isoDate(value.calculated_at)))) return null;
  return value as unknown as ControlDecision['signals'];
}

function parseDecision(value: unknown): ControlDecision | null {
  if (!(isObject(value)
    && hasExactKeys(value, [
      'decision_id', 'asset', 'action', 'confidence', 'decision_at', 'price_at_decision',
      'reflected', 'signals', 'report_snippet',
    ])
    && nonEmptyString(value.decision_id) && nonEmptyString(value.asset, 32)
    && typeof value.action === 'string' && DECISION_ACTIONS.has(value.action)
    && typeof value.confidence === 'number' && Number.isFinite(value.confidence)
    && value.confidence >= 0 && value.confidence <= 1 && isoDate(value.decision_at)
    && typeof value.price_at_decision === 'number' && Number.isFinite(value.price_at_decision)
    && typeof value.reflected === 'boolean' && boundedString(value.report_snippet))) return null;
  const signals = parseDecisionSignals(value.signals);
  return signals ? { ...value, signals } as unknown as ControlDecision : null;
}

const parseDecisionPage: Parser<ControlDecisionPage> = (value) => {
  if (!(isObject(value)
    && hasExactKeys(value, ['items', 'page', 'page_size', 'total', 'has_next'])
    && Array.isArray(value.items) && value.items.length <= 200
    && Number.isSafeInteger(value.page) && (value.page as number) >= 1
    && Number.isSafeInteger(value.page_size) && (value.page_size as number) >= 1 && (value.page_size as number) <= 200
    && nonNegativeInteger(value.total) && typeof value.has_next === 'boolean')) return null;
  const items = value.items.map(parseDecision);
  return items.some((item) => item === null) ? null : { ...value, items } as ControlDecisionPage;
};

const parseSystemStatus: Parser<ControlSystemStatus> = (value) => {
  if (!(isObject(value)
    && hasExactKeys(value, [
      'api_liveness', 'api_readiness', 'backend_service_liveness', 'research_pipeline_health',
      'research_data_freshness', 'live_price_freshness', 'database_status', 'requested_mode',
      'effective_mode', 'execution_capability', 'kill_switch_state', 'orders_count', 'trades_count',
    ])
    && value.api_liveness === 'UP' && ['READY', 'NOT_READY'].includes(String(value.api_readiness))
    && ['ALIVE', 'STALE', 'UNKNOWN'].includes(String(value.backend_service_liveness))
    && ['HEALTHY', 'STALE', 'NO_DATA', 'UNKNOWN'].includes(String(value.research_pipeline_health))
    && ['AVAILABLE', 'UNAVAILABLE', 'UNKNOWN'].includes(String(value.database_status))
    && typeof value.requested_mode === 'string' && EXECUTION_MODES.has(value.requested_mode)
    && typeof value.effective_mode === 'string' && EXECUTION_MODES.has(value.effective_mode)
    && typeof value.execution_capability === 'string' && EXECUTION_CAPABILITIES.has(value.execution_capability)
    && typeof value.kill_switch_state === 'string' && KILL_SWITCH_STATES.has(value.kill_switch_state)
    && (value.orders_count === null || nonNegativeInteger(value.orders_count))
    && (value.trades_count === null || nonNegativeInteger(value.trades_count))
    && parseFreshness(value.research_data_freshness) && parseFreshness(value.live_price_freshness))) return null;
  return value as unknown as ControlSystemStatus;
};

const parseCapabilities: Parser<ControlCapabilities> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['items', 'total', 'verified', 'summary'])
    && Array.isArray(value.items) && value.items.length <= 1_000
    && nonNegativeInteger(value.total) && nonNegativeInteger(value.verified)
    && boundedString(value.summary))) return null;
  const items = value.items.map((item): ControlCapability | null => {
    if (!(isObject(item)
      && hasExactKeys(item, [
        'capability_id', 'name', 'status', 'last_run_at', 'valid_until', 'benchmark_run_id',
        'metric', 'threshold', 'evidence_ref',
      ])
      && nonEmptyString(item.capability_id) && nonEmptyString(item.name)
      && typeof item.status === 'string' && ['PASS', 'FAIL', 'STALE', 'UNKNOWN'].includes(item.status)
      && (item.last_run_at === null || isoDate(item.last_run_at))
      && (item.valid_until === null || isoDate(item.valid_until))
      && nullableString(item.benchmark_run_id, 512) && nullableNumber(item.metric)
      && nullableNumber(item.threshold) && nullableString(item.evidence_ref))) return null;
    return item as unknown as ControlCapability;
  });
  return items.some((item) => item === null) ? null : { ...value, items } as ControlCapabilities;
};

const parseCosts: Parser<ControlCostSummary> = (value) => {
  if (!(isObject(value)
    && hasExactKeys(value, [
      'evidence_quality', 'currency', 'total_sessions', 'total_llm_calls', 'total_tool_calls',
      'amount', 'sessions', 'note',
    ])
    && ['EXACT', 'ESTIMATED', 'UNKNOWN'].includes(String(value.evidence_quality))
    && value.currency === 'USD' && nonNegativeInteger(value.total_sessions)
    && (value.total_llm_calls === null || nonNegativeInteger(value.total_llm_calls))
    && (value.total_tool_calls === null || nonNegativeInteger(value.total_tool_calls))
    && nullableNumber(value.amount) && Array.isArray(value.sessions) && value.sessions.length <= 1_000
    && boundedString(value.note))) return null;
  const sessions = value.sessions.map((item) => {
    if (!(isObject(item)
      && hasExactKeys(item, ['session', 'symbols', 'steps', 'llm_calls', 'tool_calls', 'decisions', 'estimated_cost'])
      && nonEmptyString(item.session) && Array.isArray(item.symbols) && item.symbols.length <= 100
      && item.symbols.every((symbol) => nonEmptyString(symbol, 32))
      && ['steps', 'llm_calls', 'tool_calls', 'decisions'].every((key) => nonNegativeInteger(item[key]))
      && typeof item.estimated_cost === 'number' && Number.isFinite(item.estimated_cost)
      && item.estimated_cost >= 0)) return null;
    return item;
  });
  return sessions.some((session) => session === null) ? null : { ...value, sessions } as unknown as ControlCostSummary;
};

const parseMeta: Parser<ControlDeploymentMeta> = (value) => {
  if (!(isObject(value)
    && hasExactKeys(value, [
      'service', 'git_commit', 'build_time', 'deployment_id', 'repo_root_identifier',
      'data_root_identifier', 'schema_versions', 'requested_mode', 'effective_mode',
      'execution_capability', 'live_execution_enabled', 'live_trading_approved', 'kill_switch_state',
    ])
    && ['service', 'git_commit', 'build_time', 'deployment_id', 'repo_root_identifier', 'data_root_identifier']
      .every((key) => nonEmptyString(value[key], 512))
    && Array.isArray(value.schema_versions) && value.schema_versions.length <= 32
    && value.schema_versions.every((item) => nonEmptyString(item, 64))
    && typeof value.requested_mode === 'string' && EXECUTION_MODES.has(value.requested_mode)
    && typeof value.effective_mode === 'string' && EXECUTION_MODES.has(value.effective_mode)
    && typeof value.execution_capability === 'string' && EXECUTION_CAPABILITIES.has(value.execution_capability)
    && typeof value.live_execution_enabled === 'boolean' && typeof value.live_trading_approved === 'boolean'
    && typeof value.kill_switch_state === 'string' && KILL_SWITCH_STATES.has(value.kill_switch_state))) return null;
  return value as unknown as ControlDeploymentMeta;
};

function parseEnvelope<T>(value: unknown, parser: Parser<T>): ControlEnvelope<T> | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'data', 'freshness'])
    && value.schema_version === CONTROL_API_SCHEMA_VERSION
    && typeof value.trace_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value.trace_id)
    && isoDate(value.generated_at))) return null;
  const data = parser(value.data);
  const freshness = value.freshness === null ? null : parseFreshness(value.freshness);
  return data === null || (value.freshness !== null && freshness === null) ? null : {
    schema_version: CONTROL_API_SCHEMA_VERSION,
    trace_id: value.trace_id,
    generated_at: value.generated_at,
    data,
    freshness,
  };
}

async function readBoundedBody(response: Response): Promise<string | null> {
  const declared = response.headers.get('content-length');
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_UPSTREAM_BODY_BYTES)) {
    try { await response.body?.cancel(); } catch { /* Best-effort cancellation. */ }
    return null;
  }
  if (!response.body) return '';
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_UPSTREAM_BODY_BYTES) {
        try { await reader.cancel(); } catch { /* Best-effort cancellation. */ }
        return null;
      }
      chunks.push(value);
    }
  } catch {
    try { await reader.cancel(); } catch { /* Best-effort cancellation. */ }
    return null;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { return null; }
}

async function requestControlApi<T>(pathname: string, parser: Parser<T>): Promise<ControlEnvelope<T>> {
  let url: URL;
  try { url = new URL(pathname, CONTROL_API_ORIGIN); } catch { throw new ControlApiClientError(); }
  if (url.origin !== CONTROL_API_ORIGIN || !url.pathname.startsWith('/v1/')) throw new ControlApiClientError();
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: { accept: 'application/json' },
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(CONTROL_API_TIMEOUT_MS),
    });
    const body = await readBoundedBody(response);
    if (!response.ok || body === null) throw new ControlApiClientError();
    let value: unknown;
    try { value = JSON.parse(body); } catch { throw new ControlApiClientError(); }
    const envelope = parseEnvelope(value, parser);
    if (!envelope) throw new ControlApiClientError();
    return envelope;
  } catch (error) {
    if (error instanceof ControlApiClientError) throw error;
    throw new ControlApiClientError();
  }
}

export function getControlMarket(): Promise<ControlEnvelope<{ report: ControlMarketReport | null }>> {
  return requestControlApi('/v1/market/latest', parseMarket);
}

export function getControlDecisions(query = ''): Promise<ControlEnvelope<ControlDecisionPage>> {
  const input = new URLSearchParams(query);
  const filtered = new URLSearchParams();
  const allowed = new Set(['page', 'page_size', 'asset', 'action', 'date_from', 'date_to']);
  for (const [key, value] of input) if (allowed.has(key)) filtered.append(key, value);
  return requestControlApi(`/v1/decisions${filtered.size ? `?${filtered}` : ''}`, parseDecisionPage);
}

export function getControlStatus(): Promise<ControlEnvelope<ControlSystemStatus>> {
  return requestControlApi('/v1/system/status', parseSystemStatus);
}

export function getControlCapabilities(): Promise<ControlEnvelope<ControlCapabilities>> {
  return requestControlApi('/v1/capabilities', parseCapabilities);
}

export function getControlCosts(): Promise<ControlEnvelope<ControlCostSummary>> {
  return requestControlApi('/v1/costs', parseCosts);
}

export function getControlMeta(): Promise<ControlEnvelope<ControlDeploymentMeta>> {
  return requestControlApi('/v1/meta', parseMeta);
}

export function controlApiUnavailableResponse(): Response {
  return Response.json(
    { ok: false, code: 'CONTROL_API_UNAVAILABLE', message: 'Canonical trading data is unavailable.' },
    { status: 503, headers: { 'cache-control': 'no-store' } },
  );
}
