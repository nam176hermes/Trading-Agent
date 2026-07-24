import { NextResponse } from 'next/server';
import { authorizeMutation, checkAuth } from '@/lib/trading/auth';

function unavailable() {
  return NextResponse.json(
    {
      ok: false,
      code: 'PROCESS_EXECUTION_DISABLED',
      message: 'Service inspection and control are unavailable from the dashboard.',
    },
    { status: 503 },
  );
}

export async function GET(request: Request) {
  const authError = checkAuth(request);
  return authError ?? unavailable();
}

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'service.control', 'MUTATION_EXECUTION_SENSITIVE', 'admin');
  return authError ?? unavailable();
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
