export interface Order {
  timestamp: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  shares: number;
  fill_price: number;
  cost?: number;
  proceeds?: number;
  pnl?: number;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
  profit: number;
  profit_percent: number;
}

export function computeEquityCurve(
  orders: Order[],
  priceMap: Record<string, number>,
  initialCapital: number
): { curve: EquityPoint[]; peak: number; maxDrawdown: number } {
  const chrono = [...orders].reverse(); // oldest first
  let cash = initialCapital;
  const positions: Record<string, number> = {};
  let peak = initialCapital;
  let maxDrawdown = 0;
  const curve: EquityPoint[] = [];

  // Track last known fill price per symbol as we walk chronologically
  const historicalPrice: Record<string, number> = {};

  for (const o of chrono) {
    if (o.side === 'BUY') {
      cash -= o.cost ?? o.shares * o.fill_price;
      positions[o.symbol] = (positions[o.symbol] || 0) + o.shares;
    } else if (o.side === 'SELL') {
      cash += o.proceeds ?? o.shares * o.fill_price;
      positions[o.symbol] = (positions[o.symbol] || 0) - o.shares;
      if (positions[o.symbol] <= 0) delete positions[o.symbol];
    }

    // Record fill price as best-known price for this symbol at this moment
    historicalPrice[o.symbol] = o.fill_price;

    // Mark-to-market open positions using historical prices at this point in time
    let positionsValue = 0;
    for (const [sym, shares] of Object.entries(positions)) {
      const price = historicalPrice[sym] ?? priceMap[sym] ?? o.fill_price;
      positionsValue += shares * price;
    }

    const equity = cash + positionsValue;
    if (equity > peak) peak = equity;
    const dd = peak > 0 ? (peak - equity) / peak : 0;
    if (dd > maxDrawdown) maxDrawdown = dd;

    curve.push({
      timestamp: o.timestamp,
      equity: Math.round(equity * 100) / 100,
      profit: Math.round((equity - initialCapital) * 100) / 100,
      profit_percent: Math.round(((equity - initialCapital) / initialCapital) * 10000) / 100,
    });
  }

  return { curve, peak, maxDrawdown };
}
