'use client';

import { useEffect, useState } from 'react';
import type { FundamentalsReport } from '@/lib/trading/types';

function peColor(pe: number | null): string {
  if (pe == null) return 'text-zinc-500';
  if (pe < 15) return 'text-green-400';
  if (pe <= 25) return 'text-amber-400';
  return 'text-red-400';
}

function pbColor(pb: number | null): string {
  if (pb == null) return 'text-zinc-500';
  if (pb < 1.5) return 'text-green-400';
  if (pb <= 3) return 'text-amber-400';
  return 'text-red-400';
}

function roeColor(roe: number | null): string {
  if (roe == null) return 'text-zinc-500';
  if (roe > 0.2) return 'text-green-400';
  if (roe >= 0.1) return 'text-amber-400';
  return 'text-red-400';
}

function deColor(de: number | null): string {
  if (de == null) return 'text-zinc-500';
  if (de < 1) return 'text-green-400';
  if (de <= 2) return 'text-amber-400';
  return 'text-red-400';
}

function fmtRatio(n: number | null): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  return n.toFixed(2);
}

function interpretPE(pe: number | null): string {
  if (pe == null) return 'No data';
  if (pe < 15) return 'Cheap';
  if (pe <= 25) return 'Fair';
  return 'Expensive';
}

function interpretPB(pb: number | null): string {
  if (pb == null) return 'No data';
  if (pb < 1.5) return 'Cheap';
  if (pb <= 3) return 'Fair';
  return 'Expensive';
}

export function ValuationGrid() {
  const [report, setReport] = useState<FundamentalsReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/fundamentals')
      .then(r => r.json())
      .then(d => {
        if (d.error) setReport(null);
        else setReport(d);
      })
      .catch(() => setReport(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 animate-pulse">
        <div className="h-4 w-32 bg-zinc-800 rounded mb-3" />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-16 bg-zinc-800 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (!report || report.assets.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-xs text-zinc-500">No valuation data available</p>
      </div>
    );
  }

  const assets = report.assets;

  return (
    <div className="space-y-4">
      {/* P/E Grid */}
      <div>
        <h4 className="mb-2 text-xs font-bold uppercase tracking-wider text-zinc-400">P/E Ratio</h4>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {assets.map(a => (
            <ValuationTile
              key={a.symbol}
              symbol={a.symbol}
              value={fmtRatio(a.valuation.peRatio)}
              interpretation={interpretPE(a.valuation.peRatio)}
              color={peColor(a.valuation.peRatio)}
            />
          ))}
        </div>
      </div>

      {/* P/B Grid */}
      <div>
        <h4 className="mb-2 text-xs font-bold uppercase tracking-wider text-zinc-400">P/B Ratio</h4>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {assets.map(a => (
            <ValuationTile
              key={a.symbol}
              symbol={a.symbol}
              value={fmtRatio(a.valuation.pbRatio)}
              interpretation={interpretPB(a.valuation.pbRatio)}
              color={pbColor(a.valuation.pbRatio)}
            />
          ))}
        </div>
      </div>

      {/* ROE Grid */}
      <div>
        <h4 className="mb-2 text-xs font-bold uppercase tracking-wider text-zinc-400">Return on Equity</h4>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {assets.map(a => (
            <ValuationTile
              key={a.symbol}
              symbol={a.symbol}
              value={a.valuation.roe != null ? `${(a.valuation.roe * 100).toFixed(1)}%` : '—'}
              interpretation={a.valuation.roe != null ? (a.valuation.roe > 0.2 ? 'Strong' : a.valuation.roe >= 0.1 ? 'OK' : 'Weak') : 'No data'}
              color={roeColor(a.valuation.roe)}
            />
          ))}
        </div>
      </div>

      {/* Debt/Equity Grid */}
      <div>
        <h4 className="mb-2 text-xs font-bold uppercase tracking-wider text-zinc-400">Debt / Equity</h4>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {assets.map(a => (
            <ValuationTile
              key={a.symbol}
              symbol={a.symbol}
              value={fmtRatio(a.valuation.debtToEquity)}
              interpretation={a.valuation.debtToEquity != null ? (a.valuation.debtToEquity < 1 ? 'Low' : a.valuation.debtToEquity <= 2 ? 'Medium' : 'High') : 'No data'}
              color={deColor(a.valuation.debtToEquity)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ValuationTile({ symbol, value, interpretation, color }: { symbol: string; value: string; interpretation: string; color: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
      <p className="text-xs font-mono font-medium text-zinc-300">{symbol}</p>
      <p className={`text-lg font-bold ${color}`}>{value}</p>
      <p className="text-[10px] text-zinc-500">{interpretation}</p>
    </div>
  );
}
