import { randomUUID } from 'crypto';
import path from 'path';

import { roleSatisfies } from './access-policy';
import { appendPrivateLocalStateLine, readLocalStateFile, retirePrivateLocalStateFile } from './local-state';
import { memoryDir } from './paths';
import { verifySession } from './session';
import type { SessionPayload, SessionRole } from './session';

export type MutationClassification =
  | 'MUTATION_LOW_RISK'
  | 'MUTATION_EXECUTION_SENSITIVE'
  | 'SECRET_MANAGEMENT';

type AuthorizationOutcome = 'GRANTED' | 'CONFIGURATION_ERROR' | 'FORBIDDEN' | 'UNAUTHORIZED';

const MAX_AUDIT_BYTES = 1024 * 1024;
// Each dashboard process writes only its own log, so a partial write by one process can never
// corrupt a record another process appends; within a process appendAuditEvent is synchronous,
// so every log has exactly one writer.
const PROCESS_AUDIT_ID = randomUUID();

/** This process's audit log; readers take every dashboard_mutation_audit.*.jsonl in memoryDir(). */
export function mutationAuditPath(): string {
  return path.join(memoryDir(), `dashboard_mutation_audit.p-${PROCESS_AUDIT_ID}.jsonl`);
}

function hasValidAuditRecords(existing: string | null): boolean {
  if (existing === null || existing === '') return true;
  if (!existing.endsWith('\n')) return false;
  try {
    for (const line of existing.slice(0, -1).split('\n')) {
      if (line === '') return false;
      JSON.parse(line);
    }
  } catch {
    return false;
  }
  return true;
}

function appendAuditEvent(
  action: string,
  classification: MutationClassification,
  authorizationOutcome: AuthorizationOutcome,
  role?: SessionRole,
): boolean {
  const auditPath = mutationAuditPath();
  try {
    const existing = readLocalStateFile(auditPath, MAX_AUDIT_BYTES);
    if (!hasValidAuditRecords(existing)) return false;
    const event = {
      event: 'dashboard_mutation_authorization',
      action,
      classification,
      authorization_outcome: authorizationOutcome,
      occurred_at: new Date().toISOString(),
      ...(role ? { role } : {}),
    };
    const line = `${JSON.stringify(event)}\n`;
    if (Buffer.byteLength(existing ?? '', 'utf8') + Buffer.byteLength(line, 'utf8') > MAX_AUDIT_BYTES) {
      // Keep every record: the validated log is renamed whole into a uniquely named segment and
      // the next append starts a new log, so the bounded reader never meets a file it cannot admit.
      const stamp = event.occurred_at.replace(/[:.]/g, '-');
      retirePrivateLocalStateFile(auditPath, auditPath.replace(/\.jsonl$/, `.${stamp}.${randomUUID()}.jsonl`));
    }
    // A partial write can only damage this process's own log; the next append then fails closed.
    appendPrivateLocalStateLine(auditPath, line);
    return true;
  } catch {
    return false;
  }
}

function auditUnavailable(): Response {
  return Response.json(
    { ok: false, code: 'AUDIT_UNAVAILABLE', message: 'Mutation audit is unavailable.' },
    { status: 503 },
  );
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

  if (!appendAuditEvent(action, classification, 'GRANTED', session.role)) {
    return auditUnavailable();
  }
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
