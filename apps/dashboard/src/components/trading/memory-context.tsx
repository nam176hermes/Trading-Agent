'use client';

import { useEffect, useState } from 'react';
import { Brain, Lightbulb } from 'lucide-react';

interface Reflection {
  date: string;
  suggestion: string;
  raw_return_pct: number;
  reflection: string;
}

interface DecisionMemory {
  [key: string]: unknown;
}

interface Lesson {
  ticker: string;
  reflection: string;
}

interface MemoryData {
  reflections: Reflection[];
  decisions: DecisionMemory[];
  lessons: Lesson[];
  totalReflections: number;
  totalDecisions: number;
}

interface Props {
  ticker: string;
}

export function MemoryContext({ ticker }: Props) {
  const [memory, setMemory] = useState<MemoryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchMemory() {
      try {
        const res = await fetch(`/api/trading/memory?ticker=${ticker}&limit=5`);
        if (!res.ok) return;
        const data = await res.json();
        setMemory(data.memoryByTicker?.[ticker] || null);
      } catch {
        // Memory not available yet — normal for new agents
      } finally {
        setLoading(false);
      }
    }
    fetchMemory();
  }, [ticker]);

  if (loading) return null;
  if (!memory || (memory.reflections.length === 0 && memory.decisions.length === 0)) {
    return null; // No memory yet — clean display
  }

  return (
    <div className="mt-3 rounded-lg border border-zinc-700/50 bg-zinc-800/30 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Brain className="h-3.5 w-3.5 text-purple-400" />
        <span className="text-xs font-medium text-zinc-300">
          Past Lessons ({memory.totalReflections} reflections)
        </span>
      </div>

      {memory.reflections.slice(0, 3).map((r, i) => (
        <div key={i} className="mb-2 border-b border-zinc-700/30 pb-2 last:border-0 last:pb-0">
          <div className="mb-1 flex items-center gap-2 text-xs">
            <span className="text-zinc-400">{r.date}</span>
            <span className="font-medium text-zinc-300">{r.suggestion}</span>
            <span className={r.raw_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}>
              {r.raw_return_pct >= 0 ? '+' : ''}{r.raw_return_pct}%
            </span>
          </div>
          <p className="text-xs leading-relaxed text-zinc-400">
            {r.reflection?.substring(0, 180)}
            {(r.reflection?.length || 0) > 180 ? '...' : ''}
          </p>
        </div>
      ))}

      {memory.lessons.length > 0 && (
        <div className="mt-2 border-t border-zinc-700/30 pt-2">
          <div className="mb-1 flex items-center gap-1.5">
            <Lightbulb className="h-3 w-3 text-amber-400" />
            <span className="text-xs text-zinc-400">Cross-ticker lessons</span>
          </div>
          {memory.lessons.slice(0, 2).map((l, i) => (
            <p key={i} className="text-xs text-zinc-500 mt-1">
              <span className="text-zinc-400">{l.ticker}:</span> {l.reflection?.substring(0, 120)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
