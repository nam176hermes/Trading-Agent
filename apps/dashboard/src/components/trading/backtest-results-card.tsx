'use client';

import { useEffect, useState } from 'react';
import { BarChart3 } from 'lucide-react';

interface SymbolResult {
  sharpe: number;
  win_rate: number;
  total_pnl: number;
  trades: number;
}

interface BacktestData {
  symbols: Record<string, SymbolResult>;
  generated_at: string | null;
  error?: string;
}

function sharpeColor(s: number): string {
  if (s > 0.5) return 'text-emerald-400';
  if (s >= 0) return 'text-yellow-400';
  return 'text-red-400';
}

function pnlColor(pnl: number): string {
  if (pnl > 0) return 'text-emerald-400';
  if (pnl < 0) return 'text-red-400';
  return 'text-zinc-400';
}

export function BacktestResultsCard() {
  const [data, setData] = useState<BacktestData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/backtest-results')
      .then(r => r.json())
      .then(setData)
      .catch(() => setData({ symbols: {}, generated_at: null, error: 'Failed to fetch' }))
      .finally(() => setLoading(false));
  }, []);

  const symbols = Object.entries(data?.symbols || {});

  if (loading) return <p className="text-xs text-zinc-500">Loading backtest results...</p>;
  if (symbols.length === 0) return <p className="text-xs text-zinc-500">No backtest data available</p>;

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 mb-2">
        <BarChart3 className="w-3.5 h-3.5 text-violet-400" />
        <span className="text-xs text-zinc-500">
          {data?.generated_at ? `Updated ${new Date(data.generated_at).toLocaleDateString()}` : ''}
        </span>
      </div>
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-zinc-600 border-b border-zinc-800">
            <th className="text-left py-1 font-medium">Sym</th>
            <th className="text-right py-1 font-medium">Sharpe</th>
            <th className="text-right py-1 font-medium">PnL</th>
            <th className="text-right py-1 font-medium">Win%</th>
            <th className="text-right py-1 font-medium">#Tr</th>
          </tr>
        </thead>
        <tbody>
          {symbols.map(([sym, s]) => (
            <tr key={sym} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
              <td className="py-1 font-mono text-zinc-300">{sym}</td>
              <td className={`py-1 text-right font-mono ${sharpeColor(s.sharpe)}`}>
                {Number.isFinite(s.sharpe) ? s.sharpe.toFixed(2) : '—'}
              </td>
              <td className={`py-1 text-right font-mono ${pnlColor(s.total_pnl)}`}>
                {Number.isFinite(s.total_pnl) ? (s.total_pnl > 0 ? '+' : '') + s.total_pnl.toFixed(0) : '—'}
              </td>
              <td className="py-1 text-right font-mono text-zinc-300">
                {Number.isFinite(s.win_rate) ? (s.win_rate * 100).toFixed(0) : '—'}%
              </td>
              <td className="py-1 text-right font-mono text-zinc-500">{s.trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
