'use client';

import { useEffect, useState } from 'react';
import { ShieldAlert, Power, AlertTriangle } from 'lucide-react';

interface CircuitBreakerState {
  is_active: boolean;
  daily_loss_pct: number;
  threshold_pct: number;
  triggered_at: string | null;
  cooldown_until: string | null;
  remaining_until_trigger: number | null;
  trades_halted: boolean;
}

export function CircuitBreakerStatus({ initialData }: { initialData?: CircuitBreakerState }) {
  const hasServerData = initialData !== undefined;
  const [state, setState] = useState<CircuitBreakerState>(initialData ?? {
    is_active: false,
    daily_loss_pct: 0,
    threshold_pct: 3,
    triggered_at: null,
    cooldown_until: null,
    remaining_until_trigger: 3,
    trades_halted: false,
  });
  const [loading, setLoading] = useState(!hasServerData);

  useEffect(() => {
    if (hasServerData) return;
    const fetchState = async () => {
      try {
        const res = await fetch('/api/trading/circuit-breaker');
        if (res.ok) {
          const data = await res.json();
          setState(data);
        }
      } catch {
      } finally {
        setLoading(false);
      }
    };

    fetchState();
    const interval = setInterval(fetchState, 30000);
    return () => clearInterval(interval);
  }, [hasServerData]);

  const isActive = state.is_active;
  const dailyLossPct = state.daily_loss_pct;
  const threshold = state.threshold_pct;
  const stoppedAt = state.triggered_at;
  const remainingPct = state.remaining_until_trigger;
  const cooldownUntil = state.cooldown_until;

  const warningLevel = Math.abs(dailyLossPct) / threshold;
  const isNearThreshold = warningLevel >= 0.7 && warningLevel < 1;

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <span className="text-sm">Loading...</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`rounded-lg border ${isActive ? 'border-red-500/50 bg-red-500/10' : isNearThreshold ? 'border-orange-500/30 bg-orange-500/5' : 'border-zinc-800 bg-zinc-900/50'} p-4`}>
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${isActive ? 'bg-red-500/20' : isNearThreshold ? 'bg-orange-500/20' : 'bg-zinc-800'}`}>
            {isActive ? (
              <Power className="h-4 w-4 text-red-400" />
            ) : isNearThreshold ? (
              <AlertTriangle className="h-4 w-4 text-orange-400" />
            ) : (
              <ShieldAlert className="h-4 w-4 text-zinc-500" />
            )}
          </div>
          <div>
            <p className="text-sm font-bold text-zinc-200">Circuit Breaker</p>
            <p className="text-[10px] text-zinc-500">
              {isActive ? 'TRADING HALTED' : isNearThreshold ? 'Approaching threshold' : 'System normal'}
            </p>
          </div>
        </div>
        <div className={`text-right ${isActive ? 'text-red-400' : isNearThreshold ? 'text-orange-400' : 'text-zinc-500'}`}>
          <p className="text-[10px]">Daily Loss</p>
          <p className="text-sm font-mono font-bold">{Number.isFinite(Number(dailyLossPct)) ? Number(dailyLossPct).toFixed(2) : '—'}%</p>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-[10px] text-zinc-500 mb-1">
          <span>Threshold: -{threshold}%</span>
          <span>{remainingPct !== null && remainingPct !== undefined ? `${Math.abs(remainingPct).toFixed(2)}% remaining` : ''}</span>
        </div>
        <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all duration-300 ${isActive ? 'bg-red-500' : isNearThreshold ? 'bg-orange-500' : 'bg-green-500'}`}
            style={{ width: `${Math.min(100, (Math.abs(dailyLossPct) / threshold) * 100)}%` }}
          />
        </div>
      </div>

      {/* Status Messages */}
      {isActive && (
        <div className="rounded bg-red-500/10 border border-red-500/20 p-2">
          <p className="text-[10px] text-red-400">
            Trading halted at {stoppedAt ? new Date(stoppedAt).toLocaleTimeString() : 'unknown'}
          </p>
          {cooldownUntil && (
            <p className="text-[10px] text-zinc-500 mt-1">
              Cooldown until {new Date(cooldownUntil).toLocaleTimeString()}
            </p>
          )}
        </div>
      )}

      {isNearThreshold && !isActive && (
        <div className="rounded bg-orange-500/10 border border-orange-500/20 p-2">
          <p className="text-[10px] text-orange-400">
            Warning: Approaching circuit breaker threshold. Consider reducing position sizes.
          </p>
        </div>
      )}

      {!isActive && !isNearThreshold && (
        <div className="rounded bg-zinc-800/30 p-2">
          <p className="text-[10px] text-zinc-500">
            Circuit breaker will trigger when daily loss exceeds -{threshold}%
          </p>
        </div>
      )}
    </div>
  );
}
