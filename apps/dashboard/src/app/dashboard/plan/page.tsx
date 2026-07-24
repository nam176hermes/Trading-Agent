export const dynamic = 'force-dynamic';
import { MarketTicker } from '@/components/trading/market-ticker';
import { TradingSubNav } from '@/components/trading/trading-sub-nav';
import { PlanBuilder } from '@/components/trading/plan-builder';

export default function PlanPage() {
  return (
    <div>
      <MarketTicker />
      <TradingSubNav currentPath="/dashboard/plan" />

      <div className="p-3 md:p-6">
        <div className="mb-6">
        <h1 className="text-2xl font-bold text-zinc-100">Research Plan Builder</h1>
        <p className="text-sm text-zinc-400">
          Create structured research plans for crypto trading analysis
        </p>
      </div>

      <div className="mx-auto max-w-3xl">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
          <PlanBuilder />
        </div>

        <div className="mt-8 rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
          <h2 className="mb-3 text-lg font-bold text-zinc-100">How It Works</h2>
          <ol className="space-y-2 text-sm text-zinc-300">
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-violet-400 text-xs font-bold">
                1
              </span>
              <span>
                Describe what you want to research (e.g., &quot;Analyze BTC and ETH for entry points&quot;)
              </span>
            </li>
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-violet-400 text-xs font-bold">
                2
              </span>
              <span>
                Select focus areas (Technical, Fundamental, Sentiment, On-chain, Risk, Debate)
              </span>
            </li>
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-violet-400 text-xs font-bold">
                3
              </span>
              <span>Generate a structured research plan with step dependencies</span>
            </li>
            <li className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-violet-400 text-xs font-bold">
                4
              </span>
              <span>
                Submit the plan through the configured Control/Job API
              </span>
            </li>
          </ol>

          <div className="mt-6 rounded-md bg-zinc-800/50 p-4">
            <p className="mb-2 text-xs font-medium text-zinc-300">Plan execution</p>
            <p className="text-xs text-zinc-400">
              Use the authenticated Control/Job API configured for this dashboard.
            </p>
          </div>
        </div>

        <div className="mt-6 rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
          <h2 className="mb-3 text-lg font-bold text-zinc-100">Available Research Modes</h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded border border-zinc-800 bg-zinc-800/30 p-4">
              <h3 className="mb-2 text-sm font-bold text-zinc-100">Snapshot</h3>
              <p className="text-xs text-zinc-400">
                Quick market data collection and signal generation for all assets
              </p>
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-800/30 p-4">
              <h3 className="mb-2 text-sm font-bold text-zinc-100">Debate</h3>
              <p className="text-xs text-zinc-400">
                Adversarial bull vs bear analysis for balanced decision-making
              </p>
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-800/30 p-4">
              <h3 className="mb-2 text-sm font-bold text-zinc-100">Risk Assessment</h3>
              <p className="text-xs text-zinc-400">
                Multi-persona risk review with position sizing recommendations
              </p>
            </div>
            <div className="rounded border border-zinc-800 bg-zinc-800/30 p-4">
              <h3 className="mb-2 text-sm font-bold text-zinc-100">Custom Plan</h3>
              <p className="text-xs text-zinc-400">
                Build your own research workflow with custom steps
              </p>
            </div>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
