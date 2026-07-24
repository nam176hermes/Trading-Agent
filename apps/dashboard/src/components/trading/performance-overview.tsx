'use client';

import { useState, useEffect } from 'react';
import { BarChart3, TrendingDown, TrendingUp } from 'lucide-react';
import { Area, AreaChart, ResponsiveContainer, Tooltip, type TooltipValueType } from 'recharts';

interface EquityPoint {
  timestamp: string;
  equity: number;
  label: string;
}

interface EquityCurveData {
  points: EquityPoint[];
  metrics: {
    peak: number;
    current: number;
    drawdown: number;
    drawdownPct: number;
    totalOrders: number;
  };
}

export function PerformanceOverview() {
  const [data, setData] = useState<EquityCurveData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const TIMEOUT_MS = 10_000;

    fetch('/api/trading/equity-curve', {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(TIMEOUT_MS)]),
    })
      .then(r => r.json())
      .then(d => {
        if (!d.error) setData(d);
        else setError(true);
      })
      .catch((err) => {
        if (err.name !== 'AbortError') setError(true);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, []);

  const startEquity = 100_000;
  const pnl = data ? data.metrics.current - startEquity : 0;
  const pnlPct = (pnl / startEquity) * 100;
  const isPositive = pnl >= 0;

  // Loading skeleton
  if (loading) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 animate-pulse">
        <div className="flex items-center gap-2 mb-3">
          <div className="rounded-lg p-2 bg-zinc-800 w-9 h-9" />
          <div className="h-4 bg-zinc-800 rounded w-32" />
        </div>
        <div className="h-[200px] bg-zinc-800/50 rounded-lg" />
        <div className="grid grid-cols-4 gap-3 mt-3">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-14 bg-zinc-800/50 rounded-md" />
          ))}
        </div>
      </div>
    );
  }

  // Error state
  if (error || !data) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="rounded-lg p-2 bg-violet-500/10">
            <BarChart3 className="h-5 w-5 text-violet-400" />
          </div>
          <h3 className="text-sm font-bold text-zinc-100">Performance</h3>
        </div>
        <p className="text-xs text-zinc-500">
          Performance data unavailable — pipeline may be initializing.
        </p>
      </div>
    );
  }

  const chartData = data.points.map(p => ({
    time: new Date(p.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity: p.equity,
    label: p.label,
  }));

  const lineColor = isPositive ? '#4ade80' : '#f87171';
  const gradId = isPositive ? 'perfGreenGrad' : 'perfRedGrad';

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 overflow-hidden">
      {/* Header */}
      <div className="px-4 pt-4 pb-2 flex items-center gap-2">
        <div className="rounded-lg p-2 bg-violet-500/10">
          <BarChart3 className="h-5 w-5 text-violet-400" />
        </div>
        <div className="flex-1">
          <h3 className="text-sm font-bold text-zinc-100">Performance</h3>
          <p className="text-[11px] text-zinc-500">{data.metrics.totalOrders} trades · since May 13</p>
        </div>
        <div className={`flex items-center gap-1 text-sm font-bold font-mono ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
          {isPositive ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
          {isPositive ? '+' : ''}{Number.isFinite(Number(pnlPct)) ? Number(pnlPct).toFixed(2) : '—'}%
        </div>
      </div>

      {/* Chart */}
      <div className="px-2">
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={lineColor} stopOpacity={0.3} />
                <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Tooltip
              contentStyle={{
                background: '#18181b',
                border: '1px solid #27272a',
                borderRadius: '8px',
                fontSize: '12px',
                color: '#e4e4e7',
              }}
              labelStyle={{ color: '#a1a1aa', fontSize: '10px' }}
              formatter={(value: TooltipValueType | undefined) => {
                const n = typeof value === 'number' ? value : parseFloat(String(value ?? ''));
                return Number.isFinite(n) ? [`$${(n / 1000).toFixed(1)}k`, 'Equity'] : ['—', 'Equity'];
              }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={lineColor}
              strokeWidth={1.5}
              fill={`url(#${gradId})`}
              dot={false}
              activeDot={{ r: 3, fill: lineColor }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-4 gap-px bg-zinc-800">
        <MetricBox label="Peak" value={`$${Number.isFinite(Number(data.metrics.peak)) ? (Number(data.metrics.peak) / 1000).toFixed(1) : '—'}k`} />
        <MetricBox
          label="Current"
          value={`$${Number.isFinite(Number(data.metrics.current)) ? (Number(data.metrics.current) / 1000).toFixed(1) : '—'}k`}
          color={isPositive ? 'text-green-400' : 'text-red-400'}
        />
        <MetricBox
          label="P&L"
          value={`${isPositive ? '+' : ''}$${Number.isFinite(Number(pnl)) ? Number(pnl).toFixed(0) : '—'}`}
          color={isPositive ? 'text-green-400' : 'text-red-400'}
          sub={`${Number.isFinite(Number(pnlPct)) ? Number(pnlPct).toFixed(2) : '—'}%`}
        />
        <MetricBox
          label="Drawdown"
          value={`${Number.isFinite(Number(data.metrics.drawdownPct)) ? Number(data.metrics.drawdownPct).toFixed(1) : '—'}%`}
          color="text-amber-400"
        />
      </div>
    </div>
  );
}

function MetricBox({
  label,
  value,
  color,
  sub,
}: {
  label: string;
  value: string;
  color?: string;
  sub?: string;
}) {
  return (
    <div className="bg-zinc-900/70 px-3 py-2 text-center">
      <p className="text-[9px] text-zinc-500 uppercase">{label}</p>
      <p className={`text-sm font-bold font-mono ${color || 'text-zinc-200'}`}>{value}</p>
      {sub && <p className={`text-[9px] font-mono ${color || 'text-zinc-400'}`}>{sub}</p>}
    </div>
  );
}
