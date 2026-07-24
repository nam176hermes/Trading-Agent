'use client';

import { useEffect, useState } from 'react';
import type { MacroReport } from '@/lib/trading/types';

interface Props {
  collapsed?: boolean;
}

const INDICATOR_LABELS: Record<string, string> = {
  gdp_growth: 'GDP Growth',
  cpi_inflation: 'CPI Inflation',
  fed_funds_rate: 'Fed Funds Rate',
  unemployment: 'Unemployment',
  dxy: 'US Dollar Index',
  vix: 'VIX',
};

function isRiskOn(key: string, value: number): boolean {
  switch (key) {
    case 'gdp_growth': return value >= 2.0;
    case 'cpi_inflation': return value <= 3.0;
    case 'fed_funds_rate': return value <= 4.0;
    case 'unemployment': return value <= 4.5;
    case 'dxy': return value >= 95 && value <= 105;
    case 'vix': return value <= 20;
    default: return true;
  }
}

export function MacroDashboard({ collapsed = false }: Props) {
  const [report, setReport] = useState<MacroReport | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(collapsed);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/trading/macro')
      .then(r => r.json())
      .then(d => { if (!d.error) setReport(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 animate-pulse">
        <div className="h-4 w-32 bg-zinc-800 rounded mb-3" />
        <div className="grid grid-cols-3 gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-16 bg-zinc-800 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (!report || Object.keys(report.indicators).length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-xs text-zinc-500">No macro data available. Run macro.py to collect.</p>
      </div>
    );
  }

  const regimeColor = report.regime === 'risk_on' ? 'text-green-400' :
    report.regime === 'risk_off' ? 'text-red-400' : 'text-amber-400';
  const regimeBg = report.regime === 'risk_on' ? 'bg-green-500/10 border-green-500/20' :
    report.regime === 'risk_off' ? 'bg-red-500/10 border-red-500/20' : 'bg-amber-500/10 border-amber-500/20';
  const regimeLabel = report.regime.replace('_', '-').toUpperCase();

  const sortedKeys = Object.keys(report.indicators);

  return (
    <div className="space-y-3">
      {!collapsed && (
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className="w-full flex items-center justify-between px-0 py-0 text-left"
        >
          <h3 className="text-sm font-bold text-zinc-200">Macro Dashboard</h3>
          <span className={`text-zinc-400 text-xs transition-transform ${isCollapsed ? '' : 'rotate-90'}`}>▶</span>
        </button>
      )}

      {!isCollapsed && (
        <>
          {/* Regime Banner */}
          <div className={`rounded-lg border p-3 ${regimeBg}`}>
            <div className="flex items-center justify-between mb-1">
              <span className={`text-sm font-bold ${regimeColor}`}>{regimeLabel}</span>
              <span className="text-xs text-zinc-400">
                {Number.isFinite(Number(report.regime_confidence)) ? (Number(report.regime_confidence) * 100).toFixed(0) : '—'}% confidence
              </span>
            </div>
            <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden mb-1">
              <div
                className={`h-full rounded-full transition-all ${
                  report.regime === 'risk_on' ? 'bg-green-500' :
                  report.regime === 'risk_off' ? 'bg-red-500' : 'bg-amber-500'
                }`}
                style={{ width: `${report.regime_confidence * 100}%` }}
              />
            </div>
            <p className="text-[10px] text-zinc-400 leading-relaxed">{report.regime_rationale}</p>
          </div>

          {/* 3x2 Indicator Grid */}
          <div className="grid grid-cols-3 gap-2">
            {sortedKeys.map(key => {
              const ind = report.indicators[key];
              if (!ind) return null;
              const on = isRiskOn(key, ind.value);
              const trendIcon = ind.trend === 'up' ? '▲' : ind.trend === 'down' ? '▼' : ind.trend === 'flat' ? '—' : '●';
              const trendColor = ind.trend === 'up' ? 'text-green-400' :
                ind.trend === 'down' ? 'text-red-400' : 'text-zinc-500';

              return (
                <div
                  key={key}
                  className={`rounded border p-2.5 ${
                    on ? 'border-green-500/20 bg-green-500/5' : 'border-red-500/20 bg-red-500/5'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] text-zinc-400">{INDICATOR_LABELS[key] || key}</span>
                    <span className={`text-[10px] ${trendColor}`}>{trendIcon}</span>
                  </div>
                  <div className="flex items-baseline gap-1">
                    <span className={`text-lg font-bold font-mono ${
                      on ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {ind.value}
                    </span>
                    <span className="text-[10px] text-zinc-500">{ind.unit}</span>
                  </div>
                  <p className="text-[9px] text-zinc-600 mt-0.5">{ind.period}</p>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
