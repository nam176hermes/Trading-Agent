import { NextResponse } from 'next/server';

interface TradingAgent {
  id: string;
  name: string;
  role: string;
  icon: string;
  model: string;
  stage: 'analyst' | 'debate' | 'risk' | 'execution';
  consumes: string[];
  produces: string;
  sourceFile: string;
}

const AGENTS: TradingAgent[] = [
  {
    id: 'technical',
    name: 'Technical Analyst',
    role: 'RSI, MACD, SMA-200, Bollinger Bands, ATR stops',
    icon: 'Activity',
    model: 'deepseek-chat',
    stage: 'analyst',
    consumes: ['Binance OHLCV candles', 'pandas-ta indicators'],
    produces: '46-field AssetData dict with TA signals',
    sourceFile: 'analysts.py:50-120',
  },
  {
    id: 'sentiment',
    name: 'Sentiment Analyst',
    role: 'News sentiment aggregation with 3-tier source credibility',
    icon: 'MessageCircle',
    model: 'deepseek-chat',
    stage: 'analyst',
    consumes: ['CryptoPanic News API', '3-tier credibility filter'],
    produces: 'Sentiment score + article count + tier-weighted confidence',
    sourceFile: 'sentiment_filter.py:106-543',
  },
  {
    id: 'onchain',
    name: 'On-Chain & Derivatives',
    role: 'Funding rate, open interest, squeeze detection',
    icon: 'Link2',
    model: 'deepseek-chat',
    stage: 'analyst',
    consumes: ['Binance futures funding rate', 'Open interest data'],
    produces: '3D signal matrix (funding × OI × price) with 10 signal types',
    sourceFile: 'derivatives_collector.py:231-347',
  },
  {
    id: 'regime',
    name: 'Regime Detector',
    role: 'ADX, Bollinger Bands, SMA-50 slope — 3-method vote with ADX veto',
    icon: 'Gauge',
    model: 'N/A (pure computation)',
    stage: 'analyst',
    consumes: ['OHLCV candles (≥60 minimum)'],
    produces: 'Regime label + signal modifier (suppresses RSI/MACD in choppy markets)',
    sourceFile: 'regime_detector.py:370-441',
  },
  {
    id: 'debate',
    name: 'Bull/Bear Debate',
    role: 'Multi-round adversarial debate with turn-order balancing',
    icon: 'Swords',
    model: 'deepseek-reasoner',
    stage: 'debate',
    consumes: ['4 analyst reports', 'Historical debate memory'],
    produces: 'BullArgument + BearArgument with survived/rebutted points',
    sourceFile: 'debate.py:271-595',
  },
  {
    id: 'risk',
    name: 'Risk Personas',
    role: 'Aggressive, Conservative, Neutral personas — independent assessment',
    icon: 'Shield',
    model: 'deepseek-reasoner',
    stage: 'risk',
    consumes: ['Debate synthesis', 'Per-agent accuracy memory'],
    produces: '3 RiskAssessment dicts + synthesis (position size, stop loss, risk level)',
    sourceFile: 'risk_personas.py:216-389',
  },
  {
    id: 'pm',
    name: 'Portfolio Manager',
    role: 'Final judge: RATIFY, MODIFY, or REJECT with conservative fallback',
    icon: 'Scale',
    model: 'deepseek-reasoner',
    stage: 'execution',
    consumes: ['Debate winner', '3 risk assessments', 'Current portfolio state'],
    produces: 'Final action (BUY/SELL/WATCH) + position size + stop loss',
    sourceFile: 'portfolio_manager.py:48-209',
  },
];

export async function GET() {
  return NextResponse.json(AGENTS);
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
