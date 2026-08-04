import { NextResponse } from 'next/server';
import { controlApiUnavailableResponse, getControlCanonicalMarketData } from '@/lib/trading/control-api';

export async function GET() {
  try {
    return NextResponse.json(await getControlCanonicalMarketData(), {
      headers: { 'cache-control': 'no-store' },
    });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
