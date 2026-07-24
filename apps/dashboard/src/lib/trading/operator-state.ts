export type OperatorAvailability = 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE';
export type OperatorHealth = 'UNKNOWN';
export type OperatorMode = 'PAPER' | 'DRYRUN' | 'LIVE' | 'UNKNOWN';
export type OperatorExecutionCapability =
  | 'NON_LIVE'
  | 'LIVE_BLOCKED'
  | 'LIVE_AVAILABLE'
  | 'UNKNOWN';
export type OperatorKillSwitchState = 'ACTIVE' | 'INACTIVE' | 'UNKNOWN';

export interface OperatorDeployment {
  service: string;
  gitCommit: string;
  buildTime: string;
  deploymentId: string;
  canonicalDeploymentId: string;
}

export interface OperatorState {
  availability: OperatorAvailability;
  health: OperatorHealth;
  requestedMode: OperatorMode;
  mode: OperatorMode;
  executionCapability: OperatorExecutionCapability;
  liveExecutionEnabled: boolean | null;
  liveTradingApproved: boolean | null;
  killSwitchState: OperatorKillSwitchState;
  deployment: OperatorDeployment | null;
  metrics: null;
  controlsEnabled: boolean;
}

export const INITIAL_OPERATOR_STATE: Readonly<OperatorState> = Object.freeze({
  availability: 'LOADING',
  health: 'UNKNOWN',
  requestedMode: 'UNKNOWN',
  mode: 'UNKNOWN',
  executionCapability: 'UNKNOWN',
  liveExecutionEnabled: null,
  liveTradingApproved: null,
  killSwitchState: 'UNKNOWN',
  deployment: null,
  metrics: null,
  controlsEnabled: false,
});

export const UNAVAILABLE_OPERATOR_STATE: Readonly<OperatorState> = Object.freeze({
  ...INITIAL_OPERATOR_STATE,
  availability: 'UNAVAILABLE',
});

export interface SafetyAuthorityState {
  state: Readonly<OperatorState>;
  safetyRevision: number;
}

export const INITIAL_SAFETY_AUTHORITY_STATE: Readonly<SafetyAuthorityState> = Object.freeze({
  state: INITIAL_OPERATOR_STATE,
  safetyRevision: 0,
});

function sameSafetyAuthority(
  left: Readonly<OperatorState>,
  right: Readonly<OperatorState>,
): boolean {
  return left.availability === right.availability
    && left.requestedMode === right.requestedMode
    && left.mode === right.mode
    && left.executionCapability === right.executionCapability
    && left.liveExecutionEnabled === right.liveExecutionEnabled
    && left.liveTradingApproved === right.liveTradingApproved
    && left.killSwitchState === right.killSwitchState
    && left.deployment?.canonicalDeploymentId === right.deployment?.canonicalDeploymentId
    && left.controlsEnabled === right.controlsEnabled;
}

export function advanceSafetyAuthorityState(
  current: Readonly<SafetyAuthorityState>,
  nextState: Readonly<OperatorState>,
): Readonly<SafetyAuthorityState> {
  return {
    state: nextState,
    safetyRevision: sameSafetyAuthority(current.state, nextState)
      ? current.safetyRevision
      : current.safetyRevision + 1,
  };
}

interface OperatorMetaResponse {
  service: string;
  git_commit: string;
  build_time: string;
  deployment_id: string;
  control_api_available: boolean;
  requested_mode: 'paper' | 'dryrun' | 'live' | null;
  effective_mode: 'paper' | 'dryrun' | 'live' | null;
  execution_capability: 'NON_LIVE' | 'LIVE_BLOCKED' | 'LIVE_AVAILABLE' | null;
  live_execution_enabled: boolean;
  live_trading_approved: boolean;
  kill_switch_state: OperatorKillSwitchState;
  canonical_deployment_id: string | null;
}

type JsonObject = Record<string, unknown>;
export type OperatorFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface LatestStateCoordinator<T> {
  run: () => Promise<T | null>;
  invalidate: (value: T) => void;
  resume: () => Promise<T | null>;
  cancel: () => void;
}

export function createLatestStateCoordinator<T>({
  load,
  publish,
}: {
  load: () => Promise<T>;
  publish: (value: T) => void;
}): LatestStateCoordinator<T> {
  let generation = 0;
  let suspended = false;

  const execute = async (): Promise<T | null> => {
    if (suspended) return null;
    const requestGeneration = ++generation;
    const value = await load();
    if (!suspended && requestGeneration === generation) publish(value);
    return value;
  };

  return {
    run: execute,
    invalidate(value) {
      suspended = true;
      generation += 1;
      publish(value);
    },
    resume() {
      suspended = false;
      return execute();
    },
    cancel() {
      suspended = true;
      generation += 1;
    },
  };
}

const META_KEYS = [
  'service',
  'git_commit',
  'build_time',
  'deployment_id',
  'control_api_available',
  'requested_mode',
  'effective_mode',
  'execution_capability',
  'live_execution_enabled',
  'live_trading_approved',
  'kill_switch_state',
  'canonical_deployment_id',
] as const;
const MODES = new Set(['paper', 'dryrun', 'live']);
const EXECUTION_CAPABILITIES = new Set(['NON_LIVE', 'LIVE_BLOCKED', 'LIVE_AVAILABLE']);
const KILL_SWITCH_STATES = new Set(['ACTIVE', 'INACTIVE', 'UNKNOWN']);

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function nonEmptyString(value: unknown, maxLength = 512): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function parseOperatorMeta(value: unknown): OperatorMetaResponse | null {
  if (!(isObject(value)
    && hasExactKeys(value, META_KEYS)
    && nonEmptyString(value.service)
    && nonEmptyString(value.git_commit)
    && nonEmptyString(value.build_time)
    && nonEmptyString(value.deployment_id)
    && typeof value.control_api_available === 'boolean'
    && typeof value.live_execution_enabled === 'boolean'
    && typeof value.live_trading_approved === 'boolean'
    && typeof value.kill_switch_state === 'string'
    && KILL_SWITCH_STATES.has(value.kill_switch_state))) return null;

  if (!value.control_api_available) {
    if (!(value.requested_mode === null
      && value.effective_mode === null
      && value.execution_capability === null
      && value.canonical_deployment_id === null)) return null;
    return value as unknown as OperatorMetaResponse;
  }

  if (!(typeof value.requested_mode === 'string' && MODES.has(value.requested_mode)
    && typeof value.effective_mode === 'string' && MODES.has(value.effective_mode)
    && typeof value.execution_capability === 'string'
    && EXECUTION_CAPABILITIES.has(value.execution_capability)
    && nonEmptyString(value.canonical_deployment_id))) return null;

  return value as unknown as OperatorMetaResponse;
}

export function projectOperatorState(value: unknown): Readonly<OperatorState> {
  const meta = parseOperatorMeta(value);
  if (meta === null || !meta.control_api_available) return UNAVAILABLE_OPERATOR_STATE;
  if (meta.requested_mode === null
    || meta.effective_mode === null
    || meta.execution_capability === null
    || meta.canonical_deployment_id === null) return UNAVAILABLE_OPERATOR_STATE;

  const requestedMode = meta.requested_mode.toUpperCase() as Exclude<OperatorMode, 'UNKNOWN'>;
  const mode = meta.effective_mode.toUpperCase() as Exclude<OperatorMode, 'UNKNOWN'>;
  const controlsEnabled = requestedMode === 'PAPER'
    && mode === 'PAPER'
    && meta.execution_capability === 'NON_LIVE'
    && meta.live_execution_enabled === false
    && meta.live_trading_approved === false
    && meta.kill_switch_state !== 'UNKNOWN';

  return {
    availability: 'AVAILABLE',
    health: 'UNKNOWN',
    requestedMode,
    mode,
    executionCapability: meta.execution_capability,
    liveExecutionEnabled: meta.live_execution_enabled,
    liveTradingApproved: meta.live_trading_approved,
    killSwitchState: meta.kill_switch_state,
    deployment: {
      service: meta.service,
      gitCommit: meta.git_commit,
      buildTime: meta.build_time,
      deploymentId: meta.deployment_id,
      canonicalDeploymentId: meta.canonical_deployment_id,
    },
    metrics: null,
    controlsEnabled,
  };
}

export async function loadOperatorState(
  fetcher: OperatorFetch = fetch,
  { timeoutMs = 5_000 }: { timeoutMs?: number } = {},
): Promise<Readonly<OperatorState>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher('/api/trading/meta', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) return UNAVAILABLE_OPERATOR_STATE;
    return projectOperatorState(await response.json());
  } catch {
    return UNAVAILABLE_OPERATOR_STATE;
  } finally {
    clearTimeout(timeout);
  }
}

export interface ExecutionOrder {
  id: number;
  exchange_order_id: string;
  symbol: string;
  side: string;
  order_type: string;
  status: string;
  quantity: number;
  filled_quantity: number;
  avg_fill_price: number | null;
  created_at: string;
}

export interface ExecutionTrade {
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  trade_timestamp: string;
}

export interface ExecutionPosition {
  symbol: string;
  quantity: number;
  avg_entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  stop_loss: number | null;
  take_profit: number | null;
  trailing_stop: number;
  highest_price: number | null;
}

export interface ExecutionAlert {
  id: number;
  message: string;
  severity: string;
  created_at: string;
}

export interface ExecutionSlippageRecord {
  symbol: string;
  side: string;
  quantity: number;
  expected_price: number;
  fill_price: number;
  slippage_pct: number;
  created_at: string;
}

export interface ExecutionPayload {
  mode: 'paper' | 'dryrun' | 'live';
  halted: boolean;
  open_orders: ExecutionOrder[];
  recent_trades: ExecutionTrade[];
  positions: ExecutionPosition[];
  pnl: {
    total_realized_pnl: number;
    total_unrealized_pnl: number;
  };
  alerts: ExecutionAlert[];
  slippage_history: ExecutionSlippageRecord[];
  avg_slippage: number | null;
}

export interface ExecutionState {
  availability: OperatorAvailability;
  data: ExecutionPayload | null;
}

export const INITIAL_EXECUTION_STATE: Readonly<ExecutionState> = Object.freeze({
  availability: 'LOADING',
  data: null,
});

export const UNAVAILABLE_EXECUTION_STATE: Readonly<ExecutionState> = Object.freeze({
  availability: 'UNAVAILABLE',
  data: null,
});

const EXECUTION_KEYS = [
  'mode', 'halted', 'open_orders', 'recent_trades', 'positions', 'pnl', 'alerts',
  'slippage_history', 'avg_slippage',
] as const;

function parseArray<T>(
  value: unknown,
  parser: (entry: unknown) => T | null,
): T[] | null {
  if (!Array.isArray(value) || value.length > 10_000) return null;
  const parsed = value.map(parser);
  return parsed.some((entry) => entry === null) ? null : parsed as T[];
}

function parseOrder(value: unknown): ExecutionOrder | null {
  const keys = [
    'id', 'exchange_order_id', 'symbol', 'side', 'order_type', 'status', 'quantity',
    'filled_quantity', 'avg_fill_price', 'created_at',
  ];
  if (!(isObject(value) && hasExactKeys(value, keys)
    && Number.isSafeInteger(value.id) && (value.id as number) >= 0
    && ['exchange_order_id', 'symbol', 'side', 'order_type', 'status', 'created_at']
      .every((key) => nonEmptyString(value[key], 512))
    && finiteNumber(value.quantity) && finiteNumber(value.filled_quantity)
    && (value.avg_fill_price === null || finiteNumber(value.avg_fill_price)))) return null;
  return value as unknown as ExecutionOrder;
}

function parseTrade(value: unknown): ExecutionTrade | null {
  const keys = ['symbol', 'side', 'quantity', 'price', 'trade_timestamp'];
  if (!(isObject(value) && hasExactKeys(value, keys)
    && nonEmptyString(value.symbol) && nonEmptyString(value.side)
    && finiteNumber(value.quantity) && finiteNumber(value.price)
    && nonEmptyString(value.trade_timestamp))) return null;
  return value as unknown as ExecutionTrade;
}

function parsePosition(value: unknown): ExecutionPosition | null {
  const keys = [
    'symbol', 'quantity', 'avg_entry_price', 'current_price', 'unrealized_pnl',
    'realized_pnl', 'stop_loss', 'take_profit', 'trailing_stop', 'highest_price',
  ];
  if (!(isObject(value) && hasExactKeys(value, keys)
    && nonEmptyString(value.symbol)
    && ['quantity', 'avg_entry_price', 'current_price', 'unrealized_pnl', 'realized_pnl', 'trailing_stop']
      .every((key) => finiteNumber(value[key]))
    && (value.stop_loss === null || finiteNumber(value.stop_loss))
    && (value.take_profit === null || finiteNumber(value.take_profit))
    && (value.highest_price === null || finiteNumber(value.highest_price)))) return null;
  return value as unknown as ExecutionPosition;
}

function parseAlert(value: unknown): ExecutionAlert | null {
  const keys = ['id', 'message', 'severity', 'created_at'];
  if (!(isObject(value) && hasExactKeys(value, keys)
    && Number.isSafeInteger(value.id) && (value.id as number) >= 0
    && nonEmptyString(value.message, 16_384) && nonEmptyString(value.severity)
    && nonEmptyString(value.created_at))) return null;
  return value as unknown as ExecutionAlert;
}

function parseSlippage(value: unknown): ExecutionSlippageRecord | null {
  const keys = [
    'symbol', 'side', 'quantity', 'expected_price', 'fill_price', 'slippage_pct',
    'created_at',
  ];
  if (!(isObject(value) && hasExactKeys(value, keys)
    && nonEmptyString(value.symbol) && nonEmptyString(value.side)
    && ['quantity', 'expected_price', 'fill_price', 'slippage_pct']
      .every((key) => finiteNumber(value[key]))
    && nonEmptyString(value.created_at))) return null;
  return value as unknown as ExecutionSlippageRecord;
}

export function parseExecutionPayload(value: unknown): ExecutionPayload | null {
  if (!(isObject(value) && hasExactKeys(value, EXECUTION_KEYS)
    && typeof value.mode === 'string' && MODES.has(value.mode)
    && typeof value.halted === 'boolean'
    && isObject(value.pnl)
    && hasExactKeys(value.pnl, ['total_realized_pnl', 'total_unrealized_pnl'])
    && finiteNumber(value.pnl.total_realized_pnl)
    && finiteNumber(value.pnl.total_unrealized_pnl)
    && (value.avg_slippage === null || finiteNumber(value.avg_slippage)))) return null;

  const openOrders = parseArray(value.open_orders, parseOrder);
  const recentTrades = parseArray(value.recent_trades, parseTrade);
  const positions = parseArray(value.positions, parsePosition);
  const alerts = parseArray(value.alerts, parseAlert);
  const slippage = parseArray(value.slippage_history, parseSlippage);
  if (openOrders === null || recentTrades === null || positions === null
    || alerts === null || slippage === null) return null;

  return {
    mode: value.mode as ExecutionPayload['mode'],
    halted: value.halted,
    open_orders: openOrders,
    recent_trades: recentTrades,
    positions,
    pnl: value.pnl as unknown as ExecutionPayload['pnl'],
    alerts,
    slippage_history: slippage,
    avg_slippage: value.avg_slippage,
  };
}

export function executionMatchesOperator(
  execution: ExecutionPayload,
  operator: Readonly<OperatorState>,
): boolean {
  if (operator.availability !== 'AVAILABLE'
    || operator.mode === 'UNKNOWN'
    || operator.killSwitchState === 'UNKNOWN') return false;
  return execution.mode.toUpperCase() === operator.mode
    && execution.halted === (operator.killSwitchState === 'ACTIVE');
}

export async function loadExecutionState(
  fetcher: OperatorFetch = fetch,
  {
    timeoutMs = 5_000,
    signal,
  }: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Readonly<ExecutionState>> {
  if (signal?.aborted) return UNAVAILABLE_EXECUTION_STATE;
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher('/api/trading/execution', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) return UNAVAILABLE_EXECUTION_STATE;
    const data = parseExecutionPayload(await response.json());
    return data === null
      ? UNAVAILABLE_EXECUTION_STATE
      : { availability: 'AVAILABLE', data };
  } catch {
    return UNAVAILABLE_EXECUTION_STATE;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}

export interface ExchangeStatusInfo {
  connected: boolean;
  sandbox?: boolean;
  balance_usd?: number | null;
  error?: string;
}

export interface ExchangeStatusPayload {
  mode: 'paper' | 'dryrun' | 'live';
  exchanges: Record<string, ExchangeStatusInfo>;
  live_execution_enabled: boolean;
}

export interface ExchangeStatusState {
  availability: OperatorAvailability;
  data: ExchangeStatusPayload | null;
}

export const INITIAL_EXCHANGE_STATUS_STATE: Readonly<ExchangeStatusState> = Object.freeze({
  availability: 'LOADING',
  data: null,
});

export const UNAVAILABLE_EXCHANGE_STATUS_STATE: Readonly<ExchangeStatusState> = Object.freeze({
  availability: 'UNAVAILABLE',
  data: null,
});

const EXCHANGE_STATUS_KEYS = ['mode', 'exchanges', 'live_execution_enabled'] as const;
const EXCHANGE_INFO_KEYS = new Set(['connected', 'sandbox', 'balance_usd', 'error']);

function parseExchangeInfo(value: unknown): ExchangeStatusInfo | null {
  if (!(isObject(value)
    && Object.keys(value).every((key) => EXCHANGE_INFO_KEYS.has(key))
    && typeof value.connected === 'boolean'
    && (value.sandbox === undefined || typeof value.sandbox === 'boolean')
    && (value.balance_usd === undefined
      || value.balance_usd === null
      || finiteNumber(value.balance_usd))
    && (value.error === undefined || nonEmptyString(value.error, 2_048)))) return null;

  return {
    connected: value.connected,
    ...(value.sandbox === undefined ? {} : { sandbox: value.sandbox }),
    ...(value.balance_usd === undefined ? {} : { balance_usd: value.balance_usd }),
    ...(value.error === undefined ? {} : { error: value.error }),
  };
}

export function parseExchangeStatusPayload(value: unknown): ExchangeStatusPayload | null {
  if (!(isObject(value)
    && hasExactKeys(value, EXCHANGE_STATUS_KEYS)
    && typeof value.mode === 'string' && MODES.has(value.mode)
    && isObject(value.exchanges)
    && Object.keys(value.exchanges).length <= 100
    && typeof value.live_execution_enabled === 'boolean')) return null;

  const exchanges: Array<[string, ExchangeStatusInfo]> = [];
  for (const [name, info] of Object.entries(value.exchanges)) {
    if (!/^[a-z0-9_-]{1,64}$/i.test(name)) return null;
    const parsed = parseExchangeInfo(info);
    if (parsed === null) return null;
    exchanges.push([name, parsed]);
  }

  return {
    mode: value.mode as ExchangeStatusPayload['mode'],
    exchanges: Object.fromEntries(exchanges),
    live_execution_enabled: value.live_execution_enabled,
  };
}

export function exchangeStatusMatchesOperator(
  exchangeStatus: ExchangeStatusPayload,
  operator: Readonly<OperatorState>,
): boolean {
  if (operator.availability !== 'AVAILABLE'
    || operator.mode === 'UNKNOWN'
    || operator.liveExecutionEnabled === null) return false;
  return exchangeStatus.mode.toUpperCase() === operator.mode
    && exchangeStatus.live_execution_enabled === operator.liveExecutionEnabled;
}

export async function loadExchangeStatusState(
  fetcher: OperatorFetch = fetch,
  {
    timeoutMs = 5_000,
    signal,
  }: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Readonly<ExchangeStatusState>> {
  if (signal?.aborted) return UNAVAILABLE_EXCHANGE_STATUS_STATE;
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher('/api/trading/exchange-status', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) return UNAVAILABLE_EXCHANGE_STATUS_STATE;
    const data = parseExchangeStatusPayload(await response.json());
    return data === null
      ? UNAVAILABLE_EXCHANGE_STATUS_STATE
      : { availability: 'AVAILABLE', data };
  } catch {
    return UNAVAILABLE_EXCHANGE_STATUS_STATE;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}

export interface ServicePayload {
  active: boolean;
  pid: number;
  started: string;
}

export interface ServiceState {
  availability: OperatorAvailability;
  data: ServicePayload | null;
}

export const INITIAL_SERVICE_STATE: Readonly<ServiceState> = Object.freeze({
  availability: 'LOADING',
  data: null,
});

export const UNAVAILABLE_SERVICE_STATE: Readonly<ServiceState> = Object.freeze({
  availability: 'UNAVAILABLE',
  data: null,
});

export function parseServicePayload(value: unknown): ServicePayload | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['active', 'pid', 'started'])
    && typeof value.active === 'boolean'
    && Number.isSafeInteger(value.pid) && (value.pid as number) >= 0
    && typeof value.started === 'string' && value.started.length <= 512)) return null;
  if (value.active && ((value.pid as number) === 0 || value.started.length === 0)) return null;
  return value as unknown as ServicePayload;
}

export async function loadServiceState(
  fetcher: OperatorFetch = fetch,
  {
    timeoutMs = 5_000,
    signal,
  }: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Readonly<ServiceState>> {
  if (signal?.aborted) return UNAVAILABLE_SERVICE_STATE;
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher('/api/trading/service', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) return UNAVAILABLE_SERVICE_STATE;
    const data = parseServicePayload(await response.json());
    return data === null
      ? UNAVAILABLE_SERVICE_STATE
      : { availability: 'AVAILABLE', data };
  } catch {
    return UNAVAILABLE_SERVICE_STATE;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}
