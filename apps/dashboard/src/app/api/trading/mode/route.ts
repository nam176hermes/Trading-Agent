import { NextRequest, NextResponse } from 'next/server';
import { authorizeMutation, checkAuth } from '@/lib/trading/auth';
import { getControlStatus, controlApiUnavailableResponse } from '@/lib/trading/control-api';
import { updatePrivateLocalStateFile } from '@/lib/trading/local-state';
import { modeFile } from '@/lib/trading/paths';
import { readBoundedJsonBody } from '@/lib/trading/request-body';

const ALLOWED_MODES = new Set(['paper', 'dryrun', 'live']);
const TRUTHY = new Set(['1', 'true', 'yes', 'on']);
const MAX_MODE_BODY_BYTES = 4 * 1024;
const MAX_MODE_STATE_BYTES = 4 * 1024;

function enabled(name: string): boolean {
  return TRUTHY.has((process.env[name] ?? '').trim().toLowerCase());
}

function requireValidPersistedMode(existing: string | null): void {
  if (existing === null) return;
  if (!ALLOWED_MODES.has(existing.trim().toLowerCase())) {
    throw new Error('persisted mode state is malformed');
  }
}

export async function GET(request: NextRequest) {
  const authError = checkAuth(request);
  if (authError) return authError;
  try {
    const { data } = await getControlStatus();
    return NextResponse.json({
      ok: true,
      requested_mode: data.requested_mode.toLowerCase(),
      effective_mode: data.effective_mode.toLowerCase(),
      live_execution_enabled: data.execution_capability === 'LIVE_AVAILABLE',
    });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export async function POST(request: NextRequest) {
  const authError = authorizeMutation(request, 'mode.update', 'MUTATION_EXECUTION_SENSITIVE', 'admin');
  if (authError) return authError;

  const parsed = await readBoundedJsonBody(request, MAX_MODE_BODY_BYTES);
  if (!parsed.ok) {
    return NextResponse.json(
      { ok: false, code: parsed.reason === 'too_large' ? 'PAYLOAD_TOO_LARGE' : 'INVALID_MODE' },
      { status: parsed.reason === 'too_large' ? 413 : 400 },
    );
  }
  const body = parsed.value;
  const mode = body && typeof body === 'object' && !Array.isArray(body)
    && Object.keys(body).length === 1 && Object.keys(body)[0] === 'mode'
    ? (body as Record<string, unknown>).mode
    : null;
  if (typeof mode !== 'string' || !ALLOWED_MODES.has(mode)) {
    return NextResponse.json({ ok: false, code: 'INVALID_MODE' }, { status: 400 });
  }
  if (mode === 'live') {
    const code = enabled('LIVE_EXECUTION_ENABLED') ? 'LIVE_APPROVAL_MISSING' : 'LIVE_EXECUTION_DISABLED';
    return NextResponse.json({
      ok: false,
      code,
      requested_mode: 'live',
      effective_mode: 'paper',
    }, { status: 403 });
  }
  if (mode === 'dryrun') {
    return NextResponse.json({
      ok: false,
      code: 'PAPER_ONLY_RELEASE',
      requested_mode: 'dryrun',
      effective_mode: 'paper',
    }, { status: 403 });
  }

  try {
    updatePrivateLocalStateFile(modeFile(), MAX_MODE_STATE_BYTES, (existing) => {
      requireValidPersistedMode(existing);
      return 'paper\n';
    });
  } catch {
    return NextResponse.json({ ok: false, code: 'MODE_STATE_UNAVAILABLE' }, { status: 503 });
  }
  return NextResponse.json({ ok: true, requested_mode: mode, effective_mode: mode });
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
