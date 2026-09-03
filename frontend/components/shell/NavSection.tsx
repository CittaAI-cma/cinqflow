"use client";

import { useId, useState } from "react";
import NavLink from "@/components/shell/NavLink";
import { ChevronDown } from "@/components/icons";
import type { NavSectionSpec } from "@/lib/navigation";

/** A collapsible group of nav rows. Open state is local to the group so two
 *  sections can be open at once — this is a tree, not an accordion. */
export default function NavSection({
  section,
  onNavigate,
}: {
  section: NavSectionSpec;
  onNavigate?: () => void;
}) {
  const [open, setOpen] = useState(section.defaultOpen ?? true);
  const listId = useId();

  return (
    <div className="nav-section">
      <button
        type="button"
        className="nav-section-header"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={listId}
      >
        <span>{section.label}</span>
        <ChevronDown size={15} className={`nav-chevron${open ? "" : " closed"}`} />
      </button>
      <div id={listId} className="nav-section-items" hidden={!open}>
        {section.items.map((item) => (
          <NavLink key={item.label} item={item} onNavigate={onNavigate} />
        ))}
      </div>
    </div>
  );
}
