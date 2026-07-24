import { TypedDecision, RiskAssessmentEntry } from '@/lib/trading/types';
import { filterStub } from '@/lib/trading/utils';
import { Zap, Shield, Scale, CheckCircle, XCircle } from 'lucide-react';

interface RiskPersonaPanelProps {
  decision: TypedDecision | null;
}

const PERSONA_CFG = {
  aggressive: {
    Icon: Zap, label: 'Aggressive', sub: 'High risk, momentum-driven',
    color: 'text-red-400', bg: 'bg-red-500/5', border: 'border-red-500/25',
  },
  conservative: {
    Icon: Shield, label: 'Conservative', sub: 'Capital preservation first',
    color: 'text-blue-400', bg: 'bg-blue-500/5', border: 'border-blue-500/25',
  },
  neutral: {
    Icon: Scale, label: 'Neutral', sub: 'Balanced risk / reward',
    color: 'text-amber-400', bg: 'bg-amber-500/5', border: 'border-amber-500/25',
  },
} as const;

type PersonaKey = keyof typeof PERSONA_CFG;

export function RiskPersonaPanel({ decision }: RiskPersonaPanelProps) {
  if (!decision?.risk_assessments?.length) {
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/30 p-4 text-center">
          <p className="text-xs text-zinc-500">No risk assessments yet</p>
          <p className="mt-1 text-[10px] text-zinc-600">
            Next debate run will generate persona risk analysis (every 4h)
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3 opacity-40">
          {(Object.keys(PERSONA_CFG) as PersonaKey[]).map((key) => {
            const cfg = PERSONA_CFG[key];
            const { Icon } = cfg;
            return (
              <div key={key} className={`rounded-lg border ${cfg.border} ${cfg.bg} p-4`}>
                <div className="mb-3 flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`flex h-8 w-8 items-center justify-center rounded-lg border ${cfg.border} ${cfg.bg}`}>
                      <Icon className={`h-4 w-4 ${cfg.color}`} />
                    </div>
                    <div>
                      <p className={`text-sm font-bold ${cfg.color}`}>{cfg.label}</p>
                      <p className="text-[10px] text-zinc-600">{cfg.sub}</p>
                    </div>
                  </div>
                </div>
                <div className="rounded-md bg-zinc-900/60 px-3 py-2 text-center">
                  <span className="text-[10px] text-zinc-500">Awaiting assessment</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  const ras = decision.risk_assessments;
  const accepts = ras.filter((r: RiskAssessmentEntry) => r.accept_signal).length;
  const avgPos = accepts > 0
    ? ras.filter((r: RiskAssessmentEntry) => r.accept_signal).reduce((s: number, r: RiskAssessmentEntry) => s + (r.position_size_pct ?? 0), 0) / accepts
    : 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            {ras.map((ra, i) => (
              <div key={i} className={`h-2 w-8 rounded-full ${ra.accept_signal ? 'bg-green-500' : 'bg-red-500'}`} />
            ))}
          </div>
          <span className="text-xs text-zinc-400">
            <span className={accepts > 0 ? 'text-green-400 font-medium' : 'text-red-400 font-medium'}>{accepts}</span>
            /{ras.length} personas approve
          </span>
        </div>
        {accepts > 0 && (
          <span className="text-xs text-zinc-400">
            Avg size: <span className="font-mono font-medium text-zinc-200">{Number.isFinite(avgPos) ? avgPos.toFixed(1) : '—'}%</span>
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {ras.map((ra: RiskAssessmentEntry, i: number) => {
          const key = (ra.persona?.toLowerCase() ?? 'neutral') as PersonaKey;
          const cfg = PERSONA_CFG[key] ?? PERSONA_CFG.neutral;
          const { Icon } = cfg;
          return (
            <div key={i} className={`rounded-lg border ${cfg.border} ${cfg.bg} p-4`}>
              <div className="mb-3 flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg border ${cfg.border} ${cfg.bg}`}>
                    <Icon className={`h-4 w-4 ${cfg.color}`} />
                  </div>
                  <div>
                    <p className={`text-sm font-bold ${cfg.color}`}>{cfg.label}</p>
                    <p className="text-[10px] text-zinc-600">{cfg.sub}</p>
                  </div>
                </div>
                {ra.accept_signal
                  ? <CheckCircle className="h-5 w-5 shrink-0 text-green-500" />
                  : <XCircle className="h-5 w-5 shrink-0 text-red-500" />}
              </div>
              <div className="mb-3 flex items-center justify-between rounded-md bg-zinc-900/60 px-3 py-2">
                <span className={`text-xs font-bold ${ra.accept_signal ? 'text-green-400' : 'text-red-400'}`}>
                  {ra.accept_signal ? 'ACCEPT' : 'REJECT'}
                </span>
                {(ra.position_size_pct ?? 0) > 0 && (
                  <span className="text-xs text-zinc-400">
                    <span className="font-mono font-medium text-zinc-200">{ra.position_size_pct ?? 0}%</span> size
                  </span>
                )}
              </div>
              <p className="text-xs leading-relaxed text-zinc-300">{filterStub(ra.rationale)}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
