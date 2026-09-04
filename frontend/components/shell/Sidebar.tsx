"use client";

import Link from "next/link";
import NavLink from "@/components/shell/NavLink";
import NavSection from "@/components/shell/NavSection";
import ThemeToggle from "@/components/shell/ThemeToggle";
import { BrandMark } from "@/components/icons";
import { BRAND_NAME } from "@/lib/appConfig";
import { ADMIN_SECTION, FOOTER_ITEMS, HOME_ITEM, NAV_SECTIONS } from "@/lib/navigation";

/** Presentational: all state (collapsed, mobile open) lives in AppShell so the
 *  sidebar can be rendered in either context without owning layout concerns. */
export default function Sidebar({
  mobileOpen,
  onNavigate,
  isAdmin,
}: {
  mobileOpen: boolean;
  onNavigate: () => void;
  isAdmin: boolean;
}) {
  const sections = isAdmin ? [...NAV_SECTIONS, ADMIN_SECTION] : NAV_SECTIONS;

  return (
    <aside
      className={`sidebar${mobileOpen ? " mobile-open" : ""}`}
      aria-label="Platform navigation"
    >
      <Link href="/" className="sidebar-brand">
        <BrandMark size={22} />
        <span>{BRAND_NAME}</span>
      </Link>

      <nav className="sidebar-nav">
        <NavLink item={HOME_ITEM} level="top" onNavigate={onNavigate} />

        {sections.map((section) => (
          <NavSection key={section.id} section={section} onNavigate={onNavigate} />
        ))}

        {FOOTER_ITEMS.map((item) => (
          <NavLink key={item.label} item={item} level="top" onNavigate={onNavigate} />
        ))}
      </nav>

      <div className="sidebar-footer">
        <ThemeToggle />
      </div>
    </aside>
  );
}
