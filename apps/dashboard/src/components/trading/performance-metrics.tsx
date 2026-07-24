'use client';

import { useCallback, useEffect, useState } from 'react';

interface EquityPoint {
  timestamp: string;
  equity: number;
  label: string;
}

interface EquityCurveMetrics {
  peak: number;
  current: number;
  drawdown: number;
  drawdownPct: number;
  totalOrders: number;
}

interface EquityCurveData {
  points: EquityPoint[];
  metrics: EquityCurveMetrics;
}

interface OrderEntry {
  pnl: number | null;
  side: string;
}

const INITIAL_CAPITAL = 100_000;

export function PerformanceMetrics({ initialData, initialWinRate }: { initialData?: EquityCurveData | null; initialWinRate?: number | null }) {
  const hasServerData = initialData !== undefined;
  const [data, setData] = useState<EquityCurveData | null>(initialData ?? null);
  const [winRate, setWinRate] = useState<number | null>(initialWinRate ?? null);
  const [loading, setLoading] = useState(!hasServerData);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (hasServerData) return;
    try {
      setError(null);
      setLoading(true);
      const [eqRes, ordersRes] = await Promise.all([
        fetch('/api/trading/equity-curve'),
        fetch('/api/trading/orders'),
      ]);
      const eq = await eqRes.json();
      setData(eq.error ? null : eq);

      if (ordersRes.ok) {
        const ordersData = await ordersRes.json();
        const orders: OrderEntry[] = ordersData.orders ?? [];
        const withPnl = orders.filter(o => o.pnl != null);
        if (withPnl.length > 0) {
          const wins = withPnl.filter(o => (o.pnl ?? 0) > 0).length;
          setWinRate(Math.round((wins / withPnl.length) * 100));
        }
      }
    } catch {
      setError('Failed to load equity curve');
    } finally {
      setLoading(false);
    }
  }, [hasServerData]);

  useEffect(() => {
    const initialFetch = setTimeout(fetchData, 0);
    return () => clearTimeout(initialFetch);
  }, [fetchData]);

  if (error) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
        <p className="text-sm text-red-400 mb-2">{error}</p>
        <button onClick={fetchData} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
        <p className="text-sm text-zinc-400">Loading equity curve...</p>
      </div>
    );
  }

  if (!data || !data.points || data.points.length < 2) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
        <p className="text-sm text-zinc-400">No trade history yet. Equity curve will appear after the first order.</p>
      </div>
    );
  }

  const { points, metrics } = data;
  const hasCurrent = points.some(p => p.label === 'current');

  // Format the date for the x-axis label
  const firstTs = points[0]?.timestamp ? new Date(points[0].timestamp) : null;
  const dateLabel = firstTs
    ? `${firstTs.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} · ${firstTs.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`
    : '';

  const ddColor = metrics.drawdownPct >= -1 ? 'text-green-400' : metrics.drawdownPct >= -5 ? 'text-amber-400' : 'text-red-400';

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-zinc-100">Equity Curve</h2>

      {/* SVG Sparkline */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <EquitySparkline points={points} />
        {dateLabel && (
          <div className="mt-2 flex justify-between text-[10px] text-zinc-500">
            <span>{dateLabel}</span>
            <span>{hasCurrent ? 'live' : ''}</span>
          </div>
        )}
      </div>

      {/* Metrics summary */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
        <MetricCard label="Total P&amp;L" value={`${metrics.current >= INITIAL_CAPITAL ? '+' : ''}$${(metrics.current - INITIAL_CAPITAL).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`} className={metrics.current >= INITIAL_CAPITAL ? 'text-green-400' : 'text-red-400'} />
        <MetricCard label="Peak" value={`$${metrics.peak.toLocaleString()}`} />
        <MetricCard label="Current" value={`$${metrics.current.toLocaleString()}`} />
        <MetricCard label="Drawdown" value={`$${Math.abs(metrics.drawdown).toLocaleString()} (${Number.isFinite(Number(metrics.drawdownPct)) ? Number(metrics.drawdownPct).toFixed(2) : '—'}%)`} className={ddColor} />
        <MetricCard label="Trades" value={String(metrics.totalOrders)} />
        <MetricCard label="Win Rate" value={winRate != null ? `${winRate}%` : '—'} className={winRate != null && winRate >= 50 ? 'text-green-400' : winRate != null ? 'text-red-400' : 'text-zinc-500'} />
      </div>
    </div>
  );
}

function MetricCard({ label, value, className = 'text-zinc-100' }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-xl font-bold font-mono ${className}`}>{value}</p>
    </div>
  );
}

function EquitySparkline({ points }: { points: EquityPoint[] }) {
  const equities = points.map(p => p.equity);
  const min = Math.min(...equities);
  const max = Math.max(...equities);
  const range = max - min || 1;
  const height = 120;
  const width = 100;
  const padding = 8;

  const polylinePoints = points.map((p, i) => {
    const x = padding + (i / (points.length - 1)) * (width - padding * 2);
    const y = height - padding - ((p.equity - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  }).join(' ');

  const startEquity = points[0]?.equity ?? 0;
  const endEquity = points[points.length - 1]?.equity ?? 0;
  const strokeColor = endEquity >= startEquity ? '#22c55e' : '#ef4444';

  // Y-axis labels (top and bottom)
  const topLabel = `$${Number.isFinite(Number(max)) ? (Number(max) / 1000).toFixed(1) : '—'}k`;
  const bottomLabel = `$${Number.isFinite(Number(min)) ? (Number(min) / 1000).toFixed(1) : '—'}k`;

  // Dot indicators for each order point (skip start and current for dots)
  const currentPoint = points.find(p => p.label === 'current');

  return (
    <div>
      <div className="flex">
        {/* Y-axis labels */}
        <div className="flex flex-col justify-between pr-2 text-[10px] font-mono text-zinc-500" style={{ height }}>
          <span>{topLabel}</span>
          <span>{bottomLabel}</span>
        </div>

        {/* Chart */}
        <div className="flex-1 relative">
          <svg viewBox={`0 0 ${width} ${height}`} className="w-full" preserveAspectRatio="xMidYMid meet" style={{ height }}>
            {/* Grid lines */}
            <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="#27272a" strokeWidth="0.5" />
            <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#27272a" strokeWidth="0.5" />

            {/* Polyline */}
            <polyline
              points={polylinePoints}
              fill="none"
              stroke={strokeColor}
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />

            {/* Gradient fill under the line */}
            <defs>
              <linearGradient id={`equityGradient-${startEquity}-${endEquity}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={strokeColor} stopOpacity="0.15" />
                <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
              </linearGradient>
            </defs>
            <polygon
              points={`${polylinePoints} ${width - padding},${height - padding} ${padding},${height - padding}`}
              fill={`url(#equityGradient-${startEquity}-${endEquity})`}
            />

            {/* Order dots */}
            {points.map((p, i) => {
              if (p.label === 'current') return null;
              const x = padding + (i / (points.length - 1)) * (width - padding * 2);
              const y = height - padding - ((p.equity - min) / range) * (height - padding * 2);
              return (
                <circle
                  key={i}
                  cx={x}
                  cy={y}
                  r="2"
                  fill={strokeColor}
                  stroke="#18181b"
                  strokeWidth="1"
                />
              );
            })}

            {/* Current point marker */}
            {currentPoint && (() => {
              const i = points.indexOf(currentPoint);
              const x = padding + (i / (points.length - 1)) * (width - padding * 2);
              const y = height - padding - ((currentPoint.equity - min) / range) * (height - padding * 2);
              return (
                <circle
                  cx={x}
                  cy={y}
                  r="3"
                  fill={strokeColor}
                  stroke="#18181b"
                  strokeWidth="1.5"
                />
              );
            })()}
          </svg>
        </div>
      </div>

      {/* Dollar labels below */}
      <div className="mt-1 flex justify-between text-[10px] font-mono text-zinc-500">
        <span>${startEquity.toLocaleString()}</span>
        <span>${endEquity.toLocaleString()}</span>
      </div>
    </div>
  );
}
