import 'server-only';

import {
  getControlDecisions,
  getControlMarket,
  getControlStatus,
  type ControlDecision,
  type ControlMarketAsset,
} from './control-api';
import type {
  AssetClass,
  AssetData,
  AssetSymbol,
  Decision,
  MarketReport,
  MarketTicker,
  ScratchpadEntry,
  TypedDecision,
} from './types';

const CRYPTO = new Set(['BTC', 'ETH', 'SOL', 'TON', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'MATIC']);
const ETF = new Set(['SPY', 'QQQ', 'IWM', 'DIA', 'VIX']);

function assetClass(symbol: string): AssetClass {
  if (CRYPTO.has(symbol)) return 'crypto';
  if (ETF.has(symbol)) return 'etf';
  if (symbol.includes('/')) return 'forex';
  return 'stock';
}

function legacyAction(action: string): Decision['suggestion'] {
  return action.replaceAll('_', ' ') as Decision['suggestion'];
}

function legacyAsset(asset: ControlMarketAsset): AssetData {
  return {
    ...asset,
    asset_class: assetClass(asset.symbol),
    suggestion: legacyAction(asset.suggestion),
    _memory_context: asset.memory_context,
    _debate_context: asset.debate_context,
    _risk_context: asset.risk_context,
  };
}

function legacyDecision(decision: ControlDecision): Decision {
  return {
    ticker: decision.asset,
    date: decision.decision_at.slice(0, 10),
    suggestion: legacyAction(decision.action),
    confidence: decision.confidence,
    price_at_decision: decision.price_at_decision,
    signals: {
      ...decision.signals,
      calculated_at: decision.signals.calculated_at ?? '',
    },
    report_snippet: decision.report_snippet,
    stored_at: decision.decision_at,
    reflected: decision.reflected,
  };
}

export async function getLatestReport(): Promise<MarketReport | null> {
  const response = await getControlMarket();
  const report = response.data.report;
  return report ? {
    timestamp: report.as_of,
    assets: report.assets.map(legacyAsset),
    source: report.source_file,
  } : null;
}

export async function getAllReports(): Promise<MarketReport[]> {
  const report = await getLatestReport();
  return report ? [report] : [];
}

async function decisionPage(limit: number, asset?: string): Promise<Decision[]> {
  const bounded = Math.max(0, Math.min(1_000, Math.trunc(limit)));
  if (bounded === 0) return [];
  const result: Decision[] = [];
  let page = 1;
  while (result.length < bounded) {
    const pageSize = Math.min(200, bounded - result.length);
    const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (asset) query.set('asset', asset);
    const response = await getControlDecisions(query.toString());
    result.push(...response.data.items.map(legacyDecision));
    if (!response.data.has_next || response.data.items.length === 0) break;
    page += 1;
  }
  return result.slice(0, bounded);
}

export function getDecisions(limit = 50): Promise<Decision[]> {
  return decisionPage(limit);
}

export function getDecisionsBySymbol(symbol: AssetSymbol, limit = 20): Promise<Decision[]> {
  return decisionPage(limit, symbol.toUpperCase());
}

export async function getScratchpadEntries(
  _sessionId?: string,
  _limit = 100,
): Promise<ScratchpadEntry[]> {
  void _sessionId;
  void _limit;
  return [];
}

export async function getLatestScratchpadSession(): Promise<string | null> {
  return null;
}

export async function getAllScratchpadSessions(): Promise<string[]> {
  return [];
}

export async function getReflections(): Promise<unknown[]> {
  return [];
}

export async function getDataStats(): Promise<{
  totalReports: number | null;
  totalDecisions: number;
  totalScratchpadSessions: number | null;
  latestReportTimestamp: string | null;
}> {
  const [market, decisions] = await Promise.all([
    getControlMarket(),
    getControlDecisions('page=1&page_size=1'),
  ]);
  return {
    totalReports: null,
    totalDecisions: decisions.data.total,
    totalScratchpadSessions: null,
    latestReportTimestamp: market.data.report?.as_of ?? null,
  };
}

export async function getExecutionSummary(): Promise<{
  mode: string;
  positions: number | null;
  openOrders: number | null;
  realizedPnl: number | null;
  unrealizedPnl: number | null;
  error?: string;
}> {
  const response = await getControlStatus();
  return {
    mode: response.data.effective_mode.toLowerCase(),
    positions: null,
    openOrders: null,
    realizedPnl: null,
    unrealizedPnl: null,
    error: 'Position and P&L domains are not exposed by the canonical read contract.',
  };
}

export async function getAllMarketData(): Promise<{
  timestamp: string | null;
  tickers: MarketTicker[];
}> {
  const report = await getLatestReport();
  return {
    timestamp: report?.timestamp ?? null,
    tickers: report?.assets.map((asset) => ({
      symbol: asset.symbol,
      asset_class: asset.asset_class,
      price: asset.current_price,
      change24h: asset.price_change_24h_pct,
      volume24h: null,
    })) ?? [],
  };
}

export async function getTickersByClass(assetClassFilter?: AssetClass): Promise<MarketTicker[]> {
  const { tickers } = await getAllMarketData();
  return assetClassFilter ? tickers.filter((ticker) => ticker.asset_class === assetClassFilter) : tickers;
}

export async function getTypedDecisions(_limit = 10): Promise<TypedDecision[]> {
  void _limit;
  return [];
}
