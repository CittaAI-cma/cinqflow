"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { DotsVerticalIcon } from "@/components/icons";

export interface RowMenuItem {
  label: string;
  href?: string;
  /** Present when the action has no backing; the row renders it inert. */
  reason?: string;
  danger?: boolean;
}

interface Anchor {
  top: number;
  right: number;
}

/** The ⋮ row menu. The panel is portalled to the body and positioned from the
 *  trigger's rect, because the table it lives in is a scroll container and
 *  would otherwise clip it. Items without an href state why they are inert. */
export default function RowMenu({ items, label }: { items: RowMenuItem[]; label: string }) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const open = anchor !== null;

  function toggle() {
    if (open) {
      setAnchor(null);
      return;
    }
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setAnchor({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
  }

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setAnchor(null);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setAnchor(null);
    }
    // A fixed panel would drift away from its row, so close instead of chasing it.
    function onScrollOrResize() {
      setAnchor(null);
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [open]);

  return (
    <div className="row-menu">
      <button
        ref={triggerRef}
        type="button"
        className="icon-action neutral"
        onClick={toggle}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
      >
        <DotsVerticalIcon size={16} />
      </button>

      {anchor
        ? createPortal(
            <div
              ref={panelRef}
              className="row-menu-panel"
              role="menu"
              style={{ top: anchor.top, right: anchor.right }}
            >
              {items.map((item) =>
                item.href ? (
                  <Link
                    key={item.label}
                    href={item.href}
                    role="menuitem"
                    className={`row-menu-item${item.danger ? " danger" : ""}`}
                    onClick={() => setAnchor(null)}
                  >
                    {item.label}
                  </Link>
                ) : (
                  <span
                    key={item.label}
                    role="menuitem"
                    aria-disabled="true"
                    className="row-menu-item disabled"
                    title={item.reason}
                  >
                    {item.label}
                  </span>
                ),
              )}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
