import fs from 'fs';
import path from 'path';
import { EquityReport, CryptoReport, EquityAsset, CryptoAsset, FundamentalsReport, FundamentalAsset, MacroReport, NewsReport, TaValidationReport } from './types';
import { memoryDir, reportsDir } from './paths';

function readLatestReport<T>(dir: string, prefix: string): T | null {
  try {
    const files = fs.readdirSync(dir)
      .filter(f => f.startsWith(prefix) && f.endsWith('.json'))
      .sort()
      .reverse();

    if (files.length === 0) return null;

    const content = fs.readFileSync(path.join(dir, files[0]), 'utf-8');
    return JSON.parse(content) as T;
  } catch {
    return null;
  }
}

export function getLatestEquityReport(): EquityReport | null {
  return readLatestReport<EquityReport>(reportsDir(), 'equity_report_');
}

export function getLatestCryptoReport(): CryptoReport | null {
  return readLatestReport<CryptoReport>(reportsDir(), 'crypto_report_');
}

export function getAllEquityAssets(): EquityAsset[] {
  const report = getLatestEquityReport();
  return report?.assets ?? [];
}

export function getAllCryptoAssets(): CryptoAsset[] {
  const report = getLatestCryptoReport();
  return report?.assets ?? [];
}

export function getLatestFundamentalsReport(): FundamentalsReport | null {
  return readLatestReport<FundamentalsReport>(reportsDir(), 'fundamentals_report_');
}

export function getFundamentalsBySymbol(symbol: string): FundamentalAsset | null {
  const report = getLatestFundamentalsReport();
  return report?.assets.find(a => a.symbol === symbol) ?? null;
}

export function getLatestMacroReport(): MacroReport | null {
  return readLatestReport<MacroReport>(reportsDir(), 'macro_report_');
}

export function getLatestNewsReport(): NewsReport | null {
  return readLatestReport<NewsReport>(reportsDir(), 'news_report_');
}

export function getLatestTaValidationReport(): TaValidationReport | null {
  return readLatestReport<TaValidationReport>(reportsDir(), 'ta_validation_');
}

// ── Phase 5: Paper Trading Portfolio ──────────────────────────────

export interface PaperPortfolio {
  cash: number;
  positions: Record<string, { shares: number; avg_cost: number }>;
  pnl: number;
  orders: unknown[];
  created_at: string | null;
}

export function getPaperPortfolio(): PaperPortfolio | null {
  try {
    const portfolioPath = path.join(memoryDir(), 'paper', 'portfolio.json');
    if (!fs.existsSync(portfolioPath)) return null;
    return JSON.parse(fs.readFileSync(portfolioPath, 'utf-8')) as PaperPortfolio;
  } catch {
    return null;
  }
}

export function getPaperOrders(limit = 50): unknown[] {
  try {
    const ordersPath = path.join(memoryDir(), 'paper', 'orders.jsonl');
    if (!fs.existsSync(ordersPath)) return [];
    const raw = fs.readFileSync(ordersPath, 'utf-8');
    return raw.trim().split('\n').filter(Boolean).reverse().slice(0, limit).map(line => {
      try { return JSON.parse(line); } catch { return null; }
    }).filter(Boolean);
  } catch {
    return [];
  }
}

export function getTradeJournal(limit = 50): unknown[] {
  try {
    const journalPath = path.join(memoryDir(), 'trade_journal.jsonl');
    if (!fs.existsSync(journalPath)) return [];
    const raw = fs.readFileSync(journalPath, 'utf-8');
    return raw.trim().split('\n').filter(Boolean).reverse().slice(0, limit).map(line => {
      try { return JSON.parse(line); } catch { return null; }
    }).filter(Boolean);
  } catch {
    return [];
  }
}
