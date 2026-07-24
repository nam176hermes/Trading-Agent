import { NextResponse } from 'next/server';
import { checkAuth } from '@/lib/trading/auth';

export async function GET(request: Request) {
  const authError = checkAuth(request);
  if (authError) return authError;

  return NextResponse.json(
    {
      ok: false,
      code: 'PROCESS_EXECUTION_DISABLED',
      message: 'Execution data is unavailable until an exact Control API contract is provided.',
    },
    { status: 503 },
  );
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
