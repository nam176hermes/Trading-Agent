'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Wallet, X, ChevronDown, ChevronUp, Edit3, Download } from 'lucide-react';
import { subscribePrices, extractPrice, PricePayload } from '@/lib/trading/price-stream';

interface Position {
  symbol: string;
  shares: number;
  avgCost: number;
  currentPrice: number | null;
  marketValue: number | null;
  unrealizedPnl: number | null;
  unrealizedPnlPct: number | null;
  stopLoss: number;
  trailStop: number | null;
  highestPrice: number | null;
  trailActivated: boolean;
  distanceToStop: number;
  distanceToStopPct: number;
  manualStop?: boolean;
}

interface Order {
  timestamp: string;
  symbol: string;
  side: string;
  shares: number;
  fillPrice: number;
  cost?: number;
  proceeds?: number;
  pnl?: number;
}

interface PortfolioData {
  cash: number;
  equity: number;
  pnl: number;
  positions: Position[];
  positionCount: number;
  totalMarketValue: number;
  pricesAsOf: string | null;
  recentOrders: Order[];
  ordersCount: number;
  createdAt: string | null;
  maxRisk: number;
  maxRiskPct: number;
  exposure: number;
}

export function PortfolioCard() {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);
  const [corrData, setCorrData] = useState<{ symbols: string[]; matrix: number[][] } | null>(null);
  const [editingStop, setEditingStop] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [savingStop, setSavingStop] = useState<string | null>(null);
  const [dataFetchedAt, setDataFetchedAt] = useState<number | null>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  // SSE-based live prices
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const [sseConnected, setSseConnected] = useState(false);

  // Merge live prices into position data
  const getCurrentPrice = useCallback((symbol: string, fallback: number | null): number | null => {
    return livePrices[symbol] ?? fallback;
  }, [livePrices]);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();

    async function fetchData() {
      try {
        const res = await fetch('/api/trading/portfolio', { signal: controller.signal });
        if (res.ok) {
          const json = await res.json();
          if (alive) {
            // Tag manual stops
            if (json.positions) {
              json.positions = json.positions.map((p: Position) => ({
                ...p,
                manualStop: p.manualStop ?? false,
              }));
            }
            setData(json);
            setDataFetchedAt(Date.now());
          }
        }
      } catch {
        // keep existing (may be aborted)
      } finally {
        if (alive) setLoading(false);
      }
    }
    async function fetchCorrelation() {
      try {
        const res = await fetch('/api/trading/correlation', { signal: controller.signal });
        if (res.ok && alive) {
          const json = await res.json();
          if (json.symbols && json.matrix) {
            setCorrData({ symbols: json.symbols, matrix: json.matrix });
          }
        }
      } catch { /* ignore */ }
    }

    fetchData();
    fetchCorrelation();

    // Subscribe to shared SSE singleton (avoids opening a new connection per component)
    const unsubPrices = subscribePrices((payload: PricePayload) => {
      if (!alive) return;
      // Build a plain symbol→number map for position P&L calculations
      const numericPrices: Record<string, number> = {};
      for (const sym of Object.keys(payload)) {
        if (sym.startsWith('_')) continue;
        const p = extractPrice(payload, sym);
        if (p !== null) numericPrices[sym] = p;
      }
      setLivePrices(numericPrices);
      setSseConnected(true);
    });

    // Polling fallback for portfolio data (every 60s)
    const pollInterval = setInterval(() => {
      fetchData();
      fetchCorrelation();
    }, 60_000);

    return () => {
      alive = false;
      controller.abort();
      unsubPrices();
      clearInterval(pollInterval);
    };
  }, []);

  // Focus input when editing starts
  useEffect(() => {
    if (editingStop && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingStop]);

  function startEditStop(symbol: string, currentStop: number) {
    setEditingStop(symbol);
    setEditValue(Number.isFinite(Number(currentStop)) ? Number(currentStop).toFixed(2) : '—');
  }

  async function saveStop(symbol: string) {
    const newStop = parseFloat(editValue);
    if (isNaN(newStop) || newStop <= 0) {
      alert('Please enter a valid positive stop-loss price.');
      return;
    }

    setSavingStop(symbol);
    try {
      const res = await fetch('/api/trading/update-stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, stopLoss: newStop }),
      });
      const result = await res.json();
      if (result.success) {
        // Refresh data
        const refresh = await fetch('/api/trading/portfolio');
        if (refresh.ok) {
          const { json, fetchedAt } = await refresh.json().then(json => ({
            json,
            fetchedAt: Date.now(),
          }));
          if (json.positions) {
            json.positions = json.positions.map((p: Position) => ({
              ...p,
              manualStop: p.symbol === symbol ? true : (p.manualStop ?? false),
            }));
          }
          setData(json);
          setDataFetchedAt(fetchedAt);
        }
        setEditingStop(null);
      } else {
        alert(`Failed to update stop: ${result.error || 'Unknown error'}`);
      }
    } catch {
      alert('Failed to update stop: network error');
    } finally {
      setSavingStop(null);
    }
  }

  function cancelEdit() {
    setEditingStop(null);
  }

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
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-zinc-800" style={{ width: `${80 - i * 10}%` }} />
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <Wallet className="h-4 w-4" />
          <span className="text-sm">No portfolio data — run the pipeline with execution to populate</span>
        </div>
      </div>
    );
  }

  const equity = data.equity ?? 100000;
  const pnlColor = data.pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const pnlTotal = data.pnl ?? 0;

  let pricesAge: string | null = null;
  if (data.pricesAsOf) {
    const then = new Date(data.pricesAsOf).getTime();
    const diff = (dataFetchedAt ?? then) - then;
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) pricesAge = 'just now';
    else if (minutes === 1) pricesAge = '1m ago';
    else if (minutes < 60) pricesAge = `${minutes}m ago`;
    else {
      const hours = Math.floor(minutes / 60);
      pricesAge = hours === 1 ? '1h ago' : `${hours}h ago`;
    }
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Wallet className="h-4 w-4 text-emerald-400" />
          <span className="text-xs font-bold text-zinc-200">Paper Portfolio</span>
          {sseConnected && (
            <span className="w-1.5 h-1.5 rounded-full bg-green-400" title="Live prices via SSE" />
          )}
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${pnlTotal >= 0 ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
            {pnlTotal >= 0 ? '+' : ''}{pnlTotal.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })} USD
          </span>
        </div>
        {expanded ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
      </button>

      {expanded && (
        <div className="p-4 space-y-4">
          {/* Export Button */}
          <div className="flex justify-end">
            <button
              onClick={exportCsv}
              className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-800/50 px-2.5 py-1 text-[10px] font-medium text-zinc-400 hover:bg-zinc-700/50 hover:text-zinc-200 transition-colors"
            >
              <Download className="h-3 w-3" />
              Export CSV
            </button>
          </div>

          {/* Stats Row */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Cash</p>
              <p className="text-base md:text-lg font-bold text-zinc-100">
                ${(data.cash ?? 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Equity</p>
              <p className={`text-base md:text-lg font-bold ${pnlColor}`}>
                ${equity.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </p>
              {pricesAge && (
                <p className="text-[9px] text-zinc-600 mt-0.5">↑ prices {pricesAge}</p>
              )}
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Unrealized P&amp;L</p>
              <p className={`text-base md:text-lg font-bold ${pnlColor}`}>
                {pnlTotal >= 0 ? '+' : ''}${pnlTotal.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </p>
              {data.totalMarketValue > 0 && (
                <p className={`text-[9px] mt-0.5 ${pnlColor}`}>
                  {Number.isFinite(pnlTotal / data.totalMarketValue) ? ((pnlTotal / data.totalMarketValue) * 100).toFixed(2) : '—'}%
                </p>
              )}
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-900/70 p-2 text-center">
              <p className="text-[10px] text-zinc-500">Positions</p>
              <p className="text-base md:text-lg font-bold text-zinc-100">{data.positionCount ?? 0}</p>
            </div>
          </div>

          {/* P&L Bar */}
          <div>
            <div className="flex justify-between text-[10px] text-zinc-500 mb-1">
              <span>Total P&L</span>
              <span className={pnlColor}>
                {pnlTotal >= 0 ? '+' : ''}${pnlTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
            <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-500 rounded-full ${pnlTotal >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
                style={{ width: `${Math.min(Math.abs(pnlTotal) / 1000 * 100, 100)}%` }}
              />
            </div>
          </div>

          {/* Positions Table */}
          {data.positions.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-zinc-500 mb-2">POSITIONS</p>
              <div className="space-y-1">
                {data.positions.map((pos, i) => {
                  const currentPrice = getCurrentPrice(pos.symbol, pos.currentPrice);
                  const marketValue = currentPrice != null ? pos.shares * currentPrice : pos.marketValue;
                  const unrealizedPnl = marketValue != null ? marketValue - pos.shares * pos.avgCost : pos.unrealizedPnl;
                  const unrealizedPnlPct = unrealizedPnl != null ? (unrealizedPnl / (pos.shares * pos.avgCost)) * 100 : pos.unrealizedPnlPct;
                  const distPct = Math.abs(pos.distanceToStopPct);
                  const distColor = distPct < 5 ? 'text-red-400' : distPct < 10 ? 'text-yellow-400' : 'text-zinc-500';
                  const upnlColor = unrealizedPnl != null
                    ? (unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400')
                    : 'text-zinc-600';
                  const isEditing = editingStop === pos.symbol;
                  const isSaving = savingStop === pos.symbol;

                  return (
                    <div key={i} className="group flex items-center justify-between text-xs rounded bg-zinc-800/30 px-2 py-1.5 flex-wrap gap-x-1 gap-y-0.5">
                      <span className="font-mono text-zinc-300 w-14 shrink-0">{pos.symbol}</span>
                      <span className="text-zinc-500 w-14 text-right shrink-0">{Number.isFinite(Number(pos.shares)) ? Number(pos.shares).toFixed(2) : '—'}</span>
                      <span className="font-mono text-zinc-400 w-16 text-right shrink-0 hidden sm:inline">@{Number.isFinite(Number(pos.avgCost)) ? Number(pos.avgCost).toFixed(2) : '—'}</span>
                      <span className="font-mono text-zinc-400 w-16 text-right shrink-0">
                        {Number.isFinite(currentPrice) ? <>${(currentPrice as number).toFixed(2)}</> : <span className="text-zinc-600">—</span>}
                      </span>
                      <span className={`font-mono w-20 text-right shrink-0 ${upnlColor}`}>
                        {unrealizedPnl != null
                          ? <>{unrealizedPnl >= 0 ? '+' : ''}${Number.isFinite(Number(unrealizedPnl)) ? Number(unrealizedPnl).toFixed(2) : '—'} ({Number(unrealizedPnlPct) >= 0 ? '+' : ''}{Number.isFinite(Number(unrealizedPnlPct)) ? Number(unrealizedPnlPct).toFixed(1) : '—'}%)</>
                          : <span className="text-zinc-600">—</span>}
                      </span>
                      <span className="font-mono text-zinc-400 text-right shrink-0 flex items-center gap-1">
                        {isEditing ? (
                          <span className="flex items-center gap-1">
                            <input
                              ref={editInputRef}
                              type="number"
                              step="0.01"
                              value={editValue}
                              onChange={(e) => setEditValue(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') saveStop(pos.symbol);
                                if (e.key === 'Escape') cancelEdit();
                              }}
                              className="w-16 bg-zinc-800 border border-zinc-600 rounded px-1 py-0 text-[11px] font-mono text-zinc-100 focus:outline-none focus:border-green-500"
                              disabled={isSaving}
                            />
                            {isSaving ? (
                              <span className="inline-block w-3 h-3 border border-zinc-500 border-t-transparent rounded-full animate-spin" />
                            ) : (
                              <>
                                <button onClick={() => saveStop(pos.symbol)} className="text-green-400 hover:text-green-300" title="Save">✓</button>
                                <button onClick={cancelEdit} className="text-red-400 hover:text-red-300" title="Cancel">✕</button>
                              </>
                            )}
                          </span>
                        ) : (
                          <>
                            SL:<span className={`${distPct < 5 ? 'text-red-400' : 'text-zinc-500'} cursor-pointer hover:underline`}
                              onClick={() => startEditStop(pos.symbol, pos.stopLoss)}>
                              {Number.isFinite(Number(pos.stopLoss)) ? Number(pos.stopLoss).toFixed(2) : '—'}
                            </span>
                            {pos.manualStop && (
                              <span className="rounded bg-amber-500/20 px-1 py-0 text-[8px] text-amber-400 font-bold" title="Manual override">MANUAL</span>
                            )}
                            <button
                              onClick={(e) => { e.stopPropagation(); startEditStop(pos.symbol, pos.stopLoss); }}
                              className="opacity-0 group-hover:opacity-100 transition-opacity text-zinc-600 hover:text-zinc-300"
                              title="Edit stop-loss"
                            >
                              <Edit3 className="h-2.5 w-2.5" />
                            </button>
                          </>
                        )}
                      </span>
                      {pos.trailActivated && pos.highestPrice != null && (
                        <span className="font-mono text-green-400 text-right ml-1 shrink-0 hidden sm:inline">🏔{Number.isFinite(Number(pos.highestPrice)) ? Number(pos.highestPrice).toFixed(2) : '—'}</span>
                      )}
                      <span className={`font-mono text-right ml-1 shrink-0 ${distColor}`}>
                        -{Number.isFinite(Number(distPct)) ? Number(distPct).toFixed(1) : '—'}%
                      </span>
                      <button
                        disabled
                        className="ml-1 opacity-0 group-hover:opacity-40 transition-opacity text-zinc-600 cursor-not-allowed shrink-0"
                        title="Position close commands are disabled in this dashboard"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Risk Summary */}
          {data.positions.length > 0 && (
            <div className="flex flex-col sm:flex-row justify-between text-[10px] text-zinc-500 border-t border-zinc-800 pt-3 gap-1">
              <span>Max risk: <span className="text-red-400">${data.maxRisk.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span> <span className="text-zinc-600">({Number.isFinite(Number(data.maxRiskPct)) ? Number(data.maxRiskPct).toFixed(1) : '—'}% of equity)</span></span>
              <span>Exposure: <span className="text-zinc-300">{Number.isFinite(Number(data.exposure)) ? Number(data.exposure).toFixed(1) : '—'}%</span> / 50% cap</span>
            </div>
          )}

          {/* Correlation Heatmap — only if 2+ positions and data available */}
          {data.positions.length >= 2 && corrData && corrData.symbols.length >= 2 && (() => {
            const posSymbols = new Set(data.positions.map(p => p.symbol));
            const relevantSymbols = corrData.symbols.filter(s => posSymbols.has(s));
            if (relevantSymbols.length < 2) return null;
            const topN = relevantSymbols.slice(0, Math.min(6, relevantSymbols.length));
            const symIdx: Record<string, number> = {};
            corrData.symbols.forEach((s, i) => { symIdx[s] = i; });

            return (
              <div>
                <p className="text-[10px] font-semibold text-zinc-500 mb-2">CORRELATION HEATMAP</p>
                <div className="overflow-x-auto">
                  <div className="min-w-fit">
                    {/* Header */}
                    <div className="flex gap-1 mb-1">
                      <div className="w-7 h-6 sm:w-10 sm:h-7" />
                      {topN.map(s => (
                        <div key={s} className="w-7 h-6 sm:w-10 sm:h-7 flex items-center justify-center text-[8px] sm:text-[9px] font-mono font-bold text-zinc-500 truncate" title={s}>
                          {s}
                        </div>
                      ))}
                    </div>
                    {/* Rows */}
                    {topN.map(symA => (
                      <div key={symA} className="flex gap-1 mb-1">
                        <div className="w-7 h-6 sm:w-10 sm:h-7 flex items-center justify-center text-[8px] sm:text-[9px] font-mono font-bold text-zinc-500 truncate" title={symA}>
                          {symA}
                        </div>
                        {topN.map(symB => {
                          const i = symIdx[symA];
                          const j = symIdx[symB];
                          const corr = corrData.matrix[i]?.[j] ?? 0;
                          const isDiagonal = symA === symB;
                          const absCorr = Math.abs(corr);
                          const isPositive = corr >= 0;

                          let bgClass = 'bg-zinc-800';
                          if (!isDiagonal) {
                            if (absCorr >= 0.85) bgClass = isPositive ? 'bg-red-500/80' : 'bg-green-500/80';
                            else if (absCorr >= 0.7) bgClass = isPositive ? 'bg-orange-500/70' : 'bg-blue-500/70';
                            else if (absCorr >= 0.4) bgClass = isPositive ? 'bg-yellow-500/50' : 'bg-cyan-500/50';
                            else if (absCorr >= 0.2) bgClass = isPositive ? 'bg-yellow-500/20' : 'bg-cyan-500/20';
                          }

                          return (
                            <div
                              key={symB}
                              className={`w-7 h-6 sm:w-10 sm:h-7 flex items-center justify-center text-[8px] sm:text-[9px] font-mono font-bold ${
                                isDiagonal ? 'bg-zinc-700 text-zinc-600' : absCorr >= 0.4 ? 'text-white' : 'text-zinc-500'
                              } ${bgClass} rounded-sm`}
                              title={`${symA} ↔ ${symB}: ${Number.isFinite(Number(corr)) ? Number(corr).toFixed(3) : '—'}`}
                            >
                              {isDiagonal ? '—' : Number.isFinite(Number(corr)) ? Number(corr).toFixed(2) : '—'}
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                </div>
                <p className="text-[9px] text-zinc-600 mt-1">Red = positive, Green = negative correlation</p>
              </div>
            );
          })()}

          {/* Recent Orders */}
          {data.recentOrders.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-zinc-500 mb-2">RECENT ORDERS</p>
              <div className="space-y-1.5">
                {data.recentOrders.slice(0, 5).map((order, i) => (
                  <div key={i} className="flex items-center justify-between text-xs flex-wrap gap-x-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-zinc-300">{order.symbol}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                        order.side === 'BUY' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                      }`}>{order.side}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500">{Number.isFinite(Number(order.shares)) ? Number(order.shares).toFixed(4) : '—'}</span>
                      <span className="font-mono text-zinc-400">@${Number.isFinite(Number(order.fillPrice)) ? Number(order.fillPrice).toFixed(2) : '—'}</span>
                      {order.pnl != null && (
                        <span className={`font-mono font-bold ${order.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {order.pnl >= 0 ? '+' : ''}{Number.isFinite(Number(order.pnl)) ? Number(order.pnl).toFixed(2) : '—'}
                        </span>
                      )}
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
