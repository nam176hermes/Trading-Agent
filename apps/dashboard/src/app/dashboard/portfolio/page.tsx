'use client';

import { useState, useEffect } from 'react';
import { BarChart3 } from 'lucide-react';
import { PortfolioCard } from '@/components/trading/portfolio-card';
import { PnlTrackerCard } from '@/components/trading/pnl-tracker-card';

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

export default function PortfolioPage() {
  const [equityData, setEquityData] = useState<{
    points: EquityPoint[];
    metrics: EquityCurveMetrics;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const TIMEOUT_MS = 10_000;

    fetch('/api/trading/equity-curve', {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(TIMEOUT_MS)]),
    })
      .then(r => r.json())
      .then(d => { if (!d.error) setEquityData(d); })
      .catch((err) => {
        if (err.name !== 'AbortError') console.error('equity-curve fetch failed:', err);
        setEquityData(null);
      });

    return () => controller.abort();
  }, []);

  return (
    <div className="p-3 md:p-6 space-y-4">
      <div>
        <h1 className="text-lg font-bold text-zinc-100">Portfolio</h1>
        <p className="text-xs text-zinc-500">Paper portfolio tracking — positions, P&amp;L, and equity curve</p>
      </div>

      {/* Equity Curve */}
      {equityData && (
        <EquityCurveChart points={equityData.points} metrics={equityData.metrics} />
      )}

      {/* P&L Tracker */}
      <PnlTrackerCard />

      {/* Portfolio Positions Card */}
      <PortfolioCard />
    </div>
  );
}

/* ── Equity Curve SVG Chart ── */
function EquityCurveChart({ points, metrics }: {
  points: EquityPoint[];
  metrics: EquityCurveMetrics;
}) {
  if (points.length < 2) return null;

  const W = 800;
  const H = 200;
  const PAD_L = 50;
  const PAD_R = 20;
  const PAD_T = 12;
  const PAD_B = 24;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  const equities = points.map(p => p.equity);
  const minE = Math.min(...equities);
  const maxE = Math.max(...equities);
  const range = maxE - minE || 1;

  const scaleX = (i: number) => PAD_L + (i / (points.length - 1)) * chartW;
  const scaleY = (v: number) => PAD_T + chartH - ((v - minE) / range) * chartH;

  const pathD = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${scaleX(i)},${scaleY(p.equity)}`)
    .join(' ');

  const startEquity = points[0]?.equity ?? 100_000;
  const changePct = ((metrics.current - startEquity) / startEquity) * 100;
  const changeColor = changePct >= 0 ? 'text-green-400' : 'text-red-400';

  // Y-axis labels
  const yTicks = 4;
  const yLabels = Array.from({ length: yTicks }, (_, i) => {
    const v = minE + (range / (yTicks - 1)) * i;
    return { v, y: scaleY(v) };
  });

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center gap-2">
        <BarChart3 className="h-4 w-4 text-violet-400" />
        <span className="text-xs font-bold text-zinc-200">Equity Curve</span>
        <span className={`text-[10px] font-mono ml-auto ${changeColor}`}>
          {Number(changePct) >= 0 ? '+' : ''}{Number.isFinite(Number(changePct)) ? Number(changePct).toFixed(2) : '—'}%
        </span>
      </div>

      {/* Chart */}
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" style={{ minWidth: 400 }}>
          {/* Grid lines */}
          {yLabels.map(({ v, y }) => (
            <g key={v}>
              <line x1={PAD_L} y1={y} x2={W - PAD_R} y2={y}
                stroke="#27272a" strokeWidth={0.5} />
              <text x={PAD_L - 4} y={y + 4} textAnchor="end"
                className="text-[8px]" fill="#52525b" fontFamily="monospace">
                ${Number.isFinite(Number(v)) ? (Number(v) / 1000).toFixed(1) : '—'}k
              </text>
            </g>
          ))}

          {/* Equity line */}
          <path d={pathD} fill="none" stroke={changePct >= 0 ? '#4ade80' : '#f87171'}
            strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />

          {/* Area fill */}
          <path d={`${pathD} L${scaleX(points.length - 1)},${scaleY(minE)} L${scaleX(0)},${scaleY(minE)} Z`}
            fill={changePct >= 0 ? 'url(#greenGrad)' : 'url(#redGrad)'} opacity={0.3} />

          <defs>
            <linearGradient id="greenGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4ade80" stopOpacity={0.4} />
              <stop offset="100%" stopColor="#4ade80" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="redGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f87171" stopOpacity={0.4} />
              <stop offset="100%" stopColor="#f87171" stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* Tooltip dots (first/last/peak) */}
          {[
            { i: 0, label: 'Start', color: '#a1a1aa' },
            { i: points.length - 1, label: 'Now', color: changePct >= 0 ? '#4ade80' : '#f87171' },
            { i: equities.indexOf(maxE), label: 'Peak', color: '#a78bfa' },
          ].map(({ i, label, color }) => {
            const p = points[i];
            return (
              <g key={label}>
                <circle cx={scaleX(i)} cy={scaleY(p.equity)} r={3} fill={color} />
                <text x={scaleX(i)} y={scaleY(p.equity) - 6} textAnchor="middle"
                  className="text-[8px]" fill={color} fontFamily="monospace">
                  ${Number.isFinite(Number(p.equity)) ? (Number(p.equity) / 1000).toFixed(1) : '—'}k
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-4 gap-px bg-zinc-800">
        <MetricBox label="Peak" value={`${Number.isFinite(Number(metrics.peak)) ? '$' + (Number(metrics.peak) / 1000).toFixed(1) + 'k' : '—'}`} />
        <MetricBox label="Current" value={Number.isFinite(Number(metrics.current)) ? '$' + (Number(metrics.current) / 1000).toFixed(1) + 'k' : '—'}
          color={metrics.current >= 100_000 ? 'text-green-400' : 'text-red-400'} />
        <MetricBox label="Drawdown" value={Number.isFinite(Number(metrics.drawdown)) ? '-$' + Math.abs(Number(metrics.drawdown)).toFixed(0) : '—'}
          color="text-red-400" sub={Number.isFinite(Number(metrics.drawdownPct)) ? Number(metrics.drawdownPct).toFixed(2) + '%' : '—'} />
        <MetricBox label="Trades" value={String(metrics.totalOrders)} />
      </div>
    </div>
  );
}

function MetricBox({ label, value, color, sub }: {
  label: string;
  value: string;
  color?: string;
  sub?: string;
}) {
  return (
    <div className="bg-zinc-900/70 px-3 py-2 text-center">
      <p className="text-[9px] text-zinc-500 uppercase">{label}</p>
      <p className={`text-sm font-bold font-mono ${color || 'text-zinc-200'}`}>{value}</p>
      {sub && <p className={`text-[9px] font-mono ${color}`}>{sub}</p>}
    </div>
  );
}
