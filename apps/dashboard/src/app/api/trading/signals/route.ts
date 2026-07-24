import { NextResponse } from 'next/server';
import { getLatestReport, getDecisions } from '@/lib/trading/data';
import { controlApiUnavailableResponse } from '@/lib/trading/control-api';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const symbol = searchParams.get('symbol');

    const report = await getLatestReport();
    if (!report) {
      return NextResponse.json({ error: 'No reports found' }, { status: 404 });
    }

    let assets = report.assets;
    if (symbol) {
      assets = assets.filter(a => a.symbol === symbol);
      if (assets.length === 0) {
        return NextResponse.json({ error: 'Symbol not found' }, { status: 404 });
      }
    }

    // Enhance with recent decisions
    const decisions = await getDecisions(20);
    const decisionsBySymbol = decisions.reduce((acc, d) => {
      if (!acc[d.ticker]) {
        acc[d.ticker] = [];
      }
      acc[d.ticker].push(d);
      return acc;
    }, {} as Record<string, typeof decisions>);

    const enrichedAssets = assets.map(asset => ({
      ...asset,
      recentDecisions: decisionsBySymbol[asset.symbol] || [],
    }));

    return NextResponse.json({
      timestamp: report.timestamp,
      assets: enrichedAssets,
    });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
