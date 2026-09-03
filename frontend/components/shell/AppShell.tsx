"use client";

import { useEffect, useState } from "react";
import Sidebar from "@/components/shell/Sidebar";
import SidebarRail from "@/components/shell/SidebarRail";
import TopBar from "@/components/shell/TopBar";
import { ChevronLeft } from "@/components/icons";
import { ToastProvider } from "@/lib/useToast";

const COLLAPSE_KEY = "sidebar-collapsed";

/** Owns the two pieces of layout state the shell has: the desktop collapse —
 *  which swaps the full sidebar for an icon rail — and the mobile drawer.
 *  Children are server-rendered pages passed straight through. */
export default function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Read after mount: localStorage is not available while rendering on the server.
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "true");
    } catch {
      // Blocked storage: the sidebar simply starts expanded.
    }
  }, []);

  function setCollapsedPersisted(next: boolean) {
    setCollapsed(next);
    try {
      localStorage.setItem(COLLAPSE_KEY, String(next));
    } catch {
      // Ignore: collapse still works for this session.
    }
  }

  return (
    <ToastProvider>
      <div className={`shell${collapsed ? " collapsed" : ""}`}>
        {collapsed ? (
          <SidebarRail onExpand={() => setCollapsedPersisted(false)} />
        ) : null}

        <Sidebar mobileOpen={mobileOpen} onNavigate={() => setMobileOpen(false)} />

        {mobileOpen ? (
          <div className="shell-scrim" onClick={() => setMobileOpen(false)} aria-hidden="true" />
        ) : null}

        <button
          type="button"
          className="sidebar-toggle"
          onClick={() => setCollapsedPersisted(!collapsed)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        >
          <ChevronLeft size={14} />
        </button>

        <div className="shell-main">
          <TopBar onOpenNav={() => setMobileOpen(true)} />
          <div className="shell-content">{children}</div>
        </div>
      </div>
    </ToastProvider>
  );
}
