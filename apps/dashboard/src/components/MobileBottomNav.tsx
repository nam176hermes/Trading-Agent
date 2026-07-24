"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Brain,
  CheckSquare,
  Star,
  Lightbulb,
} from "lucide-react";

const NAV = [
  { href: "/dashboard", icon: LayoutDashboard, label: "Home" },
  { href: "/memories", icon: Brain, label: "Memory" },
  { href: "/tasks", icon: CheckSquare, label: "Tasks" },
  { href: "/reviews", icon: Star, label: "Reviews" },
  { href: "/research", icon: Lightbulb, label: "Research" },
];

export function MobileBottomNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-30 md:hidden bg-zinc-950 border-t border-zinc-800">
      <div className="flex items-center justify-around px-2 py-2 safe-area-inset-bottom">
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex flex-col items-center gap-0.5 px-3 py-2 rounded-2xl transition-all min-w-0 ${
                active
                  ? "text-white"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              <div
                className={`p-1.5 rounded-xl transition-colors ${
                  active ? "bg-violet-600" : ""
                }`}
              >
                <item.icon className="w-5 h-5" />
              </div>
              <span className="text-[10px] truncate">{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
