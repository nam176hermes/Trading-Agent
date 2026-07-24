'use client';

import { useEffect, useState, useCallback } from 'react';
import { timeAgo } from '@/lib/trading/utils';

interface Alert {
  timestamp: string;
  asset: string;
  action: string;
  confidence: number;
  price: number;
  reasoning: string;
  delivered: boolean;
}

type FilterMode = 'all' | 'actionable';

function getActionBg(action: string): string {
  const a = action.toUpperCase();
  if (a.includes('BUY')) return 'bg-green-500/15 text-green-400 border-green-500/20';
  if (a.includes('SELL')) return 'bg-red-500/15 text-red-400 border-red-500/20';
  if (a.includes('WATCH')) return 'bg-yellow-500/15 text-yellow-400 border-yellow-500/20';
  return 'bg-zinc-500/15 text-zinc-400 border-zinc-500/20';
}

function getConfidenceBarColor(action: string): string {
  const a = action.toUpperCase();
  if (a.includes('BUY')) return 'bg-green-500';
  if (a.includes('SELL')) return 'bg-red-500';
  if (a.includes('WATCH')) return 'bg-yellow-500';
  return 'bg-zinc-500';
}

function getEmoji(action: string): string {
  const a = action.toUpperCase();
  if (a.includes('BUY')) return '🟢';
  if (a.includes('SELL')) return '🔴';
  if (a.includes('WATCH')) return '🟡';
  return '⚪';
}

function isActionable(action: string): boolean {
  const a = action.toUpperCase();
  return a.includes('BUY') || a.includes('SELL');
}

export function AlertsPanel({ initialAlerts }: { initialAlerts?: Alert[] }) {
  const hasServerData = initialAlerts !== undefined && initialAlerts.length > 0;
  const [alerts, setAlerts] = useState<Alert[]>(initialAlerts ?? []);
  const [loading, setLoading] = useState(!hasServerData);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);
  const [filter, setFilter] = useState<FilterMode>(hasServerData ? 'all' : 'all');
  const [lastFetch, setLastFetch] = useState<number | null>(null);
  const [displayTimestamp, setDisplayTimestamp] = useState<number | null>(null);

  const fetchAlerts = useCallback(async () => {
    try {
      setError(null);
      const res = await fetch('/api/trading/alerts');
      if (res.ok) {
        const data = await res.json();
        setAlerts(data.alerts || []);
      } else {
        setError('Failed to load alerts');
      }
    } catch {
      setError('Failed to load alerts');
    }
    setLoading(false);
    const fetchedAt = Date.now();
    setLastFetch(fetchedAt);
    setDisplayTimestamp(fetchedAt);
  }, []);

  useEffect(() => {
    if (hasServerData) return;
    const initialFetch = setTimeout(fetchAlerts, 0);
    const interval = setInterval(fetchAlerts, 30_000);
    return () => {
      clearTimeout(initialFetch);
      clearInterval(interval);
    };
  }, [fetchAlerts, hasServerData]);

  const filtered = filter === 'actionable'
    ? alerts.filter(a => isActionable(a.action))
    : alerts;
  const secondsSinceFetch = lastFetch !== null && displayTimestamp !== null
    ? Math.floor((displayTimestamp - lastFetch) / 1000)
    : null;

  function handleToggle() {
    setOpen(current => !current);
    if (lastFetch !== null) setDisplayTimestamp(Date.now());
  }

  function handleFilter(nextFilter: FilterMode) {
    setFilter(nextFilter);
    if (lastFetch !== null) setDisplayTimestamp(Date.now());
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50">
      {/* Header */}
      <button
        onClick={handleToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-zinc-800/30 transition-colors rounded-lg"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-zinc-200">Alerts</span>
          {alerts.length > 0 && (
            <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-[10px] font-bold text-blue-400">
              {alerts.length}
            </span>
          )}
          <span className="text-[10px] text-zinc-500">
            auto-refresh 30s
          </span>
        </div>
        <span className={`text-zinc-400 transition-transform text-xs ${open ? 'rotate-90' : ''}`}>▶</span>
      </button>

      {open && (
        <div className="px-4 pb-4">
          {/* Filter chips */}
          <div className="flex items-center gap-2 mb-3">
            <button
              onClick={() => handleFilter('all')}
              className={`rounded px-2.5 py-1 text-[10px] font-bold transition-colors ${
                filter === 'all'
                  ? 'bg-zinc-700 text-zinc-100'
                  : 'bg-zinc-800/50 text-zinc-400 hover:text-zinc-200'
              }`}
            >
              All
            </button>
            <button
              onClick={() => handleFilter('actionable')}
              className={`rounded px-2.5 py-1 text-[10px] font-bold transition-colors ${
                filter === 'actionable'
                  ? 'bg-zinc-700 text-zinc-100'
                  : 'bg-zinc-800/50 text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Actionable
            </button>
            {secondsSinceFetch != null && (
              <span className="ml-auto text-[10px] text-zinc-600">
                refreshed {secondsSinceFetch}s ago
              </span>
            )}
          </div>

          {/* Error state */}
          {error && (
            <div className="py-4 text-center">
              <p className="text-xs text-red-400 mb-2">{error}</p>
              <button onClick={fetchAlerts} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
            </div>
          )}

          {/* Loading state */}
          {!error && loading && (
            <p className="text-xs text-zinc-500 py-4 text-center">Loading alerts...</p>
          )}

          {/* Empty state */}
          {!error && !loading && filtered.length === 0 && (
            <p className="text-xs text-zinc-500 py-4 text-center">
              {filter === 'actionable' ? 'No actionable alerts' : 'No alerts yet'}
            </p>
          )}

          {/* Alert list */}
          {!error && !loading && filtered.length > 0 && (
            <div className="max-h-[364px] overflow-y-auto space-y-1">
              {filtered.map((alert, i) => {
                const confPct = Math.round(alert.confidence * 100);
                const confBarColor = getConfidenceBarColor(alert.action);
                const actionBg = getActionBg(alert.action);

                return (
                  <div
                    key={i}
                    className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-zinc-800/30 transition-colors text-xs"
                  >
                    {/* Emoji + Asset */}
                    <span className="w-5 text-center flex-shrink-0">
                      {getEmoji(alert.action)}
                    </span>
                    <span className="font-mono font-bold text-zinc-200 w-10 flex-shrink-0">
                      {alert.asset}
                    </span>

                    {/* Action badge */}
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold flex-shrink-0 ${actionBg}`}>
                      {alert.action}
                    </span>

                    {/* Confidence bar */}
                    <div className="flex-1 min-w-0 flex items-center gap-1">
                      <span className="font-mono text-[10px] text-zinc-400 w-7 text-right flex-shrink-0">
                        {confPct}%
                      </span>
                      <div className="flex-1 h-2 rounded-full bg-zinc-800 overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${confBarColor}`}
                          style={{ width: `${confPct}%` }}
                        />
                      </div>
                    </div>

                    {/* Time ago */}
                    <span className="text-[10px] text-zinc-500 flex-shrink-0 w-14 text-right">
                      {timeAgo(alert.timestamp)}
                    </span>

                    {/* Delivered status */}
                    <span className={`flex-shrink-0 w-4 text-center font-bold ${
                      alert.delivered ? 'text-green-400' : 'text-zinc-600'
                    }`}>
                      {alert.delivered ? '✓' : '✗'}
                    </span>
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
