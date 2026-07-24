"use client";

import { useState, createContext } from "react";
import { Sidebar } from "@/components/sidebar";
import { MobileBottomNav } from "@/components/MobileBottomNav";
import { Menu } from "lucide-react";

type LayoutCtx = { closeMobileDrawer: () => void };
export const LayoutContext = createContext<LayoutCtx>({
  closeMobileDrawer: () => {},
});

export function AppLayout({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <LayoutContext.Provider
      value={{ closeMobileDrawer: () => setDrawerOpen(false) }}
    >
      <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
        {/* Desktop sidebar */}
        <div className="hidden md:flex">
          <Sidebar />
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
          <Sidebar onNavigate={() => setDrawerOpen(false)} />
        </div>

        {/* Main content */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          {/* Mobile top bar */}
          <div className="flex md:hidden items-center justify-between px-4 h-12 border-b border-zinc-800 bg-zinc-950 shrink-0">
            <button
              onClick={() => setDrawerOpen(true)}
              className="p-2 hover:bg-zinc-900 rounded-xl transition-colors"
            >
              <Menu className="w-5 h-5" />
            </button>
            <span className="text-sm font-semibold tracking-tight">
              2nd Brain
            </span>
            <div className="w-9" /> {/* spacer */}
          </div>

          {/* Page content — add bottom padding on mobile for the nav bar */}
          <div className="flex-1 flex flex-col overflow-y-auto pb-16 md:pb-0">
            {children}
          </div>
        </div>

        {/* Mobile bottom nav */}
        <MobileBottomNav />
      </div>
    </LayoutContext.Provider>
  );
}
