import { NextResponse } from 'next/server';
import { authorizeMutation } from '@/lib/trading/auth';

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'position.close', 'MUTATION_EXECUTION_SENSITIVE', 'operator');
  if (authError) return authError;

  return NextResponse.json(
    {
      ok: false,
      code: 'PROCESS_EXECUTION_DISABLED',
      message: 'Position close commands are unavailable from the dashboard.',
    },
    { status: 503 },
  );
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
