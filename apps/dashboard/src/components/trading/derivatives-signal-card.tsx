'use client';

import { AssetData } from '@/lib/trading/types';
import { Link2, TrendingUp, TrendingDown, AlertTriangle, Minus, HelpCircle } from 'lucide-react';

interface Props {
  asset: AssetData;
}

const SIGNAL_MAP: Record<string, { label: string; color: string; icon: string; warning: string }> = {
  squeeze_risk_caution: { label: 'Squeeze Risk', color: 'text-orange-400', icon: 'AlertTriangle', warning: 'Elevated short squeeze risk — reduce short exposure' },
  long_squeeze_risk: { label: 'Long Squeeze Risk', color: 'text-red-400', icon: 'AlertTriangle', warning: 'Long positions at risk of cascade liquidation' },
  strong_long_setup: { label: 'Strong Long Setup', color: 'text-green-400', icon: 'TrendingUp', warning: '' },
  strong_short_setup: { label: 'Strong Short Setup', color: 'text-red-400', icon: 'TrendingDown', warning: '' },
  short_squeeze_relief: { label: 'Short Squeeze Relief', color: 'text-green-400', icon: 'TrendingUp', warning: 'Short pressure easing — potential reversal' },
  long_capitulation: { label: 'Long Capitulation', color: 'text-red-400', icon: 'AlertTriangle', warning: 'Long positions capitulating — avoid catching knife' },
  bullish_divergence: { label: 'Bullish Divergence', color: 'text-green-400', icon: 'TrendingUp', warning: 'OI + price diverging bullishly' },
  bearish_divergence: { label: 'Bearish Divergence', color: 'text-red-400', icon: 'TrendingDown', warning: 'OI + price diverging bearishly' },
  neutral_derivatives: { label: 'Neutral', color: 'text-zinc-400', icon: 'Minus', warning: '' },
  insufficient_data: { label: 'Insufficient Data', color: 'text-zinc-500', icon: 'HelpCircle', warning: '' },
};

function SignalIcon({ icon }: { icon: string }) {
  const cls = 'h-3.5 w-3.5';
  switch (icon) {
    case 'TrendingUp': return <TrendingUp className={cls} />;
    case 'TrendingDown': return <TrendingDown className={cls} />;
    case 'AlertTriangle': return <AlertTriangle className={cls} />;
    case 'Minus': return <Minus className={cls} />;
    case 'HelpCircle': return <HelpCircle className={cls} />;
    default: return <Minus className={cls} />;
  }
}

function fundingColor(pct: number | null): string {
  if (pct == null) return 'text-zinc-500';
  if (pct >= -0.03 && pct <= 0.03) return 'text-green-400';
  if ((pct > 0.03 && pct <= 0.1) || (pct >= -0.05 && pct < -0.03)) return 'text-yellow-400';
  return 'text-red-400';
}

function formatBillions(value: number | null): string {
  if (value == null) return '—';
  const b = value / 1e9;
  return `$${b.toFixed(2)}B`;
}

export function DerivativesSignalCard({ asset }: Props) {
  const hasData = asset.funding_rate_pct != null || asset.derivatives_signal != null;
  const signal = asset.derivatives_signal ? SIGNAL_MAP[asset.derivatives_signal] ?? null : null;

  if (!hasData) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Link2 className="h-4 w-4 text-zinc-500" />
          <span className="text-sm font-semibold text-zinc-400">Derivatives Signals</span>
          <span className="text-xs text-zinc-600 font-mono">{asset.symbol}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-zinc-600">
          <HelpCircle className="h-3.5 w-3.5" />
          No derivatives data available
        </div>
      </div>
    );
  }

  const fundingWarnings: string[] = [];
  if (asset.funding_rate_pct != null) {
    if (asset.funding_rate_pct > 0.1) {
      fundingWarnings.push('Funding >0.1% — overleveraged longs');
    } else if (asset.funding_rate_pct < -0.05) {
      fundingWarnings.push('Funding <-0.05% — overleveraged shorts');
    }
  }
  if (
    asset.oi_change_pct != null && asset.oi_change_pct < 0 &&
    asset.price_change_24h_pct != null && asset.price_change_24h_pct > 0
  ) {
    fundingWarnings.push('OI declining while price rising — bearish divergence');
  }
  if (
    asset.oi_change_pct != null && asset.oi_change_pct > 0 &&
    asset.price_change_24h_pct != null && asset.price_change_24h_pct < 0
  ) {
    fundingWarnings.push('OI rising while price falling — bullish divergence');
  }
  if (signal?.warning) {
    fundingWarnings.push(signal.warning);
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Link2 className="h-4 w-4 text-zinc-200" />
        <span className="text-sm font-semibold text-zinc-200">Derivatives Signals</span>
        <span className="text-xs text-zinc-500 font-mono">{asset.symbol}</span>
      </div>

      {asset.funding_rate_pct != null && (
        <>
          <div className="flex items-center justify-between py-1.5 text-xs">
            <span className="text-zinc-500">Funding Rate (8h)</span>
            <span className={`font-mono text-zinc-200 ${fundingColor(asset.funding_rate_pct)}`}>
              {asset.funding_rate_pct >= 0 ? '+' : ''}{asset.funding_rate_pct.toFixed(4)}%
            </span>
          </div>
          <div className="flex items-center justify-between py-1.5 text-xs">
            <span className="text-zinc-500">Annualized</span>
            <span className="font-mono text-zinc-200">
              {asset.funding_rate_annualized != null
                ? `${asset.funding_rate_annualized >= 0 ? '+' : ''}${asset.funding_rate_annualized.toFixed(1)}%`
                : '—'}
            </span>
          </div>
        </>
      )}

      {asset.open_interest_usd != null && (
        <div className="flex items-center justify-between py-1.5 text-xs">
          <span className="text-zinc-500">Open Interest</span>
          <span className="font-mono text-zinc-200">{formatBillions(asset.open_interest_usd)}</span>
        </div>
      )}

      {asset.oi_change_pct != null && (
        <div className="flex items-center justify-between py-1.5 text-xs">
          <span className="text-zinc-500">OI Change (24h)</span>
          <span className={`font-mono ${asset.oi_change_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {asset.oi_change_pct >= 0 ? '+' : ''}{asset.oi_change_pct.toFixed(2)}%
          </span>
        </div>
      )}

      {signal && (
        <div className="flex items-center justify-between py-1.5 text-xs">
          <span className="text-zinc-500">Signal</span>
          <span className={`font-mono font-semibold ${signal.color} flex items-center gap-1`}>
            <SignalIcon icon={signal.icon} />
            {signal.label}
          </span>
        </div>
      )}

      {fundingWarnings.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {fundingWarnings.map((w, i) => (
            <div key={i} className="rounded bg-orange-500/10 border border-orange-500/20 p-2 text-[10px] text-orange-400">
              {w}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
