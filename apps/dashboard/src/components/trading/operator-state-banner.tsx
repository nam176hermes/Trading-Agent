'use client';

import { AlertTriangle, LoaderCircle, ShieldCheck } from 'lucide-react';

import { useOperatorState } from '@/components/trading/operator-state-provider';

export function OperatorStateBanner() {
  const state = useOperatorState();

  if (state.availability === 'LOADING') {
    return (
      <div className="mx-3 mt-3 border border-amber-500/30 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-300 md:mx-4">
        <span className="flex items-center gap-2">
          <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          Safety-state metadata loading — mode, kill state, and controls are unknown; readiness is not reported.
        </span>
      </div>
    );
  }

  if (state.availability === 'UNAVAILABLE') {
    return (
      <div className="mx-3 mt-3 border border-red-500/40 bg-red-950/40 px-3 py-2 text-[11px] text-red-300 md:mx-4">
        <span className="flex items-center gap-2">
          <AlertTriangle className="h-3.5 w-3.5" />
          Safety-state metadata unavailable — mode, kill state, and metrics are unknown; readiness is not reported and controls are disabled.
        </span>
      </div>
    );
  }

  const blocked = !state.controlsEnabled;
  const halted = state.killSwitchState === 'ACTIVE';
  const classes = halted
    ? 'border-red-500/40 bg-red-950/40 text-red-300'
    : blocked
      ? 'border-amber-500/30 bg-amber-950/30 text-amber-300'
      : 'border-blue-500/30 bg-blue-950/30 text-blue-300';

  return (
    <div className={`mx-3 mt-3 border px-3 py-2 text-[11px] md:mx-4 ${classes}`}>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {halted || blocked
          ? <AlertTriangle className="h-3.5 w-3.5" />
          : <ShieldCheck className="h-3.5 w-3.5" />}
        <strong>Canonical safety-state metadata available</strong>
        <span>Mode {state.mode}</span>
        <span>Kill {state.killSwitchState}</span>
        <span>Readiness {state.health} (not reported)</span>
        <span>Metrics unavailable</span>
        <span>{state.controlsEnabled ? 'Controls enabled' : 'Controls disabled'}</span>
      </span>
    </div>
  );
}
