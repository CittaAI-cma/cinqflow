"use client";

import { useId, useState } from "react";
import { ChevronDown } from "@/components/icons";

export default function CollapsibleSection({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();

  return (
    <div className="collapsible">
      <button
        type="button"
        className="collapsible-header"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={bodyId}
      >
        <span>{title}</span>
        <ChevronDown size={16} className={`nav-chevron${open ? "" : " closed"}`} />
      </button>
      <div id={bodyId} className="collapsible-body" hidden={!open}>
        {children}
      </div>
    </div>
  );
}
