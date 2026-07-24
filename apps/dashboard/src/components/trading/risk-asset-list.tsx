'use client';

import { useState } from 'react';
import { RiskBadge } from './risk-badge';
import { RiskPersonaPanel } from './risk-persona-panel';
import { getRiskBgColor, getRiskColor } from '@/lib/trading/utils';
import { AssetData, TypedDecision } from '@/lib/trading/types';

const RISK_LEVELS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;

interface Props {
  initialAssets: AssetData[];
  initialDecisionMap: Record<string, TypedDecision>;
  totalAssets: number;
}

export function RiskAssetList({ initialAssets, initialDecisionMap, totalAssets }: Props) {
  const [assets, setAssets] = useState<AssetData[]>(initialAssets);
  const [decisionMap, setDecisionMap] = useState<Record<string, TypedDecision>>(initialDecisionMap);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasMore = assets.length < totalAssets;

  const loadMore = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/trading/risk-assets');
      if (res.ok) {
        const data = await res.json();
        setAssets(prev => [...prev, ...(data.assets || [])]);
        // Re-fetch typed decisions to get decision map for new assets
        const tres = await fetch('/api/trading/decisions/typed');
        if (tres.ok) {
          const tdata = await tres.json();
          const map: Record<string, TypedDecision> = {};
          for (const d of (tdata.decisions || tdata || [])) {
            if (!map[d.asset]) map[d.asset] = d;
          }
          setDecisionMap(map);
        }
      } else {
        setError('Failed to load more assets');
      }
    } catch {
      setError('Failed to load more assets');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Risk Heatmap */}
      <div>
        <h2 className="mb-4 text-lg font-bold text-zinc-100">Risk Heatmap</h2>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="border border-zinc-800 bg-zinc-900 px-4 py-2 text-left text-sm font-medium text-zinc-400">Asset</th>
                {RISK_LEVELS.map(l => (
                  <th key={l} className="border border-zinc-800 bg-zinc-900 px-4 py-2 text-center text-sm font-medium text-zinc-400">{l}</th>
                ))}
                <th className="border border-zinc-800 bg-zinc-900 px-4 py-2 text-center text-sm font-medium text-zinc-400">Personas</th>
              </tr>
            </thead>
            <tbody>
              {assets.map(asset => {
                const d = decisionMap[asset.symbol];
                const approves = d?.risk_assessments?.filter((r: {accept_signal: boolean}) => r.accept_signal).length ?? 0;
                const total = d?.risk_assessments?.length ?? 0;
                return (
                  <tr key={asset.symbol}>
                    <td className="border border-zinc-800 bg-zinc-900/50 px-4 py-3 font-medium text-zinc-100">{asset.symbol}</td>
                    {RISK_LEVELS.map(level => {
                      const active = asset.risk_assessment?.risk_level === level;
                      return (
                        <td key={level} className={`border border-zinc-800 px-4 py-3 text-center ${active ? getRiskBgColor(level) : 'bg-zinc-900/30'}`}>
                          {active ? <span className={`text-sm font-bold ${getRiskColor(level)}`}>●</span> : <span className="text-zinc-700">○</span>}
                        </td>
                      );
                    })}
                    <td className="border border-zinc-800 bg-zinc-900/30 px-4 py-3 text-center">
                      {total > 0
                        ? <span className={`text-xs font-medium ${approves > 0 ? 'text-green-400' : 'text-red-400'}`}>{approves}/{total}</span>
                        : <span className="text-zinc-700">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Per-Asset Risk Breakdown */}
      <div>
        <h2 className="mb-4 text-lg font-bold text-zinc-100">Per-Asset Risk Breakdown</h2>
        <div className="space-y-8">
          {assets.map(asset => {
            const decision = decisionMap[asset.symbol] ?? null;
            return (
              <div key={asset.symbol} className="rounded-lg border border-zinc-800 bg-zinc-900/30 p-5">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <h3 className="text-lg font-bold text-zinc-100">{asset.symbol}</h3>
                    <RiskBadge risk={asset.risk_assessment?.risk_level ?? 'unknown'} />
                  </div>
                  <div className="flex flex-wrap gap-4 text-xs text-zinc-400">
                    <span>Position: <span className="font-mono font-medium text-zinc-200">{asset.risk_assessment?.position_size_pct ? asset.risk_assessment.position_size_pct + '%' : 'N/A'}</span></span>
                    <span>Stop: <span className="font-mono font-medium text-zinc-200">{asset.risk_assessment?.stop_loss_pct ? asset.risk_assessment.stop_loss_pct + '%' : 'N/A'}</span></span>
                    {asset.atr_pct > 0 && <span>ATR: <span className="font-mono font-medium text-zinc-200">{asset.atr_pct.toFixed(2)}%</span></span>}
                  </div>
                </div>
                <div className="mb-4 rounded bg-zinc-800/30 p-3">
                  <p className="text-xs leading-relaxed text-zinc-300">{asset.risk_assessment?.rationale ?? 'No risk rationale available'}</p>
                </div>
                {asset.alerts.length > 0 && (
                  <div className="mb-4 flex flex-wrap gap-1">
                    {asset.alerts.map((a, i) => <span key={i} className="rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-500">{a}</span>)}
                  </div>
                )}
                <div>
                  <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-zinc-500">3-Way Risk Debate</p>
                  <RiskPersonaPanel decision={decision} />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Load More */}
      {hasMore && (
        <div className="text-center">
          {error && <p className="text-xs text-red-400 mb-2">{error}</p>}
          <button
            onClick={loadMore}
            disabled={loading}
            className="rounded-lg border border-zinc-700 bg-zinc-800/50 px-6 py-2 text-sm text-zinc-300 hover:bg-zinc-700/50 transition-colors disabled:opacity-50"
          >
            {loading ? 'Loading...' : `Show All ${totalAssets} Assets`}
          </button>
        </div>
      )}
    </div>
  );
}
