'use client';

import { TypedDecision } from '@/lib/trading/types';
import { Swords, TrendingUp, TrendingDown, Scale, Crosshair, ChevronDown, ChevronUp } from 'lucide-react';
import { useState } from 'react';

interface Props { decision: TypedDecision | null; }

export function DebateView({ decision }: Props) {
  const [expanded, setExpanded] = useState(false);

  const stripStub = (text: string | null | undefined): string => {
    if (!text) return '';
    return text.replace(/\[LLM STUB\]|\[NO API KEY\]/gi, '').trim();
  };

  if (!decision) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 text-center">
        <Scale className="mx-auto mb-2 h-5 w-5 text-zinc-600" />
        <p className="text-xs text-zinc-500">No structured decision available</p>
      </div>
    );
  }

  const finalBg =
    decision.final_action === 'BUY'   ? 'bg-green-500/20 text-green-400 border-green-500/30' :
    decision.final_action === 'SELL'  ? 'bg-red-500/20 text-red-400 border-red-500/30'       :
    decision.final_action === 'WATCH' ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' :
                                        'bg-zinc-500/20 text-zinc-400 border-zinc-500/30';

  const confidencePct = Math.round((decision.initial_signal.confidence ?? 0) * 100);
  const confidenceColor = confidencePct < 50 ? 'bg-red-500' : confidencePct <= 70 ? 'bg-yellow-500' : 'bg-green-500';

  const bullText = stripStub(decision.bull_synthesis);
  const bearText = stripStub(decision.bear_synthesis);
  const summaryText = stripStub(decision.executive_summary);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-3 border-b border-zinc-800 hover:bg-zinc-800/60 transition-colors text-left"
      >
        <div className="flex items-center gap-2 min-w-0">
          <Swords className="h-4 w-4 text-zinc-400 shrink-0" />
          <span className="text-xs font-bold text-zinc-200">Bull vs Bear Debate</span>
          <span className="font-mono text-[10px] text-zinc-500 hidden sm:inline">{decision.asset}</span>
          <span className={`rounded border px-2 py-0.5 text-xs font-bold shrink-0 ${finalBg}`}>
            {decision.final_action}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {(decision.final_position_size_pct ?? 0) > 0 && (
            <span className="text-xs text-zinc-500 hidden sm:inline">{decision.final_position_size_pct}% pos</span>
          )}
          {expanded
            ? <ChevronUp className="h-3.5 w-3.5 text-zinc-500" />
            : <ChevronDown className="h-3.5 w-3.5 text-zinc-500" />}
        </div>
      </button>

      {expanded && (
        <>
          {/* Section 1: Initial Signal */}
          <div className="border-b border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-1.5 mb-2">
              <Crosshair className="h-3.5 w-3.5 text-zinc-500" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Initial Signal</span>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-1.5">
              <span className="text-xs text-zinc-400">
                Action: <span className="font-medium text-zinc-200">{decision.initial_signal.action}</span>
              </span>
              <span className="text-xs text-zinc-400">
                Confidence: <span className="font-mono font-medium text-zinc-200">{confidencePct}%</span>
              </span>
              {decision.initial_signal.entry_price != null && (
                <span className="text-xs text-zinc-400">
                  Entry: <span className="font-mono font-medium text-zinc-200">${decision.initial_signal.entry_price.toLocaleString()}</span>
                </span>
              )}
              {decision.initial_signal.stop_loss != null && (
                <span className="text-xs text-zinc-400">
                  Stop: <span className="font-mono font-medium text-red-400">${decision.initial_signal.stop_loss.toLocaleString()}</span>
                </span>
              )}
            </div>
            {decision.initial_signal.reasoning && (
              <p className="text-xs text-zinc-300 leading-relaxed">
                {decision.initial_signal.reasoning.length > 150
                  ? decision.initial_signal.reasoning.slice(0, 150) + '...'
                  : decision.initial_signal.reasoning}
              </p>
            )}
          </div>

          {/* Section 2: Bull Thesis */}
          <div className="border-l-2 border-green-500/50 bg-green-500/5 px-4 py-3">
            <div className="flex items-center gap-1.5 mb-1.5">
              <TrendingUp className="h-3.5 w-3.5 text-green-500" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-green-500">Bull Thesis</span>
            </div>
            {bullText ? (
              <p className="text-xs text-zinc-300 leading-relaxed">
                {bullText.length > 200 ? bullText.slice(0, 200) + '...' : bullText}
              </p>
            ) : (
              <p className="text-xs text-zinc-600 italic">Awaiting next debate cycle (every 4h)</p>
            )}
          </div>

          {/* Section 3: Bear Thesis */}
          <div className="border-l-2 border-red-500/50 bg-red-500/5 px-4 py-3">
            <div className="flex items-center gap-1.5 mb-1.5">
              <TrendingDown className="h-3.5 w-3.5 text-red-500" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-red-500">Bear Thesis</span>
            </div>
            {bearText ? (
              <p className="text-xs text-zinc-300 leading-relaxed">
                {bearText.length > 200 ? bearText.slice(0, 200) + '...' : bearText}
              </p>
            ) : (
              <p className="text-xs text-zinc-600 italic">Awaiting next debate cycle (every 4h)</p>
            )}
          </div>

          {/* Section 4: Verdict */}
          <div className="border-t border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-1.5 mb-2">
              <Scale className="h-3.5 w-3.5 text-zinc-400" />
              <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Verdict</span>
            </div>
            <div className="flex items-center gap-2 mb-2">
              <span className={`rounded border px-2 py-0.5 text-xs font-bold ${finalBg}`}>
                {decision.final_action}
              </span>
              <span className="text-xs text-zinc-500">Portfolio Manager</span>
            </div>
            <p className="text-xs text-zinc-300 leading-relaxed mb-3">
              {summaryText || 'Full analysis available after next debate run'}
            </p>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-zinc-500">Confidence</span>
              <span className="font-mono text-[10px] text-zinc-400">{confidencePct}%</span>
              <div className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className={`h-1 rounded-full ${confidenceColor} transition-all`}
                  style={{ width: `${confidencePct}%` }}
                />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
