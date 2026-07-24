'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle, XCircle } from 'lucide-react';
import Link from 'next/link';

interface GoNoGoCheck {
  threshold: string;
  actual: string;
  pass: boolean;
}

interface GoNoGoData {
  signal_accuracy: GoNoGoCheck;
  high_confidence: GoNoGoCheck;
  conflict_respect: GoNoGoCheck;
  overall: 'GO' | 'NO-GO';
  win_rate: number;
  total_records: number;
  directional_signals: number;
  generated_at: string;
}

function MetricItem({ label, check, needsLabel }: { label: string; check: GoNoGoCheck; needsLabel?: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-zinc-400">{label}:</span>
      <span className="text-xs font-mono text-zinc-200">{check.actual}</span>
      <span className="text-xs text-zinc-500">
        ({needsLabel ? 'needs' : ''} {check.threshold})
      </span>
      {check.pass ? (
        <CheckCircle className="h-3.5 w-3.5 text-green-400" />
      ) : (
        <XCircle className="h-3.5 w-3.5 text-red-400" />
      )}
    </div>
  );
}

export function GoNoGoBanner() {
  const [data, setData] = useState<GoNoGoData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch('/api/trading/go-nogo');
        if (res.ok) {
          const json = await res.json();
          setData(json);
          setError(false);
        } else {
          setError(true);
        }
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 300000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="border-l-4 border-zinc-700 bg-zinc-900/30 p-4">
        <p className="text-sm text-zinc-500 animate-pulse">Checking backtest...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="border-l-4 border-zinc-600 bg-zinc-900/30 p-4">
        <p className="text-sm text-zinc-400">
          Backtest data unavailable — run <code className="text-xs font-mono text-zinc-500">backtest_analyzer.py</code>
        </p>
      </div>
    );
  }

  const isGo = data.overall === 'GO';

  return (
    <div
      className={`border-l-4 p-4 ${
        isGo
          ? 'border-green-500 bg-green-500/5'
          : 'border-red-500 bg-red-500/5'
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-3">
          {isGo ? (
            <CheckCircle className="h-5 w-5 text-green-400 mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle className="h-5 w-5 text-red-400 mt-0.5 shrink-0" />
          )}
          <div>
            <p className={`text-sm font-semibold ${isGo ? 'text-green-400' : 'text-red-400'}`}>
              {isGo
                ? 'READY FOR LIVE — All backtest thresholds met'
                : 'PAPER TRADING ONLY — Backtest thresholds not met'}
            </p>
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
              <MetricItem label="Signal accuracy" check={data.signal_accuracy} needsLabel />
              <MetricItem label="High-confidence" check={data.high_confidence} needsLabel />
              <MetricItem label="Conflict respect" check={data.conflict_respect} />
            </div>
          </div>
        </div>
        <Link
          href="/dashboard/history"
          className={`shrink-0 text-xs hover:underline ${isGo ? 'text-green-400' : 'text-red-400'}`}
        >
          View full backtest &rarr;
        </Link>
      </div>
    </div>
  );
}
