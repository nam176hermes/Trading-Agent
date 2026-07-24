import { NextResponse } from 'next/server';
import path from 'path';
import { authorizeMutation } from '@/lib/trading/auth';
import { readLocalStateFile, writePrivateLocalStateFile } from '@/lib/trading/local-state';
import { memoryDir } from '@/lib/trading/paths';
import { readBoundedJsonBody } from '@/lib/trading/request-body';

const MAX_UPDATE_STOP_BODY_BYTES = 16 * 1024;
const MAX_TRAILING_STOPS_STATE_BYTES = 128 * 1024;
const MAX_STOP_LOSS = 1_000_000_000_000;
const RESERVED_SYMBOLS = new Set(['__proto__', 'constructor', 'prototype']);

function isExactObject(value: unknown, keys: string[]): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function canonicalSymbol(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 32) return null;
  const symbol = value.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.-]{0,15}$/.test(symbol) || RESERVED_SYMBOLS.has(symbol.toLowerCase())) return null;
  return symbol;
}

function trailingStopsPath(): string {
  return path.join(memoryDir(), 'trailing_stops.json');
}

/**
 * POST /api/trading/update-stop
 * Body: { symbol: "SOL", stopLoss: 85.00 }
 *
 * Writes a manual stop-loss override to trailing_stops.json.
 * Returns the updated position data.
 */
export async function POST(request: Request) {
  const authError = authorizeMutation(request, 'position.update_stop', 'MUTATION_EXECUTION_SENSITIVE', 'operator');
  if (authError) return authError;

  try {
    const parsed = await readBoundedJsonBody(request, MAX_UPDATE_STOP_BODY_BYTES);
    if (!parsed.ok) {
      return NextResponse.json(
        { error: parsed.reason === 'too_large' ? 'Request body too large' : 'Invalid request body' },
        { status: parsed.reason === 'too_large' ? 413 : 400 },
      );
    }
    if (!isExactObject(parsed.value, ['symbol', 'stopLoss'])) {
      return NextResponse.json({ error: 'symbol and stopLoss (number) are required' }, { status: 400 });
    }
    const { stopLoss } = parsed.value;
    const symbol = canonicalSymbol(parsed.value.symbol);

    if (!symbol || typeof stopLoss !== 'number' || !Number.isFinite(stopLoss)) {
      return NextResponse.json(
        { error: 'symbol and stopLoss (number) are required' },
        { status: 400 },
      );
    }

    if (stopLoss <= 0 || stopLoss > MAX_STOP_LOSS) {
      return NextResponse.json(
        { error: 'stopLoss must be positive' },
        { status: 400 },
      );
    }

    // Load existing stops. Corrupted risk state must never be replaced with a
    // partial map because that could silently discard stops for other assets.
    const target = trailingStopsPath();
    let stops: Record<string, unknown> = Object.create(null);
    try {
      const content = readLocalStateFile(target, MAX_TRAILING_STOPS_STATE_BYTES);
      if (content !== null) {
        const loaded: unknown = JSON.parse(content);
        if (!loaded || typeof loaded !== 'object' || Array.isArray(loaded)) {
          throw new Error('stop state must be an object');
        }
        stops = Object.assign(Object.create(null), loaded);
      }
    } catch {
      return NextResponse.json(
        { error: 'Existing stop state is invalid' },
        { status: 503 },
      );
    }

    // Update with manual override
    const current = Object.prototype.hasOwnProperty.call(stops, symbol) ? stops[symbol] : null;
    const existing = current && typeof current === 'object' && !Array.isArray(current)
      ? current as Record<string, unknown>
      : { stop: stopLoss, highest_price: 0 };
    stops[symbol] = {
      ...existing,
      stop: stopLoss,
      manual: true,
    };

    // Write back atomically
    writePrivateLocalStateFile(target, JSON.stringify(stops));

    return NextResponse.json({
      success: true,
      symbol,
      stopLoss,
      manual: true,
    });
  } catch (error) {
    console.error('Error updating stop-loss:', error);
    return NextResponse.json(
      { error: 'Failed to update stop-loss' },
      { status: 500 },
    );
  }
}

export const dynamic = 'force-dynamic';
export const revalidate = 0;
