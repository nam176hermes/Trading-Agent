import 'server-only';

import fs from 'node:fs';
import path from 'node:path';

import type { components } from '../../generated/operator-api-types';
import { readBoundedUtf8Body } from './request-body';

const DEFAULT_OPERATOR_API_ORIGIN = 'http://127.0.0.1:8402';
const OPERATOR_API_TIMEOUT_MS = 5_000;
const MAX_UPSTREAM_BODY_BYTES = 256 * 1024;
const MAX_TOKEN_BYTES = 4_096;

type CommandResult = components['schemas']['CommandExecutionResultV1'];
type CommandReceipt = components['schemas']['CommandReceiptV1'];
type JsonObject = Record<string, unknown>;
type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface KillSwitchActivation {
  operationId: string;
  reason: string;
}

export class OperatorApiClientError extends Error {
  constructor() {
    super('Operator API is unavailable.');
    this.name = 'OperatorApiClientError';
  }
}

function unavailable(): never {
  throw new OperatorApiClientError();
}

function operatorApiOrigin(): string {
  const value = process.env.TRADING_OPERATOR_API_ORIGIN ?? DEFAULT_OPERATOR_API_ORIGIN;
  let url: URL;
  try { url = new URL(value); } catch { return unavailable(); }
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash
    || value !== `http://127.0.0.1:${url.port}`) unavailable();
  return value;
}

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function identity(value: unknown): value is string {
  return typeof value === 'string'
    && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
}

function digest(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
}

function canonicalUtc(value: unknown): value is string {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function nullableDigest(value: unknown): value is string | null {
  return value === null || digest(value);
}

function parseReceipt(value: unknown): CommandReceipt | null {
  if (!(isObject(value)
    && exactKeys(value, [
      'schema_version', 'command_id', 'idempotency_key_sha256', 'correlation_id',
      'request_sha256', 'actor', 'command_type', 'desired_state', 'prior_state_sha256',
      'expected_state_sha256', 'safety_evidence_sha256', 'reason_sha256', 'accepted_at',
      'applied_at', 'completed_at', 'outcome', 'outcome_code', 'resulting_state_sha256',
      'intent_sha256', 'applied_sha256', 'receipt_sha256',
    ])
    && value.schema_version === 'operator-command-receipt-v1'
    && typeof value.command_id === 'string' && /^cmd_[0-9a-f]{32}$/.test(value.command_id)
    && digest(value.idempotency_key_sha256) && identity(value.correlation_id)
    && digest(value.request_sha256) && isObject(value.actor)
    && exactKeys(value.actor, ['schema_version', 'principal_id', 'interface'])
    && value.actor.schema_version === 'operator-actor-v1'
    && identity(value.actor.principal_id) && value.actor.interface === 'WEB'
    && value.command_type === 'SET_KILL_SWITCH'
    && value.desired_state === 'KILL_SWITCH_ACTIVE'
    && digest(value.prior_state_sha256) && value.expected_state_sha256 === null
    && nullableDigest(value.safety_evidence_sha256) && digest(value.reason_sha256)
    && canonicalUtc(value.accepted_at) && canonicalUtc(value.applied_at)
    && canonicalUtc(value.completed_at)
    && ['APPLIED', 'NO_CHANGE', 'RECOVERED_APPLIED'].includes(String(value.outcome))
    && typeof value.outcome_code === 'string' && value.outcome_code.length > 0
    && value.outcome_code.length <= 128
    && digest(value.resulting_state_sha256) && digest(value.intent_sha256)
    && digest(value.applied_sha256) && digest(value.receipt_sha256))) return null;
  return value as unknown as CommandReceipt;
}

function parseCommandEnvelope(value: unknown): CommandResult | null {
  if (!(isObject(value)
    && exactKeys(value, ['schema_version', 'trace_id', 'generated_at', 'data'])
    && value.schema_version === '1.0.0' && identity(value.trace_id)
    && canonicalUtc(value.generated_at) && isObject(value.data)
    && exactKeys(value.data, ['result']) && isObject(value.data.result)
    && exactKeys(value.data.result, ['schema_version', 'receipt', 'deduplicated'])
    && value.data.result.schema_version === 'operator-command-execution-result-v1'
    && typeof value.data.result.deduplicated === 'boolean')) return null;
  const receipt = parseReceipt(value.data.result.receipt);
  return receipt === null ? null : { ...value.data.result, receipt } as CommandResult;
}

function sameFile(left: fs.Stats, right: fs.Stats): boolean {
  return left.dev === right.dev && left.ino === right.ino && left.mode === right.mode
    && left.uid === right.uid && left.nlink === right.nlink && left.size === right.size
    && left.mtimeMs === right.mtimeMs && left.ctimeMs === right.ctimeMs;
}

function safeDirectory(target: string): boolean {
  const info = fs.lstatSync(/* turbopackIgnore: true */ target);
  const mode = info.mode & 0o7777;
  const owner = process.geteuid?.() ?? info.uid;
  return info.isDirectory() && (info.uid === 0 || info.uid === owner)
    && (!(mode & 0o022) || (info.uid === 0 && Boolean(mode & 0o1000)));
}

/** Loads the Operator API WEB bearer without accepting aliases or unsafe metadata. */
export function loadOperatorWebToken(rawPath = process.env.OPERATOR_API_WEB_TOKEN_FILE): string {
  let descriptor: number | undefined;
  try {
    if (typeof rawPath !== 'string' || !path.isAbsolute(rawPath) || rawPath === '/'
      || path.normalize(rawPath) !== rawPath || rawPath.includes('\\')) unavailable();
    let ancestor = '/';
    for (const part of rawPath.split('/').slice(1, -1)) {
      if (!part || part === '.' || part === '..') unavailable();
      ancestor = path.join(/* turbopackIgnore: true */ ancestor, part);
      if (!safeDirectory(ancestor)) unavailable();
    }
    const flags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW;
    descriptor = fs.openSync(/* turbopackIgnore: true */ rawPath, flags);
    const before = fs.fstatSync(descriptor);
    const mode = before.mode & 0o7777;
    const owner = process.geteuid?.() ?? before.uid;
    if (!before.isFile() || before.uid !== owner || before.nlink !== 1
      || Boolean(mode & ~0o600) || before.size > MAX_TOKEN_BYTES + 1) unavailable();
    const buffer = Buffer.alloc(MAX_TOKEN_BYTES + 2);
    const size = fs.readSync(descriptor, buffer, 0, buffer.length, 0);
    const after = fs.fstatSync(descriptor);
    const named = fs.lstatSync(/* turbopackIgnore: true */ rawPath);
    if (size > MAX_TOKEN_BYTES + 1 || !sameFile(before, after) || !sameFile(before, named)) unavailable();
    const raw = buffer.subarray(0, size);
    const token = raw.at(-1) === 0x0a ? raw.subarray(0, -1) : raw;
    if (token.length < 32 || token.length > MAX_TOKEN_BYTES
      || token.some((byte) => byte < 0x21 || byte > 0x7e)) unavailable();
    return token.toString('ascii');
  } catch (error) {
    if (error instanceof OperatorApiClientError) throw error;
    return unavailable();
  } finally {
    if (descriptor !== undefined) try { fs.closeSync(descriptor); } catch { /* Best effort. */ }
  }
}

export async function submitKillSwitchActivation(
  activation: KillSwitchActivation,
  fetcher: Fetcher = fetch,
): Promise<CommandResult> {
  const reason = activation.reason.trim();
  if (!/^op_[0-9a-f]{32}$/.test(activation.operationId)
    || reason.length < 1 || reason.length > 256 || /[\r\n]/.test(reason)) unavailable();
  const operationHex = activation.operationId.slice(3);
  const origin = operatorApiOrigin();
  const token = loadOperatorWebToken();
  const body = {
    schema_version: 'submit-operator-command-v1',
    command_id: `cmd_${operationHex}`,
    idempotency_key: `web.kill-switch.activate:${activation.operationId}`,
    correlation_id: activation.operationId,
    expected_state_sha256: null,
    command: {
      command_type: 'SET_KILL_SWITCH',
      desired_state: 'ACTIVE',
      reason,
    },
  } as const;
  try {
    const response = await fetcher(`${origin}/v1/commands`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${token}`,
        accept: 'application/json',
        'content-type': 'application/json',
      },
      body: JSON.stringify(body),
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(OPERATOR_API_TIMEOUT_MS),
    });
    const contentType = response.headers.get('content-type') ?? '';
    if (!response.ok || response.redirected
      || (response.url && new URL(response.url).origin !== origin)
      || !/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(contentType)) unavailable();
    const bounded = await readBoundedUtf8Body(response, MAX_UPSTREAM_BODY_BYTES);
    if (!bounded.ok) unavailable();
    let parsed: unknown;
    try { parsed = JSON.parse(bounded.text); } catch { unavailable(); }
    const result = parseCommandEnvelope(parsed);
    if (result === null || result.receipt.command_id !== body.command_id
      || result.receipt.correlation_id !== activation.operationId) unavailable();
    return result;
  } catch (error) {
    if (error instanceof OperatorApiClientError) throw error;
    unavailable();
  }
}
