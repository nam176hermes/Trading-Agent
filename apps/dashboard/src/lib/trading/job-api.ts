import 'server-only';

import {
  APPROVED_ASSETS,
  hasExactKeys,
  isObject,
  parseCreateData,
  parseErrorEnvelope,
  parseJob,
  parseJobDetail,
  parseJobList,
  parseSuccessEnvelope,
  type JobCreateData,
  type JobDetailData,
  type JobListData,
  type JobMetadata,
  type JobType,
  type JsonObject,
  type Parser,
} from './job-api-contract';
import { readBoundedJsonBody, readBoundedUtf8Body } from './request-body';

export type {
  EngineArtifactReference,
  EngineBacktestInput,
  JobActor,
  JobArtifact,
  JobAttempt,
  JobDetailData,
  JobEvent,
  JobListData,
  JobMetadata,
  JobPayload,
  JobState,
  JobType,
} from './job-api-contract';

const JOB_API_ORIGIN = 'http://127.0.0.1:8401';
const JOB_API_TIMEOUT_MS = 5_000;
const MAX_UPSTREAM_BODY_BYTES = 256 * 1024;
const MAX_DASHBOARD_BODY_BYTES = 16 * 1024;

export type DashboardJobAction =
  | { action: 'snapshot'; operationId: string }
  | { action: 'debate'; asset: string; operationId: string }
  | { action: 'replay'; sessionId: string; operationId: string }
  | { action: 'backtest'; asset: string; operationId: string };

export interface JobApiResult<T = unknown> {
  response: Response;
  data?: T;
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
  if (url.origin !== JOB_API_ORIGIN || !url.pathname.startsWith('/v1/jobs')) {
    return unavailable() as JobApiResult<T>;
  }

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
    try {
      parsed = JSON.parse(body.text);
    } catch {
      try { await upstream.body?.cancel(); } catch { /* Best-effort cleanup. */ }
      return unavailable() as JobApiResult<T>;
    }
    const sanitized = upstream.ok
      ? parseSuccessEnvelope(parsed, parse)
      : parseErrorEnvelope(parsed);
    if (!sanitized) {
      try { await upstream.body?.cancel(); } catch { /* Best-effort cleanup. */ }
      return unavailable() as JobApiResult<T>;
    }
    const traceId = upstream.headers.get('x-trace-id');
    const response = Response.json(sanitized, {
      status: upstream.status,
      headers: {
        'cache-control': 'no-store',
        ...(traceId && /^[A-Za-z0-9_.:-]{1,128}$/.test(traceId)
          ? { 'x-trace-id': traceId }
          : {}),
      },
    });
    return {
      response,
      ...(upstream.ok ? { data: sanitized.data as T } : {}),
    };
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
  const allowed = new Set([
    'job_type', 'state', 'actor_type', 'actor_id', 'requested_from',
    'requested_to', 'limit', 'offset',
  ]);
  for (const [key, value] of input) if (allowed.has(key)) filtered.append(key, value);
  return jobApiRequest(
    `/v1/jobs${filtered.size ? `?${filtered}` : ''}`,
    { method: 'GET' },
    parseJobList,
  );
}

export function getJob(jobId: string): Promise<JobApiResult<JobDetailData>> {
  if (!validJobId(jobId)) {
    return Promise.resolve(unavailable() as JobApiResult<JobDetailData>);
  }
  return jobApiRequest(
    `/v1/jobs/${encodeURIComponent(jobId)}`,
    { method: 'GET' },
    parseJobDetail,
  );
}

function canonicalAction(action: DashboardJobAction): {
  job_type: JobType;
  payload: JsonObject;
  idempotency_key: string;
  priority: number;
} | null {
  if (!/^[0-9a-f]{32}$/.test(action.operationId)) return null;
  const common = {
    idempotency_key: `dashboard:${action.action}:${action.operationId}`,
    priority: 3,
  };
  if (action.action === 'snapshot') {
    return { job_type: 'SNAPSHOT', payload: { scope: 'default', requested_as_of: null }, ...common };
  }
  if (action.action === 'replay') {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(action.sessionId)) return null;
    return { job_type: 'REPLAY', payload: { session_id: action.sessionId }, ...common };
  }
  const asset = action.asset.trim().toUpperCase();
  if (!APPROVED_ASSETS.has(asset)) return null;
  if (action.action === 'debate') {
    return { job_type: 'DEBATE', payload: { asset, horizon: '1d' }, ...common };
  }
  return {
    job_type: 'BACKTEST',
    payload: { asset, strategy_id: 'legacy-binary-report-v1', date_from: null, date_to: null },
    ...common,
  };
}

export async function createJob(
  action: DashboardJobAction,
): Promise<JobApiResult<JobCreateData>> {
  const command = canonicalAction(action);
  if (!command) {
    return {
      response: Response.json(
        { ok: false, code: 'INVALID_REQUEST', message: 'Research action is invalid.' },
        { status: 400 },
      ),
    };
  }
  return jobApiRequest(
    '/v1/jobs',
    { method: 'POST', body: JSON.stringify(command) },
    parseCreateData,
  );
}

export function cancelJob(jobId: string): Promise<JobApiResult<JobMetadata>> {
  if (!validJobId(jobId)) {
    return Promise.resolve({
      response: Response.json(
        { ok: false, code: 'INVALID_REQUEST', message: 'Job identity is invalid.' },
        { status: 400 },
      ),
    });
  }
  return jobApiRequest(
    `/v1/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: 'POST', body: JSON.stringify({}) },
    parseJob,
  );
}

export async function readDashboardAction(
  request: Request,
): Promise<DashboardJobAction | null> {
  const parsed = await readBoundedJsonBody(request, MAX_DASHBOARD_BODY_BYTES);
  if (!parsed.ok) return null;
  const { value } = parsed;
  if (!isObject(value) || typeof value.action !== 'string'
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
