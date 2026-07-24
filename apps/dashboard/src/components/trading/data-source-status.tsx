'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Database, ChevronDown, ChevronUp, CheckCircle, XCircle, Clock } from 'lucide-react';

import {
  INITIAL_DATA_SOURCES_STATE,
  loadDataSourcesState,
  summarizeDataSources,
  type DataSourceHealth,
  type DataSourcesState,
} from '@/lib/trading/data-source-state';

const STATUS_CONFIG: Record<
  DataSourceHealth,
  { icon: typeof CheckCircle; color: string; bg: string; border: string }
> = {
  active: {
    icon: CheckCircle,
    color: 'text-green-400',
    bg: 'bg-green-500/10',
    border: 'border-green-500/30',
  },
  error: {
    icon: XCircle,
    color: 'text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/30',
  },
  unknown: {
    icon: Clock,
    color: 'text-zinc-400',
    bg: 'bg-zinc-500/10',
    border: 'border-zinc-500/30',
  },
};

export function DataSourceStatus() {
  const [state, setState] = useState<Readonly<DataSourcesState>>(
    INITIAL_DATA_SOURCES_STATE,
  );
  const [expanded, setExpanded] = useState(true);
  const requestGeneration = useRef(0);

  const fetchSources = useCallback(async (signal: AbortSignal) => {
    const generation = ++requestGeneration.current;
    const next = await loadDataSourcesState(fetch, { signal });
    if (!signal.aborted && generation === requestGeneration.current) {
      setState(next);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const initialFetch = window.setTimeout(() => void fetchSources(controller.signal), 0);
    const interval = window.setInterval(() => void fetchSources(controller.signal), 60_000);
    return () => {
      window.clearTimeout(initialFetch);
      window.clearInterval(interval);
      requestGeneration.current += 1;
      controller.abort();
    };
  }, [fetchSources]);

  const summary = summarizeDataSources(state);

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between border-b border-zinc-800 bg-zinc-900 px-4 py-2.5 transition-colors hover:bg-zinc-800/60"
      >
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-cyan-400" />
          <span className="text-xs font-bold text-zinc-200">Data Sources</span>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {summary.counts === null ? (
            <span className="text-[10px] font-bold uppercase text-zinc-400">Status unknown</span>
          ) : (
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-green-400">{summary.counts.active} active</span>
              {summary.counts.error > 0 && (
                <span className="text-red-400">{summary.counts.error} errors</span>
              )}
              {summary.counts.unknown > 0 && (
                <span className="text-zinc-400">{summary.counts.unknown} unknown</span>
              )}
            </div>
          )}
          {expanded
            ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" />
            : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
        </div>
      </button>

      {expanded && (
        <div className="p-4">
          {summary.availability === 'LOADING' ? (
            <div className="flex items-center gap-2 text-zinc-500">
              <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
              <span className="text-sm">Loading status...</span>
            </div>
          ) : summary.availability === 'UNAVAILABLE' || summary.sources === null ? (
            <div className="text-center text-sm text-red-300">
              Data-source status unavailable — health and counts are unknown.
            </div>
          ) : summary.sources.length === 0 ? (
            <div className="text-center text-sm text-zinc-500">
              Canonical source returned an authoritative empty list.
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {summary.sources.map((source) => {
                const config = STATUS_CONFIG[source.status];
                const Icon = config.icon;

                return (
                  <div
                    key={source.id}
                    className={`rounded border ${config.border} ${config.bg} p-3`}
                  >
                    <div className="mb-2 flex items-start justify-between">
                      <div className="flex items-center gap-2">
                        <Icon className={`h-3.5 w-3.5 ${config.color}`} />
                        <span className="text-xs font-semibold text-zinc-200">{source.name}</span>
                      </div>
                      <span className={`text-[10px] font-bold uppercase ${config.color}`}>
                        {source.status}
                      </span>
                    </div>

                    <div className="space-y-1 text-[10px] text-zinc-500">
                      <div className="flex justify-between">
                        <span>Last update:</span>
                        <span className="font-mono text-zinc-400">
                          {source.lastUpdate === null
                            ? '—'
                            : new Date(source.lastUpdate).toLocaleTimeString()}
                        </span>
                      </div>
                      {source.latency !== null && (
                        <div className="flex justify-between">
                          <span>Latency:</span>
                          <span className={`font-mono ${
                            source.latency < 100
                              ? 'text-green-400'
                              : source.latency < 500
                                ? 'text-yellow-400'
                                : 'text-red-400'
                          }`}>
                            {source.latency}ms
                          </span>
                        </div>
                      )}
                      {source.rateLimitRemaining !== null && (
                        <div className="flex justify-between">
                          <span>Rate limit remaining:</span>
                          <span className="font-mono text-zinc-400">
                            {source.rateLimitRemaining}
                          </span>
                        </div>
                      )}
                    </div>

                    {source.error !== null && (
                      <div className="mt-2 rounded bg-red-500/10 p-1.5">
                        <p className="truncate text-[10px] text-red-400">{source.error}</p>
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
