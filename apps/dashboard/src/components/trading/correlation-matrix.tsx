'use client';

import { useState, useEffect } from 'react';
import { GitBranch, AlertTriangle, CheckCircle } from 'lucide-react';

interface CorrelationPair {
  asset1: string;
  asset2: string;
  correlation: number;
  status: 'safe' | 'warning' | 'danger';
}

interface SectorExposure {
  sector: string;
  exposure_pct: number;
  limit_pct: number;
  assets: string[];
  is_over_limit: boolean;
}

interface CorrelationData {
  symbols: string[];
  matrix: number[][];
  threshold: number;
  high_correlation_pairs: CorrelationPair[];
  sector_exposures: SectorExposure[];
  calculated_at: string;
}

function corrColor(val: number): string {
  if (val >= 0.85) return 'bg-red-900/60 text-red-300';
  if (val >= 0.7)  return 'bg-amber-900/50 text-amber-300';
  if (val >= 0.5)  return 'bg-zinc-800 text-zinc-300';
  return 'bg-zinc-900 text-zinc-500';
}

function statusColor(status: CorrelationPair['status']) {
  if (status === 'danger')  return 'text-red-400';
  if (status === 'warning') return 'text-amber-400';
  return 'text-emerald-400';
}

export function CorrelationMatrix() {
  const [data, setData] = useState<CorrelationData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/correlation')
      .then(r => r.json())
      .then(d => { if (!d.error) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="rounded-none border border-zinc-800 bg-zinc-900/50 p-4 text-center">
        <p className="text-xs text-zinc-500 font-mono">Loading correlation data…</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-none border border-zinc-800 bg-zinc-900/50 p-4 text-center">
        <p className="text-xs text-zinc-500 font-mono">No correlation data available</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Correlation Matrix Grid */}
      <div className="border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
          <GitBranch className="h-3 w-3 text-amber-500" />
          <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">
            Correlation Matrix
          </span>
          <span className="ml-auto text-[9px] text-zinc-600 font-mono">
            threshold: {data.threshold}
          </span>
        </div>

        <div className="p-3 overflow-x-auto">
          <table className="text-[10px] font-mono border-collapse">
            <thead>
              <tr>
                <th className="w-10" />
                {data.symbols.map(sym => (
                  <th key={sym} className="px-2 py-1 text-zinc-500 font-bold text-center w-14">
                    {sym}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.matrix.map((row, i) => (
                <tr key={data.symbols[i]}>
                  <td className="pr-2 py-0.5 text-zinc-400 font-bold text-right">
                    {data.symbols[i]}
                  </td>
                  {row.map((val, j) => (
                    <td key={j} className={`px-1 py-0.5 text-center tabular-nums ${corrColor(i === j ? -1 : val)}`}>
                      {i === j ? '—' : val.toFixed(2)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-2 flex items-center gap-3 text-[9px] text-zinc-600">
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 bg-red-900/60" /> ≥0.85 Danger
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 bg-amber-900/50" /> ≥0.70 Warning
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 bg-zinc-800" /> ≥0.50 Moderate
            </span>
          </div>
        </div>
      </div>

      {/* High Correlation Pairs */}
      {data.high_correlation_pairs.length > 0 && (
        <div className="border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
            <AlertTriangle className="h-3 w-3 text-amber-500" />
            <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">
              High Correlation Pairs
            </span>
          </div>
          <div className="divide-y divide-zinc-800/50">
            {data.high_correlation_pairs.map(pair => (
              <div key={`${pair.asset1}-${pair.asset2}`}
                className="px-3 py-2 flex items-center gap-3 text-xs font-mono">
                <span className="text-zinc-200 font-bold">{pair.asset1}</span>
                <span className="text-zinc-600">↔</span>
                <span className="text-zinc-200 font-bold">{pair.asset2}</span>
                <span className={`ml-auto font-bold tabular-nums ${statusColor(pair.status)}`}>
                  {pair.correlation.toFixed(2)}
                </span>
                <span className={`text-[9px] uppercase font-bold border px-1 py-0.5 ${
                  pair.status === 'danger'
                    ? 'text-red-400 border-red-800'
                    : 'text-amber-400 border-amber-800'
                }`}>
                  {pair.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sector Exposures */}
      {data.sector_exposures.length > 0 && (
        <div className="border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
            <CheckCircle className="h-3 w-3 text-amber-500" />
            <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">
              Sector Exposure
            </span>
          </div>
          <div className="divide-y divide-zinc-800/50">
            {data.sector_exposures.map(sector => (
              <div key={sector.sector} className="px-3 py-2 space-y-1.5">
                <div className="flex items-center gap-2 text-xs font-mono">
                  <span className="text-zinc-300 font-bold">{sector.sector}</span>
                  <span className="text-zinc-600 text-[9px]">{sector.assets.join(' · ')}</span>
                  <span className={`ml-auto font-bold tabular-nums ${
                    sector.is_over_limit ? 'text-red-400' : 'text-emerald-400'
                  }`}>
                    {sector.exposure_pct}%
                  </span>
                  <span className="text-zinc-600 text-[9px]">/ {sector.limit_pct}% limit</span>
                  {sector.is_over_limit && (
                    <span className="text-[9px] uppercase font-bold border border-red-800 text-red-400 px-1 py-0.5">
                      OVER
                    </span>
                  )}
                </div>
                {/* Bar */}
                <div className="h-1 bg-zinc-800 w-full">
                  <div
                    className={`h-1 ${sector.is_over_limit ? 'bg-red-500' : 'bg-amber-500'}`}
                    style={{ width: `${Math.min(sector.exposure_pct / sector.limit_pct * 100, 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
