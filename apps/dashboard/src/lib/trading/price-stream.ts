'use client';

/**
 * Singleton SSE price stream shared by all components.
 * Instead of each SignalCard/PortfolioCard opening its own EventSource
 * (which exhausts the browser's 6-connection limit), all components
 * subscribe to this single shared connection.
 */

// Raw SSE payload: { BTC: { price: 76553, change_pct_24h: 0.02, ... }, _health: {...}, ... }
export type PriceEntry = { price: number; change_pct_24h?: number; volume_24h?: number; high_24h?: number };
export type PricePayload = Record<string, PriceEntry | Record<string, unknown>>;

type PriceListener = (payload: PricePayload) => void;

let singletonSource: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<PriceListener>();
let lastPayload: PricePayload = {};

function connect() {
  if (typeof window === 'undefined') return;
  if (singletonSource && singletonSource.readyState !== EventSource.CLOSED) return;

  try {
    const es = new EventSource('/api/trading/prices-stream');
    singletonSource = es;

    es.onmessage = (e: MessageEvent) => {
      try {
        // Pass the full raw payload so each consumer gets what it needs
        const raw = JSON.parse(e.data) as PricePayload;
        lastPayload = raw;
        for (const fn of listeners) fn(raw);
      } catch { /* ignore malformed data */ }
    };

    es.onerror = () => {
      es.close();
      singletonSource = null;
      if (listeners.size > 0) {
        reconnectTimer = setTimeout(connect, 10_000);
      }
    };
  } catch { /* SSE not supported */ }
}

function disconnect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (singletonSource) { singletonSource.close(); singletonSource = null; }
}

export function subscribePrices(fn: PriceListener): () => void {
  listeners.add(fn);
  if (listeners.size === 1) connect();
  // Deliver last known payload immediately so UI doesn't flash
  if (Object.keys(lastPayload).length > 0) {
    try { fn(lastPayload); } catch { /* ignore */ }
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) disconnect();
  };
}

/** Convenience helper: extract a plain price number for a symbol, handling both object and scalar formats. */
export function extractPrice(payload: PricePayload, symbol: string): number | null {
  const entry = payload[symbol];
  if (entry == null) return null;
  if (typeof entry === 'number' && Number.isFinite(entry)) return entry;
  if (typeof entry === 'object') {
    const p = entry.price;
    if (typeof p === 'number' && Number.isFinite(p)) return p;
  }
  return null;
}
