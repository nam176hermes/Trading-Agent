'use client';

import { useCallback, useState, useEffect } from 'react';
import { SentimentTrendTracker } from './sentiment-trend-tracker';

interface ApiSentimentData {
  symbol: string;
  sources: Array<{
    source: string;
    sentiment: 'bullish' | 'bearish' | 'neutral';
    confidence: number;
    sample_count: number;
    top_signals: string[];
    has_divergence: boolean;
  }>;
  overall_sentiment: 'bullish' | 'bearish' | 'neutral';
  overall_score: number;
  trend_direction: 'improving' | 'declining' | 'stable';
  trend_strength: number;
  inflection_point: {
    timestamp: string;
    previous_direction: string;
    new_direction: string;
    strength: number;
  } | null;
  momentum: {
    short_term: number;
    medium_term: number;
    long_term: number;
  };
  history: Array<{
    timestamp: string;
    score: number;
    source: string;
  }>;
  catalysts: string[];
  risks: string[];
}

interface SentimentTrendClientProps {
  symbol?: string;
  initialData?: ApiSentimentData | null;
}

export function SentimentTrendClient({ symbol = 'BTC', initialData }: SentimentTrendClientProps) {
  const hasServerData = initialData !== undefined;
  const [data, setData] = useState<ApiSentimentData | null>(initialData ?? null);
  const [loading, setLoading] = useState(!hasServerData);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState(() => Date.now());

  const fetchSentiment = useCallback(async () => {
    if (hasServerData) return;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    try {
      setError(null);
      const res = await fetch(`/api/trading/sentiment?symbol=${symbol}`, { signal: controller.signal });
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setFetchedAt(Date.now());
      } else {
        setError('Failed to load sentiment data');
      }
    } catch {
      setError('Failed to load sentiment data');
    } finally {
      clearTimeout(timeoutId);
      setLoading(false);
    }
  }, [hasServerData, symbol]);

  useEffect(() => {
    const initialFetch = setTimeout(fetchSentiment, 0);
    const interval = setInterval(fetchSentiment, 120000);
    return () => {
      clearTimeout(initialFetch);
      clearInterval(interval);
    };
  }, [fetchSentiment]);

  if (error) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-3">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={fetchSentiment} className="text-xs text-blue-400 hover:text-blue-300 underline">Retry</button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <div className="h-4 w-4 animate-pulse rounded bg-zinc-800" />
          <span className="text-sm">Loading sentiment trend...</span>
        </div>
      </div>
    );
  }

  if (!data || !data.symbol) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 text-zinc-500">
          <span className="text-sm">Sentiment trend offline</span>
        </div>
      </div>
    );
  }

  const trendDirection: 'improving' | 'declining' | 'stable' =
    data.trend_direction ??
    (data.overall_sentiment === 'bullish' ? 'improving' :
     data.overall_sentiment === 'bearish' ? 'declining' : 'stable');

  const trendStrength: number =
    Number.isFinite(Number(data.trend_strength)) ? Number(data.trend_strength) : Number(data.overall_score ?? 0);

  const momentum = data.momentum
    ? {
        shortTerm: data.momentum.short_term,
        mediumTerm: data.momentum.medium_term,
        longTerm: data.momentum.long_term,
      }
    : {
        shortTerm: data.overall_score,
        mediumTerm: data.overall_score * 0.8,
        longTerm: 0.5,
      };

  const history = data.history?.length
    ? data.history
    : data.sources.map((s, i) => ({
        timestamp: new Date(fetchedAt - (data.sources.length - i) * 300000).toISOString(),
        score: s.confidence * (s.sentiment === 'bullish' ? 1 : s.sentiment === 'bearish' ? 0 : 0.5),
        source: s.source,
      }));

  return (
    <SentimentTrendTracker
      symbol={data.symbol}
      currentScore={data.overall_score}
      trendDirection={trendDirection}
      trendStrength={trendStrength}
      inflectionPoint={data.inflection_point ? {
        timestamp: data.inflection_point.timestamp,
        previousDirection: data.inflection_point.previous_direction,
        newDirection: data.inflection_point.new_direction,
      } : null}
      history={history}
      momentum={momentum}
    />
  );
}
