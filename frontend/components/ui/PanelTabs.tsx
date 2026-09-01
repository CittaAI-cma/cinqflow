import Link from "next/link";

/**
 * The five panels of the one drawer, with real tab semantics.
 *
 * They were anchors with `aria-current` — which reads to a screen reader as
 * "link, current page" rather than "tab 3 of 5, selected", and gives no
 * indication that the five are a set. They stay anchors (each panel is a URL,
 * because each panel is a CITATION), but the list is announced as a tablist.
 *
 * `data-panel` is preserved exactly: tests/workspace.spec.ts asserts all five
 * are visible and clicks one, and that test is the Wave-0 exit criterion.
 */
export function PanelTabs<T extends string>({
  panels,
  current,
  href,
}: {
  panels: readonly T[];
  current: T;
  href: (panel: T) => string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label="Panels">
      {panels.map((panel) => (
        <Link
          key={panel}
          role="tab"
          href={href(panel)}
          aria-selected={panel === current}
          aria-current={panel === current ? "page" : undefined}
          tabIndex={panel === current ? 0 : -1}
          data-panel={panel}
        >
          {panel}
        </Link>
      ))}
    </div>
  );
}
