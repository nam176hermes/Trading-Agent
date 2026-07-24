'use client';

import { useEffect, useState } from 'react';
import type { FundamentalAsset } from '@/lib/trading/types';

interface Props {
  symbol: string;
}

function fmtB(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1000) return `$${(n / 1000).toFixed(2)}T`;
  return `$${n.toFixed(1)}B`;
}

function fmtRatio(n: number | null | undefined): string {
  if (n == null) return '—';
  return n.toFixed(2);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${(n * 100).toFixed(1)}%`;
}

function betaLabel(beta: number | null | undefined): { label: string; color: string } {
  if (beta == null) return { label: 'N/A', color: 'text-zinc-500' };
  if (beta < 0.8) return { label: 'Low', color: 'text-green-400' };
  if (beta <= 1.5) return { label: 'Medium', color: 'text-amber-400' };
  return { label: 'High', color: 'text-red-400' };
}

function daysUntil(dateStr: string | null): number | null {
  if (!dateStr) return null;
  const target = new Date(dateStr);
  const now = new Date();
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

export function FundamentalsCard({ symbol }: Props) {
  const [data, setData] = useState<FundamentalAsset | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/trading/fundamentals?symbol=${symbol}`)
      .then(r => r.json())
      .then(d => {
        if (d.error) setData(null);
        else setData(d);
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [symbol]);

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 animate-pulse">
        <div className="h-4 w-24 bg-zinc-800 rounded mb-3" />
        <div className="space-y-2">
          <div className="h-3 w-full bg-zinc-800 rounded" />
          <div className="h-3 w-2/3 bg-zinc-800 rounded" />
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-xs text-zinc-500">No fundamentals data for {symbol}</p>
      </div>
    );
  }

  const { profile, valuation, financials, earnings } = data;
  const beta = betaLabel(profile.beta);
  const days = daysUntil(earnings.nextDate);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 w-full max-w-[300px] transition-colors hover:border-zinc-700">
      {/* Header */}
      <div className="mb-3">
        <h3 className="text-lg font-bold text-zinc-100">{profile.companyName || symbol}</h3>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xs text-zinc-400">{symbol}</span>
          {profile.sector && (
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-300">
              {profile.sector}
            </span>
          )}
        </div>
        <p className="text-xs text-zinc-500 mt-1">
          Market Cap: {fmtB(profile.marketCap)} · Beta: <span className={beta.color}>{beta.label} ({profile.beta?.toFixed(2) ?? '—'})</span>
        </p>
      </div>

      {/* Valuation Metrics */}
      <div className="mb-3 grid grid-cols-2 gap-2">
        <Metric label="P/E" value={fmtRatio(valuation.peRatio)} />
        <Metric label="P/B" value={fmtRatio(valuation.pbRatio)} />
        <Metric label="EV/EBITDA" value={fmtRatio(valuation.evToEbitda)} />
        <Metric label="ROE" value={valuation.roe != null ? `${(valuation.roe * 100).toFixed(0)}%` : '—'} />
        <Metric label="D/E" value={fmtRatio(valuation.debtToEquity)} />
        <Metric label="Curr. Ratio" value={fmtRatio(valuation.currentRatio)} />
      </div>

      {/* Growth Indicators */}
      <div className="mb-3 rounded bg-zinc-800/30 p-2.5 space-y-1.5">
        <GrowthRow label="Revenue Growth" value={fmtPct(financials.revenueGrowth)} delta={financials.revenueGrowth} />
        <GrowthRow label="Earnings Growth" value={fmtPct(financials.earningsGrowth)} delta={financials.earningsGrowth} />
        <div className="flex justify-between text-[10px]">
          <span className="text-zinc-400">Gross Margin</span>
          <span className="font-mono text-zinc-200">{financials.grossMargin != null ? `${(financials.grossMargin * 100).toFixed(1)}%` : '—'}</span>
        </div>
        <div className="flex justify-between text-[10px]">
          <span className="text-zinc-400">Net Margin</span>
          <span className="font-mono text-zinc-200">{financials.netMargin != null ? `${(financials.netMargin * 100).toFixed(1)}%` : '—'}</span>
        </div>
      </div>

      {/* Latest Quarter */}
      <div className="mb-3 text-[10px] text-zinc-500">
        <span>Q Rev: {financials.latestQuarterRevenue != null ? fmtB(financials.latestQuarterRevenue) : '—'}</span>
        <span className="mx-2">|</span>
        <span>Q Earn: {financials.latestQuarterEarnings != null ? fmtB(financials.latestQuarterEarnings) : '—'}</span>
      </div>

      {/* Earnings Date */}
      {earnings.nextDate && (
        <div className="rounded bg-zinc-800/30 p-2">
          <p className="text-[10px] text-zinc-400">Next Earnings</p>
          <p className="text-xs font-mono text-zinc-100">{earnings.nextDate}</p>
          <div className="flex items-center justify-between mt-1">
            {days != null && (
              <span className={`text-[10px] font-medium ${days <= 7 ? 'text-amber-400' : days <= 14 ? 'text-zinc-300' : 'text-zinc-500'}`}>
                {days}d away
              </span>
            )}
            {earnings.estimatedEps != null && (
              <span className="text-[10px] font-mono text-zinc-300">EPS Est: ${earnings.estimatedEps.toFixed(2)}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-zinc-800/40 px-2 py-1.5">
      <p className="text-[10px] text-zinc-400">{label}</p>
      <p className="text-xs font-mono font-medium text-zinc-100">{value}</p>
    </div>
  );
}

function GrowthRow({ label, value, delta }: { label: string; value: string; delta: number | null }) {
  const color = delta == null ? 'text-zinc-400' : delta >= 0 ? 'text-green-400' : 'text-red-400';
  const arrow = delta == null ? '' : delta >= 0 ? '▲' : '▼';
  return (
    <div className="flex justify-between text-[10px]">
      <span className="text-zinc-400">{label}</span>
      <span className={`font-mono ${color}`}>{arrow} {value}</span>
    </div>
  );
}
