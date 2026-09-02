export const dynamic = 'force-dynamic';
import { MarketTicker } from '@/components/trading/market-ticker';
import { TradingSubNav } from '@/components/trading/trading-sub-nav';
import { RiskAssetList } from '@/components/trading/risk-asset-list';
import { PipelineTracker } from '@/components/trading/pipeline-tracker';
import { ValuationGrid } from '@/components/trading/valuation-grid';
import { EarningsTimeline } from '@/components/trading/earnings-timeline';
import { CollapsibleSection } from '@/components/trading/collapsible-section';
import { MacroDashboard } from '@/components/trading/macro-dashboard';
import { PositionSizingCalculator } from '@/components/trading/position-sizing-calculator';
import { CorrelationMatrix } from '@/components/trading/correlation-matrix';
import { getLatestReport } from '@/lib/trading/data';
import { getRiskColor } from '@/lib/trading/utils';

const PAGE_SIZE = 10;
export default async function RiskDashboard() {
  let report: Awaited<ReturnType<typeof getLatestReport>> = null;
  let sourceUnavailable = false;
  try {
    report = await getLatestReport();
  } catch {
    sourceUnavailable = true;
  }
  const allAssets = report?.assets ?? [];

  const riskLevels = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;
  const initialAssets = allAssets.slice(0, PAGE_SIZE);

  return (
    <div>
      <MarketTicker />
      <TradingSubNav currentPath="/dashboard/risk" />
      <div className="p-3 md:p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Risk Dashboard</h1>
          <p className="text-sm text-zinc-400">3-way risk debate · Aggressive / Conservative / Neutral · Position sizing</p>
        </div>

        {sourceUnavailable ? (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-center">
            <p className="text-xs font-mono text-amber-300">Canonical risk source is unavailable.</p>
          </div>
        ) : allAssets.length > 0 ? (
          <div className="space-y-8">
            <PipelineTracker completedStages={[]} />

            <RiskAssetList
              initialAssets={initialAssets}
              initialDecisionMap={{}}
              totalAssets={allAssets.length}
            />

            {/* Summary */}
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
              <h2 className="mb-3 text-lg font-bold text-zinc-100">Risk Summary</h2>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                {riskLevels.map(level => (
                  <div key={level} className="text-center">
                    <p className={`text-2xl font-bold ${getRiskColor(level)}`}>
                      {allAssets.filter(a => a.risk_assessment?.risk_level === level).length}
                    </p>
                    <p className="text-xs text-zinc-400">{level}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Valuation Context */}
            <div className="space-y-4">
              <h2 className="text-lg font-bold text-zinc-100">Valuation Context</h2>
              <p className="text-xs text-zinc-500">
                Valuation metrics provide context for risk assessment. High P/E stocks may have more downside risk during corrections.
              </p>
              <CollapsibleSection title="Valuation Comparison" defaultOpen={false}>
                <ValuationGrid />
              </CollapsibleSection>
              <CollapsibleSection title="Earnings Calendar" defaultOpen={false}>
                <EarningsTimeline />
              </CollapsibleSection>
            </div>

            {/* Position Sizing */}
            <div className="space-y-4">
              <h2 className="text-lg font-bold text-zinc-100">Position Sizing</h2>
              <p className="text-xs text-zinc-500">
                Kelly Criterion-based position sizing adjusted by risk level and portfolio concentration limits.
              </p>
              <PositionSizingCalculator />
            </div>

            {/* Macro Context */}
            <div className="space-y-4">
              <h2 className="text-lg font-bold text-zinc-100">Macro Regime Context</h2>
              <p className="text-xs text-zinc-500">
                The macro regime affects risk appetite across all assets. Risk-on favors aggressive positioning; risk-off calls for defensive sizing.
              </p>
              <CollapsibleSection title="Macro Overview" defaultOpen={true}>
                <MacroDashboard collapsed={false} />
              </CollapsibleSection>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-12 text-center">
            <h3 className="mb-2 text-lg font-bold text-zinc-100">No Risk Data Yet</h3>
            <p className="text-zinc-400">Run the trading agent with risk assessment to generate risk data.</p>
          </div>
        )}

        <div className="mt-8 space-y-4">
          <h2 className="text-lg font-bold text-zinc-100">Correlation &amp; Sector Risk</h2>
          <p className="text-xs text-zinc-500">
            Assets with high correlation (≥0.7) amplify drawdowns. Sector exposure limits prevent over-concentration.
          </p>
          <CorrelationMatrix />
        </div>
      </div>
    </div>
  );
}
