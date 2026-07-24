import { NextResponse } from 'next/server';
import { getAllMarketData } from '@/lib/trading/data';
import { AssetClass } from '@/lib/trading/types';
import { controlApiUnavailableResponse } from '@/lib/trading/control-api';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const classParam = searchParams.get('class');
    const normalizedClass = classParam?.replace(/s$/, '') as AssetClass | undefined;

    const data = await getAllMarketData();

    const tickers = normalizedClass
      ? data.tickers.filter(t => t.asset_class === normalizedClass)
      : data.tickers;

    return NextResponse.json({
      timestamp: data.timestamp,
      tickers,
    });
  } catch {
    return controlApiUnavailableResponse();
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
