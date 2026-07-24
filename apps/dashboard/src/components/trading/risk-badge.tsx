import { RiskLevel } from '@/lib/trading/types';
import { getRiskColor, getRiskBgColor } from '@/lib/trading/utils';

interface RiskBadgeProps {
  risk: RiskLevel;
}

export function RiskBadge({ risk }: RiskBadgeProps) {
  const color = getRiskColor(risk);
  const bgColor = getRiskBgColor(risk);

  return (
    <div className={`inline-flex items-center rounded-md border px-2 py-1 ${bgColor}`}>
      <span className={`text-xs font-bold ${color}`}>{risk}</span>
    </div>
  );
}
