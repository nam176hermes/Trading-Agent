'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { AssetClass } from '@/lib/trading/types';

const TABS: { id: AssetClass; label: string }[] = [
  { id: 'crypto', label: 'Crypto' },
  { id: 'stock', label: 'Stocks' },
  { id: 'etf', label: 'ETFs' },
  { id: 'forex', label: 'Forex' },
];

export function AssetClassTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const active = (searchParams.get('class') as AssetClass) || 'crypto';

  function handleClick(id: AssetClass) {
    const params = new URLSearchParams(searchParams.toString());
    params.set('class', id);
    router.push(`/dashboard?${params.toString()}`, { scroll: false });
  }

  return (
    <div className="flex gap-1 border-b border-zinc-800 bg-zinc-950/50 px-6 py-1.5">
      {TABS.map(tab => (
        <button
          key={tab.id}
          onClick={() => handleClick(tab.id)}
          className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
            active === tab.id
              ? 'bg-zinc-700 text-zinc-100'
              : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
