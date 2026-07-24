"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/dashboard", label: "Command Center" },
  { href: "/dashboard/signals", label: "Signals" },
  { href: "/dashboard/risk", label: "Risk" },
  { href: "/dashboard/history", label: "History" },
  { href: "/dashboard/plan", label: "Plan" },
];

export function TradingNav() {
  const pathname = usePathname();

  return (
    <nav className="mb-6 flex gap-1 rounded-lg border border-zinc-800 bg-zinc-900/50 p-1">
      {links.map((link) => {
        const active = pathname === link.href;
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${
              active
                ? "bg-zinc-700 text-zinc-100"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
