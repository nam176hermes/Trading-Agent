'use client';

import { MarketTicker } from '@/components/trading/market-ticker';
import { TradingSubNav } from '@/components/trading/trading-sub-nav';
import { DecisionTimeline } from '@/components/trading/decision-timeline';
import { DebateView } from '@/components/trading/debate-view';
import { RiskPersonaPanel } from '@/components/trading/risk-persona-panel';
import { PerformanceMetrics } from '@/components/trading/performance-metrics';
import { DateRangeFilter, DateRange } from '@/components/trading/date-range-filter';
import { useEffect, useState } from 'react';
import { Decision, AssetSymbol, TypedDecision } from '@/lib/trading/types';
import { BookOpen } from 'lucide-react';

type Tab = 'structured' | 'timeline' | 'journal';

interface JournalOrder {
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

export default function DecisionHistory() {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [typedDecisions, setTypedDecisions] = useState<TypedDecision[]>([]);
  const [journalOrders, setJournalOrders] = useState<JournalOrder[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<AssetSymbol | 'ALL'>('ALL');
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>('structured');
  const [dateRange, setDateRange] = useState<DateRange>({ from: null, to: null, preset: 'all' });
  const [journalSort, setJournalSort] = useState<'newest' | 'oldest'>('newest');

  useEffect(() => {
    async function load() {
      try {
        const [dr, tr, or] = await Promise.all([
          fetch('/api/trading/decisions?limit=500'),
          fetch('/api/trading/decisions/typed?limit=50'),
          fetch('/api/trading/orders'),
        ]);
        if (dr.ok) { const d = await dr.json(); setDecisions(d.decisions); }
        if (tr.ok) { const d = await tr.json(); setTypedDecisions(d.decisions); }
        if (or.ok) { const d = await or.json(); setJournalOrders(d.orders ?? []); }
      } catch (e) { console.error(e); } finally { setIsLoading(false); }
    }
    load();
  }, []);

  const filteredDecisions = selectedSymbol === 'ALL'
    ? decisions
    : decisions.filter(d => d.ticker === selectedSymbol);

  // Date range filter helper
  function inDateRange(ts: string): boolean {
    if (!ts) return false;
    if (!dateRange.from && !dateRange.to) return true;
    const d = new Date(ts);
    if (isNaN(d.getTime())) return false;
    if (dateRange.from && d < dateRange.from) return false;
    if (dateRange.to) {
      const endOfDay = new Date(dateRange.to);
      endOfDay.setHours(23, 59, 59, 999);
      if (d > endOfDay) return false;
    }
    return true;
  }

  const filteredTyped = typedDecisions
    .filter(d => selectedSymbol === 'ALL' || d.asset === selectedSymbol)
    .filter(d => inDateRange(d.timestamp));

  const timelineFiltered = filteredDecisions.filter(d => inDateRange(d.date ?? d.stored_at));

  const filteredJournal = journalOrders
    .filter(o => selectedSymbol === 'ALL' || o.symbol === selectedSymbol)
    .filter(o => inDateRange(o.timestamp))
    .sort((a, b) => {
      const da = new Date(a.timestamp).getTime();
      const db = new Date(b.timestamp).getTime();
      return journalSort === 'newest' ? db - da : da - db;
    });

  // Journal summary
  const journalTotalPnl = filteredJournal.reduce((sum, o) => sum + (o.pnl ?? 0), 0);
  const journalWins = filteredJournal.filter(o => (o.pnl ?? 0) > 0).length;
  const journalLosses = filteredJournal.filter(o => (o.pnl ?? 0) < 0).length;
  const journalWithPnl = filteredJournal.filter(o => o.pnl != null);

  return (
    <div>
      <MarketTicker />
      <TradingSubNav currentPath="/dashboard/history" />
      <div className="p-3 md:p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Decision History</h1>
          <p className="text-sm text-zinc-400">Full pipeline decisions with Bull/Bear debate, risk persona assessments, and trade journal</p>
        </div>

        <div className="mb-6">
          <PerformanceMetrics />
        </div>

        <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
          <div className="flex gap-2">
            {(['structured', 'timeline', 'journal'] as Tab[]).map(t => (
              <button key={t} onClick={() => setActiveTab(t)}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${activeTab === t ? 'bg-violet-600 text-white' : 'bg-zinc-900 text-zinc-400 hover:text-white'}`}
              >
                {t === 'structured' ? 'Structured Decisions' : t === 'timeline' ? 'Signal Timeline' : 'Trade Journal'}
                {t === 'structured' && typedDecisions.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-violet-500/40 px-1.5 py-0.5 text-[10px]">{typedDecisions.length}</span>
                )}
                {t === 'journal' && filteredJournal.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-amber-500/40 px-1.5 py-0.5 text-[10px]">{filteredJournal.length}</span>
                )}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <DateRangeFilter value={dateRange} onChange={setDateRange} />
            <select value={selectedSymbol} onChange={e => setSelectedSymbol(e.target.value as AssetSymbol | 'ALL')}
              className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-blue-500 focus:outline-none"
            >
              <option value="ALL">All Assets</option>
              {['BTC','ETH','SOL','TON','DOGE'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>

        {isLoading ? (
          <div className="p-12 text-center"><p className="text-zinc-400">Loading...</p></div>
        ) : activeTab === 'journal' ? (
          /* --- Trade Journal Tab --- */
          <div className="space-y-4">
            {/* P&L Summary */}
            {journalWithPnl.length > 0 && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                  <p className="text-[10px] uppercase tracking-wider text-zinc-500">Total P&amp;L</p>
                  <p className={`mt-1 text-xl font-bold font-mono ${journalTotalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {journalTotalPnl >= 0 ? '+' : ''}${Number.isFinite(Number(journalTotalPnl)) ? Number(journalTotalPnl).toFixed(2) : '—'}
                  </p>
                </div>
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                  <p className="text-[10px] uppercase tracking-wider text-zinc-500">Win Rate</p>
                  <p className="mt-1 text-xl font-bold font-mono text-zinc-100">
                    {journalWithPnl.length > 0 ? `${Math.round((journalWins / journalWithPnl.length) * 100)}%` : '—'}
                  </p>
                </div>
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                  <p className="text-[10px] uppercase tracking-wider text-zinc-500">Wins</p>
                  <p className="mt-1 text-xl font-bold font-mono text-green-400">{journalWins}</p>
                </div>
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                  <p className="text-[10px] uppercase tracking-wider text-zinc-500">Losses</p>
                  <p className="mt-1 text-xl font-bold font-mono text-red-400">{journalLosses}</p>
                </div>
              </div>
            )}

            {/* Sort toggle */}
            <div className="flex items-center justify-between">
              <p className="text-xs text-zinc-500">
                Showing {filteredJournal.length} of {journalOrders.length} trades
              </p>
              <button
                onClick={() => setJournalSort(journalSort === 'newest' ? 'oldest' : 'newest')}
                className="text-xs text-zinc-400 hover:text-white transition-colors"
              >
                Sort: {journalSort === 'newest' ? 'Newest first' : 'Oldest first'}
              </button>
            </div>

            {filteredJournal.length > 0 ? (
              <div className="space-y-2">
                {filteredJournal.map((entry, i) => {
                  const ts = entry.timestamp ? new Date(entry.timestamp) : null;
                  const timeStr = ts ? ts.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
                    ts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—';

                  return (
                    <div key={i} className="rounded-lg border border-zinc-800 bg-zinc-900/30 p-3 md:p-4">
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

                      {entry.pnl != null && (
                        <div className={`text-xs font-mono font-bold mb-1.5 ${entry.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          P&amp;L: {entry.pnl >= 0 ? '+' : ''}${Number.isFinite(Number(entry.pnl)) ? Number(entry.pnl).toFixed(2) : '—'}
                        </div>
                      )}

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
            ) : (
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-12 text-center">
                <BookOpen className="h-8 w-8 text-zinc-700 mx-auto mb-2" />
                <h3 className="mb-2 text-lg font-bold text-zinc-100">No Trades Yet</h3>
                <p className="text-zinc-400">Trades will appear here after the pipeline executes signals</p>
              </div>
            )}
          </div>
        ) : activeTab === 'structured' ? (
          filteredTyped.length > 0 ? (
            <div className="space-y-8">
              <p className="text-xs text-zinc-500">
                Showing {filteredTyped.length} of {typedDecisions.length} structured decisions
              </p>
              {filteredTyped.map((d, i) => (
                <div key={i} className="rounded-lg border border-zinc-800 bg-zinc-900/30 p-5">
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xl font-bold text-zinc-100">{d.asset}</span>
                      <span className={`rounded px-2 py-0.5 text-sm font-bold ${
                        d.final_action === 'BUY'  ? 'bg-green-500/20 text-green-400' :
                        d.final_action === 'SELL' ? 'bg-red-500/20 text-red-400'    :
                        'bg-zinc-500/20 text-zinc-400'
                      }`}>{d.final_action}</span>
                    </div>
                    <div className="flex flex-wrap gap-4 text-xs text-zinc-400">
                      <span>Conf: <span className="font-medium text-zinc-200">{Number.isFinite(Number(d.initial_signal?.confidence)) ? (Number(d.initial_signal?.confidence) * 100).toFixed(0) : '—'}%</span></span>
                      <span>Size: <span className="font-medium text-zinc-200">{d.final_position_size_pct}%</span></span>
                      <span>{new Date(d.timestamp).toLocaleString()}</span>
                    </div>
                  </div>
                  {d.executive_summary && (
                    <p className="mb-4 rounded bg-zinc-800/30 p-3 text-xs leading-relaxed text-zinc-300">{d.executive_summary}</p>
                  )}
                  <div className="space-y-4">
                    <DebateView decision={d} />
                    <div>
                      <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-zinc-500">3-Way Risk Debate</p>
                      <RiskPersonaPanel decision={d} />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-12 text-center">
              <h3 className="mb-2 text-lg font-bold text-zinc-100">No Structured Decisions Yet</h3>
              <p className="text-zinc-400">Run a full pipeline cycle to see bull/bear debate and risk personas here.</p>
            </div>
          )
        ) : (
          timelineFiltered.length > 0 ? (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <h2 className="mb-4 text-lg font-bold text-zinc-100">Signal Timeline</h2>
                <p className="mb-2 text-xs text-zinc-500">
                  Showing {timelineFiltered.length} of {decisions.length} decisions
                </p>
                <DecisionTimeline decisions={timelineFiltered} />
              </div>
              <div>
                <h2 className="mb-4 text-lg font-bold text-zinc-100">Stats</h2>
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
                  <h3 className="mb-1 text-sm font-medium text-zinc-300">Avg Confidence</h3>
                  <p className="text-3xl font-bold text-zinc-100">
                    {timelineFiltered.length > 0
                      ? (() => { const avg = timelineFiltered.reduce((s,d) => s + Number(d.confidence ?? 0), 0) / timelineFiltered.length; return Number.isFinite(avg) ? avg.toFixed(0) : '0'; })()
                      : '0'}%
                  </p>
                  <p className="mt-2 text-xs text-zinc-500">{timelineFiltered.length} decisions shown</p>
                </div>
              </div>
            </div>
          ) : (
            <div className="p-12 text-center"><p className="text-zinc-400">No decisions yet.</p></div>
          )
        )}
      </div>
    </div>
  );
}
