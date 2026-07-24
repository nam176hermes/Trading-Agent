import { NextResponse } from 'next/server';
import { authorizeMutation } from '@/lib/trading/auth';
import {
  activateKillSwitch,
  clearKillSwitch,
  publicKillSwitchPath,
  readKillSwitchState,
} from '@/lib/trading/kill-switch';
import { controlApiUnavailableResponse, getControlStatus } from '@/lib/trading/control-api';
import { readBoundedJsonBody } from '@/lib/trading/request-body';

const MAX_KILL_SWITCH_BODY_BYTES = 4 * 1024;

function isKillSwitchRequest(value: unknown): value is { action: 'on'; reason?: string } | { action: 'off' } {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const body = value as Record<string, unknown>;
  const keys = Object.keys(body).sort();
  if (body.action === 'off') return keys.length === 1 && keys[0] === 'action';
  return body.action === 'on'
    && ((keys.length === 1 && keys[0] === 'action')
      || (keys.length === 2 && keys[0] === 'action' && keys[1] === 'reason'
        && typeof body.reason === 'string' && body.reason.length <= 256));
}

export async function GET() {
  try {
    const { data } = await getControlStatus();
    return NextResponse.json({
      state: data.kill_switch_state,
      reason: null,
      activated_at: null,
      path: 'canonical-safety-provider',
    });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'kill_switch.update', 'MUTATION_EXECUTION_SENSITIVE', 'admin');
  if (authError) return authError;

  try {
    const parsed = await readBoundedJsonBody(request, MAX_KILL_SWITCH_BODY_BYTES);
    if (!parsed.ok) {
      return NextResponse.json(
        { ok: false, code: parsed.reason === 'too_large' ? 'PAYLOAD_TOO_LARGE' : 'INVALID_REQUEST' },
        { status: parsed.reason === 'too_large' ? 413 : 400 },
      );
    }
    if (!isKillSwitchRequest(parsed.value)) {
      return NextResponse.json({ ok: false, code: 'INVALID_ACTION' }, { status: 400 });
    }
    const { action } = parsed.value;
    const reason = action === 'on' ? parsed.value.reason : undefined;

    const state = action === 'on'
      ? activateKillSwitch((reason as string | undefined)?.trim() || 'Manual dashboard override')
      : clearKillSwitch();
    const reread = readKillSwitchState();
    if (reread.state !== state.state || reread.state === 'UNKNOWN') {
      return NextResponse.json({ ok: false, code: 'KILL_SWITCH_VERIFICATION_FAILED', state: reread.state }, { status: 503 });
    }
    return NextResponse.json({ ok: true, ...reread, path: publicKillSwitchPath() });
  } catch {
    return NextResponse.json({ ok: false, code: 'KILL_SWITCH_WRITE_FAILED', state: 'UNKNOWN' }, { status: 503 });
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
