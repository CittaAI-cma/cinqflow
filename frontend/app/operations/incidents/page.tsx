import { ActionBar, type ActionSpec } from "@/components/ActionBar";
import { CascadeTree } from "@/components/CascadeTree";
import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Tag, type Tone } from "@/components/Tag";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { Incident, IncidentRow, Principal } from "@/lib/types";
import { acknowledgeIncident, closeIncident, resolveIncident } from "./actions";

/**
 * CF-V2-E12-04 — what broke, what it was, and what fixed it last time.
 *
 * `open`, `acknowledged` and `resolved` are the three non-final states — a
 * CLOSED incident has nothing left to act on and is what "open incidents"
 * deliberately excludes. Fetched one call per state rather than the whole
 * ledger and filtered client-side, the same concurrent-`Promise.all` shape
 * `batchesForFeeds` uses: three round-trips in parallel, not one that risks
 * growing without bound as closed incidents accumulate.
 *
 * The ROW is the ledger's own cheap view; the FULL evidence (root cause,
 * consequences, the guide match) is the per-batch route's, fetched once per
 * open incident — a bounded set, unlike every batch a feed has ever run —
 * exactly as `app.list_incidents`'s own docstring says the list route itself
 * must not do for every incident that ever existed.
 */

const OPEN_STATES = ["open", "acknowledged", "resolved"] as const;

export default async function Incidents({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const result = await searchParams;

  const [rowSets, me] = await Promise.all([
    Promise.all(
      OPEN_STATES.map((state) => attempt<IncidentRow[]>(`/api/operations/incidents?state=${state}`)),
    ),
    attempt<Principal>("/api/me"),
  ]);
  const first = rowSets[0];
  if (isRefused(first)) return <RefusalNotice refusal={first} />;

  const rows = rowSets
    .flatMap((set) => (isRefused(set) ? [] : set))
    .sort((a, b) => b.opened_ts.localeCompare(a.opened_ts));

  const evidence = await Promise.all(
    rows.map((row) =>
      attempt<Incident>(`/api/operations/batches/${encodeURIComponent(row.batch_id)}/incident`),
    ),
  );

  const mayAct = !isRefused(me) && me.permitted_actions.includes("acknowledge");
  const refusal = mayAct ? undefined : "acknowledge is not permitted for your role";

  return (
    <>
      <h1>Incidents</h1>
      <p className="lede">What broke, what it was, and what fixed it last time.</p>

      {result.outcome ? (
        <div className="card outcome" data-outcome={result.outcome}>
          <strong className="outcome-word">{result.outcome}</strong>
          <p>{result.headline}</p>
        </div>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState kind="recorded" what="open incidents" />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="sr-only">Open incidents, newest first</caption>
            <thead>
              <tr>
                <th scope="col">State</th>
                <th scope="col">Batch</th>
                <th scope="col">Feed</th>
                <th scope="col">Failure</th>
                <th scope="col">Known?</th>
                <th scope="col">Assigned to</th>
                <th scope="col">Opened</th>
                <th scope="col">Act</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const full = evidence[index];
                const detail = isRefused(full) ? null : full;
                return (
                  <tr className="row" key={row.incident_id}>
                    <td>
                      <Tag tone={incidentTone(row.state)}>{row.state}</Tag>
                    </td>
                    <td>
                      <Cited value={row.batch_id} citationId={detail?.citation ?? `batch:${row.batch_id}`} />
                    </td>
                    <td>{row.feed_id}</td>
                    <td>
                      <CascadeTree
                        rootCause={detail?.root_cause ?? null}
                        consequences={detail?.consequences ?? []}
                      />
                    </td>
                    <td>
                      {detail?.match ? (
                        <>
                          <Tag tone="good">Known</Tag>
                          <div className="note">
                            {detail.match.occurrences} prior occurrence
                            {detail.match.occurrences === 1 ? "" : "s"}, mean fix{" "}
                            {detail.match.mean_fix_minutes ?? "—"} minutes
                          </div>
                        </>
                      ) : (
                        <Tag tone="bad">Novel</Tag>
                      )}
                    </td>
                    <td>{row.assigned_to || "—"}</td>
                    <td className="num mono">{row.opened_ts.slice(0, 19).replace("T", " ")}</td>
                    <td>
                      <ActionBar
                        subjectField="incident_id"
                        subjectId={row.incident_id}
                        actions={actionsFor(row.state, refusal)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/**
 * `available(state, guards)`, plate 4.7's own phrase — the incident's own
 * transition table (`open → {acknowledged, resolved}`,
 * `acknowledged → {resolved}`, `resolved → {closed}`), read here rather than
 * imported: `core.operations.fingerprint._TRANSITIONS` is private, and this
 * mirrors only which BUTTONS are worth showing. The server holds the actual
 * rule and refuses anything this guessed wrong.
 */
function actionsFor(state: string, disabledBecause: string | undefined): ActionSpec[] {
  const resolveField = (
    <input name="resolution" placeholder="What fixed it" aria-label="Resolution" required />
  );
  const assignField = <input name="assigned_to" placeholder="Assign to (optional)" aria-label="Assign to" />;

  if (state === "open") {
    return [
      { key: "acknowledge", label: "Acknowledge", action: acknowledgeIncident, fields: assignField, disabledBecause },
      { key: "resolve", label: "Resolve", action: resolveIncident, fields: resolveField, disabledBecause },
    ];
  }
  if (state === "acknowledged") {
    return [
      { key: "resolve", label: "Resolve", action: resolveIncident, fields: resolveField, disabledBecause },
    ];
  }
  if (state === "resolved") {
    return [{ key: "close", label: "Close", action: closeIncident, disabledBecause }];
  }
  return [];
}

function incidentTone(state: string): Tone {
  switch (state) {
    case "open":
      return "bad";
    case "acknowledged":
      return "pending";
    case "resolved":
      return "good";
    default:
      return "neutral";
  }
}
