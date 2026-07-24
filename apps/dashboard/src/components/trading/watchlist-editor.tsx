'use client';

import { useState, useEffect, useRef } from 'react';
import { Plus, X } from 'lucide-react';

interface WatchlistData {
  symbols: string[];
  count: number;
}

export function WatchlistEditor() {
  const [data, setData] = useState<WatchlistData>({ symbols: [], count: 0 });
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newSymbol, setNewSymbol] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function fetchWatchlist() {
    try {
      const res = await fetch('/api/trading/watchlist');
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch {
      // Silently ignore fetch errors
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const initialFetch = setTimeout(fetchWatchlist, 0);
    return () => clearTimeout(initialFetch);
  }, []);

  useEffect(() => {
    if (adding && inputRef.current) inputRef.current.focus();
  }, [adding]);

  async function handleRemove(symbol: string) {
    try {
      const res = await fetch('/api/trading/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'remove', symbol }),
      });
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch {
      // Silently ignore
    }
  }

  function handleAddClick() {
    setAdding(true);
    setNewSymbol('');
    setError(null);
  }

  async function handleAddSubmit() {
    const sym = newSymbol.toUpperCase().trim();
    if (!sym) {
      setAdding(false);
      return;
    }

    if (!/^[A-Z]{1,5}$/.test(sym)) {
      setError('Invalid: uppercase 1-5 chars (e.g. DOGE)');
      return;
    }

    try {
      const res = await fetch('/api/trading/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'add', symbol: sym }),
      });
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setAdding(false);
        setError(null);
      } else {
        const json = await res.json();
        setError(json.error || 'Failed to add');
      }
    } catch {
      setError('Network error');
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') handleAddSubmit();
    if (e.key === 'Escape') {
      setAdding(false);
      setError(null);
    }
  }

  if (loading) return null;

  return (
    <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-zinc-300">
          Watchlist · {data.count} asset{data.count !== 1 ? 's' : ''}
        </h3>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {data.symbols.map(sym => (
          <span
            key={sym}
            className="inline-flex items-center gap-1 rounded-md bg-zinc-800 px-2.5 py-1 text-xs font-mono text-zinc-200"
          >
            {sym}
            <button
              onClick={() => handleRemove(sym)}
              className="ml-0.5 rounded p-0.5 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"
              title={`Remove ${sym}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}

        {adding ? (
          <span className="inline-flex items-center gap-1">
            <input
              ref={inputRef}
              type="text"
              value={newSymbol}
              onChange={e => { setNewSymbol(e.target.value); setError(null); }}
              onKeyDown={handleKeyDown}
              onBlur={() => { if (!newSymbol.trim()) setAdding(false); }}
              placeholder="SYM"
              maxLength={5}
              className="w-20 rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs font-mono text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-blue-500"
            />
            {error && (
              <span className="text-[10px] text-red-400">{error}</span>
            )}
          </span>
        ) : (
          <button
            onClick={handleAddClick}
            className="inline-flex items-center gap-1 rounded-md border border-dashed border-zinc-700 px-2.5 py-1 text-xs text-zinc-500 hover:text-zinc-300 hover:border-zinc-500 transition-colors"
          >
            <Plus className="h-3 w-3" />
            Add
          </button>
        )}
      </div>
    </div>
  );
}
