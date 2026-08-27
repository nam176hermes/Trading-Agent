import type { components } from '../../generated/job-api-types';

type Schemas = components['schemas'];

export type EngineArtifactReference = Schemas['ArtifactReference'];
export type EngineBacktestInput = Schemas['EngineBacktestInput'];
export type JobActor = Schemas['ActorIdentity'];
export type JobArtifact = Schemas['ArtifactMetadata'];
export type JobAttempt = Schemas['AttemptMetadata'];
export type JobDetailData = Schemas['JobDetail'];
export type JobEvent = Schemas['EventMetadata'];
export type JobListData = Schemas['JobListData'];
export type JobMetadata = Schemas['JobMetadata'];
export type JobPayload = JobMetadata['payload'];
export type JobState = Schemas['JobState'];
export type JobType = Schemas['JobType'];
export type JobCreateData = Schemas['JobEnqueuedData'] | Schemas['JobDeduplicatedData'];
export type JsonObject = Record<string, unknown>;
export type Parser<T> = (value: unknown) => T | null;

const JOB_STATES = new Set([
  'QUEUED', 'CLAIMED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED',
  'TIMED_OUT', 'CANCEL_REQUESTED', 'CANCELLED',
]);
const JOB_TYPES = new Set(['SNAPSHOT', 'DEBATE', 'REPLAY', 'BACKTEST']);
const ACTOR_TYPES = new Set(['OPERATOR', 'SCHEDULER', 'WORKER', 'RECOVERY', 'SYSTEM']);
export const APPROVED_ASSETS = new Set([
  'BTC', 'ETH', 'SOL', 'TON', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'MATIC',
  'AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA',
]);

export function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function hasExactKeys(value: JsonObject, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
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

function isTraceId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
}

function parseActor(value: unknown): JobActor | null {
  if (!(isObject(value) && hasExactKeys(value, ['actor_type', 'actor_id'])
    && typeof value.actor_type === 'string' && ACTOR_TYPES.has(value.actor_type)
    && isBoundedString(value.actor_id))) return null;
  return { actor_type: value.actor_type as JobActor['actor_type'], actor_id: value.actor_id };
}

function canonicalEngineTimeKey(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/.exec(value);
  if (match === null || match[1] === '0000') return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  const parsed = new Date(timestamp);
  if (parsed.getUTCFullYear() !== Number(match[1])
    || parsed.getUTCMonth() + 1 !== Number(match[2])
    || parsed.getUTCDate() !== Number(match[3])
    || parsed.getUTCHours() !== Number(match[4])
    || parsed.getUTCMinutes() !== Number(match[5])
    || parsed.getUTCSeconds() !== Number(match[6])) return null;
  return `${value.slice(0, 19)}.${(match[7] ?? '').padEnd(6, '0')}Z`;
}

function isArtifactReference(value: unknown): value is EngineArtifactReference {
  return isObject(value) && hasExactKeys(value, ['artifact_id', 'sha256', 'media_type'])
    && typeof value.artifact_id === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.artifact_id)
    && typeof value.sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.sha256)
    && (value.media_type === 'application/json' || value.media_type === 'application/jsonl');
}

function isEnginePayload(value: JsonObject): value is { engine_backtest: EngineBacktestInput } {
  if (!(hasExactKeys(value, ['engine_backtest']) && isObject(value.engine_backtest)
    && hasExactKeys(value.engine_backtest, [
      'engine_configuration', 'instrument_catalog', 'strategy_configuration',
      'market_data', 'start_time', 'end_time',
    ]))) return false;
  const input = value.engine_backtest;
  if (!(isArtifactReference(input.engine_configuration)
    && isArtifactReference(input.instrument_catalog)
    && isArtifactReference(input.strategy_configuration)
    && isArtifactReference(input.market_data))) return false;
  const start = canonicalEngineTimeKey(input.start_time);
  const end = canonicalEngineTimeKey(input.end_time);
  return start !== null && end !== null && end > start;
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
    return hasExactKeys(value, ['session_id']) && typeof value.session_id === 'string'
      && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value.session_id);
  }
  const legacy = hasExactKeys(value, ['asset', 'date_from', 'date_to', 'strategy_id'])
    && typeof value.asset === 'string' && APPROVED_ASSETS.has(value.asset)
    && value.strategy_id === 'legacy-binary-report-v1'
    && value.date_from === null && value.date_to === null;
  return legacy || isEnginePayload(value);
}

export function parseJob(value: unknown): JobMetadata | null {
  if (!isObject(value) || !hasExactKeys(value, [
    'job_id', 'job_type', 'state', 'payload', 'payload_fingerprint', 'actor',
    'priority', 'requested_at', 'updated_at', 'attempt_count', 'reason_code', 'result_hash',
  ]) || typeof value.job_type !== 'string' || !JOB_TYPES.has(value.job_type)) return null;
  const jobType = value.job_type as JobType;
  const actor = parseActor(value.actor);
  if (!(isBoundedString(value.job_id)
    && typeof value.state === 'string' && JOB_STATES.has(value.state)
    && isPayload(value.payload, jobType)
    && typeof value.payload_fingerprint === 'string' && /^[0-9a-f]{64}$/.test(value.payload_fingerprint)
    && actor && Number.isInteger(value.priority) && (value.priority as number) >= 0
    && (value.priority as number) <= 100 && isIsoDate(value.requested_at)
    && isIsoDate(value.updated_at) && Number.isInteger(value.attempt_count)
    && (value.attempt_count as number) >= 0
    && (value.reason_code === null || (typeof value.reason_code === 'string'
      && /^[A-Z][A-Z0-9_]{0,127}$/.test(value.reason_code)))
    && (value.result_hash === null || (typeof value.result_hash === 'string'
      && /^[0-9a-f]{64}$/.test(value.result_hash))))) return null;
  return {
    job_id: value.job_id, job_type: jobType, state: value.state as JobState,
    payload: { ...value.payload } as JobPayload,
    payload_fingerprint: value.payload_fingerprint, actor,
    priority: value.priority as number, requested_at: value.requested_at,
    updated_at: value.updated_at, attempt_count: value.attempt_count as number,
    reason_code: value.reason_code as string | null,
    result_hash: value.result_hash as string | null,
  } as JobMetadata;
}

function parseAttempt(value: unknown): JobAttempt | null {
  if (!(isObject(value) && hasExactKeys(value, [
    'attempt_id', 'attempt_number', 'worker_id', 'claimed_at', 'started_at',
    'finished_at', 'exit_code', 'termination_reason', 'artifact_count',
  ]) && isBoundedString(value.attempt_id) && Number.isInteger(value.attempt_number)
    && (value.attempt_number as number) >= 1
    && (value.worker_id === null || isBoundedString(value.worker_id))
    && (value.claimed_at === null || isIsoDate(value.claimed_at))
    && (value.started_at === null || isIsoDate(value.started_at))
    && (value.finished_at === null || isIsoDate(value.finished_at))
    && (value.exit_code === null || Number.isInteger(value.exit_code))
    && (value.termination_reason === null || isBoundedString(value.termination_reason))
    && Number.isInteger(value.artifact_count) && (value.artifact_count as number) >= 0)) return null;
  return { ...value } as JobAttempt;
}

function parseEvent(value: unknown): JobEvent | null {
  if (!isObject(value) || !hasExactKeys(value, [
    'event_id', 'sequence', 'from_state', 'to_state', 'reason_code', 'actor',
    'trace_id', 'created_at',
  ])) return null;
  const actor = parseActor(value.actor);
  if (!(isBoundedString(value.event_id) && Number.isInteger(value.sequence)
    && (value.sequence as number) >= 1
    && (value.from_state === null || (typeof value.from_state === 'string'
      && JOB_STATES.has(value.from_state)))
    && typeof value.to_state === 'string' && JOB_STATES.has(value.to_state)
    && typeof value.reason_code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(value.reason_code)
    && actor && isTraceId(value.trace_id) && isIsoDate(value.created_at))) return null;
  return { ...value, actor } as JobEvent;
}

function parseArtifact(value: unknown): JobArtifact | null {
  if (!(isObject(value) && hasExactKeys(value, [
    'artifact_id', 'attempt_id', 'artifact_type', 'validator_id', 'sha256',
    'size_bytes', 'created_at',
  ]) && isBoundedString(value.artifact_id) && isBoundedString(value.attempt_id)
    && typeof value.artifact_type === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value.artifact_type)
    && isTraceId(value.validator_id) && typeof value.sha256 === 'string'
    && /^[0-9a-f]{64}$/.test(value.sha256) && Number.isInteger(value.size_bytes)
    && (value.size_bytes as number) >= 0 && isIsoDate(value.created_at))) return null;
  return { ...value } as JobArtifact;
}

export const parseJobList: Parser<JobListData> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['items', 'limit', 'offset'])
    && Array.isArray(value.items) && value.items.length <= 100
    && Number.isInteger(value.limit) && (value.limit as number) >= 1
    && (value.limit as number) <= 100 && Number.isInteger(value.offset)
    && (value.offset as number) >= 0)) return null;
  const items = value.items.map(parseJob);
  return items.some((item) => item === null) ? null : {
    items: items as JobMetadata[], limit: value.limit as number,
    offset: value.offset as number,
  };
};

export const parseJobDetail: Parser<JobDetailData> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['job', 'attempts', 'events', 'artifacts'])
    && Array.isArray(value.attempts) && value.attempts.length <= 100
    && Array.isArray(value.events) && value.events.length <= 1_000
    && Array.isArray(value.artifacts) && value.artifacts.length <= 1_000)) return null;
  const job = parseJob(value.job);
  const attempts = value.attempts.map(parseAttempt);
  const events = value.events.map(parseEvent);
  const artifacts = value.artifacts.map(parseArtifact);
  return !job || attempts.includes(null) || events.includes(null) || artifacts.includes(null)
    ? null
    : { job, attempts, events, artifacts } as JobDetailData;
};

export const parseCreateData: Parser<JobCreateData> = (value) => {
  if (!(isObject(value) && hasExactKeys(value, ['outcome', 'job'])
    && (value.outcome === 'ENQUEUED' || value.outcome === 'DEDUPLICATED'))) return null;
  const job = parseJob(value.job);
  return job ? { outcome: value.outcome, job } as JobCreateData : null;
};

function parseDetails(value: unknown): JsonObject | null {
  if (!isObject(value)) return null;
  if (hasExactKeys(value, [])) return {};
  if (!hasExactKeys(value, ['issues']) || !Array.isArray(value.issues)
    || value.issues.length > 100) return null;
  const issues = value.issues.map((issue) => {
    if (!(isObject(issue) && hasExactKeys(issue, ['location', 'message'])
      && Array.isArray(issue.location) && issue.location.length <= 10
      && issue.location.every((part) => (typeof part === 'string' && part.length <= 128)
        || Number.isSafeInteger(part)) && isBoundedString(issue.message, 512))) return null;
    return { location: [...issue.location], message: issue.message };
  });
  return issues.includes(null) ? null : { issues };
}

export function parseErrorEnvelope(value: unknown): JsonObject | null {
  if (!(isObject(value) && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'error'])
    && value.schema_version === '1.0.0' && isTraceId(value.trace_id)
    && isIsoDate(value.generated_at) && isObject(value.error)
    && hasExactKeys(value.error, ['code', 'message', 'details'])
    && typeof value.error.code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(value.error.code)
    && isBoundedString(value.error.message, 512))) return null;
  const details = parseDetails(value.error.details);
  return details === null ? null : {
    schema_version: '1.0.0', trace_id: value.trace_id,
    generated_at: value.generated_at,
    error: { code: value.error.code, message: value.error.message, details },
  };
}

export function parseSuccessEnvelope<T>(value: unknown, parse: Parser<T>): JsonObject | null {
  if (!(isObject(value) && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'data'])
    && value.schema_version === '1.0.0' && isTraceId(value.trace_id)
    && isIsoDate(value.generated_at))) return null;
  const data = parse(value.data);
  return data === null ? null : {
    schema_version: '1.0.0', trace_id: value.trace_id,
    generated_at: value.generated_at, data,
  };
}
