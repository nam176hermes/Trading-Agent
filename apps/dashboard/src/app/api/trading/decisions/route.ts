import { NextResponse } from 'next/server';
import { getDecisions, getDecisionsBySymbol } from '@/lib/trading/data';
import { AssetSymbol } from '@/lib/trading/types';
import { controlApiUnavailableResponse } from '@/lib/trading/control-api';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const symbol = searchParams.get('symbol') as AssetSymbol | null;
    const limit = parseInt(searchParams.get('limit') || '50', 10);

    let decisions;
    if (symbol) {
      decisions = await getDecisionsBySymbol(symbol, limit);
    } else {
      decisions = await getDecisions(limit);
    }

    return NextResponse.json({
      decisions,
      count: decisions.length,
    });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
