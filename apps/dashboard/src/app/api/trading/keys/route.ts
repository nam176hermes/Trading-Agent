import { NextResponse } from 'next/server';
import { authorizeMutation, checkAuth } from '@/lib/trading/auth';

function unavailable() {
  return NextResponse.json(
    {
      ok: false,
      code: 'PROCESS_EXECUTION_DISABLED',
      message: 'Exchange credential operations are disabled in this dashboard.',
    },
    { status: 503 },
  );
}

export async function GET(request: Request) {
  const authError = checkAuth(request);
  return authError ?? unavailable();
}

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'keys.manage', 'SECRET_MANAGEMENT', 'admin');
  return authError ?? unavailable();
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
