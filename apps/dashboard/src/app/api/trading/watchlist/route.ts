import { NextResponse } from 'next/server';
import path from 'path';
import { authorizeMutation } from '@/lib/trading/auth';
import { readLocalStateFile, writePrivateLocalStateFile } from '@/lib/trading/local-state';
import { memoryDir } from '@/lib/trading/paths';
import { sourceUnavailable } from '@/lib/trading/source-unavailable';
import { readBoundedJsonBody } from '@/lib/trading/request-body';

const MAX_WATCHLIST_BODY_BYTES = 4 * 1024;
const MAX_WATCHLIST_STATE_BYTES = 8 * 1024;
const MAX_WATCHLIST_SYMBOLS = 64;
const RESERVED_SYMBOLS = new Set(['__proto__', 'constructor', 'prototype']);

function canonicalSymbol(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 32) return null;
  const symbol = value.trim().toUpperCase();
  if (!/^[A-Z]{1,5}$/.test(symbol) || RESERVED_SYMBOLS.has(symbol.toLowerCase())) return null;
  return symbol;
}

function parseWatchlistRequest(value: unknown): { action: 'add' | 'remove'; symbol: string } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  const keys = Object.keys(body).sort();
  if (keys.length !== 2 || keys[0] !== 'action' || keys[1] !== 'symbol'
    || (body.action !== 'add' && body.action !== 'remove')) return null;
  const symbol = canonicalSymbol(body.symbol);
  if (!symbol) return null;
  return { action: body.action, symbol };
}

function watchlistPath(): string {
  return path.join(memoryDir(), 'watchlist.json');
}

function getDefaultSymbols(): string[] {
  return ['BTC', 'ETH', 'SOL', 'TON', 'DOGE', 'AAPL', 'NVDA', 'MSFT'];
}

function loadOverrides(): string[] | null {
  const content = readLocalStateFile(watchlistPath(), MAX_WATCHLIST_STATE_BYTES);
  if (content === null) return null;
  const data: unknown = JSON.parse(content);
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('watchlist state must be an object');
  const record = data as Record<string, unknown>;
  const keys = Object.keys(record);
  if (keys.length !== 1 || keys[0] !== 'symbols' || !Array.isArray(record.symbols)
    || record.symbols.length > MAX_WATCHLIST_SYMBOLS) {
    throw new Error('watchlist state is invalid');
  }
  const symbols: string[] = [];
  for (const value of record.symbols) {
    const symbol = canonicalSymbol(value);
    if (!symbol) throw new Error('watchlist state contains an invalid symbol');
    symbols.push(symbol);
  }
  if (new Set(symbols).size !== symbols.length) throw new Error('watchlist state contains duplicate symbols');
  return symbols;
}

function saveOverrides(symbols: string[]): void {
  writePrivateLocalStateFile(watchlistPath(), JSON.stringify({ symbols }));
}

function getEffectiveSymbols(): string[] {
  const overrides = loadOverrides();
  return overrides ?? getDefaultSymbols();
}

export async function GET() {
  return sourceUnavailable('Watchlist overrides');
}

export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'watchlist.update', 'MUTATION_LOW_RISK', 'operator');
  if (authError) return authError;

  try {
    const parsed = await readBoundedJsonBody(request, MAX_WATCHLIST_BODY_BYTES);
    if (!parsed.ok) {
      return NextResponse.json(
        { error: parsed.reason === 'too_large' ? 'Request body too large' : 'Invalid request body' },
        { status: parsed.reason === 'too_large' ? 413 : 400 },
      );
    }
    const body = parseWatchlistRequest(parsed.value);
    if (!body) {
      return NextResponse.json(
        { error: 'Invalid symbol format. Must be uppercase, 1-5 characters.' },
        { status: 400 },
      );
    }
    const { action, symbol: sym } = body;

    let symbols: string[];
    try {
      symbols = getEffectiveSymbols();
    } catch {
      return NextResponse.json({ error: 'Existing watchlist state is invalid' }, { status: 503 });
    }

    if (action === 'add') {
      if (!symbols.includes(sym)) {
        if (symbols.length >= MAX_WATCHLIST_SYMBOLS) {
          return NextResponse.json({ error: 'Watchlist symbol limit reached' }, { status: 409 });
        }
        symbols = [...symbols, sym];
      }
    } else if (action === 'remove') {
      symbols = symbols.filter(s => s !== sym);
    }

    saveOverrides(symbols);
    return NextResponse.json({ symbols, count: symbols.length });
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error';
    console.error('Error updating watchlist:', error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
