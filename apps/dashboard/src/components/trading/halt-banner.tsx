'use client';

import { useEffect, useState } from 'react';

interface CircuitBreakerState {
  is_active: boolean;
  daily_loss_pct: number;
  threshold_pct: number;
  triggered_at: string | null;
  cooldown_until: string | null;
  remaining_until_trigger: number | null;
  trades_halted: boolean;
}

function formatCountdown(target: string): string {
  const now = Date.now();
  const end = new Date(target).getTime();
  const diff = end - now;
  if (diff <= 0) return '0h 0m';
  const hours = Math.floor(diff / (1000 * 60 * 60));
  const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
  return `${hours}h ${minutes}m`;
}

function formatTime(iso: string | null): string {
  if (!iso) return 'unknown';
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function HaltBanner() {
  const [state, setState] = useState<CircuitBreakerState | null>(null);
  const [loading, setLoading] = useState(true);
  const [countdown, setCountdown] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    async function fetchState() {
      try {
        const res = await fetch('/api/trading/circuit-breaker');
        if (res.ok && alive) {
          const data: CircuitBreakerState = await res.json();
          setState(data);
          if (data.cooldown_until) {
            setCountdown(formatCountdown(data.cooldown_until));
          }
        }
      } catch {
        // keep previous state on error
      } finally {
        if (alive) setLoading(false);
      }
    }

    fetchState();
    const interval = setInterval(fetchState, 60_000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  // Countdown tick — update every 60 seconds per spec
  useEffect(() => {
    if (!state?.cooldown_until) return;
    const tick = setInterval(() => {
      setCountdown(formatCountdown(state.cooldown_until!));
    }, 60_000);
    return () => clearInterval(tick);
  }, [state?.cooldown_until]);

  const isHalted = state?.is_active || state?.trades_halted;

  // Loading state — thin gray bar with pulse
  if (loading) {
    return (
      <div className="w-full h-1 bg-zinc-800 animate-pulse" />
    );
  }

  // Not halted — render nothing
  if (!isHalted) return null;

  return (
    <div className="w-full bg-red-600/20 border-b-2 border-red-500/50 px-4 py-3">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-xl flex-shrink-0">⛔</span>
          <div className="min-w-0">
            <p className="text-sm font-bold text-red-300">
              TRADING HALTED — Daily loss exceeded -{state.threshold_pct}% threshold
            </p>
            <p className="text-xs text-red-400/80 mt-0.5">
              Halted at {formatTime(state.triggered_at)}
              {state.cooldown_until && (
                <> · Resumes at {new Date(state.cooldown_until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} tomorrow ({countdown})</>
              )}
            </p>
          </div>
        </div>
        <span className="flex-shrink-0 rounded border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300">
          Clear via CLI
        </span>
      </div>
    </div>
  );
}
