'use client';

import { useState, useEffect, useMemo } from 'react';
import { BarChart3, ChevronDown, ChevronUp, Trophy, TrendingUp } from 'lucide-react';

interface BenchmarkComparison {
  name: string;
  sharpe_ratio: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  win_rate?: number;
  category: 'model' | 'baseline';
}

interface ApiOptimizationResult {
  timestamp: string;
  sharpe_ratio: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  win_rate: number;
  parameters: Record<string, number>;
}

interface OptimizerState {
  is_running: boolean;
  last_optimized_at: string | null;
  lookback_days: number;
  parameters: Array<{
    name: string;
    current_value: number;
    default_value: number;
    unit: string;
    is_optimized: boolean;
    improvement_pct: number | null;
  }>;
  best_result: ApiOptimizationResult | null;
  next_run_at: string | null;
  benchmarks: BenchmarkComparison[];
}

type RowData = {
  name: string;
  sharpe_ratio: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  win_rate?: number;
  category: 'model' | 'baseline' | 'strategy';
};

export function BenchmarkComparison() {
  const [data, setData] = useState<OptimizerState | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch('/api/trading/optimizer');
        if (res.ok) {
          const json: OptimizerState = await res.json();
          setData(json);
        }
      } catch {
        // keep existing data
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 60000);
    return () => clearInterval(interval);
  }, []);

  const allRows = useMemo((): RowData[] => {
    const rows: RowData[] = [];

    if (data?.best_result) {
      rows.push({
        name: 'Your Strategy',
        sharpe_ratio: data.best_result.sharpe_ratio,
        total_return_pct: data.best_result.total_return_pct,
        max_drawdown_pct: data.best_result.max_drawdown_pct,
        win_rate: data.best_result.win_rate,
        category: 'strategy',
      });
    }

    for (const b of data?.benchmarks ?? []) {
      rows.push({
        name: b.name,
        sharpe_ratio: b.sharpe_ratio,
        total_return_pct: b.total_return_pct,
        max_drawdown_pct: b.max_drawdown_pct,
        win_rate: b.win_rate,
        category: b.category,
      });
    }

    rows.sort((a, b) => b.sharpe_ratio - a.sharpe_ratio);
    return rows;
  }, [data]);

  const bestValues = useMemo(() => {
    if (allRows.length === 0) return null;
    return {
      sharpe: Math.max(...allRows.map((r) => r.sharpe_ratio)),
      return_: Math.max(...allRows.map((r) => r.total_return_pct)),
      drawdown: Math.min(...allRows.map((r) => r.max_drawdown_pct)),
      winRate: Math.max(...allRows.filter((r) => r.win_rate != null).map((r) => r.win_rate!)),
    };
  }, [allRows]);

  const maxSharpe = useMemo(() => {
    if (allRows.length === 0) return 1;
    return Math.max(...allRows.map((r) => r.sharpe_ratio));
  }, [allRows]);

  const rank = allRows.findIndex((r) => r.category === 'strategy') + 1;
  const totalCount = allRows.length;

  const hasNoData = !data?.benchmarks?.length && !data?.best_result;

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-800">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <div className="h-4 w-36 animate-pulse rounded bg-zinc-800" />
        </div>
        <div className="p-4 space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-zinc-800" style={{ width: `${85 - i * 5}%` }} />
          ))}
        </div>
      </div>
    );
  }

  if (hasNoData) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <BarChart3 className="h-4 w-4" />
          <span className="text-sm">No benchmark data available</span>
        </div>
      </div>
    );
  }

  const isBest = (col: 'sharpe' | 'return_' | 'drawdown' | 'winRate', value: number) => {
    if (!bestValues) return false;
    if (col === 'winRate') return value === bestValues.winRate;
    if (col === 'drawdown') return value === bestValues.drawdown;
    if (col === 'return_') return value === bestValues.return_;
    return value === bestValues.sharpe;
  };

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-purple-400" />
          <span className="text-xs font-bold text-zinc-200">Strategy vs Benchmarks</span>
          {rank > 0 && (
            <span className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-purple-500/20 text-purple-400">
              #{rank} of {totalCount}
            </span>
          )}
        </div>
        {expanded ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
      </button>

      {expanded && (
        <div className="p-4 space-y-4">
          {/* Comparison Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="pb-2 text-left font-medium">Strategy / Model</th>
                  <th className="pb-2 text-right font-medium">Sharpe</th>
                  <th className="pb-2 text-right font-medium">Return</th>
                  <th className="pb-2 text-right font-medium">Drawdown</th>
                  <th className="pb-2 text-right font-medium">Win Rate</th>
                </tr>
              </thead>
              <tbody>
                {allRows.map((row, i) => {
                  const isStrategyRow = row.category === 'strategy';
                  const isBaseline = row.category === 'baseline';

                  return (
                    <tr
                      key={i}
                      className={`border-b border-zinc-800/50 ${
                        isStrategyRow ? 'border-l-2 border-l-purple-500/30 bg-purple-500/5' : ''
                      } ${isBaseline ? 'text-zinc-500' : ''}`}
                    >
                      <td className="py-2 pl-2">
                        <div className="flex items-center gap-1.5">
                          {isStrategyRow && <Trophy className="h-3 w-3 text-purple-400" />}
                          <span className={isStrategyRow ? 'font-bold text-zinc-200' : ''}>{row.name}</span>
                        </div>
                      </td>
                      <td
                        className={`py-2 text-right font-mono ${
                          isBest('sharpe', row.sharpe_ratio) ? 'text-green-400 font-bold' : 'text-zinc-200'
                        }`}
                      >
                        {Number.isFinite(Number(row.sharpe_ratio)) ? Number(row.sharpe_ratio).toFixed(2) : '—'}
                      </td>
                      <td
                        className={`py-2 text-right font-mono ${
                          isBest('return_', row.total_return_pct)
                            ? 'text-green-400 font-bold'
                            : row.total_return_pct >= 0
                              ? 'text-green-400'
                              : 'text-red-400'
                        }`}
                      >
                        {row.total_return_pct >= 0 ? '+' : ''}
                        {Number.isFinite(Number(row.total_return_pct)) ? Number(row.total_return_pct).toFixed(1) : '—'}%
                      </td>
                      <td
                        className={`py-2 text-right font-mono ${
                          isBest('drawdown', row.max_drawdown_pct) ? 'text-green-400 font-bold' : 'text-red-400'
                        }`}
                      >
                        {Number.isFinite(Number(row.max_drawdown_pct)) ? Number(row.max_drawdown_pct).toFixed(1) : '—'}%
                      </td>
                      <td className="py-2 text-right font-mono text-zinc-200">
                        {row.win_rate != null && Number.isFinite(Number(row.win_rate)) ? `${(Number(row.win_rate) * 100).toFixed(0)}%` : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Bar Chart */}
          <div>
            <p className="text-[10px] font-semibold text-zinc-500 mb-2 flex items-center gap-1">
              <TrendingUp className="h-3 w-3" />
              SHARPE RATIO COMPARISON
            </p>
            <div className="space-y-1">
              {allRows.map((row, i) => {
                const widthPct = maxSharpe > 0 ? (row.sharpe_ratio / maxSharpe) * 100 : 0;
                const barColor =
                  row.category === 'strategy'
                    ? 'bg-purple-500'
                    : row.category === 'model'
                      ? 'bg-blue-500/60'
                      : 'bg-zinc-600';

                return (
                  <div key={i} className="flex items-center gap-2">
                    <span
                      className={`w-28 shrink-0 text-right text-[10px] ${
                        row.category === 'strategy'
                          ? 'font-bold text-purple-400'
                          : row.category === 'baseline'
                            ? 'text-zinc-500'
                            : 'text-zinc-400'
                      }`}
                    >
                      {row.name}
                    </span>
                    <div className="flex-1 h-4 bg-zinc-800 rounded-sm overflow-hidden">
                      <div
                        className={`h-full rounded-sm ${barColor} transition-all duration-500`}
                        style={{ width: `${Math.max(widthPct, 2)}%` }}
                      />
                    </div>
                    <span className="w-10 shrink-0 text-right text-[10px] font-mono text-zinc-400">
                      {Number.isFinite(Number(row.sharpe_ratio)) ? Number(row.sharpe_ratio).toFixed(2) : '—'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
