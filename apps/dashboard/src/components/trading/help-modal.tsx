'use client';

import { useState, useEffect, useCallback } from 'react';
import { HelpCircle, X } from 'lucide-react';

function renderMarkdown(text: string) {
  const lines = text.split('\n');
  const elements: React.ReactNode[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const key = `l${i}`;

    if (line.startsWith('### ')) {
      elements.push(
        <h3 key={key} className="text-base font-semibold text-zinc-200 mt-4 mb-1">
          {boldText(line.slice(4))}
        </h3>
      );
    } else if (line.startsWith('## ')) {
      elements.push(
        <h2 key={key} className="text-lg font-bold text-zinc-100 mt-4 mb-2">
          {boldText(line.slice(3))}
        </h2>
      );
    } else if (line.startsWith('```')) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      if (codeLines.length > 0) {
        elements.push(
          <pre key={key} className="rounded bg-zinc-800 p-3 text-xs font-mono text-zinc-300 mb-3 overflow-x-auto">
            {codeLines.join('\n')}
          </pre>
        );
      }
    } else if (line.trim() === '') {
      if (elements.length > 0 && lastIsParagraph(elements)) {
        elements.push(<div key={key} className="mb-3" />);
      }
    } else if (line.startsWith('- ')) {
      elements.push(
        <li key={key} className="text-sm text-zinc-300 ml-4 list-disc">
          {boldText(line.slice(2))}
        </li>
      );
    } else if (line.startsWith('| ')) {
      // Skip table rows — display as pre
      elements.push(
        <p key={key} className="text-xs text-zinc-400 font-mono mb-1">
          {line}
        </p>
      );
    } else {
      elements.push(
        <p key={key} className="text-sm text-zinc-300 leading-relaxed mb-3">
          {boldText(line)}
        </p>
      );
    }
  }

  return elements;
}

function lastIsParagraph(elements: React.ReactNode[]): boolean {
  return elements.length > 0;
}

function boldText(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} className="font-bold text-zinc-100">{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

export function HelpModal() {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState('');

  useEffect(() => {
    fetch('/api/trading/bootstrap')
      .then(r => r.json())
      .then(d => setContent(d.content))
      .catch(() => setContent('# System overview unavailable'));
  }, []);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') close();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, close]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-8 h-8 rounded-full bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 flex items-center justify-center transition-colors"
        aria-label="Help"
      >
        <HelpCircle className="w-4 h-4 text-zinc-400" />
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
          onClick={(e) => { if (e.target === e.currentTarget) close(); }}
        >
          <div className="max-w-2xl w-full bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-h-[80vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold text-zinc-100">Trading System Overview</h2>
              <button
                onClick={close}
                className="w-8 h-8 rounded-full bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 flex items-center justify-center transition-colors"
                aria-label="Close"
              >
                <X className="w-4 h-4 text-zinc-400" />
              </button>
            </div>
            <div>{renderMarkdown(content)}</div>
          </div>
        </div>
      )}
    </>
  );
}
