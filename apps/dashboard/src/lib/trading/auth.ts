import fs from 'fs';
import path from 'path';

import { roleSatisfies } from './access-policy';
import { memoryDir } from './paths';
import { verifySession } from './session';
import type { SessionPayload, SessionRole } from './session';

export type MutationClassification =
  | 'MUTATION_LOW_RISK'
  | 'MUTATION_EXECUTION_SENSITIVE'
  | 'SECRET_MANAGEMENT';

type AuditOutcome = 'AUTHORIZED' | 'CONFIGURATION_ERROR' | 'FORBIDDEN' | 'UNAUTHORIZED';

function appendAuditEvent(
  action: string,
  classification: MutationClassification,
  outcome: AuditOutcome,
  role?: SessionRole,
): void {
  const auditPath = path.join(memoryDir(), 'dashboard_mutation_audit.jsonl');
  try {
    fs.mkdirSync(path.dirname(auditPath), { recursive: true, mode: 0o700 });
    const fd = fs.openSync(auditPath, fs.constants.O_APPEND | fs.constants.O_CREAT | fs.constants.O_WRONLY, 0o600);
    try {
      const event = {
        event: 'dashboard_mutation_authorization',
        action,
        classification,
        outcome,
        occurred_at: new Date().toISOString(),
        ...(role ? { role } : {}),
      };
      fs.writeSync(fd, `${JSON.stringify(event)}\n`);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.chmodSync(auditPath, 0o600);
  } catch (error) {
    console.error('Failed to write mutation authorization audit event', error instanceof Error ? error.name : 'UnknownError');
  }
}

function sessionCookie(request: Request): string | null {
  const cookieHeader = request.headers.get('cookie');
  if (!cookieHeader) return null;

  for (const pair of cookieHeader.split(';')) {
    const separator = pair.indexOf('=');
    if (separator < 0 || pair.slice(0, separator).trim() !== 'trading_session') continue;
    return pair.slice(separator + 1).trim();
  }
  return null;
}

function requestSession(request: Request): SessionPayload | null {
  const token = sessionCookie(request);
  return token ? verifySession(token) : null;
}

export function authorizeMutation(
  request: Request,
  action: string,
  classification: MutationClassification,
  requiredRole: SessionRole,
): Response | null {
  let session: SessionPayload | null;
  try {
    session = requestSession(request);
  } catch {
    appendAuditEvent(action, classification, 'CONFIGURATION_ERROR');
    return Response.json(
      { ok: false, code: 'CONFIGURATION_ERROR', message: 'Dashboard authentication is unavailable.' },
      { status: 503 },
    );
  }

  if (!session) {
    appendAuditEvent(action, classification, 'UNAUTHORIZED');
    return Response.json(
      { ok: false, code: 'UNAUTHORIZED', message: 'Authentication required.' },
      { status: 401 },
    );
  }

  if (!roleSatisfies(session.role, requiredRole)) {
    appendAuditEvent(action, classification, 'FORBIDDEN', session.role);
    return Response.json(
      { ok: false, code: 'FORBIDDEN', message: 'Insufficient permissions.' },
      { status: 403 },
    );
  }

  appendAuditEvent(action, classification, 'AUTHORIZED', session.role);
  return null;
}

/** Read-only UX authentication. It is deliberately fail-closed. */
export function checkAuth(request: Request): Response | null {
  let session: SessionPayload | null;
  try {
    session = requestSession(request);
  } catch {
    return Response.json(
      { ok: false, code: 'CONFIGURATION_ERROR', message: 'Dashboard authentication is unavailable.' },
      { status: 503 },
    );
  }
  return session
    ? null
    : Response.json(
      { ok: false, code: 'UNAUTHORIZED', message: 'Authentication required.' },
      { status: 401 },
    );
}
