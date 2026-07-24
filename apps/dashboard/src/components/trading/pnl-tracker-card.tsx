'use client';

import { useState, useEffect } from 'react';
import { TrendingUp, ChevronDown, ChevronUp } from 'lucide-react';

interface Trade {
  asset: string;
  action: string;
  pnl_pct: number;
  outcome: 'win' | 'loss' | 'breakeven';
  resolved_at: string;
  days_held: number;
}

interface PnlData {
  totalTrades: number;
  wins: number;
  losses: number;
  winRate: number;
  totalPnlPct: number;
  avgHoldDays: number;
  recentTrades: Trade[];
  updatedAt: string | null;
}

export function PnlTrackerCard({ initialData }: { initialData?: PnlData | null }) {
  const hasServerData = initialData !== undefined;
  const [data, setData] = useState<PnlData | null>(initialData ?? null);
  const [loading, setLoading] = useState(!hasServerData);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (hasServerData) return;
    let alive = true;
    async function fetchData() {
      try {
        const res = await fetch('/api/trading/pnl');
        if (res.ok) {
          const json = await res.json();
          if (alive) setData(json);
        }
      } catch {
        // keep existing data
      } finally {
        if (alive) setLoading(false);
      }
    }
    fetchData();
    const interval = setInterval(fetchData, 60_000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [hasServerData]);

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-800">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <div className="h-4 w-32 animate-pulse rounded bg-zinc-800" />
        </div>
        <div className="p-4 space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-zinc-800" style={{ width: `${80 - i * 10}%` }} />
          ))}
        </div>
      </div>
    );
  }

  if (!data || data.totalTrades === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <TrendingUp className="h-4 w-4" />
          <span className="text-sm">No P&L data yet — run the pipeline to start tracking</span>
        </div>
      </div>
    );
  }

  const winRateColor =
    data.winRate >= 0.5 ? 'text-green-400' : data.winRate >= 0.4 ? 'text-yellow-400' : 'text-red-400';

  const totalPnlColor = data.totalPnlPct >= 0 ? 'text-green-400' : 'text-red-400';

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-emerald-400" />
          <span className="text-xs font-bold text-zinc-200">P&amp;L Tracker</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${data.totalPnlPct >= 0 ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
            {data.totalPnlPct >= 0 ? '+' : ''}{Number.isFinite(Number(data.totalPnlPct)) ? Number(data.totalPnlPct).toFixed(1) : '—'}%
          </span>
        </div>
        {expanded ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
      </button>

      {expanded && (
        <div className="p-4 space-y-4">
          {/* Stats Row */}
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Win Rate</p>
              <p className={`text-lg font-bold ${winRateColor}`}>
                {Number.isFinite(Number(data.winRate)) ? (Number(data.winRate) * 100).toFixed(0) : '—'}%
              </p>
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Total P&amp;L</p>
              <p className={`text-lg font-bold ${totalPnlColor}`}>
                {data.totalPnlPct >= 0 ? '+' : ''}{Number.isFinite(Number(data.totalPnlPct)) ? Number(data.totalPnlPct).toFixed(1) : '—'}%
              </p>
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Trades</p>
              <p className="text-lg font-bold text-zinc-100">{data.totalTrades}</p>
            </div>
          </div>

          {/* Win/Loss Bar */}
          {data.totalTrades > 0 && (
            <div>
              <div className="flex justify-between text-[10px] text-zinc-500 mb-1">
                <span>{data.wins}W / {data.losses}L</span>
                <span className="text-zinc-600">avg {Number.isFinite(Number(data.avgHoldDays)) ? Number(data.avgHoldDays).toFixed(1) : '—'}d hold</span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 transition-all duration-500 rounded-l-full"
                  style={{ width: `${data.winRate * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Recent Trades */}
          {data.recentTrades.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-zinc-500 mb-2">RECENT TRADES</p>
              <div className="space-y-1.5">
                {data.recentTrades.slice(0, 5).map((trade, i) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-zinc-300">{trade.asset}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                        trade.action === 'BUY' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                      }`}>{trade.action}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-zinc-500">{trade.days_held}d</span>
                      <span className={`font-mono font-bold ${
                        trade.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}>
                        {trade.pnl_pct >= 0 ? '+' : ''}{Number.isFinite(Number(trade.pnl_pct)) ? Number(trade.pnl_pct).toFixed(2) : '—'}%
                      </span>
                      <span className="text-[10px]">
                        {trade.outcome === 'win' ? '🟢' : trade.outcome === 'loss' ? '🔴' : '⚪'}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
