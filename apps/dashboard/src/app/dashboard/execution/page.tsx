'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Activity, Clock, CheckCircle2, XCircle, AlertTriangle, Shield, Zap, TrendingDown } from 'lucide-react';
import { AlertsPanel } from '@/components/trading/alerts-panel';
import { useOperatorState } from '@/components/trading/operator-state-provider';
import {
  INITIAL_EXECUTION_STATE,
  UNAVAILABLE_EXECUTION_STATE,
  executionMatchesOperator,
  loadExecutionState,
  type ExecutionState,
} from '@/lib/trading/operator-state';

export default function ExecutionPage() {
  const operator = useOperatorState();
  const [execution, setExecution] = useState<Readonly<ExecutionState>>(INITIAL_EXECUTION_STATE);
  const requestGeneration = useRef(0);

  const fetchData = useCallback(async (signal: AbortSignal) => {
    const generation = ++requestGeneration.current;
    if (operator.availability !== 'AVAILABLE'
      || operator.mode === 'UNKNOWN'
      || operator.killSwitchState === 'UNKNOWN') {
      if (!signal.aborted && generation === requestGeneration.current) {
        setExecution(UNAVAILABLE_EXECUTION_STATE);
      }
      return;
    }
    const next = await loadExecutionState(fetch, { signal });
    if (!signal.aborted && generation === requestGeneration.current) {
      setExecution(next);
    }
  }, [operator.availability, operator.killSwitchState, operator.mode]);

  useEffect(() => {
    const controller = new AbortController();
    const initialFetch = setTimeout(() => void fetchData(controller.signal), 0);
    const interval = setInterval(() => void fetchData(controller.signal), 5000);
    return () => {
      clearTimeout(initialFetch);
      clearInterval(interval);
      requestGeneration.current += 1;
      controller.abort();
    };
  }, [fetchData]);

  if (operator.availability === 'LOADING' || execution.availability === 'LOADING') {
    return (
      <div className="space-y-4 p-6">
        <h1 className="text-lg font-bold text-zinc-100">Execution Monitor</h1>
        <div className="border border-amber-500/30 bg-amber-950/30 p-4 text-sm text-amber-300">
          Execution state loading — mode, halt state, and metrics are unknown.
        </div>
      </div>
    );
  }

  if (operator.availability === 'UNAVAILABLE'
    || execution.availability === 'UNAVAILABLE'
    || execution.data === null) {
    return (
      <div className="space-y-4 p-6">
        <h1 className="text-lg font-bold text-zinc-100">Execution Monitor</h1>
        <div className="border border-red-500/40 bg-red-950/40 p-4 text-sm text-red-300">
          Execution data unavailable — positions, orders, P&amp;L, slippage, and halt state are unknown.
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {['Realized P&L', 'Unrealized P&L', 'Open Orders', 'Positions', 'Avg Slippage'].map((label) => (
            <div key={label} className="border border-zinc-800 bg-zinc-900/70 p-3">
              <p className="text-[10px] text-zinc-500">{label}</p>
              <p className="text-sm font-bold text-zinc-500">—</p>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const data = execution.data;
  const mode = data.mode;
  const halted = data.halted;
  const orders = data.open_orders;
  const trades = data.recent_trades;
  const positions = data.positions;
  const pnl = data.pnl;
  const alerts = data.alerts;
  const slippageHistory = data.slippage_history;
  const avgSlippage = data.avg_slippage;

  if (!executionMatchesOperator(data, operator)) {
    return (
      <div className="space-y-4 p-6">
        <h1 className="text-lg font-bold text-zinc-100">Execution Monitor</h1>
        <div className="border border-red-500/40 bg-red-950/40 p-4 text-sm text-red-300">
          Execution data conflicts with canonical operator state; metrics are withheld.
        </div>
      </div>
    );
  }

  const statusIcon = (status: string) => {
    switch (status) {
      case 'filled': return <CheckCircle2 className="h-3.5 w-3.5 text-green-400" />;
      case 'rejected': return <XCircle className="h-3.5 w-3.5 text-red-400" />;
      case 'canceled': return <XCircle className="h-3.5 w-3.5 text-zinc-500" />;
      case 'open': return <Clock className="h-3.5 w-3.5 text-blue-400 animate-pulse" />;
      case 'partially_filled': return <Activity className="h-3.5 w-3.5 text-yellow-400" />;
      default: return <Clock className="h-3.5 w-3.5 text-zinc-500" />;
    }
  };

  return (
    <div className="p-3 md:p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">Execution Monitor</h1>
          <p className="text-xs text-zinc-500">Live order tracking and position management</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Mode Badge */}
          <span className={`rounded-full px-3 py-1 text-[10px] font-bold uppercase ${
            mode === 'live' ? 'bg-red-500/20 text-red-400' :
            mode === 'dryrun' ? 'bg-yellow-500/20 text-yellow-400' :
            'bg-blue-500/20 text-blue-400'
          }`}>
            {mode === 'live' ? '🟢 LIVE' : mode === 'dryrun' ? '🟡 DRY RUN' : '📝 PAPER'}
          </span>

          {/* Halted Badge */}
          {halted && (
            <span className="rounded-full bg-red-600/20 px-3 py-1 text-[10px] font-bold text-red-400 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" />
              HALTED
            </span>
          )}
        </div>
      </div>

      {/* P&L Summary */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <div className="rounded border border-zinc-800 bg-zinc-900/70 p-3">
          <p className="text-[10px] text-zinc-500">Realized P&L</p>
          <p className={`text-sm font-bold ${pnl.total_realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            ${pnl.total_realized_pnl.toLocaleString(undefined, { minimumFractionDigits: 0 })}
          </p>
        </div>
        <div className="rounded border border-zinc-800 bg-zinc-900/70 p-3">
          <p className="text-[10px] text-zinc-500">Unrealized P&L</p>
          <p className={`text-sm font-bold ${pnl.total_unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            ${pnl.total_unrealized_pnl.toLocaleString(undefined, { minimumFractionDigits: 0 })}
          </p>
        </div>
        <div className="rounded border border-zinc-800 bg-zinc-900/70 p-3">
          <p className="text-[10px] text-zinc-500">Open Orders</p>
          <p className="text-sm font-bold text-zinc-100">{orders.length}</p>
        </div>
        <div className="rounded border border-zinc-800 bg-zinc-900/70 p-3">
          <p className="text-[10px] text-zinc-500">Positions</p>
          <p className="text-sm font-bold text-zinc-100">{positions.length}</p>
        </div>
        <div className="rounded border border-zinc-800 bg-zinc-900/70 p-3">
          <p className="text-[10px] text-zinc-500">Avg Slippage</p>
          <p className={`text-sm font-bold ${avgSlippage === null ? 'text-zinc-500' : avgSlippage > 2 ? 'text-red-400' : avgSlippage > 1 ? 'text-yellow-400' : 'text-green-400'}`}>
            {avgSlippage === null ? '—' : `${avgSlippage.toFixed(2)}%`}
          </p>
        </div>
      </div>

      {/* Slippage Monitor */}
      {slippageHistory.length > 0 && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center gap-2">
            <TrendingDown className="h-4 w-4 text-yellow-400" />
            <span className="text-xs font-bold text-zinc-200">Slippage Monitor</span>
            <span className="text-[10px] text-zinc-500">
              7d avg: {avgSlippage === null ? '—' : `${avgSlippage.toFixed(2)}%`}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-left px-4 py-2">Side</th>
                  <th className="text-right px-4 py-2">Qty</th>
                  <th className="text-right px-4 py-2">Expected</th>
                  <th className="text-right px-4 py-2">Fill</th>
                  <th className="text-right px-4 py-2">Slippage</th>
                  <th className="text-right px-4 py-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {slippageHistory.map((rec, i) => {
                  const isHigh = rec.slippage_pct > 2;
                  const isModerate = rec.slippage_pct > 1;
                  return (
                    <tr key={i} className={`border-b border-zinc-800/50 hover:bg-zinc-800/30 ${isHigh ? 'bg-red-500/5' : ''}`}>
                      <td className="px-4 py-2 font-mono text-zinc-200">{rec.symbol}</td>
                      <td className={`px-4 py-2 font-bold ${rec.side === 'buy' ? 'text-green-400' : 'text-red-400'}`}>
                        {rec.side.toUpperCase()}
                      </td>
                      <td className="px-4 py-2 text-right text-zinc-300">{rec.quantity.toFixed(4)}</td>
                      <td className="px-4 py-2 text-right text-zinc-500">${rec.expected_price.toFixed(2)}</td>
                      <td className="px-4 py-2 text-right text-zinc-300">${rec.fill_price.toFixed(2)}</td>
                      <td className={`px-4 py-2 text-right font-mono font-bold ${
                        isHigh ? 'text-red-400' : isModerate ? 'text-yellow-400' : 'text-green-400'
                      }`}>
                        {rec.slippage_pct.toFixed(3)}%
                        {isHigh && ' ⚠️'}
                      </td>
                      <td className="px-4 py-2 text-right text-zinc-500 text-[10px]">
                        {rec.created_at ? timeAgo(rec.created_at) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Positions */}
      {positions.length > 0 && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center gap-2">
            <Shield className="h-4 w-4 text-zinc-400" />
            <span className="text-xs font-bold text-zinc-200">Positions</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-right px-4 py-2">Qty</th>
                  <th className="text-right px-4 py-2">Entry</th>
                  <th className="text-right px-4 py-2">Current</th>
                  <th className="text-right px-4 py-2">SL</th>
                  <th className="text-right px-4 py-2">TP</th>
                  <th className="text-right px-4 py-2">U-P&L</th>
                  <th className="text-right px-4 py-2">R-P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos, i) => (
                  <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                    <td className="px-4 py-2 font-mono text-zinc-200">{pos.symbol}</td>
                      <td className="px-4 py-2 text-right text-zinc-300">{pos.quantity.toFixed(4)}</td>
                      <td className="px-4 py-2 text-right text-zinc-400">${pos.avg_entry_price.toFixed(2)}</td>
                      <td className="px-4 py-2 text-right text-zinc-200">${pos.current_price.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right">
                      {pos.stop_loss !== null ? (
                        <span className="text-red-400 font-mono">
                          ${pos.stop_loss.toFixed(2)}
                          {pos.trailing_stop ? <span className="text-[8px] ml-0.5">↗</span> : ''}
                        </span>
                      ) : <span className="text-zinc-600">—</span>}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {pos.take_profit !== null ? (
                        <span className="text-green-400 font-mono">${pos.take_profit.toFixed(2)}</span>
                      ) : <span className="text-zinc-600">—</span>}
                    </td>
                    <td className={`px-4 py-2 text-right font-mono ${pos.unrealized_pnl != null ? (pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400') : 'text-zinc-500'}`}>
                      ${pos.unrealized_pnl.toFixed(2)}
                    </td>
                    <td className={`px-4 py-2 text-right font-mono ${pos.realized_pnl != null ? (pos.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400') : 'text-zinc-500'}`}>
                      ${pos.realized_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Open Orders */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center gap-2">
          <Zap className="h-4 w-4 text-yellow-400" />
          <span className="text-xs font-bold text-zinc-200">Open Orders</span>
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">
            {orders.length}
          </span>
        </div>
        {orders.length === 0 ? (
          <div className="p-6 text-center text-xs text-zinc-500">No open orders</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-left px-4 py-2">Side</th>
                  <th className="text-right px-4 py-2">Qty</th>
                  <th className="text-right px-4 py-2">Filled</th>
                  <th className="text-right px-4 py-2">Price</th>
                  <th className="text-left px-4 py-2">Status</th>
                  <th className="text-right px-4 py-2">Age</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order, i) => (
                  <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                    <td className="px-4 py-2 font-mono text-zinc-200">{order.symbol}</td>
                    <td className={`px-4 py-2 font-bold ${order.side === 'buy' ? 'text-green-400' : 'text-red-400'}`}>
                      {order.side.toUpperCase()}
                    </td>
                    <td className="px-4 py-2 text-right text-zinc-300">{order.quantity.toFixed(4)}</td>
                    <td className="px-4 py-2 text-right text-zinc-300">{order.filled_quantity.toFixed(4)}</td>
                    <td className="px-4 py-2 text-right text-zinc-200">
                      {order.avg_fill_price === null ? '—' : `$${order.avg_fill_price.toFixed(2)}`}
                    </td>
                    <td className="px-4 py-2">
                      <span className="flex items-center gap-1">
                        {statusIcon(order.status)}
                        <span className="text-zinc-400">{order.status}</span>
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-zinc-500 text-[10px]">
                      {order.created_at ? timeAgo(order.created_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent Fills */}
      {trades.length > 0 && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center gap-2">
            <Activity className="h-4 w-4 text-green-400" />
            <span className="text-xs font-bold text-zinc-200">Recent Fills</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-left px-4 py-2">Side</th>
                  <th className="text-right px-4 py-2">Qty</th>
                  <th className="text-right px-4 py-2">Price</th>
                  <th className="text-right px-4 py-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, i) => (
                  <tr key={i} className="border-b border-zinc-800/50">
                    <td className="px-4 py-2 font-mono text-zinc-200">{trade.symbol}</td>
                    <td className={`px-4 py-2 font-bold ${trade.side === 'buy' ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.side.toUpperCase()}
                    </td>
                    <td className="px-4 py-2 text-right text-zinc-300">{trade.quantity.toFixed(4)}</td>
                    <td className="px-4 py-2 text-right text-zinc-200">${trade.price.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right text-zinc-500 text-[10px]">
                      {trade.trade_timestamp ? timeAgo(trade.trade_timestamp) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Signal Alerts Panel */}
      <AlertsPanel />

      {/* Execution Alerts */}
      {alerts.length > 0 && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-zinc-800 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-yellow-400" />
            <span className="text-xs font-bold text-zinc-200">Recent Alerts</span>
          </div>
          <div className="divide-y divide-zinc-800/50">
            {alerts.map((alert, i) => (
              <div key={i} className="px-4 py-2 flex items-start gap-2">
                <span className={`mt-0.5 text-[10px] ${
                  alert.severity === 'critical' ? 'text-red-400' :
                  alert.severity === 'warning' ? 'text-yellow-400' :
                  'text-blue-400'
                }`}>
                  {alert.severity === 'critical' ? '🔴' : alert.severity === 'warning' ? '🟡' : '🔵'}
                </span>
                <div>
                  <p className="text-xs text-zinc-300">{alert.message}</p>
                  <p className="text-[10px] text-zinc-600">{alert.created_at}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function timeAgo(timestamp: string): string {
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const seconds = Math.floor((now - then) / 1000);

  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}
