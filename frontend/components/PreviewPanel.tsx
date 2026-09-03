"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import Kpi from "@/components/Kpi";
import StatusWord from "@/components/StatusWord";
import { runPreview, type StudioState } from "@/app/mapping/actions";
import type { PreviewResult } from "@/lib/api";
import { previewStatusWord } from "@/lib/statusWords";

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
}: {
  feed: string;
  version: number;
  preview: PreviewResult | null;
}) {
  const [state, action] = useActionState<StudioState, FormData>(runPreview, {});
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
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="meta">
            Runs v{version} against a bounded sample spread across the latest Bronze batch —
            every k-th row rather than the first N, so a clean result is not merely a clean
            first window. Nothing is written to Silver.
          </span>
          <RunButton label={preview ? "Run preview again" : "Run preview"} />
        </div>
        {state.error ? <p className="alert error">{state.error}</p> : null}
        {state.saved ? (
          <p className="alert ok">
            Preview queued — run <span className="mono">make worker</span> and reload.
          </p>
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
          ) : null}

          {Object.keys(aggregates!.null_or_invalid).length ? (
            <div className="card" style={{ marginTop: 14 }}>
              <label>Targets that would receive no value</label>
              <span className="mono small">
                {Object.entries(aggregates!.null_or_invalid)
                  .map(([target, count]) => `${target} (${count})`)
                  .join(" · ")}
              </span>
            </div>
          ) : null}

          <h2>
            Row by row{" "}
            <span className="meta">
              · showing {preview.row_results.length} of {preview.row_results_total}
            </span>
          </h2>
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
                {preview.row_results.flatMap((row) =>
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
                      <td className="mono">{field.source}</td>
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
