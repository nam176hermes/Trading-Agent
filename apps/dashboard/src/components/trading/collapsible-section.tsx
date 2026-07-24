'use client';

import { useState } from 'react';

interface Props {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function CollapsibleSection({ title, defaultOpen = false, children }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border border-zinc-800 bg-zinc-900">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-zinc-800/40 transition-colors"
      >
        <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">
          {title}
        </span>
        <span className={`text-zinc-600 text-[10px] transition-transform ${open ? 'rotate-90' : ''}`}>
          ▶
        </span>
      </button>
      {open && <div className="border-t border-zinc-800 p-3">{children}</div>}
    </div>
  );
}
