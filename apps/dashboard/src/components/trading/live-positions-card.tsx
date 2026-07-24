'use client';

import { useEffect, useState } from 'react';
import { Wallet } from 'lucide-react';

interface Position {
  symbol: string;
  quantity: number;
  entry_price: number;
  current_price: number | null;
  pnl_pct: number | null;
  stop_loss: number | null;
  take_profit: number | null;
}

interface PositionsData {
  positions: Position[];
  total_equity: number;
  cash: number;
  total_pnl_pct: number | null;
  error?: string;
}

export function LivePositionsCard() {
  const [data, setData] = useState<PositionsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/live-positions')
      .then(r => r.json())
      .then(setData)
      .catch(() => setData({ positions: [], total_equity: 0, cash: 0, total_pnl_pct: null, error: 'Failed to fetch' }))
      .finally(() => setLoading(false));
  }, []);

  const positions = data?.positions || [];
  const totalPnl = data?.total_pnl_pct;

  if (loading) return <p className="text-xs text-zinc-500">Loading positions...</p>;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Wallet className="w-3.5 h-3.5 text-violet-400" />
          <span className="text-xs text-zinc-500">
            Equity: <span className="text-zinc-300 font-mono">${data?.total_equity?.toLocaleString()}</span>
          </span>
        </div>
        {totalPnl != null && (
          <span className={`text-[11px] font-mono ${totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {totalPnl >= 0 ? '+' : ''}{totalPnl}%
          </span>
        )}
      </div>

      {positions.length === 0 ? (
        <p className="text-xs text-zinc-500">No open positions</p>
      ) : (
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-zinc-600 border-b border-zinc-800">
              <th className="text-left py-1 font-medium">Sym</th>
              <th className="text-right py-1 font-medium">Qty</th>
              <th className="text-right py-1 font-medium">Entry</th>
              <th className="text-right py-1 font-medium">Current</th>
              <th className="text-right py-1 font-medium">PnL%</th>
              <th className="text-right py-1 font-medium">Stop</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => (
              <tr key={p.symbol} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td className="py-1 font-mono text-zinc-300">{p.symbol}</td>
                <td className="py-1 text-right font-mono text-zinc-400">
                  {p.quantity > 0 ? p.quantity.toFixed(p.quantity < 1 ? 4 : 1) : '—'}
                </td>
                <td className="py-1 text-right font-mono text-zinc-400">
                  ${p.entry_price?.toFixed(2) || '—'}
                </td>
                <td className="py-1 text-right font-mono text-zinc-300">
                  {p.current_price ? `$${p.current_price.toFixed(2)}` : '—'}
                </td>
                <td className={`py-1 text-right font-mono ${
                  p.pnl_pct == null ? 'text-zinc-500' :
                  p.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'
                }`}>
                  {p.pnl_pct != null ? `${p.pnl_pct >= 0 ? '+' : ''}${p.pnl_pct}%` : '—'}
                </td>
                <td className="py-1 text-right font-mono text-zinc-500">
                  {p.stop_loss ? `$${p.stop_loss.toFixed(2)}` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
