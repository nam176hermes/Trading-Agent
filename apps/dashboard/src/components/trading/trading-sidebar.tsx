"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  TrendingUp,
  Shield,
  History,
  Zap,
  ClipboardList,
  Settings,
  BarChart3,
  Briefcase,
  Activity,
} from "lucide-react";
import { HelpModal } from "@/components/trading/help-modal";
import { useOperatorState } from "@/components/trading/operator-state-provider";

const navItems = [
  { icon: LayoutDashboard, label: "Hub",         href: "/dashboard" },
  { icon: TrendingUp,      label: "Signals",     href: "/dashboard/signals" },
  { icon: Zap,             label: "Execution",   href: "/dashboard/execution" },
  { icon: Briefcase,       label: "Portfolio",   href: "/dashboard/portfolio" },
  { icon: Shield,          label: "Risk",        href: "/dashboard/risk" },
  { icon: History,         label: "History",     href: "/dashboard/history" },
  { icon: BarChart3,       label: "Performance", href: "/dashboard/performance" },
  { icon: ClipboardList,   label: "Plan",        href: "/dashboard/plan" },
  { icon: Settings,        label: "Settings",    href: "/dashboard/settings" },
];

function LiveClock() {
  const [utc, setUtc] = useState("--:--:--");
  const [date, setDate] = useState("");

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setUtc(now.toISOString().slice(11, 19));
      setDate(now.toUTCString().slice(5, 11));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center justify-between">
      <span className="text-[9px] text-zinc-600 uppercase tracking-widest">{date} UTC</span>
      <span className="font-mono text-[10px] text-zinc-400 tabular-nums">{utc}</span>
    </div>
  );
}

export function TradingSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const operator = useOperatorState();
  const operatorStatus = operator.availability === 'LOADING'
    ? { label: 'STATE LOADING', color: 'text-amber-400', dot: 'bg-amber-500 animate-pulse' }
    : operator.availability === 'UNAVAILABLE'
      ? { label: 'STATE UNAVAILABLE', color: 'text-red-400', dot: 'bg-red-500' }
      : operator.killSwitchState === 'ACTIVE'
        ? { label: 'TRADING HALTED', color: 'text-red-400', dot: 'bg-red-500' }
        : { label: 'CANONICAL AVAILABLE', color: 'text-blue-400', dot: 'bg-blue-500' };

  return (
    <div className="w-52 border-r border-zinc-800 bg-zinc-950 flex flex-col h-screen">
      {/* Logo */}
      <div className="px-4 py-3 border-b border-zinc-800 flex items-center gap-2.5 shrink-0">
        <div className="w-7 h-7 bg-amber-400 flex items-center justify-center shrink-0">
          <Activity className="w-3.5 h-3.5 text-black" />
        </div>
        <div>
          <div className="text-[11px] font-bold tracking-widest uppercase text-amber-400 leading-tight">
            HERMES
          </div>
          <div className="text-[9px] text-zinc-600 uppercase tracking-widest leading-tight">
            TRADING SYSTEM
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-1 overflow-y-auto">
        {navItems.map((item) => {
          const active =
            pathname === item.href || pathname.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`flex items-center gap-2.5 px-3 py-2.5 transition-colors text-[10px] uppercase tracking-widest border-l-2 ${
                active
                  ? "border-l-amber-500 bg-amber-500/10 text-amber-400"
                  : "border-l-transparent hover:bg-zinc-900 text-zinc-500 hover:text-zinc-200"
              }`}
            >
              <item.icon className="w-3.5 h-3.5 shrink-0" />
              <span className="flex-1">{item.label}</span>
              {active && (
                <span className="text-amber-600 text-[8px] shrink-0">▶</span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Status footer */}
      <div className="border-t border-zinc-800 px-4 py-3 shrink-0 space-y-2">
        <div className="flex items-center justify-between">
          <div className={`flex items-center gap-2 text-[9px] uppercase tracking-widest ${operatorStatus.color}`}>
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${operatorStatus.dot}`} />
            {operatorStatus.label}
          </div>
          <span className="text-[9px] text-zinc-500 uppercase tracking-widest">{operator.mode}</span>
        </div>
        <div className="border-t border-zinc-800/60 pt-2">
          <LiveClock />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-[9px] text-zinc-700 uppercase tracking-widest">V0.2.0</span>
          <HelpModal />
        </div>
      </div>
    </div>
  );
}
