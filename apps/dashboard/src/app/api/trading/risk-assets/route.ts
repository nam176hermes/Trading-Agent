import { NextResponse } from 'next/server';
import { getLatestReport } from '@/lib/trading/data';
import { controlApiUnavailableResponse } from '@/lib/trading/control-api';

export async function GET() {
  try {
    const report = await getLatestReport();
    const assets = report?.assets ?? [];
    return NextResponse.json({ assets, total: assets.length });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
