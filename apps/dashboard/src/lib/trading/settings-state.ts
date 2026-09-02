export interface ExchangeConfiguration {
  configured: boolean;
}

export type ExchangeConfig = Record<string, ExchangeConfiguration>;

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseExchangeConfigPayload(value: unknown): ExchangeConfig | null {
  if (!(isObject(value)
    && Object.keys(value).length === 1
    && isObject(value.exchanges)
    && Object.keys(value.exchanges).length <= 100)) return null;

  const entries: Array<[string, ExchangeConfiguration]> = [];
  for (const [name, info] of Object.entries(value.exchanges)) {
    if (!/^[a-z0-9_-]{1,64}$/i.test(name)
      || !isObject(info)
      || Object.keys(info).length !== 1
      || typeof info.configured !== 'boolean') return null;
    entries.push([name, { configured: info.configured }]);
  }
  return Object.fromEntries(entries);
}

export function exchangeConfigurationPresentation(
  configuration: ExchangeConfiguration,
): { label: 'configured' | 'not configured'; configured: boolean } {
  return configuration.configured
    ? { label: 'configured', configured: true }
    : { label: 'not configured', configured: false };
}

export type SettingsAvailability = 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE';

export interface SettingsOperatorStatus {
  availability: SettingsAvailability;
  mode: 'PAPER' | 'DRYRUN' | 'LIVE' | 'UNKNOWN';
  liveExecutionEnabled: 'ENABLED' | 'DISABLED' | 'UNKNOWN';
  liveTradingApproved: 'APPROVED' | 'NOT_APPROVED' | 'UNKNOWN';
}

export function settingsOperatorStatus({
  availability,
  mode,
  liveExecutionEnabled,
  liveTradingApproved,
}: {
  availability: SettingsAvailability;
  mode: 'PAPER' | 'DRYRUN' | 'LIVE' | 'UNKNOWN';
  liveExecutionEnabled: boolean | null;
  liveTradingApproved: boolean | null;
}): SettingsOperatorStatus {
  if (availability !== 'AVAILABLE'
    || mode === 'UNKNOWN'
    || liveExecutionEnabled === null
    || liveTradingApproved === null) {
    return {
      availability: availability === 'LOADING' ? 'LOADING' : 'UNAVAILABLE',
      mode: 'UNKNOWN',
      liveExecutionEnabled: 'UNKNOWN',
      liveTradingApproved: 'UNKNOWN',
    };
  }
  return {
    availability: 'AVAILABLE',
    mode,
    liveExecutionEnabled: liveExecutionEnabled ? 'ENABLED' : 'DISABLED',
    liveTradingApproved: liveTradingApproved ? 'APPROVED' : 'NOT_APPROVED',
  };
}

export interface TradingAgent {
  id: string;
  name: string;
  role: string;
  icon: string;
  model: string;
  stage: 'analyst' | 'debate' | 'risk' | 'execution';
  consumes: string[];
  produces: string;
  sourceFile: string;
}

export interface AgentsState {
  availability: SettingsAvailability;
  data: TradingAgent[] | null;
}

export interface AgentsSummary {
  availability: SettingsAvailability;
  agents: TradingAgent[] | null;
  count: number | null;
}

export const INITIAL_AGENTS_STATE: Readonly<AgentsState> = Object.freeze({
  availability: 'LOADING',
  data: null,
});

export const UNAVAILABLE_AGENTS_STATE: Readonly<{
  availability: 'UNAVAILABLE';
  data: null;
}> = Object.freeze({
  availability: 'UNAVAILABLE',
  data: null,
});

export interface CostSummary {
  totalSessions: number;
  totalLLMCalls: number | null;
  totalToolCalls: number | null;
  estimatedCost: number | null;
  optimizerTokensSaved: number | null;
  optimizerCostSaved: number | null;
  evidenceQuality: 'EXACT' | 'ESTIMATED' | 'UNKNOWN';
  note: string;
}

export interface CostSession {
  session: string;
  symbols: string[];
  steps: number;
  llmCalls: number;
  toolCalls: number;
  decisions: number;
  duration: number | null;
  estimatedCost: number;
}

export interface CostsData {
  summary: CostSummary;
  sessions: CostSession[];
  costModel: null;
  efficiency: {
    avgLLMCallsPerSession: number | null;
    avgCostPerSession: number | null;
    avgToolCallsPerLLM: number | null;
  };
}

export interface CostsState {
  availability: SettingsAvailability;
  data: CostsData | null;
}

export const INITIAL_COSTS_STATE: Readonly<CostsState> = Object.freeze({
  availability: 'LOADING',
  data: null,
});

export const UNAVAILABLE_COSTS_STATE: Readonly<{
  availability: 'UNAVAILABLE';
  data: null;
}> = Object.freeze({
  availability: 'UNAVAILABLE',
  data: null,
});

export type SettingsFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const AGENT_KEYS = [
  'id', 'name', 'role', 'icon', 'model', 'stage', 'consumes', 'produces', 'sourceFile',
] as const;
const COST_ROOT_KEYS = ['summary', 'sessions', 'costModel', 'efficiency'] as const;
const COST_SUMMARY_KEYS = [
  'totalSessions', 'totalLLMCalls', 'totalToolCalls', 'estimatedCost',
  'optimizerTokensSaved', 'optimizerCostSaved', 'evidenceQuality', 'note',
] as const;
const COST_SESSION_KEYS = [
  'session', 'symbols', 'steps', 'llmCalls', 'toolCalls', 'decisions', 'duration',
  'estimatedCost',
] as const;
const COST_EFFICIENCY_KEYS = [
  'avgLLMCallsPerSession', 'avgCostPerSession', 'avgToolCallsPerLLM',
] as const;
const AGENT_STAGES = new Set(['analyst', 'debate', 'risk', 'execution']);
const EVIDENCE_QUALITIES = new Set(['EXACT', 'ESTIMATED', 'UNKNOWN']);

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function boundedString(value: unknown, maxLength = 512): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function nonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function nonNegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function nullableNonNegativeInteger(value: unknown): value is number | null {
  return value === null || nonNegativeInteger(value);
}

function nullableNonNegativeNumber(value: unknown): value is number | null {
  return value === null || nonNegativeNumber(value);
}

function parseAgent(value: unknown): TradingAgent | null {
  if (!(isObject(value)
    && hasExactKeys(value, AGENT_KEYS)
    && boundedString(value.id, 128)
    && boundedString(value.name, 256)
    && boundedString(value.role, 1_024)
    && boundedString(value.icon, 128)
    && boundedString(value.model, 256)
    && typeof value.stage === 'string' && AGENT_STAGES.has(value.stage)
    && Array.isArray(value.consumes) && value.consumes.length <= 100
    && value.consumes.every((item) => boundedString(item, 512))
    && boundedString(value.produces, 1_024)
    && boundedString(value.sourceFile, 512))) return null;
  return value as unknown as TradingAgent;
}

export function parseAgentsPayload(value: unknown): TradingAgent[] | null {
  if (!Array.isArray(value) || value.length > 100) return null;
  const agents = value.map(parseAgent);
  if (agents.some((agent) => agent === null)) return null;
  const parsed = agents as TradingAgent[];
  return new Set(parsed.map((agent) => agent.id)).size === parsed.length ? parsed : null;
}

export function summarizeAgentsState(state: Readonly<AgentsState>): AgentsSummary {
  if (state.availability !== 'AVAILABLE' || state.data === null) {
    return {
      availability: state.availability === 'AVAILABLE' ? 'UNAVAILABLE' : state.availability,
      agents: null,
      count: null,
    };
  }
  return { availability: 'AVAILABLE', agents: state.data, count: state.data.length };
}

function parseCostsPayload(value: unknown): CostsData | null {
  if (!(isObject(value)
    && hasExactKeys(value, COST_ROOT_KEYS)
    && isObject(value.summary)
    && hasExactKeys(value.summary, COST_SUMMARY_KEYS)
    && nonNegativeInteger(value.summary.totalSessions)
    && nullableNonNegativeInteger(value.summary.totalLLMCalls)
    && nullableNonNegativeInteger(value.summary.totalToolCalls)
    && nullableNonNegativeNumber(value.summary.estimatedCost)
    && nullableNonNegativeInteger(value.summary.optimizerTokensSaved)
    && nullableNonNegativeNumber(value.summary.optimizerCostSaved)
    && typeof value.summary.evidenceQuality === 'string'
    && EVIDENCE_QUALITIES.has(value.summary.evidenceQuality)
    && boundedString(value.summary.note, 2_048)
    && Array.isArray(value.sessions) && value.sessions.length <= 1_000
    && value.costModel === null
    && isObject(value.efficiency)
    && hasExactKeys(value.efficiency, COST_EFFICIENCY_KEYS)
    && nullableNonNegativeNumber(value.efficiency.avgLLMCallsPerSession)
    && nullableNonNegativeNumber(value.efficiency.avgCostPerSession)
    && nullableNonNegativeNumber(value.efficiency.avgToolCallsPerLLM))) return null;

  const sessions = value.sessions.map((session): CostSession | null => {
    if (!(isObject(session)
      && hasExactKeys(session, COST_SESSION_KEYS)
      && boundedString(session.session, 512)
      && Array.isArray(session.symbols) && session.symbols.length <= 100
      && session.symbols.every((symbol) => boundedString(symbol, 32))
      && nonNegativeInteger(session.steps)
      && nonNegativeInteger(session.llmCalls)
      && nonNegativeInteger(session.toolCalls)
      && nonNegativeInteger(session.decisions)
      && nullableNonNegativeNumber(session.duration)
      && nonNegativeNumber(session.estimatedCost))) return null;
    return session as unknown as CostSession;
  });
  if (sessions.some((session) => session === null)) return null;
  return { ...value, sessions } as unknown as CostsData;
}

async function loadSettingsState<T>(
  fetcher: SettingsFetch,
  path: string,
  parse: (value: unknown) => T | null,
  unavailable: Readonly<{ availability: 'UNAVAILABLE'; data: null }>,
  { timeoutMs = 5_000, signal }: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Readonly<{ availability: 'AVAILABLE'; data: T } | typeof unavailable>> {
  if (signal?.aborted) return unavailable;
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(path, {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) return unavailable;
    const data = parse(await response.json());
    return data === null ? unavailable : { availability: 'AVAILABLE', data };
  } catch {
    return unavailable;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}

export function loadAgentsState(
  fetcher: SettingsFetch = fetch,
  options?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<Readonly<AgentsState>> {
  return loadSettingsState(
    fetcher,
    '/api/trading/agents',
    parseAgentsPayload,
    UNAVAILABLE_AGENTS_STATE,
    options,
  );
}

export function loadCostsState(
  fetcher: SettingsFetch = fetch,
  options?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<Readonly<CostsState>> {
  return loadSettingsState(
    fetcher,
    '/api/trading/costs',
    parseCostsPayload,
    UNAVAILABLE_COSTS_STATE,
    options,
  );
}
