'use client';

import { useEffect, useState } from 'react';

interface EarningsEntry {
  symbol: string;
  companyName: string;
  nextDate: string;
  estimatedEps: number | null;
}

interface EarningsData {
  timestamp: string;
  earnings: EarningsEntry[];
}

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr);
  const now = new Date();
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

function formatDateNice(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

export function EarningsTimeline() {
  const [data, setData] = useState<EarningsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/earnings')
      .then(r => r.json())
      .then(d => {
        if (d.error) setData(null);
        else setData(d);
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 animate-pulse">
        <div className="h-4 w-32 bg-zinc-800 rounded mb-3" />
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-8 bg-zinc-800 rounded mb-2" />
        ))}
      </div>
    );
  }

  if (!data || data.earnings.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-xs text-zinc-500">No upcoming earnings dates</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {data.earnings.map((entry, i) => {
        const days = daysUntil(entry.nextDate);
        const urgencyColor = days < 0
          ? 'text-zinc-600'
          : days <= 7
            ? 'text-red-400'
            : days <= 14
              ? 'text-amber-400'
              : 'text-zinc-400';

        return (
          <div key={i} className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
            {/* Timeline dot */}
            <div className="flex-shrink-0">
              <div className={`h-3 w-3 rounded-full border-2 ${
                days < 0
                  ? 'border-zinc-600 bg-transparent'
                  : days <= 7
                    ? 'border-red-500 bg-red-500/30'
                    : days <= 14
                      ? 'border-amber-500 bg-amber-500/30'
                      : 'border-zinc-500 bg-zinc-500/30'
              }`} />
            </div>

            {/* Date */}
            <div className="w-28 flex-shrink-0">
              <p className="text-xs font-mono text-zinc-200">{formatDateNice(entry.nextDate)}</p>
              <p className={`text-[10px] ${urgencyColor}`}>
                {days < 0 ? 'Past' : `${days}d`}
              </p>
            </div>

            {/* Connector line */}
            <div className="w-px h-6 bg-zinc-800 flex-shrink-0" />

            {/* Symbol + Company */}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-mono font-bold text-zinc-100">{entry.symbol}</p>
              <p className="text-[10px] text-zinc-500 truncate">{entry.companyName || entry.symbol}</p>
            </div>

            {/* EPS Estimate */}
            <div className="flex-shrink-0 text-right">
              {entry.estimatedEps != null ? (
                <>
                  <p className="text-xs font-mono text-zinc-200">${entry.estimatedEps.toFixed(2)}</p>
                  <p className="text-[10px] text-zinc-500">EPS Est</p>
                </>
              ) : (
                <p className="text-[10px] text-zinc-600">No est.</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
