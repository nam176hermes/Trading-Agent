'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle, Clock3, Loader2, Play, XCircle } from 'lucide-react';

import {
  createPipelineCommand,
  type PipelineCommand,
} from '@/lib/trading/quick-actions-state';

type JobState = 'QUEUED' | 'CLAIMED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'BLOCKED'
  | 'TIMED_OUT' | 'CANCEL_REQUESTED' | 'CANCELLED';

interface Job {
  job_id: string;
  state: JobState;
  requested_at: string;
  attempt_count: number;
  reason_code: string | null;
  result_hash: string | null;
}

interface JobListEnvelope { data: { items: Job[] } }
interface JobDetailEnvelope { data: { job: Job; attempts: unknown[]; events: unknown[] } }
interface CreateEnvelope { data: { job: Job } }

const ACTIVE_STATES = new Set<JobState>(['QUEUED', 'CLAIMED', 'RUNNING', 'CANCEL_REQUESTED']);
const FAILURE_STATES = new Set<JobState>(['FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELLED']);
const MAX_POLL_ATTEMPTS = 120;
const POLL_DELAY_MS = 5_000;

function shortId(jobId: string): string {
  return jobId.length > 18 ? `${jobId.slice(0, 8)}…${jobId.slice(-6)}` : jobId;
}

export function RunPipelineButton() {
  const [job, setJob] = useState<Job | null>(null);
  const [posting, setPosting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const pollAttempts = useRef(0);
  const pendingCommand = useRef<PipelineCommand | null>(null);
  const jobId = job?.job_id;
  const jobState = job?.state;

  const loadDetail = useCallback(async (jobId: string, signal?: AbortSignal) => {
    const response = await fetch(`/api/trading/jobs/${encodeURIComponent(jobId)}`, {
      cache: 'no-store', signal,
    });
    if (!response.ok) throw new Error('JOB_DETAIL_UNAVAILABLE');
    const payload = await response.json() as JobDetailEnvelope;
    setJob(payload.data.job);
    return payload.data.job;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch('/api/trading/run', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) return;
        const payload = await response.json() as JobListEnvelope;
        const latest = payload.data.items[0];
        if (latest) await loadDetail(latest.job_id, controller.signal);
      } catch {}
    })();
    return () => controller.abort();
  }, [loadDetail]);

  useEffect(() => {
    if (!jobId || !jobState || !ACTIVE_STATES.has(jobState)) return;
    const controller = new AbortController();
    pollAttempts.current = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (controller.signal.aborted || pollAttempts.current >= MAX_POLL_ATTEMPTS) return;
      pollAttempts.current += 1;
      try {
        const updated = await loadDetail(jobId, controller.signal);
        if (!ACTIVE_STATES.has(updated.state)) return;
      } catch {
        if (controller.signal.aborted) return;
      }
      timer = setTimeout(poll, POLL_DELAY_MS);
    };
    timer = setTimeout(poll, POLL_DELAY_MS);
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId, jobState, loadDetail]);

  async function handleRun() {
    const command = pendingCommand.current ?? createPipelineCommand('snapshot');
    if (command === null) {
      setMessage('Research operation identity could not be created.');
      return;
    }
    pendingCommand.current = command;
    setPosting(true);
    setMessage(null);
    try {
      const response = await fetch('/api/trading/run', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(command),
      });
      const payload = await response.json() as CreateEnvelope & { message?: string };
      if (!response.ok) {
        setMessage(payload.message ?? 'Research job could not be queued.');
        return;
      }
      pendingCommand.current = null;
      setJob(payload.data.job);
    } catch {
      setMessage('Research job service is unavailable.');
    } finally {
      setPosting(false);
    }
  }

  const active = job ? ACTIVE_STATES.has(job.state) : false;
  const succeeded = job?.state === 'SUCCEEDED';
  const failed = job ? FAILURE_STATES.has(job.state) : false;

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-3">
        <button
          onClick={handleRun}
          disabled={active || posting}
          className="flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {active || posting ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> {posting ? 'Queueing…' : job?.state}</>
          ) : (
            <><Play className="h-4 w-4" /> Run Analysis</>
          )}
        </button>
        {job && (
          <span className="flex items-center gap-1.5 text-xs text-zinc-500" title={job.job_id}>
            {succeeded && <CheckCircle className="h-3.5 w-3.5 text-green-400" />}
            {failed && <XCircle className="h-3.5 w-3.5 text-red-400" />}
            {active && <Clock3 className="h-3.5 w-3.5 text-amber-400" />}
            <span className="font-mono">{shortId(job.job_id)}</span>
            <span className={succeeded ? 'text-green-400' : failed ? 'text-red-400' : 'text-amber-400'}>
              {job.state}
            </span>
          </span>
        )}
      </div>
      {message && <span className="max-w-80 text-right text-[10px] text-amber-400">{message}</span>}
    </div>
  );
}
