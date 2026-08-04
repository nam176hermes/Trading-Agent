'use client';

import { useCallback, useEffect, useState } from 'react';

import { parseCanonicalMarketTickerView } from '@/lib/trading/market-data-view';
import type { CanonicalMarketTickerView } from '@/lib/trading/market-data-view';

function abbreviatedDigest(digest: string): string {
  return `${digest.slice(0, 12)}…${digest.slice(-8)}`;
}

function knownAtLabel(knownAt: string): string {
  const parsed = new Date(knownAt);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString() : knownAt;
}

export function MarketTicker() {
  const [view, setView] = useState<CanonicalMarketTickerView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMarketData = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/trading/market', { cache: 'no-store' });
      if (!response.ok) throw new Error('Canonical market data is unavailable.');
      const parsed = parseCanonicalMarketTickerView(await response.json());
      if (parsed === null) throw new Error('Canonical market data response is invalid.');
      setView(parsed);
      setError(null);
    } catch {
      // Do not retain a prior quote and accidentally present it as current.
      setView(null);
      setError('Canonical market data is unavailable. No fallback quote is shown.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialFetch = setTimeout(fetchMarketData, 0);
    const interval = setInterval(fetchMarketData, 30_000);
    return () => {
      clearTimeout(initialFetch);
      clearInterval(interval);
    };
  }, [fetchMarketData]);

  if (loading && view === null && error === null) {
    return (
      <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
        <p className="text-sm text-zinc-400">Loading canonical market data…</p>
      </div>
    );
  }

  if (error !== null) {
    return (
      <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
        <div className="flex items-center gap-3">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={fetchMarketData} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
        </div>
      </div>
    );
  }

  if (view?.kind === 'no_data') {
    return (
      <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
        <p className="text-sm text-amber-300">
          No canonical BTC fixture snapshot is persisted yet. No price is shown.
        </p>
      </div>
    );
  }

  if (view?.kind !== 'snapshot') {
    return (
      <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
        <p className="text-sm text-red-400">Canonical market data is unavailable. No fallback quote is shown.</p>
      </div>
    );
  }

  const stale = view.freshness === 'STALE';
  return (
    <div className="border-b border-zinc-800 bg-zinc-950/50 px-6 py-2">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="rounded border border-orange-500/30 bg-orange-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-orange-400">
            P10 CANONICAL
          </span>
          <span className="text-sm font-medium text-zinc-100">BTC · FIXTURE</span>
          <span className="text-sm font-mono text-zinc-300">Close {view.close}</span>
          <span className={`text-xs font-semibold ${stale ? 'text-amber-300' : 'text-green-400'}`}>
            {view.freshness}
          </span>
          {stale && <span className="text-xs text-amber-300">Source is outside its freshness window.</span>}
        </div>
        <span className="text-xs text-zinc-500">Known: {knownAtLabel(view.knownAt)}</span>
      </div>
      <p className="mt-1 text-[11px] text-zinc-500">
        Provider: {view.provider} · evidence {abbreviatedDigest(view.evidenceDigest)} · snapshot {abbreviatedDigest(view.snapshotDigest)}
      </p>
    </div>
  );
}
