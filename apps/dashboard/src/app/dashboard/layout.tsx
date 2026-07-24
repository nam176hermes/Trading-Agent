'use client';

import { useState } from "react";
import { TradingSidebar } from "@/components/trading/trading-sidebar";
import { AuthGuard } from "@/components/trading/auth-guard";
import { PriceTicker } from "@/components/trading/price-ticker";
import { OperatorStateBanner } from "@/components/trading/operator-state-banner";
import { OperatorStateProvider } from "@/components/trading/operator-state-provider";
import { Menu } from "lucide-react";

export default function TradingDashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <AuthGuard>
      <OperatorStateProvider>
        <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
        {/* Desktop sidebar */}
        <div className="hidden md:flex">
          <TradingSidebar />
        </div>

        {/* Mobile drawer overlay */}
        {drawerOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/60 md:hidden"
            onClick={() => setDrawerOpen(false)}
          />
        )}

        {/* Mobile drawer panel */}
        <div
          className={`fixed top-0 left-0 h-full z-50 md:hidden transition-transform duration-300 ${
            drawerOpen ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <TradingSidebar onNavigate={() => setDrawerOpen(false)} />
        </div>

        {/* Main content */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          {/* Mobile top bar */}
          <div className="flex md:hidden items-center justify-between px-4 h-11 border-b border-zinc-800 bg-zinc-950 shrink-0">
            <button
              onClick={() => setDrawerOpen(true)}
              className="p-2 hover:bg-zinc-900 transition-colors"
            >
              <Menu className="w-4 h-4 text-zinc-400" />
            </button>
            <span className="text-[11px] font-bold tracking-widest uppercase text-amber-400">
              HERMES
            </span>
            <div className="w-8" />
          </div>

          {/* Page content */}
          <div className="flex-1 flex flex-col overflow-y-auto pb-16 md:pb-0">
            <PriceTicker />
            <OperatorStateBanner />
            {children}
          </div>
        </div>
        </div>
      </OperatorStateProvider>
    </AuthGuard>
  );
}
