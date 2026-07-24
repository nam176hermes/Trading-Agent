import { createHash, timingSafeEqual } from 'node:crypto';

import { authenticatePassword } from '../../../../lib/trading/access-policy';
import {
  checkLoginAttempt,
  recordLoginFailure,
} from '../../../../lib/trading/login-rate-limit';
import { issueSession, verifySession } from '../../../../lib/trading/session';
import { readBoundedJsonBody } from '../../../../lib/trading/request-body';

const COOKIE_NAME = 'trading_session';
const SESSION_MAX_AGE = 8 * 60 * 60;
const MAX_LOGIN_BODY_BYTES = 16 * 1024;
const MAX_PASSWORD_LENGTH = 1_024;
const UNTRUSTED_CLIENT_KEY = 'untrusted-client';

function hasTrustedProxySecret(request: Request): boolean {
  const configured = process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET;
  const provided = request.headers.get('x-trusted-proxy-secret');
  if (!configured || configured.trim() !== configured || configured.length > 256
    || !provided || provided.length > 256) return false;
  const expectedDigest = createHash('sha256').update(configured).digest();
  const providedDigest = createHash('sha256').update(provided).digest();
  return timingSafeEqual(expectedDigest, providedDigest);
}

function boundedForwardedAddress(value: string | null | undefined): string | null {
  const address = value?.trim();
  return address && address.length <= 64 && /^[0-9A-Fa-f:.]+$/.test(address)
    ? address
    : null;
}

function clientKey(request: Request): string | null {
  const configured = process.env.TRADING_DASHBOARD_TRUSTED_PROXY_SECRET;
  if (!configured || configured.trim() !== configured || configured.length > 256) {
    return process.env.NODE_ENV === 'test' ? UNTRUSTED_CLIENT_KEY : null;
  }
  if (!hasTrustedProxySecret(request)) return UNTRUSTED_CLIENT_KEY;
  const cloudflareAddress = request.headers.get('cf-connecting-ip')?.trim();
  const cloudflareKey = boundedForwardedAddress(cloudflareAddress);
  if (cloudflareKey) return `proxy:${cloudflareKey}`;

  const forwardedAddress = boundedForwardedAddress(request.headers.get('x-forwarded-for')?.split(',', 1)[0] ?? null);
  return forwardedAddress ? `proxy:${forwardedAddress}` : UNTRUSTED_CLIENT_KEY;
}

function sessionCookie(request: Request): string | null {
  const cookieHeader = request.headers.get('cookie');
  if (!cookieHeader) return null;

  for (const pair of cookieHeader.split(';')) {
    const separator = pair.indexOf('=');
    if (separator < 0 || pair.slice(0, separator).trim() !== COOKIE_NAME) continue;
    return pair.slice(separator + 1).trim();
  }
  return null;
}

function setSessionCookie(response: Response, value: string, maxAge: number): void {
  const attributes = [
    `${COOKIE_NAME}=${value}`,
    `Max-Age=${maxAge}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Strict',
  ];
  if (process.env.NODE_ENV === 'production') attributes.push('Secure');
  response.headers.append('Set-Cookie', attributes.join('; '));
}

function rateLimitedResponse(retryAfter: number): Response {
  return Response.json(
    { authenticated: false },
    { status: 429, headers: { 'Retry-After': String(retryAfter) } },
  );
}

export async function GET(request: Request) {
  const token = sessionCookie(request);
  if (!token) return Response.json({ authenticated: false });

  try {
    const session = verifySession(token);
    return session
      ? Response.json({ authenticated: true, role: session.role })
      : Response.json({ authenticated: false });
  } catch {
    return Response.json({ authenticated: false });
  }
}

export async function POST(request: Request) {
  const key = clientKey(request);
  if (key === null) return Response.json({ authenticated: false }, { status: 503 });
  const attempt = checkLoginAttempt(key, Date.now());
  if (!attempt.allowed) {
    return rateLimitedResponse(attempt.retryAfter);
  }

  const parsed = await readBoundedJsonBody(request, MAX_LOGIN_BODY_BYTES);
  if (!parsed.ok) {
    if (parsed.reason === 'too_large') return Response.json({ authenticated: false }, { status: 413 });
    return Response.json({ authenticated: false }, { status: 400 });
  }
  const body = parsed.value;
  const password = body && typeof body === 'object' && !Array.isArray(body)
    && Object.keys(body).length === 1 && Object.keys(body)[0] === 'password'
    ? (body as Record<string, unknown>).password
    : undefined;
  if (typeof password !== 'string' || password.length === 0 || password.length > MAX_PASSWORD_LENGTH) {
    return Response.json({ authenticated: false }, { status: 400 });
  }

  // Body parsing can yield to other requests. Re-check synchronously beside
  // authentication so concurrent requests cannot all pass the early check.
  const currentAttempt = checkLoginAttempt(key, Date.now());
  if (!currentAttempt.allowed) {
    return rateLimitedResponse(currentAttempt.retryAfter);
  }

  const result = authenticatePassword(password);
  if (!result.ok) {
    if (result.code === 'UNAUTHORIZED') recordLoginFailure(key, Date.now());
    return Response.json(
      { authenticated: false },
      { status: result.code === 'CONFIGURATION_ERROR' ? 503 : 401 },
    );
  }

  try {
    const token = issueSession(result.role);
    const response = Response.json({ authenticated: true });
    setSessionCookie(response, token, SESSION_MAX_AGE);
    return response;
  } catch {
    return Response.json({ authenticated: false }, { status: 503 });
  }
}

export async function DELETE() {
  const response = Response.json({ authenticated: false });
  setSessionCookie(response, '', 0);
  return response;
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
