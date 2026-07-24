'use client';

import { useCallback, useEffect, useState } from 'react';
import { MarketTicker as MarketTickerType, AssetClass } from '@/lib/trading/types';
import { formatCurrency, formatPercentage } from '@/lib/trading/utils';

const ASSET_CLASS_STYLES: Record<AssetClass, { badge: string; label: string }> = {
  crypto: { badge: 'bg-orange-500/20 text-orange-400 border-orange-500/30', label: 'CRYPTO' },
  stock: { badge: 'bg-blue-500/20 text-blue-400 border-blue-500/30', label: 'STOCK' },
  etf: { badge: 'bg-green-500/20 text-green-400 border-green-500/30', label: 'ETF' },
  forex: { badge: 'bg-purple-500/20 text-purple-400 border-purple-500/30', label: 'FX' },
};

export function MarketTicker() {
  const [tickers, setTickers] = useState<MarketTickerType[]>([]);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchTickers = useCallback(async () => {
    try {
      setError(null);
      const response = await fetch('/api/trading/market');
      if (response.ok) {
        const data = await response.json();
        if (data.tickers) {
          setTickers(data.tickers);
          setLastUpdate(typeof data.timestamp === 'string' ? new Date(data.timestamp) : null);
        }
      } else {
        setError('Failed to load market data');
      }
    } catch {
      setError('Failed to load market data');
    }
  }, []);

  useEffect(() => {
    const initialFetch = setTimeout(fetchTickers, 0);
    const interval = setInterval(fetchTickers, 30000);
    return () => {
      clearTimeout(initialFetch);
      clearInterval(interval);
    };
  }, [fetchTickers]);

  if (error && tickers.length === 0 && !lastUpdate) {
    return (
      <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
        <div className="flex items-center gap-3">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={fetchTickers} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
        </div>
      </div>
    );
  }

  if (tickers.length === 0) {
    return (
      <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
        <p className="text-sm text-zinc-400">Loading market data...</p>
      </div>
    );
  }

  return (
    <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
      <div className="flex items-center justify-between">
        <div className="flex flex-wrap gap-6">
          {tickers.map((ticker) => {
            const cls = ticker.asset_class ? ASSET_CLASS_STYLES[ticker.asset_class] : null;
            return (
              <div key={ticker.symbol} className="flex items-center gap-2">
                {cls && (
                  <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls.badge}`}>
                    {cls.label}
                  </span>
                )}
                <span className="text-sm font-medium text-zinc-100">{ticker.symbol}</span>
                <span className="text-sm font-mono text-zinc-300">
                  {formatCurrency(ticker.price)}
                </span>
                <span
                  className={`text-sm font-medium ${
                    (ticker.change24h ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'
                  }`}
                >
                  {formatPercentage(ticker.change24h)}
                </span>
              </div>
            );
          })}
        </div>

        {lastUpdate && (
          <span className="text-xs text-zinc-500">
            Updated: {lastUpdate.toLocaleTimeString()}
          </span>
        )}
      </div>
    </div>
  );
}
