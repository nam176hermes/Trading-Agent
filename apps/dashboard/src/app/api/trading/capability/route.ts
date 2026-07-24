import { NextResponse } from 'next/server';

import { controlApiUnavailableResponse, getControlCapabilities } from '@/lib/trading/control-api';

export async function GET() {
  try {
    const response = await getControlCapabilities();
    const latest = response.data.items.reduce<string | null>((current, item) => {
      if (!item.last_run_at || current && current >= item.last_run_at) return current;
      return item.last_run_at;
    }, null);
    return NextResponse.json({
      capabilities: response.data.items.map((item) => ({
        name: item.name,
        icon: 'database',
        description: item.evidence_ref ?? 'No current benchmark evidence.',
        tools: [],
        status: item.status === 'PASS' ? 'verified' : item.status.toLowerCase(),
        lastTested: item.last_run_at?.slice(0, 10) ?? null,
      })),
      total: response.data.total,
      verified: response.data.verified,
      benchmark: {
        passed: response.data.verified,
        total: response.data.total,
        lastRun: latest,
      },
      summary: response.data.summary,
    }, { headers: { 'cache-control': 'no-store' } });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
