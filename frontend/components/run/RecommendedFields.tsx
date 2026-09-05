import type { ColumnRole } from "@/lib/api";
import { ROLE_LABEL, ROLE_ORDER } from "@/lib/columnRoles";

/** How many recommended fields the panel shows before deferring the rest to
 *  Forensic. The acceptance for a 60-column file is "≤ 12 recommended fields
 *  and seven role groups before any 60-row table". */
const MAX_SHOWN = 12;

/** S2, Evidence and Verdict modes: the L1/L2 answer to "what is this file made
 *  of" without reading sixty rows - the columns the model marked `high`
 *  importance, grouped by role, each with its reason. Importance is bounded
 *  by knowledge on the backend (a glossary term that maps toward a canonical
 *  field, or the domain's `what_it_answers`), so this list is short by
 *  construction; the reason carries the citation as text - the knowledge layer
 *  has no HTTP surface yet (knowledge-base-screen.md §5), so no link.
 *
 *  Never hides a fact: every column, whatever its importance, is in Forensic. */
export default function RecommendedFields({ roles }: { roles: ColumnRole[] }) {
  const high = roles.filter((r) => r.importance === "high");
  if (!high.length) return null;

  const ordered = ROLE_ORDER.flatMap((role) => high.filter((r) => r.role === role));
  const shown = ordered.slice(0, MAX_SHOWN);
  const groups = ROLE_ORDER.map((role) => ({
    role,
    items: shown.filter((r) => r.role === role),
  })).filter((g) => g.items.length > 0);
  const deferred = ordered.length - shown.length;

  return (
    <div className="card recommended-fields">
      <span className="panel-label">Recommended fields</span>
      <p className="meta" style={{ marginTop: 6 }}>
        The {ordered.length} column{ordered.length === 1 ? "" : "s"} governed knowledge marks as
        high importance for this file — what it is made of, before any row.
      </p>
      {groups.map((group) => (
        <div key={group.role} className="recommended-group">
          <span className={`role-pill ${group.role}`}>{ROLE_LABEL[group.role]}</span>
          <ul className="plain">
            {group.items.map((item) => (
              <li key={item.name}>
                <span className="mono">{item.name}</span>{" "}
                <span className="meta">— {item.reason}</span>
                {item.source === "hint" ? (
                  <span className="meta" title="The model did not classify this column; the profiler's rule stands">
                    {" "}
                    · from the profiler
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {deferred > 0 ? (
        <p className="meta recommended-more">
          {deferred} more high-importance column{deferred === 1 ? "" : "s"} — open Forensic to see
          every column grouped by role.
        </p>
      ) : null}
    </div>
  );
}
