'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';
import { ShieldOff } from 'lucide-react';

import { useOperatorState } from '@/components/trading/operator-state-provider';
import {
  createKillSwitchIntent,
  createPipelineCommand,
  killSwitchRequest,
  submitPipelineCommand,
  validateKillSwitchIntent,
  type KillSwitchIntent,
  type PipelineCommand,
  type PipelineKind,
} from '@/lib/trading/quick-actions-state';

export function QuickActions() {
  const {
    availability,
    controlsEnabled,
    invalidateSafetyState,
    killSwitchState,
    refresh,
    safetyRevision,
  } = useOperatorState();
  const [isLoading, setIsLoading] = useState<PipelineKind | null>(null);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [killSwitchLoading, setKillSwitchLoading] = useState(false);
  const [killIntent, setKillIntent] = useState<KillSwitchIntent | null>(null);
  const [killReason, setKillReason] = useState('');
  const pendingPipelineCommands = useRef<Partial<Record<PipelineKind, PipelineCommand>>>({});

  const killSwitchKnown = killSwitchState === 'ACTIVE' || killSwitchState === 'INACTIVE';
  const killSwitchActive = killSwitchState === 'ACTIVE';
  const actionsEnabled = controlsEnabled
    && killSwitchKnown
    && !killSwitchActive
    && !killSwitchLoading;

  function handleToggleKillSwitch() {
    if (!controlsEnabled || !killSwitchKnown) return;
    const intent = createKillSwitchIntent(killSwitchState, safetyRevision);
    if (intent === null) return;
    if (intent.action === 'on') setKillReason('');
    setKillIntent(intent);
  }

  async function confirmKillSwitch() {
    const request = killSwitchRequest(
      killIntent,
      killSwitchState,
      safetyRevision,
      killReason,
    );
    if (!controlsEnabled || !killSwitchKnown || request === null) {
      setKillIntent(null);
      setMessage({ type: 'error', text: 'Kill-switch authority changed; reopen the dialog' });
      return;
    }
    setKillIntent(null);
    setKillSwitchLoading(true);
    setMessage(null);
    invalidateSafetyState();

    try {
      const res = await fetch('/api/trading/kill-switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });

      if (!res.ok) {
        setMessage({ type: 'error', text: 'Kill-switch request was rejected' });
        return;
      }
    } catch {
      setMessage({ type: 'error', text: 'Kill-switch state could not be verified' });
    } finally {
      await refresh();
      setKillSwitchLoading(false);
      setKillReason('');
    }
  }

  async function runPipeline(kind: 'snapshot' | 'debate') {
    if (!actionsEnabled) return;
    const command = pendingPipelineCommands.current[kind]
      ?? createPipelineCommand(kind, 'BTC');
    if (command === null) {
      setMessage({ type: 'error', text: 'Pipeline command is invalid' });
      return;
    }
    pendingPipelineCommands.current[kind] = command;
    setIsLoading(kind);
    setMessage(null);

    const result = await submitPipelineCommand(fetch, command);
    if (result !== null) delete pendingPipelineCommands.current[kind];
    setMessage(result === null
      ? { type: 'error', text: 'Pipeline request could not be verified' }
      : {
          type: 'success',
          text: result.outcome === 'ENQUEUED'
            ? `${result.jobType} job enqueued (${result.jobId})`
            : `${result.jobType} request matched an existing job (${result.jobId})`,
        });
    setIsLoading(null);
  }

  const validKillIntent = validateKillSwitchIntent(
    killIntent,
    killSwitchState,
    safetyRevision,
  );

  const killLabel = killSwitchLoading
    ? '...'
    : !killSwitchKnown
      ? 'Kill State Unknown'
      : killSwitchActive
        ? 'Trading Halted'
        : 'Kill Switch Inactive';

  return (
    <div className="mb-6">
      {availability !== 'AVAILABLE' && (
        <div className="mb-4 border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          Operator state unavailable — all operational controls are disabled.
        </div>
      )}

      {killSwitchActive && (
        <div className="mb-4 flex items-center justify-between border border-red-500/40 bg-red-600/20 px-4 py-3">
          <div className="flex items-center gap-2">
            <ShieldOff className="h-4 w-4 text-red-400" />
            <span className="text-sm font-semibold text-red-400">TRADING HALTED</span>
          </div>
          <button
            onClick={handleToggleKillSwitch}
            disabled={!controlsEnabled || killSwitchLoading}
            className="rounded-md bg-red-600 px-3 py-1 text-xs font-medium text-zinc-100 hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {killSwitchLoading ? 'Resuming...' : 'Resume'}
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => void runPipeline('snapshot')}
          disabled={!actionsEnabled || isLoading !== null}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-zinc-100 transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLoading === 'snapshot' ? 'Running...' : 'Run Snapshot'}
        </button>
        <button
          onClick={() => void runPipeline('debate')}
          disabled={!actionsEnabled || isLoading !== null}
          className="rounded-md bg-purple-600 px-4 py-2 text-sm font-medium text-zinc-100 transition-colors hover:bg-purple-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLoading === 'debate' ? 'Running...' : 'Run BTC Debate'}
        </button>
        {actionsEnabled ? (
          <Link
            href="/dashboard/plan"
            className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-zinc-100 transition-colors hover:bg-green-700"
          >
            New Plan
          </Link>
        ) : (
          <span
            aria-disabled="true"
            className="cursor-not-allowed rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-zinc-100 opacity-50"
          >
            New Plan
          </span>
        )}

        <button
          onClick={handleToggleKillSwitch}
          disabled={!controlsEnabled || !killSwitchKnown || killSwitchLoading}
          className={`rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
            killSwitchActive
              ? 'bg-red-600 text-zinc-100 hover:bg-red-700'
              : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
          }`}
        >
          <span className="flex items-center gap-2">
            <ShieldOff className="h-4 w-4" />
            {killLabel}
          </span>
        </button>
      </div>

      {message && (
        <div className={`mt-3 rounded-md px-4 py-2 text-sm ${
          message.type === 'success'
            ? 'bg-green-500/20 text-green-400'
            : 'bg-red-500/20 text-red-400'
        }`}>
          {message.text}
        </div>
      )}

      {killIntent !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => setKillIntent(null)}
        >
          <div
            className="w-full max-w-md rounded-lg border border-zinc-700 bg-zinc-900 p-6 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <h3 className="mb-2 text-lg font-semibold text-zinc-100">
              {killIntent.action === 'off' ? 'Resume Trading?' : 'Activate Kill Switch'}
            </h3>
            <p className="mb-4 text-sm text-zinc-400">
              {killIntent.action === 'off'
                ? 'This requests removal of the current trading halt.'
                : 'This requests an immediate halt of trading activity.'}
            </p>

            {killIntent.action === 'on' && (
              <div className="mb-4">
                <label className="mb-1 block text-xs font-medium text-zinc-400">
                  Reason (optional)
                </label>
                <input
                  type="text"
                  value={killReason}
                  onChange={(event) => setKillReason(event.target.value)}
                  placeholder="e.g., Market volatility, system maintenance"
                  className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-200 placeholder-zinc-500 focus:border-red-500 focus:outline-none"
                />
              </div>
            )}

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setKillIntent(null)}
                className="rounded-md border border-zinc-600 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
              >
                Cancel
              </button>
              <button
                onClick={() => void confirmKillSwitch()}
                disabled={!controlsEnabled
                  || !killSwitchKnown
                  || killSwitchLoading
                  || validKillIntent === null}
                className={`rounded-md px-4 py-2 text-sm font-medium text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 ${
                  killIntent.action === 'off'
                    ? 'bg-green-600 hover:bg-green-700'
                    : 'bg-red-600 hover:bg-red-700'
                }`}
              >
                {validKillIntent === null
                  ? 'Authority Changed'
                  : killIntent.action === 'off'
                    ? 'Resume Trading'
                    : 'Halt Trading'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
