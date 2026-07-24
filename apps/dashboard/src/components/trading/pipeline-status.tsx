'use client';

import { useCallback, useEffect, useState } from 'react';
import { Activity, CheckCircle2, CircleSlash2, Clock3, XCircle } from 'lucide-react';

type JobState = 'QUEUED' | 'CLAIMED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'BLOCKED'
  | 'TIMED_OUT' | 'CANCEL_REQUESTED' | 'CANCELLED';

interface Job {
  job_id: string;
  job_type: 'SNAPSHOT' | 'DEBATE' | 'REPLAY' | 'BACKTEST';
  state: JobState;
  requested_at: string;
  attempt_count: number;
  reason_code: string | null;
  result_hash: string | null;
}

interface JobAttempt {
  attempt_id: string;
  attempt_number: number;
  finished_at: string | null;
  termination_reason: string | null;
}

interface JobEvent {
  event_id: string;
  sequence: number;
  to_state: JobState;
  reason_code: string;
  created_at: string;
}

interface JobDetail {
  job: Job;
  attempts: JobAttempt[];
  events: JobEvent[];
}

interface JobListEnvelope { data: { items: Job[] } }
interface JobDetailEnvelope { data: JobDetail }

const ACTIVE_STATES = new Set<JobState>(['QUEUED', 'CLAIMED', 'RUNNING', 'CANCEL_REQUESTED']);
const FAILURE_STATES = new Set<JobState>(['FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELLED']);
const MAX_POLL_ATTEMPTS = 120;
const POLL_DELAY_MS = 10_000;

function stateStyle(state: JobState): string {
  if (state === 'SUCCEEDED') return 'border-emerald-500/30 bg-emerald-950/30 text-emerald-400';
  if (FAILURE_STATES.has(state)) return 'border-red-500/30 bg-red-950/30 text-red-400';
  return 'border-amber-500/30 bg-amber-950/30 text-amber-400';
}

function StateIcon({ state }: { state: JobState }) {
  if (state === 'SUCCEEDED') return <CheckCircle2 className="h-4 w-4" />;
  if (FAILURE_STATES.has(state)) return <XCircle className="h-4 w-4" />;
  if (state === 'CANCEL_REQUESTED') return <CircleSlash2 className="h-4 w-4" />;
  return <Clock3 className="h-4 w-4" />;
}

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 10)}…` : '—';
}

export function PipelineStatus() {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchLatest = useCallback(async (signal?: AbortSignal) => {
    const listResponse = await fetch('/api/trading/pipeline-status', { cache: 'no-store', signal });
    if (!listResponse.ok) throw new Error('JOB_LIST_UNAVAILABLE');
    const list = await listResponse.json() as JobListEnvelope;
    const latest = list.data.items[0];
    if (!latest) {
      setDetail(null);
      return null;
    }
    const detailResponse = await fetch(`/api/trading/jobs/${encodeURIComponent(latest.job_id)}`, {
      cache: 'no-store', signal,
    });
    if (!detailResponse.ok) throw new Error('JOB_DETAIL_UNAVAILABLE');
    const payload = await detailResponse.json() as JobDetailEnvelope;
    setDetail(payload.data);
    return payload.data;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    const poll = async () => {
      if (controller.signal.aborted || attempts >= MAX_POLL_ATTEMPTS) return;
      attempts += 1;
      try {
        const latest = await fetchLatest(controller.signal);
        setError(false);
        setLoading(false);
        const delay = latest && ACTIVE_STATES.has(latest.job.state) ? POLL_DELAY_MS : 30_000;
        timer = setTimeout(poll, delay);
      } catch {
        if (controller.signal.aborted) return;
        setError(true);
        setLoading(false);
        timer = setTimeout(poll, 30_000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [fetchLatest]);

  if (loading) return null;
  if (error) {
    return (
      <div className="mb-6 rounded-lg border border-red-500/30 bg-red-950/30 p-4">
        <p className="text-xs text-red-400">Canonical job status unavailable. No legacy status fallback is used.</p>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-xs text-zinc-500">No durable research jobs have been requested.</p>
      </div>
    );
  }

  const { job, attempts, events } = detail;
  const latestAttempt = attempts.at(-1);
  const latestEvent = events.at(-1);
  return (
    <div className={`mb-6 rounded-lg border p-4 ${stateStyle(job.state)}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Activity className="h-4 w-4 text-zinc-300" />
        <span className="text-sm font-medium text-zinc-200">Latest research job</span>
        <span className="flex items-center gap-1 text-xs font-semibold"><StateIcon state={job.state} />{job.state}</span>
        <span className="font-mono text-[10px] text-zinc-400" title={job.job_id}>{job.job_id}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2 text-[10px] md:grid-cols-4">
        <div><span className="block text-zinc-500">Type</span><span className="text-zinc-200">{job.job_type}</span></div>
        <div><span className="block text-zinc-500">Requested</span><span className="text-zinc-200">{new Date(job.requested_at).toLocaleString()}</span></div>
        <div><span className="block text-zinc-500">Attempts</span><span className="text-zinc-200">{job.attempt_count}{latestAttempt ? ` · #${latestAttempt.attempt_number}` : ''}</span></div>
        <div><span className="block text-zinc-500">Result</span><span className="font-mono text-zinc-200" title={job.result_hash ?? undefined}>{shortHash(job.result_hash)}</span></div>
        <div className="col-span-2"><span className="block text-zinc-500">Latest event</span><span className="text-zinc-200">{latestEvent ? `${latestEvent.to_state} · ${latestEvent.reason_code}` : '—'}</span></div>
        <div className="col-span-2"><span className="block text-zinc-500">Reason</span><span className="text-zinc-200">{job.reason_code ?? latestAttempt?.termination_reason ?? '—'}</span></div>
      </div>
    </div>
  );
}
