import { Decision } from '@/lib/trading/types';
import { formatDate, getSignalColor, formatCurrency } from '@/lib/trading/utils';

interface DecisionTimelineProps {
  decisions: Decision[];
}

export function DecisionTimeline({ decisions }: DecisionTimelineProps) {
  if (decisions.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-8 text-center">
        <p className="text-zinc-400">No decisions yet</p>
      </div>
    );
  }

  // Helper to filter STUB text
  const filterStub = (text: string | null | undefined): string => {
    if (!text) return 'N/A';
    if (text.includes('[LLM STUB]') || text.includes('[NO API KEY]')) {
      return 'Run pipeline for data';
    }
    return text;
  };

  return (
    <div className="space-y-4">
      {decisions.map((decision, idx) => (
        <div
          key={`${decision.ticker}-${decision.date}-${idx}`}
          className="relative border-l-2 border-zinc-800 pl-6 pb-4 last:pb-0"
        >
          <div className="absolute left-[-5px] top-0 h-2.5 w-2.5 rounded-full bg-zinc-700" />

          <div className="mb-1 flex items-center gap-2">
            <span className="text-sm font-bold text-zinc-100">{decision.ticker}</span>
            <span className={`text-xs font-bold ${getSignalColor(decision.suggestion)}`}>
              {decision.suggestion}
            </span>
            <span className="text-xs text-zinc-500">{formatDate(decision.stored_at)}</span>
          </div>

          <div className="mb-2 text-xs text-zinc-400">
            <div>Confidence: {Number.isFinite(Number(decision.confidence)) ? (Number(decision.confidence) * 100).toFixed(0) : '—'}%</div>
            {(decision.price_at_decision ?? 0) > 0 && (
              <div>Price: {formatCurrency(decision.price_at_decision ?? 0)}</div>
            )}
          </div>

          {decision.signals && (
            <div className="rounded bg-zinc-800/30 p-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span className="text-zinc-500">RSI: </span>
                  <span className="text-zinc-300">{decision.signals.rsi_14?.toFixed(1) ?? 'N/A'}</span>
                </div>
                <div>
                  <span className="text-zinc-500">vs SMA200: </span>
                  <span className={`${
                    decision.signals.price_vs_sma200 === 'above' ? 'text-green-400' : 'text-red-400'
                  }`}>
                    {filterStub(decision.signals.price_vs_sma200)}
                  </span>
                </div>
                <div>
                  <span className="text-zinc-500">Volume Ratio: </span>
                  <span className="text-zinc-300">{decision.signals?.volume_trend_ratio?.toFixed(2) ?? 'N/A'}x</span>
                </div>
                <div>
                  <span className="text-zinc-500">MACD Hist: </span>
                  <span className="text-zinc-300">{decision.signals?.macd_histogram?.toFixed(2) ?? 'N/A'}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
