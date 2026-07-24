import { NextResponse } from 'next/server.js';
import type { NextRequest } from 'next/server.js';

import { requiredRole, roleSatisfies } from './lib/trading/access-policy';
import { verifySession } from './lib/trading/session';

const SESSION_COOKIE = 'trading_session';

function errorResponse(
  status: 401 | 403 | 503,
  code: 'CONFIGURATION_ERROR' | 'FORBIDDEN' | 'UNAUTHORIZED',
  message: string,
): NextResponse {
  return NextResponse.json({ ok: false, code, message }, { status });
}

function mutationOriginAllowed(request: NextRequest): boolean {
  if (request.method === 'GET' || request.method === 'HEAD') return true;
  const origin = request.headers.get('origin');
  if (origin !== null) return origin === request.nextUrl.origin;
  return process.env.TRADING_DASHBOARD_NON_BROWSER_TEST_MODE === '1'
    && process.env.NODE_ENV === 'test';
}

export function proxy(request: NextRequest): NextResponse {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return errorResponse(401, 'UNAUTHORIZED', 'Authentication required.');
  }

  let session;
  try {
    session = verifySession(token);
  } catch {
    return errorResponse(503, 'CONFIGURATION_ERROR', 'Dashboard authentication is unavailable.');
  }
  if (!session) {
    return errorResponse(401, 'UNAUTHORIZED', 'Authentication required.');
  }

  if (!mutationOriginAllowed(request)) {
    return errorResponse(403, 'FORBIDDEN', 'Same-origin request required.');
  }

  const required = requiredRole(request.nextUrl.pathname, request.method);
  if (!roleSatisfies(session.role, required)) {
    return errorResponse(403, 'FORBIDDEN', 'Insufficient permissions.');
  }

  return NextResponse.next();
}

export const config = {
  matcher: '/api/trading/:path*',
};
