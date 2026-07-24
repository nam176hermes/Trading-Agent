'use client';

import { useEffect, useState } from 'react';
import { MessageCircle, X, Zap } from 'lucide-react';

interface SentimentData {
  collected_at: string | null;
  sections: string[];
  data: Record<string, unknown>;
}

interface SocialToken {
  symbol?: string;
  name?: string;
  sentiment_score?: number;
  buzz_score?: number;
}

function isSocialToken(value: unknown): value is SocialToken {
  return typeof value === 'object' && value !== null;
}

function SentimentBar({ label, score, buzz }: { label: string; score: number; buzz?: number }) {
  const isPositive = score > 0;
  const barColor = isPositive ? 'bg-emerald-500' : 'bg-red-500';
  const barWidth = `${Math.abs(score) * 100}%`;

  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-xs">
        <span className="text-gray-400">{label}</span>
        <span className={isPositive ? 'text-emerald-400' : 'text-red-400'}>
          {isPositive ? '+' : ''}{score.toFixed(2)}
          {buzz != null && <span className="text-gray-600 ml-1">buzz:{buzz}</span>}
        </span>
      </div>
      <div className="h-1 bg-white/10 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: barWidth, marginLeft: isPositive ? '50%' : `${50 - parseFloat(barWidth)}%` }}
        />
      </div>
    </div>
  );
}

export default function SocialSentimentCard() {
  const [data, setData] = useState<SentimentData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/social')
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="animate-pulse bg-white/5 rounded-lg h-48" />;
  if (!data?.sections?.length) {
    return (
      <div className="bg-white/5 rounded-lg border border-white/10 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Zap size={16} className="text-amber-400" />
          <h3 className="text-sm font-semibold text-gray-300">Social Sentiment</h3>
        </div>
        <p className="text-xs text-gray-600">
          API key not configured. Sign up at <a href="https://adanos.org" className="text-blue-400 hover:underline">adanos.org</a> (free tier)
        </p>
      </div>
    );
  }

  const reddit = data.data?.reddit_crypto;
  const twitter = data.data?.x_twitter;

  return (
    <div className="bg-white/5 rounded-lg border border-white/10 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Zap size={16} className="text-amber-400" />
        <h3 className="text-sm font-semibold text-gray-300">Social Sentiment</h3>
        <span className="text-xs text-gray-500 ml-auto">
          {data.sections.length} sources
        </span>
      </div>
      <div className="space-y-3">
        {Boolean(reddit) && (
          <div className="flex items-start gap-2">
            <MessageCircle size={14} className="text-orange-400 mt-0.5" />
            <div className="flex-1 space-y-1.5">
              <span className="text-xs text-gray-500">Reddit Crypto</span>
              {Array.isArray(reddit) ? reddit.filter(isSocialToken).slice(0, 3).map((token, i) => (
                <SentimentBar
                  key={i}
                  label={token?.symbol || token?.name || '???'}
                  score={token?.sentiment_score ?? 0}
                  buzz={token?.buzz_score}
                />
              )) : (
                <span className="text-xs text-gray-600">No token data</span>
              )}
            </div>
          </div>
        )}
        {Boolean(twitter) && (
          <div className="flex items-start gap-2">
            <X size={14} className="text-blue-400 mt-0.5" />
            <div className="flex-1 space-y-1.5">
              <span className="text-xs text-gray-500">X / Twitter</span>
              {Array.isArray(twitter) ? twitter.filter(isSocialToken).slice(0, 3).map((token, i) => (
                <SentimentBar
                  key={i}
                  label={token?.symbol || token?.name || '???'}
                  score={token?.sentiment_score ?? 0}
                  buzz={token?.buzz_score}
                />
              )) : (
                <span className="text-xs text-gray-600">No data</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
