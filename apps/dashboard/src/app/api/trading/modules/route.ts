import { NextResponse } from 'next/server';

interface SystemModule {
  id: string;
  name: string;
  file: string;
  description: string;
  category: 'data' | 'analysis' | 'risk' | 'execution' | 'evaluation';
  lines?: number;
  status: 'active' | 'idle' | 'error';
  lastRun?: string;
}

const MODULES: SystemModule[] = [
  {
    id: 'data-collector',
    name: 'Data Collector',
    file: 'data_collector.py',
    description: 'Multi-vendor price/OHLCV fetching with fallback chain (Yahoo → Binance → CoinGecko)',
    category: 'data',
    lines: 493,
    status: 'idle',
    lastRun: '2026-05-08',
  },
  {
    id: 'data-vendors',
    name: 'Data Vendors',
    file: 'data_vendors.py',
    description: 'Vendor health checks, freshness validation, server time drift detection. Max staleness: 5min',
    category: 'data',
    lines: 493,
    status: 'idle',
  },
  {
    id: 'ta-engine',
    name: 'TA Engine',
    file: 'ta_engine.py',
    description: 'RSI, MACD, SMA-200, Bollinger Bands calculation via pandas-ta',
    category: 'analysis',
    status: 'idle',
  },
  {
    id: 'regime-detector',
    name: 'Regime Detector',
    file: 'regime_detector.py',
    description: 'ADX + BB + SMA-50 voting with ADX veto. Produces signal modifier dict',
    category: 'analysis',
    lines: 487,
    status: 'idle',
  },
  {
    id: 'sentiment-filter',
    name: 'Sentiment Filter',
    file: 'sentiment_filter.py',
    description: '3-tier source credibility (Tier 1: Bloomberg/Reuters 3x weight, Tier 2: BeInCrypto 2x, Tier 3: unknown 1x)',
    category: 'analysis',
    lines: 543,
    status: 'idle',
  },
  {
    id: 'derivatives-collector',
    name: 'Derivatives Collector',
    file: 'derivatives_collector.py',
    description: 'Funding rate + OI + price → 3D signal matrix with 10 signal types',
    category: 'analysis',
    lines: 347,
    status: 'idle',
  },
  {
    id: 'debate',
    name: 'Debate Engine',
    file: 'debate.py',
    description: 'Multi-round bull/bear debate with turn-order balancing and point survival tracking',
    category: 'analysis',
    lines: 595,
    status: 'idle',
  },
  {
    id: 'risk-personas',
    name: 'Risk Personas',
    file: 'risk_personas.py',
    description: 'Aggressive/Conservative/Neutral persona deliberation with synthesis',
    category: 'risk',
    lines: 389,
    status: 'idle',
  },
  {
    id: 'atr-stops',
    name: 'ATR Stops',
    file: 'atr_stops.py',
    description: "Wilder's smoothing (2x ATR stop, 3x ATR target, 1:1.5 R:R)",
    category: 'risk',
    lines: 194,
    status: 'idle',
  },
  {
    id: 'backtest-analyzer',
    name: 'Backtest Analyzer',
    file: 'backtest_analyzer.py',
    description: 'Performance report generator. 11 metrics, Go/No-Go criteria, confidence calibration',
    category: 'evaluation',
    lines: 554,
    status: 'idle',
    lastRun: '2026-05-03',
  },
];

export async function GET() {
  return NextResponse.json(MODULES);
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
