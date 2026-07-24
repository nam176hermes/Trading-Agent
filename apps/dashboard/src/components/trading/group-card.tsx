import Link from 'next/link';
import type React from 'react';
import { ArrowRight } from 'lucide-react';

export function GroupCard({
  icon: Icon,
  iconColor,
  title,
  subtitle,
  href,
  metrics,
  children,
}: {
  icon: React.ElementType;
  iconColor: string;
  iconBg: string;
  title: string;
  subtitle: string;
  href: string;
  metrics: { label: string; value: string | number; color?: string }[];
  children?: React.ReactNode;
}) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-3 py-2.5 border-b border-zinc-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className={`h-3.5 w-3.5 shrink-0 ${iconColor}`} />
          <div>
            <h3 className="text-[10px] font-bold text-zinc-200 uppercase tracking-widest leading-tight">
              {title}
            </h3>
            <p className="text-[9px] text-zinc-600 uppercase tracking-wider leading-tight mt-0.5">
              {subtitle}
            </p>
          </div>
        </div>
        <Link
          href={href}
          className="flex items-center gap-1 text-[9px] text-zinc-600 hover:text-amber-400 uppercase tracking-widest transition-colors"
        >
          OPEN <ArrowRight className="h-2.5 w-2.5" />
        </Link>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-4 divide-x divide-zinc-800 border-b border-zinc-800">
        {metrics.map(m => (
          <div key={m.label} className="px-2 py-2.5 text-center">
            <p className="text-[8px] text-zinc-600 uppercase tracking-widest mb-1 leading-none">
              {m.label}
            </p>
            <p className={`text-sm font-bold font-mono leading-none ${m.color || 'text-zinc-200'}`}>
              {m.value}
            </p>
          </div>
        ))}
      </div>

      {/* Optional body */}
      {children && (
        <div className="p-3">
          {children}
        </div>
      )}
    </div>
  );
}
