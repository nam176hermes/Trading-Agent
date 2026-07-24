import { createHmac, timingSafeEqual } from 'node:crypto';

export type SessionRole = 'reader' | 'operator' | 'admin';

export interface SessionPayload {
  v: 1;
  role: SessionRole;
  iat: number;
  exp: number;
}

const SESSION_VERSION = 1;
const SESSION_DURATION_SECONDS = 8 * 60 * 60;
const MINIMUM_SECRET_LENGTH = 32;
const ALLOWED_ROLES = new Set<SessionRole>(['reader', 'operator', 'admin']);

class SessionConfigurationError extends Error {
  readonly code = 'CONFIGURATION_ERROR';

  constructor() {
    super('A dashboard session signing secret of at least 32 characters is required.');
    this.name = 'SessionConfigurationError';
  }
}

function signingSecret(): string {
  const secret = process.env.TRADING_DASHBOARD_SESSION_SECRET;
  if (!secret || secret.length < MINIMUM_SECRET_LENGTH) throw new SessionConfigurationError();
  return secret;
}

function timestamp(now: number): number {
  if (!Number.isFinite(now) || now < 0) throw new TypeError('now must be a valid epoch timestamp');
  const seconds = Math.floor(now / 1000);
  if (
    !Number.isSafeInteger(seconds)
    || !Number.isSafeInteger(seconds + SESSION_DURATION_SECONDS)
  ) {
    throw new TypeError('now must produce safe session timestamps');
  }
  return seconds;
}

function signature(encodedPayload: string, secret: string): Buffer {
  return createHmac('sha256', secret).update(encodedPayload).digest();
}

function decodeCanonicalBase64Url(value: string): Buffer | null {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return null;
  const decoded = Buffer.from(value, 'base64url');
  return decoded.toString('base64url') === value ? decoded : null;
}

function isSessionPayload(value: unknown, now: number): value is SessionPayload {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const payload = value as Record<string, unknown>;
  if (Object.keys(payload).join(',') !== 'v,role,iat,exp') return false;

  return payload.v === SESSION_VERSION
    && typeof payload.role === 'string'
    && ALLOWED_ROLES.has(payload.role as SessionRole)
    && Number.isSafeInteger(payload.iat)
    && (payload.iat as number) >= 0
    && Number.isSafeInteger(payload.exp)
    && (payload.exp as number) >= 0
    && (payload.iat as number) <= now
    && (payload.exp as number) === (payload.iat as number) + SESSION_DURATION_SECONDS
    && now < (payload.exp as number);
}

export function issueSession(role: SessionRole, now = Date.now()): string {
  if (!ALLOWED_ROLES.has(role)) throw new TypeError('Invalid session role');
  const iat = timestamp(now);
  const payload: SessionPayload = {
    v: SESSION_VERSION,
    role,
    iat,
    exp: iat + SESSION_DURATION_SECONDS,
  };
  const encodedPayload = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const encodedSignature = signature(encodedPayload, signingSecret()).toString('base64url');
  return `${encodedPayload}.${encodedSignature}`;
}

export function verifySession(token: string, now = Date.now()): SessionPayload | null {
  const secret = signingSecret();
  const nowSeconds = timestamp(now);
  const parts = token.split('.');
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;

  const [encodedPayload, encodedSignature] = parts;
  const decodedPayload = decodeCanonicalBase64Url(encodedPayload);
  const providedSignature = decodeCanonicalBase64Url(encodedSignature);
  if (!decodedPayload || !providedSignature) return null;
  const expectedSignature = signature(encodedPayload, secret);
  if (
    providedSignature.length !== expectedSignature.length
    || !timingSafeEqual(providedSignature, expectedSignature)
  ) return null;

  try {
    const payload: unknown = JSON.parse(decodedPayload.toString('utf8'));
    return isSessionPayload(payload, nowSeconds) ? payload : null;
  } catch {
    return null;
  }
}
