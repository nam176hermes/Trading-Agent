import { NextResponse } from 'next/server';

import {
  controlApiUnavailableResponse,
  getControlDecisions,
  getControlMarket,
  getControlStatus,
} from '@/lib/trading/control-api';

export async function GET() {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const [market, decisions, decisionsToday, system] = await Promise.all([
      getControlMarket(),
      getControlDecisions('page=1&page_size=1'),
      getControlDecisions(`page=1&page_size=1&date_from=${encodeURIComponent(`${today}T00:00:00Z`)}`),
      getControlStatus(),
    ]);
    const report = market.data.report;
    const minutesAgo = market.freshness?.age_seconds === null
      || market.freshness?.age_seconds === undefined
      ? null
      : Math.round(market.freshness.age_seconds / 60);
    const stubFree = report === null || !JSON.stringify(report).includes('[LLM STUB]')
      && !JSON.stringify(report).includes('[NO API KEY]');
    return NextResponse.json({
      status: system.data.api_readiness === 'READY' && report ? 'online' : 'offline',
      lastReportAt: report?.as_of ?? null,
      minutesAgo,
      assetCount: report?.assets.length ?? 0,
      totalDecisions: decisions.data.total,
      decisionsToday: decisionsToday.data.total,
      stubFree,
      pipeline: {
        dataFreshness: market.freshness?.status === 'FRESH'
          ? 'fresh'
          : market.freshness?.status === 'NO_DATA' ? 'none' : 'stale',
        llmWorking: null,
        assetsTracked: report?.assets.length ?? 0,
      },
    }, { headers: { 'cache-control': 'no-store' } });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
