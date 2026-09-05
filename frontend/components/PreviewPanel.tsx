"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useToast } from "@/lib/useToast";
import EmptyState from "@/components/ui/EmptyState";
import { useActionState, useEffect, useMemo, useState } from "react";
import { useFormStatus } from "react-dom";
import Kpi from "@/components/Kpi";
import StatusWord from "@/components/StatusWord";
import WorkflowSteps, { stepsInFlight } from "@/components/run/WorkflowSteps";
import { runPreview, type StudioState } from "@/app/mapping/actions";
import type { PreviewResult, StepProgress } from "@/lib/api";
import { previewStatusWord } from "@/lib/statusWords";

/** A preview is deterministic execution over a bounded sample - it is quick
 *  once a worker has it. A minute means nothing claimed the job. */
const PREVIEW_STALL_MS = 60_000;

const ROW_LIMITS = [10, 25, 50] as const;

function RunButton({ label }: { label: string }) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending}>
      {pending ? "Queueing…" : label}
    </button>
  );
}

const OUTCOME_CLASS: Record<string, string> = {
  ok: "ok",
  defaulted: "warn",
  null: "warn",
  failure: "bad",
  quarantined: "bad",
  rejected: "bad",
};

export default function PreviewPanel({
  feed,
  version,
  preview,
  limit = 25,
  baseHref,
  initialSteps = [],
  canRerun = false,
}: {
  feed: string;
  version: number;
  preview: PreviewResult | null;
  limit?: number;
  /** The row-limit chips build their own `?v=&limit=` URL from this plus
   *  `version` - a *string*, not a callback, because this component is
   *  "use client": a Server Component caller (`MappingPageBody`) cannot pass
   *  a function prop across that boundary ("Functions cannot be passed
   *  directly to Client Components"), only serializable data. `baseHref`
   *  is whichever of the two mapping routes (`/mapping/{feed}` or
   *  `/runs/{uploadId}/mapping`) is actually rendering this. */
  baseHref: string;
  /** The feed version's ledger steps from the server render
   *  (`getFeedVersionProgress`). `WorkflowSteps` polls only while the preview
   *  step is queued or running, and refreshes this page when it is not. */
  initialSteps?: StepProgress[];
  /** `capabilities.can_rerun_steps` - whether a failed preview offers Re-run. */
  canRerun?: boolean;
}) {
  const router = useRouter();
  const [state, action] = useActionState<StudioState, FormData>(runPreview, {});
  const { push } = useToast();
  const [fieldFilter, setFieldFilter] = useState<string>("");

  // The preview is written by a worker: `runPreview` only queues it
  // (`app/mapping/actions.ts`). By the time the action returns, the step
  // ledger already holds a `pending` row for the preview step, so refreshing
  // the server render hands `WorkflowSteps` an in-flight step and it polls
  // until the preview is done - then refreshes again with the result. An
  // identical request the queue deduplicated leaves no pending row, so there
  // is nothing to wait for and the existing preview simply stays.
  useEffect(() => {
    if (state.saved) router.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.saved]);
  const previewStep = initialSteps.find((s) => s.key === "preview");

  // The fields actually present across the loaded sample, in source order of
  // first appearance — what the dropdown offers. Recomputed only when the
  // preview itself changes, not on every keystroke elsewhere on the page.
  const fieldOptions = useMemo(() => {
    const seen = new Set<string>();
    const options: { source: string; target: string }[] = [];
    for (const row of preview?.row_results ?? []) {
      for (const field of row.fields) {
        if (seen.has(field.source)) continue;
        seen.add(field.source);
        options.push({ source: field.source, target: field.target });
      }
    }
    return options;
  }, [preview]);

  // The inline alert below remains the record; the toast is what reaches an
  // analyst who has already scrolled past this panel into the row table.
  useEffect(() => {
    if (state.saved) push("Preview queued.", "success");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.saved]);
  useEffect(() => {
    if (state.error) push(state.error, "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.error]);
  const aggregates = preview?.aggregates;

  return (
    <>
      <h2>
        Deterministic preview{" "}
        <span className="meta">
          · executed in code, no model involved ·{" "}
          <StatusWord word={previewStatusWord(preview)} />
        </span>
      </h2>

      <form action={action} className="card grid">
        <input type="hidden" name="feed" value={feed} />
        <input type="hidden" name="version" value={version} />
        {/* So `runPreview` revalidates the route this panel is actually on.
            Every action in app/mapping/actions.ts used to revalidate only
            `/mapping/{feed}`, leaving the run surface serving a cached render. */}
        <input type="hidden" name="base_path" value={baseHref} />
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="meta">
            Runs v{version} against a bounded sample spread across the latest Bronze batch —
            every k-th row rather than the first N, so a clean result is not merely a clean
            first window. Nothing is written to Silver.
          </span>
          <RunButton label={preview ? "Run preview again" : "Run preview"} />
        </div>
        {state.error ? <p className="alert error">{state.error}</p> : null}
        {stepsInFlight(initialSteps, ["preview"]) || previewStep?.state === "failed" ? (
          <WorkflowSteps
            source={{ kind: "feed_version", feed, version }}
            initial={initialSteps}
            only={["preview"]}
            canRerun={canRerun}
            stallAfterMs={PREVIEW_STALL_MS}
            what="the preview"
            stalledCopy="The mapping version is saved and unchanged — only the preview run is outstanding. No worker appears to have claimed mapping.preview. G2 stays closed until one does, by design: nobody approves a mapping they have not seen run."
          />
        ) : null}
      </form>

      {!preview ? (
        <p className="empty">
          No preview yet for v{version}. Until one exists, G2 approval stays closed.
        </p>
      ) : (
        <>
          {!preview.is_current ? (
            <p className="alert warn">
              <b>Stale.</b> {preview.stale_reason} — v{version} changed after this preview ran.
              G2 approval will stay closed until it is re-run.
            </p>
          ) : (
            <p className="alert ok">
              Current for this spec — the mapping below is what v{version} would do.
            </p>
          )}

          <Kpi
            items={[
              { key: "previewed", value: aggregates!.rows_previewed.toLocaleString(), label: "rows previewed" },
              { key: "ok", value: aggregates!.rows_ok.toLocaleString(), label: "rows ok", tone: "ok" },
              {
                key: "failures",
                value: aggregates!.rows_with_failures.toLocaleString(),
                label: "with failures",
                tone: aggregates!.rows_with_failures ? "danger" : undefined,
              },
              { key: "quarantined", value: aggregates!.rows_quarantined.toLocaleString(), label: "quarantined" },
              { key: "rejected", value: aggregates!.rows_rejected.toLocaleString(), label: "rejected" },
              {
                key: "sample",
                value: `${preview.sample.rows}/${preview.sample.rows_in_batch}`,
                label: preview.sample_is_partial ? "sample (partial)" : "sample",
              },
            ]}
          />
          <p className="meta" style={{ marginTop: 10 }}>
            batch <span className="mono">{preview.sample.batch_id}</span> ·{" "}
            <span className="mono">{preview.sample.bronze_table}</span> · selector{" "}
            <span className="mono">{preview.sample.selector}</span>
          </p>

          {Object.keys(aggregates!.failures_by_rule).length ? (
            <div className="card" style={{ marginTop: 14 }}>
              <label>Failures by rule</label>
              <ul className="plain">
                {Object.entries(aggregates!.failures_by_rule).map(([rule, count]) => (
                  <li key={rule} className="mono small">
                    {rule} — {count} row{count === 1 ? "" : "s"}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div style={{ marginTop: 14 }}>
              {/* A clean preview is the result the analyst is hoping for. It
                  has to be stated: silence here is indistinguishable from
                  "this check did not run". */}
              <EmptyState
                tone="result"
                compact
                title="No rule refused a row."
                detail="Every sampled row satisfied the spec's null rules, casts and value maps."
              />
            </div>
          )}

          {Object.keys(aggregates!.null_or_invalid).length ? (
            <div className="card" style={{ marginTop: 14 }}>
              <label>Targets that would receive no value</label>
              <span className="mono small">
                {Object.entries(aggregates!.null_or_invalid)
                  .map(([target, count]) => `${target} (${count})`)
                  .join(" · ")}
              </span>
            </div>
          ) : (
            <div style={{ marginTop: 14 }}>
              <EmptyState
                tone="result"
                compact
                title="Every mapped target received a value."
                detail="No target in this spec came out null or invalid across the sample."
              />
            </div>
          )}

          <h2>
            Row by row{" "}
            <span className="meta">
              · showing {preview.row_results.length} of {preview.row_results_total} rows
              {fieldFilter ? <> · filtered to <span className="mono">{fieldFilter}</span></> : null}
            </span>
          </h2>

          {preview.phi_masked.length ? (
            <p className="meta" style={{ marginBottom: 8 }}>
              <span className="tag phi">PHI</span> {preview.phi_masked.length} column
              {preview.phi_masked.length === 1 ? "" : "s"} masked below —{" "}
              <span className="mono">{preview.phi_masked.join(", ")}</span>
            </p>
          ) : null}

          <div className="row" style={{ justifyContent: "space-between", marginBottom: 10, flexWrap: "wrap", gap: 10 }}>
            <div>
              <label htmlFor="preview-field-filter" className="sr-only">
                Filter by field
              </label>
              <select
                id="preview-field-filter"
                className="native-select"
                value={fieldFilter}
                onChange={(event) => setFieldFilter(event.target.value)}
                title="See how one field mapped across every sampled row"
              >
                <option value="">All fields ({fieldOptions.length})</option>
                {fieldOptions.map((option) => (
                  <option key={option.source} value={option.source}>
                    {option.source} → {option.target}
                    {preview.phi_masked.includes(option.source) ? " (PHI)" : ""}
                  </option>
                ))}
              </select>
            </div>

            <div className="chip-row" role="group" aria-label="Rows to show">
              {ROW_LIMITS.map((n) => (
                <Link
                  key={n}
                  href={`${baseHref}?v=${version}&limit=${n}`}
                  className={`chip${n === limit ? " on" : ""}`}
                  aria-current={n === limit ? "true" : undefined}
                  title={`Show the first ${n} sampled rows`}
                >
                  First {n}
                </Link>
              ))}
            </div>
          </div>

          <div className="card scroll" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th className="num">Row</th>
                  <th>Outcome</th>
                  <th>Source column</th>
                  <th>Source value</th>
                  <th>Mapped value</th>
                  <th>Target</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {preview.row_results
                  .map((row) => ({
                    ...row,
                    fields: fieldFilter
                      ? row.fields.filter((f) => f.source === fieldFilter)
                      : row.fields,
                  }))
                  .filter((row) => row.fields.length > 0)
                  .flatMap((row) =>
                    row.fields.map((field, index) => (
                      <tr key={`${row.row_number}-${field.source}`}>
                        {index === 0 ? (
                          <>
                            <td className="num mono" rowSpan={row.fields.length}>
                              {row.row_number}
                            </td>
                            <td rowSpan={row.fields.length}>
                              <span className={`outcome ${OUTCOME_CLASS[row.outcome] ?? ""}`}>
                                {row.outcome}
                              </span>
                            </td>
                          </>
                        ) : null}
                        <td className="mono">
                          {field.source}
                          {preview.phi_masked.includes(field.source) ? (
                            <span className="tag phi" style={{ marginLeft: 6 }}>
                              PHI
                            </span>
                          ) : null}
                        </td>
                        <td className="mono">
                          {field.source_value === null || field.source_value === ""
                            ? "—"
                            : field.source_value}
                        </td>
                        <td className="mono">
                          {field.mapped_value === null ? (
                            <span className="error">null</span>
                          ) : (
                            field.mapped_value
                          )}
                        </td>
                        <td className="mono small">{field.target}</td>
                        <td className="evidence">
                          {field.outcome === "ok" ? "" : field.reason}
                        </td>
                      </tr>
                    )),
                  )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
