export interface TickerPriceData {
  price: number;
  change_pct_24h?: number;
  volume_24h?: number;
  high_24h?: number;
}

export interface DisplayPriceData extends TickerPriceData {
  previousPrice?: number;
}

export type DisplayPrices = Record<string, DisplayPriceData>;

export function mergePriceUpdates(
  previous: DisplayPrices,
  updates: Array<[string, TickerPriceData]>,
): DisplayPrices {
  const next = { ...previous };
  for (const [symbol, data] of updates) {
    next[symbol] = {
      ...data,
      previousPrice: previous[symbol]?.price,
    };
  }
  return next;
}

export function getPriceFlashClass(data: DisplayPriceData, duplicate: boolean): string {
  if (duplicate || data.previousPrice === undefined || data.price === data.previousPrice) return '';
  return data.price > data.previousPrice ? 'price-flash-up' : 'price-flash-down';
}
