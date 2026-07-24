'use client';

import { AlertTriangle, DollarSign, FileText } from 'lucide-react';

import { useOperatorState } from '@/components/trading/operator-state-provider';

export function ModeToggle() {
  const operator = useOperatorState();
  const unavailable = operator.availability !== 'AVAILABLE' || operator.mode === 'UNKNOWN';
  const classes = unavailable
    ? 'border-amber-500/30 bg-amber-500/10 text-amber-400'
    : operator.mode === 'LIVE'
      ? 'border-red-500/40 bg-red-500/10 text-red-400'
      : 'border-blue-500/30 bg-blue-500/10 text-blue-400';
  const icon = unavailable
    ? <AlertTriangle className="h-4 w-4" />
    : operator.mode === 'PAPER'
      ? <FileText className="h-4 w-4" />
      : <DollarSign className="h-4 w-4" />;

  return (
    <div
      title={operator.controlsEnabled ? 'Canonical operator mode' : 'Operator controls disabled'}
      className={`flex min-h-[44px] items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold ${classes}`}
    >
      {icon}
      {operator.mode} · {operator.controlsEnabled ? 'CANONICAL' : 'CONTROLS DISABLED'}
    </div>
  );
}
