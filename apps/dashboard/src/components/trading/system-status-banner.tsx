'use client';

import { Activity, AlertTriangle, ShieldCheck, WifiOff } from 'lucide-react';

import { useOperatorState } from '@/components/trading/operator-state-provider';

export function SystemStatusBanner() {
  const operator = useOperatorState();

  if (operator.availability === 'LOADING') {
    return (
      <div className="mb-4 border border-amber-500/30 bg-amber-950/30 p-3 text-xs text-amber-300">
        <span className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5 animate-pulse" />
          Canonical safety state is loading; service readiness and operational metrics are unknown.
        </span>
      </div>
    );
  }

  if (operator.availability === 'UNAVAILABLE') {
    return (
      <div className="mb-4 border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-300">
        <span className="flex items-center gap-2">
          <WifiOff className="h-3.5 w-3.5" />
          Canonical safety state unavailable; service readiness, mode, kill state, and metrics are unknown.
        </span>
      </div>
    );
  }

  const blocked = !operator.controlsEnabled;
  return (
    <div className={`mb-4 border p-3 text-xs ${
      blocked
        ? 'border-amber-500/30 bg-amber-950/30 text-amber-300'
        : 'border-blue-500/30 bg-blue-950/30 text-blue-300'
    }`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5 font-medium">
          {blocked
            ? <AlertTriangle className="h-3.5 w-3.5" />
            : <ShieldCheck className="h-3.5 w-3.5" />}
          Safety metadata available
        </span>
        <span>Readiness: {operator.health} (not reported)</span>
        <span>Mode: {operator.mode}</span>
        <span>Kill switch: {operator.killSwitchState}</span>
        <span>Execution: {operator.executionCapability}</span>
        <span>Operational metrics: —</span>
        <span className="ml-auto font-semibold">
          {operator.controlsEnabled ? 'Controls enabled' : 'Controls disabled'}
        </span>
      </div>
    </div>
  );
}
