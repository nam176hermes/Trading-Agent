'use client';

import { useState, useEffect } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { Award, AlertTriangle, CheckCircle2, XCircle, Download, GitCompareArrows, Activity, BarChart3 } from 'lucide-react';
import { BenchmarkComparison } from '@/components/trading/benchmark-comparison';

interface EquityPoint { timestamp: string; equity: number; label?: string; }
interface EquityCurveData {
  points: EquityPoint[];
  metrics: { peak: number; current: number; drawdown: number; drawdownPct: number; totalOrders: number };
}
interface Phase1Checks {
  'sharpe>1.5': boolean; 'win_rate>55%': boolean; 'max_drawdown<15%': boolean;
  'monthly_return>3%': boolean; 'trades>=50': boolean;
}
interface PerformanceMetrics {
  total_return_pct: number; sharpe: number; sortino: number; max_drawdown_pct: number;
  win_rate_pct: number; profit_factor: number | null;
  avg_win: number; avg_loss: number; closed_trades: number; open_trades: number; equity_snapshots: number;
}
interface Phase1Data {
  metrics: PerformanceMetrics; checks: Phase1Checks; phase1_pass: boolean;
  rolling_30d_return_pct?: number;
}

interface SlippageData {
  avg: number; p50: number; p95: number; recommended: number; samples: number;
}
interface ExportData {
  stats: Record<string, number | null>;
  slippage: SlippageData;
  rolling_30d_return_pct: number;
  gate_status: string;
  generated_at: string;
}

interface ReconciliationDrift {
  backtest: number; live: number; delta: number; status: string;
}
interface ReconciliationData {
  status: string;
  overall_drift: string;
  backtest: Record<string, number>;
  live: Record<string, number>;
  drift: Record<string, ReconciliationDrift>;
  recommendations: string[];
  backtest_last_run?: string;
}

function fmt(n: number | null | undefined, dec = 2) {
  if (n == null) return '—';
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
function fmtPct(n: number | null | undefined) {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${fmt(n)}%`;
}
function fmtDate(iso: string) {
  try { return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
  catch { return iso.slice(5, 10); }
}

function MetricCard({ label, value, sub, good }: {
  label: string; value: string; sub?: string; good?: boolean;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 px-3 py-3 flex flex-col gap-1.5">
      <span className="text-[9px] text-zinc-600 uppercase tracking-widest">{label}</span>
      <span className={`text-xl font-bold tabular-nums font-mono leading-none ${
        good === true ? 'text-emerald-400' : good === false ? 'text-red-400' : 'text-zinc-100'
      }`}>
        {value}
      </span>
      {sub && <span className="text-[9px] text-zinc-600 uppercase tracking-widest">{sub}</span>}
    </div>
  );
}

function CheckRow({ label, pass }: { label: string; pass: boolean }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-zinc-800 last:border-0">
      <span className="text-[10px] text-zinc-400 uppercase tracking-widest">{label}</span>
      {pass
        ? <CheckCircle2 size={14} className="text-emerald-400" />
        : <XCircle size={14} className="text-red-400" />}
    </div>
  );
}

export default function PerformancePage() {
  const [curve, setCurve] = useState<EquityCurveData | null>(null);
  const [phase1, setPhase1] = useState<Phase1Data | null>(null);
  const [exportData, setExportData] = useState<ExportData | null>(null);
  const [reconciliation, setReconciliation] = useState<ReconciliationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const [curveRes, phase1Res, exportRes, reconRes] = await Promise.all([
          fetch('/api/trading/equity-curve'),
          fetch('/api/trading/performance'),
          fetch('/api/trading/performance-export?format=json'),
          fetch('/api/trading/reconciliation'),
        ]);
        if (curveRes.ok) setCurve(await curveRes.json());
        if (phase1Res.ok) setPhase1(await phase1Res.json());
        if (exportRes.ok) setExportData(await exportRes.json());
        if (reconRes.ok) setReconciliation(await reconRes.json());
      } catch (e) {
        console.error('performance load error', e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function downloadReport() {
    setDownloading(true);
    try {
      const res = await fetch('/api/trading/performance-export?format=markdown');
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `novatrade-performance-${new Date().toISOString().split('T')[0]}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-500 text-sm">
        Loading performance data…
      </div>
    );
  }

  const m = phase1?.metrics;
  const checks = phase1?.checks;
  const points = curve?.points ?? [];
  const INITIAL = 100_000;
  const passCount = checks ? Object.values(checks).filter(Boolean).length : 0;

  const chartData = points.map(p => ({
    date: fmtDate(p.timestamp),
    equity: p.equity,
  }));

  return (
    <div className="space-y-4 p-4 md:p-6">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3 border-b border-zinc-800 pb-3">
        <div>
          <h1 className="text-sm font-bold text-zinc-100 uppercase tracking-widest">PERFORMANCE</h1>
          <p className="text-[10px] text-zinc-600 uppercase tracking-wider mt-0.5">
            PHASE-1 METRICS · EQUITY CURVE · TRADE STATS · RECONCILIATION
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {exportData && (
            <div className={`flex items-center gap-1.5 px-2.5 py-1 text-[9px] font-bold uppercase tracking-widest border ${
              exportData.gate_status === 'aligned'
                ? 'bg-emerald-900/20 text-emerald-400 border-emerald-800'
                : exportData.gate_status === 'fail'
                ? 'bg-red-900/20 text-red-400 border-red-900'
                : 'bg-amber-900/20 text-amber-400 border-amber-800'
            }`}>
              <Activity size={10} />
              GATE: {exportData.gate_status === 'aligned' ? 'PASS' : exportData.gate_status === 'fail' ? 'BLOCKING' : exportData.gate_status.toUpperCase()}
            </div>
          )}
          {phase1 && (
            <div className={`flex items-center gap-1.5 px-2.5 py-1 text-[9px] font-bold uppercase tracking-widest border ${
              phase1.phase1_pass
                ? 'bg-emerald-900/20 text-emerald-400 border-emerald-800'
                : 'bg-red-900/20 text-red-400 border-red-900'
            }`}>
              {phase1.phase1_pass
                ? <><Award size={10} /> PHASE-1 PASS</>
                : <><AlertTriangle size={10} /> FAILING ({passCount}/5)</>}
            </div>
          )}
          <button
            onClick={downloadReport}
            disabled
            title="Performance export is unavailable from the dashboard process"
            className="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-[9px] uppercase tracking-widest transition-colors disabled:opacity-50 border border-zinc-700"
          >
            <Download size={10} />
            {downloading ? 'GENERATING…' : 'EXPORT REPORT'}
          </button>
        </div>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-px bg-zinc-800 border border-zinc-800">
        <MetricCard label="Total Return" value={fmtPct(m?.total_return_pct)}
          good={m != null ? m.total_return_pct > 0 : undefined} />
        <MetricCard label="30d Return" value={fmtPct(phase1?.rolling_30d_return_pct)}
          sub="rolling window"
          good={phase1?.rolling_30d_return_pct != null ? phase1.rolling_30d_return_pct > 3 : undefined} />
        <MetricCard label="Sharpe" value={fmt(m?.sharpe)}
          good={m != null ? m.sharpe > 1.5 : undefined} />
        <MetricCard label="Sortino" value={fmt(m?.sortino)}
          good={m != null ? m.sortino > 1.0 : undefined} />
        <MetricCard label="Max Drawdown"
          value={fmtPct(m?.max_drawdown_pct != null ? -m.max_drawdown_pct : null)}
          good={m != null ? m.max_drawdown_pct < 15 : undefined} />
        <MetricCard label="Win Rate" value={m ? `${fmt(m.win_rate_pct)}%` : '—'}
          good={m != null ? m.win_rate_pct > 55 : undefined} />
      </div>

      {/* Equity curve */}
      <div className="bg-zinc-900 border border-zinc-800">
        <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
          <Activity size={11} className="text-violet-400" />
          <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">EQUITY CURVE</span>
        </div>
        <div className="p-4">
          {chartData.length < 2 ? (
            <div className="flex items-center justify-center h-48 text-zinc-600 text-[10px] uppercase tracking-widest">
              NO EQUITY DATA — RUN THE PIPELINE TO GENERATE ENTRIES
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1c1c1c" />
                <XAxis dataKey="date" tick={{ fill: '#555555', fontSize: 10, fontFamily: 'JetBrains Mono' }} tickLine={false} />
                <YAxis
                  tick={{ fill: '#555555', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                />
                <Tooltip
                  contentStyle={{ backgroundColor: '#0f0f0f', border: '1px solid #1c1c1c', borderRadius: 0, fontFamily: 'JetBrains Mono', fontSize: 11 }}
                  labelStyle={{ color: '#555555' }}
                  formatter={(v) => [`$${Number(v ?? 0).toLocaleString()}`, 'Equity']}
                />
                <ReferenceLine y={INITIAL} stroke="#282828" strokeDasharray="4 4" />
                <Area type="monotone" dataKey="equity" stroke="#9966ff" strokeWidth={1.5}
                  fill="url(#eqGrad)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
          {curve?.metrics && (
            <div className="mt-3 flex flex-wrap gap-4 text-[9px] text-zinc-600 uppercase tracking-widest">
              <span>PEAK: <span className="text-zinc-300 font-mono">${curve.metrics.peak.toLocaleString()}</span></span>
              <span>CURRENT: <span className="text-zinc-300 font-mono">${curve.metrics.current.toLocaleString()}</span></span>
              <span className={curve.metrics.drawdownPct < 0 ? 'text-red-400' : 'text-emerald-400'}>
                DRAWDOWN: {fmtPct(curve.metrics.drawdownPct)}
              </span>
              <span>ORDERS: <span className="text-zinc-300 font-mono">{curve.metrics.totalOrders}</span></span>
            </div>
          )}
        </div>
      </div>

      {/* Phase-1 checks + trade stats */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-zinc-900 border border-zinc-800">
          <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
            <Award size={11} className="text-amber-400" />
            <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">PHASE-1 CHECKS</span>
          </div>
          <div className="px-4 py-2">
            {checks ? (
              <div>
                <CheckRow label="Sharpe > 1.5" pass={checks['sharpe>1.5']} />
                <CheckRow label="Win rate > 55%" pass={checks['win_rate>55%']} />
                <CheckRow label="Max drawdown < 15%" pass={checks['max_drawdown<15%']} />
                <CheckRow label="Rolling 30d return > 3%" pass={checks['monthly_return>3%']} />
                <CheckRow label="At least 50 closed trades" pass={checks['trades>=50']} />
              </div>
            ) : (
              <p className="text-[10px] text-zinc-600 py-3 uppercase tracking-widest">No phase-1 data available.</p>
            )}
          </div>
        </div>

        <div className="bg-zinc-900 border border-zinc-800">
          <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
            <BarChart3 size={11} className="text-blue-400" />
            <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">TRADE STATS</span>
          </div>
          <div className="px-4 py-3 space-y-2">
            {([
              ['Closed trades', fmt(m?.closed_trades, 0)],
              ['Open trades',   fmt(m?.open_trades, 0)],
              ['Avg win',       m?.avg_win  != null ? `$${fmt(m.avg_win)}`  : '—'],
              ['Avg loss',      m?.avg_loss != null ? `-$${fmt(m.avg_loss)}` : '—'],
              ['Profit factor', fmt(m?.profit_factor)],
              ['Equity snapshots', fmt(m?.equity_snapshots, 0)],
            ] as [string, string][]).map(([label, value]) => (
              <div key={label} className="flex justify-between">
                <span className="text-[9px] text-zinc-600 uppercase tracking-widest">{label}</span>
                <span className="text-[10px] text-zinc-200 tabular-nums font-mono">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Slippage Analysis */}
      {exportData?.slippage && (
        <div className="bg-zinc-900 border border-zinc-800">
          <div className="px-3 py-2 border-b border-zinc-800">
            <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">SLIPPAGE ANALYSIS</span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-zinc-800">
            {([
              ['Avg Realised', exportData.slippage.avg != null ? `${(exportData.slippage.avg * 100).toFixed(4)}%` : '—'],
              ['Median (p50)', exportData.slippage.p50 != null ? `${(exportData.slippage.p50 * 100).toFixed(4)}%` : '—'],
              ['Tail (p95)',   exportData.slippage.p95 != null ? `${(exportData.slippage.p95 * 100).toFixed(4)}%` : '—'],
              ['Recommended',  exportData.slippage.recommended != null ? `${(exportData.slippage.recommended * 100).toFixed(3)}%` : '—'],
            ] as [string, string][]).map(([label, value]) => (
              <div key={label} className="bg-zinc-900 px-3 py-3">
                <p className="text-[9px] text-zinc-600 uppercase tracking-widest mb-1.5">{label}</p>
                <p className="text-lg font-bold tabular-nums font-mono text-zinc-100">{value}</p>
              </div>
            ))}
          </div>
          <div className="px-3 py-2 border-t border-zinc-800">
            <p className="text-[9px] text-zinc-600 uppercase tracking-widest">
              Based on {exportData.slippage.samples} orders · Recommended = avg × 1.5
            </p>
          </div>
        </div>
      )}

      {/* Backtest-to-Live Reconciliation */}
      {reconciliation && (
        <div className="bg-zinc-900 border border-zinc-800">
          <div className="px-3 py-2 border-b border-zinc-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <GitCompareArrows size={11} className="text-violet-400" />
              <span className="text-[9px] font-bold text-zinc-400 uppercase tracking-widest">
                BACKTEST VS LIVE RECONCILIATION
              </span>
            </div>
            <span className={`text-[9px] px-2 py-0.5 border font-bold uppercase tracking-widest ${
              reconciliation.overall_drift === 'aligned'
                ? 'bg-emerald-900/20 text-emerald-400 border-emerald-800'
                : reconciliation.overall_drift === 'significant_drift'
                ? 'bg-red-900/20 text-red-400 border-red-900'
                : 'bg-amber-900/20 text-amber-400 border-amber-800'
            }`}>
              {reconciliation.overall_drift?.replace('_', ' ') ?? '—'}
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-zinc-800">
                  <th className="text-left px-4 py-2 text-[9px] text-zinc-600 uppercase tracking-widest font-medium">Metric</th>
                  <th className="text-right px-4 py-2 text-[9px] text-zinc-600 uppercase tracking-widest font-medium">Backtest</th>
                  <th className="text-right px-4 py-2 text-[9px] text-zinc-600 uppercase tracking-widest font-medium">Live</th>
                  <th className="text-right px-4 py-2 text-[9px] text-zinc-600 uppercase tracking-widest font-medium">Δ</th>
                  <th className="text-right px-4 py-2 text-[9px] text-zinc-600 uppercase tracking-widest font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(reconciliation.drift ?? {}).map(([key, d]) => (
                  <tr key={key} className="border-b border-zinc-800/50 last:border-0">
                    <td className="px-4 py-2 text-[10px] text-zinc-400 uppercase tracking-widest">{key.replace('_', ' ')}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-[10px] font-mono text-zinc-300">{fmt(d.backtest)}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-[10px] font-mono text-zinc-300">{fmt(d.live)}</td>
                    <td className={`px-4 py-2 text-right tabular-nums text-[10px] font-mono ${d.delta >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {d.delta >= 0 ? '+' : ''}{fmt(d.delta)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <span className={`text-[9px] px-1.5 py-0.5 uppercase tracking-widest border ${
                        d.status === 'aligned' ? 'bg-emerald-900/20 text-emerald-400 border-emerald-800'
                        : d.status === 'significant_drift' ? 'bg-red-900/20 text-red-400 border-red-900'
                        : 'bg-amber-900/20 text-amber-400 border-amber-800'
                      }`}>
                        {d.status?.replace('_', ' ')}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {reconciliation.recommendations?.length > 0 && (
            <div className="border-t border-zinc-800 px-4 py-3 space-y-2">
              <p className="text-[9px] text-zinc-600 uppercase tracking-widest font-medium mb-2">Recommendations</p>
              {reconciliation.recommendations.map((rec, i) => (
                <div key={i} className="flex gap-2 text-[10px] text-zinc-400 bg-zinc-800/30 border border-zinc-800 p-2.5">
                  <AlertTriangle size={11} className="text-amber-400 mt-0.5 shrink-0" />
                  {rec}
                </div>
              ))}
            </div>
          )}

          {reconciliation.backtest_last_run && (
            <div className="border-t border-zinc-800 px-4 py-2">
              <p className="text-[9px] text-zinc-600 uppercase tracking-widest">
                Backtest last run: {new Date(reconciliation.backtest_last_run).toLocaleString()}
                · Live: {reconciliation.live?.total_trades ?? '—'} trades
                · Backtest: {reconciliation.backtest?.total_trades ?? '—'} trades
              </p>
            </div>
          )}
        </div>
      )}

      {/* Strategy vs Benchmarks */}
      <BenchmarkComparison />

    </div>
  );
}
