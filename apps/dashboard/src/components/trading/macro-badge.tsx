import { Globe } from 'lucide-react';

interface Props {
  regime: string;
  confidence?: number;
}

export function MacroBadge({ regime, confidence }: Props) {
  const color =
    regime === 'risk_on'  ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800' :
    regime === 'risk_off' ? 'bg-red-900/30 text-red-400 border-red-900' :
                            'bg-zinc-800 text-zinc-400 border-zinc-700';
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border font-medium ${color}`}>
      <Globe size={10} />
      {regime.replace('_', '-')}
      {confidence != null && Number.isFinite(confidence) ? ` ${(confidence * 100).toFixed(0)}%` : ''}
    </span>
  );
}
