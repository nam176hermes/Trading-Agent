import Link from 'next/link';

interface TradingSubNavProps {
  currentPath: string;
}

const navItems = [
  { href: '/dashboard',             label: 'Hub' },
  { href: '/dashboard/signals',     label: 'Signals' },
  { href: '/dashboard/execution',   label: 'Execution' },
  { href: '/dashboard/portfolio',   label: 'Portfolio' },
  { href: '/dashboard/risk',        label: 'Risk' },
  { href: '/dashboard/history',     label: 'History' },
  { href: '/dashboard/performance', label: 'Performance' },
  { href: '/dashboard/plan',        label: 'Plan' },
  { href: '/dashboard/settings',    label: 'Settings' },
];

export function TradingSubNav({ currentPath }: TradingSubNavProps) {
  return (
    <div className="border-b border-zinc-800 bg-zinc-950 px-4">
      <nav className="flex">
        {navItems.map((item) => {
          const isActive =
            currentPath === item.href ||
            currentPath.startsWith(item.href + '/');

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`border-b-2 px-3 py-2.5 text-[10px] font-medium uppercase tracking-widest transition-colors whitespace-nowrap ${
                isActive
                  ? 'border-amber-500 text-amber-400'
                  : 'border-transparent text-zinc-600 hover:text-zinc-300 hover:border-zinc-600'
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
