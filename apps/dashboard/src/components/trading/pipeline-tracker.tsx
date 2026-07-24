import { CheckCircle2, Circle, ArrowRight } from 'lucide-react';

const STAGES = [
  { id: 'technical',    label: 'Technical',    sub: 'RSI · MACD · SMA',      color: 'text-blue-400',   border: 'border-blue-500/30',   bg: 'bg-blue-500/5'   },
  { id: 'sentiment',   label: 'Sentiment',    sub: 'News · Social',           color: 'text-purple-400', border: 'border-purple-500/30', bg: 'bg-purple-500/5' },
  { id: 'onchain',     label: 'On-chain',     sub: 'Funding · OI',            color: 'text-amber-400',  border: 'border-amber-500/30',  bg: 'bg-amber-500/5'  },
  { id: 'fundamentals',label: 'Fundamentals', sub: 'Regime · Data',           color: 'text-green-400',  border: 'border-green-500/30',  bg: 'bg-green-500/5'  },
  { id: 'debate',      label: 'Bull/Bear',    sub: 'Debate rounds',           color: 'text-orange-400', border: 'border-orange-500/30', bg: 'bg-orange-500/5' },
  { id: 'risk',        label: 'Risk Debate',  sub: 'Aggr · Cons · Neut',     color: 'text-red-400',    border: 'border-red-500/30',    bg: 'bg-red-500/5'    },
  { id: 'decision',    label: 'Decision',     sub: 'Portfolio Manager',       color: 'text-zinc-200',   border: 'border-zinc-500/30',   bg: 'bg-zinc-500/5'   },
] as const;

interface PipelineTrackerProps {
  completedStages?: string[];
  activeStage?: string;
  showHeader?: boolean;
}

export function PipelineTracker({ completedStages, activeStage, showHeader = true }: PipelineTrackerProps) {
  const effective = completedStages ?? STAGES.map(s => s.id);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      {showHeader && (
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs font-bold text-zinc-200">Agent Pipeline</span>
          <span className="text-[10px] text-zinc-600">
            Inspired by TradingAgents (TauricResearch · 73K★)
          </span>
        </div>
      )}
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {STAGES.map((s, i) => {
          const done = effective.includes(s.id);
          const active = activeStage === s.id;
          return (
            <div key={s.id} className="flex shrink-0 items-center gap-1">
              <div className={`flex flex-col items-center gap-1 rounded-lg border px-3 py-2 transition-all ${
                done ? `${s.bg} ${s.border} ${s.color}` :
                active ? `${s.bg} ${s.border} ${s.color} ring-1 ring-current` :
                'border-zinc-800 text-zinc-600'
              }`}>
                <div className="flex items-center gap-1.5">
                  {done
                    ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                    : <Circle className="h-3.5 w-3.5 shrink-0" />}
                  <span className="whitespace-nowrap text-[11px] font-bold">{s.label}</span>
                </div>
                <span className="whitespace-nowrap text-[9px] opacity-60">{s.sub}</span>
              </div>
              {i < STAGES.length - 1 && <ArrowRight className="h-3 w-3 shrink-0 text-zinc-700" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}
