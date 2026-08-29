"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * THE drawer. The first client component in the app, and it earns that.
 *
 * ADR-0020: "depth is a drawer, never an IA branch." The routing half was
 * already right — a batch's URL IS its citation_id, so `recon:8842#DQ-002`
 * deep-links to the panel. What was missing is that the drawer was a full
 * PAGE: clicking a row threw away the list you were reading, and changing
 * panels was a full navigation. The decision had been implemented as routing
 * and never as experience.
 *
 * Paired with an intercepting route, so all three hold at once:
 *   · clicking a row overlays the drawer and keeps the list behind it;
 *   · the URL is still the citation, so it is still shareable — "look at
 *     recon:8842#DQ-002" instead of a screenshot in Slack;
 *   · pasting that URL cold renders the full page, because a shared link must
 *     not depend on how you arrived.
 *
 * Two mechanics that are not decoration:
 *
 * PORTAL. The drawer renders into <body>, not into the list it covers. It sits
 * inside `main` in the route tree, and a modal that lives inside the content it
 * is meant to supersede cannot make that content inert without making itself
 * inert too.
 *
 * INERT. While it is open, `main` and the navigation are removed from the
 * accessibility tree and from tab order. This is what makes the scrim honest:
 * dimmed text behind a modal fails contrast, and axe is right to say so — the
 * fix is for that text not to be exposed at all, rather than for the scrim to
 * be lighter. The browser then enforces the focus trap natively; the Tab
 * handler below stays as a fallback for older engines.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** Everything the drawer supersedes while it is open. */
const BACKGROUND = ["#main", "nav.sidebar"];

export function Drawer({ title, children }: { title: string; children: React.ReactNode }) {
  const router = useRouter();
  const panel = useRef<HTMLDivElement>(null);
  // Focus must go back to the row that opened the drawer, or a keyboard user
  // lands at the top of the document and has to find their place again.
  const opener = useRef<Element | null>(null);
  const [mounted, setMounted] = useState(false);

  const close = useCallback(() => router.back(), [router]);

  useEffect(() => setMounted(true), []);

  // useLayoutEffect, not useEffect: this runs BEFORE the browser paints, so
  // there is never a frame in which the drawer is visible while the content
  // behind it is still focusable and still in the accessibility tree. With
  // useEffect that window is one frame — small, real, and exactly the kind of
  // thing that shows up as an intermittent axe failure under load.
  useLayoutEffect(() => {
    if (!mounted) return;

    opener.current = document.activeElement;
    panel.current?.focus();

    const backdrops = BACKGROUND.flatMap((selector) =>
      Array.from(document.querySelectorAll<HTMLElement>(selector)),
    );
    for (const element of backdrops) element.inert = true;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;

      const stops = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (stops.length === 0) return;
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || active === panel.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      for (const element of backdrops) element.inert = false;
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [close, mounted]);

  if (!mounted) return null;

  return createPortal(
    <>
      <div className="scrim" onClick={close} aria-hidden="true" />
      <div
        ref={panel}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <button className="close" onClick={close} aria-label="Close">
          ✕
        </button>
        {children}
      </div>
    </>,
    document.body,
  );
}
