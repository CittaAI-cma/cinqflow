"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ActivityIcon,
  BrandMark,
  ChartIcon,
  ChevronRight,
  CompassIcon,
  DatabaseIcon,
  GridIcon,
  HomeIcon,
  MenuIcon,
} from "@/components/icons";
import { BRAND_NAME, USER_INITIALS } from "@/lib/appConfig";
import { TOP_NAV, breadcrumbsFor, type TopNavIcon } from "@/lib/navigation";

const TOP_NAV_ICONS: Record<TopNavIcon, typeof DatabaseIcon> = {
  catalog: DatabaseIcon,
  design: GridIcon,
  opshub: ActivityIcon,
  explore: CompassIcon,
  observability: ChartIcon,
};

/** Breadcrumb chrome for inner surfaces. On home there are no crumbs — the
 *  greeting is that page's own header — so the bar collapses to the mobile
 *  navigation trigger and hides itself entirely on wide screens. */
export default function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const pathname = usePathname();
  const crumbs = breadcrumbsFor(pathname);
  const bare = crumbs.length === 0;

  return (
    <header className={`topbar${bare ? " bare" : ""}`}>
      <button
        type="button"
        className="icon-button topbar-menu"
        onClick={onOpenNav}
        aria-label="Open navigation"
      >
        <MenuIcon size={18} />
      </button>

      {bare ? (
        <span className="topbar-brand">{BRAND_NAME}</span>
      ) : (
        <nav className="crumbs" aria-label="Breadcrumb">
          <Link href="/" className="crumb-home" aria-label="Home">
            <HomeIcon size={16} />
          </Link>
          {crumbs.map((crumb, index) => {
            // The last crumb is where you already are: a label, never a link.
            const isCurrent = index === crumbs.length - 1;
            return (
              <span key={crumb.label} className="crumb-group">
                <ChevronRight size={14} className="crumb-sep" />
                {crumb.href && !isCurrent ? (
                  <Link href={crumb.href} className="crumb link">
                    {crumb.label}
                  </Link>
                ) : (
                  <span className="crumb" aria-current={isCurrent ? "page" : undefined}>
                    {crumb.label}
                  </span>
                )}
              </span>
            );
          })}
        </nav>
      )}

      <div className="topbar-right">
        <nav className="product-nav" aria-label="Product areas">
          {TOP_NAV.map((item) => {
            const Icon = TOP_NAV_ICONS[item.icon];
            return (
              <span
                key={item.label}
                className="product-link"
                aria-disabled="true"
                title={`${item.label} — ${item.reason}`}
              >
                <Icon size={15} />
                {item.label}
              </span>
            );
          })}
        </nav>

        <span className="ask-ai" aria-disabled="true" title="Ask AI — not part of this build yet">
          <BrandMark size={18} />
          Ask AI
        </span>
        <span
          className="account-chip"
          title="Signed-in account (configured — there is no auth on this build)"
        >
          {USER_INITIALS}
        </span>
      </div>
    </header>
  );
}
