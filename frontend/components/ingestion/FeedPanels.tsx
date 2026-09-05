import Link from "next/link";
import LineageChain from "@/components/LineageChain";
import CollapsibleSection from "@/components/ui/CollapsibleSection";
import Timestamp from "@/components/ui/Timestamp";
import {
  getBatchQuarantine,
  getLineage,
  type ColumnFacts,
  type Run,
  type Upload,
  type UploadDetail,
} from "@/lib/api";
import { DQ_ACTIONS, DQ_TODAY } from "@/lib/dqVocabulary";

/** How many batches the lineage and quality panels resolve per render. A feed
 *  is small; a very large one keeps its most recent batches here and the rest
 *  on `/batches/{id}`. */
const BATCH_BUDGET = 8;

interface Drift {
  previous: Upload;
  added: string[];
  removed: string[];
  typeChanged: { name: string; from: string; to: string }[];
}

/** Columns added, removed or re-typed between the two most recent profiles of
 *  the feed - computed here from what the profiler recorded, no new endpoint. */
function driftBetween(latest: UploadDetail, previous: { upload: Upload; detail: UploadDetail }): Drift {
  const now = new Map(latest.profile!.facts.columns.map((c) => [c.name, c]));
  const before = new Map(previous.detail.profile!.facts.columns.map((c) => [c.name, c]));
  return {
    previous: previous.upload,
    added: [...now.keys()].filter((name) => !before.has(name)),
    removed: [...before.keys()].filter((name) => !now.has(name)),
    typeChanged: [...now.values()]
      .filter((c) => before.has(c.name) && before.get(c.name)!.inferred_type !== c.inferred_type)
      .map((c) => ({ name: c.name, from: before.get(c.name)!.inferred_type, to: c.inferred_type })),
  };
}

function newestFirst<T extends { created_ts?: string; started_ts?: string }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) =>
      Date.parse(b.started_ts ?? b.created_ts ?? "") - Date.parse(a.started_ts ?? a.created_ts ?? ""),
  );
}

/** The feed-level view (PR-8): what the feed's schema looks like now and how it
 *  drifted since the previous delivery, the lineage of each batch with both
 *  approvals, and the quality record - balance equation per run, what promotion
 *  refused and why. Every figure is already served (`GET /api/uploads/{id}`,
 *  `/api/lineage/{batch}`, `/api/batches/{id}/quarantine`); nothing new is
 *  stored. Tabs within the group view, not a route: the group *is* the feed
 *  (structure.md keeps feed-level surfaces here).
 *
 *  Persona: open by default for Data Platform (`defaultOpen`), one click away
 *  for everyone else - a persona changes emphasis, never what is reachable
 *  (plan §18.2). */
export default async function FeedPanels({
  objects,
  details,
  defaultOpen,
}: {
  objects: Upload[];
  /** Positional against `objects`, as the group page loads them. */
  details: (UploadDetail | null)[];
  defaultOpen: boolean;
}) {
  const profiled = newestFirst(
    objects
      .map((upload, index) => ({ upload, detail: details[index] ?? null, created_ts: upload.created_ts }))
      .filter((entry): entry is { upload: Upload; detail: UploadDetail; created_ts: string } =>
        Boolean(entry.detail?.profile),
      ),
  );
  const latest = profiled[0] ?? null;
  const previous = profiled[1] ?? null;
  const drift = latest && previous ? driftBetween(latest.detail, previous) : null;

  const runs = newestFirst(details.flatMap((detail) => detail?.runs ?? []));
  const landRuns = runs.filter((run) => run.kind === "land_bronze").slice(0, BATCH_BUDGET);
  const promoteRuns = runs.filter((run) => run.kind === "promote_silver").slice(0, BATCH_BUDGET);
  const [lineages, quarantines] = await Promise.all([
    Promise.all(landRuns.map((run) => getLineage(run.batch_id).catch(() => null))),
    Promise.all(promoteRuns.map((run) => getBatchQuarantine(run.batch_id, 25))),
  ]);

  return (
    <div className="feed-panels">
      <div className="feed-panels-head">
        <span className="panel-label">Feed view</span>
        <span className="meta">
          Schema, lineage and quality for this feed — open by default for the Data Platform
          persona, one click away for everyone.
        </span>
      </div>

      <CollapsibleSection
        title={
          latest
            ? `Schema · ${latest.detail.profile!.facts.columns.length} columns`
            : "Schema · no profile yet"
        }
        defaultOpen={defaultOpen}
      >
        {latest ? (
          <SchemaPanel latest={latest.detail} upload={latest.upload} drift={drift} />
        ) : (
          <p className="meta">No upload of this feed has been profiled yet.</p>
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title={`Lineage · ${landRuns.length} batch${landRuns.length === 1 ? "" : "es"}`}
        defaultOpen={defaultOpen}
      >
        {landRuns.length === 0 ? (
          <p className="meta">Nothing has landed for this feed yet — lineage starts at G1.</p>
        ) : (
          landRuns.map((run, index) => {
            const lineage = lineages[index];
            return (
              <div key={run.batch_id} className="card" style={{ marginTop: 10 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                  <span className="mono small">
                    batch <Link href={`/batches/${run.batch_id}`}>{run.batch_id}</Link>
                  </span>
                  <Timestamp value={run.started_ts} withSeconds={false} className="meta" />
                </div>
                {lineage ? (
                  <LineageChain chain={lineage.chain} gates={lineage.gates} />
                ) : (
                  <p className="meta">Lineage for this batch could not be loaded.</p>
                )}
              </div>
            );
          })
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title={`Quality · ${runs.length} run${runs.length === 1 ? "" : "s"}`}
        defaultOpen={defaultOpen}
      >
        <QualityPanel runs={runs} promoteRuns={promoteRuns} quarantines={quarantines} />
      </CollapsibleSection>
    </div>
  );
}

function SchemaPanel({
  latest,
  upload,
  drift,
}: {
  latest: UploadDetail;
  upload: Upload;
  drift: Drift | null;
}) {
  const facts = latest.profile!.facts;
  const keyColumns = new Set(facts.candidate_keys.flat());
  const added = new Set(drift?.added ?? []);
  const retyped = new Map(drift?.typeChanged.map((t) => [t.name, t]) ?? []);
  return (
    <>
      <p className="meta" style={{ marginTop: 8 }}>
        From <Link href={`/uploads/${upload.upload_id}`}>{upload.filename}</Link> · profiler v
        {latest.profile!.profiler_version} · {facts.row_count.toLocaleString()} rows
        {facts.time_coverage
          ? ` · time coverage ${facts.time_coverage.min} → ${facts.time_coverage.max}`
          : ""}
        {facts.candidate_keys.length
          ? ` · key ${facts.candidate_keys.map((k) => k.join(" + ")).join(", ")}`
          : " · no candidate key"}
      </p>
      {drift ? (
        <p className="meta">
          Drift since <Link href={`/uploads/${drift.previous.upload_id}`}>{drift.previous.filename}</Link>:{" "}
          {drift.added.length + drift.removed.length + drift.typeChanged.length === 0
            ? "none — same columns, same types."
            : `${drift.added.length} added · ${drift.removed.length} removed · ${drift.typeChanged.length} re-typed.`}
        </p>
      ) : (
        <p className="meta">One profiled delivery so far — drift appears from the second.</p>
      )}
      {drift?.removed.length ? (
        <div className="drift-tags">
          {drift.removed.map((name) => (
            <span key={name} className="tag danger" title="Present in the previous delivery, absent now">
              removed {name}
            </span>
          ))}
        </div>
      ) : null}
      <div className="card scroll" style={{ padding: 0, marginTop: 10 }}>
        <table>
          <thead>
            <tr>
              <th>Column</th>
              <th>Type</th>
              <th>Role hint</th>
              <th className="num">Null ratio</th>
              <th className="num">Distinct</th>
              <th>Constraint</th>
              <th>Range</th>
            </tr>
          </thead>
          <tbody>
            {facts.columns.map((column: ColumnFacts) => (
              <tr key={column.name}>
                <td className="mono">
                  {column.name}{" "}
                  {column.phi_candidate ? <span className="tag phi">PHI</span> : null}
                  {added.has(column.name) ? (
                    <span className="tag" title="Not in the previous delivery">new</span>
                  ) : null}
                  {retyped.has(column.name) ? (
                    <span className="tag danger" title="Type changed since the previous delivery">
                      was {retyped.get(column.name)!.from}
                    </span>
                  ) : null}
                </td>
                <td className="mono">{column.inferred_type}</td>
                <td>
                  <span className={`role-pill ${column.hint ?? "unclassified"}`}>
                    {column.hint ?? "—"}
                  </span>
                </td>
                <td className="num">
                  {column.null_ratio !== undefined
                    ? `${(column.null_ratio * 100).toFixed(1)}%`
                    : column.null_count.toLocaleString()}
                </td>
                <td className="num">{column.distinct_count.toLocaleString()}</td>
                <td className="mono small">
                  {keyColumns.has(column.name) ? "key" : column.constant ? "constant" : "—"}
                </td>
                <td className="mono small">
                  {column.phi_candidate
                    ? "•••• masked"
                    : column.min != null || column.max != null
                      ? `${column.min ?? "?"} → ${column.max ?? "?"}`
                      : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function QualityPanel({
  runs,
  promoteRuns,
  quarantines,
}: {
  runs: Run[];
  promoteRuns: Run[];
  quarantines: Awaited<ReturnType<typeof getBatchQuarantine>>[];
}) {
  if (runs.length === 0) {
    return <p className="meta">No runs yet — the quality record starts when a batch lands.</p>;
  }
  return (
    <>
      <div className="card scroll" style={{ padding: 0, marginTop: 10 }}>
        <table>
          <thead>
            <tr>
              <th>Batch</th>
              <th>Run</th>
              <th>State</th>
              <th className="num">In</th>
              <th className="num">Out</th>
              <th className="num">Quarantined</th>
              <th className="num">Drops</th>
              <th>Balanced</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={`${run.batch_id}:${run.kind}`}>
                <td className="mono small">
                  <Link href={`/batches/${run.batch_id}`}>{run.batch_id.slice(0, 12)}</Link>
                </td>
                <td className="mono small">{run.kind}</td>
                <td className="mono small">{run.state}</td>
                <td className="num">{run.counts?.records_in.toLocaleString() ?? "—"}</td>
                <td className="num">{run.counts?.records_out.toLocaleString() ?? "—"}</td>
                <td className="num">{run.counts?.quarantined.toLocaleString() ?? "—"}</td>
                <td className="num">{run.counts?.attributed_drops.toLocaleString() ?? "—"}</td>
                <td>
                  {run.balanced === null ? (
                    <span className="meta">—</span>
                  ) : run.balanced ? (
                    <span className="balance-ok">in = out + quarantined + drops</span>
                  ) : (
                    <span className="balance-bad">does not balance{run.error ? ` · ${run.error}` : ""}</span>
                  )}
                </td>
                <td>
                  <Timestamp value={run.started_ts} withSeconds={false} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {promoteRuns.map((run, index) => {
        const quarantine = quarantines[index];
        if (!quarantine) return null;
        const rules = Object.entries(quarantine.by_rule).sort((a, b) => b[1] - a[1]).slice(0, 8);
        return (
          <div key={run.batch_id} className="card" style={{ marginTop: 10 }}>
            <span className="panel-label">
              Refused at promotion · batch <span className="mono">{run.batch_id.slice(0, 12)}</span>
            </span>
            <p className="meta" style={{ marginTop: 6 }}>
              {quarantine.total.toLocaleString()} row{quarantine.total === 1 ? "" : "s"} held in{" "}
              <span className="mono">{quarantine.table}</span>
              {quarantine.phi_masked?.length ? ` · ${quarantine.phi_masked.length} PHI columns masked` : ""}
            </p>
            <div className="chip-row">
              {Object.entries(quarantine.by_outcome).map(([outcome, count]) => (
                <span key={outcome} className="chip">
                  {outcome} <span className="mono">{count}</span>
                </span>
              ))}
            </div>
            {rules.length ? (
              <ul className="plain" style={{ marginTop: 8 }}>
                {rules.map(([rule, count]) => (
                  <li key={rule} className="mono small">
                    {rule} — {count} row{count === 1 ? "" : "s"}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}

      <div className="dq-legend">
        <span className="panel-label">Action vocabulary · knowledge/dq/severity.yaml</span>
        {DQ_ACTIONS.map((entry) => (
          <span key={entry.action}>
            <b>{entry.action}</b> {entry.meaning}
          </span>
        ))}
        <span className="meta">{DQ_TODAY}</span>
      </div>
    </>
  );
}
