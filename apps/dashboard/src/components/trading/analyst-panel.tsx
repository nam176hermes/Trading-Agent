import { AssetData } from '@/lib/trading/types';
import { Activity, MessageCircle, Link2, Newspaper } from 'lucide-react';

interface AnalystPanelProps { asset: AssetData; }

function Row({ label, value, cls = 'text-zinc-100' }: {
  label: string; value: string | number | null | undefined; cls?: string;
}) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="shrink-0 text-zinc-500">{label}</span>
      <span className={`text-right font-mono font-medium ${cls}`}>{value}</span>
    </div>
  );
}

function Quad({ title, Icon, color, children }: {
  title: string; Icon: React.ElementType; color: string; children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 flex items-center gap-2 border-b border-zinc-800 pb-2">
        <Icon className={`h-3.5 w-3.5 ${color}`} />
        <span className={`text-[11px] font-bold uppercase tracking-wider ${color}`}>{title}</span>
      </div>
      <div className="space-y-1.5 text-xs">{children}</div>
    </div>
  );
}

export function AnalystPanel({ asset }: AnalystPanelProps) {
  // Helper to filter STUB text
  const filterStub = (text: string | null | undefined): string => {
    if (!text) return '-';
    if (text.includes('[LLM STUB]') || text.includes('[NO API KEY]')) {
      return 'Run pipeline for data';
    }
    return text;
  };

  const rsiCls = asset.rsi_signal === 'overbought' ? 'text-red-400' : asset.rsi_signal === 'oversold' ? 'text-green-400' : 'text-zinc-100';
  const macdCls = asset.macd_signal?.includes('bullish') ? 'text-green-400' : asset.macd_signal?.includes('bearish') ? 'text-red-400' : 'text-zinc-400';
  const sentCls = asset.sentiment === 'bullish' ? 'text-green-400' : asset.sentiment === 'bearish' ? 'text-red-400' : 'text-zinc-400';

  return (
    <div className="grid grid-cols-2 gap-3">
      <Quad title="Technical Analyst" Icon={Activity} color="text-blue-400">
        <Row label="RSI (14)" value={Number.isFinite(asset.rsi_14) ? asset.rsi_14.toFixed(1) : 'N/A'} cls={rsiCls} />
        <Row label="RSI Signal" value={filterStub(asset.rsi_signal)} cls={rsiCls} />
        <Row label="MACD" value={filterStub(asset.macd_signal?.replace(/_/g, ' '))} cls={macdCls} />
        <Row label="vs SMA 200" value={filterStub(asset.price_vs_sma200)} cls={asset.price_vs_sma200 === 'above' ? 'text-green-400' : 'text-red-400'} />
        <Row label="Volume" value={filterStub(asset.volume_trend)} />
        {Number.isFinite(asset.atr_pct) && asset.atr_pct > 0 && <Row label="ATR (14)" value={asset.atr_pct.toFixed(2) + '%'} />}
        {asset.market_regime && (
          <Row label="Regime" value={filterStub(asset.market_regime)}
            cls={asset.market_regime.toLowerCase().includes('bull') ? 'text-green-400' : asset.market_regime.toLowerCase().includes('bear') ? 'text-red-400' : 'text-zinc-400'}
          />
        )}
        {asset.stop_loss_suggestion != null && <Row label="Stop Loss" value={'$' + (asset.stop_loss_suggestion ?? 0).toLocaleString()} cls="text-red-400" />}
        {asset.target_suggestion != null && <Row label="Target" value={'$' + (asset.target_suggestion ?? 0).toLocaleString()} cls="text-green-400" />}
      </Quad>

      <Quad title="Sentiment Analyst" Icon={MessageCircle} color="text-purple-400">
        {asset.sentiment ? (
          <>
            <Row label="Overall" value={filterStub(asset.sentiment)} cls={sentCls} />
            {asset.sentiment_score != null && Number.isFinite(asset.sentiment_score) && <Row label="Score" value={asset.sentiment_score.toFixed(3)} />}
            {asset.articles_found != null && <Row label="Found" value={asset.articles_found} />}
            {asset.articles_scored != null && <Row label="Scored" value={asset.articles_scored} />}
            {asset.sentiment_source && <Row label="Source" value={filterStub(asset.sentiment_source)} cls="text-zinc-500" />}
            {asset.sentiment_summary && (
              <div className="mt-2 rounded bg-zinc-800/30 p-2">
                <p className="leading-relaxed text-zinc-400">
                  {filterStub(asset.sentiment_summary.substring(0, 200))}{asset.sentiment_summary.length > 200 ? '…' : ''}
                </p>
              </div>
            )}
          </>
        ) : <p className="text-zinc-600">No sentiment data</p>}
      </Quad>

      <Quad title="On-Chain & Derivatives" Icon={Link2} color="text-amber-400">
        {asset.onchain_risk && (
          <Row label="On-chain Risk" value={filterStub(asset.onchain_risk)}
            cls={asset.onchain_risk.toLowerCase().includes('high') ? 'text-red-400' : asset.onchain_risk.toLowerCase().includes('low') ? 'text-green-400' : 'text-zinc-400'}
          />
        )}
        {asset.funding_rate_pct != null && Number.isFinite(asset.funding_rate_pct) && (
          <Row label="Funding Rate" value={asset.funding_rate_pct.toFixed(4) + '%'}
            cls={asset.funding_rate_pct > 0.1 ? 'text-red-400' : asset.funding_rate_pct < -0.05 ? 'text-green-400' : 'text-zinc-100'}
          />
        )}
        {asset.funding_signal && <Row label="Funding Signal" value={filterStub(asset.funding_signal)} />}
        {asset.open_interest_usd != null && Number.isFinite(asset.open_interest_usd) && <Row label="Open Interest" value={'$' + (asset.open_interest_usd / 1e9).toFixed(2) + 'B'} />}
        {asset.oi_change_pct != null && Number.isFinite(asset.oi_change_pct) && (
          <Row label="OI Change" value={(asset.oi_change_pct >= 0 ? '+' : '') + asset.oi_change_pct.toFixed(2) + '%'}
            cls={asset.oi_change_pct > 0 ? 'text-green-400' : asset.oi_change_pct < 0 ? 'text-red-400' : 'text-zinc-400'}
          />
        )}
        {asset.derivatives_signal && <Row label="Derivatives" value={filterStub(asset.derivatives_signal)} />}
        {!asset.onchain_risk && asset.funding_rate_pct == null && !asset.derivatives_signal && (
          <p className="text-zinc-600">No on-chain data</p>
        )}
      </Quad>

      <Quad title="News & Alerts" Icon={Newspaper} color="text-green-400">
        <Row label="7d Change" value={asset.price_change_7d_pct != null && Number.isFinite(asset.price_change_7d_pct) ? ((asset.price_change_7d_pct >= 0 ? '+' : '') + asset.price_change_7d_pct.toFixed(2) + '%') : '—'}
          cls={asset.price_change_7d_pct != null && Number.isFinite(asset.price_change_7d_pct) && asset.price_change_7d_pct >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <Row label="Stop Method" value={filterStub(asset.stop_method)} />
        {asset.signal_conflict && (
          <div className="rounded bg-orange-500/10 px-2 py-1 text-[11px] text-orange-400">Signal conflict detected</div>
        )}
        {asset.warning && (
          <div className="rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-400">{filterStub(asset.warning)}</div>
        )}
        {asset.alerts?.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {asset.alerts.map((a, i) => (
              <span key={i} className="rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-400">{a}</span>
            ))}
          </div>
        )}
        {!asset.warning && (asset.alerts?.length ?? 0) === 0 && !asset.signal_conflict && (
          <p className="text-zinc-600">No active alerts</p>
        )}
      </Quad>
    </div>
  );
}
