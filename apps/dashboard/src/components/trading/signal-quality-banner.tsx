'use client';

import { useState, useEffect } from 'react';
import { BarChart2, Zap } from 'lucide-react';

interface QualityEntry {
  asset: string;
  overall: number;
}

interface SignalQualityData {
  total_scored: number;
  avg_overall: number;
  decisions: QualityEntry[];
}

export function SignalQualityBanner() {
  const [data, setData] = useState<SignalQualityData | null>(null);

  useEffect(() => {
    fetch('/api/trading/signal-quality')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {});
  }, []);

  if (!data || !data.decisions?.length) return null;

  // Scores are on a 0–5 scale; normalise to 0–100 for display
  const MAX_SCORE = 5;
  const avgPct = Math.round((data.avg_overall / MAX_SCORE) * 100);
  const color =
    avgPct >= 60 ? 'text-emerald-400 border-emerald-800 bg-emerald-900/20' :
    avgPct >= 35 ? 'text-amber-400 border-amber-800 bg-amber-900/20' :
                   'text-red-400 border-red-900 bg-red-900/20';

  return (
    <div className={`flex items-center gap-4 rounded-xl border px-4 py-3 ${color}`}>
      <div className="flex items-center gap-2">
        <BarChart2 size={15} />
        <span className="text-xs font-medium uppercase tracking-wide opacity-70">Signal Quality</span>
      </div>
      <div className="flex items-center gap-1">
        <span className="text-2xl font-bold tabular-nums">{avgPct}</span>
        <span className="text-sm opacity-60">/100</span>
      </div>
      <div className="text-xs opacity-60">
        avg {Number.isFinite(data.avg_overall) ? data.avg_overall.toFixed(2) : '—'}/5 · {data.total_scored} scored
      </div>
      {/* Top 3 per asset */}
      <div className="ml-auto hidden sm:flex items-center gap-2">
        {data.decisions.slice(0, 3).map(d => (
          <div key={d.asset} className="flex items-center gap-1 text-xs opacity-80">
            <Zap size={10} />
            <span className="font-mono font-bold">{d.asset}</span>
            <span className="opacity-60">{Math.round((d.overall / MAX_SCORE) * 100)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

