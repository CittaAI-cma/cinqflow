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
  LogoutIcon,
  MenuIcon,
} from "@/components/icons";
import { signOut } from "@/app/logout/actions";
import { BRAND_NAME } from "@/lib/appConfig";
import type { CurrentUser } from "@/lib/auth";
import { TOP_NAV, breadcrumbsFor, type TopNavIcon } from "@/lib/navigation";

function initialsOf(user: CurrentUser): string {
  const source = user.display_name?.trim() || user.email;
  const parts = source.split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? [parts[0][0], parts[parts.length - 1][0]] : [source[0]];
  return letters.join("").toUpperCase();
}

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
export default function TopBar({
  onOpenNav,
  user,
}: {
  onOpenNav: () => void;
  user: CurrentUser | null;
}) {
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
        {user ? (
          <span className="account-menu">
            <span className="account-chip" title={user.email}>
              {initialsOf(user)}
            </span>
            <span className="account-name">{user.display_name}</span>
            <form action={signOut}>
              <button type="submit" className="account-signout" title="Sign out">
                <LogoutIcon size={16} />
              </button>
            </form>
          </span>
        ) : null}
      </div>
    </header>
  );
}
