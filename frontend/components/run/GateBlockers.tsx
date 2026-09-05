import type { GateBlocker } from "@/lib/api";

/** What each blocker is *about*, so the reason can point at the work rather
 *  than describe it. The ids are the anchors the S4 surfaces already render;
 *  a blocker whose anchor this map does not know renders as prose, which is
 *  exactly what the screen did for all of them before. */
const ANCHOR_HREF: Record<string, string> = {
  unmapped: "#studio",
  preview: "#preview",
};

const ANCHOR_ACTION: Record<string, string> = {
  unmapped: "go to the mapping table",
  preview: "run a preview",
};

/** Every reason G2 is closed, as work with somewhere to go.
 *
 *  The gate used to say one thing — "v3 changed after this preview ran" —
 *  because that was the only rule the page knew. The other three arrived as a
 *  409 after the analyst had already pressed the button and checked a box
 *  saying they stood behind the mapping. Naming all of them up front is the
 *  difference between a gate that refuses and a gate that tells you what to do.
 *
 *  Ordered by the server, which orders by what an analyst can act on: terminal
 *  states first (things to know), then the work, so the list reads as a
 *  to-do rather than a verdict. */
export default function GateBlockers({ blockers }: { blockers: GateBlocker[] }) {
  if (!blockers.length) return null;
  return (
    <ul className="gate-blockers" aria-label={`${blockers.length} reason(s) G2 is closed`}>
      {blockers.map((blocker) => {
        const href = blocker.anchor ? ANCHOR_HREF[blocker.anchor] : undefined;
        return (
          <li key={blocker.code} className="gate-blocker">
            <span className="gate-blocker-mark" aria-hidden="true" />
            <div className="gate-blocker-body">
              <span className="gate-blocker-message">{blocker.message}</span>
              {blocker.missing_required?.length ? (
                <span className="gate-blocker-list">
                  {blocker.missing_required.map((target) => (
                    <span key={target} className="mono">
                      {target}
                    </span>
                  ))}
                </span>
              ) : null}
              {blocker.hint ? <span className="meta small">{blocker.hint}</span> : null}
              {blocker.approver ? (
                <span className="meta small">
                  by <span className="mono">{blocker.approver}</span>
                </span>
              ) : null}
            </div>
            {href ? (
              <a className="gate-blocker-go" href={href}>
                {ANCHOR_ACTION[blocker.anchor as string]}
              </a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
