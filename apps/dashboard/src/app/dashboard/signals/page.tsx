import { MarketTicker } from '@/components/trading/market-ticker';
import { TradingSubNav } from '@/components/trading/trading-sub-nav';
import { SignalCard } from '@/components/trading/signal-card';
import { AnalystPanel } from '@/components/trading/analyst-panel';
import { DebateView } from '@/components/trading/debate-view';
import { SignalQualityBanner } from '@/components/trading/signal-quality-banner';
import { MacroBadge } from '@/components/trading/macro-badge';
import { FundamentalsCard } from '@/components/trading/fundamentals-card';
import { DerivativesSignalCard } from '@/components/trading/derivatives-signal-card';
import { SentimentTrendClient } from '@/components/trading/sentiment-trend-client';
import { getLatestReport } from '@/lib/trading/data';

export const dynamic = 'force-dynamic';

export default async function SignalsPage() {
  const report = await getLatestReport();
  const assets = report?.assets ?? [];

  return (
    <div>
      <MarketTicker />
      <TradingSubNav currentPath="/dashboard/signals" />

      <div className="p-3 md:p-6">
        <div className="mb-4">
          <h1 className="text-2xl font-bold text-zinc-100">Trading Signals</h1>
          <p className="text-sm text-zinc-400">
            Signal cards · 4-analyst breakdown · Bull vs Bear debate per asset
          </p>
        </div>

        {/* Signal quality banner — client-side fetch */}
        <div className="mb-6">
          <SignalQualityBanner />
        </div>

        {assets.length > 0 ? (
          <div className="space-y-10">
            {assets.map((asset) => {
              return (
                <div key={asset.symbol} className="space-y-4">
                  {/* Asset heading */}
                  <div className="flex items-center gap-3 border-b border-zinc-800 pb-3 flex-wrap">
                    <span className="font-mono text-xl font-bold text-zinc-100">{asset.symbol}</span>
                    <span className={`rounded px-2 py-0.5 text-sm font-bold ${
                      asset.suggestion === 'BUY'  ? 'bg-green-500/20 text-green-400' :
                      asset.suggestion === 'SELL' ? 'bg-red-500/20 text-red-400' :
                      'bg-zinc-500/20 text-zinc-400'
                    }`}>{asset.suggestion}</span>
                    <span className="text-sm text-zinc-500">
                      ${asset.current_price?.toLocaleString() ?? 'N/A'} &bull;{' '}
                      <span className={(asset.price_change_24h_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}>
                        {(asset.price_change_24h_pct ?? 0) >= 0 ? '+' : ''}
                        {Number.isFinite(Number(asset.price_change_24h_pct))
                          ? Number(asset.price_change_24h_pct).toFixed(2)
                          : 'N/A'}% (24h)
                      </span>
                    </span>

                    {/* Macro regime badge — rendered if data is present in report */}
                    {asset.macro_regime && (
                      <MacroBadge
                        regime={asset.macro_regime}
                        confidence={asset.macro_regime_confidence ?? undefined}
                      />
                    )}

                    {/* Derivatives signal badge */}
                    {asset.derivatives_signal && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400 border border-zinc-700 font-mono">
                        {asset.derivatives_signal}
                      </span>
                    )}
                  </div>

                  {/* Row 1: Signal card + quick stats */}
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                    <div>
                      <SignalCard asset={asset} />
                    </div>

                    <div className="lg:col-span-2 rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
                      <p className="mb-3 text-[11px] font-bold uppercase tracking-wider text-zinc-500">Quick Stats</p>
                      <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                        <div className="rounded bg-zinc-800/30 p-2">
                          <p className="text-zinc-500">RSI (14)</p>
                          <p className={`font-mono font-bold ${asset.rsi_signal === 'overbought' ? 'text-red-400' : asset.rsi_signal === 'oversold' ? 'text-green-400' : 'text-zinc-100'}`}>
                            {Number.isFinite(Number(asset.rsi_14)) ? Number(asset.rsi_14).toFixed(1) : 'N/A'}
                          </p>
                          <p className="text-zinc-500">{asset.rsi_signal ?? 'neutral'}</p>
                        </div>
                        <div className="rounded bg-zinc-800/30 p-2">
                          <p className="text-zinc-500">MACD</p>
                          <p className={`font-mono font-bold ${asset.macd_signal?.includes('bullish') ? 'text-green-400' : asset.macd_signal?.includes('bearish') ? 'text-red-400' : 'text-zinc-400'}`}>
                            {asset.macd_signal?.split('_')[0] ?? 'N/A'}
                          </p>
                        </div>
                        <div className="rounded bg-zinc-800/30 p-2">
                          <p className="text-zinc-500">vs SMA200</p>
                          <p className={`font-mono font-bold ${asset.price_vs_sma200 === 'above' ? 'text-green-400' : 'text-red-400'}`}>
                            {asset.price_vs_sma200 ?? 'N/A'}
                          </p>
                        </div>
                        <div className="rounded bg-zinc-800/30 p-2">
                          <p className="text-zinc-500">ATR (14)</p>
                          <p className="font-mono font-bold text-zinc-100">
                            {Number.isFinite(Number(asset.atr_pct)) ? Number(asset.atr_pct).toFixed(2) : 'N/A'}%
                          </p>
                        </div>
                      </div>
                      {asset.reasoning && (
                        <div className="mt-3 rounded bg-zinc-800/20 p-3">
                          <p className="text-xs leading-relaxed text-zinc-300">{asset.reasoning}</p>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Row 2: 4-Analyst Panel */}
                  <div>
                    <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-zinc-500">Analyst Breakdown</p>
                    <AnalystPanel asset={asset} />
                  </div>

                  {/* Row 3: Bull / Bear Debate */}
                  <DebateView decision={null} />

                  {/* Row 4: Derivatives · Sentiment · Fundamentals */}
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                    <DerivativesSignalCard asset={asset} />
                    <SentimentTrendClient symbol={asset.symbol} />
                    <FundamentalsCard symbol={asset.symbol} />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-12 text-center">
            <h3 className="mb-2 text-lg font-bold text-zinc-100">No Signals Yet</h3>
            <p className="text-zinc-400">Run the trading agent to generate signals.</p>
          </div>
        )}
      </div>
    </div>
  );
}
