'use client';

import { useState, useEffect } from 'react';
import { BookOpen, ChevronDown, ChevronUp, Download } from 'lucide-react';

interface JournalEntry {
  timestamp: string;
  symbol: string;
  side: string;
  shares: number;
  fillPrice: number;
  cost?: number;
  pnl?: number | null;
  reasoning?: string | null;
  confidence?: number | null;
  slippage?: number;
  source?: string;
}

interface JournalData {
  orders: JournalEntry[];
  total: number;
}

export function TradeJournal({ initialOrders }: { initialOrders?: JournalData }) {
  const hasServerData = initialOrders !== undefined && (initialOrders.orders?.length ?? 0) > 0;
  const [data, setData] = useState<JournalData | null>(initialOrders ?? null);
  const [loading, setLoading] = useState(!hasServerData);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (hasServerData) return;
    let alive = true;
    async function fetchData() {
      try {
        const res = await fetch('/api/trading/orders');
        if (res.ok) {
          const json = await res.json();
          if (alive) setData(json);
        }
      } catch {
        // keep existing
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

  function exportCsv() {
    window.open('/api/trading/export?format=csv', '_blank');
  }

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zinc-800">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <div className="h-4 w-32 animate-pulse rounded bg-zinc-800" />
        </div>
        <div className="p-4 space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-zinc-800" style={{ width: `${70 - i * 10}%` }} />
          ))}
        </div>
      </div>
    );
  }

  const orders = data?.orders ?? [];

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <BookOpen className="h-4 w-4 text-amber-400" />
          <span className="text-xs font-bold text-zinc-200">Trade Journal</span>
          {orders.length > 0 && (
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">
              {orders.length} trade{orders.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); exportCsv(); }}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); exportCsv(); } }}
            className="flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800/50 px-2 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-700/50 hover:text-zinc-200 transition-colors cursor-pointer"
            title="Export CSV"
          >
            <Download className="h-3 w-3" />
            CSV
          </span>
          {expanded ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
        </div>
      </button>

      {expanded && (
        <div className="p-3 md:p-4">
          {orders.length === 0 ? (
            <div className="text-center py-4">
              <BookOpen className="h-8 w-8 text-zinc-700 mx-auto mb-2" />
              <p className="text-xs text-zinc-500">No trades yet</p>
              <p className="text-[10px] text-zinc-600 mt-1">Trades will appear here after the pipeline executes signals</p>
            </div>
          ) : (
            <div className="space-y-2">
              {orders.slice(0, 20).map((entry, i) => {
                const ts = entry.timestamp ? new Date(entry.timestamp) : null;
                const timeStr = ts ? ts.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
                  ts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—';

                return (
                  <div key={i} className="rounded border border-zinc-800 bg-zinc-900/70 p-2.5 md:p-3">
                    {/* Header Row */}
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-bold text-zinc-100">{entry.symbol}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                          entry.side === 'BUY' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                        }`}>{entry.side}</span>
                        {entry.confidence != null && (
                          <span className="text-[10px] text-zinc-500">
                            {Number.isFinite(Number(entry.confidence)) ? (Number(entry.confidence) * 100).toFixed(0) : '—'}% conf
                          </span>
                        )}
                      </div>
                      <span className="text-[10px] text-zinc-500">{timeStr}</span>
                    </div>

                    {/* Details Row */}
                    <div className="flex items-center gap-4 text-xs text-zinc-400 mb-1.5">
                      <span>{Number.isFinite(Number(entry.shares)) ? Number(entry.shares).toFixed(4) : '—'} shares</span>
                      <span>@ ${Number.isFinite(Number(entry.fillPrice)) ? Number(entry.fillPrice).toFixed(2) : '—'}</span>
                      {entry.cost != null && (
                        <span className="text-zinc-500">${entry.cost.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                      )}
                      {entry.slippage != null && (
                        <span className="text-[10px] text-zinc-600">{Number.isFinite(Number(entry.slippage)) ? (Number(entry.slippage) * 100).toFixed(1) : '—'}% slip</span>
                      )}
                    </div>

                    {/* P&L Row */}
                    {entry.pnl != null && (
                      <div className={`text-xs font-mono font-bold mb-1.5 ${entry.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        P&L: {entry.pnl >= 0 ? '+' : ''}${Number.isFinite(Number(entry.pnl)) ? Number(entry.pnl).toFixed(2) : '—'}
                      </div>
                    )}

                    {/* Reasoning */}
                    {entry.reasoning && (
                      <div className="mt-1.5 pt-1.5 border-t border-zinc-800/50">
                        <p className="text-[10px] text-zinc-500 leading-relaxed line-clamp-2">
                          {entry.reasoning}
                        </p>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
