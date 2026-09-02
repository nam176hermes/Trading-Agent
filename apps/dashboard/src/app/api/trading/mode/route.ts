import { NextRequest, NextResponse } from 'next/server';
import { authorizeMutation, checkAuth } from '@/lib/trading/auth';
import { getControlStatus, controlApiUnavailableResponse } from '@/lib/trading/control-api';

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
  return NextResponse.json(
    { ok: false, code: 'CLI_REQUIRED', message: 'Change mode via CLI.' },
    { status: 403 },
  );
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
