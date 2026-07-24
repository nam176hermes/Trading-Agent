'use client';

import { useState } from 'react';
import { TrendingUp, TrendingDown, Minus, Activity, ChevronDown, ChevronUp } from 'lucide-react';

interface SentimentDataPoint {
  timestamp: string;
  score: number;
  source: string;
}

interface SentimentTrendTrackerProps {
  symbol: string;
  currentScore: number;
  trendDirection: 'improving' | 'declining' | 'stable';
  trendStrength: number;
  inflectionPoint?: {
    timestamp: string;
    previousDirection: string;
    newDirection: string;
  } | null;
  history: SentimentDataPoint[];
  momentum: {
    shortTerm: number;
    mediumTerm: number;
    longTerm: number;
  };
}

export function SentimentTrendTracker({
  currentScore,
  trendDirection,
  trendStrength,
  inflectionPoint,
  history,
  momentum,
}: SentimentTrendTrackerProps) {
  const [expanded, setExpanded] = useState(true);

  const trendColor = trendDirection === 'improving' ? 'text-green-400' : trendDirection === 'declining' ? 'text-red-400' : 'text-zinc-400';
  const trendBg = trendDirection === 'improving' ? 'bg-green-500/20' : trendDirection === 'declining' ? 'bg-red-500/20' : 'bg-zinc-500/20';

  const trendIcon = trendDirection === 'improving' ? <TrendingUp className="h-3 w-3" /> : trendDirection === 'declining' ? <TrendingDown className="h-3 w-3" /> : <Minus className="h-3 w-3" />;

  // Generate mini sparkline visualization
  const sparklineData = history.slice(-12);
  const scores = sparklineData.map(d => d.score);
  const minScore = scores.length > 0 ? Math.min(...scores) : 0;
  const maxScore = scores.length > 0 ? Math.max(...scores) : 1;
  const range = maxScore - minScore || 1;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 hover:bg-zinc-800/60 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-purple-400" />
          <span className="text-xs font-bold text-zinc-200">Sentiment Trend</span>
          <span className="text-[10px] text-zinc-600 hidden sm:inline">Linear regression</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <div className={`flex items-center gap-1 rounded border px-2 py-0.5 ${trendBg} ${trendColor}`}>
            {trendIcon}
            <span className="text-xs font-bold">{trendDirection.toUpperCase()}</span>
            <span className="text-[10px] opacity-75">{Number.isFinite(Number(trendStrength)) ? (Number(trendStrength) * 100).toFixed(0) : '—'}%)</span>
          </div>
          {expanded ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
        </div>
      </button>

      {expanded && (
        <div className="p-4 space-y-4">
          {/* Current Score Display */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[10px] text-zinc-500">Current Sentiment Score</p>
              <p className={`text-2xl font-mono font-bold ${currentScore > 0 ? 'text-green-400' : currentScore < 0 ? 'text-red-400' : 'text-zinc-400'}`}>
                {currentScore > 0 ? '+' : ''}{Number.isFinite(Number(currentScore)) ? Number(currentScore).toFixed(3) : '—'}
              </p>
            </div>
            <div className="text-right">
              <p className="text-[10px] text-zinc-500">Trend Strength</p>
              <div className="flex items-center justify-end gap-1">
                <div className="h-1.5 w-20 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full ${trendDirection === 'improving' ? 'bg-green-500' : trendDirection === 'declining' ? 'bg-red-500' : 'bg-zinc-500'}`}
                    style={{ width: `${trendStrength * 100}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-zinc-400">{Number.isFinite(Number(trendStrength)) ? (Number(trendStrength) * 100).toFixed(0) : '—'}%</span>
              </div>
            </div>
          </div>

          {/* Inflection Point Alert */}
          {inflectionPoint && (
            <div className="rounded bg-amber-500/10 border border-amber-500/30 p-3">
              <div className="flex items-center gap-2 mb-1">
                <Activity className="h-3.5 w-3.5 text-amber-400" />
                <p className="text-[10px] font-bold text-amber-400">INFLECTION POINT DETECTED</p>
              </div>
              <p className="text-[10px] text-zinc-400">
                Sentiment shifted from <span className="text-zinc-300">{inflectionPoint.previousDirection}</span> to{' '}
                <span className="text-zinc-300">{inflectionPoint.newDirection}</span> at{' '}
                {new Date(inflectionPoint.timestamp).toLocaleTimeString()}
              </p>
            </div>
          )}

          {/* Momentum Breakdown */}
          <div>
            <p className="text-[10px] font-semibold text-zinc-500 mb-2">MOMENTUM ANALYSIS</p>
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded bg-zinc-800/50 p-2 text-center">
                <p className="text-[10px] text-zinc-500">Short-term</p>
                <p className={`text-sm font-mono font-bold ${momentum.shortTerm > 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {momentum.shortTerm > 0 ? '+' : ''}{Number.isFinite(Number(momentum.shortTerm)) ? Number(momentum.shortTerm).toFixed(3) : '—'}
                </p>
              </div>
              <div className="rounded bg-zinc-800/50 p-2 text-center">
                <p className="text-[10px] text-zinc-500">Medium-term</p>
                <p className={`text-sm font-mono font-bold ${momentum.mediumTerm > 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {momentum.mediumTerm > 0 ? '+' : ''}{Number.isFinite(Number(momentum.mediumTerm)) ? Number(momentum.mediumTerm).toFixed(3) : '—'}
                </p>
              </div>
              <div className="rounded bg-zinc-800/50 p-2 text-center">
                <p className="text-[10px] text-zinc-500">Long-term</p>
                <p className={`text-sm font-mono font-bold ${momentum.longTerm > 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {momentum.longTerm > 0 ? '+' : ''}{Number.isFinite(Number(momentum.longTerm)) ? Number(momentum.longTerm).toFixed(3) : '—'}
                </p>
              </div>
            </div>
          </div>

          {/* Sparkline Visualization */}
          <div>
            <p className="text-[10px] font-semibold text-zinc-500 mb-2">12-PERIOD HISTORY</p>
            <div className="flex items-end gap-0.5 h-16">
              {sparklineData.map((point, i) => {
                const height = ((point.score - minScore) / range) * 100;
                const isPositive = point.score > 0;
                return (
                  <div
                    key={i}
                    className={`flex-1 rounded-t transition-all ${isPositive ? 'bg-green-500/60' : 'bg-red-500/60'}`}
                    style={{ height: `${Math.max(10, height)}%` }}
                    title={`${new Date(point.timestamp).toLocaleTimeString()}: ${Number.isFinite(Number(point.score)) ? Number(point.score).toFixed(3) : '—'}`}
                  />
                );
              })}
            </div>
            <div className="flex justify-between text-[10px] text-zinc-600 mt-1">
              <span>{sparklineData.length > 0 ? new Date(sparklineData[0].timestamp).toLocaleDateString() : '—'}</span>
              <span>Now</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
