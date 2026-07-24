'use client';

import { useState } from 'react';

export type DateRangePreset = '7d' | '30d' | 'all';

export interface DateRange {
  from: Date | null;
  to: Date | null;
  preset: DateRangePreset;
}

interface DateRangeFilterProps {
  value: DateRange;
  onChange: (range: DateRange) => void;
}

const PRESETS: { key: DateRangePreset; label: string }[] = [
  { key: '7d', label: 'Last 7 days' },
  { key: '30d', label: 'Last 30 days' },
  { key: 'all', label: 'All time' },
];

function toDateInputValue(d: Date | null): string {
  if (!d) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function presetToRange(preset: DateRangePreset): { from: Date | null; to: Date | null } {
  const now = new Date();
  if (preset === '7d') {
    const from = new Date(now);
    from.setDate(from.getDate() - 7);
    return { from, to: now };
  }
  if (preset === '30d') {
    const from = new Date(now);
    from.setDate(from.getDate() - 30);
    return { from, to: now };
  }
  return { from: null, to: null };
}

export function DateRangeFilter({ value, onChange }: DateRangeFilterProps) {
  const [showCustom, setShowCustom] = useState(value.preset === 'all' && (!!value.from || !!value.to));

  const selectPreset = (preset: DateRangePreset) => {
    const { from, to } = presetToRange(preset);
    onChange({ from, to, preset });
    setShowCustom(false);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-zinc-500 mr-1">Filter:</span>
      {PRESETS.map(p => (
        <button
          key={p.key}
          onClick={() => selectPreset(p.key)}
          className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
            value.preset === p.key && !showCustom
              ? 'bg-violet-600 text-white'
              : 'bg-zinc-800 text-zinc-400 hover:text-white hover:bg-zinc-700'
          }`}
        >
          {p.label}
        </button>
      ))}
      <button
        onClick={() => { setShowCustom(!showCustom); if (!showCustom) onChange({ ...value, preset: 'all' }); }}
        className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
          showCustom
            ? 'bg-violet-600 text-white'
            : 'bg-zinc-800 text-zinc-400 hover:text-white hover:bg-zinc-700'
        }`}
      >
        Custom
      </button>
      {showCustom && (
        <div className="flex items-center gap-2 ml-2">
          <input
            type="date"
            value={toDateInputValue(value.from)}
            onChange={e => onChange({ ...value, preset: 'all', from: e.target.value ? new Date(e.target.value) : null })}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-100 focus:border-violet-500 focus:outline-none"
          />
          <span className="text-xs text-zinc-500">to</span>
          <input
            type="date"
            value={toDateInputValue(value.to)}
            onChange={e => onChange({ ...value, preset: 'all', to: e.target.value ? new Date(e.target.value) : null })}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-100 focus:border-violet-500 focus:outline-none"
          />
        </div>
      )}
    </div>
  );
}
