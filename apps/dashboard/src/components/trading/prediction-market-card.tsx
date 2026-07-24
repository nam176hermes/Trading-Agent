'use client';

import { useEffect, useState } from 'react';
import { TrendingUp, Coins } from 'lucide-react';

interface PredictionMarket {
  title: string;
  slug: string;
  volume?: number;
  volume_24h?: number;
  liquidity?: number;
  categories: string[];
  outcomes: Array<{
    title: string;
    probability: number | null;
    day_change: number | null;
  }>;
}

interface PredictionData {
  collected_at: string | null;
  total_markets: number;
  filtered_markets: number;
  markets: PredictionMarket[];
}

export default function PredictionMarketCard() {
  const [data, setData] = useState<PredictionData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/prediction')
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="animate-pulse bg-white/5 rounded-lg h-48" />;
  if (!data?.markets?.length) return null;

  return (
    <div className="bg-white/5 rounded-lg border border-white/10 p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={16} className="text-blue-400" />
        <h3 className="text-sm font-semibold text-gray-300">Prediction Markets</h3>
        <span className="text-xs text-gray-500 ml-auto">
          {data.filtered_markets} markets
        </span>
      </div>
      <div className="space-y-2">
        {data.markets.slice(0, 6).map((m, i) => {
          const topOutcome = m.outcomes?.[0];
          const pct = topOutcome?.probability != null
            ? (topOutcome.probability * 100).toFixed(1)
            : null;
          const change = topOutcome?.day_change != null
            ? (topOutcome.day_change * 100).toFixed(1)
            : null;
          const isUp = change != null && parseFloat(change) > 0;

          return (
            <div key={i} className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-1.5 min-w-0">
                <Coins size={12} className="text-gray-500 shrink-0" />
                <span className="text-gray-400 truncate">{m.title}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {pct != null && (
                  <span className="text-gray-200 font-mono">{pct}%</span>
                )}
                {change != null && (
                  <span className={isUp ? 'text-emerald-400' : 'text-red-400'}>
                    {isUp ? '+' : ''}{change}%
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
