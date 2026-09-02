'use client';

import { useState, useEffect, useCallback } from 'react';
import { Key, Plus, RefreshCw, Shield, AlertTriangle, CheckCircle2, XCircle, Zap, Play, Square, RotateCcw, DollarSign, Bot } from 'lucide-react';
import { DataSourceStatus } from '@/components/trading/data-source-status';
import { useOperatorState } from '@/components/trading/operator-state-provider';
import {
  INITIAL_SERVICE_STATE,
  loadServiceState,
  type OperatorMode,
  type ServiceState,
} from '@/lib/trading/operator-state';
import {
  INITIAL_AGENTS_STATE,
  INITIAL_COSTS_STATE,
  exchangeConfigurationPresentation,
  loadAgentsState,
  loadCostsState,
  parseExchangeConfigPayload,
  settingsOperatorStatus,
  summarizeAgentsState,
  type AgentsState,
  type CostsState,
  type ExchangeConfig,
} from '@/lib/trading/settings-state';

interface ConnectionTestResult {
  testing?: boolean;
  connected?: boolean;
  error?: string;
  [key: string]: unknown;
}

function connectionErrorMessage(error: unknown): string {
  if (typeof error === 'string') return error;
  if (typeof error === 'object' && error !== null && 'message' in error) {
    const message = error.message;
    if (typeof message === 'string') return message;
  }
  return 'Unknown error';
}

function numberRecord(value: unknown): Record<string, number> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  return entries.every((entry): entry is [string, number] => typeof entry[1] === 'number')
    ? Object.fromEntries(entries)
    : null;
}

export default function SettingsPage() {
  const operator = useOperatorState();
  const [exchanges, setExchanges] = useState<ExchangeConfig | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ exchange: 'binance', apiKey: '', secret: '', password: '' });
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const testResult: Record<string, ConnectionTestResult> = {};
  const [serviceStatus, setServiceStatus] = useState<Readonly<ServiceState>>(
    INITIAL_SERVICE_STATE,
  );
  const [serviceLoading, setServiceLoading] = useState(false);
  const serviceControlsEnabled = false;

  const fetchServiceStatus = useCallback(async (signal?: AbortSignal) => {
    setServiceLoading(true);
    const next = await loadServiceState(fetch, { signal });
    if (!signal?.aborted) {
      setServiceStatus(next);
      setServiceLoading(false);
    }
  }, []);

  // Load read-only service and credential status on mount.
  useEffect(() => {
    const controller = new AbortController();
    const initialFetch = setTimeout(() => void fetchServiceStatus(controller.signal), 0);
    void fetch('/api/trading/keys', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error('Exchange configuration unavailable');
      const parsed = parseExchangeConfigPayload(await response.json());
      if (parsed === null) throw new Error('Invalid exchange configuration');
      setExchanges(parsed);
    }).catch(() => {
      if (!controller.signal.aborted) setExchanges(null);
    });

    return () => {
      clearTimeout(initialFetch);
      controller.abort();
    };
  }, [fetchServiceStatus]);

  const handleSubmit = async () => {
    setLoading(true);
    setMessage('');
    try {
      const res = await fetch('/api/trading/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (res.ok) {
        setMessage(`✅ Keys saved for ${form.exchange}`);
        setShowAdd(false);
        setForm({ exchange: 'binance', apiKey: '', secret: '', password: '' });
        // Refresh exchange list
        setExchanges((prev) => ({
          ...(prev ?? {}),
          [form.exchange]: { configured: true },
        }));
      } else {
        setMessage(`❌ ${data.error || 'Failed'}`);
      }
    } catch {
      setMessage('❌ Network error');
    } finally {
      setLoading(false);
    }
  };

  const serviceAction = async (action: 'start' | 'stop' | 'restart') => {
    if (!serviceControlsEnabled) return;
    setServiceLoading(true);
    try {
      const response = await fetch('/api/trading/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!response.ok) throw new Error('Service control unavailable');
      // Wait for state change, then refresh
      await new Promise((r) => setTimeout(r, 2000));
      await fetchServiceStatus();
    } catch {
      setMessage('❌ Service control failed');
    } finally {
      setServiceLoading(false);
    }
  };

  const modeBadge = (mode: OperatorMode) => {
    const colors: Record<OperatorMode, string> = {
      PAPER: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
      DRYRUN: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
      LIVE: 'bg-red-500/10 text-red-400 border-red-500/30',
      UNKNOWN: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/30',
    };
    const labels: Record<OperatorMode, string> = {
      PAPER: '📝 Paper',
      DRYRUN: '🟡 Dry Run',
      LIVE: '🔴 Live',
      UNKNOWN: 'UNKNOWN',
    };
    return (
      <span
        className={`rounded border px-2 py-0.5 text-[10px] font-bold uppercase ${colors[mode]}`}
      >
        {labels[mode]}
      </span>
    );
  };

  const operatorStatus = settingsOperatorStatus({
    availability: operator.availability,
    mode: operator.mode,
    liveExecutionEnabled: operator.liveExecutionEnabled,
    liveTradingApproved: operator.liveTradingApproved,
  });
  const serviceRunning = serviceStatus.availability === 'AVAILABLE' && serviceStatus.data !== null
    ? serviceStatus.data.active
    : null;

  return (
    <div className="p-3 md:p-6 space-y-4">
      <div>
        <h1 className="text-lg font-bold text-zinc-100">Settings</h1>
        <p className="text-xs text-zinc-500">Exchange API keys, mode, and system configuration</p>
      </div>

      {/* Mode Selection */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-zinc-400" />
            <span className="text-xs font-bold text-zinc-200">Trading Mode</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-zinc-500">Requested:</span>
            {modeBadge(operator.requestedMode)}
            <span className="text-[10px] text-zinc-500">Effective:</span>
            {modeBadge(operator.mode)}
          </div>
        </div>
        <p className="mt-2 text-[10px] text-zinc-500">
          {operator.availability === 'AVAILABLE'
            ? 'Requested and effective modes are reported by the canonical operator-state boundary.'
            : 'Canonical operator mode is unavailable; current mode remains UNKNOWN.'}
        </p>
        <p className="mt-1 text-[10px] font-medium text-amber-400">
          Change mode via CLI
        </p>
      </div>

      {/* Exchange API Keys */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Key className="h-4 w-4 text-zinc-400" />
            <span className="text-xs font-bold text-zinc-200">Exchange API Keys</span>
          </div>
          <button
            disabled
            title="Credential management is disabled in this dashboard"
            className="flex cursor-not-allowed items-center gap-1 rounded border border-zinc-700 bg-zinc-800/50 px-3 py-1.5 text-[10px] text-zinc-600"
          >
            <Plus className="h-3 w-3" />
            Add Key
          </button>
        </div>

        <p className="mb-3 rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[10px] text-amber-300">
          Credential storage and exchange connection tests are unavailable from the dashboard.
        </p>

        {message && (
          <div
            className={`rounded border px-3 py-2 mb-3 text-xs ${
              message.startsWith('✅')
                ? 'border-green-500/30 bg-green-500/10 text-green-400'
                : 'border-red-500/30 bg-red-500/10 text-red-400'
            }`}
          >
            {message}
          </div>
        )}

        {/* Configured exchanges */}
        {exchanges === null ? (
          <p className="mb-3 text-xs text-red-300">
            Exchange configuration unavailable — configured status is unknown.
          </p>
        ) : Object.keys(exchanges).length === 0 ? (
          <p className="mb-3 text-xs text-zinc-500">No configured exchanges reported.</p>
        ) : (
          <div className="space-y-2 mb-3">
            {Object.entries(exchanges).map(([name, configuration]) => {
              const presentation = exchangeConfigurationPresentation(configuration);
              return (
                <div
                  key={name}
                  className="flex items-center justify-between rounded border border-zinc-700 bg-zinc-800/30 px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    {presentation.configured ? (
                      <CheckCircle2 className="h-3 w-3 text-green-400" />
                    ) : (
                      <XCircle className="h-3 w-3 text-zinc-500" />
                    )}
                    <span className="text-xs text-zinc-300 font-bold uppercase">{name}</span>
                    <span className={`text-[10px] ${
                      presentation.configured ? 'text-green-400' : 'text-zinc-500'
                    }`}>
                      {presentation.label}
                    </span>
                  </div>
                  <button
                    disabled
                    title="Exchange connection tests are disabled"
                    className="flex cursor-not-allowed items-center gap-1 rounded border border-zinc-700 bg-zinc-800/50 px-2 py-1 text-[10px] text-zinc-600"
                  >
                    {testResult[name]?.testing ? (
                      <RefreshCw className="h-3 w-3 animate-spin" />
                    ) : (
                      <Zap className="h-3 w-3" />
                    )}
                    Test Connection
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {/* Test results */}
        {Object.entries(testResult).map(([exchange, result]) => {
          if (result.testing) return null;
          const balances = numberRecord(result.balances);
          return (
            <div
              key={`test-${exchange}`}
              className={`rounded border px-3 py-2 mb-3 text-xs ${
                result.connected
                  ? 'border-green-500/30 bg-green-500/10 text-green-400'
                  : 'border-red-500/30 bg-red-500/10 text-red-400'
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                {result.connected ? (
                  <CheckCircle2 className="h-3 w-3" />
                ) : (
                  <XCircle className="h-3 w-3" />
                )}
                <span className="font-bold">
                  {result.connected
                    ? `Connected to ${exchange} testnet ✅`
                    : `Connection failed: ${connectionErrorMessage(result.error)}`}
                </span>
              </div>
              {balances && Object.keys(balances).length > 0 && (
                <div className="mt-1 space-y-0.5">
                  {Object.entries(balances).map(([asset, amount]) => (
                    <div key={asset} className="flex justify-between text-[10px]">
                      <span>{asset}</span>
                      <span className="font-mono">{Number.isFinite(Number(amount)) ? Number(amount).toFixed(4) : '—'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {showAdd && (
          <div className="rounded border border-zinc-700 bg-zinc-900/70 p-4 space-y-3 mb-3">
            <div>
              <label className="text-[10px] text-zinc-500 block mb-1">Exchange</label>
              <select
                value={form.exchange}
                onChange={(e) => setForm({ ...form, exchange: e.target.value })}
                className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs text-zinc-200"
              >
                <option value="binance">Binance</option>
                <option value="coinbase">Coinbase</option>
                <option value="bybit">Bybit</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-zinc-500 block mb-1">API Key</label>
              <input
                type="password"
                value={form.apiKey}
                onChange={(e) => setForm({ ...form, apiKey: e.target.value })}
                className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs text-zinc-200 font-mono"
                placeholder="Enter API key..."
              />
            </div>
            <div>
              <label className="text-[10px] text-zinc-500 block mb-1">API Secret</label>
              <input
                type="password"
                value={form.secret}
                onChange={(e) => setForm({ ...form, secret: e.target.value })}
                className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs text-zinc-200 font-mono"
                placeholder="Enter API secret..."
              />
            </div>
            <div>
              <label className="text-[10px] text-zinc-500 block mb-1">Password (optional)</label>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs text-zinc-200 font-mono"
                placeholder="Optional passphrase..."
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSubmit}
                disabled={loading || !form.apiKey || !form.secret}
                className="flex items-center gap-1 rounded bg-violet-600 px-4 py-2 text-xs font-bold text-white hover:bg-violet-500 disabled:opacity-50"
              >
                {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                Save Keys
              </button>
              <button
                onClick={() => setShowAdd(false)}
                className="rounded border border-zinc-700 px-4 py-2 text-xs text-zinc-400 hover:text-zinc-200"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Credential boundary */}
        <div className="mt-3 rounded border border-violet-500/20 bg-violet-500/5 p-3">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="h-3 w-3 text-violet-400" />
            <span className="text-[10px] font-bold text-violet-300">Credential Boundary Disabled</span>
          </div>
          <div className="space-y-1 text-[10px] text-violet-400/80">
            <p>• Dashboard credential writes and connection tests are disabled.</p>
            <p>
              • Canonical operator mode: {operatorStatus.mode} ({operatorStatus.availability}).
            </p>
            <p>
              • Live execution: {operatorStatus.liveExecutionEnabled}; live approval:{' '}
              {operatorStatus.liveTradingApproved}.
            </p>
            <p>• Live trading policy is NO_GO. Do not enter or provision real exchange keys here.</p>
          </div>
        </div>

        <div className="text-xs text-zinc-500 space-y-1 mt-3">
          <p>• Credential state is read-only and unavailable when the canonical source is absent.</p>
          <p>• Real trading credentials are outside this dashboard&apos;s approved boundary.</p>
        </div>

        <div className="mt-3 flex items-start gap-2 rounded border border-yellow-500/20 bg-yellow-500/5 p-3">
          <AlertTriangle className="h-4 w-4 text-yellow-400 mt-0.5" />
          <div className="text-[10px] text-yellow-400/80">
            <p className="font-bold mb-1">Production Safety Posture</p>
            <p>
              • Canonical mode: {operatorStatus.mode}; live execution:{' '}
              {operatorStatus.liveExecutionEnabled}; live approval:{' '}
              {operatorStatus.liveTradingApproved}.
            </p>
            <p>• Release policy keeps live trading NO_GO pending a separately approved gate.</p>
            <p>• Do not use this page to prepare, test, or store real exchange credentials.</p>
          </div>
        </div>
      </div>

      {/* Data Sources */}
      <DataSourceStatus />

      {/* Agent Pipeline */}
      <AgentPipeline />

      {/* LLM Costs */}
      <CostsDashboard />

      {/* Service Control */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="mb-3 rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[10px] text-amber-300">
          Service inspection and process controls are unavailable from the dashboard.
        </p>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div
              className={`h-2 w-2 rounded-full ${
                serviceRunning === true
                  ? 'bg-green-400 animate-pulse'
                  : serviceRunning === false
                  ? 'bg-red-400'
                  : 'bg-zinc-500'
              }`}
            />
            <span className="text-xs font-bold text-zinc-200">Agent Service</span>
            <span
              className={`text-[10px] font-bold ${
                serviceRunning === true
                  ? 'text-green-400'
                  : serviceRunning === false
                  ? 'text-red-400'
                  : 'text-zinc-400'
              }`}
            >
              {serviceRunning === true ? 'RUNNING' : serviceRunning === false ? 'STOPPED' : 'UNKNOWN'}
            </span>
          </div>
          <div className="flex gap-1.5">
            {serviceRunning === true ? (
              <>
                <button
                  onClick={() => serviceAction('restart')}
                  disabled={!serviceControlsEnabled}
                  className="flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800/50 px-2 py-1 text-[10px] text-zinc-400 hover:bg-zinc-700/50"
                >
                  <RotateCcw className="h-3 w-3" />
                  Restart
                </button>
                <button
                  onClick={() => serviceAction('stop')}
                  disabled={!serviceControlsEnabled}
                  className="flex items-center gap-1 rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-[10px] text-red-400 hover:bg-red-500/20"
                >
                  <Square className="h-3 w-3" />
                  Stop
                </button>
              </>
            ) : serviceRunning === false ? (
              <button
                  onClick={() => serviceAction('start')}
                disabled={!serviceControlsEnabled}
                className="flex items-center gap-1 rounded border border-green-500/30 bg-green-500/10 px-2 py-1 text-[10px] text-green-400 hover:bg-green-500/20"
              >
                <Play className="h-3 w-3" />
                Start
              </button>
            ) : null}
            <button
              onClick={() => void fetchServiceStatus()}
              disabled
              className="flex items-center gap-1 rounded border border-zinc-700 px-2 py-1 text-[10px] text-zinc-500 hover:text-zinc-300"
            >
              <RefreshCw className={`h-3 w-3 ${serviceLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
        {serviceRunning === true && serviceStatus.data !== null && (
          <div className="grid grid-cols-2 gap-2 text-[10px]">
            <div>
              <span className="text-zinc-500">PID: </span>
              <code className="bg-zinc-800 px-1 rounded text-zinc-300">{serviceStatus.data.pid}</code>
            </div>
            <div>
              <span className="text-zinc-500">Since: </span>
              <span className="text-zinc-300">{serviceStatus.data.started}</span>
            </div>
          </div>
        )}
        <div className="mt-2 text-[10px] text-zinc-500 space-y-0.5">
          <p>• Control: use the authenticated Control/Job API</p>
          <p>• Runtime data: configured external data root</p>
          <p>• Credentials: protected server-side configuration</p>
        </div>
      </div>
    </div>
  );
}

/* ── inline panels ── */

const STAGE_COLORS: Record<string, string> = {
  analyst:   'border-blue-800 bg-blue-900/20 text-blue-300',
  debate:    'border-amber-800 bg-amber-900/20 text-amber-300',
  risk:      'border-red-800 bg-red-900/20 text-red-300',
  execution: 'border-emerald-800 bg-emerald-900/20 text-emerald-300',
};

function AgentPipeline() {
  const [state, setState] = useState<Readonly<AgentsState>>(INITIAL_AGENTS_STATE);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void loadAgentsState(fetch, { signal: controller.signal }).then((next) => {
      if (!controller.signal.aborted) setState(next);
    });
    return () => controller.abort();
  }, []);

  const summary = summarizeAgentsState(state);
  const stages = ['analyst', 'debate', 'risk', 'execution'] as const;
  const grouped = stages.map((stage) => summary.agents?.filter(
    (agent) => agent.stage === stage,
  ) ?? []);
  const countLabel = summary.count === null
    ? summary.availability === 'LOADING' ? 'Loading…' : 'Status unavailable'
    : `${summary.count} agents`;

  return (
    <div className="border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-amber-500" />
          <span className="text-xs font-bold text-zinc-200">Agent Pipeline</span>
        </div>
        <span className="text-[10px] text-zinc-500">{open ? '▲' : '▼'} {countLabel}</span>
      </button>

      {open && summary.availability === 'LOADING' && (
        <div className="p-4 text-center text-xs text-zinc-500 font-mono">
          Loading canonical agent metadata…
        </div>
      )}
      {open && summary.availability === 'UNAVAILABLE' && (
        <div className="p-4 text-center text-xs text-red-300 font-mono">
          Agent metadata unavailable — count and pipeline composition are unknown.
        </div>
      )}
      {open && summary.availability === 'AVAILABLE' && summary.agents?.length === 0 && (
        <div className="p-4 text-center text-xs text-zinc-500 font-mono">
          Canonical source returned an authoritative empty agent list.
        </div>
      )}
      {open && summary.availability === 'AVAILABLE' && summary.agents !== null
        && summary.agents.length > 0 && (
        <div className="p-4 space-y-3">
          {stages.map((stage, si) => (
            <div key={stage}>
              <div className="flex items-center gap-2 mb-1.5">
                <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 border ${STAGE_COLORS[stage]}`}>
                  {stage}
                </span>
                {si < stages.length - 1 && (
                  <span className="text-[9px] text-zinc-600">→</span>
                )}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {grouped[si].map(agent => (
                  <div key={agent.id} className={`border p-2.5 text-[10px] font-mono ${STAGE_COLORS[agent.stage]}`}>
                    <div className="font-bold text-xs mb-1">{agent.name}</div>
                    <div className="text-zinc-500 mb-1 text-[9px]">{agent.role}</div>
                    <div className="flex items-center gap-1 text-[9px]">
                      <span className="text-zinc-600">MODEL:</span>
                      <span className="text-amber-400">{agent.model}</span>
                    </div>
                    <div className="mt-1 text-[9px] text-zinc-600 truncate">→ {agent.produces}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CostsDashboard() {
  const [state, setState] = useState<Readonly<CostsState>>(INITIAL_COSTS_STATE);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void loadCostsState(fetch, { signal: controller.signal }).then((next) => {
      if (!controller.signal.aborted) setState(next);
    });
    return () => controller.abort();
  }, []);

  const data = state.data;
  const costLabel = state.availability === 'LOADING'
    ? 'Loading…'
    : state.availability === 'UNAVAILABLE' || data === null
      ? 'Unavailable'
      : data.summary.estimatedCost === null
        ? 'Cost unknown'
        : `$${data.summary.estimatedCost.toFixed(4)} est.`;

  return (
    <div className="border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors">
        <div className="flex items-center gap-2">
          <DollarSign className="h-4 w-4 text-emerald-500" />
          <span className="text-xs font-bold text-zinc-200">LLM Cost Tracker</span>
        </div>
        <span className="text-[10px] font-mono text-emerald-400">
          {costLabel}
        </span>
      </button>

      {open && state.availability === 'LOADING' && (
        <div className="p-4 text-center text-xs text-zinc-500 font-mono">
          Loading canonical cost data…
        </div>
      )}
      {open && (state.availability === 'UNAVAILABLE'
        || (state.availability === 'AVAILABLE' && data === null)) && (
        <div className="p-4 text-center text-xs text-red-300 font-mono">
          Cost data unavailable — totals and sessions are unknown.
        </div>
      )}
      {open && state.availability === 'AVAILABLE' && data !== null && (
        <div className="p-4 space-y-3">
          <div className="grid grid-cols-4 gap-px bg-zinc-800 border border-zinc-800">
            {[
              { label: 'Sessions', val: String(data.summary.totalSessions) },
              { label: 'LLM Calls', val: data.summary.totalLLMCalls === null ? '—' : String(data.summary.totalLLMCalls) },
              { label: 'Est. Cost', val: data.summary.estimatedCost === null ? '—' : `$${data.summary.estimatedCost.toFixed(4)}` },
              { label: 'Tokens Saved', val: data.summary.optimizerTokensSaved !== null && data.summary.optimizerTokensSaved > 0 ? `${(data.summary.optimizerTokensSaved / 1000).toFixed(0)}k` : '—' },
            ].map(({ label, val }) => (
              <div key={label} className="bg-zinc-900/70 px-3 py-2 text-center">
                <p className="text-[9px] text-zinc-500 uppercase">{label}</p>
                <p className="text-sm font-bold font-mono text-zinc-200">{val}</p>
              </div>
            ))}
          </div>

          {data.sessions.length > 0 && (
            <div className="border border-zinc-800 overflow-hidden">
              <div className="px-3 py-1.5 bg-zinc-900 border-b border-zinc-800">
                <span className="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Recent Sessions</span>
              </div>
              <div className="divide-y divide-zinc-800/50 max-h-60 overflow-y-auto">
                {data.sessions.map(s => (
                  <div key={s.session} className="px-3 py-2 flex items-center gap-3 text-[10px] font-mono">
                    <span className="text-zinc-600 truncate max-w-[140px]">{s.session.slice(-16)}</span>
                    <span className="text-zinc-400">{s.symbols.join('·')}</span>
                    <span className="ml-auto text-zinc-500">{s.llmCalls} calls</span>
                    <span className="text-emerald-400 w-16 text-right">{Number.isFinite(s.estimatedCost) ? `$${s.estimatedCost.toFixed(4)}` : '—'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {data.sessions.length === 0 && (
            <div className="border border-zinc-800 px-3 py-2 text-center text-[10px] text-zinc-500">
              Canonical source reports an authoritative empty session list.
            </div>
          )}

          <div className="text-[10px] text-zinc-600 font-mono flex gap-4">
            <span>avg {data.efficiency.avgLLMCallsPerSession ?? '—'} calls/session</span>
            <span>avg {data.efficiency.avgCostPerSession === null ? '—' : `$${data.efficiency.avgCostPerSession.toFixed(4)}`}/session</span>
          </div>
        </div>
      )}
    </div>
  );
}
