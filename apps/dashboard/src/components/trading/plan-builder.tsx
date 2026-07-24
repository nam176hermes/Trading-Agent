'use client';

import { useState } from 'react';
import { ResearchPlan } from '@/lib/trading/types';

const KEYWORDS = [
  'Technical',
  'Fundamental',
  'Sentiment',
  'On-chain',
  'Risk',
  'Debate',
];

export function PlanBuilder() {
  const [query, setQuery] = useState('');
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>([]);
  const [plan, setPlan] = useState<ResearchPlan | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleKeyword = (keyword: string) => {
    setSelectedKeywords((prev) =>
      prev.includes(keyword)
        ? prev.filter((k) => k !== keyword)
        : [...prev, keyword]
    );
  };

  const generatePlan = async () => {
    if (!query.trim()) {
      setError('Please enter a research query');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/trading/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, keywords: selectedKeywords }),
      });

      if (!response.ok) {
        throw new Error('Failed to generate plan');
      }

      const generatedPlan = await response.json();
      setPlan(generatedPlan);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate plan');
    } finally {
      setIsLoading(false);
    }
  };

  const executePlan = () => {
    // In a real implementation, this would trigger the CLI
    alert('Plan execution would trigger the CLI research pipeline');
  };

  return (
    <div className="space-y-6">
      <div>
        <label className="mb-2 block text-sm font-medium text-zinc-300">
          What do you want to research?
        </label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g., Analyze BTC and ETH for potential entry points..."
          className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-4 py-3 text-zinc-100 placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
          rows={3}
        />
      </div>

      <div>
        <label className="mb-2 block text-sm font-medium text-zinc-300">
          Focus Areas
        </label>
        <div className="flex flex-wrap gap-2">
          {KEYWORDS.map((keyword) => (
            <button
              key={keyword}
              onClick={() => toggleKeyword(keyword)}
              className={`rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
                selectedKeywords.includes(keyword)
                  ? 'border-blue-500 bg-blue-500/20 text-blue-400'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-400 hover:border-zinc-600'
              }`}
            >
              {keyword}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={generatePlan}
        disabled={isLoading}
        className="w-full rounded-md bg-blue-600 px-4 py-2 font-medium text-zinc-100 transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isLoading ? 'Generating...' : 'Generate Research Plan'}
      </button>

      {error && (
        <div className="rounded-md border border-red-500/20 bg-red-500/10 p-3">
          <p className="text-sm text-red-400">{error}</p>
        </div>
      )}

      {plan && (
        <div className="rounded-md border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="mb-4">
            <h3 className="text-lg font-bold text-zinc-100">Research Plan</h3>
            <p className="text-sm text-zinc-400">{plan.query}</p>
          </div>

          <div className="mb-4">
            <h4 className="mb-2 text-sm font-medium text-zinc-300">Steps</h4>
            <div className="space-y-2">
              {plan.steps.map((step, idx) => (
                <div
                  key={step.id}
                  className="flex items-start gap-3 rounded-md border border-zinc-800 bg-zinc-800/30 p-3"
                >
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-700 text-xs font-bold text-zinc-100">
                    {idx + 1}
                  </div>
                  <div className="flex-1">
                    <h5 className="text-sm font-medium text-zinc-100">{step.title}</h5>
                    <p className="text-xs text-zinc-400">{step.description}</p>
                    {step.dependencies.length > 0 && (
                      <p className="mt-1 text-xs text-zinc-500">
                        Depends on: {step.dependencies.join(', ')}
                      </p>
                    )}
                  </div>
                  <div className="text-xs">
                    <span
                      className={`rounded px-2 py-1 ${
                        step.status === 'completed'
                          ? 'bg-green-500/20 text-green-400'
                          : step.status === 'in_progress'
                            ? 'bg-blue-500/20 text-blue-400'
                            : 'bg-zinc-700 text-zinc-400'
                      }`}
                    >
                      {step.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <button
            onClick={executePlan}
            className="w-full rounded-md bg-green-600 px-4 py-2 font-medium text-zinc-100 transition-colors hover:bg-green-700"
          >
            Execute Plan (CLI)
          </button>
        </div>
      )}
    </div>
  );
}
