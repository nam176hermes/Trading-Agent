'use client';

import { useState, useEffect } from 'react';
import { AssetData } from '@/lib/trading/types';
import { filterStub, formatCurrency, formatPercentage, getSignalColor, getSignalBgColor } from '@/lib/trading/utils';
import { ConfidenceGauge } from './confidence-gauge';
import { RiskBadge } from './risk-badge';
import { MemoryContext } from './memory-context';
import { subscribePrices, extractPrice } from '@/lib/trading/price-stream';

interface SignalCardProps {
  asset: AssetData;
}

export function SignalCard({ asset }: SignalCardProps) {
  const signalColor = getSignalColor(asset.suggestion);
  const signalBg = getSignalBgColor(asset.suggestion);

  const [livePrice, setLivePrice] = useState<number | null>(null);
  const currentPrice = livePrice ?? asset.current_price;

  useEffect(() => {
    return subscribePrices((payload) => {
      const value = extractPrice(payload, asset.symbol);
      if (value !== null) setLivePrice(value);
    });
  }, [asset.symbol]);

  const isLive = livePrice != null;
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3 md:p-4 transition-colors hover:border-zinc-700">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h3 className="text-base md:text-lg font-bold text-zinc-100">{asset.symbol}</h3>
          <div className="flex items-baseline gap-2">
            <p className={`text-xl md:text-2xl font-mono font-semibold ${isLive ? 'text-emerald-400' : 'text-zinc-100'}`}>
              {formatCurrency(currentPrice)}
            </p>
            {isLive && (
              <span className="text-[10px] text-zinc-500">live</span>
            )}
          </div>
          <p
            className={`text-sm font-medium ${
              (asset.price_change_24h_pct ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'
            }`}
          >
            {formatPercentage(asset.price_change_24h_pct ?? 0)} (24h)
          </p>
        </div>

        <div className="flex flex-col items-end gap-2">
          <div className={`rounded-md border px-2 md:px-3 py-1 ${signalBg}`}>
            <span className={`text-xs md:text-sm font-bold ${signalColor}`}>
              {asset.suggestion}
            </span>
          </div>
          <ConfidenceGauge confidence={asset.confidence} />
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-1.5 md:gap-2 text-xs">
        <div className="rounded bg-zinc-800/50 p-1.5 md:p-2">
          <span className="text-zinc-400 text-[10px]">RSI (14)</span>
          <p className="font-mono font-medium text-zinc-100">{Number.isFinite(asset.rsi_14) ? asset.rsi_14.toFixed(1) : 'N/A'}</p>
          <p className={`text-[10px] ${asset.rsi_signal === 'overbought' ? 'text-red-400' : asset.rsi_signal === 'oversold' ? 'text-green-400' : 'text-zinc-400'}`}>
            {filterStub(asset.rsi_signal)}
          </p>
        </div>

        <div className="rounded bg-zinc-800/50 p-1.5 md:p-2">
          <span className="text-zinc-400 text-[10px]">MACD</span>
          <p className="font-mono text-xs font-medium text-zinc-100">
            {filterStub(asset.macd_signal)}
          </p>
        </div>

        <div className="rounded bg-zinc-800/50 p-1.5 md:p-2">
          <span className="text-zinc-400 text-[10px]">vs SMA200</span>
          <p className={`font-mono text-xs font-medium ${
            asset.price_vs_sma200 === 'above' ? 'text-green-400' : 'text-red-400'
          }`}>
            {filterStub(asset.price_vs_sma200)}
          </p>
        </div>

        <div className="rounded bg-zinc-800/50 p-1.5 md:p-2">
          <span className="text-zinc-400 text-[10px]">Volume Trend</span>
          <p className="font-mono text-xs font-medium text-zinc-100">
            {filterStub(asset.volume_trend)}
          </p>
        </div>
      </div>

      <div className="mb-3 space-y-2">
        {/* Sentiment + On-chain indicators */}
        {(asset.sentiment_score != null || asset.onchain_risk != null) && (
          <div className="flex flex-wrap gap-1.5">
            {asset.sentiment_score != null && (
              <span className={`inline-flex items-center gap-0.5 rounded px-2 py-0.5 text-[10px] font-bold ${
                asset.sentiment_score >= 0
                  ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                  : 'bg-red-500/10 text-red-400 border border-red-500/20'
              }`}>
                {asset.sentiment_score >= 0 ? '😊' : '😟'}
                {asset.sentiment_score >= 0 ? '+' : ''}{Number.isFinite(asset.sentiment_score) ? asset.sentiment_score.toFixed(2) : '—'}
              </span>
            )}
            {asset.onchain_risk && asset.asset_class === 'crypto' && (
              <span className={`inline-flex items-center gap-0.5 rounded px-2 py-0.5 text-[10px] font-bold border ${
                asset.onchain_risk === 'low'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : asset.onchain_risk === 'medium'
                  ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'
                  : asset.onchain_risk === 'high'
                  ? 'bg-orange-500/10 text-orange-400 border-orange-500/20'
                  : 'bg-red-500/10 text-red-400 border-red-500/20'
              }`}>
                ⛓ {asset.onchain_risk}
              </span>
            )}
          </div>
        )}

        {/* Risk badge + ATR header */}
        <div className="flex items-center justify-between">
          <RiskBadge risk={asset.risk_assessment?.risk_level ?? 'unknown'} />
          {(asset.atr_pct ?? 0) > 0 && (
            <span className="text-[10px] text-zinc-500">
              ATR: {Number.isFinite(Number(asset.atr_pct)) ? Number(asset.atr_pct).toFixed(2) : '—'}% · Stop: {asset.stop_method || 'atr'}
            </span>
          )}
        </div>

        {/* Stop Loss & Target Preview — show if ATR data exists */}
        {(asset.atr_pct ?? 0) > 0 && (asset.stop_loss_suggestion != null || asset.target_suggestion != null) && (
          <div className="rounded bg-zinc-800/30 p-2.5 space-y-2">
            {/* Price Line with Stop/Target markers */}
            <div className="space-y-1">
              {/* Visual band: Target → Current → Stop */}
              <div className="flex items-center gap-1.5 text-[10px]">
                <span className="text-green-400 font-mono w-12 md:w-16 text-right">
                  {asset.target_suggestion != null
                    ? '$' + (asset.target_suggestion ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})
                    : '—'}
                </span>
                <span className="text-green-400 text-[10px] hidden sm:inline">TARGET</span>
                <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-red-500/60 via-zinc-600 to-green-500/60"
                       style={{width: '100%'}} />
                </div>
                <span className="text-red-400 text-[10px] hidden sm:inline">STOP</span>
                <span className="text-red-400 font-mono w-12 md:w-16 text-left">
                  {asset.stop_loss_suggestion != null
                    ? '$' + (asset.stop_loss_suggestion ?? 0).toLocaleString(undefined, {maximumFractionDigits: 0})
                    : '—'}
                </span>
              </div>

              {/* Current price marker */}
              <div className="flex items-center justify-center text-[10px] text-zinc-500">
                <span>Current: {formatCurrency(currentPrice)}</span>
              </div>
            </div>

            {/* Risk/Reward Summary */}
            <div className="grid grid-cols-3 gap-1 md:gap-2 text-center border-t border-zinc-700 pt-2">
              <div>
                <p className="text-[10px] text-zinc-500">Stop Distance</p>
                <p className="text-xs font-mono font-bold text-red-400">
                  {asset.stop_loss_suggestion != null && currentPrice > 0
                    ? Number.isFinite(Math.abs(currentPrice - asset.stop_loss_suggestion) / currentPrice) ? ((Math.abs(currentPrice - asset.stop_loss_suggestion) / currentPrice) * 100).toFixed(1) + '%' : '—'
                    : '—'}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-zinc-500">Target Distance</p>
                <p className="text-xs font-mono font-bold text-green-400">
                  {asset.target_suggestion != null && currentPrice > 0
                    ? Number.isFinite(Math.abs(asset.target_suggestion - currentPrice) / currentPrice) ? ((Math.abs(asset.target_suggestion - currentPrice) / currentPrice) * 100).toFixed(1) + '%' : '—'
                    : '—'}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-zinc-500">R:R Ratio</p>
                <p className="text-xs font-mono font-bold text-zinc-200">
                  {asset.stop_loss_suggestion != null && asset.target_suggestion != null && currentPrice > 0
                    ? (() => {
                        const risk = Math.abs(currentPrice - asset.stop_loss_suggestion);
                        const reward = Math.abs(asset.target_suggestion - currentPrice);
                        return risk > 0 && Number.isFinite(reward / risk) ? '1:' + (reward / risk).toFixed(1) : '—';
                      })()
                    : '—'}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* ATR only — no stop/target available */}
        {(asset.atr_pct ?? 0) > 0 && asset.stop_loss_suggestion == null && asset.target_suggestion == null && (
          <div className="rounded bg-zinc-800/30 p-2 text-center">
            <p className="text-[10px] text-zinc-500">ATR: {Number.isFinite(Number(asset.atr_pct)) ? Number(asset.atr_pct).toFixed(2) : '—'}% — No stop/target computed</p>
          </div>
        )}
      </div>

      {asset.reasoning && (
        <div className="rounded bg-zinc-800/30 p-2">
          <p className="text-xs text-zinc-300">{filterStub(asset.reasoning)}</p>
        </div>
      )}

      {asset.alerts?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {asset.alerts.map((alert, i) => (
            <span
              key={i}
              className="rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-500"
            >
              {alert}
            </span>
          ))}
        </div>
      )}

      <MemoryContext ticker={asset.symbol} />
    </div>
  );
}
