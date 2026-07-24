export const dynamic = 'force-dynamic';
import type { Metadata } from 'next';
import Link from 'next/link';
import type React from 'react';
import {
  BarChart3, Shield, TrendingUp, History, Activity,
  Zap, Play,
} from 'lucide-react';
import { MarketTicker } from '@/components/trading/market-ticker';
import { TradingSubNav } from '@/components/trading/trading-sub-nav';
import { SystemStatusBanner } from '@/components/trading/system-status-banner';
import { PipelineTracker } from '@/components/trading/pipeline-tracker';
import { PipelineStatus } from '@/components/trading/pipeline-status';
import { QuickActions } from '@/components/trading/quick-actions';
import { WatchlistEditor } from '@/components/trading/watchlist-editor';
import { PerformanceOverview } from '@/components/trading/performance-overview';
import { ModeToggle } from '@/components/trading/mode-toggle';

import { getLatestReport, getDecisions, getDataStats } from '@/lib/trading/data';
import { GroupCard } from '@/components/trading/group-card';
import { DashboardExtras } from '@/components/trading/dashboard-extras';
import {
  dashboardReportAssets,
  summarizeAssetRisk,
} from '@/lib/trading/dashboard-report-state';

export const metadata: Metadata = { title: 'Trading Hub — Hermes' };

export default async function TradingHubPage() {
  const [reportResult, decisionsResult, statsResult] = await Promise.allSettled([
    getLatestReport(), getDecisions(3), getDataStats(),
  ]);

  const report = reportResult.status === 'fulfilled' ? reportResult.value : null;
  const reportAvailable = report !== null;
  const decisionsAvailable = decisionsResult.status === 'fulfilled';
  const statsAvailable = statsResult.status === 'fulfilled';
  const readDataAvailable = reportAvailable && decisionsAvailable && statsAvailable;
  const decisions = decisionsAvailable ? decisionsResult.value : null;
  const stats = statsAvailable ? statsResult.value : null;
  const allAssets = dashboardReportAssets(report);
  const topAssets = allAssets === null ? null : allAssets.slice(0, 3);
  const latestDecision = decisions?.[0] ?? null;

  // Aggregate metrics
  const buyCount = decisions === null ? null : decisions.filter(
    d => d.suggestion === 'BUY' || d.suggestion === 'STRONG BUY',
  ).length;
  const sellCount = decisions === null ? null : decisions.filter(
    d => d.suggestion === 'SELL' || d.suggestion === 'STRONG SELL',
  ).length;
  const avgConf = decisions !== null && decisions.length > 0
    ? decisions.reduce((sum, decision) => sum + decision.confidence, 0) / decisions.length
    : null;

  const riskSummary = summarizeAssetRisk(allAssets);

  const pipelineStageCount: string[] = [];

  return (
    <div className="h-full overflow-y-auto">
      <MarketTicker />
      <TradingSubNav currentPath="/dashboard" />
      <SystemStatusBanner />

      <div className="p-3 md:p-4 space-y-4">
        {/* ── Header ── */}
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div>
            <h1 className="text-sm font-bold text-zinc-100 uppercase tracking-widest">TRADING HUB</h1>
            <p className="text-[10px] text-zinc-600 uppercase tracking-wider mt-0.5">
              RESEARCH PIPELINE · CANONICAL OPERATOR STATE · RISK CONTROLS
            </p>
            {!readDataAvailable && (
              <p className="text-[9px] font-mono text-red-300 mt-1">
                Canonical dashboard reads unavailable; affected counts and metrics are withheld.
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <ModeToggle />
          </div>
        </div>

        {/* ── Stats Strip ── */}
        <div className="border border-zinc-800 bg-zinc-900">
          <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-zinc-800">
            {[
              { label: 'Reports', value: stats?.totalReports, icon: Activity },
              { label: 'Decisions', value: stats?.totalDecisions, icon: Zap },
              { label: 'Sessions', value: stats?.totalScratchpadSessions, icon: BarChart3 },
              { label: 'Last Update', value: stats?.latestReportTimestamp
                ? new Date(stats.latestReportTimestamp).toLocaleDateString()
                : '—', icon: History, mono: true },
            ].map(s => (
              <div key={s.label} className="px-4 py-3 flex items-center gap-3">
                <s.icon className="h-3.5 w-3.5 text-zinc-600 shrink-0" />
                <div>
                  <p className="text-[9px] text-zinc-600 uppercase tracking-widest">{s.label}</p>
                  <p className={`font-bold text-zinc-100 leading-tight ${s.mono ? 'text-sm font-mono' : 'text-lg'}`}>
                    {s.value ?? '—'}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Pipeline Status ── */}
        <PipelineTracker completedStages={pipelineStageCount} />
        <PipelineStatus />

        {/* ── Performance Overview ── */}
        <PerformanceOverview />

        {/* ════════════════════════════════════════════
            MAIN GROUP CARDS — 2×2 Grid
           ════════════════════════════════════════════ */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">

          {/* ── Group A: Signals & Analysis ── */}
          <GroupCard
            icon={TrendingUp}
            iconColor="text-emerald-400"
            iconBg="bg-emerald-500/10"
            title="Signals & Analysis"
            subtitle={allAssets === null ? 'Asset data unavailable' : `${allAssets.length} assets tracked`}
            href="/dashboard/signals"
            metrics={[
              { label: 'BUY Signals', value: buyCount ?? '—', color: 'text-green-400' },
              { label: 'SELL Signals', value: sellCount ?? '—', color: 'text-red-400' },
              { label: 'Avg Confidence', value: avgConf === null ? '—' : `${(avgConf * 100).toFixed(0)}%` },
              { label: 'Latest Action',
                value: latestDecision?.suggestion ?? '—',
                color: latestDecision?.suggestion.includes('BUY') ? 'text-green-400'
                     : latestDecision?.suggestion.includes('SELL') ? 'text-red-400'
                     : 'text-zinc-400' },
            ]}
          >
            {/* Top asset rows */}
            {topAssets !== null && topAssets.length > 0 && (
              <div className="divide-y divide-zinc-800 border border-zinc-800">
                {topAssets.map(a => (
                  <Link key={a.symbol} href="/dashboard/signals"
                    className="flex items-center justify-between px-3 py-2 hover:bg-zinc-800/40 transition-colors">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] font-bold text-zinc-200 uppercase">{a.symbol}</span>
                      <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest ${
                        a.suggestion === 'BUY' ? 'bg-emerald-500/15 text-emerald-400'
                        : a.suggestion === 'SELL' ? 'bg-red-500/15 text-red-400'
                        : 'bg-zinc-700/50 text-zinc-400'}`}>{a.suggestion}</span>
                    </div>
                    <span className="text-[10px] font-mono text-zinc-500">
                      ${a.current_price.toLocaleString()}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </GroupCard>

          {/* ── Group B: Execution & Positions ── */}
          <GroupCard
            icon={Zap}
            iconColor="text-blue-400"
            iconBg="bg-blue-500/10"
            title="Execution & Positions"
            subtitle="Use canonical operator and execution state"
            href="/dashboard/execution"
            metrics={[
              { label: 'Mode', value: 'See operator state', color: 'text-zinc-400' },
              { label: 'Realized P&L', value: '—', color: 'text-zinc-400' },
              { label: 'Unrealized P&L', value: '—', color: 'text-zinc-400' },
              { label: 'Positions', value: '—', color: 'text-zinc-400' },
            ]}
          />

          {/* ── Group C: Risk Management ── */}
          <GroupCard
            icon={Shield}
            iconColor="text-amber-400"
            iconBg="bg-amber-500/10"
            title="Risk Management"
            subtitle={riskSummary.availability === 'AVAILABLE'
              ? 'Complete per-asset risk evidence'
              : 'Risk evidence incomplete — metrics withheld'}
            href="/dashboard/risk"
            metrics={[
              { label: 'High Risk Assets', value: riskSummary.highRisk ?? '—', color: riskSummary.highRisk === null ? 'text-zinc-400' : riskSummary.highRisk > 0 ? 'text-red-400' : 'text-green-400' },
              { label: 'Risk Assets Tracked', value: riskSummary.tracked ?? '—' },
              { label: 'Daily Loss Limit', value: '—', color: 'text-zinc-400' },
              { label: 'Circuit Breaker', value: 'UNKNOWN', color: 'text-zinc-400' },
            ]}
          />

          {/* ── Group D: Decision History ── */}
          <GroupCard
            icon={History}
            iconColor="text-violet-400"
            iconBg="bg-violet-500/10"
            title="Decision History"
            subtitle={decisions === null ? 'Decision data unavailable' : `${decisions.length} recent decisions`}
            href="/dashboard/history"
            metrics={[
              { label: 'Canonical', value: decisions?.length ?? '—' },
              { label: 'Timeline Entries', value: decisions?.length ?? '—' },
              { label: 'Latest', value: latestDecision?.ticker ?? '—', color: 'text-violet-400' },
              { label: 'Avg Confidence', value: avgConf === null ? '—' : `${(avgConf * 100).toFixed(0)}%` },
            ]}
          >
            {/* Latest decision preview */}
            {latestDecision && (
              <div className="border border-zinc-800 bg-zinc-950/60 px-3 py-2">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="font-mono text-[10px] font-bold text-zinc-200 uppercase">{latestDecision.ticker}</span>
                  <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest ${
                    latestDecision.suggestion.includes('BUY') ? 'bg-emerald-500/15 text-emerald-400'
                    : latestDecision.suggestion.includes('SELL') ? 'bg-red-500/15 text-red-400'
                    : 'bg-zinc-700/50 text-zinc-400'}`}>{latestDecision.suggestion}</span>
                </div>
                <p className="text-[9px] text-zinc-600 line-clamp-2 leading-relaxed">
                  {latestDecision.report_snippet || 'No summary available'}
                </p>
              </div>
            )}
          </GroupCard>

        </div>

        {/* ── Quick Actions ── */}
        <div className="border border-zinc-800 bg-zinc-900">
          <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-2">
            <Play className="h-3 w-3 text-amber-400" />
            <h2 className="text-[10px] font-bold text-zinc-300 uppercase tracking-widest">Quick Actions</h2>
          </div>
          <div className="p-3">
            <QuickActions />
            <div className="mt-4">
              <WatchlistEditor />
            </div>
          </div>
        </div>

        {/* ── Collapsible Extras ── */}
        <DashboardExtras />

      </div>
    </div>
  );
}
