"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { NavItem } from "@/lib/navigation";

/** One row in the sidebar. With an href it is a link that knows when it is the
 *  current page; without one it renders as unavailable and carries the reason
 *  rather than pretending to navigate.
 *
 *  `top` rows sit at the root of the tree (Home, All Chats) and take the same
 *  uppercase treatment as a section heading. `nested` rows hang off a section's
 *  rail. */
export default function NavLink({
  item,
  level = "nested",
  onNavigate,
}: {
  item: NavItem;
  level?: "top" | "nested";
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const className = `nav-link ${level === "top" ? "top" : "railed"}`;

  if (!item.href) {
    return (
      <span className={`${className} unavailable`} aria-disabled="true" title={item.reason}>
        {item.label}
      </span>
    );
  }

  const active = pathname === item.href || pathname.startsWith(`${item.href}/`);

  return (
    <Link
      href={item.href}
      className={`${className}${active ? " active" : ""}`}
      aria-current={active ? "page" : undefined}
      onClick={onNavigate}
    >
      {item.label}
    </Link>
  );
}
