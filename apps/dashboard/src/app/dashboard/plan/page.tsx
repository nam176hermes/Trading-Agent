import { MarketTicker } from '@/components/trading/market-ticker';
import { PlanBuilder } from '@/components/trading/plan-builder';
import { TradingSubNav } from '@/components/trading/trading-sub-nav';

export default function PlanPage() {
  return (
    <div>
      <MarketTicker />
      <TradingSubNav currentPath="/dashboard/plan" />
      <div className="p-3 md:p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Research Plan Builder</h1>
          <p className="text-sm text-zinc-400">Canonical planning status</p>
        </div>
        <div className="mx-auto max-w-3xl rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
          <PlanBuilder />
        </div>
      </div>
    </div>
  );
}

export const dynamic = 'force-dynamic';
