import { createHash, timingSafeEqual } from 'node:crypto';

import type { SessionRole } from './session';

export type AuthenticationResult =
  | { ok: true; role: SessionRole }
  | { ok: false; code: 'CONFIGURATION_ERROR' | 'UNAUTHORIZED' };

export interface PasswordEnvironment {
  TRADING_DASHBOARD_ADMIN_PASSWORD?: string;
  TRADING_DASHBOARD_OPERATOR_PASSWORD?: string;
  TRADING_DASHBOARD_PASSWORD?: string;
}

const ROLE_RANK: Record<SessionRole, number> = {
  reader: 0,
  operator: 1,
  admin: 2,
};

const PASSWORD_CONFIG: ReadonlyArray<readonly [SessionRole, keyof PasswordEnvironment]> = [
  ['admin', 'TRADING_DASHBOARD_ADMIN_PASSWORD'],
  ['operator', 'TRADING_DASHBOARD_OPERATOR_PASSWORD'],
  ['reader', 'TRADING_DASHBOARD_PASSWORD'],
];

const ADMIN_POST_PATHS = new Set([
  '/api/trading/service',
  '/api/trading/mode',
  '/api/trading/kill-switch',
]);

const OPERATOR_POST_PATHS = new Set([
  '/api/trading/run',
  '/api/trading/close-position',
  '/api/trading/update-stop',
  '/api/trading/plan',
  '/api/trading/watchlist',
  '/api/trading/jobs',
]);

function normalizedPath(pathname: string): string {
  return pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
}

function constantTimeMatch(provided: string, configured: string): boolean {
  const providedDigest = createHash('sha256').update(provided).digest();
  const configuredDigest = createHash('sha256').update(configured).digest();
  return timingSafeEqual(providedDigest, configuredDigest);
}

function hasDuplicatePasswords(configured: ReadonlyArray<readonly [SessionRole, string]>): boolean {
  for (let left = 0; left < configured.length; left += 1) {
    for (let right = left + 1; right < configured.length; right += 1) {
      if (constantTimeMatch(configured[left][1], configured[right][1])) return true;
    }
  }
  return false;
}

export function roleSatisfies(actual: SessionRole, required: SessionRole): boolean {
  return ROLE_RANK[actual] >= ROLE_RANK[required];
}

export function requiredRole(pathname: string, method: string): SessionRole {
  const path = normalizedPath(pathname);
  const normalizedMethod = method.toUpperCase();

  if (path === '/api/trading/keys' || path.startsWith('/api/trading/keys/')) return 'admin';
  if (normalizedMethod === 'GET' || normalizedMethod === 'HEAD') return 'reader';
  if (normalizedMethod === 'POST' && ADMIN_POST_PATHS.has(path)) return 'admin';
  if (normalizedMethod === 'POST' && OPERATOR_POST_PATHS.has(path)) return 'operator';
  if (normalizedMethod === 'POST' && /^\/api\/trading\/jobs\/[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\/cancel$/.test(path)) return 'operator';
  return 'admin';
}

export function authenticatePassword(
  password: string,
  env: PasswordEnvironment = process.env as PasswordEnvironment,
): AuthenticationResult {
  const configured = PASSWORD_CONFIG.flatMap(([role, variable]) => {
    const value = env[variable];
    return value ? [[role, value] as const] : [];
  });

  if (configured.length === 0 || hasDuplicatePasswords(configured)) {
    return { ok: false, code: 'CONFIGURATION_ERROR' };
  }

  const match = configured.find(([, configuredPassword]) => (
    constantTimeMatch(password, configuredPassword)
  ));
  return match
    ? { ok: true, role: match[0] }
    : { ok: false, code: 'UNAUTHORIZED' };
}
