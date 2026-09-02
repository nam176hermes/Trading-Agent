export type PipelineKind = 'snapshot' | 'debate';
export type PipelineCommand =
  | { action: 'snapshot'; operationId: string }
  | { action: 'debate'; asset: string; operationId: string };

export interface PipelineSubmission {
  outcome: 'ENQUEUED' | 'DEDUPLICATED';
  jobId: string;
  jobType: 'SNAPSHOT' | 'DEBATE';
}

export type KillSwitchAuthorityState = 'ACTIVE' | 'INACTIVE' | 'UNKNOWN';
export interface KillSwitchIntent {
  observedState: 'INACTIVE';
  observedRevision: number;
  action: 'on';
  operationId: string;
}

export type QuickActionFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const APPROVED_ASSETS = new Set([
  'BTC', 'ETH', 'SOL', 'TON', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'MATIC',
  'AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA',
]);
const JOB_STATES = new Set([
  'QUEUED', 'CLAIMED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED',
  'TIMED_OUT', 'CANCEL_REQUESTED', 'CANCELLED',
]);
const ACTOR_TYPES = new Set(['OPERATOR', 'SCHEDULER', 'WORKER', 'RECOVERY', 'SYSTEM']);

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function boundedString(value: unknown, maxLength = 128): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function isoDate(value: unknown): value is string {
  return typeof value === 'string'
    && value.length <= 35
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value));
}

export function createPipelineCommand(
  kind: PipelineKind,
  debateAsset = 'BTC',
): PipelineCommand | null {
  const operationId = globalThis.crypto.randomUUID().replaceAll('-', '');
  if (!/^[0-9a-f]{32}$/.test(operationId)) return null;
  if (kind === 'snapshot') return { action: 'snapshot', operationId };
  const asset = debateAsset.trim().toUpperCase();
  return APPROVED_ASSETS.has(asset) ? { action: 'debate', asset, operationId } : null;
}

function payloadMatchesCommand(value: unknown, command: PipelineCommand): boolean {
  if (!isObject(value)) return false;
  if (command.action === 'snapshot') {
    return hasExactKeys(value, ['scope', 'requested_as_of'])
      && value.scope === 'default'
      && value.requested_as_of === null;
  }
  return hasExactKeys(value, ['asset', 'horizon'])
    && value.asset === command.asset
    && value.horizon === '1d';
}

function parsePipelineSubmission(
  value: unknown,
  command: PipelineCommand,
): PipelineSubmission | null {
  if (!(isObject(value)
    && hasExactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'data'])
    && value.schema_version === '1.0.0'
    && typeof value.trace_id === 'string'
    && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value.trace_id)
    && isoDate(value.generated_at)
    && isObject(value.data)
    && hasExactKeys(value.data, ['outcome', 'job'])
    && (value.data.outcome === 'ENQUEUED' || value.data.outcome === 'DEDUPLICATED')
    && isObject(value.data.job))) return null;

  const job = value.data.job;
  const expectedJobType = command.action === 'snapshot' ? 'SNAPSHOT' : 'DEBATE';
  if (!(hasExactKeys(job, [
    'job_id', 'job_type', 'state', 'payload', 'payload_fingerprint', 'actor',
    'priority', 'requested_at', 'updated_at', 'attempt_count', 'reason_code', 'result_hash',
  ])
    && boundedString(job.job_id)
    && job.job_type === expectedJobType
    && typeof job.state === 'string' && JOB_STATES.has(job.state)
    && payloadMatchesCommand(job.payload, command)
    && typeof job.payload_fingerprint === 'string'
    && /^[0-9a-f]{64}$/.test(job.payload_fingerprint)
    && isObject(job.actor)
    && hasExactKeys(job.actor, ['actor_type', 'actor_id'])
    && typeof job.actor.actor_type === 'string' && ACTOR_TYPES.has(job.actor.actor_type)
    && boundedString(job.actor.actor_id)
    && Number.isInteger(job.priority) && (job.priority as number) >= 0 && (job.priority as number) <= 100
    && isoDate(job.requested_at) && isoDate(job.updated_at)
    && Number.isInteger(job.attempt_count) && (job.attempt_count as number) >= 0
    && (job.reason_code === null
      || (typeof job.reason_code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(job.reason_code)))
    && (job.result_hash === null
      || (typeof job.result_hash === 'string' && /^[0-9a-f]{64}$/.test(job.result_hash))))) return null;

  return {
    outcome: value.data.outcome,
    jobId: job.job_id,
    jobType: expectedJobType,
  };
}

export async function submitPipelineCommand(
  fetcher: QuickActionFetch,
  command: PipelineCommand,
): Promise<PipelineSubmission | null> {
  try {
    const response = await fetcher('/api/trading/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(command),
    });
    if (!response.ok) return null;
    return parsePipelineSubmission(await response.json(), command);
  } catch {
    return null;
  }
}

export function createKillSwitchIntent(
  state: KillSwitchAuthorityState,
  revision: number,
): KillSwitchIntent | null {
  if (!Number.isSafeInteger(revision) || revision < 0) return null;
  if (state === 'INACTIVE') {
    const operationId = `op_${globalThis.crypto.randomUUID().replaceAll('-', '')}`;
    return /^op_[0-9a-f]{32}$/.test(operationId)
      ? { observedState: 'INACTIVE', observedRevision: revision, action: 'on', operationId }
      : null;
  }
  return null;
}

export function validateKillSwitchIntent(
  intent: KillSwitchIntent | null,
  currentState: KillSwitchAuthorityState,
  currentRevision: number,
): KillSwitchIntent | null {
  return intent !== null
    && Number.isSafeInteger(currentRevision)
    && currentRevision >= 0
    && intent.observedState === currentState
    && intent.observedRevision === currentRevision
    ? intent
    : null;
}

export function killSwitchRequest(
  intent: KillSwitchIntent | null,
  currentState: KillSwitchAuthorityState,
  currentRevision: number,
  reason: string,
): { action: 'on'; reason: string; operation_id: string } | null {
  const validated = validateKillSwitchIntent(intent, currentState, currentRevision);
  if (validated === null) return null;
  const trimmedReason = reason.trim();
  return trimmedReason.length >= 1 && trimmedReason.length <= 256 && !/[\r\n]/.test(trimmedReason)
    ? { action: 'on', reason: trimmedReason, operation_id: validated.operationId }
    : null;
}
