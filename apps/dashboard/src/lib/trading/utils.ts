import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { SignalType, RiskLevel, ConfidenceLevel } from './types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatCurrency(value: number | null | undefined, currency = 'USD'): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: value < 1 ? 4 : 2,
    maximumFractionDigits: value < 1 ? 4 : 2,
  }).format(value);
}

export function formatPercentage(value: number | null | undefined, decimals = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(decimals)}%`;
}

export function formatNumber(value: number | null | undefined, decimals = 2): string {
  if (value == null) return '—';
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function formatLargeNumber(value: number): string {
  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(2)}B`;
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(2)}K`;
  }
  return formatNumber(value);
}

export function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatDate(timestamp: string): string {
  const date = new Date(timestamp);
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date);
}

export function getSignalColor(signal: SignalType): string {
  switch (signal) {
    case 'BUY':
      return 'text-green-500';
    case 'SELL':
      return 'text-red-500';
    case 'WAIT':
    case 'WATCH FOR ENTRY':
      return 'text-amber-500';
    case 'NO SIGNAL':
    default:
      return 'text-gray-500';
  }
}

export function getSignalBgColor(signal: SignalType): string {
  switch (signal) {
    case 'BUY':
      return 'bg-green-500/10 border-green-500/20';
    case 'SELL':
      return 'bg-red-500/10 border-red-500/20';
    case 'WAIT':
    case 'WATCH FOR ENTRY':
      return 'bg-amber-500/10 border-amber-500/20';
    case 'NO SIGNAL':
    default:
      return 'bg-gray-500/10 border-gray-500/20';
  }
}

export function getRiskColor(risk: RiskLevel): string {
  switch (risk) {
    case 'LOW':
      return 'text-green-500';
    case 'MEDIUM':
      return 'text-amber-500';
    case 'HIGH':
      return 'text-orange-500';
    case 'CRITICAL':
      return 'text-red-500';
    default:
      return 'text-gray-500';
  }
}

export function getRiskBgColor(risk: RiskLevel): string {
  switch (risk) {
    case 'LOW':
      return 'bg-green-500/10 border-green-500/20';
    case 'MEDIUM':
      return 'bg-amber-500/10 border-amber-500/20';
    case 'HIGH':
      return 'bg-orange-500/10 border-orange-500/20';
    case 'CRITICAL':
      return 'bg-red-500/10 border-red-500/20';
    default:
      return 'bg-gray-500/10 border-gray-500/20';
  }
}

export function confidenceToPercent(confidence: ConfidenceLevel): number {
  switch (confidence) {
    case 'low':
      return 33;
    case 'medium':
      return 66;
    case 'high':
      return 100;
    default:
      return 50;
  }
}

export function getConfidenceColor(confidence: number): string {
  if (confidence >= 75) return 'text-green-500';
  if (confidence >= 50) return 'text-amber-500';
  return 'text-red-500';
}

export function timeAgo(timestamp: string): string {
  const now = new Date();
  const past = new Date(timestamp);
  const diffMs = now.getTime() - past.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
}

export function filterStub(
  text: string | null | undefined,
  fallback = '—',
  stubMessage = 'Run pipeline for data'
): string {
  if (!text) return fallback;
  if (text.includes('[LLM STUB]') || text.includes('[NO API KEY]')) {
    return stubMessage;
  }
  return text;
}
