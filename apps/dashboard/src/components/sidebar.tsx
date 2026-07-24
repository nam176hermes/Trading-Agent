"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import {
  LayoutDashboard,
  Brain,
  CheckSquare,
  Star,
  Search,
  Settings,
  Plus,
  Edit3,
  PlayCircle,
  Lightbulb,
  FolderKanban,
  Box,
  Server,
  Calendar,
  Shield,
  Puzzle,
  LineChart,
} from "lucide-react";

const navItems = [
  { icon: LayoutDashboard, label: "Dashboard", href: "/dashboard", shortcut: "1" },
  { icon: Shield, label: "Guardrails", href: "/guardrails", shortcut: "0" },
  { icon: Brain, label: "Memories", href: "/memories", shortcut: "2" },
  { icon: CheckSquare, label: "Tasks", href: "/tasks", shortcut: "3" },
  { icon: Star, label: "Reviews", href: "/reviews", shortcut: "4" },
  { icon: Calendar, label: "Daily Note", href: "/daily-note", shortcut: "8" },
  { icon: Search, label: "Search", href: "/search", shortcut: "K" },
  { icon: Lightbulb, label: "Research", href: "/research", shortcut: "5" },
  { icon: FolderKanban, label: "Projects", href: "/projects", shortcut: "6" },
  { icon: Box, label: "Agent Bank", href: "/agent-bank", shortcut: "7" },
  { icon: Server, label: "MCP Explorer", href: "/mcp-explorer", shortcut: "9" },
  { icon: Puzzle, label: "Skills", href: "/skills", shortcut: "s" },
  { icon: LineChart, label: "Trading", href: "/dashboard", shortcut: "" },
  { icon: Settings, label: "Settings", href: "/settings", shortcut: "" },
];

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <div className="w-72 border-r border-zinc-800 bg-zinc-950 flex flex-col h-screen">
      {/* Logo */}
      <div className="p-6 border-b border-zinc-800 flex items-center gap-3 shrink-0">
        <div className="w-9 h-9 bg-violet-600 rounded-2xl flex items-center justify-center">
          <Brain className="w-5 h-5 text-white" />
        </div>
        <div>
          <div className="font-semibold tracking-tight">2nd Brain</div>
          <div className="text-xs text-zinc-500 -mt-0.5">OpenClaw</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
        {navItems.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={`flex items-center gap-3 px-4 py-3 rounded-2xl transition-all group ${
                active
                  ? "bg-violet-600 text-white"
                  : "hover:bg-zinc-900 text-zinc-400 hover:text-white"
              }`}
            >
              <item.icon className="w-5 h-5 shrink-0" />
              <span className="flex-1 text-sm">{item.label}</span>
              {item.shortcut && (
                <kbd className="hidden lg:block text-[10px] font-mono px-1.5 py-px bg-zinc-900/50 border border-zinc-700 rounded text-zinc-400">
                  {item.shortcut}
                </kbd>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Quick Actions */}
      <div className="p-4 border-t border-zinc-800 space-y-2 shrink-0">
        <Link
          href="/dashboard#quick-capture"
          onClick={onNavigate}
          className="w-full flex items-center gap-3 px-4 py-3 bg-zinc-900 hover:bg-zinc-800 rounded-2xl text-sm transition-colors"
        >
          <Plus className="w-4 h-4" /> Quick Capture
        </Link>
        <Link
          href="/tasks"
          onClick={onNavigate}
          className="w-full flex items-center gap-3 px-4 py-3 bg-zinc-900 hover:bg-zinc-800 rounded-2xl text-sm transition-colors"
        >
          <Edit3 className="w-4 h-4" /> New Task
        </Link>
        <Link
          href="/daily-note"
          onClick={onNavigate}
          className="w-full flex items-center gap-3 px-4 py-3 bg-zinc-900 hover:bg-zinc-800 rounded-2xl text-sm transition-colors"
        >
          <PlayCircle className="w-4 h-4" /> Log Daily Note
        </Link>
      </div>

      {/* System Status */}
      <div className="p-4 border-t border-zinc-800 text-xs space-y-1 text-emerald-400 font-mono shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
          OpenClaw Online
        </div>
        <div>Hermes Online</div>
        <div>SQLite &bull; OK</div>
        <div className="text-zinc-500 pt-1 text-[10px]">v0.1.0 &bull; WSL Ubuntu</div>
      </div>
    </div>
  );
}
