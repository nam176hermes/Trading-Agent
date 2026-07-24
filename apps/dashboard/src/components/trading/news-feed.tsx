'use client';

import { useEffect, useState, useMemo } from 'react';
import type { NewsReport } from '@/lib/trading/types';

interface Props {
  collapsed?: boolean;
}

function sentimentBadge(score: number) {
  if (score > 0.3) return { label: 'POS', bg: 'bg-green-500/20', text: 'text-green-400' };
  if (score < -0.3) return { label: 'NEG', bg: 'bg-red-500/20', text: 'text-red-400' };
  return { label: 'NEU', bg: 'bg-zinc-500/20', text: 'text-zinc-400' };
}

function timeAgo(dateStr: string): string {
  try {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return '';
  }
}

export function NewsFeed({ collapsed = false }: Props) {
  const [report, setReport] = useState<NewsReport | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(collapsed);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/trading/news')
      .then(r => r.json())
      .then(d => { if (!d.error) setReport(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const symbols = useMemo(() => {
    if (!report) return [];
    return Object.keys(report.sentiment_summary).sort();
  }, [report]);

  const filteredArticles = useMemo(() => {
    if (!report) return [];
    if (!filter) return report.articles;
    return report.articles.filter(a => a.symbol.toUpperCase() === filter.toUpperCase());
  }, [report, filter]);

  if (loading) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 animate-pulse">
        <div className="h-4 w-24 bg-zinc-800 rounded mb-3" />
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-12 bg-zinc-800 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (!report || report.articles.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-xs text-zinc-500">No news data available. Run news_collector.py to collect.</p>
      </div>
    );
  }

  const summary = report.sentiment_summary;

  return (
    <div className="space-y-3">
      {!collapsed && (
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className="w-full flex items-center justify-between px-0 py-0 text-left"
        >
          <h3 className="text-sm font-bold text-zinc-200">News Feed</h3>
          <span className={`text-zinc-400 text-xs transition-transform ${isCollapsed ? '' : 'rotate-90'}`}>▶</span>
        </button>
      )}

      {!isCollapsed && (
        <>
          {/* Sentiment Summary Bar */}
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setFilter(null)}
              className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
                filter === null
                  ? 'bg-zinc-600 text-zinc-100'
                  : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'
              }`}
            >
              ALL ({report.articles.length})
            </button>
            {symbols.map(sym => {
              const s = summary[sym];
              if (!s) return null;
              const avg = s.avg_score;
              const color = avg > 0.15 ? 'text-green-400' : avg < -0.15 ? 'text-red-400' : 'text-zinc-400';
              return (
                <button
                  key={sym}
                  onClick={() => setFilter(filter === sym ? null : sym)}
                  className={`rounded px-2 py-1 text-[10px] font-mono transition-colors ${
                    filter === sym
                      ? 'bg-zinc-600 text-zinc-100'
                      : 'bg-zinc-800 text-zinc-300 hover:text-zinc-100'
                  }`}
                >
                  {sym} <span className={color}>{avg >= 0 ? '+' : ''}{Number.isFinite(Number(avg)) ? Number(avg).toFixed(2) : '—'}</span>
                </button>
              );
            })}
          </div>

          {/* Articles Feed */}
          <div className="max-h-[400px] overflow-y-auto space-y-1.5">
            {filteredArticles.slice(0, 50).map((article, i) => {
              const badge = sentimentBadge(article.sentiment_score);
              return (
                <a
                  key={i}
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded border border-zinc-800 bg-zinc-900/30 p-2.5 hover:border-zinc-700 hover:bg-zinc-900/50 transition-colors"
                >
                  <div className="flex items-start gap-2">
                    <div className="flex gap-1.5 shrink-0 mt-0.5">
                      {filter === null && (
                        <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-mono text-zinc-300">
                          {article.symbol}
                        </span>
                      )}
                      <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${badge.bg} ${badge.text}`}>
                        {badge.label}
                      </span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-zinc-200 leading-relaxed line-clamp-2">
                        {article.title}
                      </p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-[9px] text-zinc-500">{article.source}</span>
                        <span className="text-[9px] text-zinc-600">{timeAgo(article.published_at)}</span>
                        {article.sentiment_score !== 0 && (
                          <span className={`text-[9px] font-mono ${
                            article.sentiment_score > 0.3 ? 'text-green-400' :
                            article.sentiment_score < -0.3 ? 'text-red-400' : 'text-zinc-500'
                          }`}>
                            {article.sentiment_score >= 0 ? '+' : ''}{Number.isFinite(Number(article.sentiment_score)) ? Number(article.sentiment_score).toFixed(2) : '—'}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </a>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
