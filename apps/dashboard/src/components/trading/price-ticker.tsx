'use client';

import { useEffect, useState } from 'react';
import { subscribePrices, PricePayload } from '@/lib/trading/price-stream';
import {
  DisplayPriceData,
  DisplayPrices,
  getPriceFlashClass,
  mergePriceUpdates,
  TickerPriceData,
} from '@/lib/trading/price-ticker-state';

interface HealthData {
  ws_connected: boolean;
  stream_count: number;
  rest_latency_ms: number;
  last_health_check: string;
}

const TICKER_ORDER = ['BTC', 'ETH', 'SOL', 'TON', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'MATIC'];

function getHealthStatus(health: HealthData): { color: string; label: string } {
  if (!health.ws_connected && health.rest_latency_ms === Infinity) {
    return { color: 'bg-red-500', label: 'EXCH DOWN' };
  }
  if (!health.ws_connected) {
    return { color: 'bg-amber-500', label: 'WS DISCONN' };
  }
  if (health.rest_latency_ms > 2000) {
    return { color: 'bg-amber-500', label: `REST ${Math.round(health.rest_latency_ms)}MS` };
  }
  if (health.rest_latency_ms < 500) {
    return { color: 'bg-emerald-500', label: `${health.stream_count} STREAMS` };
  }
  return { color: 'bg-amber-500', label: `REST ${Math.round(health.rest_latency_ms)}MS` };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isHealthData(value: unknown): value is HealthData {
  return isRecord(value)
    && typeof value.ws_connected === 'boolean'
    && typeof value.stream_count === 'number'
    && typeof value.rest_latency_ms === 'number'
    && typeof value.last_health_check === 'string';
}

function isPriceData(value: unknown): value is TickerPriceData {
  return isRecord(value)
    && typeof value.price === 'number';
}

export function PriceTicker() {
  const [prices, setPrices] = useState<DisplayPrices>({});
  const [health, setHealth] = useState<HealthData | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    return subscribePrices((payload: PricePayload) => {
      if (isHealthData(payload._health)) setHealth(payload._health);
      const priceEntries = Object.entries(payload).filter(
        (entry): entry is [string, TickerPriceData] => entry[0] !== '_health' && isPriceData(entry[1]),
      );
      if (priceEntries.length > 0) {
        setPrices(prev => mergePriceUpdates(prev, priceEntries));
        setError(false);
      }
    });
  }, []);

  const sortedSymbols = Object.keys(prices).sort((a, b) => {
    const ai = TICKER_ORDER.indexOf(a);
    const bi = TICKER_ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  const healthStatus = health ? getHealthStatus(health) : null;

  if (error && sortedSymbols.length === 0) return null;

  return (
    <div className="w-full border-b border-zinc-800 bg-zinc-950 overflow-hidden relative h-7 flex items-center">
      {sortedSymbols.length === 0 ? (
        <span className="px-4 text-[9px] text-zinc-600 uppercase tracking-widest whitespace-nowrap">
          {error ? 'PRICE FEED DISCONNECTED — RECONNECTING...' : 'INITIALISING PRICE FEED...'}
        </span>
      ) : (
        <div className="ticker-animate">
          {/* Duplicate for seamless infinite loop */}
          {[...sortedSymbols, ...sortedSymbols].map((sym, i) => (
            <TickerItem
              key={`${sym}-${i}`}
              symbol={sym}
              data={prices[sym]}
              duplicate={i >= sortedSymbols.length}
            />
          ))}
        </div>
      )}

      {/* Health indicator — fades in from right */}
      {healthStatus && (
        <div className="absolute right-0 top-0 bottom-0 flex items-center gap-1.5 pl-10 pr-3
                        bg-gradient-to-l from-zinc-950 via-zinc-950/90 to-transparent z-10 pointer-events-none">
          <span className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${healthStatus.color} ${
            healthStatus.color === 'bg-emerald-500' ? 'status-live' :
            healthStatus.color === 'bg-red-500'     ? 'animate-pulse' : ''
          }`} />
          <span className="text-[9px] text-zinc-500 uppercase tracking-widest whitespace-nowrap">
            {healthStatus.label}
          </span>
        </div>
      )}
    </div>
  );
}

function TickerItem({
  symbol,
  data,
  duplicate,
}: {
  symbol: string;
  data: DisplayPriceData;
  duplicate: boolean;
}) {
  const price = data?.price ?? 0;
  const change = data.change_pct_24h ?? 0;
  const isUp = change >= 0;
  const flashClass = getPriceFlashClass(data, duplicate);

  const formatPrice = (p: number): string => {
    if (p >= 1000) return p.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (p >= 1)    return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return p.toPrecision(4);
  };

  return (
    <div className="flex items-center whitespace-nowrap shrink-0">
      <div className="flex items-center gap-1.5 px-3">
        <span className="text-[9px] font-bold text-amber-400 tracking-wider">{symbol}</span>
        <span className={`text-[10px] font-mono text-zinc-100 tabular-nums ${flashClass}`}>
          ${formatPrice(price)}
        </span>
        <span className={`text-[9px] font-mono tabular-nums ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
          {isUp ? '▲' : '▼'}{Number.isFinite(Number(change)) ? Math.abs(Number(change)).toFixed(2) : '—'}%
        </span>
      </div>
      <span className="text-zinc-800 text-[10px] select-none">│</span>
    </div>
  );
}
