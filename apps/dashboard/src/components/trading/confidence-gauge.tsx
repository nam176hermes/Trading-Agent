import { ConfidenceLevel } from '@/lib/trading/types';
import { confidenceToPercent, getConfidenceColor } from '@/lib/trading/utils';

interface ConfidenceGaugeProps {
  confidence: ConfidenceLevel;
  size?: number;
}

export function ConfidenceGauge({ confidence, size = 40 }: ConfidenceGaugeProps) {
  const percent = confidenceToPercent(confidence);
  const color = getConfidenceColor(percent);
  const radius = size / 2;
  const circumference = 2 * Math.PI * (radius - 4);
  const strokeDasharray = circumference;
  const strokeDashoffset = circumference - (percent / 100) * circumference;

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="transform -rotate-90">
        <circle
          cx={radius}
          cy={radius}
          r={radius - 4}
          stroke="currentColor"
          strokeWidth="4"
          fill="none"
          className="text-zinc-700"
        />
        <circle
          cx={radius}
          cy={radius}
          r={radius - 4}
          stroke="currentColor"
          strokeWidth="4"
          fill="none"
          strokeDasharray={strokeDasharray}
          strokeDashoffset={strokeDashoffset}
          className={color}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className={`text-xs font-bold ${color}`}>{percent}%</span>
      </div>
    </div>
  );
}
