import 'server-only';

import { readBoundedJsonBody, readBoundedUtf8Body } from './request-body';

const JOB_API_ORIGIN = 'http://127.0.0.1:8401';
const JOB_API_SCHEMA_VERSION = '1.0.0';
const JOB_API_TIMEOUT_MS = 5_000;
const MAX_UPSTREAM_BODY_BYTES = 256 * 1024;
const MAX_DASHBOARD_BODY_BYTES = 16 * 1024;
const JOB_STATES = new Set([
  'QUEUED', 'CLAIMED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED',
  'TIMED_OUT', 'CANCEL_REQUESTED', 'CANCELLED',
]);
const JOB_TYPES = new Set(['SNAPSHOT', 'DEBATE', 'REPLAY', 'BACKTEST']);
const ACTOR_TYPES = new Set(['OPERATOR', 'SCHEDULER', 'WORKER', 'RECOVERY', 'SYSTEM']);
const APPROVED_ASSETS = new Set([
  'BTC', 'ETH', 'SOL', 'TON', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'MATIC',
  'AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA',
]);

export type JobState =
  | 'QUEUED' | 'CLAIMED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'BLOCKED'
  | 'TIMED_OUT' | 'CANCEL_REQUESTED' | 'CANCELLED';
export type JobType = 'SNAPSHOT' | 'DEBATE' | 'REPLAY' | 'BACKTEST';

export interface EngineArtifactReference {
  artifact_id: string;
  sha256: string;
  media_type: 'application/json' | 'application/jsonl';
}

export interface EngineBacktestInput {
  engine_configuration: EngineArtifactReference;
  instrument_catalog: EngineArtifactReference;
  strategy_configuration: EngineArtifactReference;
  market_data: EngineArtifactReference;
  start_time: string;
  end_time: string;
}

export type JobPayload =
  | { scope: 'default'; requested_as_of: null }
  | { asset: string; horizon: '1d' }
  | { session_id: string }
  | {
    asset: string;
    strategy_id: 'legacy-binary-report-v1';
    date_from: null;
    date_to: null;
  }
  | { engine_backtest: EngineBacktestInput };

export interface JobActor {
  actor_type: 'OPERATOR' | 'SCHEDULER' | 'WORKER' | 'RECOVERY' | 'SYSTEM';
  actor_id: string;
}

export interface JobMetadata {
  job_id: string;
  job_type: JobType;
  state: JobState;
  payload: JobPayload;
  payload_fingerprint: string;
  actor: JobActor;
  priority: number;
  requested_at: string;
  updated_at: string;
  attempt_count: number;
  reason_code: string | null;
  result_hash: string | null;
}

export interface JobAttempt {
  attempt_id: string;
  attempt_number: number;
  worker_id: string | null;
  claimed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  termination_reason: string | null;
  artifact_count: number;
}

export interface JobEvent {
  event_id: string;
  sequence: number;
  from_state: JobState | null;
  to_state: JobState;
  reason_code: string;
  actor: JobActor;
  trace_id: string;
  created_at: string;
}

export interface JobArtifact {
  artifact_id: string;
  attempt_id: string;
  artifact_type: string;
  validator_id: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
}

export interface JobListData {
  items: JobMetadata[];
  limit: number;
  offset: number;
}

export interface JobDetailData {
  job: JobMetadata;
  attempts: JobAttempt[];
  events: JobEvent[];
  artifacts: JobArtifact[];
}

export type DashboardJobAction =
  | { action: 'snapshot'; operationId: string }
  | { action: 'debate'; asset: string; operationId: string }
  | { action: 'replay'; sessionId: string; operationId: string }
  | { action: 'backtest'; asset: string; operationId: string };

export interface JobApiResult<T = unknown> {
  response: Response;
  data?: T;
}

type JsonObject = Record<string, unknown>;
type Parser<T> = (value: unknown) => T | null;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isBoundedString(value: unknown, max = 128): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max;
}

function isIsoDate(value: unknown): value is string {
  return typeof value === 'string'
    && value.length <= 35
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function isNullableIsoDate(value: unknown): value is string | null {
  return value === null || isIsoDate(value);
}

function isTraceId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
}

function parseActor(value: unknown): JobActor | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['actor_type', 'actor_id'])
    && typeof value.actor_type === 'string'
    && ACTOR_TYPES.has(value.actor_type)
    && isBoundedString(value.actor_id))) return null;
  return { actor_type: value.actor_type as JobActor['actor_type'], actor_id: value.actor_id };
}

function hasExactKeys(value: JsonObject, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index]);
}

function isCanonicalEngineTime(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?Z$/.exec(value);
  if (match === null || match[1] === '0000') return false;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return false;
  const parsed = new Date(timestamp);
  return parsed.getUTCFullYear() === Number(match[1])
    && parsed.getUTCMonth() + 1 === Number(match[2])
    && parsed.getUTCDate() === Number(match[3])
    && parsed.getUTCHours() === Number(match[4])
    && parsed.getUTCMinutes() === Number(match[5])
    && parsed.getUTCSeconds() === Number(match[6]);
}

function isEngineArtifactReference(value: unknown): value is EngineArtifactReference {
  return isObject(value)
    && hasExactKeys(value, ['artifact_id', 'sha256', 'media_type'])
    && typeof value.artifact_id === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.artifact_id)
    && typeof value.sha256 === 'string'
    && /^[0-9a-f]{64}$/.test(value.sha256)
    && (value.media_type === 'application/json' || value.media_type === 'application/jsonl');
}

function isEngineBacktestPayload(
  value: JsonObject,
): value is { engine_backtest: EngineBacktestInput } {
  if (!(hasExactKeys(value, ['engine_backtest'])
    && isObject(value.engine_backtest)
    && hasExactKeys(value.engine_backtest, [
      'engine_configuration', 'instrument_catalog', 'strategy_configuration',
      'market_data', 'start_time', 'end_time',
    ]))) return false;
  const input = value.engine_backtest;
  return isEngineArtifactReference(input.engine_configuration)
    && isEngineArtifactReference(input.instrument_catalog)
    && isEngineArtifactReference(input.strategy_configuration)
    && isEngineArtifactReference(input.market_data)
    && isCanonicalEngineTime(input.start_time)
    && isCanonicalEngineTime(input.end_time)
    && Date.parse(input.end_time) > Date.parse(input.start_time);
}

function isPayload(value: unknown, jobType: JobType): value is JobPayload {
  if (!isObject(value)) return false;
  if (jobType === 'SNAPSHOT') {
    return hasExactKeys(value, ['requested_as_of', 'scope'])
      && value.scope === 'default' && value.requested_as_of === null;
  }
  if (jobType === 'DEBATE') {
    return hasExactKeys(value, ['asset', 'horizon'])
      && typeof value.asset === 'string' && APPROVED_ASSETS.has(value.asset)
      && value.horizon === '1d';
  }
  if (jobType === 'REPLAY') {
    return hasExactKeys(value, ['session_id'])
      && typeof value.session_id === 'string'
      && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value.session_id);
  }
  const legacy = hasExactKeys(value, ['asset', 'date_from', 'date_to', 'strategy_id'])
    && typeof value.asset === 'string' && APPROVED_ASSETS.has(value.asset)
    && value.strategy_id === 'legacy-binary-report-v1'
    && value.date_from === null && value.date_to === null;
  return legacy || isEngineBacktestPayload(value);
}

function parseJob(value: unknown): JobMetadata | null {
  if (!isObject(value)
    || !hasExactKeys(value, [
      'job_id', 'job_type', 'state', 'payload', 'payload_fingerprint', 'actor',
      'priority', 'requested_at', 'updated_at', 'attempt_count', 'reason_code', 'result_hash',
    ])
    || typeof value.job_type !== 'string' || !JOB_TYPES.has(value.job_type)) return null;
  const jobType = value.job_type as JobType;
  const actor = parseActor(value.actor);
  if (!(isBoundedString(value.job_id)
    && typeof value.state === 'string' && JOB_STATES.has(value.state)
    && isPayload(value.payload, jobType)
    && typeof value.payload_fingerprint === 'string' && /^[0-9a-f]{64}$/.test(value.payload_fingerprint)
    && actor
    && Number.isInteger(value.priority) && (value.priority as number) >= 0 && (value.priority as number) <= 100
    && isIsoDate(value.requested_at) && isIsoDate(value.updated_at)
    && Number.isInteger(value.attempt_count) && (value.attempt_count as number) >= 0
    && (value.reason_code === null || (typeof value.reason_code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(value.reason_code)))
    && (value.result_hash === null || (typeof value.result_hash === 'string' && /^[0-9a-f]{64}$/.test(value.result_hash))))) return null;
  return {
    job_id: value.job_id, job_type: jobType, state: value.state as JobState,
    payload: { ...value.payload } as JobPayload,
    payload_fingerprint: value.payload_fingerprint,
    actor, priority: value.priority as number, requested_at: value.requested_at,
    updated_at: value.updated_at, attempt_count: value.attempt_count as number,
    reason_code: value.reason_code as string | null, result_hash: value.result_hash as string | null,
  };
}

function parseAttempt(value: unknown): JobAttempt | null {
  if (!(isObject(value)
    && hasExactKeys(value, [
      'attempt_id', 'attempt_number', 'worker_id', 'claimed_at', 'started_at',
      'finished_at', 'exit_code', 'termination_reason', 'artifact_count',
    ])
    && isBoundedString(value.attempt_id)
    && Number.isInteger(value.attempt_number) && (value.attempt_number as number) >= 1
    && (value.worker_id === null || isBoundedString(value.worker_id))
    && isNullableIsoDate(value.claimed_at) && isNullableIsoDate(value.started_at)
    && isNullableIsoDate(value.finished_at)
    && (value.exit_code === null || Number.isInteger(value.exit_code))
    && (value.termination_reason === null || isBoundedString(value.termination_reason))
    && Number.isInteger(value.artifact_count) && (value.artifact_count as number) >= 0)) return null;
  return {
    attempt_id: value.attempt_id, attempt_number: value.attempt_number as number,
    worker_id: value.worker_id as string | null, claimed_at: value.claimed_at as string | null,
    started_at: value.started_at as string | null, finished_at: value.finished_at as string | null,
    exit_code: value.exit_code as number | null, termination_reason: value.termination_reason as string | null,
    artifact_count: value.artifact_count as number,
  };
}

function parseEvent(value: unknown): JobEvent | null {
  if (!isObject(value)
    || !hasExactKeys(value, ['event_id', 'sequence', 'from_state', 'to_state', 'reason_code', 'actor', 'trace_id', 'created_at'])) return null;
  const actor = parseActor(value.actor);
  if (!(isBoundedString(value.event_id)
    && Number.isInteger(value.sequence) && (value.sequence as number) >= 1
    && (value.from_state === null || (typeof value.from_state === 'string' && JOB_STATES.has(value.from_state)))
    && typeof value.to_state === 'string' && JOB_STATES.has(value.to_state)
    && typeof value.reason_code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(value.reason_code)
    && actor && isTraceId(value.trace_id) && isIsoDate(value.created_at))) return null;
  return {
    event_id: value.event_id, sequence: value.sequence as number,
    from_state: value.from_state as JobState | null, to_state: value.to_state as JobState,
    reason_code: value.reason_code, actor, trace_id: value.trace_id, created_at: value.created_at,
  };
}

function parseArtifact(value: unknown): JobArtifact | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['artifact_id', 'attempt_id', 'artifact_type', 'validator_id', 'sha256', 'size_bytes', 'created_at'])
    && isBoundedString(value.artifact_id) && isBoundedString(value.attempt_id)
    && typeof value.artifact_type === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value.artifact_type)
    && isTraceId(value.validator_id)
    && typeof value.sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.sha256)
    && Number.isInteger(value.size_bytes) && (value.size_bytes as number) >= 0
    && isIsoDate(value.created_at))) return null;
  return {
    artifact_id: value.artifact_id, attempt_id: value.attempt_id,
    artifact_type: value.artifact_type, validator_id: value.validator_id,
    sha256: value.sha256, size_bytes: value.size_bytes as number, created_at: value.created_at,
  };
}

const parseJobList: Parser<JobListData> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['items', 'limit', 'offset'])
    && Array.isArray(value.items) && value.items.length <= 100
    && Number.isInteger(value.limit) && (value.limit as number) >= 1 && (value.limit as number) <= 100
    && Number.isInteger(value.offset) && (value.offset as number) >= 0)) return null;
  const items = value.items.map(parseJob);
  if (items.some((item) => item === null)) return null;
  return { items: items as JobMetadata[], limit: value.limit as number, offset: value.offset as number };
};

const parseJobDetail: Parser<JobDetailData> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['job', 'attempts', 'events', 'artifacts'])
    && Array.isArray(value.attempts) && value.attempts.length <= 100
    && Array.isArray(value.events) && value.events.length <= 1_000
    && Array.isArray(value.artifacts) && value.artifacts.length <= 1_000)) return null;
  const job = parseJob(value.job);
  const attempts = value.attempts.map(parseAttempt);
  const events = value.events.map(parseEvent);
  const artifacts = value.artifacts.map(parseArtifact);
  if (!job || attempts.some((item) => item === null) || events.some((item) => item === null) || artifacts.some((item) => item === null)) return null;
  return { job, attempts: attempts as JobAttempt[], events: events as JobEvent[], artifacts: artifacts as JobArtifact[] };
};

const parseCreateData: Parser<{ outcome: 'ENQUEUED' | 'DEDUPLICATED'; job: JobMetadata }> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['outcome', 'job'])
    && (value.outcome === 'ENQUEUED' || value.outcome === 'DEDUPLICATED'))) return null;
  const job = parseJob(value.job);
  return job ? { outcome: value.outcome, job } : null;
};

interface SanitizedErrorEnvelope {
  schema_version: '1.0.0';
  trace_id: string;
  generated_at: string;
  error: { code: string; message: string; details: JsonObject };
}

function parseErrorDetails(value: unknown): JsonObject | null {
  if (!isObject(value)) return null;
  if (hasExactKeys(value, [])) return {};
  if (!hasExactKeys(value, ['issues']) || !Array.isArray(value.issues) || value.issues.length > 100) return null;
  const issues = value.issues.map((issue) => {
    if (!(isObject(issue) && hasExactKeys(issue, ['location', 'message'])
      && Array.isArray(issue.location) && issue.location.length <= 10
      && issue.location.every((part) => (typeof part === 'string' && part.length <= 128) || Number.isSafeInteger(part))
      && isBoundedString(issue.message, 512))) return null;
    return { location: [...issue.location], message: issue.message };
  });
  return issues.some((issue) => issue === null) ? null : { issues };
}

function parseErrorEnvelope(value: unknown): SanitizedErrorEnvelope | null {
  if (!(isObject(value) && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'error'])
    && value.schema_version === JOB_API_SCHEMA_VERSION && isTraceId(value.trace_id)
    && isIsoDate(value.generated_at) && isObject(value.error)
    && hasExactKeys(value.error, ['code', 'message', 'details'])
    && typeof value.error.code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(value.error.code)
    && isBoundedString(value.error.message, 512))) return null;
  const details = parseErrorDetails(value.error.details);
  return details === null ? null : {
    schema_version: JOB_API_SCHEMA_VERSION, trace_id: value.trace_id, generated_at: value.generated_at,
    error: { code: value.error.code, message: value.error.message, details },
  };
}

function parseSuccessEnvelope<T>(value: unknown, parse: Parser<T>): { schema_version: '1.0.0'; trace_id: string; generated_at: string; data: T } | null {
  if (!(isObject(value) && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'data'])
    && value.schema_version === JOB_API_SCHEMA_VERSION && isTraceId(value.trace_id)
    && isIsoDate(value.generated_at))) return null;
  const data = parse(value.data);
  return data === null ? null : {
    schema_version: JOB_API_SCHEMA_VERSION, trace_id: value.trace_id, generated_at: value.generated_at, data,
  };
}

function unavailable(): JobApiResult {
  return {
    response: Response.json(
      { ok: false, code: 'JOB_API_UNAVAILABLE', message: 'Research job service is unavailable.' },
      { status: 503, headers: { 'cache-control': 'no-store' } },
    ),
  };
}

export function commandsEnabled(): boolean {
  return process.env.TRADING_JOB_COMMANDS_ENABLED === '1';
}

export function commandsDisabledResponse(): Response {
  return Response.json(
    { ok: false, code: 'JOB_COMMANDS_DISABLED', message: 'Research job commands are disabled.' },
    { status: 503, headers: { 'cache-control': 'no-store' } },
  );
}

export async function jobApiRequest<T>(
  pathname: string,
  init: RequestInit,
  parse: Parser<T>,
): Promise<JobApiResult<T>> {
  const token = process.env.TRADING_JOB_API_TOKEN;
  if (!token || token.trim() !== token) return unavailable() as JobApiResult<T>;
  let url: URL;
  try {
    url = new URL(pathname, JOB_API_ORIGIN);
  } catch {
    return unavailable() as JobApiResult<T>;
  }
  if (url.origin !== JOB_API_ORIGIN || !url.pathname.startsWith('/v1/jobs')) return unavailable() as JobApiResult<T>;

  const headers = new Headers(init.headers);
  headers.set('authorization', `Bearer ${token}`);
  headers.set('accept', 'application/json');
  if (init.body !== undefined) headers.set('content-type', 'application/json');
  try {
    const upstream = await fetch(url, {
      ...init,
      headers,
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(JOB_API_TIMEOUT_MS),
    });
    const body = await readBoundedUtf8Body(upstream, MAX_UPSTREAM_BODY_BYTES);
    if (!body.ok) return unavailable() as JobApiResult<T>;
    let parsed: unknown;
    try { parsed = JSON.parse(body.text); } catch {
      try { await upstream.body?.cancel(); } catch { /* Cancellation is best-effort after parse abort. */ }
      return unavailable() as JobApiResult<T>;
    }
    const sanitized = upstream.ok ? parseSuccessEnvelope(parsed, parse) : parseErrorEnvelope(parsed);
    if (!sanitized) {
      try { await upstream.body?.cancel(); } catch { /* Cancellation is best-effort after validation abort. */ }
      return unavailable() as JobApiResult<T>;
    }
    const traceId = upstream.headers.get('x-trace-id');
    const response = Response.json(sanitized, {
      status: upstream.status,
      headers: {
        'cache-control': 'no-store',
        ...(traceId && /^[A-Za-z0-9_.:-]{1,128}$/.test(traceId) ? { 'x-trace-id': traceId } : {}),
      },
    });
    return { response, ...(upstream.ok ? { data: (sanitized as { data: T }).data } : {}) };
  } catch {
    return unavailable() as JobApiResult<T>;
  }
}

function validJobId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
}

export function listJobs(query: string): Promise<JobApiResult<JobListData>> {
  const input = new URLSearchParams(query);
  const filtered = new URLSearchParams();
  const allowed = new Set(['job_type', 'state', 'actor_type', 'actor_id', 'requested_from', 'requested_to', 'limit', 'offset']);
  for (const [key, value] of input) if (allowed.has(key)) filtered.append(key, value);
  return jobApiRequest(`/v1/jobs${filtered.size ? `?${filtered}` : ''}`, { method: 'GET' }, parseJobList);
}

export function getJob(jobId: string): Promise<JobApiResult<JobDetailData>> {
  if (!validJobId(jobId)) return Promise.resolve(unavailable() as JobApiResult<JobDetailData>);
  return jobApiRequest(`/v1/jobs/${encodeURIComponent(jobId)}`, { method: 'GET' }, parseJobDetail);
}

function canonicalAction(action: DashboardJobAction): { job_type: JobType; payload: JsonObject; idempotency_key: string; priority: number } | null {
  if (!/^[0-9a-f]{32}$/.test(action.operationId)) return null;
  const common = { idempotency_key: `dashboard:${action.action}:${action.operationId}`, priority: 3 };
  if (action.action === 'snapshot') return { job_type: 'SNAPSHOT', payload: { scope: 'default', requested_as_of: null }, ...common };
  if (action.action === 'replay') {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(action.sessionId)) return null;
    return { job_type: 'REPLAY', payload: { session_id: action.sessionId }, ...common };
  }
  const asset = action.asset.trim().toUpperCase();
  if (!APPROVED_ASSETS.has(asset)) return null;
  if (action.action === 'debate') return { job_type: 'DEBATE', payload: { asset, horizon: '1d' }, ...common };
  return { job_type: 'BACKTEST', payload: { asset, strategy_id: 'legacy-binary-report-v1', date_from: null, date_to: null }, ...common };
}

export async function createJob(action: DashboardJobAction): Promise<JobApiResult<{ outcome: 'ENQUEUED' | 'DEDUPLICATED'; job: JobMetadata }>> {
  const command = canonicalAction(action);
  if (!command) return { response: Response.json({ ok: false, code: 'INVALID_REQUEST', message: 'Research action is invalid.' }, { status: 400 }) };
  return jobApiRequest('/v1/jobs', { method: 'POST', body: JSON.stringify(command) }, parseCreateData);
}

export function cancelJob(jobId: string): Promise<JobApiResult<JobMetadata>> {
  if (!validJobId(jobId)) return Promise.resolve({ response: Response.json({ ok: false, code: 'INVALID_REQUEST', message: 'Job identity is invalid.' }, { status: 400 }) });
  return jobApiRequest(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify({}),
  }, parseJob);
}

export async function readDashboardAction(
  request: Request,
): Promise<DashboardJobAction | null> {
  const parsed = await readBoundedJsonBody(request, MAX_DASHBOARD_BODY_BYTES);
  if (!parsed.ok) return null;
  const { value } = parsed;
  if (!isObject(value)
    || typeof value.action !== 'string'
    || typeof value.operationId !== 'string'
    || !/^[0-9a-f]{32}$/.test(value.operationId)) return null;
  if (value.action === 'snapshot' && hasExactKeys(value, ['action', 'operationId'])) {
    return { action: 'snapshot', operationId: value.operationId };
  }
  if (value.action === 'debate'
    && hasExactKeys(value, ['action', 'asset', 'operationId'])
    && typeof value.asset === 'string') {
    return { action: 'debate', asset: value.asset, operationId: value.operationId };
  }
  if (value.action === 'replay'
    && hasExactKeys(value, ['action', 'operationId', 'sessionId'])
    && typeof value.sessionId === 'string') {
    return { action: 'replay', sessionId: value.sessionId, operationId: value.operationId };
  }
  if (value.action === 'backtest'
    && hasExactKeys(value, ['action', 'asset', 'operationId'])
    && typeof value.asset === 'string') {
    return { action: 'backtest', asset: value.asset, operationId: value.operationId };
  }
  return null;
}
