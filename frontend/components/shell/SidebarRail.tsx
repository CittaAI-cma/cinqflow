"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BrandMark,
  LogoutIcon,
  PaletteIcon,
  RAIL_ICONS,
  SettingsIcon,
} from "@/components/icons";
import { BRAND_NAME } from "@/lib/appConfig";
import {
  FOOTER_ITEMS,
  HOME_ITEM,
  NAV_SECTIONS,
  RAIL_UTILITIES,
  activeSectionId,
} from "@/lib/navigation";
import { useTheme } from "@/lib/useTheme";

/** The collapsed sidebar: one glyph per section, an active marker on the section
 *  owning the current route, and the utilities pinned to the bottom. Clicking a
 *  section glyph expands the sidebar rather than navigating, because a section
 *  is not itself a destination. */
export default function SidebarRail({ onExpand }: { onExpand: () => void }) {
  const pathname = usePathname();
  const activeId = activeSectionId(pathname);
  const { theme, toggle } = useTheme();
  const HomeGlyph = RAIL_ICONS.home;
  const ChatGlyph = RAIL_ICONS.chat;
  const homeActive = pathname === "/";

  return (
    <nav className="rail" aria-label="Platform navigation (collapsed)">
      <Link href="/" className="rail-brand" aria-label={BRAND_NAME}>
        <BrandMark size={24} />
      </Link>

      <div className="rail-items">
        <Link
          href={HOME_ITEM.href!}
          className={`rail-button${homeActive ? " active" : ""}`}
          aria-current={homeActive ? "page" : undefined}
          title={HOME_ITEM.label}
        >
          <HomeGlyph size={19} />
        </Link>

        {NAV_SECTIONS.map((section) => {
          const Glyph = RAIL_ICONS[section.icon];
          const active = section.id === activeId;
          return (
            <button
              key={section.id}
              type="button"
              className={`rail-button${active ? " active" : ""}`}
              onClick={onExpand}
              title={`${section.label} — expand to choose`}
              aria-expanded={false}
            >
              <Glyph size={19} />
            </button>
          );
        })}

        {FOOTER_ITEMS.map((item) => (
          <span
            key={item.label}
            className="rail-button unavailable"
            aria-disabled="true"
            title={`${item.label} — ${item.reason}`}
          >
            <ChatGlyph size={19} />
          </span>
        ))}
      </div>

      <div className="rail-footer">
        <span
          className="rail-button unavailable"
          aria-disabled="true"
          title={`${RAIL_UTILITIES.settings.label} — ${RAIL_UTILITIES.settings.reason}`}
        >
          <SettingsIcon size={19} />
        </span>
        <button
          type="button"
          className="rail-button"
          onClick={toggle}
          title={`Theme — ${theme === "dark" ? "switch to light" : "switch to dark"}`}
        >
          <PaletteIcon size={19} />
        </button>
        <span
          className="rail-button unavailable"
          aria-disabled="true"
          title={`${RAIL_UTILITIES.logout.label} — ${RAIL_UTILITIES.logout.reason}`}
        >
          <LogoutIcon size={19} />
        </span>
      </div>
    </nav>
  );
}
