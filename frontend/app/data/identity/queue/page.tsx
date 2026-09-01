import { ActionBar, type ActionSpec } from "@/components/ActionBar";
import { RefusalNotice } from "@/components/Refusal";
import { Tag, type Tone } from "@/components/Tag";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { IdentityException, Principal, QueueHealth } from "@/lib/types";
import { assignException, resolveException } from "./actions";

/**
 * CF-V3-E9-02 — one queue holding every identity exception: failed calls,
 * retry-exhausted requests, low-confidence matches, unresolved records.
 *
 * DEDUPLICATED BY PERSON, NEVER BY BATCH. A row here is one `source_system`
 * + `source_member_id` — "the same person failing in three batches is one
 * exception with three occurrences, not three items" is rendered as a single
 * row whose occurrence badge says 3, never as three rows. The server did
 * this folding (`core.identity.exceptions.fold`); this screen only shows it.
 *
 * QUEUE HEALTH IS PER SOURCE, DELIBERATELY NEVER ROLLED UP. "A payer sending
 * bad demographics becomes visible" is false the moment every payer's counts
 * are summed into one number — so `/health` returns one card per
 * `source_system` and this screen renders exactly that many cards, not one.
 *
 * OLDEST-OPENED FIRST. `list_identity_exceptions` orders on `opened_ts`, not
 * `latest_ts` — the longest-standing problem is the one a steward should see
 * first, and a third failure must make an exception look WORSE (rise to the
 * top), never younger (reset to the bottom as if it just started).
 */

export default async function IdentityQueue({
  searchParams,
}: {
  searchParams: Promise<{
    source_system?: string;
    state?: string;
    outcome?: string;
    headline?: string;
  }>;
}) {
  const { source_system: sourceFilter, state: stateFilter, outcome, headline } = await searchParams;

  const query = new URLSearchParams();
  if (sourceFilter) query.set("source_system", sourceFilter);
  if (stateFilter) query.set("state", stateFilter);
  const queryString = query.toString();

  const [rows, health, me] = await Promise.all([
    attempt<IdentityException[]>(`/api/identity/exceptions${queryString ? `?${queryString}` : ""}`),
    attempt<QueueHealth[]>("/api/identity/exceptions/health"),
    attempt<Principal>("/api/me"),
  ]);

  if (isRefused(rows)) return <RefusalNotice refusal={rows} />;

  const mayAssign = !isRefused(me) && me.permitted_actions.includes("assign");
  const mayResolve = !isRefused(me) && me.permitted_actions.includes("acknowledge");

  return (
    <>
      <h1>Identity Exception Queue</h1>
      <p className="lede">
        Every identity problem, triaged, deduplicated, aged and assignable — the highest-stakes
        error class on a PHI platform, with an owner and a deadline instead of ad-hoc handling.
      </p>

      {outcome ? (
        <div className="card outcome" data-outcome={outcome}>
          <strong className="outcome-word">{outcome}</strong>
          <p>{headline}</p>
        </div>
      ) : null}

      {!isRefused(health) && health.length > 0 ? (
        <div className="grid">
          {health.map((h) => (
            <div className="card" key={h.source_system}>
              <h3 style={{ marginBottom: "var(--s-2)" }}>{h.source_system}</h3>
              <dl className="kv">
                <dt>Open</dt>
                <dd className="num mono">{h.open_count}</dd>
                <dt>Past SLA</dt>
                <dd className="num mono">
                  {h.breached_count > 0 ? (
                    <Tag tone="bad">{h.breached_count}</Tag>
                  ) : (
                    <span className="mono">0</span>
                  )}
                </dd>
                <dt>Resolved</dt>
                <dd className="num mono">{h.resolved_count}</dd>
              </dl>
            </div>
          ))}
        </div>
      ) : null}

      <form className="inline" style={{ marginBottom: "var(--s-3)" }}>
        <div className="field">
          <label htmlFor="source_system">Source</label>
          <input
            id="source_system"
            name="source_system"
            defaultValue={sourceFilter ?? ""}
            placeholder="e.g. fidelis"
          />
        </div>
        <div className="field">
          <label htmlFor="state">State</label>
          <select id="state" name="state" defaultValue={stateFilter ?? ""}>
            <option value="">Any</option>
            <option value="open">Open</option>
            <option value="assigned">Assigned</option>
            <option value="escalated">Escalated</option>
            <option value="resolved">Resolved</option>
          </select>
        </div>
        <button type="submit">Filter</button>
      </form>

      {rows.length === 0 ? (
        <EmptyState kind="recorded" what="identity exceptions" />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="sr-only">Identity exceptions, oldest-opened first</caption>
            <thead>
              <tr>
                <th scope="col">State</th>
                <th scope="col">Source</th>
                <th scope="col">Member</th>
                <th scope="col">Occurrences</th>
                <th scope="col">Assigned to</th>
                <th scope="col">Opened</th>
                <th scope="col">Act</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((exc) => (
                <tr className="row" key={exc.key}>
                  <td>
                    <Tag tone={exceptionTone(exc.state)}>{exc.state}</Tag>
                  </td>
                  <td>{exc.source_system}</td>
                  <td className="mono">{exc.source_member_id}</td>
                  <td>
                    {exc.occurrence_count}{" "}
                    {exc.occurrence_count === 1 ? "occurrence" : "occurrences"}
                  </td>
                  <td>{exc.assigned_to || "—"}</td>
                  <td className="num mono">{exc.opened_ts.slice(0, 19).replace("T", " ")}</td>
                  <td>
                    <ActionBar
                      subjectField="key"
                      subjectId={exc.key}
                      actions={actionsFor(exc.state, mayAssign, mayResolve)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/**
 * The queue's own reachable moves, read here rather than imported — this
 * mirrors only which BUTTONS are worth showing. The server holds the real
 * rule (`assign`/`resolve` in `core.identity.exceptions`) and refuses
 * anything this guessed wrong: a resolved exception offers nothing, because
 * `assign()` itself refuses one and there is nothing further `resolve()`
 * could do to it.
 */
function actionsFor(state: string, mayAssign: boolean, mayResolve: boolean): ActionSpec[] {
  if (state === "resolved") return [];

  const assignedTo = <input name="assigned_to" placeholder="Assign to" aria-label="Assign to" required />;
  const note = <input name="note" placeholder="Note (optional)" aria-label="Resolution note" />;

  const actions: ActionSpec[] = [];
  actions.push({
    key: "assign",
    label: "Assign",
    action: assignException,
    fields: assignedTo,
    disabledBecause: mayAssign ? undefined : "assign is not permitted for your role",
  });
  actions.push({
    key: "resolve",
    label: "Resolve",
    action: resolveException,
    fields: note,
    disabledBecause: mayResolve ? undefined : "resolve is not permitted for your role",
  });
  return actions;
}

function exceptionTone(state: string): Tone {
  switch (state) {
    case "open":
      return "bad";
    case "assigned":
      return "pending";
    case "escalated":
      return "bad";
    case "resolved":
      return "good";
    default:
      return "neutral";
  }
}
