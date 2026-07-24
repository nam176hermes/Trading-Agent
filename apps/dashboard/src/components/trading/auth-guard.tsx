'use client';

import { useEffect, useState } from 'react';
import { Lock } from 'lucide-react';
import { readAuthenticatedResponse } from '../../lib/trading/browser-auth-response';

const AUTH_TIMEOUT_MS = 10_000;

interface AuthGuardProps {
  children: React.ReactNode;
}

export function AuthGuard({ children }: AuthGuardProps) {
  const [state, setState] = useState<'loading' | 'unauthorized' | 'authorized'>('loading');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(false);
  const [configurationError, setConfigurationError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);
    async function check() {
      try {
        const res = await fetch('/api/auth/session', {
          method: 'GET',
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (await readAuthenticatedResponse(res)) {
          setState('authorized');
          return;
        }
        if (res.status === 503) setConfigurationError(true);
        setState('unauthorized');
      } catch {
        setState('unauthorized');
      } finally {
        window.clearTimeout(timeout);
      }
    }
    check();

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(false);
    setConfigurationError(false);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);

    try {
      const res = await fetch('/api/auth/session', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
        signal: controller.signal,
      });

      if (await readAuthenticatedResponse(res)) {
        setPassword('');
        setState('authorized');
      } else {
        if (res.status === 503) setConfigurationError(true);
        setError(true);
      }
    } catch {
      setError(true);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  if (state === 'loading') {
    return (
      <div className="flex items-center justify-center min-h-screen bg-zinc-950">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
      </div>
    );
  }

  if (state === 'unauthorized') {
    return (
      <div className="flex items-center justify-center min-h-screen bg-zinc-950">
        <form
          onSubmit={handleSubmit}
          className="w-full max-w-sm rounded-lg border border-zinc-800 bg-zinc-900 p-6"
        >
          <div className="flex items-center gap-2 mb-4 text-zinc-200">
            <Lock className="h-5 w-5 text-zinc-400" />
            <h2 className="text-lg font-semibold">Trading Dashboard</h2>
          </div>
          <p className="text-sm text-zinc-500 mb-4">Enter password to continue.</p>
          {configurationError && <p className="mb-4 text-xs text-red-400">Dashboard authentication is not configured. Mutating controls are disabled.</p>}

          <input
            type="password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); setError(false); }}
            placeholder="Password"
            autoFocus
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-500 focus:border-zinc-500 focus:outline-none"
          />

          {error && (
            <p className="mt-2 text-xs text-red-400">Invalid password. Try again.</p>
          )}

          <button
            type="submit"
            disabled={!password}
            className="mt-4 w-full rounded bg-zinc-200 px-3 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-100 disabled:opacity-40"
          >
            Unlock
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
