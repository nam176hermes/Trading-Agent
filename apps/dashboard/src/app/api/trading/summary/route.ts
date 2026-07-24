import { NextResponse } from 'next/server';

import { controlApiUnavailableResponse, getControlDecisions, getControlMarket } from '@/lib/trading/control-api';

export async function GET() {
  try {
    const [market, decisions] = await Promise.all([
      getControlMarket(),
      getControlDecisions('page=1&page_size=1'),
    ]);
    const report = market.data.report;
    const assets = report?.assets ?? [];
    const rank: Record<string, number> = { high: 3, medium: 2, low: 1 };
    return NextResponse.json({
      reportDate: report?.as_of ?? null,
      signalCount: assets.filter((asset) => asset.suggestion !== 'NO_SIGNAL').length,
      assetCount: assets.length,
      latestDecisions: decisions.data.total,
      topAssets: [...assets]
        .sort((left, right) => (rank[right.confidence] ?? 0) - (rank[left.confidence] ?? 0))
        .slice(0, 3)
        .map((asset) => ({
          symbol: asset.symbol,
          suggestion: asset.suggestion.replaceAll('_', ' '),
          confidence: asset.confidence,
          riskLevel: asset.risk_assessment.risk_level,
          price: asset.current_price,
        })),
      btcPrice: assets.find((asset) => asset.symbol === 'BTC')?.current_price ?? null,
      ethPrice: assets.find((asset) => asset.symbol === 'ETH')?.current_price ?? null,
    }, { headers: { 'cache-control': 'no-store' } });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
