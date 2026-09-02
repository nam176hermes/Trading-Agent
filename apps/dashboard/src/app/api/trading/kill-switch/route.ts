import { NextResponse } from 'next/server';
import { authorizeMutation } from '@/lib/trading/auth';
import { controlApiUnavailableResponse, getControlStatus } from '@/lib/trading/control-api';
import { submitKillSwitchActivation } from '@/lib/trading/operator-api';
import { readBoundedJsonBody } from '@/lib/trading/request-body';

const MAX_KILL_SWITCH_BODY_BYTES = 4 * 1024;

type KillSwitchRequest = { action: 'on'; reason: string; operation_id: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isKillSwitchRequest(value: unknown): value is KillSwitchRequest {
  if (!isObject(value)) return false;
  const body = value;
  const keys = Object.keys(body).sort();
  return keys.length === 3
    && keys[0] === 'action' && keys[1] === 'operation_id' && keys[2] === 'reason'
    && body.action === 'on'
    && typeof body.operation_id === 'string' && /^op_[0-9a-f]{32}$/.test(body.operation_id)
    && typeof body.reason === 'string' && body.reason === body.reason.trim()
    && body.reason.length >= 1 && body.reason.length <= 256 && !/[\r\n]/.test(body.reason);
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
    if (isObject(parsed.value) && parsed.value.action === 'off') {
      return NextResponse.json(
        { ok: false, code: 'CLI_REQUIRED', message: 'Clear the kill switch via CLI.' },
        { status: 403 },
      );
    }
    if (!isKillSwitchRequest(parsed.value)) {
      return NextResponse.json({ ok: false, code: 'INVALID_ACTION' }, { status: 400 });
    }
    const result = await submitKillSwitchActivation({
      operationId: parsed.value.operation_id,
      reason: parsed.value.reason,
    });
    let observationStatus: 'OBSERVED' | 'PENDING' | 'UNAVAILABLE' = 'UNAVAILABLE';
    try {
      const { data } = await getControlStatus();
      observationStatus = data.kill_switch_state === 'ACTIVE' ? 'OBSERVED' : 'PENDING';
    } catch {
      // The command receipt remains authoritative when observation is unavailable.
    }
    return NextResponse.json({
      ok: true,
      command_status: 'SUCCEEDED',
      observation_status: observationStatus,
      receipt: result.receipt,
      deduplicated: result.deduplicated,
    });
  } catch {
    return NextResponse.json(
      { ok: false, code: 'OPERATOR_API_UNAVAILABLE', message: 'Operator command service is unavailable.' },
      { status: 503 },
    );
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
