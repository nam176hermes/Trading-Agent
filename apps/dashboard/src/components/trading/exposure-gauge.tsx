'use client';

import { useCallback, useEffect, useState } from 'react';
import { Gauge } from 'lucide-react';

const CAP_PCT = 50;

export function ExposureGauge({ initialData }: { initialData?: { exposure: number; totalCapital: number } }) {
  const hasServerData = initialData !== undefined;
  const [exposure, setExposure] = useState<number | null>(initialData?.exposure ?? null);
  const [totalCapital, setTotalCapital] = useState<number | null>(initialData?.totalCapital ?? null);
  const [loading, setLoading] = useState(!hasServerData);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (hasServerData) {
      setLoading(false);
      return;
    }
    try {
      setError(null);
      const res = await fetch('/api/trading/portfolio');
      if (res.ok) {
        const json = await res.json();
        setExposure(json.exposure ?? 0);
        setTotalCapital(json.totalCapital ?? null);
      } else {
        setError('Failed to load exposure data');
      }
    } catch {
      setError('Failed to load exposure data');
    } finally {
      setLoading(false);
    }
  }, [hasServerData]);

  useEffect(() => {
    let alive = true;
    const wrapped = async () => { if (alive) await fetchData(); };
    const initialFetch = setTimeout(wrapped, 0);
    const interval = setInterval(wrapped, 30_000);
    return () => {
      alive = false;
      clearTimeout(initialFetch);
      clearInterval(interval);
    };
  }, [fetchData]);

  if (error && exposure === null) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-3">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={fetchData} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <span className="text-sm">Loading exposure...</span>
        </div>
      </div>
    );
  }

  const exposurePct = exposure ?? 0;
  const barFill = Math.min(exposurePct / CAP_PCT, 1);
  const breached = exposurePct > CAP_PCT;

  const zoneColor = breached
    ? 'text-red-400'
    : exposurePct >= 30
      ? 'text-yellow-400'
      : exposurePct > 0
        ? 'text-green-400'
        : 'text-zinc-500';

  const statusLabel = breached
    ? 'CAP BREACHED — trading halted'
    : exposurePct >= 30
      ? 'Warning: approaching cap'
      : exposurePct > 0
        ? `${Number.isFinite(Number(exposurePct)) ? Number(exposurePct).toFixed(1) : '—'}% of ${CAP_PCT}% cap`
        : 'No positions';

  return (
    <div className={`rounded-lg border ${
      breached ? 'border-red-500/50 bg-red-500/10' :
      exposurePct >= 30 ? 'border-yellow-500/30 bg-yellow-500/5' :
      'border-zinc-800 bg-zinc-900/50'
    } p-4`}>
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${
            breached ? 'bg-red-500/20' :
            exposurePct >= 30 ? 'bg-yellow-500/20' :
            exposurePct > 0 ? 'bg-green-500/20' :
            'bg-zinc-800'
          }`}>
            <Gauge className={`h-4 w-4 ${zoneColor}`} />
          </div>
          <div>
            <p className="text-sm font-bold text-zinc-200">Exposure</p>
            <p className="text-[10px] text-zinc-500">{statusLabel}</p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-[10px] text-zinc-500">Invested</p>
          <p className={`text-sm font-mono font-bold ${zoneColor}`}>
            {Number.isFinite(Number(exposurePct)) ? Number(exposurePct).toFixed(1) : '—'}%
          </p>
        </div>
      </div>

      {/* Gauge Bar */}
      <div className="mb-2">
        <div className="flex items-center justify-between text-[10px] text-zinc-500 mb-1">
          <span>0%</span>
          <span>Cap: {CAP_PCT}%</span>
        </div>

        {/* Gradient background bar */}
        <div className="relative h-3 bg-zinc-800 rounded-full overflow-hidden">
          {/* Green zone background */}
          <div className="absolute inset-0 rounded-full"
            style={{
              background: 'linear-gradient(to right, #22c55e 0%, #22c55e 60%, #eab308 60%, #eab308 100%)',
              opacity: 0.3,
            }}
          />
          {/* Filled portion */}
          <div
            className={`absolute inset-y-0 left-0 rounded-full transition-all duration-500 ${
              breached ? 'animate-pulse' : ''
            }`}
            style={{
              width: `${Math.min(barFill * 100, 100)}%`,
              background: breached
                ? '#ef4444'
                : `linear-gradient(to right, #22c55e, #eab308, #ef4444)`,
              backgroundSize: '100% 100%',
            }}
          />
          {/* Cap marker at 50% */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white/40"
            style={{ left: '100%' }}
          />
        </div>

        {/* Zone labels */}
        <div className="flex justify-between mt-1">
          <span className="text-[10px] text-green-500/60">Green (&lt;30%)</span>
          <span className="text-[10px] text-yellow-500/60">Yellow (30-50%)</span>
          <span className="text-[10px] text-red-500/60">Red (&gt;50%)</span>
        </div>
      </div>

      {/* Breached Banner */}
      {breached && (
        <div className="rounded bg-red-500/10 border border-red-500/20 p-2">
          <p className="text-[10px] text-red-400">
            CAP BREACHED — Total exposure exceeds the {CAP_PCT}% limit. All new positions blocked until exposure is reduced.
          </p>
        </div>
      )}

      {/* Normal state detail */}
      {!breached && totalCapital != null && (
        <div className="rounded bg-zinc-800/30 p-2">
          <p className="text-[10px] text-zinc-500">
            Capital: ${totalCapital.toLocaleString()} — {CAP_PCT}% max allocation (${(totalCapital * CAP_PCT / 100).toLocaleString()})
          </p>
        </div>
      )}
    </div>
  );
}
