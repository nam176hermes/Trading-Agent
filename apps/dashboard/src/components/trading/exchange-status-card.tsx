'use client';

import { useEffect, useRef, useState } from 'react';
import { Radio, CheckCircle, XCircle } from 'lucide-react';
import { useOperatorState } from '@/components/trading/operator-state-provider';
import {
  INITIAL_EXCHANGE_STATUS_STATE,
  UNAVAILABLE_EXCHANGE_STATUS_STATE,
  exchangeStatusMatchesOperator,
  loadExchangeStatusState,
  type ExchangeStatusState,
  type OperatorMode,
} from '@/lib/trading/operator-state';

function modeBadge(mode: OperatorMode) {
  switch (mode) {
    case 'PAPER':
      return { label: 'Paper', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
    case 'DRYRUN':
      return { label: 'Dry Run', color: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20' };
    case 'LIVE':
      return { label: 'Live', color: 'bg-red-500/10 text-red-400 border-red-500/20' };
    default:
      return { label: 'Unknown', color: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20' };
  }
}

export function ExchangeStatusCard() {
  const operator = useOperatorState();
  const [status, setStatus] = useState<Readonly<ExchangeStatusState>>(
    INITIAL_EXCHANGE_STATUS_STATE,
  );
  const requestGeneration = useRef(0);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    const controller = new AbortController();

    if (operator.availability !== 'AVAILABLE') {
      queueMicrotask(() => {
        if (generation === requestGeneration.current) {
          setStatus(UNAVAILABLE_EXCHANGE_STATUS_STATE);
        }
      });
      return () => {
        requestGeneration.current += 1;
        controller.abort();
      };
    }

    queueMicrotask(() => {
      if (generation === requestGeneration.current) {
        setStatus(INITIAL_EXCHANGE_STATUS_STATE);
      }
    });
    void loadExchangeStatusState(fetch, { signal: controller.signal }).then((next) => {
      if (!controller.signal.aborted && generation === requestGeneration.current) {
        setStatus(next);
      }
    });

    return () => {
      requestGeneration.current += 1;
      controller.abort();
    };
  }, [operator.availability, operator.liveExecutionEnabled, operator.mode]);

  if (operator.availability === 'LOADING'
    || (operator.availability === 'AVAILABLE' && status.availability === 'LOADING')) {
    return <p className="text-xs text-zinc-500">Loading exchange status...</p>;
  }

  if (operator.availability !== 'AVAILABLE'
    || status.availability !== 'AVAILABLE'
    || status.data === null
    || !exchangeStatusMatchesOperator(status.data, operator)) {
    const unknown = modeBadge('UNKNOWN');
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Radio className="h-3.5 w-3.5 text-zinc-500" />
          <span className="text-xs text-zinc-500">Mode</span>
          <span className={`rounded-full border px-2 py-0.5 text-[10px] ${unknown.color}`}>
            {unknown.label}
          </span>
        </div>
        <p className="text-xs text-red-300">
          Exchange status unavailable — connectivity and balances are unknown.
        </p>
      </div>
    );
  }

  const badge = modeBadge(operator.mode);
  const exchanges = Object.entries(status.data.exchanges);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 mb-2">
        <Radio className="w-3.5 h-3.5 text-violet-400" />
        <span className="text-xs text-zinc-500">Mode</span>
        <span className={`text-[10px] px-2 py-0.5 rounded-full border ${badge.color}`}>
          {badge.label}
        </span>
      </div>
      {exchanges.length === 0 ? (
        <p className="text-xs text-zinc-500">No exchanges configured</p>
      ) : (
        exchanges.map(([name, info]) => (
          <div key={name} className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-zinc-800/30">
            <span className="text-[11px] font-mono text-zinc-300 capitalize">{name}</span>
            <div className="flex items-center gap-1.5">
              {info.connected ? (
                <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <XCircle className="w-3.5 h-3.5 text-red-400" />
              )}
              <span className={`text-[10px] ${info.connected ? 'text-emerald-400' : 'text-red-400'}`}>
                {info.connected ? (info.sandbox ? 'Sandbox' : 'Connected') : (info.error || 'Down')}
              </span>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
